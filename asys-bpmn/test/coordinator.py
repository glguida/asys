import json
import os
from pathlib import Path
import time

directory = Path(os.environ["ASYS_JOB_DIR"])
actions = directory / "actions"
requests = actions / "requests"
requests.mkdir(parents=True, exist_ok=True)
config = json.loads(Path(os.environ["ASYS_INPUT"]).read_text())


def start(identifier, activity, data=None):
    path = requests / (identifier + ".json")
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"id": identifier, "action": activity, "input": data}))
    temporary.replace(path)


def wait(identifier):
    path = actions / "results" / (identifier + ".json")
    while True:
        if path.exists():
            state = json.loads(path.read_text())
            if state["status"] == "failed":
                raise RuntimeError(state["error"])
            if state["status"] == "completed":
                return state.get("result")
        time.sleep(0.02)


results = []
for index, selection in enumerate(config["select"]):
    identifier = "action" + str(index)
    start(identifier, selection["action"], selection.get("input"))
    results.append(wait(identifier))
if config.get("hold"):
    while not Path(config["hold"]).exists():
        time.sleep(0.02)
Path(os.environ["ASYS_RESULT"]).write_text(json.dumps(results))
