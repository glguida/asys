import io
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
from asys_human.forms import Form, FormError
from asys_human.prompt import FormPrompt, Quit, Skip


APPROVAL = {"type": "object", "properties": {"approved": {"type": "boolean"}, "comments": {"type": "string"}},
            "required": ["approved"], "additionalProperties": False}
PCB_APPROVAL = json.loads((ROOT / "test/fixtures/pcb-approval.json").read_text())["form"]


class FormTests(unittest.TestCase):
    def answer(self, schema, lines, ui=None):
        description = {"form": schema}
        if ui is not None:
            description["uischema"] = ui
        output = io.StringIO()
        prompt = FormPrompt(description, output)
        scripted = iter(lines)
        result = prompt.read(lambda: next(scripted))
        self.assertEqual(list(scripted), [], "the complete conversation must be consumed")
        self.assertNotIn("JSON answer", output.getvalue())
        return result, output.getvalue()

    def test_existing_pcb_approval_and_comment_need_no_json_or_ui_schema(self):
        result, output = self.answer(APPROVAL, ["disapprove", "Move the USB connector.", "y"])
        self.assertEqual(result, {"approved": False, "comments": "Move the USB connector."})
        self.assertIn("1. Approve", output)
        self.assertIn("2. Disapprove", output)
        self.assertIn("Comments (optional", output)
        self.assertIn("Submit response?", output)

    def test_pcb_workflow_conditional_comment_form_renders_declared_fields(self):
        self.assertEqual(self.answer(PCB_APPROVAL, ["approve", "", "y"])[0], {"approved": True})
        answer, output = self.answer(PCB_APPROVAL, ["disapprove", "Move the connector.", "y"])
        self.assertEqual(answer, {"approved": False, "comments": "Move the connector."})
        self.assertIn("1. Approve", output)
        self.assertIn("2. Disapprove", output)
        self.assertEqual(Form({"form": PCB_APPROVAL}).schema, PCB_APPROVAL)

    def test_combinations_can_validate_fields_with_an_explicit_shape(self):
        for keyword in ["anyOf", "oneOf", "allOf"]:
            schema = {**APPROVAL, keyword: [{"properties": {"comments": {"minLength": 3}}}]}
            with self.subTest(keyword=keyword):
                self.assertEqual(self.answer(schema, ["approve", "Good", "y"])[0],
                                 {"approved": True, "comments": "Good"})

    def test_branches_that_need_additional_controls_are_not_silently_ignored(self):
        schemas = [{"oneOf": [{"type": "string"}, {"type": "integer"}]},
                   {**APPROVAL, "anyOf": [{"properties": {"reason": {"type": "string"}}, "required": ["reason"]}]}]
        for schema in schemas:
            with self.subTest(schema=schema), self.assertRaises(FormError):
                Form({"form": schema})

    def test_ui_controls_set_order_labels_and_multiline_input(self):
        ui = {"type": "Group", "label": "Engineering review", "elements": [
            {"type": "Label", "text": "Please review the connector placement."},
            {"type": "Control", "scope": "#/properties/comments", "label": "Reviewer note", "options": {"multi": True}},
            {"type": "HorizontalLayout", "elements": [
                {"type": "Control", "scope": "#/properties/approved", "label": "Decision", "options": {"format": "radio"}}]}]}
        result, output = self.answer(APPROVAL, ["Move the connector.", "Keep the test point.", "/done", "2", "y"], ui)
        self.assertEqual(result, {"comments": "Move the connector.\nKeep the test point.", "approved": False})
        self.assertLess(output.index("Reviewer note"), output.index("Decision"))
        self.assertIn("Engineering review", output)
        self.assertIn("Please review the connector placement.", output)

    def test_choices_preserve_their_json_types_and_use_titles(self):
        schema = {"type": "object", "properties": {
            "decision": {"oneOf": [{"const": False, "title": "Request changes"}, {"const": True, "title": "Accept"}]},
            "priority": {"type": "integer", "enum": [10, 20, 30]}}, "required": ["decision", "priority"]}
        result, output = self.answer(schema, ["request changes", "2", "y"])
        self.assertIs(result["decision"], False)
        self.assertEqual(result["priority"], 20)
        self.assertIn("Request changes", output)

    def test_omitted_empty_zero_false_and_null_remain_distinct(self):
        schema = {"type": "object", "properties": {"count": {"type": "integer"}, "enabled": {"type": "boolean"},
            "absent": {"type": "string"}, "empty": {"type": "string"}, "note": {"type": ["string", "null"]}},
            "required": ["count", "enabled", "note"]}
        result, _ = self.answer(schema, ["0", "no", "", "/empty", "/null", "y"])
        self.assertEqual(result, {"count": 0, "enabled": False, "empty": "", "note": None})

    def test_review_edits_and_validation_retries_keep_other_answers(self):
        output = io.StringIO()
        prompt = FormPrompt({"form": APPROVAL}, output)
        lines = iter(["approve", "Draft comment", "n", "disapprove", "", "y"])
        self.assertEqual(prompt.read(lambda: next(lines)), {"approved": False, "comments": "Draft comment"})
        # A worker may reject cross-field constraints after submission. The
        # same prompt reopens the draft without losing the other field.
        lines = iter(["", "Corrected comment", "y"])
        self.assertEqual(prompt.read(lambda: next(lines)), {"approved": False, "comments": "Corrected comment"})

    def test_defaults_do_not_make_decisions_and_submit_needs_an_explicit_answer(self):
        result, output = self.answer({"type": "boolean", "default": True}, ["", "disapprove", "", "yes"])
        self.assertIs(result, False)
        self.assertIn("Choose an option", output)
        self.assertIn("Enter y or n", output)

    def test_invalid_numbers_and_field_limits_are_reprompted(self):
        result, output = self.answer({"type": "object", "properties": {
            "count": {"type": "integer", "minimum": 1}, "voltage": {"type": "number", "exclusiveMaximum": 5},
            "note": {"type": "string", "minLength": 3, "maxLength": 4}}, "required": ["count", "voltage", "note"]},
            ["1.5", "0", "2", "NaN", "Infinity", "5", "3.3", "", "12345", "good", "y"])
        self.assertEqual(result, {"count": 2, "voltage": 3.3, "note": "good"})
        for message in ["whole number", "at least 1", "finite number", "less than 5", "at least 3 characters", "at most 4 characters"]:
            self.assertIn(message, output)

    def test_nested_objects_arrays_and_local_references(self):
        schema = {"type": "object", "definitions": {"person": {"type": "object", "properties": {
            "name": {"type": "string"}, "notify": {"type": "boolean"}}, "required": ["name", "notify"]}},
            "properties": {"reviewers": {"type": "array", "minItems": 1, "maxItems": 2, "items": {"$ref": "#/definitions/person"}},
                           "version": {"const": 1}}, "required": ["reviewers", "version"]}
        result, _ = self.answer(schema, ["2", "Alice", "yes", "Bob", "no", "y"])
        self.assertEqual(result, {"reviewers": [{"name": "Alice", "notify": True}, {"name": "Bob", "notify": False}], "version": 1})

    def test_nested_ui_scopes_and_pointer_escaping_preserve_field_names(self):
        schema = {"type": "object", "properties": {"design": {"type": "object", "properties": {
            "a/b~c": {"type": "string"}, "optional": {"type": "string"}}, "required": ["a/b~c"]}}, "required": ["design"]}
        ui = {"type": "VerticalLayout", "elements": [{"type": "Control", "label": "Name", "scope": "#/properties/design/properties/a~1b~0c"}]}
        self.assertEqual(self.answer(schema, ["Circuit", "y"], ui)[0], {"design": {"a/b~c": "Circuit"}})

    def test_omitted_nested_optional_fields_do_not_create_empty_parent_objects(self):
        schema = {"type": "object", "properties": {"extra": {"type": "object", "properties": {"note": {"type": "string"}}}}}
        ui = {"type": "Control", "scope": "#/properties/extra/properties/note"}
        self.assertEqual(self.answer(schema, ["", "y"], ui)[0], {})
        schema["required"] = ["extra"]
        self.assertEqual(self.answer(schema, ["", "y"], ui)[0], {"extra": {}})
        with self.assertRaises(FormError):
            Form({"form": schema, "uischema": {"type": "VerticalLayout", "elements": []}})

    def test_skip_quit_and_eof_interrupt_every_stage_without_an_answer(self):
        for lines, error in [(["/skip"], Skip), (["approve", "/quit"], Quit), (["approve", "", "/skip"], Skip)]:
            with self.subTest(lines=lines), self.assertRaises(error):
                self.answer(APPROVAL, lines)
        def eof():
            raise EOFError()
        with self.assertRaises(EOFError):
            FormPrompt({"form": APPROVAL}, io.StringIO()).read(eof)
        ui = {"type": "Control", "scope": "#", "options": {"multi": True}}
        with self.assertRaises(Quit):
            self.answer({"type": "string"}, ["first line", "/quit"], ui)

    def test_text_is_preserved_and_terminal_controls_are_not_emitted(self):
        schema = {"type": "string", "title": "Comment\x1b[2J", "description": "Keep spacing\x1b[H"}
        result, output = self.answer(schema, ["  Keep this.\x1b[2J  ", "y"])
        self.assertEqual(result, "  Keep this.\x1b[2J  ")
        self.assertNotIn("\x1b", output)
        self.assertEqual(self.answer({"type": "string"}, ["//quit", "y"])[0], "/quit")

    def test_ui_behavior_and_required_fields_are_not_silently_ignored(self):
        control = {"type": "Control", "scope": "#/properties/approved"}
        invalid = [
            {**control, "rule": {"effect": "HIDE"}},
            {**control, "scope": "#/properties/missing"},
            {**control, "options": {"readonly": True}},
            {"type": "Group", "elements": [control], "options": {"readonly": True}},
            {"type": "VerticalLayout", "elements": [control, control]},
            {"type": "Control", "scope": "#/properties/comments"},
        ]
        for ui in invalid:
            with self.subTest(ui=ui), self.assertRaises(FormError):
                Form({"form": APPROVAL, "uischema": ui})
        for schema in [False, {"$ref": "https://example.com/schema"}, {"$ref": "#/definitions/missing"},
                       {"definitions": {"loop": {"$ref": "#/definitions/loop"}}, "$ref": "#/definitions/loop"}]:
            with self.subTest(schema=schema), self.assertRaises(FormError):
                Form({"form": schema})


if __name__ == "__main__":
    unittest.main()
