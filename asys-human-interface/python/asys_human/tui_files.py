"""Browse the project workspace supplied with a human request."""
import asyncio
from pathlib import Path
import re
import subprocess
import sys

from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DirectoryTree, Markdown, Select, Static

from .prompt import text

PREVIEW_LIMIT = 256 * 1024


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
        self.revision = 0

    def compose(self):
        options = [(text(entry["label"]), index) for index, entry in enumerate(self.files)]
        yield Select(options, prompt="No workspace supplied", value=0 if options else Select.NULL, allow_blank=not options, id="file-source")
        yield Static("", classes="file-location", markup=False)
        with Horizontal(classes="file-tools"):
            yield Button("Copy path", name="copy-path", disabled=True)
            yield Button("Open externally", name="open-file", disabled=True)
        yield Horizontal(classes="file-split")

    async def on_select_changed(self, event):
        if event.select.id != "file-source" or event.value is Select.NULL:
            return
        event.stop()
        self.revision += 1
        entry = self.files[event.value]
        raw = entry.get("path")
        self.location = Path(raw) if raw else None
        self.query_one(".file-location", Static).update(text(raw or entry.get("workerPath", "")))
        for button in self.query(Button):
            button.disabled = self.location is None
        split = self.query_one(".file-split", Horizontal)
        await split.remove_children()
        if self.location and self.location.is_dir():
            await split.mount(DirectoryTree(self.location))
            await split.mount(Vertical(Markdown("Select a file to preview it.\n\nUse the arrow keys and Enter to browse.", open_links=False), classes="file-preview"))
        else:
            body = (await asyncio.to_thread(preview, self.location)) if self.location else "This worker path is not mounted on this host."
            await split.mount(Vertical(Markdown(body, open_links=False), classes="file-preview"))

    async def on_directory_tree_file_selected(self, event):
        event.stop()
        self.location = event.path
        self.query_one(".file-location", Static).update(text(event.path))
        revision = self.revision = self.revision + 1
        body = await asyncio.to_thread(preview, event.path)
        if revision == self.revision and self.is_mounted:
            await self.query_one(Markdown).update(body)

    def on_button_pressed(self, event):
        if event.button.name not in {"copy-path", "open-file"}:
            return
        event.stop()
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
