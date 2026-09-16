import json
import os
from pathlib import Path
import sys
import time

value = json.load(sys.stdin)
print('executed ' + os.environ['ASYS_JOB_ID'], flush=True)
workspace = Path.cwd()
with (workspace / "executions").open("a") as output:
    output.write(str(len((workspace / "executions").read_text().splitlines()) + 1) + "\n")
if value.get("wait"):
    while not Path(value["wait"]).exists():
        time.sleep(0.02)
if value.get("fail"):
    raise SystemExit(17)
if value.get("failOnce") and not (workspace / "saved-work.txt").exists():
    (workspace / "saved-work.txt").write_text("work before failure")
    raise SystemExit(17)
if value.get("failOnce"):
    assert (workspace / "saved-work.txt").read_text() == "work before failure"
if value.get("exception"):
    Path(os.environ["ASYS_RESULT"]).write_text(json.dumps(value))
    (workspace / "partial.txt").write_text("unfinished")
    raise SystemExit(17)
if value.get("step"):
    (workspace / (value['step'] + '.txt')).write_text(value['step'])
Path(os.environ["ASYS_RESULT"]).write_text(json.dumps(value))
