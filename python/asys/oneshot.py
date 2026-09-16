"""Run one named worker assignment, using the shared environment and queue APIs."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import sys
import time
import uuid

from asys_runtime.environment import Environment, describe, environment_root
from asys_runtime.files import timestamp, write_json
from asys_runtime.queue import Queue, TERMINAL
from asys_runtime.permissions import mkdir
from .execution import EnvironmentHost, execution_options, prepare_run
from .lifecycle import Interrupted, LaunchError
from .runs import default_root


def arguments(argv):
    parser = argparse.ArgumentParser(prog='asys-oneshot', allow_abbrev=False,
        description='Run one environment agent in the actual project workspace.')
    parser.add_argument('environment', type=Path, metavar='ENVIRONMENT_DIRECTORY')
    parser.add_argument('agent', metavar='AGENT', help="named worker entry in the environment's workers.json")
    parser.add_argument('prompt', metavar='PROMPT')
    parser.add_argument('--root', type=Path, default=default_root(), metavar='DIRECTORY', help='saved run state directory')
    execution_options(parser)
    args = parser.parse_intermixed_args(argv)
    if not args.prompt.strip():
        parser.error('prompt must be nonempty')
    return args


class OneShot(EnvironmentHost):
    def __init__(self, args):
        super().__init__(args)
        self.record = {}
        self.lease = None
        self.queue = None
        self.job_id = None
        self.sequence = 0

    def say(self, text):
        if self.directory:
            with (self.directory / 'run.log').open('a') as output:
                print(text, file=output)
        print(text, file=sys.stderr, flush=True)

    def snapshot(self):
        if self.directory:
            self.record['updated_at'] = timestamp()
            if self.record.get('status') in {'completed', 'failed', 'cancelled'}:
                self.record.setdefault('finished_at', self.record['updated_at'])
            write_json(self.directory / 'run.json', self.record)

    def event(self, kind, **data):
        self.sequence += 1
        with (self.directory / 'events.jsonl').open('a') as output:
            output.write(json.dumps({'sequence': self.sequence, 'runId': self.id,
                'type': kind, 'time': timestamp(), 'data': data}, ensure_ascii=False) + '\n')
            output.flush()
            os.fsync(output.fileno())

    def setup(self):
        environment = self.args.environment.expanduser().resolve(strict=True)
        definition = Environment(environment)
        if self.args.agent not in definition.types:
            raise LaunchError(f"Environment {definition.name} has no agent entry {self.args.agent!r}; available entries: {', '.join(definition.types)}")
        self.id, self.directory, workspace = prepare_run(self.args.root, self.args.workspace)
        self.lease = (self.directory / 'launcher.lock').open('xb')
        fcntl.flock(self.lease, fcntl.LOCK_EX)
        prefix = re.sub('[^a-z0-9-]', '-', definition.name.lower()).strip('-')[:37] or 'oneshot'
        if not prefix[0].isalpha():
            prefix = 'env-' + prefix[:33]
        self.names = {'workers': f'{prefix}-workers-{self.id[:16]}'}
        self.record = {'id': self.id, 'manager': 'oneshot', 'name': f'{definition.name}.{self.args.agent}',
            'environment': definition.name, 'agent': self.args.agent, 'workspace': str(workspace),
            'created_at': timestamp(), 'status': 'starting', 'system': self.args.system,
            'components': self.names, 'dcomp': self.dcomp}
        self.snapshot()
        self.say(f'Run {self.id}\nState: {self.directory}\nWorkspace: {workspace}')
        self.prepare_environment(environment, self.args.link)
        self.snapshot()
        self.start_workers()
        self.wait_ready()
        descriptor = describe(self.directory / 'runtime', definition.name)
        if descriptor['definition'] != self.definition:
            raise LaunchError("The running environment's workers.json differs from the selected directory")
        self.queue = Queue(environment_root(self.directory / 'runtime', definition.name))

    def execute(self):
        self.job_id = uuid.uuid4().hex
        directory = self.directory / 'jobs' / self.job_id
        mkdir(directory)
        self.record.update(job_id=self.job_id, status='running')
        self.snapshot()
        self.event('job.created', jobId=self.job_id, agent=self.args.agent, workspace=self.record['workspace'])
        self.queue.submit(self.args.agent, self.job_id, directory=directory, workspace=self.directory / 'workspace',
            input={'prompt': self.args.prompt}, metadata={'name': self.args.agent, 'run_id': self.id, 'environment': self.record['environment']})
        self.say(f'Job {self.job_id}: {self.args.agent}')
        health_check = time.monotonic() + 2
        while True:
            self.check_interrupt()
            state = self.queue.state(self.job_id)
            if state['status'] in TERMINAL:
                success = state['status'] == 'done'
                result = state.get('result')
                self.event('job.completed' if success else 'job.failed', jobId=self.job_id, reason=state.get('error', ''))
                self.record.update(status='completed' if success else 'failed', error=state.get('error', ''))
                write_json(self.directory / 'result.json', result)
                self.snapshot()
                if not success:
                    raise LaunchError(state.get('error') or f"Job {state['status']}")
                print(json.dumps(result, indent=2, ensure_ascii=False))
                return
            if time.monotonic() >= health_check:
                self.check_components(self.observe()[1])
                health_check = time.monotonic() + 2
            self.interrupted.wait(0.1)

    def close(self):
        try:
            if self.queue and self.job_id:
                try:
                    state = self.queue.cancel(self.job_id)
                except FileNotFoundError:
                    state = {'status': 'failed'}  # Submission failed before publication.
                except (OSError, ValueError) as error:
                    self.say(f'Could not cancel job: {error}')
                    state = {'status': 'failed'}
                if state['status'] not in TERMINAL:
                    try:
                        self.queue.wait(self.job_id, timeout=5)
                    except (OSError, ValueError) as error:
                        self.say(f'Could not wait for cancellation: {error}')
                        # Component removal terminates its remaining processes.
            clean = self.cleanup_components()
            if self.directory:
                self.record['components_removed'] = clean
                self.snapshot()
            return clean
        finally:
            if self.lease:
                self.lease.close()


def main(argv=None):
    launcher = OneShot(arguments(sys.argv[1:] if argv is None else argv))
    previous = {sig: signal.signal(sig, lambda *_: launcher.interrupted.set()) for sig in (signal.SIGINT, signal.SIGTERM)}
    code = 0
    try:
        launcher.setup()
        launcher.execute()
    except (Interrupted, KeyboardInterrupt):
        launcher.record['status'] = 'cancelled'
        launcher.say('Job cancelled')
        if launcher.directory:
            launcher.event('run.cancelled', jobId=launcher.job_id)
        code = 130
    except (LaunchError, OSError, ValueError) as error:
        launcher.record.update(status='failed', error=str(error))
        launcher.say(f'error: {error}')
        code = 1
    finally:
        try:
            if not launcher.close() and code == 0:
                code = 1
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
    return code
