"""Named execution environments; definitions are separate from runtime state."""

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path

from .config import parse_config
from .files import read_json, validate_name, write_json
from .permissions import mkdir, open_file


def environment_root(root, name):
    validate_name("environment name", name)
    return Path(root).expanduser().resolve() / "environments" / name


class Environment:
    def __init__(self, directory, *, external=None):
        self.directory = Path(directory).expanduser().resolve()
        if external is None and (self.directory / "external/workers.json").exists():
            external = self.directory / "external"
        self.external = Path(external).expanduser().resolve(strict=True) if external is not None else None
        self.workers_directory = self.external if self.external is not None else self.directory
        self.config = config = read_json(self.workers_directory / "workers.json")
        self.types = parse_config(config, directory=self.workers_directory, environment=True)
        self.name = config.get("name")
        validate_name("environment name", self.name)
        description = config.get("description", "")
        if not isinstance(description, str):
            raise ValueError("environment description must be text")
        definition = hashlib.sha256(json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        self.descriptor = {"version": 1, "name": self.name, "description": description,
                           "types": sorted(self.types), "definition": definition}
        for spec in self.types.values():
            spec["env"].update(ASYS_ENVIRONMENT_DIR=str(self.directory), ASYS_ENVIRONMENT=self.name,
                               ASYS_WORKERS_DIR=str(self.workers_directory))

    @contextmanager
    def register(self, root):
        """Identical replicas may share a name; live conflicting definitions cannot."""
        directory = environment_root(root, self.name)
        mkdir(directory, parents=True, exist_ok=True)
        lease = open_file(directory / "environment.lease", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW)
        try:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                fcntl.flock(lease, fcntl.LOCK_SH)
                existing = read_json(directory / "environment.json")
                if existing != self.descriptor:
                    raise ValueError(f"environment {self.name} is already running with a different workers.json")
            else:
                write_json(directory / "environment.json", self.descriptor)
                fcntl.flock(lease, fcntl.LOCK_SH)
            yield directory
        finally:
            os.close(lease)


def describe(root, name):
    value = read_json(environment_root(root, name) / "environment.json")
    if (not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 1
            or value.get("name") != name or not isinstance(value.get("description"), str)
            or not isinstance(value.get("types"), list) or not value["types"]
            or not isinstance(value.get("definition"), str) or len(value["definition"]) != 64
            or any(character not in "0123456789abcdef" for character in value["definition"])):
        raise ValueError(f"invalid environment descriptor for {name}")
    for job_type in value["types"]:
        validate_name("job type", job_type)
    if len(set(value["types"])) != len(value["types"]):
        raise ValueError(f"duplicate types in environment descriptor for {name}")
    return value


def list_environments(root):
    directory = Path(root).expanduser().resolve() / "environments"
    if not directory.exists():
        return []
    return [describe(root, path.name) for path in sorted(directory.iterdir())
            if not path.name.startswith(".") and (path / "environment.json").is_file()]
