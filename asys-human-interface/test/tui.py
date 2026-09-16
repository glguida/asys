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
from asys_human.tui_files import FilesView, PREVIEW_LIMIT, preview
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
            await pilot.press("f2", "f3", "f4")
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
            self.assertEqual([label for label, _ in app.sections], ["Overview", "Pcb review"])
            app.query_one("#section", Select).value = 1
            await pilot.pause()
            scroll = app.query_one("#review-scroll")
            scroll.scroll_end(animate=False)
            await pilot.pause()
            self.assertGreater(scroll.scroll_y, 0)
            self.assertTrue(app.query_one("#answer").display)
            self.assertEqual(field(app, ("approved",)).editor.pressed_index, -1)

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


class PreviewTests(unittest.TestCase):
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
