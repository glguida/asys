import io
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
from asys_human.presentation import host_path, request_document, review_context
from asys_human.prompt import FormPrompt


class PresentationTests(unittest.TestCase):
    def test_only_the_workspace_is_offered_even_with_job_files_and_attachments(self):
        description = {"prompt": "Review the corrected PCB", "files": [
            {"label": "PCB revision 5", "path": "create_pcb/wf-selected/pcb/board.kicad_pcb"},
            {"label": "Invalid traversal", "path": "../old/board.kicad_pcb"}],
            "context": {"checks": {"layout": {"step": "create_pcb", "job": "wf-selected", "revision": 5}},
                        "review": {"workspace": "/queue/reviewer/workspace", "text": "Review report.",
                                   "message": {"role": "assistant", "content": []}}}}
        task = {"id": "approval", "inputJson": json.dumps(description),
                "metadataJson": json.dumps({"files": {"workspace": "/var/lib/asys/workspace",
                    "directory": "/queue/approval/job", "result": "/queue/approval/job/result.json"}})}
        topology = {"components": [{"name": "alpha", "binds": [
            {"target": "/queue", "source": "/host/run"},
            {"target": "/var/lib/asys/workspace", "source": "/host/PCB project"}]}]}
        files = request_document("alpha.human", task, topology)["files"]
        self.assertEqual(files, [{"role": "workspace", "label": "Workspace",
            "workerPath": "/var/lib/asys/workspace", "path": "/host/PCB project",
            "uri": "file:///host/PCB%20project"}])

    def test_worker_results_render_as_readable_reviews_without_model_metadata(self):
        report = "Verdict: PASS.\n\n- Connector faces outward.\n- Review the robustness warning."
        context = {"pcbReview": {"passed": True, "findings": [{"component": "J1 / VBUS input",
            "requirement": "USB robustness/EMC margin", "evidence": "No VBUS TVS/filtering.",
            "correction": "Consider a VBUS-rated TVS."}], "text": report,
            "message": {"role": "assistant", "content": [
                {"type": "thinking", "thinkingSignature": "OPAQUE_REASONING_DATA"},
                {"type": "text", "text": report, "textSignature": "OPAQUE_TEXT_SIGNATURE"}],
                "usage": {"input": 1000}, "provider": "INTERNAL_PROVIDER", "responseId": "INTERNAL_RESPONSE"},
            "steps": 44, "workspace": "/worker/private-workspace"}}
        schema = json.loads((ROOT / "test/fixtures/pcb-approval.json").read_text())["form"]
        task = {"id": "review", "inputJson": json.dumps({"title": "Review PCB", "prompt": "Approve or comment.",
            "context": context, "form": schema}), "metadataJson": '{"files":{"workspace":"/queue/input"}}'}
        topology = {"components": [{"name": "alpha", "binds": [{"target": "/queue", "source": "/host/review"}]}]}
        document = json.loads(json.dumps(request_document("alpha.human", task, topology)))
        output = io.StringIO()
        lines = iter(["disapprove", "Address the TVS finding.", "y"])
        result = FormPrompt(document, output).read(lambda: next(lines))
        self.assertEqual(result, {"approved": False, "comments": "Address the TVS finding."})
        shown = output.getvalue()
        for phrase in ["Passed: Yes", "Findings", "Component: J1 / VBUS input", "No VBUS TVS/filtering.",
                       "Consider a VBUS-rated TVS.", "Verdict: PASS.", "1. Approve", "2. Disapprove"]:
            self.assertIn(phrase, shown)
        self.assertEqual(shown.count("Verdict: PASS."), 1)
        self.assertLess(shown.index("Workspace:"), shown.index("Context"))
        self.assertNotIn(r"\n\n", shown)
        self.assertNotIn("Context", shown.split("Response:", 1)[1])
        for internal in ["OPAQUE_", "INTERNAL_", "thinkingSignature", "textSignature", "responseId", "usage",
                         "/worker/private-workspace", '"passed":', '"component":']:
            self.assertNotIn(internal, shown)
            self.assertNotIn(internal, json.dumps(document))

    def test_ordinary_business_fields_are_preserved_and_message_text_is_not_duplicated(self):
        plain = {"message": "Check connector access.", "usage": "USB power only", "model": "J1", "steps": ["Inspect", "Measure"]}
        self.assertEqual(review_context(plain), plain)
        message = {"role": "assistant", "content": [{"type": "thinking", "thinking": "INTERNAL_REASONING"},
                                                    {"type": "text", "text": "Review complete."}]}
        self.assertEqual(review_context({"message": message, "passed": False}), {"passed": False, "text": "Review complete."})
        self.assertEqual(review_context(message), "Review complete.")

    def test_complete_json_document_renders_context_files_and_answer_controls(self):
        task = {"id": "review", "inputJson": json.dumps({"title": "Review the PCB", "prompt": "Approve this board?", "context": "DRC passed.",
                "form": {"type": "object", "properties": {"approved": {"type": "boolean"}, "comments": {"type": "string"}}, "required": ["approved"]}}),
                "metadataJson": json.dumps({"files": {"workspace": "/queue/review/input", "directory": "/queue/review/job", "result": "/queue/review/result.json"}})}
        topology = {"components": [{"name": "alpha", "binds": [{"target": "/queue", "source": "/tmp/PCB run"}]}]}
        document = json.loads(json.dumps(request_document("alpha.human", task, topology)))
        self.assertEqual(len(document["files"]), 1)
        self.assertEqual(document["files"][0]["path"], "/tmp/PCB run/review/input")
        self.assertEqual(document["files"][0]["uri"], "file:///tmp/PCB%20run/review/input")
        output = io.StringIO()
        prompt = FormPrompt(document, output)
        lines = iter(["disapprove", "Move the connector.", "n", "", "Keep the connector.", "y"])
        self.assertEqual(prompt.read(lambda: next(lines)), {"approved": False, "comments": "Keep the connector."})
        for content in ["Review the PCB", "DRC passed.", "Workspace: /tmp/PCB run/review/input"]:
            self.assertEqual(output.getvalue().count(content), 1, "context and file references must not be repeated during response edits")
        for private in ["Job records", "Answer result", "/tmp/PCB run/review/job", "/tmp/PCB run/review/result.json"]:
            self.assertNotIn(private, output.getvalue())

    def test_job_files_are_not_a_fallback_for_a_missing_workspace(self):
        task = {"id": "review", "inputJson": '{"prompt":"Review the proposal"}',
                "metadataJson": json.dumps({"files": {"directory": "/queue/job", "result": "/queue/job/result.json"}})}
        topology = {"components": [{"name": "alpha", "binds": [{"target": "/queue", "source": "/host/run"}]}]}
        self.assertEqual(request_document("alpha.human", task, topology)["files"], [])

    def test_a_request_without_file_metadata_has_no_attachments(self):
        task = {"id": "review", "inputJson": '{"prompt":"Review the proposal"}'}
        self.assertEqual(request_document("alpha.human", task, {})["files"], [])

    def test_most_specific_mount_wins_and_unmounted_paths_are_not_called_host_paths(self):
        component = {"binds": [{"target": "/queue", "source": "/tmp/run"}, {"target": "/queue/nested", "source": "/tmp/other"}],
                     "volumes": [{"target": "/queue/private", "name": "private"}]}
        self.assertEqual(host_path("/queue/nested/design", component), "/tmp/other/design")
        self.assertIsNone(host_path("/queue/private/design", component))
        for path in ["/queue-other/design", "/queue/../secret", "relative/design"]:
            self.assertIsNone(host_path(path, component))
        document = request_document("alpha.human", {"id": "review", "inputJson": '{"prompt":"Review"}',
            "metadataJson": '{"files":{"workspace":"/queue/output"}}'}, {})
        self.assertNotIn("path", document["files"][0])
        self.assertIn("worker path; not mounted on this host", json.dumps(document))


if __name__ == "__main__":
    unittest.main()
