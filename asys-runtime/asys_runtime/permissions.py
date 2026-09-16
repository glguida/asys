"""Inherit group access within explicitly shared (setgid) state directories."""
import os
from pathlib import Path


def shared(path):
    return Path(path).stat().st_mode & 0o2070 == 0o2070


def file_mode(path, mode=0o600):
    return mode | ((mode & 0o700) >> 3) if shared(Path(path).parent) else mode


def mkdir(path, *, parents=False, exist_ok=False, mode=0o700):
    path = Path(path)
    if parents and not path.parent.is_dir():
        mkdir(path.parent, parents=True, exist_ok=True, mode=mode)
    mode = file_mode(path, mode) | (0o2000 if shared(path.parent) else 0)
    try:
        path.mkdir(mode=mode)
    except FileExistsError:
        if not exist_ok or not path.is_dir():
            raise
    else:
        path.chmod(mode)


def open_file(path, flags, mode=0o600):
    if not flags & os.O_CREAT:
        return os.open(path, flags)
    mode = file_mode(path, mode)
    try:
        fd = os.open(path, flags | os.O_CREAT | os.O_EXCL, mode)
    except FileExistsError:
        if flags & os.O_EXCL:
            raise
        return os.open(path, flags & ~os.O_CREAT, mode)
    try:
        os.fchmod(fd, mode)
        return fd
    except BaseException:
        os.close(fd)
        raise
