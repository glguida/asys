"""Physical and runtime-contract checks for the external example world."""

from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from asys_swarm.world import World


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "terrarium"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


WORLD_PACKAGE = EXAMPLE.parents[2] / "asys-workers/worlds/terrarium"
world = load("terrarium_test_world", WORLD_PACKAGE / "component/physics.py")
policy = load("terrarium_test_policy", EXAMPLE / "env/scripted/programs/inhabitant.py")


class TerrariumTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((EXAMPLE / "env/scripted/workers/rainkeepers.json").read_text())["config"]

    def simulate(self):
        ids = [f"agent-{i:03}" for i in range(8)]
        state = world.initialize(self.config["world"]["settings"], ids, self.config["seed"])
        memory = {}
        decisions = 0
        snapshots = []
        for _ in range(36):
            actions = {}
            for agent_id in ids:
                observation = world.observe(state, agent_id)
                if observation["active"]:
                    plan = policy.decide(observation, memory.get(agent_id))
                    actions[agent_id] = plan["actions"][0]
                    memory[agent_id] = plan["memory"]
                    decisions += 1
            state = world.step(state, actions)["state"]
            # A save/reload must preserve all information needed for the next turn.
            state = json.loads(json.dumps(state, sort_keys=True))
            snapshots.append(deepcopy(state))
        return state, decisions, snapshots

    def test_scripted_run_meets_measured_goal_without_decisions_during_drought(self):
        state, decisions, snapshots = self.simulate()
        self.assertEqual(decisions, 8 * 24)
        self.assertFalse(world.evaluate(snapshots[23], self.config["objective"])["achieved"])
        self.assertEqual(snapshots[23]["agents"], {})
        self.assertTrue(snapshots[23]["drought"]["agentsRemoved"])
        self.assertEqual(state["phase"], "complete")
        result = world.evaluate(state, self.config["objective"])
        self.assertTrue(result["achieved"], result)
        self.assertEqual(result["metrics"]["minimumHealthyGardens"], 8)
        self.assertEqual(state["counts"]["rejected"], 0)
        self.assertTrue(all(not world.observe(state, a)["active"] for a in state["retired_agents"]))

    def test_repeated_seed_and_json_replay_are_identical(self):
        first = self.simulate()
        second = self.simulate()
        self.assertEqual(first, second)
        self.assertNotEqual(world.initialize({}, ["a"], 1)["agents"],
                            world.initialize({}, ["a"], 2)["agents"])

    def test_observation_is_local_and_cannot_mutate_authoritative_state(self):
        state = world.initialize({}, ["a", "b"], 7)
        observation = world.observe(state, "a")
        self.assertLess(len(observation["cells"]), len(state["cells"]))
        for cell in observation["cells"]:
            self.assertLessEqual(abs(cell["x"] - observation["agent"]["x"]) +
                                 abs(cell["y"] - observation["agent"]["y"]), 3)
        before = deepcopy(state)
        observation["agent"]["stock"] = 9999
        observation["cells"][0]["kind"] = "collector"
        self.assertEqual(state, before)

    def test_conflicting_actions_use_agent_order_not_mapping_order(self):
        state = world.initialize({}, ["a", "b"], 7)
        for agent in state["agents"].values():
            agent.update(x=0, y=0)
        action = {"type": "build", "kind": "collector", "x": 0, "y": 0}
        first = world.step(state, {"a": action, "b": action})
        second = world.step(state, {"b": action, "a": action})
        self.assertEqual(first, second)
        self.assertEqual(first["state"]["cells"][0]["built_by"], "a")
        self.assertEqual(first["state"]["agents"]["b"]["stock"], 4)
        self.assertEqual(first["state"]["counts"]["rejected"], 1)
        self.assertEqual(state["cells"][0]["kind"], "ground")

    def test_remote_construction_and_invalid_move_have_no_physical_effect(self):
        state = world.initialize({}, ["a"], 7)
        state["agents"]["a"].update(x=0, y=0)
        for action in ({"type": "build", "kind": "garden", "x": 10, "y": 8},
                       {"type": "move", "dx": 1, "dy": 1},
                       {"type": "move", "dx": True, "dy": 0}):
            result = world.step(state, {"a": action})
            self.assertEqual(result["state"]["cells"], state["cells"])
            self.assertEqual(result["state"]["agents"], state["agents"])
            self.assertEqual(result["events"][0]["type"], "rejected")

    def test_channels_supply_remote_gardens_but_bare_ground_does_not(self):
        state = world.initialize({}, [], 7)
        for index, kind in ((0, "collector"), (1, "channel"), (2, "garden"), (4, "garden")):
            state["cells"][index].update(kind=kind, water=0, health=100, built_by="past")
        result = world.step(state, {})["state"]
        self.assertTrue(result["cells"][2]["supplied"])
        self.assertEqual(result["cells"][2]["health"], 100)
        self.assertFalse(result["cells"][4]["supplied"])
        self.assertEqual(result["cells"][4]["health"], 88)

    def test_agent_claims_do_not_satisfy_objective_and_drought_cannot_be_repaired(self):
        state = world.initialize({"discoveryTurns": 1, "droughtTurns": 2}, ["a"], 7)
        state = world.step(state, {"a": {"type": "message", "message": "Goal achieved!"}})["state"]
        self.assertEqual(state["phase"], "drought")
        before = deepcopy(state["cells"])
        state = world.step(state, {"a": {"type": "build", "kind": "garden", "x": 0, "y": 0}})["state"]
        self.assertEqual(state["cells"], before)
        state = world.step(state, {})["state"]
        self.assertFalse(world.evaluate(state, {"healthyGardens": 1})["achieved"])

    def test_graph_records_delivered_messages_and_functional_connections_only(self):
        state = world.initialize({}, ["a", "b", "c"], 7)
        state["agents"]["a"].update(x=0, y=0)
        state["agents"]["b"].update(x=1, y=0)
        state["agents"]["c"].update(x=15, y=9)
        state = world.step(state, {"a": {"type": "message", "message": "Water here."}})["state"]
        self.assertEqual([(l["source"], l["target"]) for l in state["links"]], [("a", "b")])
        self.assertEqual(state["agents"]["c"]["inbox"], [])
        state = world.step(state, {"a": {"type": "build", "kind": "collector", "x": 0, "y": 0},
                                  "b": {"type": "build", "kind": "garden", "x": 1, "y": 0}})["state"]
        self.assertEqual(state["counts"]["reuse"], 1)
        self.assertEqual(state["links"][-1]["kind"], "reuse")

    def test_relocated_script_runs_as_an_ordinary_runtime_program(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scenario = root / "renamed-world"
            shutil.copytree(EXAMPLE, scenario)
            config = json.loads((scenario / "env/scripted/workers/rainkeepers.json").read_text())["config"]
            config["world"]["channel"] = "world-relocated"
            package = root / "world-package"
            shutil.copytree(WORLD_PACKAGE, package)
            repository = EXAMPLE.parents[2]
            runtime = root / 'runtime'
            env = {**os.environ, 'PYTHONPATH': os.pathsep.join([
                str(repository / 'asys-workers'), str(repository / 'asys-runtime')])}
            service = subprocess.Popen([sys.executable, str(package / 'component/serve.py'),
                '--root', str(runtime)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                remote = World(runtime, config, 'relocated-test')
                state = remote.call('initialize', config['world']['settings'], ['a'], 7)
                observation = remote.call('observe', state, 'a')
            finally:
                service.terminate()
                _, errors = service.communicate(timeout=5)
            self.assertEqual(service.returncode, 0, errors.decode())
            input_path, result_path = root / "input.json", root / "result.json"
            input_path.write_text(json.dumps({"observation": observation, "memory": {}}))
            environment = scenario / "env/scripted"
            manifest = json.loads((environment / "workers.json").read_text())
            command = manifest["types"]["swarm-step"]["command"]
            # Docker copies the relocated environment to this stable image path;
            # runtime job cwd is its workspace, not the environment directory.
            script = Path(command[1]).relative_to("/opt/asys/environment")
            command = [command[0], str(environment / script)]
            subprocess.run(command, cwd=environment, check=True, timeout=10,
                           env={**os.environ, "ASYS_INPUT": str(input_path), "ASYS_RESULT": str(result_path)})
            result = json.loads(result_path.read_text())
            self.assertEqual(result["actions"][0]["type"], "build")
            self.assertEqual(result["usage"]["totalTokens"], 0)
            self.assertTrue((package / "view.mjs").is_file())

    def test_artifacts_are_independent_json_data(self):
        state, _, _ = self.simulate()
        export = world.artifacts(state)
        self.assertEqual(len(export["constructions"]), 16)
        self.assertEqual(export["agentFreeEvaluation"]["elapsed"], 12)
        self.assertEqual(json.loads(json.dumps(export)), export)
        export["constructions"][0]["health"] = -100
        self.assertTrue(world.evaluate(state, self.config["objective"])["achieved"])


if __name__ == "__main__":
    unittest.main()
