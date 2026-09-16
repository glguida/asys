import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .config import validate_types
from .files import acquire_lock, timestamp, write_json
from .queue import Queue


class Runtime:
    """Consume the configured job types, allowing other runtimes to share the queue."""

    def __init__(self, root, types, *, health_file=None):
        self.queue = Queue(root)
        self.types = validate_types(types)
        self.children = {}
        self.stopping = False
        self.health_file = Path(health_file) if health_file else None

    def run(self, *, once=False):
        heartbeat = 0
        try:
            while not self.stopping:
                self._reap()
                for candidate in self.queue.list():
                    if self.stopping:
                        break
                    if candidate["type"] not in self.types or candidate["status"] not in {"pending", "running"}:
                        continue
                    self._claim(candidate["id"])
                if self.health_file and time.monotonic() - heartbeat >= 2:
                    self.health_file.touch(mode=0o600)
                    heartbeat = time.monotonic()
                if once and not self.children:
                    return
                time.sleep(0.05)
        finally:
            if self.health_file:
                self.health_file.unlink(missing_ok=True)
            self.stop()
            # Closing a pipe asks the job runner to terminate its process
            # group and persist an interrupted result. It also works if this
            # supervisor is killed: the OS closes its pipe descriptors.
            while self.children:
                self._reap()
                time.sleep(0.02)

    def stop(self):
        self.stopping = True
        for child in self.children.values():
            if child["keepalive"] is not None:
                os.close(child["keepalive"])
                child["keepalive"] = None

    def _reap(self):
        for job_id, child in list(self.children.items()):
            if child["process"].poll() is None:
                continue
            if child["keepalive"] is not None:
                os.close(child["keepalive"])
            del self.children[job_id]

    def _claim(self, job_id):
        directory = self.queue.directory(job_id)
        lease = acquire_lock(directory / "lease")
        if lease is None:
            return
        try:
            state = self.queue.state(job_id)
            if state["status"] not in {"pending", "running"}:
                return
            request = self.queue.request(job_id)
            spec = self.types.get(request["type"])
            if spec is None:
                return
            if self.queue.cancel_requested(job_id):
                state.update(status="cancelled", finished_at=timestamp())
                self.queue.save(state)
                return
            if state["status"] == "running":
                state.update(status="interrupted", finished_at=timestamp(), error="The job runner stopped without recording an outcome")
                self.queue.save(state)
                return
            job_directory = self.queue.paths(job_id)["directory"]
            command = [*spec["command"], *request.get("args", [])]
            write_json(job_directory / "input.json", request.get("input"))
            state.update(status="running", started_at=timestamp(), command=command)
            self.queue.save(state)
            self._spawn(job_id, lease, spec)
        except Exception as error:
            state = self.queue.state(job_id)
            if state["status"] not in {"done", "cancelled"}:
                state.update(status="failed", finished_at=timestamp(), error=str(error))
                self.queue.save(state)
        finally:
            os.close(lease)

    def _spawn(self, job_id, lease, spec):
        reader, writer = os.pipe()
        env = os.environ.copy()
        env.pop("ASYS_RUNTIME_HEALTH_FILE", None)
        package_root = str(Path(__file__).resolve().parent.parent)
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [package_root, env.get("PYTHONPATH")]))
        try:
            process = subprocess.Popen(
                [sys.executable, "-m", "asys_runtime.runner", str(self.queue.root), job_id, str(lease), str(reader)],
                stdin=subprocess.PIPE, pass_fds=(lease, reader), env=env,
                start_new_session=True,
            )
            self.children[job_id] = {"process": process, "keepalive": writer}
            try:
                process.stdin.write(json.dumps(spec).encode())
            finally:
                process.stdin.close()
        except BaseException:
            if job_id not in self.children:
                os.close(writer)
            raise
        finally:
            os.close(reader)
