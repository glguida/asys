"""Submit one goal job; the worker owns implementation, verification and help."""
import json
import sys

from asys_runtime.files import write_json
from asys_runtime.permissions import mkdir
from .runs import LogReader
from .single_job import SingleJob, job_parser, run, validate_assignment


def arguments(argv):
    parser = job_parser('asys-goal', 'goal',
        'Implement and independently verify a goal with the simple system agent.')
    parser.add_argument('--max-attempts', type=int, metavar='N',
                        help='optional cap on implementation turns, including continuations (default: unlimited)')
    args = validate_assignment(parser, parser.parse_intermixed_args(argv), 'goal')
    if args.max_attempts is not None and args.max_attempts < 1:
        parser.error('max-attempts must be a positive integer')
    return args


class Goal(SingleJob):
    manager = 'goal'
    label = 'goal'
    job_type = 'goal'

    def __init__(self, args):
        super().__init__(args)
        self.progress = None

    def run_fields(self):
        return {'goal': self.args.goal, 'max_attempts': self.args.max_attempts}

    def prepare_workers(self, definition, model):
        external = self.directory / 'external'
        mkdir(external)
        write_json(external / 'workers.json', {'version': 1, 'name': definition.name,
            'description': 'Asys goal worker', 'egress': definition.config.get('egress', False),
            'types': {'goal': {'command': ['/opt/asys/asys-workers/tools/asys-goal']}}})
        return external

    def job_input(self):
        return {'goal': self.args.goal, **({'maxAttempts': self.args.max_attempts} if self.args.max_attempts is not None else {})}

    def poll(self):
        if self.progress is None:
            self.progress = LogReader(self.directory / 'jobs' / self.job_id / 'stdout.log', None)
        for line in self.progress.read():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            kind = event.get('type')
            if kind == 'goal.phase_started':
                self.say(f"Attempt {event['attempt']}: {event['phase']}")
            elif kind == 'goal.phase_finished':
                self.say(f"Attempt {event['attempt']}: {event['phase']} {event['status']}")
            elif kind == 'goal.human_requested':
                self.say(f"Human help needed during {event['phase']}: {event['reason']}\n"
                         f"Answer with asys-human-prompt --system {self.args.system} in the same host state.")
            elif kind == 'goal.human_answered':
                self.say(f"Human decision: {event['action']}")
            elif kind == 'goal.finished':
                self.say(f"Goal {event['status']} after {event['attempt']} attempt(s)")


def main(argv=None):
    return run(Goal(arguments(sys.argv[1:] if argv is None else argv)))
