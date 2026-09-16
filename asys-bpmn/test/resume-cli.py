"""Resume launcher boundary: ownership, saved configuration and channel history."""
import fcntl
import hashlib
import json
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
module = runpy.run_path(str(ROOT / "tools/asys-bpmn"))


class Resume(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="asys-resume-cli-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "abc123"
        for child in ["environment", "engine", "workflow", "workspace"]:
            (self.directory / child).mkdir(parents=True)
        (self.directory / "workflow/workflow.sqlite").touch()
        (self.directory / "environment/component.dcomp").write_text("docker sha256:saved-workers\n")
        self.record = {"id": "abc123", "name": "Example", "status": "failed", "error": "old failure",
                       "finished_at": "2026-09-14T10:00:00Z", "workflow": "/missing/workflow.bpmn",
                       "workspace": str(self.directory / "workspace"), "environment_directory": "/missing/environment", "environment": "test", "egress": True,
                       "system": "custom", "dcomp": ["dcomp", "--state-root", "/saved/dcomp"],
                       "links": {"inference": "@custom_provider"},
                       "components": {"engine": "example-workflow-abc123", "workers": "example-workers-abc123"}}
        self.path = self.directory / "run.json"
        self.path.write_text(json.dumps(self.record))
        args = module["arguments"](["--root", self.temp.name, "resume", "abc"])
        self.launcher = module["Launcher"](args)
        self.addCleanup(self.launcher.close)
        self.source = self.source_environment()
        self.commands = []
        command = patch.object(self.launcher, "command", side_effect=self.command)
        command.start()
        self.addCleanup(command.stop)

    def command(self, command, **kwargs):
        self.commands.append(command)
        if command[-2:] == ["version", "--json"]:
            return json.dumps({"api_version": 2, "version": "0.3.1"})
        if command[:3] == ["docker", "build", "--iidfile"]:
            Path(command[3]).write_text("sha256:updated-workers")
            return ""
        if command[:3] == ["docker", "image", "inspect"]:
            return "sha256:updated-workers"
        self.fail(f"Unexpected command: {command}")

    def document(self, *command):
        if command[-1] == self.record["system"]:
            return {"components": [], "globals": [{"name": "custom_provider"}]}
        # dcomp parses the source manifest, independently of the saved digest.
        preview = Path(command[-1])
        self.assertIn("docker workers:current", (preview.parent / "environment/component.dcomp").read_text())
        return {"components": [{"image_ref": "workers:current", "inputs": [{"service": "cyclo.provider.v1.Provider", "name": "inference"}], "outputs": []}]}

    def setup(self):
        with patch.object(self.launcher, "document", side_effect=self.document), \
                patch.object(self.launcher, "engine_image", return_value="sha256:new-engine"), \
                patch.object(self.launcher, "start_components"):
            self.launcher.setup()

    def test_resume_refreshes_images_and_keeps_saved_workflow_and_connections(self):
        self.setup()
        self.assertEqual(self.launcher.record["links"], {"inference": "@custom_provider"})
        self.assertEqual(self.launcher.dcomp, self.record["dcomp"])
        self.assertEqual(self.launcher.args.system, "custom")
        self.assertEqual((self.directory / "environment/component.dcomp").read_text(), "docker sha256:updated-workers\ninput cyclo.provider.v1.Provider inference\n")
        self.assertIn("sha256:new-engine", (self.directory / "engine/component.dcomp").read_text())

    def test_environment_rename_is_rejected_before_replacing_the_saved_image(self):
        config = json.loads((self.source / 'workers.json').read_text())
        config['name'] = 'other'
        (self.source / 'workers.json').write_text(json.dumps(config))
        with self.assertRaisesRegex(module['LaunchError'], 'original environment name'):
            self.setup()
        self.assertEqual((self.directory / 'environment/component.dcomp').read_text(), 'docker sha256:saved-workers\n')

    def test_missing_connections_fail_without_reading_logs_or_starting_components(self):
        del self.record["links"]
        self.path.write_text(json.dumps(self.record))
        with patch.object(self.launcher, "document") as document, \
                self.assertRaisesRegex(module["LaunchError"], "no saved environment connections"):
            self.launcher.setup()
        document.assert_not_called()
        self.launcher.close()
        self.assertEqual(json.loads(self.path.read_text()), self.record)
        self.assertEqual(self.launcher.owned, [])

    def source_environment(self):
        source = Path(self.temp.name) / "source environment"
        source.mkdir(exist_ok=True)
        config = {"version": 1, "name": "test", "types": {"agent": {"command": ["agent"]}}}
        (source / "workers.json").write_text(json.dumps(config))
        (source / "Dockerfile").write_text("FROM asys-workers:dev\n")
        (source / "component.dcomp").write_text("docker workers:current\n")
        definition = hashlib.sha256(json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        descriptor = self.directory / "runtime/environments/test/environment.json"
        descriptor.parent.mkdir(parents=True, exist_ok=True)
        descriptor.write_text(json.dumps({"definition": definition}))
        self.record["environment_directory"] = str(source)
        self.path.write_text(json.dumps(self.record))
        return source

    def test_automatic_rebuild_updates_the_saved_image_only_after_build_success(self):
        source = self.source_environment()
        manifest = self.directory / "environment/component.dcomp"

        def build(command, **kwargs):
            if command[-2:] == ["version", "--json"]:
                return json.dumps({"api_version": 2, "version": "0.3.1"})
            self.assertEqual(command[:3], ["docker", "build", "--iidfile"])
            self.assertEqual(command[-1], str(source))
            self.assertEqual(manifest.read_text(), "docker sha256:saved-workers\n")
            Path(command[3]).write_text("sha256:updated-workers")
            return ""

        with patch.object(self.launcher, "command", side_effect=build):
            self.setup()
        self.assertEqual(manifest.read_text(), "docker sha256:updated-workers\ninput cyclo.provider.v1.Provider inference\n")

    def test_failed_rebuild_does_not_replace_the_saved_image(self):
        self.source_environment()
        before = (self.directory / "environment/component.dcomp").read_text()
        with patch.object(self.launcher, "command", side_effect=module["LaunchError"]("build failed")), \
                self.assertRaisesRegex(module["LaunchError"], "build failed"):
            self.setup()
        self.assertEqual((self.directory / "environment/component.dcomp").read_text(), before)

    def test_rebuild_requires_source_and_valid_worker_configuration(self):
        self.record["environment_directory"] = str(Path(self.temp.name) / "missing")
        self.path.write_text(json.dumps(self.record))
        with self.assertRaisesRegex(module["LaunchError"], "environment source"):
            self.setup()
        self.launcher.close()
        source = self.source_environment()
        (source / "workers.json").write_text(json.dumps({"version": 1, "name": "other", "types": {}}))
        with self.assertRaisesRegex(ValueError, "needs at least one job type"):
            self.setup()
        self.assertEqual((self.directory / "environment/component.dcomp").read_text(), "docker sha256:saved-workers\n")

    def test_image_only_environment_resolves_current_tag_on_every_resume(self):
        (self.source / "Dockerfile").unlink()
        self.setup()
        self.assertEqual([command for command in self.commands if command[0] == "docker"], [["docker", "image", "inspect", "--format", "{{.Id}}", "workers:current"]])
        self.assertEqual((self.directory / "environment/component.dcomp").read_text(), "docker sha256:updated-workers\ninput cyclo.provider.v1.Provider inference\n")

    def test_active_launcher_cannot_be_resumed_or_have_its_record_changed(self):
        with (self.directory / "launcher.lock").open("a+b") as lease:
            fcntl.flock(lease, fcntl.LOCK_EX)
            with self.assertRaisesRegex(module["LaunchError"], "active launcher"):
                self.launcher.setup()
        self.assertEqual(json.loads(self.path.read_text()), self.record)

    def test_existing_components_are_not_adopted_or_removed(self):
        before = self.path.read_text()
        with patch.object(self.launcher, "document", return_value={"components": [{"name": self.record["components"]["engine"]}]}):
            with self.assertRaisesRegex(module["LaunchError"], "cleanup"):
                self.launcher.setup()
        self.launcher.close()
        self.assertEqual(self.path.read_text(), before)
        self.assertEqual(self.launcher.owned, [])

    def test_resume_skips_old_result_and_appends_new_events(self):
        self.setup()
        channel = self.directory / "runtime/channels/workflow"
        writer = module["Writer"](channel / "out")
        writer.send("run.result", {"runId": "abc123", "status": "failed", "error": "old failure"})
        original_send = module["Writer"].send

        def send(instance, kind, data):
            event = original_send(instance, kind, data)
            if kind == "resume":
                original_send(writer, "accepted", {"request": event["sequence"], "runId": "abc123", "workflowId": "saved"})
                original_send(writer, "run.recovered", {"store": 3, "runId": "abc123", "activityId": "", "time": "2026-09-14T10:01:00Z", "data": {}})
                original_send(writer, "run.result", {"runId": "abc123", "status": "completed", "output": {"work": 42}, "workflowId": "saved"})
            return event

        with patch.object(module["Writer"], "send", send):
            self.launcher.execute()
        self.launcher.snapshot()
        record = json.loads(self.path.read_text())
        self.assertEqual(record["channel_after"], 1)
        self.assertEqual(record["status"], "completed")
        self.assertNotIn("error", record)
        request, = module["Reader"](channel / "in").read(0)
        self.assertEqual((request["type"], request["data"]), ("resume", {"id": "abc123"}))
        self.assertIn("RUN RESUMED", (self.directory / "run.log").read_text())
        result = json.loads((self.directory / "result.json").read_text())
        self.assertEqual(result, {"runId": "abc123", "status": "completed", "output": {"work": 42}, "workflowId": "saved"})


if __name__ == "__main__":
    unittest.main()
