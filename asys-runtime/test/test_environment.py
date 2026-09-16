import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from asys_runtime.environment import Environment, describe, environment_root, list_environments
from fixtures import PreparedQueue as Queue

ROOT = Path(__file__).resolve().parents[1]


class EnvironmentTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="asys-environment-")
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def definition(self, name, types, folder=None):
        directory = self.root / (folder or name)
        directory.mkdir(exist_ok=True)
        (directory / "workers.json").write_text(json.dumps({"version": 1, "name": name, "types": types}))
        return Environment(directory)

    def test_environment_registration_is_separate_from_its_source_and_allows_identical_replicas(self):
        environment = self.definition("first", {"script": {"command": ["./program"]}})
        state = self.root / "state"
        with environment.register(state) as directory:
            with environment.register(state):
                self.assertEqual(describe(state, "first")["types"], ["script"])
                self.assertEqual(directory, environment_root(state, "first"))
                self.assertEqual(environment.types["script"]["command"][0], str(environment.directory / "program"))
            self.assertFalse((environment.directory / "jobs").exists())
            changed = self.definition("first", {"different": {"command": ["false"]}}, folder="different-source")
            with self.assertRaisesRegex(ValueError, "already running with a different"):
                with changed.register(state):
                    pass
        with changed.register(state):
            self.assertEqual(list_environments(state)[0]["types"], ["different"])
        self.assertEqual(describe(state, "first")["types"], ["different"])

    def test_environment_directory_cli_executes_its_own_jobs_and_exports_environment_context(self):
        command = [sys.executable, "-c", 'import json,os; json.dump({k:os.environ[k] for k in ["ASYS_ENVIRONMENT","ASYS_ENVIRONMENT_DIR"]},open(os.environ["ASYS_RESULT"],"w"))']
        environment = self.definition("selected", {"program": {"command": command}})
        state = self.root / "state"
        queue = Queue(environment_root(state, "selected"))
        other = Queue(environment_root(state, "other"))
        queue.submit("program", "one")
        other.submit("program", "two")
        result = subprocess.run([sys.executable, str(ROOT / "tools/asys-runtime"), "run", str(environment.directory), "--root", str(state), "--once"], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(queue.state("one")["result"], {"ASYS_ENVIRONMENT": "selected", "ASYS_ENVIRONMENT_DIR": str(environment.directory)})
        self.assertEqual(other.state("two")["status"], "pending")
        result = subprocess.run([sys.executable, str(ROOT / "tools/asys-runtime"), "submit", "absent", "bad", "--root", str(state), "--environment", "selected", "--directory", str(queue.paths("one")["directory"]), "--workspace", str(queue.paths("one")["workspace"])], capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not define job type absent", result.stderr)
        self.assertFalse(queue.directory("bad").exists())

    def test_malformed_environment_definitions_are_rejected_without_running_programs(self):
        directory = self.root / "env"
        directory.mkdir()
        for config in [
            {"version": True, "name": "env", "types": {"x": {"command": ["true"]}}},
            {"version": 1, "types": {"x": {"command": ["true"]}}},
            {"version": 1, "name": "../escape", "types": {"x": {"command": ["true"]}}},
            {"version": 1, "name": "env", "types": {"x": {"command": []}}},
            {"version": 1, "name": "env", "description": 3, "types": {"x": {"command": ["true"]}}},
        ]:
            (directory / "workers.json").write_text(json.dumps(config))
            with self.assertRaises(ValueError):
                Environment(directory)

    def test_environment_egress_is_an_optional_boolean(self):
        directory = self.root / "env"
        directory.mkdir()
        config = {"version": 1, "name": "env", "types": {"program": {"command": ["true"]}}}
        for extra in [{}, {"egress": False}, {"egress": True}]:
            (directory / "workers.json").write_text(json.dumps({**config, **extra}))
            self.assertEqual(Environment(directory).descriptor["types"], ["program"])
        for value in ["false", "true", 0, 1, None, [], {}]:
            with self.subTest(egress=value):
                (directory / "workers.json").write_text(json.dumps({**config, "egress": value}))
                with self.assertRaisesRegex(ValueError, "egress must be true or false"):
                    Environment(directory)
