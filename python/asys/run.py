"""Execute one named worker through the ordinary runtime job contract."""
import argparse
from copy import deepcopy
import fcntl
import json
from pathlib import Path
import re
import shutil
import sys
import time
import uuid

from asys_runtime.channel import Reader, direction_root
from asys_runtime.environment import Environment, describe, environment_root
from asys_runtime.files import timestamp, validate_name, write_json
from asys_runtime.permissions import mkdir
from asys_runtime.queue import Queue, TERMINAL

from .config import system_model, system_models
from .execution import execution_options, prepare_run
from .lifecycle import LaunchError
from .runs import LogReader
from .single_job import SingleJob, run as run_job
from .state import state_root
from .worker_definitions import builtin_definition, definition_command, load_definition


def arguments(argv):
    parser = argparse.ArgumentParser(prog='asys-run', allow_abbrev=False,
        description='Run a named worker with a request in the selected environment.')
    parser.add_argument('environment', type=Path, metavar='ENVIRONMENT')
    parser.add_argument('worker', metavar='WORKER')
    parser.add_argument('request', metavar='REQUEST')
    parser.add_argument('--model', metavar='MODEL', help='override the worker model for this run')
    parser.add_argument('--parameters', type=Path, metavar='FILE', help='JSON parameters accepted by this worker')
    parser.add_argument('--root', type=Path, default=state_root(), metavar='DIRECTORY', help='asys system root')
    parser.add_argument('--view', action='store_true', help='open a local world view and retain it after completion')
    parser.add_argument('--port', type=int, default=0, help='world view port (default: select an available port)')
    execution_options(parser)
    args = parser.parse_intermixed_args(argv)
    try:
        validate_name('worker name', args.worker)
    except ValueError as error:
        parser.error(str(error))
    if not args.request.strip():
        parser.error('REQUEST must be nonempty')
    if args.model is not None and not args.model.strip():
        parser.error('--model must be nonempty')
    if not 0 <= args.port <= 65535:
        parser.error('--port must be between 0 and 65535')
    return args


def selected_definition(environment, name, existing_types=()):
    """Named definitions take precedence; built-ins need no source mutations."""
    path = Path(environment) / 'workers' / f'{name}.json'
    if path.exists() or path.is_symlink():
        return load_definition(environment, name), False
    if name in existing_types:
        return None, False
    try:
        definition = builtin_definition(name)
    except (KeyError, ValueError):
        definition = None
    return definition, definition is not None


def execution_models(root, definition, kind, override=None, command=()):
    if override is not None:
        return {'simple': override}
    config = definition['config'] if definition else {}
    if kind == 'program':
        return {}
    if kind in {'agent', 'goal'}:
        explicit = config.get('model')
        if not definition and '--model' in command:
            index = len(command) - 1 - list(reversed(command)).index('--model')
            if index + 1 < len(command):
                explicit = command[index + 1]
        return {'simple': system_model('simple', explicit, root=root)}
    if kind == 'senate' and all(p.get('model') for p in [config['princeps'], *config['senators']]):
        return {}
    return system_models(root)


def view_source(environment, value):
    """Resolve environment assets or files supplied by the installed worlds."""
    if not isinstance(value, str) or not value:
        raise ValueError('World view must name a renderer file')
    prefix = '/opt/asys/asys-workers/'
    if value.startswith(prefix):
        relative = Path(value[len(prefix):])
        if relative.parts[:1] != ('worlds',) or '..' in relative.parts:
            raise ValueError('Built-in view must remain inside the worlds package')
        base = Path(__file__).resolve().parents[2]
        roots = (base / 'asys-workers', base / 'workers')
        for root in roots:
            if (root / relative).is_file():
                return (root / relative).resolve(), root.resolve()
        raise ValueError('Built-in world renderer is unavailable; reinstall the asys world resources')
    relative = Path(value)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Custom world view must be relative to the environment')
    root = Path(environment).resolve()
    source = (root / relative).resolve(strict=True)
    if not source.is_relative_to(root) or not source.is_file():
        raise ValueError('World view must be a file inside the environment')
    return source, root


def retain_view(environment, value, run_directory):
    source, root = view_source(environment, value)
    destination = Path(run_directory) / 'view'
    mkdir(destination)
    # A root-level entry copies only itself. A dedicated view directory carries
    # its complete static resources, bounded independently of the source image.
    sources = [source] if source.parent == root else sorted(source.parent.rglob('*'))
    total = 0
    for path in sources:
        if path.is_symlink():
            raise ValueError('World view assets must not contain symlinks')
        if not path.is_file():
            continue
        if path.suffix in {'.py', '.pyc'} or any(part in {'.git', '__pycache__', 'node_modules'} for part in path.parts):
            continue
        total += path.stat().st_size
        if total > 32 * 1024 * 1024:
            raise ValueError('World view assets exceed 32 MiB')
        target = destination / path.relative_to(source.parent)
        mkdir(target.parent, parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    return {'view_directory': 'view', 'view': source.name,
            'view_format': 'module' if source.suffix in {'.js', '.mjs'} else 'html'}


class Run(SingleJob):
    manager = 'run'

    def __init__(self, args):
        super().__init__(args)
        self.label = self.job_type = args.worker
        self.definition = None
        self.worker_definition = None
        self.original = None
        self.overlay = None
        self.overlay_target = None
        self.builtin_file = None
        self.viewer = None
        self.progress = None
        self.parameters = None
        self.control_after = 0
        self.outbound = None

    def setup(self):
        environment = self.args.environment.expanduser().resolve(strict=True)
        self.original = Environment(environment, external=self.args.external)
        self.worker_definition, builtin = selected_definition(environment, self.args.worker, self.original.types)
        if self.worker_definition is None and self.args.worker not in self.original.types:
            raise ValueError(f'Unknown worker {self.args.worker!r}; use asys-workers ENVIRONMENT list')
        executable = Path(self.original.types.get(self.args.worker, {}).get('command', [''])[0]).name
        legacy_kind = {'asys-agent': 'agent', 'asys-goal': 'goal', 'asys-senate': 'senate',
                       'asys-swarm': 'swarm'}.get(executable, 'program')
        kind = self.worker_definition['kind'] if self.worker_definition else legacy_kind
        if self.worker_definition is None and kind in {'senate', 'swarm'}:
            raise ValueError(f'{kind} requires a named worker definition; use asys-workers ENVIRONMENT add {kind} NAME')
        if self.args.model and kind == 'program':
            raise ValueError('--model requires an agent, goal, senate, or swarm definition')
        if self.args.view and kind != 'swarm':
            raise ValueError('--view currently requires a worker that publishes a swarm world')
        if self.args.parameters:
            with self.args.parameters.expanduser().open(encoding='utf-8') as source:
                self.parameters = json.load(source)
            if not isinstance(self.parameters, dict):
                raise ValueError('Worker parameters must be a JSON object')
        # Read settings before provisioning so malformed configuration cannot
        # leave an idle component behind. Deterministic programs need no model.
        self.models = execution_models(self.args.root, self.worker_definition, kind, self.args.model,
                                       self.original.types.get(self.args.worker, {}).get('command', ()))
        self.id, self.directory, workspace = prepare_run(state_root('runs', root=self.args.root), self.args.workspace)
        self.job_id = uuid.uuid4().hex
        self.lease = (self.directory / 'launcher.lock').open('xb')
        fcntl.flock(self.lease, fcntl.LOCK_EX)
        prefix = re.sub('[^a-z0-9-]', '-', self.original.name.lower()).strip('-')[:37] or 'run'
        if not prefix[0].isalpha():
            prefix = 'env-' + prefix[:33]
        self.names = {'workers': f'{prefix}-workers-{self.id[:16]}'}
        self.record = {'id': self.id, 'manager': self.manager,
            'name': f'{self.original.name}.{self.args.worker}', 'environment': self.original.name,
            'worker_name': self.args.worker, 'worker_kind': kind, 'request': self.args.request,
            'workspace': str(workspace), 'created_at': timestamp(), 'status': 'starting',
            'system': self.args.system, 'components': self.names, 'dcomp': self.dcomp,
            'job_id': self.job_id, 'job_directory': f'jobs/{self.job_id}', 'model': self.args.model}
        self.snapshot()
        self.say(f'Run {self.id}\nState: {self.directory}\nWorkspace: {workspace}')
        effective = deepcopy(self.original.config)
        if self.worker_definition is not None:
            write_json(self.directory / 'worker-definition.json', self.worker_definition)
            command = definition_command(self.args.worker)
            self.builtin_file = self.directory / 'worker-definition.json'
            command = [command[0], '--definition', f'/opt/asys/environment/.asys-run/{self.args.worker}.json']
            previous = effective['types'].get(self.args.worker, {})
            effective['types'][self.args.worker] = {**previous, 'command': command}
        overlay_directory = self.directory / 'worker-bindings'
        mkdir(overlay_directory)
        self.overlay = overlay_directory / 'workers.json'
        write_json(self.overlay, effective)
        self.overlay_target = ('/opt/asys/environment/external/workers.json' if self.original.external
                               else '/opt/asys/environment/workers.json')
        self.prepare_environment(environment, self.args.link, external=overlay_directory)
        # Only replace the dispatch file. Existing relative commands and named
        # agent assets must retain their original environment/bundle location.
        self.record['external_directory'] = str(self.original.external) if self.original.external else None
        if kind == 'swarm':
            self.record.update(swarm_state=f'jobs/{self.job_id}/swarm',
                               control_channel=f'swarm-{self.job_id}', world_channel=f'world-{self.job_id}',
                               channel=f'runtime/channels/swarm-{self.job_id}')
            world = self.worker_definition['config'].get('world', {}) if self.worker_definition else {}
            if world.get('view'):
                self.record.update(retain_view(environment, world['view'], self.directory))
            self.outbound = Reader(direction_root(self.directory / 'runtime', self.record['control_channel'], 'out'))
        self.snapshot()
        self.start_workers()
        self.wait_ready()
        descriptor = describe(self.directory / 'runtime', self.original.name)
        if descriptor['definition'] != self.definition:
            raise LaunchError('The running environment does not match the selected worker bindings')
        self.queue = Queue(environment_root(self.directory / 'runtime', self.original.name))
        if self.args.view:
            from .swarm_view import Viewer
            self.viewer = Viewer(self.directory, port=self.args.port).start()
            self.say(f'Viewer: {self.viewer.url}')

    def worker_models(self):
        return self.models

    def add(self, role, directory, options):
        if role == 'workers':
            options = [*options, '--bind', f'{self.overlay},{self.overlay_target},ro']
            if self.builtin_file is not None:
                options += ['--bind', f'{self.builtin_file},/opt/asys/environment/.asys-run/{self.args.worker}.json,ro']
        super().add(role, directory, options)

    def job_input(self):
        result = {'request': self.args.request}
        if self.parameters is not None:
            result['parameters'] = self.parameters
        return result

    def poll(self):
        if self.outbound is not None:
            while True:
                batch = self.outbound.read(self.control_after, limit=500)
                if not batch:
                    break
                for entry in batch:
                    self.control_after = entry['sequence']
                    data = entry['data']
                    self.event(entry['type'], **data)
                    if entry['type'] == 'swarm.snapshot':
                        self.record['turn'] = data.get('turn')
                    elif entry['type'] in {'swarm.paused', 'swarm.resumed'}:
                        self.record['status'] = 'paused' if entry['type'] == 'swarm.paused' else 'running'
                        self.snapshot()
                self.outbound.advance(self.control_after)
        if self.progress is None:
            self.progress = LogReader(self.directory / 'jobs' / self.job_id / 'stdout.log', None)
        for line in self.progress.read():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get('type') in {'goal.phase_started', 'senate.phase_started'}:
                who = event.get('participant', 'worker')
                self.say(f"{who}: {event.get('phase', 'working')}")

    def execute(self):
        directory = self.directory / 'jobs' / self.job_id
        mkdir(directory, exist_ok=True)
        self.record.update(status='running')
        self.snapshot()
        self.event('job.created', jobId=self.job_id, worker=self.args.worker, workspace=self.record['workspace'])
        self.queue.submit(self.job_type, self.job_id, directory=directory,
            workspace=self.directory / 'workspace', input=self.job_input(),
            args=['--model', self.args.model] if self.args.model is not None else [],
            metadata={'name': self.args.worker, 'run_id': self.id,
                      'worker_kind': self.record['worker_kind'], 'environment': self.record['environment']})
        self.say(f'Job {self.job_id}: {self.args.worker}')
        next_health = time.monotonic() + 2
        while True:
            self.check_interrupt()
            state = self.queue.state(self.job_id)
            self.poll()
            if state['status'] in TERMINAL:
                # Runtime completion is authoritative. Drain the committed
                # world events once more before preserving the final result.
                self.poll()
                success = state['status'] == 'done'
                result = state.get('result')
                status = 'completed' if success else 'failed'
                if isinstance(result, dict) and result.get('status') == 'cancelled':
                    status = 'cancelled'
                self.event('job.completed' if success else 'job.failed', jobId=self.job_id,
                           reason=state.get('error', ''))
                self.record.update(status=status, error=state.get('error', ''))
                write_json(self.directory / 'result.json', result)
                self.snapshot()
                if not success:
                    raise LaunchError(state.get('error') or f"Job {state['status']}")
                print(json.dumps(result, indent=2, ensure_ascii=False), flush=True)
                return
            if time.monotonic() >= next_health:
                self.check_components(self.observe()[1])
                next_health = time.monotonic() + 2
            self.interrupted.wait(0.1)


def main(argv=None):
    launcher = Run(arguments(sys.argv[1:] if argv is None else argv))
    try:
        code = run_job(launcher)
        if code == 0 and launcher.record.get('status') == 'cancelled':
            code = 130
        if code == 0 and launcher.viewer is not None:
            launcher.say('Run complete. The saved view remains available; press Ctrl-C to close it.')
            try:
                launcher.viewer.thread.join()
            except KeyboardInterrupt:
                pass
        return code
    finally:
        if launcher.viewer is not None:
            launcher.viewer.close()
