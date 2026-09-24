"""A small deterministic habitat implementing the world interface.

The simulator, rather than an agent's account of its work, owns every effect.
All persistent state is JSON. This is a teaching model, not a hydrology model.
"""

from copy import deepcopy
import random


def action_schema():
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": {
            "type": {"enum": ["move", "gather", "build", "repair", "message", "wait"]},
            "dx": {"type": "integer", "minimum": -1, "maximum": 1},
            "dy": {"type": "integer", "minimum": -1, "maximum": 1},
            "x": {"type": "integer", "minimum": 0},
            "y": {"type": "integer", "minimum": 0},
            "kind": {"enum": ["collector", "garden", "channel"]},
            "message": {"type": "string", "maxLength": 240},
        },
        "required": ["type"],
        "additionalProperties": False,
        "allOf": [
            {"if": {"properties": {"type": {"const": "move"}}},
             "then": {"required": ["dx", "dy"]}},
            {"if": {"properties": {"type": {"enum": ["gather", "build", "repair"]}}},
             "then": {"required": ["x", "y"]}},
            {"if": {"properties": {"type": {"const": "build"}}},
             "then": {"required": ["kind"]}},
            {"if": {"properties": {"type": {"const": "message"}}},
             "then": {"required": ["message"]}},
        ],
    }


def _distance(a, b):
    return abs(a["x"] - b["x"]) + abs(a["y"] - b["y"])


def initialize(settings, agent_ids, seed):
    width = int(settings.get("width", 16))
    height = int(settings.get("height", 10))
    discovery = int(settings.get("discoveryTurns", 24))
    drought = int(settings.get("droughtTurns", 12))
    radius = int(settings.get("observationRadius", 3))
    if not 4 <= width <= 64 or not 4 <= height <= 64:
        raise ValueError("terrarium dimensions must be between 4 and 64")
    if discovery < 1 or drought < 1 or radius < 1:
        raise ValueError("discovery, drought and observation radius must be positive")
    if len(agent_ids) > width * height:
        raise ValueError("more agents than habitat cells")
    rng = random.Random(seed)
    cells = [{"x": x, "y": y, "kind": "ground"}
             for y in range(height) for x in range(width)]
    # Material pockets are scenery, independent of agent identities or policy.
    for cell in cells:
        if cell["x"] % 4 == 1 and cell["y"] % 4 == 1:
            cell.update(kind="resource", amount=12)
    positions = [c for c in cells if c["kind"] == "ground"]
    rng.shuffle(positions)
    if len(agent_ids) > len(positions):
        raise ValueError("more agents than starting positions")
    agents = {
        name: {"id": name, "x": positions[i]["x"], "y": positions[i]["y"],
               "stock": 4, "inbox": [], "built": 0}
        for i, name in enumerate(sorted(agent_ids))
    }
    return {
        "version": 1, "width": width, "height": height, "seed": seed,
        "turn": 0, "phase": "discovery", "agents": agents,
        "retired_agents": {}, "cells": cells,
        "rules": {"observationRadius": radius, "discoveryTurns": discovery,
                  "droughtTurns": drought, "collectorCapacity": 32,
                  "rainPerTurn": 3, "gardenWaterPerTurn": 1,
                  "dryHealthLoss": 12, "wetHealthGain": 4,
                  "costs": {"collector": 2, "garden": 2, "channel": 1},
                  "repairCost": 1, "repairHealth": 30},
        "drought": {"elapsed": 0, "minimumHealthyGardens": None,
                    "agentsRemoved": False},
        "counts": {"accepted": 0, "rejected": 0, "messages": 0, "reuse": 0},
        "links": [], "history": [],
    }


def observe(state, agent_id):
    agent = state["agents"].get(agent_id)
    if agent is None or state["phase"] != "discovery":
        return {"active": False, "phase": state["phase"], "turn": state["turn"]}
    radius = state["rules"]["observationRadius"]
    return {
        "active": True, "phase": state["phase"], "turn": state["turn"],
        "width": state["width"], "height": state["height"],
        "agent": deepcopy(agent), "rules": deepcopy(state["rules"]),
        "cells": [deepcopy(c) for c in state["cells"] if _distance(c, agent) <= radius],
        "neighbors": [{key: a[key] for key in ("id", "x", "y", "stock", "built")}
                      for name, a in sorted(state["agents"].items())
                      if name != agent_id and _distance(a, agent) <= radius],
        "hint": "Build collectors beside gardens. Connected channels extend water reach. "
                "Only collectors store rain. Gardens consume one water each turn. "
                "All agents leave at the end of discovery; rain then stops. "
                "Actions reach your cell or one cardinal neighbor. Stock is construction material, "
                "not water. In drought, constructions must work without agent decisions.",
    }


def _cell(state, x, y):
    if type(x) is not int or type(y) is not int:
        raise ValueError("coordinates must be integers")
    if not 0 <= x < state["width"] or not 0 <= y < state["height"]:
        raise ValueError("outside the habitat")
    return state["cells"][y * state["width"] + x]


def _neighbors(state, cell):
    for dx, dy in ((0, -1), (-1, 0), (1, 0), (0, 1)):
        x, y = cell["x"] + dx, cell["y"] + dy
        if 0 <= x < state["width"] and 0 <= y < state["height"]:
            yield state["cells"][y * state["width"] + x]


def _sources(state, garden):
    """Water can cross channels, but never another garden or bare ground."""
    pending = list(_neighbors(state, garden))
    visited = set()
    found = []
    while pending:
        cell = pending.pop(0)
        key = (cell["x"], cell["y"])
        if key in visited:
            continue
        visited.add(key)
        if cell["kind"] == "collector":
            found.append(cell)
        elif cell["kind"] == "channel":
            pending.extend(_neighbors(state, cell))
    return sorted(found, key=lambda c: (c["y"], c["x"]))


def _action(state, agent, action, events):
    if not isinstance(action, dict):
        raise ValueError("action must be an object")
    kind = action.get("type")
    base = {"agent_id": agent["id"], "turn": state["turn"], "type": kind}
    if kind == "wait":
        events.append(base)
        return
    if kind == "move":
        dx, dy = action.get("dx"), action.get("dy")
        if type(dx) is not int or type(dy) is not int or abs(dx) + abs(dy) != 1:
            raise ValueError("move exactly one cardinal cell")
        target = _cell(state, agent["x"] + dx, agent["y"] + dy)
        agent.update(x=target["x"], y=target["y"])
        events.append(dict(base, x=agent["x"], y=agent["y"]))
        return
    if kind == "message":
        message = action.get("message")
        if not isinstance(message, str) or not 1 <= len(message) <= 240:
            raise ValueError("message requires 1 to 240 characters")
        for other_id, other in sorted(state["agents"].items()):
            if other_id != agent["id"] and _distance(agent, other) <= state["rules"]["observationRadius"]:
                other["inbox"] = (other["inbox"] + [{"from": agent["id"], "text": message,
                                                     "turn": state["turn"]}])[-8:]
                link = {"source": agent["id"], "target": other_id, "kind": "message",
                        "turn": state["turn"]}
                state["links"].append(link)
                events.append(dict(base, target=other_id, message=message))
                state["counts"]["messages"] += 1
        return
    if kind not in ("gather", "build", "repair"):
        raise ValueError("unknown action type")
    cell = _cell(state, action.get("x"), action.get("y"))
    if _distance(agent, cell) > 1:
        raise ValueError("target must be your cell or one cardinal neighbor")
    if kind == "gather":
        if cell["kind"] != "resource" or cell.get("amount", 0) <= 0:
            raise ValueError("no material to gather here")
        cell["amount"] -= 1
        agent["stock"] += 1
        if cell["amount"] == 0:
            cell.clear()
            cell.update(x=action["x"], y=action["y"], kind="ground")
    elif kind == "build":
        artifact = action.get("kind")
        if artifact not in state["rules"]["costs"]:
            raise ValueError("unknown construction")
        if cell["kind"] != "ground":
            raise ValueError("construction requires bare ground")
        cost = state["rules"]["costs"][artifact]
        if agent["stock"] < cost:
            raise ValueError("not enough material")
        agent["stock"] -= cost
        agent["built"] += 1
        cell.update(kind=artifact, built_by=agent["id"], built_turn=state["turn"],
                    health=100, water=0, supplied=False)
        for nearby in _neighbors(state, cell):
            origin = nearby.get("built_by")
            functional_connection = (
                artifact == "channel" and nearby["kind"] in ("collector", "garden", "channel")
                or nearby["kind"] == "channel" and artifact in ("collector", "garden")
                or {artifact, nearby["kind"]} == {"collector", "garden"}
            )
            if origin and origin != agent["id"] and functional_connection:
                link = {"source": origin, "target": agent["id"], "kind": "reuse",
                        "turn": state["turn"]}
                state["links"].append(link)
                state["counts"]["reuse"] += 1
                events.append(dict(link, type="reuse", x=cell["x"], y=cell["y"]))
    else:
        if cell["kind"] not in state["rules"]["costs"]:
            raise ValueError("nothing to repair here")
        if agent["stock"] < state["rules"]["repairCost"]:
            raise ValueError("not enough material")
        agent["stock"] -= state["rules"]["repairCost"]
        cell["health"] = min(100, cell["health"] + state["rules"]["repairHealth"])
        cell["last_repaired_by"] = agent["id"]
    events.append(dict(base, x=cell["x"], y=cell["y"], kind=cell["kind"]))


def _healthy(state):
    return sum(c["kind"] == "garden" and c["health"] >= 50 for c in state["cells"])


def step(state, actions):
    result = deepcopy(state)
    events = []
    if result["phase"] == "complete":
        return {"state": result, "events": events}
    result["turn"] += 1
    for agent_id in sorted(actions):
        agent = result["agents"].get(agent_id)
        action = actions[agent_id]
        try:
            if result["phase"] != "discovery" or agent is None:
                raise ValueError("agent is not active")
            _action(result, agent, action, events)
            result["counts"]["accepted"] += 1
        except (ValueError, TypeError, KeyError) as exc:
            result["counts"]["rejected"] += 1
            events.append({"type": "rejected", "agent_id": agent_id,
                           "turn": result["turn"], "action": deepcopy(action), "reason": str(exc)})
    drought = result["phase"] == "drought"
    for cell in result["cells"]:
        if cell["kind"] == "collector" and not drought:
            cell["water"] = min(result["rules"]["collectorCapacity"],
                                cell["water"] + result["rules"]["rainPerTurn"])
    for garden in (c for c in result["cells"] if c["kind"] == "garden"):
        source = next((c for c in _sources(result, garden) if c["water"] >= 1), None)
        garden["supplied"] = source is not None
        if source:
            source["water"] -= 1
            garden["health"] = min(100, garden["health"] + result["rules"]["wetHealthGain"])
        else:
            garden["health"] = max(0, garden["health"] - result["rules"]["dryHealthLoss"])
    if drought:
        result["drought"]["elapsed"] += 1
        previous = result["drought"]["minimumHealthyGardens"]
        result["drought"]["minimumHealthyGardens"] = min(previous, _healthy(result))
        if result["drought"]["elapsed"] >= result["rules"]["droughtTurns"]:
            result["phase"] = "complete"
            events.append({"type": "phase", "phase": "complete", "turn": result["turn"]})
    elif result["turn"] >= result["rules"]["discoveryTurns"]:
        result["retired_agents"] = result["agents"]
        result["agents"] = {}
        result["phase"] = "drought"
        result["drought"].update(agentsRemoved=True, minimumHealthyGardens=_healthy(result))
        events.append({"type": "phase", "phase": "drought", "turn": result["turn"],
                       "message": "All agents removed. Rain stopped. No more agent decisions."})
    result["history"].append({"turn": result["turn"], "healthyGardens": _healthy(result),
                               "water": sum(c.get("water", 0) for c in result["cells"]),
                               "phase": result["phase"]})
    return {"state": result, "events": events}


def evaluate(state, objective):
    target = int(objective.get("healthyGardens", 6))
    healthy = _healthy(state)
    metrics = {
        "healthyGardens": healthy, "targetGardens": target,
        "gardens": sum(c["kind"] == "garden" for c in state["cells"]),
        "collectors": sum(c["kind"] == "collector" for c in state["cells"]),
        "storedWater": sum(c.get("water", 0) for c in state["cells"]),
        "droughtElapsed": state["drought"]["elapsed"],
        "droughtTurns": state["rules"]["droughtTurns"],
        "minimumHealthyGardens": state["drought"]["minimumHealthyGardens"],
        "agentsActive": len(state["agents"]), **state["counts"],
    }
    complete = state["phase"] == "complete"
    minimum = state["drought"]["minimumHealthyGardens"]
    achieved = complete and minimum is not None and minimum >= target
    if complete:
        summary = (f"{'Passed' if achieved else 'Failed'}: at least {minimum} gardens stayed "
                   f"healthy throughout {state['rules']['droughtTurns']} agent-free drought turns; "
                   f"target {target}.")
    else:
        summary = f"{healthy} healthy gardens; objective remains unverified until the agent-free drought finishes."
    return {"achieved": achieved, "metrics": metrics, "summary": summary}


def artifacts(state):
    return {
        "format": "terrarium-artifacts-v1", "seed": state["seed"],
        "width": state["width"], "height": state["height"],
        "turn": state["turn"], "phase": state["phase"],
        "rules": deepcopy(state["rules"]),
        "constructions": [deepcopy(c) for c in state["cells"]
                          if c["kind"] in state["rules"]["costs"]],
        "provenance": deepcopy(state["links"]), "history": deepcopy(state["history"]),
        "agentFreeEvaluation": deepcopy(state["drought"]),
    }
