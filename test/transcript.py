"""Read streamed agent output before it reaches the durable Pi session."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from asys.transcript import JobOutput, render


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

    def test_reports_decode_prose_and_preserve_all_assessments(self):
        report = {"final": "Checked the board.\nOne fault remains.", "exception": "Need a replacement.\nPart unavailable.",
                  "verified": False, "goal_status": "continue", "criteria": [
                      {"id": "C1", "status": "unmet", "evidence": [{"observation": "Pin 2\nis disconnected."}]}]}
        self.emit("started", parentId="prompt")
        text = json.dumps(report)
        # The worker's result parser joins text blocks; the viewer must too.
        self.emit("delta", kind="text", contentIndex=0, delta=text[:30])
        self.emit("delta", kind="text", contentIndex=1, delta=text[30:])
        live = self.read()
        self.assertIn("Assistant\nChecked the board.\nOne fault remains.", live)
        self.assertIn("Exception:\n  Need a replacement.\n  Part unavailable.", live)
        self.assertIn("Verified:\n  false", live)
        self.assertIn("Goal status:\n  continue", live)
        self.assertIn("C1", live)
        self.assertIn("unmet", live)
        self.assertIn("Pin 2\n", live)
        self.assertNotIn('"final":', live)
        self.assertNotIn('\\n', live)
        self.document["agent"]["session"]["entries"].append({
            "type": "message", "id": "answer", "parentId": "prompt", "message": {
                "role": "assistant", "content": [{"type": "text", "text": text[:30]},
                                                   {"type": "text", "text": text[30:]}]}})
        self.document["agent"]["session"]["leafId"] = "answer"
        self.save()
        self.assertEqual(live, self.read(), "saving a streamed report must not duplicate or reformat it")

    def test_unrelated_json_and_user_tool_content_are_preserved(self):
        report = json.dumps({"final": "Two\nlines", "exception": None})
        for role, text in [("user", report), ("toolResult", report), ("assistant", '{"value": 7}'),
                           ("assistant", '{"final": "still streaming'),
                           ("assistant", '{"final": "wrong contract", "exception": false}')]:
            with self.subTest(role=role, text=text):
                lines = render({"session": {"entries": [{"type": "message", "message": {
                    "role": role, "content": text}}]}})
                self.assertIn(text, "\n".join(lines))

    def test_goal_reports_and_continuation_history_for_all_saved_versions(self):
        self.checkpoint.unlink()
        phase = self.root / "attempts/2/implement-1"
        phase.mkdir(parents=True)
        agent = {"sessionStartEntryCount": 1, "session": {"leafId": "answer", "entries": [
            {"type": "message", "id": "old", "parentId": None,
             "message": {"role": "assistant", "content": "Earlier turn must not repeat."}},
            {"type": "message", "id": "answer", "parentId": "old", "message": {"role": "assistant",
             "content": json.dumps({"final": "Checks complete.\nReady for review.", "exception": None, "goal_status": "review"})}},
        ]}}
        (phase / "agent.json").write_text(json.dumps({"agent": agent}))
        for version in (1, 2, 3):
            with self.subTest(version=version):
                (self.root / "goal.json").write_text(json.dumps({"version": version, "goal": "Check board",
                    "status": "running", "sessions": [{"attempt": 2, "phase": "implement", "status": "completed",
                    "directory": "attempts/2/implement-1"}]}))
                title, lines = self.output.read(self.job)
                text = "\n".join(lines)
                self.assertEqual(title, "Goal")
                self.assertIn("Checks complete.\nReady for review.", text)
                self.assertIn("Goal status:\n  review", text)
                self.assertNotIn("Earlier turn", text)
                self.assertNotIn('"final":', text)

    def test_event_fallback_joins_deltas_and_waits_for_complete_records(self):
        self.checkpoint.unlink()
        with self.log.open("w") as stream:
            stream.write(json.dumps({"type": "goal.phase_started", "phase": "implement", "attempt": 1}) + "\n")
            stream.write(json.dumps({"type": "agent.tool_started", "name": "bash"}) + "\n")
        self.emit("started", parentId="prompt")
        self.emit("delta", kind="text", contentIndex=0, delta="Reading ")
        self.assertIn("Assistant\nReading ", "\n".join(self.output.read(self.job)[1]))
        # More chunks than the old raw tail retained must still form one sentence.
        for _ in range(120):
            self.emit("delta", kind="text", contentIndex=0, delta="a")
        event = json.dumps({"type": "agent.message_delta", "kind": "text", "contentIndex": 0, "delta": " board.\nNext line."})
        with self.log.open("a") as stream:
            stream.write(event[:35])
        text = "\n".join(self.output.read(self.job)[1])
        self.assertIn("Reading " + "a" * 120, text)
        self.assertNotIn("Next line", text)
        self.assertNotIn('"type":', text)
        with self.log.open("a") as stream:
            stream.write(event[35:] + "\n")
        title, lines = self.output.read(self.job)
        text = "\n".join(lines)
        self.assertEqual(title, "Logs")
        self.assertIn("Attempt 1: implement (started)", text)
        self.assertIn("Tool started: bash", text)
        self.assertIn("Reading " + "a" * 120 + " board.\nNext line.", text)
        self.assertEqual(text, "\n".join(self.output.read(self.job)[1]))
        self.save()  # The transcript becomes available while the response streams.
        text = self.read()
        self.assertEqual(text.count("Reading "), 1)
        self.assertNotIn("Tool started", text)

    def test_plain_and_unknown_program_output_is_not_reinterpreted(self):
        self.checkpoint.unlink()
        original = ['ordinary output', '{"type": "customer.event", "value": 7}', '{not json}',
                    '{"type": [], "value": 7}', '{"type": "agent.message_delta", "kind": []}',
                    '{"type": "agent.message_delta", "kind": "future_kind"}']
        self.log.write_text("\n".join(original) + "\n")
        title, lines = self.output.read(self.job)
        self.assertEqual(title, "Logs")
        self.assertEqual(lines, ["stdout: " + line for line in original])

    def test_completed_program_output_without_newline_remains_visible(self):
        self.checkpoint.unlink()
        self.log.write_text('finished without a newline')
        self.job['status'] = 'done'
        self.assertEqual(self.output.read(self.job), ('Logs', ['stdout: finished without a newline']))
        self.assertEqual(self.output.read(self.job), ('Logs', ['stdout: finished without a newline']))

    def test_fallback_report_can_span_text_blocks(self):
        self.checkpoint.unlink()
        report = json.dumps({'final': 'Checked.\nReady.', 'exception': None, 'verified': False})
        self.emit('started', parentId='prompt')
        self.emit('delta', kind='text', contentIndex=0, delta=report[:20])
        self.emit('delta', kind='text', contentIndex=1, delta=report[20:])
        title, lines = self.output.read(self.job)
        self.assertEqual(title, 'Logs')
        self.assertIn('Checked.\nReady.', '\n'.join(lines))
        self.assertIn('Verified:\n  false', '\n'.join(lines))
        self.assertNotIn('"final"', '\n'.join(lines))

    def test_goal_phase_without_checkpoint_uses_readable_phase_events(self):
        self.checkpoint.unlink()
        phase = self.root / "attempts/1/implement-1"
        phase.mkdir(parents=True)
        report = {"final": "Working.\nMore to do.", "exception": None, "goal_status": "continue"}
        (phase / "stdout.log").write_text(json.dumps({"type": "agent.message_delta", "kind": "text",
            "contentIndex": 0, "delta": json.dumps(report)}) + "\n")
        goal = {"version": 3, "goal": "Check board", "status": "running", "sessions": [
            {"attempt": 1, "phase": "implement", "status": "running", "directory": "attempts/1/implement-1"}]}
        (self.root / "goal.json").write_text(json.dumps(goal))
        title, lines = self.output.read(self.job)
        self.assertEqual(title, "Goal")
        self.assertIn("Working.\nMore to do.", "\n".join(lines))
        goal["sessions"][0]["result"] = report
        (self.root / "goal.json").write_text(json.dumps(goal))
        self.assertEqual(lines, self.output.read(self.job)[1])


if __name__ == "__main__":
    unittest.main()
