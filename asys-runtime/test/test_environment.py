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

    def test_external_workers_replace_the_definition_and_keep_environment_resources_separate(self):
        builtin = self.definition("builtin", {"local": {"command": ["false"]}})
        external = self.definition("portable", {"remote": {"command": ["./program"], "env": {
            "ASYS_ENVIRONMENT_DIR": "wrong", "ASYS_WORKERS_DIR": "wrong"}}})
        selected = Environment(builtin.directory, external=external.directory)
        self.assertEqual(selected.descriptor, external.descriptor)
        self.assertEqual(list(selected.types), ["remote"])
        self.assertEqual(selected.types["remote"]["command"], [str(external.directory / "program")])
        self.assertEqual(selected.types["remote"]["env"], {
            "ASYS_ENVIRONMENT": "portable", "ASYS_ENVIRONMENT_DIR": str(builtin.directory),
            "ASYS_WORKERS_DIR": str(external.directory)})

    def test_runtime_executes_external_workers_using_explicit_and_mounted_selection(self):
        builtin = self.definition("builtin", {"local": {"command": ["false"]}})
        (builtin.directory / "tool-data.txt").write_text("environment resource")
        for mode in ("explicit", "mounted"):
            with self.subTest(mode=mode):
                external = self.root / "bundle" if mode == "explicit" else builtin.directory / "external"
                external.mkdir()
                (external / "worker").write_text(f'#!{sys.executable}\n' + '''import json, os
from pathlib import Path
json.dump({
    "environment": Path(os.environ["ASYS_ENVIRONMENT_DIR"]).joinpath("tool-data.txt").read_text(),
    "agent": Path(os.environ["ASYS_WORKERS_DIR"]).joinpath("agent-data.txt").read_text()
}, open(os.environ["ASYS_RESULT"], "w"))
''')
                (external / "worker").chmod(0o755)
                (external / "agent-data.txt").write_text("external resource")
                (external / "workers.json").write_text(json.dumps({"version": 1, "name": "portable",
                    "types": {"remote": {"command": ["./worker"]}}}))
                selection = [str(builtin.directory)] + (["--external", str(external)] if mode == "explicit" else [])
                state = self.root / mode
                queue = Queue(environment_root(state, "portable"))
                queue.submit("remote", "one")
                result = subprocess.run([sys.executable, str(ROOT / "tools/asys-runtime"), "run", *selection,
                    "--root", str(state), "--once"], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(queue.state("one")["status"], "done", queue.state("one"))
                self.assertEqual(queue.state("one")["result"], {
                    "environment": "environment resource", "agent": "external resource"})
                result = subprocess.run([sys.executable, str(ROOT / "tools/asys-runtime"), "describe", *selection],
                    capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), describe(state, "portable"))

    def test_missing_or_invalid_external_workers_never_fall_back_to_builtin_workers(self):
        builtin = self.definition("builtin", {"local": {"command": ["true"]}})
        with self.assertRaises(FileNotFoundError):
            Environment(builtin.directory, external=self.root / "missing")
        external = builtin.directory / "external"
        external.mkdir()
        with self.assertRaises(FileNotFoundError):
            Environment(builtin.directory, external=external)
        (external / "workers.json").write_text('{"version": 1, "types": {}}')
        with self.assertRaises(ValueError):
            Environment(builtin.directory)
        (external / "workers.json").write_text("invalid json")
        with self.assertRaises(ValueError):
            Environment(builtin.directory)

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
