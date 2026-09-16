from pathlib import Path
from asys_runtime import Queue


class PreparedQueue(Queue):
    """The test caller prepares execution storage before submitting work."""
    def submit(self, job_type, job_id, **options):
        storage = self.root / "executions" / job_id
        job, workspace = storage / "job", storage / "workspace"
        job.mkdir(parents=True, exist_ok=True)
        workspace.mkdir(parents=True, exist_ok=True)
        return super().submit(job_type, job_id, **{"directory": job, "workspace": workspace, **options})
