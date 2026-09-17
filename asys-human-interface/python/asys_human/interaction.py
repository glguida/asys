"""Thread boundary between the terminal and the single owner of the host channel."""
from queue import Empty, Queue
from threading import Event

from .forms import Form
from .prompt import Quit, Skip


class Interaction:
    def __init__(self):
        self.events = Queue()
        self.answers = Queue()
        self.stopping = Event()
        self.generation = 0
        self.last_state = None
        self.errors = []

    def say(self, message):
        if str(message).startswith(("error:", "Could not ", "Component cleanup is incomplete:")):
            self.errors.append(str(message))
        self.events.put(("notice", str(message)))

    def state(self, workers, queued):
        value = (len(workers), queued)
        if value != self.last_state:
            self.last_state = value
            self.events.put(("state", value))

    def form(self, document):
        Form(document)  # Fail before displaying or accepting an unsupported form.
        self.generation += 1
        self.events.put(("request", (self.generation, document)))
        return self

    def read(self, tick):
        while True:
            tick()  # Channel events continue while the human edits.
            if self.stopping.is_set():
                raise Quit()
            try:
                generation, action, value = self.answers.get(timeout=0.05)
            except Empty:
                continue
            if generation != self.generation:
                continue
            if action == "skip":
                raise Skip()
            if action == "submit":
                return value

    def rejected(self, message):
        self.events.put(("rejected", str(message)))

    def finished(self, message):
        self.events.put(("finished", message))
