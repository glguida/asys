"""JSON Schema fields and the JSON Forms UI schema subset used by the terminal.

This module describes controls, not a replacement for JSON Schema validation.
The Human service remains responsible for validating the complete response.
"""
from dataclasses import dataclass, field, replace
import re
from urllib.parse import unquote


class FormError(ValueError):
    pass


MISSING = object()


def title(name):
    return re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(name)).replace("_", " ").capitalize()


@dataclass
class Field:
    path: tuple
    label: str
    schema: dict
    kind: str
    required: bool = True
    nullable: bool = False
    multi: bool = False
    choices: list = field(default_factory=list)
    children: dict = field(default_factory=dict)
    item: object = None


@dataclass
class Layout:
    label: str
    children: list


@dataclass
class Label:
    text: str


class Form:
    def __init__(self, description):
        self.schema = description.get("form", {"type": "string"})
        self.root = self.make_field(self.schema, (), True)
        self.check_constraints(self.root, self.schema)
        self.controls = []
        self.ui = self.make_ui(description["uischema"]) if "uischema" in description else self.root
        if "uischema" in description:
            self.check_coverage(self.root)

    def resolve(self, schema):
        seen = set()
        while isinstance(schema, dict) and "$ref" in schema:
            reference = schema["$ref"]
            if not isinstance(reference, str) or not reference.startswith("#/"):
                raise FormError("Only local JSON Schema references such as #/definitions/address are supported.")
            if reference in seen:
                raise FormError("Recursive JSON Schema references cannot be rendered in this terminal.")
            seen.add(reference)
            target = self.schema
            try:
                for part in unquote(reference[2:]).split("/"):
                    target = target[part.replace("~1", "/").replace("~0", "~")]
            except (KeyError, TypeError):
                raise FormError(f"Cannot resolve JSON Schema reference {reference}.") from None
            # Draft 7 treats a $ref as a reference, not a schema with siblings.
            schema = target
        return schema

    def make_field(self, schema, path, required, depth=0):
        if depth > 24:
            raise FormError("This form is too deeply nested or contains a recursive reference.")
        schema = self.resolve(schema)
        if schema is True:
            schema = {}
        if not isinstance(schema, dict):
            raise FormError("This field has no supported answer schema.")
        if schema.get("readOnly") or schema.get("writeOnly"):
            raise FormError("Read-only and secret fields are not supported by the terminal renderer.")
        label = schema.get("title") or (title(path[-1]) if path else "Answer")
        node = Field(path, label, schema, "string", required)
        if "const" in schema:
            node.kind = "constant"
            return node
        alternatives = schema.get("oneOf")
        if "enum" in schema:
            node.choices = [(value, self.value_label(value)) for value in schema["enum"]]
        elif alternatives and all(isinstance(option, dict) and "const" in option for option in alternatives):
            node.choices = [(option["const"], option.get("title") or self.value_label(option["const"])) for option in alternatives]
        if node.choices:
            node.kind = "choice"
            return node
        if (any(keyword in schema for keyword in ("oneOf", "anyOf", "allOf"))
                and not any(keyword in schema for keyword in ("type", "properties", "items"))):
            raise FormError("Schema combinations need a field type and controls declared outside their branches.")
        kind = schema.get("type") or ("object" if "properties" in schema else "array" if "items" in schema else "string")
        if isinstance(kind, list):
            types = [item for item in kind if item != "null"]
            if "null" not in kind or len(types) != 1:
                raise FormError("A field must have one type, optionally combined with null.")
            kind, node.nullable = types[0], True
        if kind not in {"object", "array", "string", "boolean", "integer", "number", "null"}:
            raise FormError(f"Unsupported field type: {kind}.")
        node.kind = kind
        if kind == "object":
            properties = schema.get("properties", {})
            required_names = set(schema.get("required", []))
            if required_names - properties.keys():
                raise FormError("Required object fields must be described in properties.")
            node.children = {name: self.make_field(child, path + (name,), name in required_names, depth + 1)
                             for name, child in properties.items()}
        elif kind == "array":
            items = schema.get("items", {})
            if isinstance(items, list):
                raise FormError("Tuple arrays are not supported; use an object or an array with one item schema.")
            node.item = self.make_field(items, path + ("item",), True, depth + 1)
        elif kind == "boolean":
            approval = not path or path[-1] == "approved"
            node.choices = [(True, "Approve" if approval else "Yes"), (False, "Disapprove" if approval else "No")]
        return node

    def check_constraints(self, node, schema, depth=0):
        """Branches may validate declared controls; complete validation stays at the worker."""
        if depth > 24:
            raise FormError("This form is too deeply nested or contains a recursive reference.")
        schema = self.resolve(schema)
        if isinstance(schema, bool):
            return
        if not isinstance(schema, dict):
            raise FormError("Invalid constraint schema.")
        if schema.get("readOnly") or schema.get("writeOnly"):
            raise FormError("Read-only and secret fields are not supported by the terminal renderer.")
        if node.kind == "object":
            properties = schema.get("properties", {})
            names = set(properties) | set(schema.get("required", []))
            if names - node.children.keys():
                raise FormError("Fields used by schema branches must also be declared in the object's properties.")
            for name, child in properties.items():
                self.check_constraints(node.children[name], child, depth + 1)
        elif node.kind == "array" and "items" in schema:
            self.check_constraints(node.item, schema["items"], depth + 1)
        for keyword in ("oneOf", "anyOf", "allOf"):
            for branch in schema.get(keyword, []):
                self.check_constraints(node, branch, depth + 1)
        for keyword in ("if", "then", "else"):
            if keyword in schema:
                self.check_constraints(node, schema[keyword], depth + 1)

    @staticmethod
    def value_label(value):
        if value is True:
            return "Yes"
        if value is False:
            return "No"
        if value is None:
            return "None"
        return str(value)

    def scoped_field(self, scope):
        if scope == "#":
            return self.root
        if not isinstance(scope, str) or not scope.startswith("#/"):
            raise FormError("A Control scope must be a JSON Schema pointer.")
        parts = unquote(scope[2:]).split("/")
        node = self.root
        for offset in range(0, len(parts), 2):
            if offset + 1 >= len(parts) or parts[offset] != "properties" or node.kind != "object":
                raise FormError(f"Unsupported Control scope: {scope}.")
            name = parts[offset + 1].replace("~1", "/").replace("~0", "~")
            if name not in node.children:
                raise FormError(f"Control scope does not name a field: {scope}.")
            node = node.children[name]
        return node

    def make_ui(self, ui, depth=0):
        if depth > 24 or not isinstance(ui, dict):
            raise FormError("Invalid or excessively nested UI schema.")
        if "rule" in ui:
            raise FormError("Conditional UI rules are not yet supported by the terminal renderer.")
        kind = ui.get("type")
        if kind != "Control" and ui.get("options"):
            raise FormError("Layout and Label options are not supported by the terminal renderer.")
        if kind == "Control":
            node = self.scoped_field(ui.get("scope"))
            if any(node.path[:len(path)] == path or path[:len(node.path)] == node.path for path in self.controls):
                raise FormError("UI Controls must not address the same field more than once.")
            self.controls.append(node.path)
            options = ui.get("options", {})
            if not isinstance(options, dict) or options.keys() - {"multi", "format"}:
                raise FormError("Supported Control options are multi and format (radio).")
            if "format" in options and options["format"] != "radio":
                raise FormError("The supported Control format is radio.")
            if "multi" in options and (not isinstance(options["multi"], bool) or node.kind != "string"):
                raise FormError("The multi option applies to text fields.")
            label = ui.get("label", node.label)
            if label is True:
                label = node.label
            if label is not False and not isinstance(label, str):
                raise FormError("Control labels must be text or a boolean.")
            return replace(node, label="" if label is False else label, multi=options.get("multi", False))
        if kind == "Label":
            if not isinstance(ui.get("text"), str):
                raise FormError("A Label needs text.")
            return Label(ui["text"])
        if kind in {"VerticalLayout", "HorizontalLayout", "Group", "Categorization", "Category"}:
            if not isinstance(ui.get("elements"), list) or not isinstance(ui.get("label", ""), str):
                raise FormError("A layout needs elements and an optional text label.")
            return Layout(ui.get("label", ""), [self.make_ui(child, depth + 1) for child in ui["elements"]])
        raise FormError(f"Unsupported UI schema element: {kind}.")

    def check_coverage(self, node):
        if any(node.path[:len(path)] == path for path in self.controls):
            return
        descendants = any(path[:len(node.path)] == node.path for path in self.controls)
        if node.required or descendants:
            if node.kind != "object" or node.path and not descendants:
                raise FormError(f"The UI schema has no Control for the required field {node.label}.")
            for child in node.children.values():
                self.check_coverage(child)


def initial_value(node):
    """Materialize required object parents, without selecting any answers."""
    if node.kind != "object":
        return MISSING
    return {name: initial_value(child) for name, child in node.children.items()
            if child.required and child.kind == "object"}


def get_value(value, path):
    for name in path:
        if not isinstance(value, dict) or name not in value:
            return MISSING
        value = value[name]
    return value


def set_value(value, path, answer):
    if not path:
        return answer
    if answer is MISSING and get_value(value, path) is MISSING:
        return value
    if value is MISSING:
        value = {}
    parent = value
    for name in path[:-1]:
        parent = parent.setdefault(name, {})
    if answer is MISSING:
        parent.pop(path[-1], None)
    else:
        parent[path[-1]] = answer
    return value
