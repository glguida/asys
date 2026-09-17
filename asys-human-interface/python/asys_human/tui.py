"""Full-screen JSON Forms host, with a persistent queue and response editor."""
from queue import Empty
from threading import Thread

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Footer, Markdown, Select, Static, Tab, Tabs

from .forms import Field, Form, Label, Layout
from .prompt import text
from .tui_files import FilesView
from .tui_forms import AnswerError, ResponseForm


def markdown(node, depth=2):
    if isinstance(node, Label):
        return text(node.text)
    if isinstance(node, Field):
        return ""
    parts = [markdown(child, depth + bool(node.label)) for child in node.children]
    body = "\n\n".join(part for part in parts if part)
    if body and node.label:
        return "#" * min(depth, 6) + " " + text(node.label) + "\n\n" + body
    return body


def review_sections(document, form):
    """Use the same JSON Forms Labels/Groups as other renderers, in pages."""
    ignored = {document.get("title", ""), document.get("prompt", ""),
               f"{document.get('worker', '')} / {document.get('task', '')}"}
    for entry in document.get("files", []):
        location = entry.get("path") or entry["workerPath"] + " (worker path; not mounted on this host)"
        ignored.add(f"{entry['label']}: {location}")
    sections = []
    notes = []

    def collect(node):
        if isinstance(node, Label):
            if node.text not in ignored:
                notes.append(text(node.text))
        elif isinstance(node, Layout):
            if node.label == "Context":
                for child in node.children:
                    content = markdown(child)
                    if content:
                        sections.append((child.label if isinstance(child, Layout) else "Context", content))
            elif node.label:
                content = markdown(node)
                if content:
                    sections.append((node.label, content))
            else:
                for child in node.children:
                    collect(child)

    collect(form.ui)
    overview = text(document.get("prompt", ""))
    if notes:
        overview += "\n\n" + "\n\n".join(notes)
    if sections:
        overview += "\n\n## Review sections\n\nChoose a section above to read:\n\n" + "\n".join(f"- {text(label)}" for label, _ in sections)
    if document.get("files"):
        overview += "\n\n## Files\n\n"
        for entry in document["files"]:
            label = text(f"{entry['label']}: {entry.get('path') or entry['workerPath']}")
            label = label.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
            overview += f"- [{label}]({entry['uri']})\n" if entry.get("uri") else f"- {label} (worker path)\n"
        overview += "\nSelect the workspace link to browse it in the **Files** tab."
    return [("Overview", overview or "Review this request and enter your response."), *sections]


class HumanApp(App):
    TITLE = "ASYS Human"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        Binding("f2", "review", "Review"),
        Binding("f3", "files", "Files"),
        Binding("f4", "response", "Response"),
        Binding("ctrl+s", "submit", "Submit", priority=True),
        Binding("ctrl+k", "skip", "Skip", priority=True),
        Binding("ctrl+q,ctrl+c", "quit", "Quit", priority=True),
    ]
    CSS = """
    Screen { background: $background; }
    #topbar { height: 1; background: $primary-background; padding: 0 1; }
    #brand { width: 1fr; text-style: bold; color: $accent; }
    #counts { width: auto; color: $text-muted; }
    #task-title { height: 2; padding: 0 1; content-align: left middle; text-style: bold; }
    #origin { height: 1; padding: 0 1; color: $text-muted; text-wrap: nowrap; text-overflow: ellipsis; }
    #tabs { height: 2; }
    #workspace { height: 1fr; padding: 0 1; }
    #reading { width: 1fr; height: 1fr; border: round $border; }
    #review { height: 1fr; }
    #section { margin: 0; }
    #review-scroll { height: 1fr; padding: 0 1; }
    #review-scroll:focus { border: none; }
    Markdown { margin: 0; padding: 0 1; }
    #files { height: 1fr; }
    #answer { width: 40; height: 1fr; border: round $border; margin-left: 1; padding: 0 1; }
    #answer:focus-within { border: round $accent; }
    #response-scroll { height: 1fr; }
    #answer-error { height: auto; max-height: 6; color: $error; }
    #actions { height: 3; padding: 0 1; }
    #actions Button { min-width: 10; margin-right: 1; }
    #status { width: 1fr; content-align: left middle; color: $text-muted; height: 3; }
    .narrow #answer { width: 1fr; margin-left: 0; }
    .narrow #workspace { padding: 0; }
    .narrow #status { text-wrap: nowrap; text-overflow: ellipsis; }
    .narrow #actions { padding: 0; }
    .narrow #task-title { height: 1; }
    Footer { background: $primary-background; }
    """

    def __init__(self, interaction, run_handler=None, *, claimant="", system="asys"):
        super().__init__()
        self.interaction = interaction
        self.run_handler = run_handler
        self.claimant = claimant
        self.system = system
        self.backend = None
        self.document = None
        self.generation = 0
        self.response_form = None
        self.sections = []
        self.submitting = False
        self.reading_tab = "review"
        self.active_tab = "review"
        self.theme = "textual-dark"

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Static("ASYS HUMAN", id="brand", markup=False)
            yield Static(f"{self.claimant} · {self.system}", id="counts", markup=False)
        yield Static("Connecting to human requests…", id="task-title", markup=False)
        yield Static("", id="origin", markup=False)
        yield Tabs(Tab("Review", id="tab-review"), Tab("Files", id="tab-files"), Tab("Response", id="tab-response"), id="tabs")
        with Horizontal(id="workspace"):
            with Vertical(id="reading"):
                with Vertical(id="review"):
                    yield Select([], prompt="Review sections", id="section")
                    with VerticalScroll(id="review-scroll", can_focus=True):
                        yield Markdown("Waiting for a human request.\n\nWorkers send requests through the Human endpoint.", id="review-body", open_links=False)
                yield Vertical(id="files")
            with Vertical(id="answer"):
                yield Static("", id="answer-error", markup=False)
                yield VerticalScroll(Static("Your response form will appear here.", markup=False), id="response-scroll")
        with Horizontal(id="actions"):
            yield Button("Submit", id="submit", variant="primary", disabled=True)
            yield Button("Skip", id="skip", disabled=True)
            yield Static("Starting handler…", id="status", markup=False)
        yield Footer()

    def on_mount(self):
        self.query_one("#answer").border_title = "Response"
        self.set_interval(0.05, self.drain_events)
        self.arrange()
        if self.run_handler:
            def run():
                try:
                    code = self.run_handler()
                except Exception as error:
                    self.interaction.say(f"error: {error}")
                    code = 1
                self.interaction.events.put(("done", code))
            self.backend = Thread(target=run, name="human-handler")
            self.backend.start()

    def on_resize(self, event):
        if self.is_mounted:
            self.arrange(event.size.width)

    def arrange(self, width=None):
        narrow = (self.size.width if width is None else width) < 100
        self.screen.set_class(narrow, "narrow")
        self.query_one("#reading").display = not (narrow and self.active_tab == "response")
        self.query_one("#answer").display = not narrow or self.active_tab == "response"
        self.query_one("#review").display = self.reading_tab == "review"
        self.query_one("#files").display = self.reading_tab == "files"

    def status(self, message):
        self.query_one("#status", Static).update(text(message))

    async def drain_events(self):
        for _ in range(100):
            try:
                kind, value = self.interaction.events.get_nowait()
            except Empty:
                break
            if kind == "notice":
                self.status(value)
            elif kind == "state":
                workers, queued = value
                self.query_one("#counts", Static).update(text(f"{self.claimant} · {workers} workers · {queued} queued"))
            elif kind == "request":
                await self.show_request(*value)
            elif kind == "rejected":
                self.submitting = False
                self.set_busy(False)
                self.query_one("#answer-error", Static).update(text(value))
                self.status("Correct your response and submit again.")
                self.action_response()
            elif kind == "finished":
                await self.clear_request(value)
            elif kind == "done":
                self.exit(value)
                break

    async def show_request(self, generation, document):
        self.generation = generation
        self.document = document
        self.submitting = True
        self.set_busy(True)
        form = Form(document)
        self.sections = review_sections(document, form)
        self.query_one("#task-title", Static).update(text(document.get("title", "Human request")))
        self.query_one("#task-title").tooltip = text(f"{document.get('worker', '')} / {document.get('task', '')}")
        self.query_one("#origin", Static).update(text(f"{document.get('worker', '')} · {document.get('task', '')}"))
        section = self.query_one("#section", Select)
        section.set_options((text(label), index) for index, (label, _) in enumerate(self.sections))
        section.value = 0
        await self.query_one("#review-body", Markdown).update(self.sections[0][1])
        self.query_one("#review-scroll", VerticalScroll).scroll_home(animate=False)
        responses = self.query_one("#response-scroll", VerticalScroll)
        await responses.remove_children()
        self.response_form = ResponseForm(form)
        await responses.mount(self.response_form)
        files = self.query_one("#files", Vertical)
        await files.remove_children()
        await files.mount(FilesView(document.get("files", [])))
        self.query_one("#answer-error", Static).update("")
        self.submitting = False
        self.set_busy(False)
        self.action_review()
        self.status("Review the request, then enter your response. Tab moves between controls.")

    async def clear_request(self, message):
        self.document = None
        self.response_form = None
        self.submitting = False
        self.set_busy(True)
        self.query_one("#task-title", Static).update("Waiting for the next human request")
        self.query_one("#origin", Static).update("")
        self.query_one("#section", Select).set_options([])
        await self.query_one("#review-body", Markdown).update("New requests appear here automatically.\n\nYou can leave this handler running.")
        await self.query_one("#response-scroll", VerticalScroll).remove_children()
        await self.query_one("#files", Vertical).remove_children()
        self.query_one("#answer-error", Static).update("")
        self.action_review()
        self.status(message)

    def set_busy(self, busy):
        self.query_one("#submit", Button).disabled = busy or self.document is None
        self.query_one("#skip", Button).disabled = busy or self.document is None
        self.query_one("#response-scroll").disabled = busy

    async def on_select_changed(self, event):
        if event.select.id == "section" and event.value is not Select.NULL and self.sections:
            event.stop()
            await self.query_one("#review-body", Markdown).update(self.sections[event.value][1])
            self.query_one("#review-scroll", VerticalScroll).scroll_home(animate=False)

    def on_tabs_tab_activated(self, event):
        self.active_tab = event.tab.id.removeprefix("tab-")
        if self.active_tab != "response":
            self.reading_tab = self.active_tab
        self.arrange()

    def action_review(self):
        self.query_one("#tabs", Tabs).active = "tab-review"
        self.active_tab = self.reading_tab = "review"
        self.arrange()
        self.query_one("#review-scroll").focus()

    def action_files(self):
        self.query_one("#tabs", Tabs).active = "tab-files"
        self.active_tab = self.reading_tab = "files"
        self.arrange()

    def action_response(self):
        self.query_one("#tabs", Tabs).active = "tab-response"
        self.active_tab = "response"
        self.arrange()
        if self.response_form:
            self.response_form.focus_input()

    def action_submit(self):
        if self.document is None or self.submitting or self.interaction.stopping.is_set():
            return
        try:
            value = self.response_form.value()
        except AnswerError as error:
            self.query_one("#answer-error", Static).update(text(error))
            self.action_response()
            error.editor.focus_input()
            return
        self.query_one("#answer-error", Static).update("")
        self.submitting = True
        self.set_busy(True)
        self.status("Recording your response…")
        self.interaction.answers.put((self.generation, "submit", value))

    def action_skip(self):
        if self.document is None or self.submitting or self.interaction.stopping.is_set():
            return
        self.submitting = True
        self.set_busy(True)
        self.status("Releasing this request…")
        self.interaction.answers.put((self.generation, "skip", None))

    def on_button_pressed(self, event):
        if event.button.id == "submit":
            self.action_submit()
        elif event.button.id == "skip":
            self.action_skip()

    def action_quit(self):
        self.interaction.stopping.set()
        self.set_busy(True)
        self.status("Releasing the current request and stopping…")
        if not self.run_handler:
            self.exit(0)

    def on_markdown_link_clicked(self, event):
        event.stop()
        if self.document:
            for index, entry in enumerate(self.document.get("files", [])):
                if event.href == entry.get("uri"):
                    self.action_files()
                    self.query_one("#file-source", Select).value = index
                    return
        self.copy_to_clipboard(event.href)
        self.notify("Link copied. Use the Files tab to browse the workspace.")
