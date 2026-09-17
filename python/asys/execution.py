"""Shared host support for running assignments in dcomp worker environments."""
import json
import os
from pathlib import Path
import re
import tempfile
import time
import uuid

from asys_runtime.environment import Environment
from asys_runtime.permissions import mkdir, shared
from .lifecycle import ComponentHost, LaunchError
from .human_service import ensure_human

PROVIDER = 'cyclo.provider.v1.Provider'
HUMAN = 'asys.human.v1.Human'


def execution_options(parser):
    parser.add_argument('--workspace', type=Path, default=Path.cwd(), metavar='DIRECTORY',
                        help='actual project directory to work in (default: current directory)')
    parser.add_argument('-L', '--link', action='append', default=[], metavar='INPUT=TARGET',
                        help='wire an environment input to COMPONENT.OUTPUT or @GLOBAL; - leaves it unconnected')
    parser.add_argument('--system', default='asys', help='dcomp system (default: asys)')
    parser.add_argument('--dcomp-state-root', type=Path, metavar='DIRECTORY')
    parser.add_argument('--runtime-root', type=Path, metavar='DIRECTORY', help='dcomp proxy root')


def prepare_run(root, workspace):
    workspace = workspace.expanduser().resolve(strict=True)
    root = root.expanduser().resolve()
    if not workspace.is_dir():
        raise LaunchError('Workspace must be an existing project directory')
    if any(',' in str(path) for path in (root, workspace)):
        raise LaunchError('State and workspace paths cannot contain commas (dcomp mount syntax)')
    mkdir(root, parents=True, exist_ok=True)
    if shared(root):
        os.umask(0o007)
    run_id = uuid.uuid4().hex
    directory = root / run_id
    mkdir(directory)
    for name in ('runtime', 'jobs'):
        mkdir(directory / name)
    mkdir(directory / 'environment')
    # A local reference to the real project, matching the container mount's
    # position relative to the queue. Neither side copies or populates it.
    (directory / 'workspace').symlink_to(workspace, target_is_directory=True)
    return run_id, directory, workspace


class EnvironmentHost(ComponentHost):
    def prepare_environment(self, environment, links, *, human_target=None):
        descriptor = Environment(environment).descriptor
        config = json.loads((environment / 'workers.json').read_text())
        self.definition = descriptor['definition']
        self.record.update(environment=descriptor['name'], environment_directory=str(environment),
                           egress=config.get('egress', False))
        version = json.loads(self.command([self.dcomp[0], 'version', '--json']))
        release = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)(?:[-+].*)?', version.get('version', ''))
        if version.get('api_version') != 2 or not release or tuple(map(int, release.groups())) < (0, 3, 1):
            raise LaunchError(f'Requires dcomp 0.3.1 (Machine API 2); {self.dcomp[0]} reports {version}')
        manifest = self.directory / 'environment/component.dcomp'
        with tempfile.TemporaryDirectory(prefix='preview-', dir=self.directory) as temporary:
            preview = Path(temporary)
            (preview / 'environment').mkdir()
            (preview / 'environment/component.dcomp').write_text((environment / 'component.dcomp').read_text())
            system = preview / 'preview.dcomp'
            system.write_text('system execution-preview\ncomponent environment environment\n')
            component = self.document('view', '--json', str(system))['components'][0]
        inputs = {entry['name']: entry['service'] for entry in component['inputs']}
        human_worker = any(Path(worker['command'][0]).name == 'asys-human' for worker in config['types'].values())
        if human_target is not None or human_worker:
            for entry in component['inputs'] + component['outputs']:
                if entry['name'] == 'human' and entry['service'] != HUMAN:
                    raise LaunchError(f"Environment interface 'human' uses {entry['service']}; expected {HUMAN}")
            # Workers call Human; the separate Human component exports it.
            component['outputs'] = [entry for entry in component['outputs'] if entry['name'] != 'human']
            if 'human' not in inputs:
                component['inputs'].append({'name': 'human', 'service': HUMAN})
                inputs['human'] = HUMAN
        resolved = {'inference': '@inference_endpoint'} if inputs.get('inference') == PROVIDER else {}
        if inputs.get('human') == HUMAN:
            resolved['human'] = '@human_endpoint'
        explicit = set()
        for link in links:
            source, separator, target = link.partition('=')
            if not separator or not target or source not in inputs or source in explicit:
                raise LaunchError(f'Invalid or duplicate environment link {link!r}; use INPUT=TARGET for a declared input')
            explicit.add(source)
            resolved[source] = target
        if human_target is not None:
            resolved['human'] = human_target
        if resolved.get('human') == '@human_endpoint':
            ensure_human(self)
        existing = self.document('view', '--json', self.args.system)
        if existing.get('operation'):
            raise LaunchError(f'dcomp system {self.args.system} has a pending operation')
        for source, target in resolved.items():
            if target.startswith('@') and not any(item['name'] == target[1:] for item in existing.get('globals', [])):
                raise LaunchError(f'Global {target} is not declared in dcomp system {self.args.system}; start its provider or supply -L {source}=TARGET')
        image = self.environment_image(environment, component['image_ref'])
        lines = [f'docker {image}']
        for direction in ('inputs', 'outputs'):
            lines += [f"{direction[:-1]} {entry['service']} {entry['name']}" for entry in component[direction]]
        manifest.write_text('\n'.join(lines) + '\n')
        self.record['links'] = resolved
        return descriptor

    def build_environment(self, environment):
        self.say(f"Building environment {self.record['environment']} from {environment}")
        image_file = self.directory / 'environment-image'
        self.command(['docker', 'build', '--iidfile', str(image_file), str(environment)], timeout=900, cancellable=True)
        return image_file.read_text().strip()

    def environment_image(self, environment, image_ref):
        if (environment / 'Dockerfile').is_file():
            return self.build_environment(environment)
        return self.command(['docker', 'image', 'inspect', '--format', '{{.Id}}', image_ref]).strip()

    def execution_mounts(self):
        if not Path(self.record['workspace']).is_dir():
            raise LaunchError(f"Workspace is unavailable: {self.record['workspace']}")
        gid = self.directory.stat().st_gid if shared(self.directory) else os.getgid()
        options = ['--user', f'{os.getuid()}:{gid}']
        for name in ('runtime', 'jobs'):
            options += ['--bind', f'{self.directory / name},/var/lib/asys/{name},rw']
        options += ['--bind', f"{self.record['workspace']},/var/lib/asys/workspace,rw"]
        return options

    def start_workers(self):
        options = self.execution_mounts()
        if self.record.get('egress'):
            options.append('--egress')
        for source, target in self.record['links'].items():
            if target != '-':
                options += ['--link', f'{source}={target}']
        self.add('workers', 'environment', options)

    def add(self, role, directory, options):
        name = self.names[role]
        self.say(f"Starting {'workflow' if role == 'engine' else role}: {name}")
        self.owned.append(name)
        self.command(self.dcomp + ['add-component', *options, self.args.system, name, str(self.directory / directory)])

    def observe(self, *, cleanup=False):
        document = self.document('view', '--json', self.args.system, cleanup=cleanup)
        return document, {item['name']: item for item in document['components'] if item['name'] in self.owned}

    def wait_ready(self):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            self.check_interrupt()
            _, components = self.observe()
            self.check_components(components)
            if len(components) == len(self.owned) and all(
                component['status'].get('status') == 'running' and component['status'].get('health') in {'healthy', 'none'}
                for component in components.values()
            ):
                return
            self.interrupted.wait(0.2)
        raise LaunchError('Timed out waiting for components to start')
