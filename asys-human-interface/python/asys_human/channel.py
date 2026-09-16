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
        self.unavailable = {}
        self.reported_unavailable = set()

    def pump(self):
        events = self.output.read()
        for event in events:
            data = event["data"]
            if event["type"] == "ready":
                self.ready = event["sequence"]
                self.unavailable.clear()  # The new bridge opens fresh subscriptions.
                self.reported_unavailable.intersection_update(data["workers"])
            elif event["type"] == "attention":
                task = data["task"]
                key = (data["worker"], task["id"])
                candidates = json.loads(task["inputJson"]).get("candidates", [])
                if (task["status"] == "pending" and key != self.current and key not in self.skipped
                        and (not candidates or self.claimant in candidates)):
                    self.queue[key] = task  # Updating a duplicate preserves its FIFO position.
                else:
                    self.queue.pop(key, None)
            elif event["type"] in {"result", "error"} and data["request"] == self.waiting and self.reply is None:
                self.reply = event
            elif event["type"] == "worker.unavailable":
                self.unavailable[data["worker"]] = data["message"]
            elif event["type"] == "worker.available":
                self.unavailable.pop(data["worker"], None)
                self.reported_unavailable.discard(data["worker"])
            self.output.advance(event["sequence"])
        if events:
            self.output.prune()

    def report_unavailable(self, workers):
        """Report outages only after the launcher has refreshed dcomp topology.

        Removing a finished workflow closes its worker subscriptions before
        discovery notices the removal. That close is ordinary lifecycle activity.
        """
        current = set(workers)
        self.unavailable = {worker: message for worker, message in self.unavailable.items() if worker in current}
        self.reported_unavailable.intersection_update(current)
        for worker in self.unavailable:
            if worker not in self.reported_unavailable:
                self.notify(f"Waiting for {worker} to reconnect.")
                self.reported_unavailable.add(worker)

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
