"""Run one assignment with the shared simple system agent."""
import sys

from .single_job import AGENT, SingleJob, job_parser, run, validate_assignment
from .system_agents import JOB_TYPE, prepare_agent


def arguments(argv):
    parser = job_parser('asys-oneshot', 'prompt',
        'Run one assignment with the simple system agent in the actual project workspace.')
    return validate_assignment(parser, parser.parse_intermixed_args(argv), 'prompt')


class OneShot(SingleJob):
    manager = 'oneshot'
    label = AGENT
    job_type = JOB_TYPE

    def prepare_workers(self, definition, model):
        return prepare_agent(AGENT, self.directory, definition, model)

    def job_input(self):
        return {'prompt': self.args.prompt}


def main(argv=None):
    return run(OneShot(arguments(sys.argv[1:] if argv is None else argv)))
