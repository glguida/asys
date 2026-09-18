"""Render the saved agent conversation and its currently streaming response."""
import json
from pathlib import Path
import textwrap

from .runs import Files, LogReader, tail


def session_entries(session):
    entries = session.get("entries", [])
    leaf = session.get("leafId")
    if not leaf:
        return entries
    by_id = {entry.get("id"): entry for entry in entries}
    branch, seen = [], set()
    while leaf in by_id and leaf not in seen:
        seen.add(leaf)
        entry = by_id[leaf]
        branch.append(entry)
        leaf = entry.get("parentId")
    return list(reversed(branch))


def render(agent):
    lines = []

    def add(label, text):
        lines.extend([label, *str(text).splitlines(), ""])

    for entry in session_entries(agent.get("session", {})):
        if entry.get("type") == "compaction":
            add("Context compacted", entry.get("summary", ""))
            continue
        if entry.get("type") != "message":
            continue
        message = entry.get("message", {})
        role = message.get("role", "message")
        label = {"user": "User", "assistant": "Assistant", "system": "System"}.get(role, role)
        if role == "toolResult":
            label = f"Tool result: {message.get('toolName', 'tool')}{' (error)' if message.get('isError') else ''}"
        content = message.get("content", [])
        if isinstance(content, str):
            add(label, content)
            continue
        for block in content:
            kind = block.get("type")
            if kind == "text":
                add(label, block.get("text", ""))
            elif kind == "thinking" and block.get("thinking"):
                add("Thinking", block["thinking"])
            elif kind == "toolCall":
                arguments = block.get("arguments", {})
                if isinstance(arguments, dict):
                    text = "\n".join(f"{key}: {value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)}"
                                     for key, value in arguments.items())
                else:
                    text = json.dumps(arguments, ensure_ascii=False)
                add(f"Tool call: {block.get('name', 'tool')}", text)
            elif kind == "image":
                add(label, f"[Image: {block.get('mimeType', 'image')}]")
        if message.get("errorMessage"):
            add("Error", message["errorMessage"])
    if not lines:
        if agent.get("prompt"):
            add("User", agent["prompt"])
        lines.append("Waiting for the agent's next saved message.")
    return lines


def wrap(lines, width):
    result = []
    for line in lines:
        # Never let saved output send terminal control characters to curses.
        line = "".join(c if c.isprintable() else " " for c in line.expandtabs(4))
        result.extend(textwrap.wrap(line, width=max(1, width), replace_whitespace=False,
                                    drop_whitespace=False, break_on_hyphens=False) or [""])
    return result


class JobOutput:
    def __init__(self):
        self.key = None
        self.saved = None
        self.lines = []
        self.files = Files()
        self.reader = None
        self.live = None

    def read(self, job):
        directory = Path(job["directory"])
        key = directory
        if self.key != key:
            self.key, self.saved, self.files = key, None, Files()
            self.reader, self.live = LogReader(directory / "stdout.log", None), None
        document = self.files.read(directory / "agent.json")
        agent = document.get("agent")
        errors = [f"stderr: {line}" for line in tail(directory / "stderr.log", 100)]
        goal = self.files.read(directory / 'goal.json')
        if goal.get('version') == 1 and isinstance(goal.get('sessions'), list):
            return 'Goal', self.read_goal(directory, goal) + errors
        if isinstance(agent, dict):
            if self.saved is not document:
                self.saved, self.lines = document, render(agent)
            self.read_stream()
            live = []
            if self.live and self.live["parentId"] == agent.get("session", {}).get("leafId"):
                for _, (kind, chunks) in sorted(self.live["blocks"].items()):
                    live += ["Assistant" if kind == "text" else "Thinking", *"".join(chunks).splitlines(), ""]
            return "Transcript", self.lines + live + errors
        output = [f"stdout: {line}" for line in tail(directory / "stdout.log", 100)]
        return "Logs", output + errors

    def read_goal(self, directory, goal):
        lines = [f"Goal: {goal.get('goal', '')}", f"Status: {goal.get('status', '')}", '']
        current = None
        for session in goal['sessions']:
            relative = session.get('directory', '')
            path = (directory / relative).resolve()
            if not relative or not path.is_relative_to(directory.resolve()):
                continue
            lines += [f"Attempt {session['attempt']}: {session['phase']} ({session['status']})", '']
            current = self.files.read(path / 'agent.json').get('agent')
            if isinstance(current, dict):
                lines += render(current)
            elif session.get('result'):
                lines += [session['result'].get('final', ''), '']
            request = self.files.read(path / 'human.request.json')
            if request:
                lines += ['Human help', request.get('summary', ''), request.get('prompt', ''), '']
                answer = self.files.read(path / 'human.result.json')
                if answer:
                    lines += [f"Human: {answer.get('action', '')}", answer.get('guidance', ''), '']
        self.read_stream()
        if isinstance(current, dict) and self.live and self.live['parentId'] == current.get('session', {}).get('leafId'):
            for _, (kind, chunks) in sorted(self.live['blocks'].items()):
                lines += ['Assistant' if kind == 'text' else 'Thinking', *''.join(chunks).splitlines(), '']
        return lines

    def read_stream(self):
        for line in self.reader.read():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "agent.message_started":
                self.live = {"parentId": event.get("parentId"), "blocks": {}}
            elif event.get("type") == "agent.message_delta" and self.live is not None:
                kind, index, delta = event.get("kind"), event.get("contentIndex"), event.get("delta")
                if kind not in {"text", "thinking"} or type(index) is not int or index < 0 or not isinstance(delta, str):
                    continue
                block = self.live["blocks"].setdefault(index, (kind, []))
                if block[0] == kind:
                    block[1].append(delta)
