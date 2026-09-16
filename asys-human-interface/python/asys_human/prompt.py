"""Text presentation; task schemas and decisions remain owned by the worker."""
import math
import os
import select
import sys
from dataclasses import replace

from .forms import Field, Form, Label, Layout, MISSING, get_value, initial_value, set_value


class Quit(Exception):
    pass


class Skip(Exception):
    pass


def text(value):
    # Task content can contain terminal controls. Display them as text.
    return "".join(char if char in "\n\t" or (ord(char) >= 32 and not 127 <= ord(char) <= 159)
                   else f"\\x{ord(char):02x}" for char in str(value))


class FormPrompt:
    def __init__(self, description, output=sys.stdout):
        self.form = Form(description)
        self.output = output
        self.draft = initial_value(self.form.root)
        self.show_labels = True

    def say(self, message=""):
        print(text(message), file=self.output, flush=True)

    def line(self, prompt, readline):
        print(text(prompt), end="", file=self.output, flush=True)
        value = readline()
        if value.startswith("//"):
            return value[1:], ""  # Escape a literal slash command in a text answer.
        command = value.strip().lower()
        if command == "/quit":
            raise Quit()
        if command == "/skip":
            raise Skip()
        return value, command

    def yes_no(self, prompt, readline):
        while True:
            _, command = self.line(prompt + " [y/n]: ", readline)
            if command in {"y", "yes"}:
                return True
            if command in {"n", "no"}:
                return False
            self.say("Enter y or n; /skip leaves the request unanswered, /quit stops the handler.")

    def read(self, readline):
        """Keep the draft for edits and for retries after worker validation."""
        self.say("Use /skip to leave this request or /quit to stop. Prefix a literal slash command with another slash.")
        while True:
            self.collect(self.form.ui, readline)
            self.show_labels = False
            self.say("\nResponse:")
            self.review(self.form.ui)
            if self.yes_no("Submit response?", readline):
                return self.draft
            self.say("Edit the fields below. Enter keeps a current answer; /omit removes an optional answer.")

    def collect(self, node, readline, indent=""):
        if isinstance(node, Label):
            if self.show_labels:
                self.say("\n".join(indent + line if line else "" for line in node.text.split("\n")))
        elif isinstance(node, Layout):
            if not self.show_labels and not self.contains_fields(node):
                return
            if node.label:
                self.say("\n" + indent + node.label)
            for child in node.children:
                self.collect(child, readline, indent + ("  " if node.label else ""))
        else:
            answer = self.read_field(node, get_value(self.draft, node.path), readline)
            self.draft = set_value(self.draft, node.path, answer)

    @staticmethod
    def contains_fields(node):
        return isinstance(node, Field) or isinstance(node, Layout) and any(FormPrompt.contains_fields(child) for child in node.children)

    def read_field(self, node, previous, readline):
        if node.schema.get("description"):
            self.say(node.schema["description"])
        if node.kind == "constant":
            self.say(f"{node.label}: {self.display(node.schema['const'])} (fixed)")
            return node.schema["const"]
        if node.kind == "null":
            return None
        if node.kind in {"object", "array"}:
            if not node.required and not self.yes_no(f"Include {node.label}?", readline):
                return MISSING
            if node.nullable and self.yes_no(f"Set {node.label} to null?", readline):
                return None
            if node.kind == "object":
                if node.path or node.label != "Answer":
                    self.say("\n" + node.label)
                answer = {}
                for name, child in node.children.items():
                    value = self.read_field(child, get_value(previous, (name,)), readline)
                    if value is not MISSING:
                        answer[name] = value
                return answer
            size_schema = {"type": "integer", "minimum": node.schema.get("minItems", 0)}
            if "maxItems" in node.schema:
                size_schema["maximum"] = node.schema["maxItems"]
            size_field = Field((), f"{node.label} — number of items", size_schema, "integer")
            size = self.read_field(size_field, len(previous) if isinstance(previous, list) else MISSING, readline)
            return [self.read_field(replace(node.item, label=f"{node.label} {index + 1}"),
                                    previous[index] if isinstance(previous, list) and index < len(previous) else MISSING, readline)
                    for index in range(size)]

        while True:
            if node.choices:
                self.say(node.label or "Choose an answer")
                for index, (_, label) in enumerate(node.choices, 1):
                    self.say(f"  {index}. {label}")
            if previous is not MISSING:
                self.say(f"Current: {self.display(previous, node)}")
            elif "default" in node.schema:
                self.say(f"Suggested: {self.display(node.schema['default'], node)}")
            hints = []
            if previous is not MISSING:
                hints.append("Enter to keep" if not node.multi else "/keep to keep")
            elif not node.required:
                hints.append("optional; Enter to omit" if not node.multi else "optional")
            if not node.required:
                hints.append("/omit to omit")
            if node.nullable:
                hints.append("/null for null")
            if node.multi:
                hints.append("/done to finish")
            label = "Choose" if node.choices else node.label or "Answer"
            prompt = label + (" (" + "; ".join(hints) + ")" if hints else "") + ": "
            try:
                if node.multi:
                    self.say(prompt)
                    value = self.multiline(node, previous, readline)
                else:
                    raw, command = self.line(prompt, readline)
                    if command == "/omit":
                        if node.required:
                            raise ValueError("This field is required.")
                        value = MISSING
                    elif command == "/null" and node.nullable:
                        value = None
                    elif command == "/empty" and node.kind == "string":
                        value = ""
                    elif raw == "" and previous is not MISSING:
                        value = previous
                    elif raw == "" and not node.required:
                        value = MISSING
                    else:
                        value = self.parse(node, raw)
                self.check(node, value)
                return value
            except ValueError as error:
                self.say(str(error))

    def multiline(self, node, previous, readline):
        lines = []
        while True:
            raw, command = self.line("> ", readline)
            if command == "/done":
                return "\n".join(lines) if lines or node.required else MISSING
            if command == "/keep" and previous is not MISSING and not lines:
                return previous
            if command == "/omit":
                if node.required:
                    self.say("This field is required.")
                    continue
                return MISSING
            if command == "/empty" and not lines:
                return ""
            if command == "/null" and node.nullable and not lines:
                return None
            lines.append(raw)

    @staticmethod
    def parse(node, raw):
        if node.choices:
            choice = raw.strip().casefold()
            if choice.isascii() and choice.isdigit() and 1 <= int(choice) <= len(node.choices):
                return node.choices[int(choice) - 1][0]
            matching = [value for value, label in node.choices if str(label).casefold() == choice]
            if len(matching) == 1:
                return matching[0]
            if node.kind == "boolean":
                if choice in {"a", "approve", "y", "yes", "true"}:
                    return True
                if choice in {"d", "disapprove", "n", "no", "false"}:
                    return False
            raise ValueError("Choose an option by number or label.")
        if node.kind == "string":
            return raw
        if node.kind == "integer":
            try:
                return int(raw.strip(), 10)
            except ValueError:
                raise ValueError("Enter a whole number.") from None
        if node.kind == "number":
            try:
                value = float(raw.strip())
                if math.isfinite(value):
                    return value
            except ValueError:
                pass
            raise ValueError("Enter a finite number.")
        raise ValueError("This answer type cannot be entered as text.")

    @staticmethod
    def check(node, value):
        # Immediate field feedback only. AJV at the worker validates every
        # keyword and every cross-field constraint before accepting an answer.
        if value is MISSING or value is None:
            return
        if isinstance(value, str):
            if len(value) < node.schema.get("minLength", 0):
                raise ValueError(f"Enter at least {node.schema['minLength']} characters.")
            if "maxLength" in node.schema and len(value) > node.schema["maxLength"]:
                raise ValueError(f"Enter at most {node.schema['maxLength']} characters.")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            for keyword, valid, wording in [
                ("minimum", lambda limit: value >= limit, "at least"),
                ("maximum", lambda limit: value <= limit, "at most"),
                ("exclusiveMinimum", lambda limit: value > limit, "greater than"),
                ("exclusiveMaximum", lambda limit: value < limit, "less than"),
            ]:
                if keyword in node.schema and not valid(node.schema[keyword]):
                    raise ValueError(f"Enter a value {wording} {node.schema[keyword]}.")

    @staticmethod
    def display(value, node=None):
        if value is MISSING:
            return "(omitted)"
        if node:
            for choice, label in node.choices:
                if type(choice) is type(value) and choice == value:
                    return label
        if isinstance(value, str):
            return value if value else "(empty text)"
        return Form.value_label(value)

    def review(self, node, value=MISSING, indent="  "):
        if isinstance(node, Label):
            return
        if isinstance(node, Layout):
            if not self.contains_fields(node):
                return
            if node.label:
                self.say(indent + node.label)
            for child in node.children:
                self.review(child, indent=indent)
            return
        if value is MISSING:
            value = get_value(self.draft, node.path)
        if node.kind == "object" and isinstance(value, dict):
            if node.path:
                self.say(indent + node.label)
                indent += "  "
            for name, child in node.children.items():
                self.review(child, value.get(name, MISSING), indent)
        elif node.kind == "array" and isinstance(value, list):
            self.say(f"{indent}{node.label}: {len(value)} item(s)")
            for index, item in enumerate(value, 1):
                self.review(replace(node.item, label=str(index)), item, indent + "  ")
        else:
            self.say(f"{indent}{node.label or 'Answer'}: {self.display(value, node)}")


class TextInput:
    """Wait for a complete line while the launcher continues handling events."""
    def __init__(self, stream=sys.stdin):
        self.fd = stream.fileno()
        self.buffer = b""
        self.eof = False

    def collect(self, timeout=0):
        if not self.eof and select.select([self.fd], [], [], timeout)[0]:
            chunk = os.read(self.fd, 4096)
            self.eof = not chunk
            self.buffer += chunk

    def idle(self):
        self.collect()
        if self.eof and not self.buffer:
            raise EOFError()
        if self.buffer.split(b"\n", 1)[0].strip() == b"/quit":
            raise Quit()

    def read(self, poll):
        while b"\n" not in self.buffer:
            poll()
            if self.eof:
                if not self.buffer:
                    raise EOFError()
                value, self.buffer = self.buffer, b""
                return value.decode("utf-8", errors="replace")
            self.collect(0.1)
        value, self.buffer = self.buffer.split(b"\n", 1)
        return value.rstrip(b"\r").decode("utf-8", errors="replace")
