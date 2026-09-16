import json
import os
from pathlib import Path
import shutil
import tempfile
import time

from .files import file_lock, read_json, same_json, sync_directory, timestamp, validate_name, write_json
from .permissions import file_mode, mkdir

TERMINAL = frozenset({"done", "failed", "cancelled", "interrupted"})
STATUSES = TERMINAL | {"pending", "running"}


class Queue:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.jobs = self.root / "jobs"
        mkdir(self.jobs, parents=True, exist_ok=True)

    def directory(self, job_id):
        path = self.jobs / validate_name("job ID", job_id)
        if path.is_symlink():
            raise ValueError(f"job {job_id} must be a directory, not a symlink")
        return path

    def paths(self, job_id):
        request = self.request(job_id)
        return {key: (self.root / request[key]).resolve() for key in ("directory", "workspace")}

    def submit(self, job_type, job_id, *, directory, workspace, args=(), input=None, metadata=None):
        validate_name("job type", job_type)
        paths = {}
        for key, value in (("directory", directory), ("workspace", workspace)):
            path = Path(os.path.abspath(Path(value).expanduser()))
            if not path.is_dir():
                raise ValueError(f"job {key} must be a prepared directory: {path}")
            paths[key] = os.path.relpath(path, self.root)
        if Path(directory).resolve() == Path(workspace).resolve():
            raise ValueError("job directory and workspace must be separate")
        directory = self.directory(job_id)
        if not isinstance(args, (list, tuple)) or any(not isinstance(v, str) or "\0" in v for v in args):
            raise ValueError("job args must be a list of strings without NUL characters")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("job metadata must be an object")
        request = {"version": 1, "id": job_id, "type": job_type, **paths, "args": list(args), "input": input, "metadata": metadata or {}}
        # Validate before creating any persistent queue entry.
        json.dumps(request, allow_nan=False)
        staging = Path(tempfile.mkdtemp(prefix=".submit-", dir=self.jobs))
        staging.chmod(file_mode(staging, 0o700) | (self.jobs.stat().st_mode & 0o2000))
        try:
            write_json(staging / "request.json", request)
            submitted = timestamp()
            write_json(staging / "state.json", {"version": 1, "id": job_id, "type": job_type,
                "status": "pending", "submitted_at": submitted, "updated_at": submitted})
            sync_directory(staging)
            try:
                staging.rename(directory)
                sync_directory(self.jobs)
            except OSError:
                if not directory.exists():
                    raise
                if not same_json(self.request(job_id), request):
                    raise ValueError(f"job {job_id} already exists with different input") from None
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return self.state(job_id)

    def request(self, job_id):
        value = read_json(self.directory(job_id) / "request.json")
        if not isinstance(value, dict) or value.get("version") != 1 or value.get("id") != job_id:
            raise ValueError(f"job {job_id}: invalid request")
        validate_name("job type", value.get("type"))
        args = value.get("args", [])
        for key in ("directory", "workspace"):
            path = value.get(key)
            if not isinstance(path, str) or not path or "\0" in path or Path(path).is_absolute():
                raise ValueError(f"job {job_id}: {key} must be a relative directory reference")
        if not isinstance(args, list) or any(not isinstance(v, str) or "\0" in v for v in args):
            raise ValueError(f"job {job_id}: invalid args")
        return value

    def state(self, job_id):
        value = read_json(self.directory(job_id) / "state.json")
        if not isinstance(value, dict) or value.get("version") != 1 or value.get("id") != job_id or value.get("status") not in STATUSES:
            raise ValueError(f"job {job_id}: invalid state")
        return value

    def list(self):
        rows = [self.state(path.name) for path in self.jobs.iterdir() if not path.name.startswith(".") and path.is_dir()]
        return sorted(rows, key=lambda row: (row["submitted_at"], row["id"]))

    def save(self, state):
        state["updated_at"] = timestamp()
        directory = self.directory(state["id"])
        write_json(directory / "state.json", state)

    def cancel_requested(self, job_id):
        return (self.directory(job_id) / "cancel.json").is_file()

    def cancel(self, job_id):
        state = self.state(job_id)
        if state["status"] in TERMINAL:
            return state
        write_json(self.directory(job_id) / "cancel.json", {"requested_at": timestamp()})
        with file_lock(self.directory(job_id) / "lease") as fd:
            if fd is not None:
                current = self.state(job_id)
                if current["status"] not in TERMINAL:
                    current.update(status="cancelled", finished_at=timestamp())
                    self.save(current)
        return self.state(job_id)

    def wait(self, job_id, *, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            state = self.state(job_id)
            if state["status"] in TERMINAL:
                return state
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting for job {job_id}")
            time.sleep(0.05)
