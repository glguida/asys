"""BPMN names and progress used by the workflow launcher and event reader."""
from pathlib import Path
import re
import unicodedata
import xml.etree.ElementTree as ET

from asys.runs import Runs as JobRuns


def workflow_name(xml, path, override=None):
    if override:
        return override
    try:
        document = ET.fromstring(xml)
    except ET.ParseError as error:
        raise ValueError(f"Invalid BPMN XML: {error}") from error
    return document.get("name") or document.get("id") or Path(path).stem


def component_names(name, run_id):
    value = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1-\2", value)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", value)
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "workflow"
    if not value[0].isalpha():
        value = "workflow-" + value
    # Dcomp instance names allow at most 63 characters. Both roles retain the
    # same full 16-character run suffix even when a display name is long.
    value = value[:37].rstrip("-")
    return {"engine": f"{value}-workflow-{run_id[:16]}", "workers": f"{value}-workers-{run_id[:16]}"}


class Runs(JobRuns):
    """Add BPMN labels to jobs for the launcher's progress formatter."""
    def jobs(self, directory):
        jobs = super().jobs(directory)
        for job in jobs:
            metadata = job["metadata"]
            job.update(activity=metadata.get("activity_id") or job["name"],
                       name=metadata.get("bpmn", {}).get("name") or job["name"],
                       execution_id=metadata.get("execution_id"))
        return jobs
