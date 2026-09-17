"""The host side of the human component's single runtime channel."""
from collections import OrderedDict
import json
import time

from asys_runtime.channel import Reader, Writer, direction_root


class RPCError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class Channel:
    def __init__(self, root, claimant, notify=lambda message: None):
        self.input = Writer(direction_root(root, "human", "in"))
        self.output = Reader(direction_root(root, "human", "out"))
        self.claimant = claimant
        self.notify = notify
        self.queue = OrderedDict()
        self.skipped = set()
        self.current = None
        self.waiting = None
        self.reply = None
        self.ready = 0

    def attention(self, worker, task):
        key = (worker, task['id'])
        candidates = json.loads(task['inputJson']).get('candidates', [])
        if (task['status'] == 'pending' and key != self.current and key not in self.skipped
                and (not candidates or self.claimant in candidates)):
            self.queue[key] = task
        else:
            self.queue.pop(key, None)

    def discover(self, poll):
        """Restore pending questions consumed by an earlier terminal session."""
        after = ''
        while True:
            result = self.call('', 'ListTasks', {'status': 'pending', 'afterId': after}, poll)
            for task in result.get('tasks', []):
                self.attention(json.loads(task.get('metadataJson') or '{}').get('component', ''), task)
            after = result.get('nextAfterId', '')
            if not after:
                return

    def pump(self):
        events = self.output.read()
        for event in events:
            data = event["data"]
            if event["type"] == "ready":
                self.ready = event["sequence"]
            elif event["type"] == "attention":
                self.attention(data['worker'], data['task'])
            elif event["type"] in {"result", "error"} and data["request"] == self.waiting and self.reply is None:
                self.reply = event
            self.output.advance(event["sequence"])
        if events:
            self.output.prune()

    def call(self, worker, method, body, poll, timeout=30):
        """Retry transport failures with the caller's unchanged operation IDs."""
        deadline = time.monotonic() + timeout
        try:
            while True:
                self.reply = None
                self.waiting = self.input.send("request", {"worker": worker, "method": method, "body": body})["sequence"]
                while self.reply is None:
                    poll()
                    self.pump()
                    if time.monotonic() >= deadline:
                        raise RPCError("DeadlineExceeded", f"Timed out waiting for {worker} {method}")
                    time.sleep(0.05)
                event = self.reply
                if event["type"] == "result":
                    return event["data"]["result"]
                data = event["data"]
                if data["code"] not in {"Unavailable", "DeadlineExceeded"} or time.monotonic() >= deadline:
                    raise RPCError(data["code"], data["message"])
                # Allow interrupts and component checks during the retry pause.
                until = min(deadline, time.monotonic() + 0.5)
                while time.monotonic() < until:
                    poll()
                    time.sleep(0.05)
        finally:
            self.waiting = None
            self.reply = None
