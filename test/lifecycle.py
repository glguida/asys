"""Host launchers must finish ownership changes and clean up only their own work."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from asys.lifecycle import ComponentHost, Interrupted, LaunchError


class LifecycleTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="asys-lifecycle-")
        self.addCleanup(directory.cleanup)
        self.host = ComponentHost(SimpleNamespace(system="test"))
        self.host.directory = Path(directory.name)
        self.host.say = lambda message: None

    def test_failed_command_keeps_diagnostics(self):
        with self.assertRaisesRegex(LaunchError, "Command failed \\(7\\)") as failure:
            self.host.command([sys.executable, "-c", "import sys; print('output'); print('diagnostic', file=sys.stderr); sys.exit(7)"])
        self.assertIn("diagnostic", str(failure.exception))
        self.assertIn("output", (self.host.directory / "commands.log").read_text())

    def test_timeout_stops_helpers_even_after_their_parent_exits(self):
        heartbeat = self.host.directory / "heartbeat"
        helper = f"import time\nfrom pathlib import Path\np = Path({str(heartbeat)!r})\nwhile True:\n with p.open('a') as f: f.write('tick\\n')\n time.sleep(.02)"
        parent = f"import subprocess, sys; subprocess.Popen([sys.executable, '-c', {helper!r}])"
        with self.assertRaisesRegex(LaunchError, "timed out"):
            self.host.command([sys.executable, "-c", parent], timeout=.5)
        saved = heartbeat.read_text()
        time.sleep(.1)
        self.assertEqual(heartbeat.read_text(), saved, "a helper must not outlive its cancelled command")

    def test_interrupt_waits_for_component_changes_but_cancels_builds(self):
        marker = self.host.directory / "changed"
        command = [sys.executable, "-c", f"import time; time.sleep(.3); open({str(marker)!r}, 'w').close()"]
        timer = threading.Timer(.05, self.host.interrupted.set)
        timer.start()
        self.addCleanup(timer.join)
        with self.assertRaises(Interrupted):
            self.host.command(command)
        self.assertTrue(marker.exists(), "a component change must finish before shutdown")
        self.assertEqual(self.host.command([sys.executable, "-c", "print('cleanup')"], cleanup=True), "cleanup\n")
        self.host.interrupted.clear()
        timer = threading.Timer(.05, self.host.interrupted.set)
        timer.start()
        self.addCleanup(timer.join)
        build = self.host.directory / "build-finished"
        with self.assertRaises(Interrupted):
            self.host.command([sys.executable, "-c", f"import time; time.sleep(1); open({str(build)!r}, 'w').close()"], cancellable=True)
        self.assertFalse(build.exists())

    def test_cleanup_removes_only_owned_components_in_reverse_order(self):
        self.host.owned = ["workers", "workflow", "never-started"]
        document = {"components": [{"name": name} for name in ["unrelated", "workers", "workflow"]]}
        commands = []
        def command(args, **options):
            self.assertTrue(options["cleanup"])
            commands.append(args)
            return "saved logs\n"
        with patch.object(self.host, "document", return_value=document), patch.object(self.host, "command", side_effect=command):
            self.assertTrue(self.host.cleanup_components())
        self.assertEqual(commands, [["dcomp", "logs", "test", "workers", "workflow"],
                                    ["dcomp", "rm-component", "test", "workflow"], ["dcomp", "rm-component", "test", "workers"]])
        self.assertEqual(self.host.owned, [])
        self.assertEqual((self.host.directory / "components.log").read_text(), "saved logs\n")

    def test_failed_cleanup_keeps_remaining_ownership(self):
        self.host.owned = ["workers", "workflow"]
        document = {"components": [{"name": name} for name in self.host.owned]}
        def command(args, **options):
            if args[1] == "rm-component" and args[-1] == "workers":
                raise LaunchError("removal failed")
            return ""
        with patch.object(self.host, "document", return_value=document), patch.object(self.host, "command", side_effect=command):
            self.assertFalse(self.host.cleanup_components())
        self.assertEqual(self.host.owned, ["workers"])
        document["operation"] = {"type": "resync"}
        with patch.object(self.host, "document", return_value=document), patch.object(self.host, "command") as execute:
            self.assertFalse(self.host.cleanup_components())
            execute.assert_not_called()

    def test_machine_api_and_component_health_are_checked(self):
        with patch.object(self.host, "command", return_value=json.dumps({"api_version": 1})):
            with self.assertRaisesRegex(LaunchError, "Machine API 2"):
                self.host.document("view", "test")
        self.host.owned = ["workers"]
        for status in [{}, {"status": "exited"}, {"status": "dead"}, {"status": "missing"},
                       {"status": "running", "health": "unhealthy"}, {"problem": "stopped"}]:
            with self.subTest(status=status), self.assertRaises(LaunchError):
                self.host.check_components({"workers": {"status": status}})
        self.host.check_components({"workers": {"status": {"status": "running", "health": "healthy"}}})


if __name__ == "__main__":
    unittest.main()
