import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python"), str(ROOT.parent / "python"), str(ROOT.parent / "asys-runtime")]
from asys_human.channel import Channel
from asys_human.launcher import Launcher, arguments
from asys_human.presentation import request_document
from asys_human.prompt import FormPrompt
from asys_runtime.channel import Writer, direction_root


class HostTests(unittest.TestCase):
    def test_context_is_presented_without_terminal_controls(self):
        output = io.StringIO()
        description = request_document("alpha.human", {"id": "review", "inputJson": json.dumps({"title": "Review", "prompt": "Question\x1b[2J?",
                    "context": {"report": "full context"}, "form": {"type": "integer"}})}, {})
        lines = iter(["2", "y"])
        FormPrompt(description, output).read(lambda: next(lines))
        self.assertEqual(description["form"], {"type": "integer"})
        self.assertIn("full context", output.getvalue())
        self.assertNotIn("\x1b", output.getvalue())

    def test_fifo_deduplicates_and_filters_candidates_without_claiming_queued_work(self):
        with tempfile.TemporaryDirectory() as directory:
            channel = Channel(directory, "alice")
            output = Writer(direction_root(directory, "human", "out"))
            def attention(worker, id="same", **changes):
                task = {"id": id, "status": "pending", "inputJson": '{"prompt":"Question?"}', **changes}
                output.send("attention", {"worker": worker, "task": task})
            attention("alpha")
            attention("beta")
            attention("alpha")
            attention("private", inputJson='{"prompt":"Private?","candidates":["bob"]}')
            channel.pump()
            self.assertEqual(list(channel.queue), [("alpha", "same"), ("beta", "same")])
            self.assertEqual(channel.input.last(), 0, "queued questions are not claimed")
            channel.current = channel.queue.popitem(last=False)[0]
            attention("alpha")
            attention("beta", status="claimed")
            channel.pump()
            self.assertFalse(channel.queue)

    def test_global_and_private_handlers_have_explicit_endpoint_roles(self):
        self.assertFalse(arguments([]).private)
        self.assertTrue(arguments(['--private', '--name', 'run-human']).private)

    def test_cleanup_recovers_an_unacknowledged_claim_and_removes_only_its_component(self):
        args = arguments(["--claimant", "alice"])
        launcher = Launcher(args)
        with tempfile.TemporaryDirectory() as directory:
            launcher.directory = Path(directory)
            launcher.name = "human-interface-test"
            launcher.channel = Channel(Path(directory) / "runtime", "alice")
            launcher.owned = [launcher.name]
            launcher.active = {"worker": "alpha.human", "id": "review", "claimId": "original"}
            calls, commands = [], []
            def call(method, body, cleanup=False):
                calls.append((method, body, cleanup))
                return {"token": "recovered"} if method == "ClaimTask" else {}
            with patch.object(launcher, "call", side_effect=call), patch.object(launcher, "document", return_value={"components": [
                    {"name": "unrelated-worker"}, {"name": launcher.name}]}), patch.object(launcher, "command", side_effect=lambda command, **kwargs: commands.append(command) or ""):
                self.assertTrue(launcher.close())
            self.assertEqual(calls, [("ClaimTask", {"id": "review", "claimant": "alice", "claimId": "original"}, True),
                                     ("ReleaseTask", {"id": "review", "token": "recovered"}, True)])
            self.assertEqual(commands[-1][-3:], ["rm-component", "asys", "human-interface-test"])
            self.assertFalse(launcher.owned)


if __name__ == "__main__":
    unittest.main()
