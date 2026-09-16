"""A job runner owns the lease, child process group, and completion write."""

import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time

from .files import read_json, timestamp
from .queue import Queue
from .permissions import open_file, shared


def terminate(process):
    # Only signal groups created by this live runner, never PIDs read from an
    # old state file. Clean up descendants even when their leader already left.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def run_job(root, job_id, lease, parent):
    queue = Queue(root)
    paths = queue.paths(job_id)
    directory, workspace = paths["directory"], paths["workspace"]
    state = queue.state(job_id)
    process = None
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    def parent_gone():
        return bool(select.select([parent], [], [], 0)[0]) and not os.read(parent, 1)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        spec = json.load(sys.stdin)
        request = queue.request(job_id)
        env = {**os.environ, **spec["env"],
            "ASYS_JOB_ID": job_id, "ASYS_JOB_TYPE": request["type"], "ASYS_JOB_DIR": str(directory),
            "ASYS_REQUEST": str(queue.directory(job_id) / "request.json"),
            "ASYS_INPUT": str(directory / "input.json"), "ASYS_RESULT": str(directory / "result.json"),
            "ASYS_WORKSPACE": str(workspace)}
        if stopping or parent_gone():
            state.update(status="interrupted", error="Runtime stopped before the program started")
            return
        if queue.cancel_requested(job_id):
            state.update(status="cancelled")
            return
        with (directory / "input.json").open("rb") as input_file, \
             os.fdopen(open_file(directory / "stdout.log", os.O_CREAT | os.O_EXCL | os.O_WRONLY), "wb") as stdout, \
             os.fdopen(open_file(directory / "stderr.log", os.O_CREAT | os.O_EXCL | os.O_WRONLY), "wb") as stderr:
            process = subprocess.Popen(state["command"], cwd=workspace, env=env,
                stdin=input_file if request.get("input") is not None else subprocess.DEVNULL,
                stdout=stdout, stderr=stderr, start_new_session=True,
                umask=0o007 if shared(directory) else -1)
            started = time.monotonic()
            while process.poll() is None:
                if queue.cancel_requested(job_id):
                    state.update(status="cancelled")
                    break
                if stopping or parent_gone():
                    state.update(status="interrupted", error="Runtime stopped during this job")
                    break
                if spec["timeout"] is not None and time.monotonic() - started >= spec["timeout"]:
                    state.update(status="failed", error="Program timed out")
                    break
                time.sleep(0.03)
            terminate(process)
            state["exit_code"] = process.returncode
            if state["status"] == "running":
                if queue.cancel_requested(job_id):
                    state.update(status="cancelled")
                elif process.returncode:
                    state.update(status="failed", error=f"Program exited with status {process.returncode}")
                    # The exit status is authoritative. A failed program can
                    # still supply a structured result and an exception reason.
                    try:
                        result = read_json(directory / "result.json")
                    except (OSError, ValueError):
                        pass
                    else:
                        state["result"] = result
                        reason = result.get("exception") if isinstance(result, dict) else None
                        if isinstance(reason, str) and reason.strip():
                            state["error"] = reason
                else:
                    result = read_json(directory / "result.json") if (directory / "result.json").exists() else None
                    if stopping or parent_gone():
                        state.update(status="interrupted", error="Runtime stopped before recording the result")
                    elif queue.cancel_requested(job_id):
                        state.update(status="cancelled")
                    else:
                        state.update(status="done", result=result)
    except Exception as error:
        state.update(status="failed", error=str(error))
    finally:
        if process is not None and process.poll() is None:
            terminate(process)
        state["finished_at"] = timestamp()
        try:
            try:
                queue.save(state)
            except ValueError as error:
                state.pop("result", None)
                state.update(status="failed", error=f"Cannot store program result: {error}")
                queue.save(state)
        finally:
            os.close(parent)
            os.close(lease)


if __name__ == "__main__":
    run_job(sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]))
