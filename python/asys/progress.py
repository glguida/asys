"""Agent progress reported through ordinary runtime job stdout."""
import json
from pathlib import Path

from .runs import LogReader


class AgentProgress:
    def __init__(self):
        self.readers = {}
        self.states = {}
        self.notices = {}

    def observe(self, job):
        path = Path(job['directory']) / 'stdout.log'
        reader = self.readers.setdefault(path, LogReader(path, None))
        for line in reader.read():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            kind = event.get("type", "")
            when = event.get("time", "")
            notice = True
            if kind in {"goal.phase_started", "senate.phase_started"}:
                state, detail = "working", "started"
            elif kind in {"goal.phase_finished", "senate.phase_finished"}:
                state, detail = "working", event.get("status", "finished")
            elif kind == "goal.human_requested":
                state, detail = "waiting_for_human", event.get("reason", "human help needed")
            elif kind == "goal.human_answered":
                state, detail = "working", f"human chose {event.get('action', 'retry')}"
            elif kind == "goal.finished":
                state, detail = event.get("status", "finished"), f"goal {event.get('status', 'finished')}"
            elif kind == "senate.finished":
                state = event.get("status", "finished")
                detail = f"senate {state}: {event.get('decision') or 'no decision'}"
            elif kind == "agent.provider_exhausted":
                state, detail = "exhausted", f"provider exhausted; retry at {event.get('retryAt', 'unknown')}"
            elif kind == "agent.provider_retrying":
                state, detail = "retrying", "retrying provider request"
            elif kind == "agent.compaction_started":
                state, detail = "compacting", "compacting context" + (f" ({event['reason']})" if event.get("reason") else "")
            elif kind == "agent.compaction_ended":
                state = "compaction_failed" if event.get("errorMessage") or event.get("aborted") else "working"
                detail = event.get("errorMessage") or ("compaction aborted" if event.get("aborted") else "context compacted")
            elif kind == "agent.model_unavailable":
                state, detail = "model_unavailable", event.get("message", "model unavailable")
            elif kind in {"agent.tool_started", "agent.tool_completed"}:
                state, detail, notice = "working", "agent working", False
            else:
                continue
            if event.get('phase'):
                if 'participant' in event:
                    detail = f"round {event.get('round', '?')} {event['participant']} {event['phase']}: {detail}"
                else:
                    detail = f"attempt {event.get('attempt', '?')} {event['phase']}: {detail}"
            current = {"status": state, "detail": detail, "time": when}
            self.states[path] = current
            if notice:
                messages = self.notices.setdefault(path, [])
                messages.append({**current, "key": (str(path), len(messages))})
        return self.states.get(path)

    def events(self, jobs):
        for job in jobs:
            for path, notices in self.notices.items():
                if path.parent == Path(job["directory"]):
                    for notice in notices:
                        yield {**notice, "job": job}
