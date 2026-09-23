#!/usr/bin/env python3
"""A transparent baseline policy, not an LLM or evidence of emergence."""

import json
import os
from pathlib import Path


def distance(a, b):
    return abs(a["x"] - b["x"]) + abs(a["y"] - b["y"])


def decide(observation, memory=None):
    memory = dict(memory or {})
    if not observation.get("active", True):
        return {"actions": [], "memory": memory}
    agent = observation["agent"]
    cells = observation["cells"]
    own_collectors = [c for c in cells if c["kind"] == "collector" and c.get("built_by") == agent["id"]]
    own_gardens = [c for c in cells if c["kind"] == "garden" and c.get("built_by") == agent["id"]]
    grounds = sorted((c for c in cells if c["kind"] == "ground"),
                     key=lambda c: (distance(c, agent), c["y"], c["x"]))
    action = {"type": "wait"}
    if own_collectors and own_gardens:
        # One visible measured contribution, followed by a local announcement.
        if not memory.get("announced"):
            collector = own_collectors[0]
            action = {"type": "message", "message": f"Water collector at {collector['x']},{collector['y']}; one garden supplied. Watch reserve capacity."}
            memory["announced"] = True
    elif agent["stock"] >= 2:
        if not own_collectors:
            candidates = [c for c in grounds if distance(c, agent) <= 1
                          and any(distance(c, g) == 1 for g in grounds)]
            if candidates:
                c = candidates[0]
                action = {"type": "build", "kind": "collector", "x": c["x"], "y": c["y"]}
                memory["collector"] = {"x": c["x"], "y": c["y"]}
        else:
            collector = own_collectors[0]
            candidates = [c for c in grounds if distance(c, collector) == 1]
            if candidates:
                c = min(candidates, key=lambda item: (distance(item, agent), item["y"], item["x"]))
                if distance(c, agent) <= 1:
                    action = {"type": "build", "kind": "garden", "x": c["x"], "y": c["y"]}
                else:
                    action = toward(agent, c)
    else:
        resources = [c for c in cells if c["kind"] == "resource" and c["amount"] > 0]
        if resources:
            resource = min(resources, key=lambda c: (distance(c, agent), c["y"], c["x"]))
            action = ({"type": "gather", "x": resource["x"], "y": resource["y"]}
                      if distance(resource, agent) <= 1 else toward(agent, resource))
    return {"actions": [action], "memory": memory}


def toward(agent, cell):
    dx, dy = cell["x"] - agent["x"], cell["y"] - agent["y"]
    return {"type": "move", "dx": (1 if dx > 0 else -1) if dx else 0,
            "dy": 0 if dx else (1 if dy > 0 else -1)}


def main():
    payload = json.loads(Path(os.environ["ASYS_INPUT"]).read_text())
    result = decide(payload["observation"], payload.get("memory"))
    result["usage"] = {"input": 0, "output": 0, "totalTokens": 0}
    Path(os.environ["ASYS_RESULT"]).write_text(json.dumps(result) + "\n")


if __name__ == "__main__":
    main()
