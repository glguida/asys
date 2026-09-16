# SPDX-License-Identifier: MIT
"""Durable file operations. Includes code covered by LICENSE.multiagent."""

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
from .permissions import file_mode, open_file

NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def validate_name(label, value):
    if not isinstance(value, str) or not NAME_RE.fullmatch(value):
        raise ValueError(f"invalid {label}: use 1–128 letters, numbers, dots, underscores or hyphens, starting with a letter or number")
    return value


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_text_atomic(path, text, *, mode=0o600):
    path = Path(path)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}.{os.urandom(6).hex()}")
    try:
        with tmp.open("x", encoding="utf-8") as stream:
            os.chmod(tmp, file_mode(path, mode))
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        tmp.replace(path)
        sync_directory(path.parent)
    finally:
        tmp.unlink(missing_ok=True)


def write_json(path, value, *, mode=0o600):
    text = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    if len(text.encode("utf-8")) > 8 * 1024 * 1024:
        raise ValueError(f"{path}: JSON exceeds 8 MiB")
    write_text_atomic(path, text, mode=mode)


def read_json(path):
    with Path(path).open("rb") as stream:
        data = stream.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024:
        raise ValueError(f"{path}: JSON exceeds 8 MiB")
    def invalid(value):
        raise ValueError(f"{path}: invalid JSON constant {value}")
    return json.loads(data, parse_constant=invalid)


def same_json(left, right):
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(same_json(left[key], right[key]) for key in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(same_json(a, b) for a, b in zip(left, right))
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    return left == right


def acquire_lock(path, *, blocking=False):
    fd = open_file(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
    except BlockingIOError:
        os.close(fd)
        return None
    except BaseException:
        os.close(fd)
        raise
    return fd


@contextmanager
def file_lock(path, *, blocking=False):
    fd = acquire_lock(path, blocking=blocking)
    try:
        yield fd
    finally:
        if fd is not None:
            # Do not LOCK_UN: a runner can inherit the same open description.
            # The lease ends only after its last holder closes the descriptor.
            os.close(fd)
