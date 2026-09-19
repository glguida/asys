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
            # File names are evidence, not prose to capitalize or split on '_'.
            heading = key if "/" in key or "." in key else title(key)
            if key == "text" and isinstance(item, str):
                elements.append(text(item))
            elif isinstance(item, (dict, list)) or isinstance(item, str) and "\n" in item:
                elements.append(context_ui(item, heading))
            else:
                elements.append(text(f"{heading}: {scalar(item)}"))
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
    if not isinstance(path, str) or not path.startswith("/") or "\x00" in path or ".." in PurePosixPath(path).parts:
        return []
    workspace = PurePosixPath(path)
    component_name = worker.split(".", 1)[0]
    component = next((item for item in topology.get("components", []) if item["name"] == component_name), {})
    files = [{"role": "workspace", "label": "Workspace", "workerPath": str(workspace)}]
    attachments = json.loads(task["inputJson"]).get("files", [])
    for attachment in attachments if isinstance(attachments, list) else []:
        if not isinstance(attachment, dict):
            continue
        raw = attachment.get("path")
        if not isinstance(raw, str) or not raw or "\x00" in raw or ".." in PurePosixPath(raw).parts:
            continue
        location = workspace / raw
        if not location.is_relative_to(workspace) or any(entry["workerPath"] == str(location) for entry in files):
            continue
        label = attachment.get("label")
        entry = {"role": "file", "label": label if isinstance(label, str) and label.strip() else str(location.relative_to(workspace)),
                 "workerPath": str(location)}
        if isinstance(attachment.get("description"), str):
            entry["description"] = attachment["description"]
        files.append(entry)
    for entry in files:
        mapped = host_path(entry["workerPath"], component)
        if mapped:
            entry.update(path=mapped, uri=PurePosixPath(mapped).as_uri())
    return files


def technical_text(document):
    """Keep complete supplied domain data and request identifiers inspectable."""
    return json.dumps(document.get("technical", {"worker": document.get("worker"), "task": document.get("task")}),
                      ensure_ascii=False, indent=2)


def request_document(worker, task, topology):
    """Separate the decision briefing from the technical request record."""
    description = json.loads(task["inputJson"])
    files = task_files(worker, task, topology)
    component = next((item for item in topology.get("components", []) if item["name"] == worker.split(".", 1)[0]), {})
    workspace = next((PurePosixPath(entry["workerPath"]) for entry in files if entry["role"] == "workspace"), None)
    mounts = {kind: [{key: mount[key] for key in ("target", "source") if key in mount}
                     for mount in component.get(kind, []) if workspace and
                     (workspace.is_relative_to(mount["target"]) or PurePosixPath(mount["target"]).is_relative_to(workspace))]
              for kind in ("binds", "volumes")}
    def label(value):
        return {"type": "Label", "text": value}
    elements = [label(description.get("title") or "Human request"), label(description["prompt"])]
    summary = description.get("summary")
    if isinstance(summary, str) and summary.strip():
        elements.append(context_ui(summary, "Work so far"))
    for entry in files:
        location = entry.get("path") or entry["workerPath"] + " (worker path; not mounted on this host)"
        elements.append(label(f"{entry['label']}: {location}"))
    if "context" in description:
        elements.append(context_ui(review_context(description["context"])))
    elements.append(description.get("uischema", {"type": "Control", "scope": "#"}))
    return {"version": 1, "worker": worker, "task": task["id"], "files": files, "fileMounts": mounts,
            "title": description.get("title") or "Human request", "prompt": description["prompt"],
            "summary": summary if isinstance(summary, str) else "",
            "technical": {"worker": worker, "task": task["id"],
                          "metadata": review_context(json.loads(task.get("metadataJson") or "{}")),
                          "request": review_context(description)},
            "form": description.get("form", {"type": "string"}),
            "uischema": {"type": "VerticalLayout", "elements": elements}}
