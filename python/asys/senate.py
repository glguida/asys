"""Submit one senate deliberation; the worker coordinates every participant."""
import json
from pathlib import Path
import sys

from asys_runtime.files import validate_name, write_json
from asys_runtime.permissions import mkdir
from .runs import LogReader
from .single_job import SingleJob, job_parser, run, validate_assignment


def validate_senate(config):
    """Validate the portable v1 roster before creating runtime state."""
    if not isinstance(config, dict):
        raise ValueError('senate must be a JSON object')
    if set(config) - {'version', 'princeps', 'senators'}:
        raise ValueError('senate only supports version, princeps and senators')
    if type(config.get('version')) is not int or config['version'] != 1:
        raise ValueError('senate version must be 1')
    if not isinstance(config.get('senators'), list) or not config['senators']:
        raise ValueError('senate senators must be a nonempty array')
    names = set()
    for label, participant in [('princeps', config.get('princeps')),
                               *((f'senators[{index}]', senator) for index, senator in enumerate(config['senators']))]:
        if not isinstance(participant, dict):
            raise ValueError(f'senate {label} must be a JSON object')
        if set(participant) - {'name', 'prompt', 'agent', 'model'}:
            raise ValueError(f'senate {label} only supports name, prompt, agent and model')
        name = participant.get('name')
        if not isinstance(name, str) or not name.strip() or '\0' in name:
            raise ValueError(f'senate {label}.name must be nonempty text')
        if name.strip() in names:
            raise ValueError(f'duplicate senate participant name: {name!r}')
        names.add(name.strip())
        if 'prompt' in participant and (not isinstance(participant['prompt'], str) or '\0' in participant['prompt']):
            raise ValueError(f'senate {label}.prompt must be text without NUL characters')
        if 'agent' in participant:
            validate_name(f'senate {label}.agent', participant['agent'])
        if 'model' in participant:
            model = participant['model']
            if not isinstance(model, str) or not model.strip() or '\0' in model:
                raise ValueError(f'senate {label}.model must be nonempty text')
    return config


def arguments(argv):
    parser = job_parser('asys-senate', 'topic',
        'Deliberate on a topic with a princeps and senators, for at most three rounds.')
    parser.add_argument('--senate', type=Path, required=True, metavar='FILE',
                        help='JSON roster defining the princeps and senators')
    return validate_assignment(parser, parser.parse_intermixed_args(argv), 'topic')


class Senate(SingleJob):
    manager = 'senate'
    label = 'senate'
    job_type = 'senate'

    def __init__(self, args):
        super().__init__(args)
        self.senate = None
        self.progress = None

    def resolve_model(self):
        with self.args.senate.expanduser().open(encoding='utf-8') as source:
            self.senate = validate_senate(json.load(source))
        participants = [self.senate['princeps'], *self.senate['senators']]
        if self.args.model is not None or any('model' not in participant for participant in participants):
            return super().resolve_model()
        return None

    def run_fields(self):
        write_json(self.directory / 'senate.json', self.senate)
        return {'topic': self.args.topic, 'senate': self.senate}

    def prepare_workers(self, definition, model):
        external = self.directory / 'external'
        mkdir(external)
        write_json(external / 'workers.json', {'version': 1, 'name': definition.name,
            'description': 'Asys senate worker', 'egress': definition.config.get('egress', False),
            'types': {'senate': {'command': ['/opt/asys/asys-workers/tools/asys-senate']}}})
        return external

    def job_input(self):
        return {'topic': self.args.topic, 'senate': self.senate}

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
            if kind in {'senate.phase_started', 'senate.phase_finished'}:
                message = f"Round {event['round']}: {event['participant']} {event['phase']}"
                if kind == 'senate.phase_finished':
                    message += f" {event['status']}"
                self.say(message)
            elif kind == 'senate.finished':
                self.say(f"Senate {event['status']} after {event['rounds']} round(s): {event['decision']}")


def main(argv=None):
    return run(Senate(arguments(sys.argv[1:] if argv is None else argv)))
