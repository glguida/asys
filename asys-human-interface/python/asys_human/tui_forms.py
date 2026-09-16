"""JSON Schema controls for the Textual host. The worker validates submissions."""
from dataclasses import replace

from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Collapsible, Input, RadioButton, RadioSet, Select, Static, TextArea

from .forms import Field, Layout, MISSING, initial_value, set_value
from .prompt import FormPrompt, text


class AnswerError(ValueError):
    def __init__(self, editor, message):
        super().__init__(f"{editor.node.label or 'Answer'}: {message}")
        self.editor = editor


class FieldEditor(Vertical):
    DEFAULT_CSS = """
    FieldEditor { height: auto; margin-bottom: 1; }
    FieldEditor > .field-label { text-style: bold; height: auto; }
    FieldEditor > .field-description { color: $text-muted; height: auto; }
    FieldEditor > .field-body { height: auto; }
    FieldEditor .object-fields { height: auto; }
    FieldEditor .array-items { height: auto; }
    FieldEditor .array-buttons { height: 3; }
    FieldEditor .array-buttons Button { min-width: 8; width: 1fr; }
    FieldEditor RadioSet { height: auto; border: none; padding: 0; margin: 0; }
    FieldEditor RadioSet.short-choices { layout: horizontal; }
    FieldEditor RadioButton { width: auto; padding: 0 1 0 0; border: none; }
    FieldEditor TextArea { height: 7; min-height: 4; border: round $border; }
    FieldEditor Input { margin: 0; }
    FieldEditor Collapsible { padding: 0; border: none; }
    FieldEditor Collapsible > Contents { padding: 0; }
    FieldEditor .value-options { color: $text-muted; }
    FieldEditor .input-error { border: tall $error; }
    """

    def __init__(self, node):
        super().__init__()
        self.node = node
        self.editor = None
        self.mode = None
        self.fields = {}
        self.items = []

    def compose(self):
        node = self.node
        root_object = node.kind == "object" and not node.path
        if node.label and not root_object:
            yield Static(text(node.label) + ("" if node.required else " · optional"), classes="field-label", markup=False)
        if node.schema.get("description"):
            yield Static(text(node.schema["description"]), classes="field-description", markup=False)
        with Vertical(classes="field-body"):
            if node.kind == "object":
                with Vertical(classes="object-fields"):
                    for name, child in node.children.items():
                        self.fields[name] = FieldEditor(child)
                        yield self.fields[name]
            elif node.kind == "array":
                yield Vertical(classes="array-items")
                with Horizontal(classes="array-buttons"):
                    yield Button("+ Add item", name="add-item")
                    yield Button("− Remove last", name="remove-item", disabled=True)
            elif node.choices:
                short = len(node.choices) <= 3 and sum(len(str(label)) + 5 for _, label in node.choices) <= 34
                self.editor = RadioSet(*(RadioButton(text(label), value=False) for _, label in node.choices),
                                       classes="short-choices" if short else "", compact=True)
                yield self.editor
            elif node.kind in {"constant", "null"}:
                value = node.schema.get("const") if node.kind == "constant" else None
                yield Static(text(FormPrompt.display(value)) + " (fixed)", markup=False)
            else:
                # Free text uses a real multiline editor, including old schemas
                # that predate JSON Forms' `multi` option (e.g. PCB comments).
                self.editor = TextArea(soft_wrap=True, tab_behavior="focus") if node.kind == "string" else Input()
                yield self.editor
        if not node.required or node.nullable:
            options = [("Use the entered value", "value")]
            default = "value"
            if not node.required:
                options.insert(0, ("Omit when blank", "auto"))
                options.append(("Omit this field", "omit"))
                default = "auto"
            if node.nullable:
                options.append(("Set to null", "null"))
            with Collapsible(title="Value options", collapsed=True, classes="value-options"):
                self.mode = Select(options, value=default, allow_blank=False)
                yield self.mode

    async def on_button_pressed(self, event):
        if event.button.parent.parent.parent is not self:
            return
        event.stop()
        if event.button.name == "add-item":
            maximum = self.node.schema.get("maxItems")
            if maximum is not None and len(self.items) >= maximum:
                return
            child = FieldEditor(replace(self.node.item, label=f"Item {len(self.items) + 1}"))
            self.items.append(child)
            await self.query_one(".array-items", Vertical).mount(child)
            child.focus_input()
        elif event.button.name == "remove-item" and self.items:
            await self.items.pop().remove()
        for button in self.query(Button):
            if button.name == "remove-item" and button.parent.parent.parent is self:
                button.disabled = not self.items
            if button.name == "add-item" and button.parent.parent.parent is self:
                button.disabled = len(self.items) >= self.node.schema.get("maxItems", float("inf"))

    def on_select_changed(self, event):
        if event.select is self.mode:
            event.stop()
            self.query_one(".field-body", Vertical).disabled = event.value in {"omit", "null"}

    def focus_input(self):
        target = self.editor or next(iter(self.fields.values()), None) or self.mode
        if isinstance(target, FieldEditor):
            target.focus_input()
        elif target:
            target.focus()
        self.scroll_visible()

    def value(self):
        node = self.node
        mode = self.mode.value if self.mode else "value"
        if mode == "omit":
            return MISSING
        if mode == "null":
            return None
        if mode == "auto" and node.kind == "object" and not self.has_entry():
            return MISSING
        try:
            if node.kind == "object":
                value = {}
                for name, child in self.fields.items():
                    item = child.value()
                    if item is not MISSING:
                        value[name] = item
                if mode == "auto" and not value:
                    return MISSING
            elif node.kind == "array":
                value = [child.value() for child in self.items]
                if mode == "auto" and not value:
                    return MISSING
                if len(value) < node.schema.get("minItems", 0):
                    raise ValueError(f"Add at least {node.schema['minItems']} items.")
            elif node.kind == "constant":
                value = node.schema["const"]
            elif node.kind == "null":
                value = None
            elif node.choices:
                index = self.editor.pressed_index
                if index < 0:
                    if mode == "auto":
                        return MISSING
                    raise ValueError("Choose an option.")
                value = node.choices[index][0]
            else:
                raw = self.editor.text if isinstance(self.editor, TextArea) else self.editor.value
                if mode == "auto" and not raw:
                    return MISSING
                value = raw if node.kind == "string" else FormPrompt.parse(node, raw)
            FormPrompt.check(node, value)
            return value
        except AnswerError:
            raise
        except ValueError as error:
            raise AnswerError(self, str(error)) from error

    def has_entry(self):
        if self.mode and self.mode.value in {"value", "null"}:
            return True
        if self.mode and self.mode.value == "omit":
            return False
        if self.node.kind == "object":
            return any(child.has_entry() for child in self.fields.values())
        if self.node.kind == "array":
            return bool(self.items)
        if self.node.choices:
            return self.editor.pressed_index >= 0
        if isinstance(self.editor, TextArea):
            return bool(self.editor.text)
        if isinstance(self.editor, Input):
            return bool(self.editor.value)
        return False


class ResponseForm(Vertical):
    DEFAULT_CSS = "ResponseForm { height: auto; } ResponseForm > .group-label { text-style: bold; color: $accent; margin-bottom: 1; }"

    def __init__(self, form):
        super().__init__()
        self.form = form
        self.editors = []

    def compose(self):
        def controls(node):
            if isinstance(node, Field):
                editor = FieldEditor(node)
                self.editors.append(editor)
                yield editor
            elif isinstance(node, Layout) and FormPrompt.contains_fields(node):
                if node.label:
                    yield Static(text(node.label), classes="group-label", markup=False)
                for child in node.children:
                    yield from controls(child)
        yield from controls(self.form.ui)

    def value(self):
        result = initial_value(self.form.root)
        for editor in self.editors:
            result = set_value(result, editor.node.path, editor.value())
        return result

    def focus_input(self):
        if self.editors:
            self.editors[0].focus_input()
