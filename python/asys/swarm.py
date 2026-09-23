"""Launch a package-defined swarm controller in a worker environment."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import sys
import time

from asys_runtime.channel import Reader, Writer, direction_root
from asys_runtime.files import timestamp, write_json
from asys_runtime.permissions import mkdir
from asys_swarm.config import load_config
from .execution import EnvironmentHost, execution_options, prepare_run
from .lifecycle import Interrupted, LaunchError
from .runs import Runs, default_root
from .swarm_view import CHANNEL, TERMINAL, Viewer, control


EXCLUDED = {'.git', '.agents', '.codex', '.asys', '.asys-runtime', '.venv', 'node_modules', '__pycache__', '.pytest_cache'}
PACKAGE_BYTES = 32 * 1024 * 1024
PACKAGE_FILES = 2000


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
    common.add_argument('--root', type=Path, default=default_root(), metavar='DIRECTORY', help='saved run state directory')
    global_args, remaining = common.parse_known_args(argv)
    parser = argparse.ArgumentParser(prog='asys-swarm', parents=[common], allow_abbrev=False,
                                     description='Run swarms defined by ordinary configuration and world files.')
    commands = parser.add_subparsers(dest='command', required=True)
    run = commands.add_parser('run', help='run CONFIG in ENVIRONMENT_DIRECTORY', allow_abbrev=False)
    run.add_argument('config', type=Path, metavar='CONFIG.json')
    run.add_argument('environment', type=Path, metavar='ENVIRONMENT_DIRECTORY')
    execution_options(run)
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
    return args


class Launcher(EnvironmentHost):
    def __init__(self, args):
        super().__init__(args)
        self.record = {}
        self.lease = None
        self.active = False
        self.outbound = None
        self.viewer = None

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
        self.say(f"Starting {'swarm controller' if role == 'engine' else role}: {name}")
        self.owned.append(name)
        self.command(self.dcomp + ['add-component', *options, self.args.system, name, str(self.directory / directory)])

    def setup(self):
        config_path = self.args.config.expanduser().absolute()
        if config_path.is_symlink():
            raise LaunchError('Swarm configuration must not be a symlink')
        config_path = config_path.resolve(strict=True)
        environment = self.args.environment.expanduser().resolve(strict=True)
        if not config_path.is_file() or not environment.is_dir():
            raise LaunchError('Expected a swarm configuration file and an environment directory')
        # Validate before creating execution resources; the package snapshot is
        # loaded again so the controller sees exactly the retained definition.
        load_config(config_path)
        self.id, self.directory, workspace = prepare_run(self.args.root, self.args.workspace)
        self.lease = (self.directory / 'launcher.lock').open('xb')
        fcntl.flock(self.lease, fcntl.LOCK_EX)
        name = re.sub(r'[^a-z0-9-]+', '-', config_path.stem.lower()).strip('-')[:32] or 'swarm'
        self.names = {role: f'swarm-{name}-{role}-{self.id[:10]}' for role in ('workers', 'engine')}
        self.channel = self.directory / 'runtime/channels' / CHANNEL
        for path in (self.directory / 'swarm', self.directory / 'engine', self.channel / 'in', self.channel / 'out'):
            mkdir(path, parents=True, exist_ok=True)
        self.record = {'id': self.id, 'manager': 'swarm', 'name': name, 'created_at': timestamp(),
                       'config': str(config_path), 'environment_directory': str(environment),
                       'workspace': str(workspace), 'system': self.args.system, 'components': self.names,
                       'dcomp': self.dcomp, 'status': 'starting', 'channel': str(self.channel)}
        self.snapshot()
        snapshot_package(config_path.parent, self.directory / 'package', exclude=(self.args.root,))
        self.config = load_config(self.directory / 'package' / config_path.name)
        self.record.update(name=self.config.get('name') or name, view=self.config.get('view'))
        write_json(self.directory / 'config.json', self.config)
        self.say(f'Run {self.id}\nState: {self.directory}\nWorkspace: {workspace}')
        self.prepare_environment(environment, self.args.link, external=self.args.external)
        image = os.environ.get('ASYS_SWARM_IMAGE') or 'asys-swarm:dev'
        try:
            engine_id = self.command(['docker', 'image', 'inspect', '--format', '{{.Id}}', image]).strip()
        except LaunchError as error:
            raise LaunchError(f'Swarm image {image} is unavailable. Build it with make -C asys-swarm build.\n{error}') from error
        (self.directory / 'engine/component.dcomp').write_text(f'docker {engine_id}\n')
        self.snapshot()
        self.start_workers()
        self.add('engine', 'engine', self.execution_mounts() + [
            '--bind', f"{self.directory / 'swarm'},/var/lib/asys-swarm,rw",
            '--bind', f"{self.directory / 'package'},/opt/asys/swarm-package,ro",
            '--arg=--root', '--arg=/var/lib/asys/runtime',
            '--arg=--state', '--arg=/var/lib/asys-swarm',
            '--arg=--workspace', '--arg=/var/lib/asys/workspace',
            '--arg=--package', '--arg=/opt/asys/swarm-package',
            '--arg=--channel', f'--arg={CHANNEL}',
        ])
        self.wait_ready()
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
        inbound = Writer(direction_root(self.directory / 'runtime', CHANNEL, 'in'))
        self.outbound = Reader(direction_root(self.directory / 'runtime', CHANNEL, 'out'))
        self.request = inbound.send('start', {'id': self.id, 'environment': self.record['environment'],
                                            'environmentDefinition': self.definition, 'config': self.config})
        self.active = True
        self.record.update(status='running', components_removed=False, channel_after=0)
        self.snapshot()
        after, next_health = 0, time.monotonic() + 2
        with (self.directory / 'events.jsonl').open('a', encoding='utf-8') as journal:
            while True:
                self.check_interrupt()
                events = self.outbound.read(after)
                for event in events:
                    data = event['data']
                    journal.write(json.dumps(event, ensure_ascii=False) + '\n')
                    if event['type'] == 'rejected' and data.get('request') == self.request['sequence']:
                        self.active = False
                        raise LaunchError(data.get('message') or str(data))
                    if data.get('runId') == self.id:
                        if event['type'] in {'swarm.paused', 'swarm.resumed'}:
                            self.record['status'] = 'paused' if event['type'] == 'swarm.paused' else 'running'
                            self.snapshot()
                        if event['type'] == 'swarm.snapshot':
                            self.record['turn'] = data.get('turn')
                            self.snapshot()
                        if event['type'] == 'run.result':
                            journal.flush()
                            self.outbound.advance(event['sequence'])
                            self.finish(data)
                            if data['status'] == 'failed':
                                raise LaunchError(data.get('error') or 'Swarm failed')
                            print(json.dumps(data.get('output', {}), indent=2, ensure_ascii=False), flush=True)
                            return
                    after = event['sequence']
                journal.flush()
                if events:
                    self.outbound.advance(after)
                if time.monotonic() >= next_health:
                    self.check_components(self.observe()[1])
                    next_health = time.monotonic() + 2
                self.interrupted.wait(0.2)

    def close(self):
        if self.directory is None:
            return True
        if self.active and self.outbound is not None and self.record.get('status') == 'cancelled':
            try:
                Writer(direction_root(self.directory / 'runtime', CHANNEL, 'in')).send('cancel', {'id': self.id})
                after, deadline = self.outbound.cursor, time.monotonic() + 5
                while time.monotonic() < deadline:
                    events = self.outbound.read(after)
                    for event in events:
                        after = event['sequence']
                        if event['type'] == 'run.result' and event['data'].get('runId') == self.id:
                            self.outbound.advance(after)
                            self.finish(event['data'])
                            break
                    if not self.active:
                        break
                    time.sleep(0.1)
                if self.active:
                    self.say('The swarm component did not confirm cancellation before cleanup')
            except (OSError, ValueError) as error:
                self.say(f'Could not cancel through the swarm channel: {error}')
        clean = self.cleanup_components()
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
            directory = Runs(args.root).select(args.run)
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
