"""Launch one swarm worker job and an explicit runtime-channel world service."""
import argparse
from copy import deepcopy
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import time

from asys_runtime.channel import Reader, Writer, direction_root
from asys_runtime.environment import Environment, environment_root
from asys_runtime.files import read_json, timestamp, write_json
from asys_runtime.permissions import mkdir, shared
from asys_runtime.queue import Queue, TERMINAL as JOB_TERMINAL
from asys_swarm.config import load_config, package_file
from .execution import EnvironmentHost, execution_options, prepare_run
from .lifecycle import Interrupted, LaunchError
from .runs import Runs
from .state import state_root
from .swarm_view import CHANNEL, TERMINAL, Viewer, control


EXCLUDED = {'.git', '.agents', '.codex', '.asys', '.asys-runtime', '.venv', 'node_modules', '__pycache__', '.pytest_cache'}
PACKAGE_BYTES = 32 * 1024 * 1024
PACKAGE_FILES = 2000
SWARM_COMMAND = ['/opt/asys/asys-workers/tools/asys-swarm']


def snapshot_package(source, destination, *, exclude=()):
    """Copy a bounded author-controlled package without following symlinks."""
    source, destination = Path(source).resolve(), Path(destination).resolve()
    excluded = {Path(path).resolve() for path in exclude} | {destination}
    files, size = [], 0
    for current, directories, filenames in os.walk(source, followlinks=False):
        current = Path(current)
        directories[:] = sorted(name for name in directories
                                if name not in EXCLUDED and (current / name).resolve() not in excluded)
        for name in directories + sorted(filenames):
            path = current / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise ValueError(f'Swarm packages cannot contain symlinks: {path}')
            if stat.S_ISDIR(mode):
                continue
            if not stat.S_ISREG(mode):
                raise ValueError(f'Swarm packages contain only regular files: {path}')
            size += path.stat().st_size
            files.append(path)
            if len(files) > PACKAGE_FILES or size > PACKAGE_BYTES:
                raise ValueError('Swarm package exceeds 2000 files or 32 MiB; put the configuration in a dedicated package directory')
    mkdir(destination, parents=True, exist_ok=True)
    for path in files:
        target = destination / path.relative_to(source)
        mkdir(target.parent, parents=True, exist_ok=True)
        shutil.copyfile(path, target, follow_symlinks=False)
        mode = 0o700 if path.stat().st_mode & 0o111 else 0o600
        target.chmod(mode | ((mode & 0o700) >> 3) if destination.stat().st_mode & 0o2000 else mode)


def arguments(argv):
    common = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    common.add_argument('--root', type=Path, default=state_root(), metavar='DIRECTORY', help='asys system root; runs are saved under ROOT/runs')
    global_args, remaining = common.parse_known_args(argv)
    parser = argparse.ArgumentParser(prog='asys-swarm', parents=[common], allow_abbrev=False,
                                     description='Run swarms defined by ordinary configuration and world files.')
    commands = parser.add_subparsers(dest='command', required=True)
    run = commands.add_parser('run', help='run CONFIG in ENVIRONMENT_DIRECTORY', allow_abbrev=False)
    run.add_argument('config', type=Path, metavar='CONFIG.json')
    run.add_argument('environment', type=Path, metavar='ENVIRONMENT_DIRECTORY')
    execution_options(run)
    world = run.add_mutually_exclusive_group(required=True)
    world.add_argument('--world', type=Path, metavar='COMPONENT_DIRECTORY', help='build/start this world component')
    world.add_argument('--world-command', metavar='JSON_ARGV', help='start an explicit host world executable argument list')
    world.add_argument('--world-external', action='store_true', help='use a world service started separately')
    run.add_argument('--view', action='store_true', help='serve the package viewer on loopback; keep it open after completion')
    run.add_argument('--port', type=int, default=0, help='viewer port (default: choose a free port)')
    for name in ('view', 'pause', 'resume', 'cancel'):
        command = commands.add_parser(name, help=f'{name} a saved swarm run', allow_abbrev=False)
        command.add_argument('run', metavar='RUN', help='saved run ID, unique prefix, or state directory')
        if name == 'view':
            command.add_argument('--port', type=int, default=0, help='loopback port (default: choose a free port)')
    parser.set_defaults(root=global_args.root)
    args = parser.parse_args(remaining)
    if hasattr(args, 'port') and not 0 <= args.port <= 65535:
        parser.error('port must be between 0 and 65535')
    if getattr(args, 'world_command', None) is not None:
        try:
            command = json.loads(args.world_command)
        except ValueError:
            parser.error('--world-command must be a JSON argument list')
        if (not isinstance(command, list) or not command or
                any(not isinstance(part, str) or '\0' in part for part in command) or not command[0]):
            parser.error('--world-command must be a nonempty JSON argument list without NUL characters')
        args.world_command = command
    return args


class Launcher(EnvironmentHost):
    def __init__(self, args):
        super().__init__(args)
        self.record = {}
        self.lease = None
        self.active = False
        self.outbound = None
        self.viewer = None
        self.queue = None
        self.world_process = None
        self.world_log = None
        self.overlay = None

    def say(self, message):
        if self.directory is not None:
            with (self.directory / 'run.log').open('a', encoding='utf-8') as output:
                print(message, file=output)
        print(message, file=sys.stderr, flush=True)

    def snapshot(self):
        if self.directory is not None and self.record:
            self.record['updated_at'] = timestamp()
            if self.record.get('status') in TERMINAL:
                self.record.setdefault('finished_at', self.record['updated_at'])
            write_json(self.directory / 'run.json', self.record)

    def add(self, role, directory, options):
        name = self.names[role]
        self.say(f'Starting {role}: {name}')
        if role == 'workers' and self.overlay is not None:
            # Overlay the definition at its original location, retaining the
            # environment's normal relative commands and asset directories.
            options = options + ['--bind', f'{self.overlay},{self.overlay_target},ro']
        self.owned.append(name)
        self.command(self.dcomp + ['add-component', *options, self.args.system, name, str(self.directory / directory)])

    def snapshot_view(self, config_path):
        view = self.config.get('view')
        if not view:
            return
        source = package_file(config_path.parent, view, 'view')
        destination = self.directory / 'view'
        if source.parent != config_path.parent:
            assets = source.parent
        else:
            # A root-level inline view does not implicitly snapshot a project,
            # its world implementation, source checkout, or build products.
            if source.stat().st_size > PACKAGE_BYTES:
                raise LaunchError('Static view exceeds the 32 MiB snapshot limit')
            mkdir(destination)
            shutil.copyfile(source, destination / source.name, follow_symlinks=False)
            assets = None
        if assets is not None:
            snapshot_package(assets, destination, exclude=(self.args.root,))
        self.record.update(view_directory='view', view=str(source.relative_to(assets) if assets else source.name))

    def prepare_workers(self, environment):
        original = Environment(environment, external=self.args.external)
        config = deepcopy(original.config)
        existing = config['types'].get('swarm')
        if existing is not None and existing.get('command') != SWARM_COMMAND:
            raise LaunchError('Environment type swarm is reserved for the standard swarm worker')
        config['types']['swarm'] = {'command': SWARM_COMMAND}
        directory = self.directory / 'worker-overlay'
        mkdir(directory)
        self.overlay = directory / 'workers.json'
        write_json(self.overlay, config)
        self.prepare_environment(environment, self.args.link, external=directory)
        self.record['external_directory'] = str(original.external) if original.external is not None else None
        self.overlay_target = ('/opt/asys/environment/external/workers.json' if original.external is not None
                               else '/opt/asys/environment/workers.json')

    def start_world(self, config_path):
        channel = self.config['world']['channel']
        if self.args.world_command is not None:
            self.world_log = (self.directory / 'world.log').open('ab')
            self.world_process = subprocess.Popen(self.args.world_command, cwd=Path.cwd(),
                env={**os.environ, 'ASYS_RUNTIME_ROOT': str(self.directory / 'runtime'),
                     'ASYS_WORLD_CHANNEL': channel}, stdin=subprocess.DEVNULL,
                stdout=self.world_log, stderr=subprocess.STDOUT, start_new_session=True)
            self.record['world_service'] = {'mode': 'command', 'command': self.args.world_command,
                'directory': str(Path.cwd()), 'pid': self.world_process.pid, 'channel': channel}
            self.say(f'World process: {self.world_process.pid}; log: {self.directory / "world.log"}')
            return
        if self.args.world_external:
            self.record['world_service'] = {'mode': 'external', 'channel': channel}
            self.say(f'External world channel: {self.directory / "runtime/channels" / channel}')
            return
        source = self.args.world.expanduser().resolve(strict=True)
        if not source.is_dir() or not (source / 'component.dcomp').is_file():
            raise LaunchError('--world requires a component directory containing component.dcomp')
        directory = self.directory / 'world-component'
        mkdir(directory)
        (directory / 'component.dcomp').write_text((source / 'component.dcomp').read_text())
        preview = self.directory / 'world-preview.dcomp'
        preview.write_text('system world-preview\ncomponent world world-component\n')
        component = self.document('view', '--json', str(preview))['components'][0]
        if (source / 'Dockerfile').is_file():
            image_file = self.directory / 'world-image'
            self.say(f'Building world from {source}')
            self.command(['docker', 'build', '--iidfile', str(image_file), str(source)], timeout=900, cancellable=True)
            image = image_file.read_text().strip()
        else:
            image = self.command(['docker', 'image', 'inspect', '--format', '{{.Id}}', component['image_ref']]).strip()
        lines = [f'docker {image}']
        for direction in ('inputs', 'outputs'):
            lines += [f"{direction[:-1]} {entry['service']} {entry['name']}" for entry in component[direction]]
        (directory / 'component.dcomp').write_text('\n'.join(lines) + '\n')
        self.record['world_service'] = {'mode': 'component', 'directory': str(source), 'image': image, 'channel': channel}
        gid = self.directory.stat().st_gid if shared(self.directory) else os.getgid()
        self.add('world', 'world-component', ['--user', f'{os.getuid()}:{gid}',
            '--bind', f'{self.directory / "runtime/channels" / channel},/var/lib/asys/runtime/channels/{channel},rw',
            '--arg=--root', '--arg=/var/lib/asys/runtime', '--arg=--channel', f'--arg={channel}'])

    def check_world(self):
        if self.world_process is not None and self.world_process.poll() is not None:
            raise LaunchError(f'World process exited with status {self.world_process.returncode}; inspect {self.directory / "world.log"}')

    def setup(self):
        config_path = self.args.config.expanduser().absolute()
        if config_path.is_symlink():
            raise LaunchError('Swarm configuration must not be a symlink')
        config_path = config_path.resolve(strict=True)
        environment = self.args.environment.expanduser().resolve(strict=True)
        if not config_path.is_file() or not environment.is_dir():
            raise LaunchError('Expected a swarm configuration file and an environment directory')
        self.config = load_config(config_path)
        if self.config['world']['channel'] == CHANNEL:
            raise LaunchError('The world channel must differ from the swarm control channel')
        self.id, self.directory, workspace = prepare_run(state_root('runs', root=self.args.root), self.args.workspace)
        self.lease = (self.directory / 'launcher.lock').open('xb')
        fcntl.flock(self.lease, fcntl.LOCK_EX)
        name = re.sub(r'[^a-z0-9-]+', '-', config_path.stem.lower()).strip('-')[:32] or 'swarm'
        roles = ('world', 'workers') if self.args.world is not None else ('workers',)
        self.names = {role: f'swarm-{name}-{role}-{self.id[:10]}' for role in roles}
        self.channel = self.directory / 'runtime/channels' / CHANNEL
        self.job_directory = self.directory / 'jobs' / self.id
        world_channel = self.directory / 'runtime/channels' / self.config['world']['channel']
        for path in (self.job_directory, self.channel / 'in', self.channel / 'out', world_channel / 'in', world_channel / 'out'):
            mkdir(path, parents=True, exist_ok=True)
        self.record = {'id': self.id, 'manager': 'swarm', 'name': name, 'created_at': timestamp(),
                       'config': str(config_path), 'environment_directory': str(environment),
                       'workspace': str(workspace), 'system': self.args.system, 'components': self.names,
                       'dcomp': self.dcomp, 'status': 'starting', 'channel': str(self.channel),
                       'job_id': self.id, 'job_directory': f'jobs/{self.id}', 'swarm_state': f'jobs/{self.id}/swarm'}
        self.snapshot()
        self.record.update(name=self.config.get('name') or name)
        self.snapshot_view(config_path)
        write_json(self.directory / 'config.json', self.config)
        self.say(f'Run {self.id}\nState: {self.directory}\nWorkspace: {workspace}')
        self.prepare_workers(environment)
        self.start_world(config_path)
        self.snapshot()
        self.start_workers()
        self.wait_ready()
        self.check_world()
        if self.args.view:
            self.viewer = Viewer(self.directory, port=self.args.port).start()
            self.say(f'Viewer: {self.viewer.url}')

    def finish(self, data):
        self.active = False
        self.record['status'] = data['status']
        if data.get('error'):
            self.record['error'] = data['error']
        write_json(self.directory / 'result.json', data)
        self.snapshot()

    def execute(self):
        self.outbound = Reader(direction_root(self.directory / 'runtime', CHANNEL, 'out'))
        self.queue = Queue(environment_root(self.directory / 'runtime', self.record['environment']))
        self.job_directory = self.directory / 'jobs' / self.id
        mkdir(self.job_directory, parents=True, exist_ok=True)
        self.queue.submit('swarm', self.id, directory=self.job_directory, workspace=self.directory / 'workspace',
            input={'id': self.id, 'config': self.config, 'channel': CHANNEL}, metadata={'swarmRun': self.id})
        self.active = True
        self.record.update(status='running', components_removed=False, channel_after=0)
        self.snapshot()
        after, next_health, terminal_event = 0, time.monotonic() + 2, None
        with (self.directory / 'events.jsonl').open('a', encoding='utf-8') as journal:
            while True:
                self.check_interrupt()
                events = self.outbound.read(after)
                for event in events:
                    data = event['data']
                    journal.write(json.dumps(event, ensure_ascii=False) + '\n')
                    if data.get('runId') == self.id:
                        if event['type'] in {'swarm.paused', 'swarm.resumed'}:
                            self.record['status'] = 'paused' if event['type'] == 'swarm.paused' else 'running'
                            self.snapshot()
                        if event['type'] == 'swarm.snapshot':
                            self.record['turn'] = data.get('turn')
                            self.snapshot()
                        if event['type'] == 'run.result':
                            terminal_event = data
                    after = event['sequence']
                journal.flush()
                if events:
                    self.outbound.advance(after)
                state = self.queue.state(self.id)
                if state['status'] in JOB_TERMINAL:
                    # The runtime result can become visible between channel
                    # reads. Drain all already published events before closing
                    # the journal, including batches larger than read's limit.
                    if self.outbound.read(after, limit=1):
                        continue
                    data = self.job_result(state, terminal_event)
                    self.finish(data)
                    if data['status'] == 'failed':
                        raise LaunchError(data.get('error') or 'Swarm failed')
                    print(json.dumps(data.get('output', {}), indent=2, ensure_ascii=False), flush=True)
                    return
                if time.monotonic() >= next_health:
                    self.check_world()
                    self.check_components(self.observe()[1])
                    next_health = time.monotonic() + 2
                self.interrupted.wait(0.2)

    def job_result(self, state, event=None):
        result = state.get('result')
        data = dict(event or (result if isinstance(result, dict) else {}))
        data['runId'] = self.id
        if state['status'] == 'cancelled':
            data.update(status='cancelled')
        elif state['status'] != 'done':
            data.update(status='failed', error=state.get('error') or 'Swarm worker stopped before completion')
        elif data.get('status') not in TERMINAL:
            data.update(status='failed', error='Swarm worker returned no terminal result')
        elif isinstance(result, dict) and result.get('exception'):
            data.update(status='failed', error=result['exception'])
        return data

    def close(self):
        if self.directory is None:
            return True
        failure = self.record.get('error') if self.record.get('status') == 'failed' else None

        def finish_cleanup(data):
            if failure:
                data = {**data, 'status': 'failed', 'error': failure}
            self.finish(data)

        if self.active and self.outbound is not None:
            try:
                Writer(direction_root(self.directory / 'runtime', CHANNEL, 'in')).send('cancel', {'id': self.id})
                after, deadline = self.outbound.cursor, time.monotonic() + 5
                while time.monotonic() < deadline:
                    events = self.outbound.read(after)
                    for event in events:
                        after = event['sequence']
                        if event['type'] == 'run.result' and event['data'].get('runId') == self.id:
                            self.outbound.advance(after)
                            # The parent runtime job must finish recording its
                            # own result before its workers component is removed.
                            if self.queue is None:
                                finish_cleanup(event['data'])
                            break
                    if self.queue is not None:
                        state = self.queue.state(self.id)
                        if state['status'] in JOB_TERMINAL:
                            finish_cleanup(self.job_result(state))
                    if not self.active:
                        break
                    time.sleep(0.1)
                if self.active:
                    self.say('The swarm worker did not confirm cancellation before cleanup')
            except (OSError, ValueError) as error:
                self.say(f'Could not cancel through the swarm channel: {error}')
        if self.queue is not None:
            try:
                self.queue.cancel(self.id)
            except (OSError, ValueError) as error:
                self.say(f'Could not cancel the runtime job: {error}')
        clean = self.cleanup_components()
        if self.world_process is not None:
            try:
                os.killpg(self.world_process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.world_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(self.world_process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.world_process.wait()
            self.world_process = None
        if self.world_log is not None:
            self.world_log.close()
            self.world_log = None
        self.record['components_removed'] = clean
        self.snapshot()
        if self.lease is not None:
            self.lease.close()
            self.lease = None
        return clean


def main(argv=None):
    args = arguments(sys.argv[1:] if argv is None else argv)
    if args.command != 'run':
        viewer = None
        try:
            directory = Runs(state_root('runs', root=args.root)).select(args.run)
            if args.command != 'view':
                event = control(directory, args.command)
                print(json.dumps(event, ensure_ascii=False))
                return 0
            viewer = Viewer(directory, port=args.port).start()
            print(f'Viewer: {viewer.url}', flush=True)
            viewer.thread.join()
        except KeyboardInterrupt:
            return 0
        except (OSError, ValueError) as error:
            print(f'error: {error}', file=sys.stderr)
            return 1
        finally:
            if viewer is not None:
                viewer.close()
        return 0
    launcher = Launcher(args)
    previous = {sig: signal.signal(sig, lambda *_: launcher.interrupted.set()) for sig in (signal.SIGINT, signal.SIGTERM)}
    code = 0
    try:
        launcher.setup()
        launcher.execute()
        if launcher.record.get('status') == 'cancelled':
            code = 130
    except Interrupted:
        launcher.say('Cancelling swarm')
        launcher.record['status'] = 'cancelled'
        code = 130
    except (LaunchError, OSError, ValueError, KeyError) as error:
        launcher.say(f'error: {error}')
        launcher.record.update(status='failed', error=str(error))
        code = 1
    finally:
        try:
            if not launcher.close() and code == 0:
                code = 1
            if launcher.viewer is not None and code == 0:
                launcher.say('Run finished. Viewer remains open; press Ctrl-C to close it.')
                launcher.interrupted.wait()
        finally:
            if launcher.viewer is not None:
                launcher.viewer.close()
            for sig, handler in previous.items():
                signal.signal(sig, handler)
    return code
