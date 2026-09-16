"""Build a JSON Forms document for a whole human request, including its files."""
import json
from pathlib import PurePosixPath

from .forms import title


AGENT_METADATA = {"message", "steps", "workspace", "api", "provider", "model", "usage",
                  "stopReason", "rawStopReason", "timestamp", "responseId"}


def assistant_text(message):
    return "\n\n".join(part["text"] for part in message.get("content", [])
                       if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str))


def review_context(value):
    """Keep domain results and prose, excluding the enclosing model transcript."""
    if isinstance(value, dict):
        if value.get("role") == "assistant" and isinstance(value.get("content"), list):
            return assistant_text(value)
        message = value.get("message")
        if isinstance(message, dict) and message.get("role") == "assistant" and isinstance(message.get("content"), list):
            result = {key: review_context(item) for key, item in value.items() if key not in AGENT_METADATA}
            if not result.get("text"):
                result["text"] = assistant_text(message)
            return result
        return {key: review_context(item) for key, item in value.items()}
    if isinstance(value, list):
        return [review_context(item) for item in value]
    return value


def context_ui(value, label="Context"):
    """Describe readable context using standard JSON Forms Groups and Labels."""
    def text(content):
        return {"type": "Label", "text": content}

    def scalar(item):
        if item is True:
            return "Yes"
        if item is False:
            return "No"
        if item is None:
            return "Not provided"
        return str(item)

    elements = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "text" and isinstance(item, str):
                elements.append(text(item))
            elif isinstance(item, (dict, list)) or isinstance(item, str) and "\n" in item:
                elements.append(context_ui(item, title(key)))
            else:
                elements.append(text(f"{title(key)}: {scalar(item)}"))
    elif isinstance(value, list):
        for index, item in enumerate(value, 1):
            if isinstance(item, (dict, list)):
                elements.append(context_ui(item, f"{index}."))
            else:
                elements.append(text(f"{index}. {scalar(item)}"))
    else:
        elements.append(text(scalar(value)))
    return {"type": "Group", "label": label, "elements": elements or [text("None")]}


def host_path(path, component):
    """Resolve a worker path through its actual mounts, without reading job files."""
    if not isinstance(path, str) or not path.startswith("/") or "\x00" in path:
        return None
    location = PurePosixPath(path)
    if ".." in location.parts:
        return None
    matches = []
    for mount in [*component.get("binds", []), *component.get("volumes", [])]:
        target = PurePosixPath(mount["target"])
        try:
            relative = location.relative_to(target)
        except ValueError:
            continue
        matches.append((len(target.parts), mount.get("source"), relative))
    if not matches:
        return None
    # A nested volume masks an outer bind. Do not invent a host path for it.
    _, source, relative = max(matches, key=lambda match: match[0])
    if not source or not PurePosixPath(source).is_absolute():
        return None
    return str(PurePosixPath(source) / relative)


def task_files(worker, task, topology):
    metadata = json.loads(task.get("metadataJson") or "{}")
    path = metadata.get("files", {}).get("workspace")
    if not isinstance(path, str) or not path:
        return []
    component_name = worker.split(".", 1)[0]
    component = next((item for item in topology.get("components", []) if item["name"] == component_name), {})
    entry = {"role": "workspace", "label": "Workspace", "workerPath": path}
    mapped = host_path(path, component)
    if mapped:
        entry.update(path=mapped, uri=PurePosixPath(mapped).as_uri())
    return [entry]


def request_document(worker, task, topology):
    """The renderer consumes this document; it has no separate question header."""
    description = json.loads(task["inputJson"])
    files = task_files(worker, task, topology)
    def label(value):
        return {"type": "Label", "text": value}
    elements = [label(description.get("title") or "Human request"), label(f"{worker} / {task['id']}"), label(description["prompt"])]
    for entry in files:
        location = entry.get("path") or entry["workerPath"] + " (worker path; not mounted on this host)"
        elements.append(label(f"{entry['label']}: {location}"))
    if "context" in description:
        elements.append(context_ui(review_context(description["context"])))
    elements.append(description.get("uischema", {"type": "Control", "scope": "#"}))
    return {"version": 1, "worker": worker, "task": task["id"], "files": files,
            "title": description.get("title") or "Human request", "prompt": description["prompt"],
            "form": description.get("form", {"type": "string"}),
            "uischema": {"type": "VerticalLayout", "elements": elements}}
