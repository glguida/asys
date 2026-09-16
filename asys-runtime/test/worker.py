"""Real subprocess fixture. This knows nothing about Runtime's implementation."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

mode, *arguments = sys.argv[1:]
workspace = Path.cwd()
with (workspace / "executions").open("a") as output:
    output.write(str(len((workspace / "executions").read_text().splitlines()) + 1) + "\n")
(workspace / "started").write_text(str(os.getpid()))
if mode in {"exception", "exception-zero", "exception-invalid"}:
    result = {"exception": "Required schematic is missing", "report": "Only architecture.json was supplied"}
    Path(os.environ["ASYS_RESULT"]).write_text("{invalid" if mode == "exception-invalid" else json.dumps(result))
    (workspace / "partial.txt").write_text("unfinished")
    raise SystemExit(0 if mode == "exception-zero" else 17)
if mode == "tree":
    child = subprocess.Popen([sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(120)"])
    (workspace / "child").write_text(str(child.pid))
if mode in {"wait", "tree"} or (mode == "resume" and len((workspace / "executions").read_text().splitlines()) == 1):
    while not (workspace / "release").exists():
        time.sleep(0.02)
if mode == "delegate":
    from asys_runtime import Queue

    queue = Queue(arguments[0])
    child_id = os.environ["ASYS_JOB_ID"] + "-child"
    storage = Path(arguments[0]) / 'executions' / child_id
    job, work = storage / 'job', storage / 'workspace'
    job.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    queue.submit("child", child_id, directory=job, workspace=work, input={"parent": os.environ["ASYS_JOB_ID"]})
    child_state = queue.wait(child_id, timeout=5)
    if child_state["status"] != "done":
        raise RuntimeError(f"Child job failed: {child_state}")
if mode == "fail" or (mode == "fail-once" and len((workspace / "executions").read_text().splitlines()) == 1):
    print("deliberate failure", file=sys.stderr)
    raise SystemExit(17)
if mode == "invalid":
    Path(os.environ["ASYS_RESULT"]).write_text("{invalid")
else:
    text = sys.stdin.read()
    result = {"args": arguments, "input": json.loads(text) if text else None,
        "type": os.environ["ASYS_JOB_TYPE"], "id": os.environ["ASYS_JOB_ID"],
        "cwd": str(workspace),
        "executions": (workspace / "executions").read_text().splitlines(),
        "custom": os.environ.get("EXAMPLE_SETTING")}
    if mode == "delegate":
        result["child"] = child_state["result"]
    Path(os.environ["ASYS_RESULT"]).write_text(json.dumps(result))
    print("finished " + os.environ["ASYS_JOB_ID"])
