"""Read streamed agent output before it reaches the durable Pi session."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from asys.transcript import JobOutput


class TranscriptTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="asys-transcript-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.workspace = self.root / "workspace"
        self.log = self.root / "stdout.log"
        self.log.parent.mkdir(parents=True, exist_ok=True)
        self.checkpoint = self.log.parent / "agent.json"
        self.job = {"workspace": str(self.workspace), "directory": str(self.root)}
        self.document = {"agent": {"session": {"leafId": "prompt", "entries": [
            {"type": "message", "id": "prompt", "parentId": None,
             "message": {"role": "user", "content": "Check the board."}},
        ]}}}
        self.save()
        self.output = JobOutput()

    def save(self):
        temporary = self.checkpoint.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.document))
        temporary.replace(self.checkpoint)

    def emit(self, event, **data):
        with self.log.open("a") as stream:
            stream.write(json.dumps({"type": "agent.message_" + event, **data}) + "\n")

    def read(self):
        kind, lines = self.output.read(self.job)
        self.assertEqual(kind, "Transcript")
        return "\n".join(lines)

    def test_streaming_then_checkpoint_without_duplicate_text(self):
        self.read()  # The monitor is already open when streaming starts.
        self.emit("started", parentId="prompt")
        self.emit("delta", kind="thinking", contentIndex=0, delta="Check connections first.")
        self.emit("delta", kind="text", contentIndex=1, delta="I am inspecting ")
        self.emit("delta", kind="text", contentIndex=1, delta="the board now.")
        text = self.read()
        self.assertIn("Thinking\nCheck connections first.", text)
        self.assertIn("Assistant\nI am inspecting the board now.", text)
        self.assertEqual(text, self.read(), "reading again must not replay deltas")
        self.document["agent"]["session"]["entries"].append({
            "type": "message", "id": "answer", "parentId": "prompt", "message": {
                "role": "assistant", "content": [
                    {"type": "thinking", "thinking": "Check connections first."},
                    {"type": "text", "text": "I am inspecting the board now."},
                ]}})
        self.document["agent"]["session"]["leafId"] = "answer"
        self.save()
        self.assertEqual(text, self.read(), "the saved message replaces its streamed version")

    def test_partial_log_records_and_new_jobs(self):
        self.emit("started", parentId="prompt")
        delta = json.dumps({"type": "agent.message_delta", "kind": "text", "contentIndex": 0,
                            "delta": "Streaming before completion."})
        with self.log.open("a") as stream:
            stream.write(delta[:30])
        self.assertNotIn("Streaming before completion.", self.read())
        with self.log.open("a") as stream:
            stream.write(delta[30:] + "\n")
        self.assertIn("Streaming before completion.", self.read())
        self.job["directory"] = str(self.root / "next-job")
        self.assertEqual(self.output.read(self.job)[0], "Logs")
        self.assertNotIn("Streaming before completion.", "\n".join(self.output.read(self.job)[1]), "a retry cannot inherit a partial response")

    def test_reopening_uses_only_the_current_message_on_the_current_branch(self):
        self.emit("started", parentId="discarded")
        self.emit("delta", kind="text", contentIndex=0, delta="Abandoned response.")
        self.assertNotIn("Abandoned response.", self.read())
        self.emit("started", parentId="prompt")
        self.emit("delta", kind="text", contentIndex=0, delta="Current response.")
        self.output = JobOutput()  # Open top after the text has already streamed.
        text = self.read()
        self.assertIn("Current response.", text)
        self.assertNotIn("Abandoned response.", text)

    def test_new_job_preserves_the_failed_job_transcript(self):
        self.checkpoint = self.root / "next-job/agent.json"
        self.checkpoint.parent.mkdir(parents=True)
        self.document["agent"]["session"]["entries"][0]["message"]["content"] = "Restarted stage."
        self.save()
        self.assertIn("Check the board.", self.read())
        self.assertNotIn("Restarted stage.", self.read())
        self.job["directory"] = str(self.root / "next-job")
        self.assertIn("Restarted stage.", self.read())
        self.assertNotIn("Check the board.", self.read())


if __name__ == "__main__":
    unittest.main()
