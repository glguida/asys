"""Ordered event streams between a component and the host over the shared root.

A channel has two directions. Each direction is a directory of events, one file
per event, named by sequence number. Publishing is an atomic link, so sequence
numbers are claimed at publish time: a reader that sees event N knows every
event below N was published, even if later pruned. Readers keep a cursor in the direction
directory. A short transaction serializes publication and pruning; there is
no long-lived owner and nothing here executes anything.
"""

import json
import os
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import time

from .files import read_json, sync_directory, timestamp, validate_name, write_json
from .permissions import mkdir

DIRECTIONS = ("in", "out")
POLL_SECONDS = 0.05
WIDTH = 9
LIMIT = 10 ** WIDTH - 1


def channel_root(root, name):
    validate_name("channel name", name)
    return Path(root).expanduser().resolve() / "channels" / name


def direction_root(root, name, direction):
    if direction not in DIRECTIONS:
        raise ValueError(f"channel direction must be one of {', '.join(DIRECTIONS)}")
    return channel_root(root, name) / direction


def event_path(directory, sequence):
    return Path(directory) / f"{sequence:0{WIDTH}d}.json"


def parse_sequence(filename):
    stem, dot, suffix = filename.partition(".")
    if dot and suffix == "json" and len(stem) == WIDTH and stem.isdigit():
        return int(stem)
    return None


def validate_event(value, directory, sequence=None):
    if (not isinstance(value, dict) or value.get("version") != 1 or type(value.get("sequence")) is not int
            or not isinstance(value.get("type"), str) or not isinstance(value.get("time"), str)
            or "data" not in value):
        raise ValueError(f"{directory}: invalid event {sequence}")
    if sequence is not None and value["sequence"] != sequence:
        raise ValueError(f"{directory}: event file {sequence} claims sequence {value['sequence']}")
    validate_name("event type", value["type"])
    return value


class Stream:
    """One direction of a channel."""

    def __init__(self, directory):
        self.directory = Path(directory).expanduser().resolve()
        mkdir(self.directory, parents=True, exist_ok=True)
        # Explicitly shared directions grant the same read/write permissions
        # to their files. The default direction remains private (0700/0600).
        self.mode = self.directory.stat().st_mode & 0o666

    @contextmanager
    def exclusive(self):
        # Python and Node both have SQLite in their standard libraries. Use
        # its OS-backed transaction lock only: all channel data stays in JSON.
        # Never unlink this file: a killed holder releases the lock itself.
        path = self.directory / ".write-lock.sqlite"
        publish_text(path, "", self.mode)
        connection = sqlite3.connect(path, timeout=30, isolation_level=None)
        try:
            connection.execute("BEGIN EXCLUSIVE")
            yield
            connection.execute("COMMIT")
        finally:
            connection.close()

    def sequences(self):
        return sorted(s for s in (parse_sequence(p.name) for p in self.directory.iterdir()) if s is not None)

    def last(self):
        sequences = self.sequences()
        return sequences[-1] if sequences else 0

    def event(self, sequence):
        return validate_event(read_json(event_path(self.directory, sequence)), self.directory, sequence)


class Writer(Stream):
    def send(self, event_type, data=None):
        validate_name("event type", event_type)
        json.dumps(data, allow_nan=False)
        with self.exclusive():
            sequence = self.last() + 1
            if sequence > LIMIT:
                raise ValueError(f"{self.directory}: channel sequence exhausted")
            event = {"version": 1, "sequence": sequence, "type": event_type, "time": timestamp(), "data": data}
            if not publish_json(event_path(self.directory, sequence), event):
                raise ValueError(f"{self.directory}: concurrent publisher did not hold the channel lock")
            return event


class Reader(Stream):
    def __init__(self, directory):
        super().__init__(directory)
        self.cursor_path = self.directory / "cursor.json"

    @property
    def cursor(self):
        try:
            value = read_json(self.cursor_path)
        except FileNotFoundError:
            return 0
        if not isinstance(value, dict) or type(value.get("after")) is not int or value["after"] < 0:
            raise ValueError(f"{self.cursor_path}: invalid cursor")
        return value["after"]

    def advance(self, sequence):
        if type(sequence) is not int or sequence < 0:
            raise ValueError("cursor must be a non-negative integer")
        write_json(self.cursor_path, {"version": 1, "after": sequence}, mode=self.mode)

    def read(self, after=None, *, limit=None):
        after = self.cursor if after is None else after
        events = []
        for sequence in self.sequences():
            if sequence <= after:
                continue
            if limit is not None and len(events) >= limit:
                break
            try:
                events.append(self.event(sequence))
            except FileNotFoundError:
                continue  # pruned between listing and reading
        return events

    def follow(self, after=None, *, timeout=None, poll=POLL_SECONDS):
        """Yield events past `after` (default: the cursor) as they are published.

        Stops after `timeout` seconds without a new event; runs forever if None.
        The cursor is not advanced: callers acknowledge with advance()."""
        position = self.cursor if after is None else after
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            events = self.read(position)
            if events:
                for event in events:
                    position = event["sequence"]
                    yield event
                deadline = None if timeout is None else time.monotonic() + timeout
                continue
            if deadline is not None and time.monotonic() >= deadline:
                return
            time.sleep(poll)

    def prune(self, before=None):
        """Delete events up to and including `before` (default: the cursor).

        The latest event always survives: it carries the sequence high-water
        mark that writers derive their next number from."""
        before = self.cursor if before is None else before
        with self.exclusive():
            removed = 0
            sequences = self.sequences()
            for sequence in sequences[:-1]:
                if sequence > before:
                    break
                event_path(self.directory, sequence).unlink()
                removed += 1
            if removed:
                sync_directory(self.directory)
            return removed


def publish_json(path, value):
    """Create `path` with `value` atomically; return False if it already exists."""
    text = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    if len(text.encode("utf-8")) > 8 * 1024 * 1024:
        raise ValueError(f"{path}: JSON exceeds 8 MiB")
    return publish_text(path, text, Path(path).parent.stat().st_mode & 0o666)


def publish_text(path, text, mode):
    path = Path(path)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}.{os.urandom(6).hex()}")
    try:
        with tmp.open("x", encoding="utf-8") as stream:
            os.chmod(tmp, mode)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError:
            return False
        sync_directory(path.parent)
        return True
    finally:
        tmp.unlink(missing_ok=True)
