import json
from pathlib import Path
from queue import Empty
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "python"), str(ROOT / ".host-deps")]

from textual.widgets import Button, DirectoryTree, Markdown, RadioButton, Select, Static, TextArea
from asys_human.interaction import Interaction
from asys_human.presentation import request_document
from asys_human.forms import Form
from asys_human.tui import HumanApp, review_sections
from asys_human.tui_files import FilesView, PREVIEW_LIMIT, preview, resolve_file_link
from asys_human.tui_forms import FieldEditor


def document(description=None):
    description = description or json.loads((ROOT / "test/fixtures/pcb-approval.json").read_text())
    return request_document("test.human", {"id": "approval", "inputJson": json.dumps(description)}, {})


def field(app, path):
    return next(editor for editor in app.query(FieldEditor) if editor.node.path == path)


async def ready(app, pilot):
    for _ in range(100):
        await pilot.pause(0.05)
        if app.document and not app.submitting and not app.query_one("#submit", Button).disabled:
            return
    raise AssertionError("The request did not become ready")


class TerminalTests(unittest.IsolatedAsyncioTestCase):
    def test_review_shows_the_actual_host_workspace_path(self):
        task = {"id": "approval", "inputJson": json.dumps({"prompt": "Review the board."}),
                "metadataJson": json.dumps({"files": {"workspace": "/var/lib/asys/workspace",
                    "directory": "/var/lib/asys/jobs/approval"}})}
        topology = {"components": [{"name": "test", "binds": [
            {"target": "/var/lib/asys/workspace", "source": "/home/designer/PCB project"},
            {"target": "/var/lib/asys/jobs", "source": "/home/designer/state/run/jobs"}]}]}
        doc = request_document("test.human", task, topology)
        overview = review_sections(doc, Form(doc))[0][1]
        self.assertIn("Workspace: /home/designer/PCB project", overview)
        self.assertIn("file:///home/designer/PCB%20project", overview)
        self.assertNotIn("/var/lib/asys/workspace", overview)
        self.assertNotIn("Job records", overview)
        self.assertNotIn("/home/designer/state/run/jobs", overview)

    async def test_actual_pcb_form_requires_a_choice_and_sends_typed_response_once(self):
        ui = Interaction()
        app = HumanApp(ui, claimant="reviewer")
        async with app.run_test(size=(120, 36)) as pilot:
            ui.form(document())
            await ready(app, pilot)
            self.assertTrue(ui.answers.empty())
            self.assertEqual(field(app, ("approved",)).editor.pressed_index, -1)
            await pilot.press("ctrl+s")
            self.assertTrue(ui.answers.empty())
            self.assertIn("Choose an option", str(app.query_one("#answer-error", Static).content))
            await pilot.click(field(app, ("approved",)).editor.query(RadioButton)[1])
            comment = field(app, ("comments",)).editor
            comment.focus()
            await pilot.press("R", "o", "t", "a", "t", "e", "enter", "J", "1")
            await pilot.press("ctrl+s", "ctrl+s")
            self.assertEqual(ui.answers.get_nowait(), (1, "submit", {"approved": False, "comments": "Rotate\nJ1"}))
            self.assertTrue(ui.answers.empty())
            self.assertTrue(app.query_one("#submit", Button).disabled)

    async def test_worker_validation_preserves_comment_and_queue_updates_preserve_focus(self):
        ui = Interaction()
        app = HumanApp(ui)
        async with app.run_test(size=(120, 36)) as pilot:
            ui.form(document())
            await ready(app, pilot)
            choice = field(app, ("approved",)).editor.query(RadioButton)[1]
            before = choice.region
            await pilot.click(choice)
            await pilot.pause()
            self.assertEqual(choice.region, before, "focusing must not move the choice between mouse-down and click")
            self.assertEqual(field(app, ("approved",)).editor.pressed_index, 1)
            comment = field(app, ("comments",)).editor
            comment.load_text(" ")
            await pilot.press("ctrl+s")
            self.assertFalse(ui.answers.empty(), str(app.query_one("#answer-error", Static).content))
            ui.answers.get_nowait()
            ui.rejected("A rejection needs a nonblank comment.")
            await ready(app, pilot)
            self.assertEqual(comment.text, " ")
            self.assertFalse(app.query_one("#submit", Button).disabled)
            comment.focus()
            comment.load_text("Rotate J1; keep its pads inside the board.")
            ui.state(["a", "b"], 3)
            ui.say("Waiting for another worker to reconnect.")
            await pilot.pause()
            self.assertIs(app.focused, comment)
            self.assertIn("3 queued", str(app.query_one("#counts", Static).content))
            await pilot.press("ctrl+s")
            self.assertEqual(ui.answers.get_nowait()[2]["comments"], comment.text)

    async def test_narrow_terminal_tabs_and_resize_keep_the_draft(self):
        ui = Interaction()
        app = HumanApp(ui)
        async with app.run_test(size=(80, 24)) as pilot:
            ui.form(document())
            await ready(app, pilot)
            self.assertFalse(app.query_one("#answer").display)
            await pilot.press("f4")
            self.assertTrue(app.query_one("#answer").display)
            field(app, ("comments",)).editor.load_text("Keep this draft.")
            await pilot.resize_terminal(140, 45)
            await pilot.pause()
            self.assertTrue(app.query_one("#answer").display)
            self.assertTrue(app.query_one("#reading").display)
            await pilot.resize_terminal(60, 18)
            await pilot.press("f2", "f3", "f5", "f4")
            self.assertEqual(field(app, ("comments",)).editor.text, "Keep this draft.")
            for selector in ["#submit", "#skip"]:
                region = app.query_one(selector).region
                self.assertLessEqual(region.bottom, 18)
                self.assertGreater(region.width, 0)

    async def test_long_review_is_paged_and_scrollable_without_resetting_controls(self):
        data = json.loads((ROOT / "test/fixtures/pcb-approval.json").read_text())
        data["context"] = {"pcbReview": {"text": "## Findings\n\n" + "\n\n".join(f"Finding {i}" for i in range(120)),
            "message": {"role": "assistant", "content": [{"type": "thinking", "thinkingSignature": "OPAQUE"}], "usage": {"input": 123}}}}
        ui = Interaction()
        app = HumanApp(ui)
        async with app.run_test(size=(120, 30)) as pilot:
            ui.form(document(data))
            await ready(app, pilot)
            self.assertNotIn("OPAQUE", str(app.sections))
            self.assertEqual([label for label, _ in app.sections], ["Question and work", "Pcb review"])
            app.query_one("#section", Select).value = 1
            await pilot.pause()
            scroll = app.query_one("#review-scroll")
            scroll.scroll_end(animate=False)
            await pilot.pause()
            self.assertGreater(scroll.scroll_y, 0)
            self.assertTrue(app.query_one("#answer").display)
            self.assertEqual(field(app, ("approved",)).editor.pressed_index, -1)

    async def test_blocked_merge_shows_question_work_and_files_before_full_technical_details(self):
        data = json.loads((ROOT / "test/fixtures/blocked-merge.json").read_text())
        data["details"]["full_evidence"] = "\n".join(f"Evidence line {i}: ```" for i in range(700))
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            changed = workspace / "worktree/rtl/sync_fifo.sv"
            changed.parent.mkdir(parents=True)
            changed.write_text("module sync_fifo; // revised implementation\nendmodule\n")
            task = {"id": "merge-review-job", "inputJson": json.dumps(data),
                    "metadataJson": json.dumps({"job_id": "merge-review-job", "files": {"workspace": "/work"}})}
            doc = request_document("test.human", task, {"components": [{"name": "test", "binds": [
                {"target": "/work", "source": directory}]}]})
            ui = Interaction()
            app = HumanApp(ui)
            async with app.run_test(size=(120, 40)) as pilot:
                ui.form(doc)
                await ready(app, pilot)
                briefing = app.query_one("#review-body", Markdown)
                shown = briefing.source
                self.assertIn(data["prompt"], shown)
                self.assertIn(data["summary"], shown)
                self.assertIn("Changed: rtl/sync_fifo.sv", shown)
                self.assertLess(shown.index("Can you preserve"), shown.index("Implemented the FIFO fix"))
                self.assertLess(shown.index("Implemented the FIFO fix"), shown.index("Full diff"))
                self.assertNotIn("merge-review-job", shown)
                self.assertNotIn(data["details"]["state"], shown)
                self.assertNotIn("Evidence line", shown)
                self.assertFalse(app.query_one("#section").display)
                self.assertEqual(field(app, ("action",)).editor.pressed_index, -1)
                field(app, ("text",)).editor.load_text("Keep this response while I inspect the files.")
                briefing.post_message(Markdown.LinkClicked(briefing, changed.as_uri()))
                await pilot.pause()
                view = app.query_one(FilesView)
                self.assertTrue(view.display)
                self.assertEqual(view.location, changed)
                self.assertIn("revised implementation", view.query_one(Markdown).source)
                await pilot.press("f5")
                self.assertTrue(app.query_one("#technical").display)
                technical = app.query_one("#technical-body", Markdown).source
                self.assertIn("merge-review-job", technical)
                self.assertIn("Evidence line 699", technical)
                self.assertIn("````json", technical)
                self.assertEqual(doc["technical"]["request"], data)
                await pilot.press("f2", "f4")
                self.assertEqual(field(app, ("text",)).editor.text, "Keep this response while I inspect the files.")
                await pilot.click(field(app, ("action",)).editor.query(RadioButton)[0])
                await pilot.press("ctrl+s")
                self.assertEqual(ui.answers.get_nowait()[2], {"action": "retry", "text": "Keep this response while I inspect the files."})

    async def test_skipping_and_next_request_clear_old_data(self):
        ui = Interaction()
        app = HumanApp(ui)
        async with app.run_test() as pilot:
            ui.form(document())
            await ready(app, pilot)
            await pilot.press("ctrl+k")
            self.assertEqual(ui.answers.get_nowait(), (1, "skip", None))
            ui.finished("Skipped")
            await pilot.pause()
            self.assertIsNone(app.document)
            ui.form(document({"title": "Another request", "prompt": "Leave a comment"}))
            await ready(app, pilot)
            self.assertEqual(len(app.query(TextArea)), 1)
            self.assertEqual(field(app, ()).editor.text, "")
            await pilot.press("ctrl+q")
            self.assertTrue(ui.stopping.is_set())

    async def test_arrays_and_nullable_fields_keep_json_types(self):
        data = {"prompt": "Enter measurements", "form": {"type": "object", "properties": {
            "values": {"type": "array", "minItems": 1, "maxItems": 2, "items": {"type": "number"}},
            "note": {"type": ["string", "null"]}}, "required": ["values", "note"]}}
        ui = Interaction()
        app = HumanApp(ui)
        async with app.run_test(size=(120, 50)) as pilot:
            ui.form(document(data))
            await ready(app, pilot)
            values = field(app, ("values",))
            add = next(button for button in values.query(Button) if button.name == "add-item")
            await pilot.click(add)
            values.items[0].editor.value = "2.5"
            note = field(app, ("note",))
            note.mode.value = "null"
            await pilot.pause()
            self.assertEqual(app.response_form.value(), {"values": [2.5], "note": None})
            remove = next(button for button in values.query(Button) if button.name == "remove-item")
            await pilot.click(remove)
            self.assertEqual(len(values.items), 0)

    async def test_workspace_files_can_be_browsed_previewed_and_opened(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "PCB project"
            workspace.mkdir()
            chosen = workspace / "review.md"
            chosen.write_text("# Board review\n\nConnector faces outward.")
            jobs = root / "jobs"
            jobs.mkdir()
            (jobs / "private.log").write_text("Internal job log")
            task = {"id": "approval", "inputJson": json.dumps({"prompt": "Review the board.", "files": [
                {"label": "Missing attachment", "path": "/var/lib/asys/jobs/approval/output/board.kicad_pcb"}]}),
                "metadataJson": json.dumps({"files": {"workspace": "/var/lib/asys/workspace",
                    "directory": "/var/lib/asys/jobs/approval", "result": "/var/lib/asys/jobs/approval/result.json"}})}
            topology = {"components": [{"name": "test", "binds": [
                {"target": "/var/lib/asys/workspace", "source": str(workspace)},
                {"target": "/var/lib/asys/jobs", "source": str(jobs)}]}]}
            doc = request_document("test.human", task, topology)
            ui = Interaction()
            app = HumanApp(ui)
            async with app.run_test(size=(120, 36)) as pilot:
                ui.form(doc)
                await ready(app, pilot)
                await pilot.press("f3")
                view = app.query_one(FilesView)
                self.assertEqual(view.location, workspace)
                tree = view.query_one(DirectoryTree)
                await tree.reload()
                self.assertEqual(tree.path, workspace)
                self.assertEqual([node.data.path for node in tree.root.children], [chosen])
                tree.select_node(tree.root.children[0])
                tree.focus()
                await pilot.press("enter")
                self.assertEqual(view.location, chosen)
                self.assertIn("Connector faces outward.", view.query_one(Markdown).source)
                with patch("asys_human.tui_files.subprocess.Popen") as opener:
                    await pilot.click(next(button for button in view.query(Button) if button.name == "open-file"))
                    self.assertEqual(opener.call_args.args[0][1], str(chosen))

    async def test_clicking_encoded_and_relative_links_reads_files_and_back_restores_the_document(self):
        with tempfile.TemporaryDirectory(prefix="human review ") as directory:
            workspace = Path(directory)
            review = workspace / "docs" / "design review.md"
            report = workspace / "reports" / "sim results.txt"
            review.parent.mkdir()
            report.parent.mkdir()
            review.write_text("# Design review\n\n[Read simulation results](../reports/sim%20results.txt)\n")
            report.write_text("Simulation passed: all 24 vectors matched.\n")
            task = {"id": "review", "inputJson": json.dumps({
                "prompt": f"[Open the review]({review.as_uri()}) before deciding.",
                "files": [{"path": "docs/design review.md", "label": "Design review"}]}),
                "metadataJson": json.dumps({"files": {"workspace": "/work"}})}
            doc = request_document("test.human", task, {"components": [{"name": "test", "binds": [
                {"target": "/work", "source": directory}]}]})
            ui = Interaction()
            app = HumanApp(ui)
            async with app.run_test(size=(120, 40)) as pilot:
                ui.form(doc)
                await ready(app, pilot)
                field(app, ()).editor.load_text("My draft answer")
                with patch.object(app, "copy_to_clipboard") as clipboard:
                    paragraph = next(block for block in app.query_one("#review-body").query("MarkdownParagraph")
                                     if "Open the review" in block.source)
                    await pilot.click(paragraph, offset=(2, 0))
                    await pilot.pause()
                    view = app.query_one(FilesView)
                    self.assertEqual(view.location, review)
                    self.assertTrue(app.query_one("#files").display)
                    paragraph = next(block for block in view.query(Markdown).first().query("MarkdownParagraph")
                                     if "Read simulation" in block.source)
                    await pilot.click(paragraph, offset=(2, 0))
                    await pilot.pause()
                    self.assertEqual(view.location, report)
                    self.assertIn("all 24 vectors matched", view.query_one(Markdown).source)
                    self.assertEqual(view.query_one(DirectoryTree).path, workspace)
                    clipboard.assert_not_called()
                    await pilot.click(next(button for button in view.query(Button) if button.name == "copy-path"))
                    clipboard.assert_called_once_with(str(report))
                    await pilot.click(next(button for button in view.query(Button) if button.name == "back-file"))
                    self.assertEqual(view.location, review)
                    self.assertIn("Design review", view.query_one(Markdown).source)
                    await pilot.press("f2", "f4")
                    self.assertEqual(field(app, ()).editor.text, "My draft answer")

    async def test_unavailable_links_explain_the_problem_and_allow_returning_to_the_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            task = {"id": "review", "inputJson": '{"prompt":"Inspect the evidence."}',
                    "metadataJson": '{"files":{"workspace":"/work"}}'}
            doc = request_document("test.human", task, {"components": [{"name": "test", "binds": [
                {"target": "/work", "source": directory}], "volumes": [{"target": "/work/private", "name": "private"}]}]})
            ui = Interaction()
            app = HumanApp(ui)
            async with app.run_test() as pilot:
                ui.form(doc)
                await ready(app, pilot)
                body = app.query_one("#review-body", Markdown)
                body.post_message(Markdown.LinkClicked(body, "/work/private/report.txt"))
                await pilot.pause()
                view = app.query_one(FilesView)
                self.assertIn("not mounted on this host", view.query_one(Markdown).source)
                await pilot.click(next(button for button in view.query(Button) if button.name == "back-file"))
                self.assertEqual(view.location, Path(directory))
                body.post_message(Markdown.LinkClicked(body, "missing-report.txt"))
                await pilot.pause()
                self.assertIn("Cannot read this file", view.query_one(Markdown).source)
                self.assertIn("missing-report.txt", str(view.query_one(".file-location", Static).content))


class PreviewTests(unittest.TestCase):
    def test_links_use_actual_mounts_and_allow_relative_document_links_inside_the_workspace(self):
        files = [{"role": "workspace", "workerPath": "/work", "path": "/host/project"}]
        mounts = {"binds": [{"target": "/work", "source": "/host/project"},
                            {"target": "/work/parts", "source": "/host/parts"}],
                  "volumes": [{"target": "/work/private"}]}
        for href, current, expected in [("parts/review.md", None, "/host/parts/review.md"),
                                        ("/work/docs/a.md", None, "/host/project/docs/a.md"),
                                        ("file:///host/project/docs/a.md", None, "/host/project/docs/a.md"),
                                        ("../reports/result.txt", Path("/host/project/docs/a.md"), "/host/project/reports/result.txt")]:
            self.assertEqual(resolve_file_link(href, files, mounts, current)[0], Path(expected))
        for href in ["/elsewhere/secret", "../outside", "file://remote/work/file"]:
            with self.assertRaises(ValueError):
                resolve_file_link(href, files, mounts)
        with self.assertRaisesRegex(ValueError, "not mounted"):
            resolve_file_link("private/report.txt", files, mounts)

    def test_binary_and_large_files_do_not_dump_into_terminal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "board.bin"
            path.write_bytes(b"\x00\x1b[2J")
            self.assertIn("binary file", preview(path))
            path.write_text("A" * (PREVIEW_LIMIT + 100))
            self.assertIn("truncated", preview(path))
            path.write_text("```\n\x1b[2J\n```")
            self.assertNotIn("\x1b", preview(path))


if __name__ == "__main__":
    unittest.main()
