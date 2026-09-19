"""Browse the project workspace supplied with a human request."""
import asyncio
from pathlib import Path, PurePosixPath
import posixpath
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit

from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DirectoryTree, Markdown, Select, Static

from .prompt import text
from .presentation import host_path
from .tui_markdown import ReviewMarkdown

PREVIEW_LIMIT = 256 * 1024


def resolve_file_link(href, files, mounts=None, current=None):
    """Resolve a Textual link (already URL-decoded) within the worker workspace."""
    for entry in files:
        if href == entry.get("workerPath") or entry.get("uri") and href == unquote(entry["uri"]):
            if entry.get("path"):
                return Path(entry["path"]), ""
            raise ValueError(f"This worker path is not mounted on this host: {entry['workerPath']}")
    root = next((entry for entry in files if entry["role"] == "workspace"), None)
    if root is None:
        raise ValueError("No project workspace was supplied for this request.")
    link = urlsplit(href)
    if link.scheme not in {"", "file"} or link.netloc not in {"", "localhost"} or "\x00" in link.path:
        raise ValueError(f"Not a local workspace file link: {href}")
    worker_root = PurePosixPath(root["workerPath"])
    component = mounts or {"binds": [{"target": str(worker_root), "source": root.get("path")}]}
    location = PurePosixPath(link.path)
    from_preview = not location.is_absolute() and current is not None
    if not location.is_absolute():
        base = (current if current.is_dir() else current.parent) if from_preview else worker_root
        location = PurePosixPath(posixpath.normpath(str(base / location)))
    else:
        location = PurePosixPath(posixpath.normpath(str(location)))
    if from_preview or not location.is_relative_to(worker_root):
        matches = []
        for mount in component.get("binds", []):
            source = mount.get("source")
            if source and location.is_relative_to(source):
                candidate = PurePosixPath(mount["target"]) / location.relative_to(source)
                if candidate.is_relative_to(worker_root):
                    matches.append((len(PurePosixPath(source).parts), candidate))
        if not matches:
            raise ValueError(f"File is outside this request's workspace: {link.path}")
        location = max(matches, key=lambda item: item[0])[1]
    if not location.is_relative_to(worker_root):
        raise ValueError(f"File is outside this request's workspace: {link.path}")
    mapped = host_path(str(location), component)
    if not mapped:
        raise ValueError(f"This worker path is not mounted on this host: {location}")
    return Path(mapped), link.fragment


def preview(path):
    if path.suffix.lower() in {".kicad_pcb", ".kicad_sch", ".kicad_pro", ".png", ".jpg", ".jpeg", ".pdf", ".svg", ".step", ".stp"}:
        if not path.exists():
            return "This file is not available yet."
        return f"## {text(path.name)}\n\nUse **Open externally** to inspect this file in its viewer.\n\nThe path above identifies the selected artifact."
    try:
        with path.open("rb") as source:
            data = source.read(PREVIEW_LIMIT + 1)
        if b"\x00" in data:
            return "This is a binary file. Use **Open externally** to view it."
        content = data[:PREVIEW_LIMIT].decode("utf-8", errors="replace")
        if path.suffix.lower() not in {".md", ".markdown"}:
            fence = "`" * max(3, max((len(part) for part in re.findall(r"`+", content)), default=0) + 1)
            content = f"{fence}\n{text(content)}\n{fence}"
        else:
            content = text(content)
        if len(data) > PREVIEW_LIMIT:
            content += "\n\n**Preview truncated at 256 KiB. Open the file to read the rest.**"
        return content or "*Empty file.*"
    except OSError as error:
        return f"Cannot read this file: {text(error)}"


class FilesView(Vertical):
    DEFAULT_CSS = """
    FilesView { height: 1fr; }
    FilesView > Select { margin: 0; }
    FilesView .file-location { height: auto; max-height: 5; color: $text-muted; padding: 0 1; }
    FilesView .file-tools { height: 3; }
    FilesView .file-tools Button { min-width: 10; width: 1fr; }
    FilesView .file-split { height: 1fr; }
    FilesView DirectoryTree { width: 35%; min-width: 15; border-right: solid $border; }
    FilesView .file-preview { width: 1fr; height: 1fr; overflow-y: auto; }
    """

    def __init__(self, files):
        super().__init__()
        self.files = files
        self.location = None
        self.history = []
        # Initial selection and link clicks arrive on different message pumps.
        self._navigation = asyncio.Lock()

    def compose(self):
        options = [(text(entry["label"]), index) for index, entry in enumerate(self.files)]
        yield Select(options, prompt="No files supplied", value=0 if options else Select.NULL, allow_blank=not options, id="file-source")
        yield Static("", classes="file-location", markup=False)
        with Horizontal(classes="file-tools"):
            yield Button("Back", name="back-file", disabled=True)
            yield Button("Copy path", name="copy-path", disabled=True)
            yield Button("Open externally", name="open-file", disabled=True)
        yield Horizontal(classes="file-split")

    async def on_select_changed(self, event):
        if event.select.id != "file-source" or event.value is Select.NULL:
            return
        event.stop()
        entry = self.files[event.value]
        if entry.get("path"):
            await self.open_path(Path(entry["path"]))
        else:
            await self.show_unavailable(entry.get("workerPath", ""), "This worker path is not mounted on this host.")

    async def show_unavailable(self, path, reason):
        async with self._navigation:
            self.location = None
            self.query_one(".file-location", Static).update(text(path))
            for button in self.query(Button):
                button.disabled = button.name != "back-file" or not self.history
            split = self.query_one(".file-split", Horizontal)
            await split.remove_children()
            document = ReviewMarkdown()
            await split.mount(Vertical(document, classes="file-preview"))
            await document.update("## Cannot open file\n\n" + text(reason))

    async def open_path(self, path, *, remember=True, anchor=""):
        async with self._navigation:
            self.location = path
            if remember and (not self.history or self.history[-1] != path):
                self.history.append(path)
            self.query_one(".file-location", Static).update(text(path))
            for button in self.query(Button):
                button.disabled = len(self.history) < 2 if button.name == "back-file" else False
            split = self.query_one(".file-split", Horizontal)
            if path.is_dir() or not split.query(Markdown):
                await split.remove_children()
                root = path if path.is_dir() else next((Path(entry["path"]) for entry in self.files
                                                       if entry["role"] == "workspace" and entry.get("path")), None)
                if root and root.is_dir():
                    await split.mount(DirectoryTree(root))
                await split.mount(Vertical(ReviewMarkdown(), classes="file-preview"))
            body = "Select a file to read it here.\n\nClick a filename, or use the arrow keys and Enter." if path.is_dir() else await asyncio.to_thread(preview, path)
            if self.is_mounted:
                document = self.query_one(Markdown)
                await document.update(body)
                self.query_one(".file-preview").scroll_home(animate=False)
                if anchor:
                    document.goto_anchor(anchor)

    async def on_directory_tree_file_selected(self, event):
        event.stop()
        await self.open_path(event.path)

    async def on_button_pressed(self, event):
        if event.button.name not in {"copy-path", "open-file", "back-file"}:
            return
        event.stop()
        if event.button.name == "back-file":
            if self.location is not None and len(self.history) > 1:
                self.history.pop()
            if self.history:
                await self.open_path(self.history[-1], remember=False)
            return
        if self.location is None:
            return
        if event.button.name == "copy-path":
            self.app.copy_to_clipboard(str(self.location))
            self.app.notify("Path copied (if supported by your terminal).")
        else:
            try:
                command = "open" if sys.platform == "darwin" else "xdg-open"
                subprocess.Popen([command, str(self.location)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
            except OSError as error:
                self.app.notify(text(error), severity="error")
