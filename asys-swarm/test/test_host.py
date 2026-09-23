"""Host boundaries: package snapshots, runtime-only control, and lifecycle."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'python'), str(ROOT / 'asys-runtime'), str(ROOT / 'asys-workers')]

from asys_runtime.channel import Reader, Writer, direction_root
from asys_runtime.files import read_json, write_json
from asys_runtime.environment import environment_root
from asys_runtime.queue import Queue
from asys.swarm import Launcher, arguments, main, snapshot_package
from asys.swarm_view import Viewer, control


class PackageTests(unittest.TestCase):
    def test_snapshot_is_frozen_and_excludes_dependencies_and_run_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'package'
            source.mkdir()
            (source / 'world.py').write_text('original')
            (source / 'world.py').chmod(0o700)
            (source / 'node_modules').mkdir()
            (source / 'node_modules/ignored.js').write_text('dependency')
            runs = source / 'runs'
            runs.mkdir()
            target = runs / 'current/package'
            snapshot_package(source, target, exclude=(runs,))
            (source / 'world.py').write_text('modified')
            self.assertEqual((target / 'world.py').read_text(), 'original')
            self.assertTrue((target / 'world.py').stat().st_mode & 0o100)
            self.assertFalse((target / 'node_modules').exists())
            self.assertFalse((target / 'runs').exists())

    def test_rejects_symlinks_and_oversized_packages(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'package'
            source.mkdir()
            (source / 'world.py').symlink_to(root / 'outside')
            with self.assertRaisesRegex(ValueError, 'symlink'):
                snapshot_package(source, root / 'saved')
            (source / 'world.py').unlink()
            (source / 'world.py').write_text('too big')
            with patch('asys.swarm.PACKAGE_BYTES', 2):
                with self.assertRaisesRegex(ValueError, '32 MiB'):
                    snapshot_package(source, root / 'saved')


class ViewerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        write_json(self.directory / 'run.json', {'id': 'experiment', 'manager': 'swarm', 'name': 'test', 'status': 'running'})
        self.outbound = Writer(direction_root(self.directory / 'runtime', 'swarm', 'out'))
        self.viewer = None

    def tearDown(self):
        if self.viewer is not None:
            self.viewer.close()
        self.temporary.cleanup()

    def start(self):
        self.viewer = Viewer(self.directory).start()

    def request(self, path, data=None, **headers):
        request = Request(self.viewer.url.rstrip('/') + path,
                          data=json.dumps(data).encode() if data is not None else None,
                          headers={'Content-Type': 'application/json', **headers})
        with urlopen(request, timeout=2) as response:
            return response.status, json.load(response)

    def test_snapshot_and_event_reads_never_advance_shared_cursor(self):
        state = {'runId': 'experiment', 'turn': 3, 'state': {'water': 7}}
        self.outbound.send('swarm.snapshot', state)
        self.outbound.send('run.completed', {'runId': 'experiment'})
        self.start()
        self.assertEqual(self.request('/api/state'), (200, state))
        status, result = self.request('/api/events?after=0')
        self.assertEqual(status, 200)
        self.assertEqual(result['after'], 2)
        self.assertEqual(len(result['events']), 2)
        self.assertEqual(Reader(self.outbound.directory).cursor, 0)
        self.assertEqual(self.request('/api/events?after=1')[1]['events'][0]['sequence'], 2)

    def test_saved_checkpoint_uses_the_same_state_shape_as_live_snapshots(self):
        (self.directory / 'swarm').mkdir()
        write_json(self.directory / 'swarm/checkpoint.json', {'id': 'experiment', 'turn': 12,
            'world': {'water': 4}, 'evaluation': {'achieved': False, 'metrics': {'gardens': 2}},
            'status': 'completed', 'config': {'mission': 'Grow', 'objective': {'gardens': 3}}})
        self.start()
        _, state = self.request('/api/state')
        self.assertEqual((state['runId'], state['turn'], state['state']), ('experiment', 12, {'water': 4}))
        self.assertEqual(state['metrics'], {'gardens': 2})
        self.assertEqual(state['objective'], {'gardens': 3})

    def test_worker_owned_checkpoint_and_static_view_are_supported(self):
        (self.directory / 'jobs/experiment/swarm').mkdir(parents=True)
        (self.directory / 'view').mkdir()
        (self.directory / 'view/index.html').write_text('<h1>Worker swarm</h1>')
        record = read_json(self.directory / 'run.json')
        write_json(self.directory / 'run.json', {**record, 'swarm_state': 'jobs/experiment/swarm',
            'view_directory': 'view', 'view': 'index.html'})
        write_json(self.directory / 'jobs/experiment/swarm/checkpoint.json', {'id': 'experiment',
            'turn': 2, 'world': {'temperature': 17}, 'status': 'paused'})
        self.start()
        self.assertEqual(self.request('/api/state')[1]['state'], {'temperature': 17})
        with urlopen(self.viewer.url, timeout=2) as response:
            self.assertEqual(response.read(), b'<h1>Worker swarm</h1>')

    def test_controls_write_channel_and_reject_cross_origin(self):
        self.start()
        status, event = self.request('/api/control', {'type': 'pause'}, Origin=self.viewer.url.rstrip('/'))
        self.assertEqual((status, event['type'], event['data']), (202, 'pause', {'id': 'experiment'}))
        with self.assertRaises(HTTPError) as caught:
            self.request('/api/control', {'type': 'cancel'}, Origin='https://elsewhere.test')
        self.assertEqual(caught.exception.code, 403)
        self.assertEqual(len(Reader(direction_root(self.directory / 'runtime', 'swarm', 'in')).read(0)), 1)
        record = read_json(self.directory / 'run.json')
        write_json(self.directory / 'run.json', {**record, 'status': 'completed'})
        with self.assertRaisesRegex(ValueError, 'already completed'):
            control(self.directory, 'resume')

    def test_custom_view_and_static_files_are_scoped(self):
        package = self.directory / 'package'
        (package / 'ui').mkdir(parents=True)
        (package / 'ui/view.html').write_text('<h1>My world</h1>')
        (package / 'ui/theme.css').write_text('body{}')
        (package / 'world.py').write_text('secret module')
        record = read_json(self.directory / 'run.json')
        write_json(self.directory / 'run.json', {**record, 'view': 'ui/view.html'})
        self.start()
        with urlopen(self.viewer.url, timeout=2) as response:
            self.assertEqual(response.read(), b'<h1>My world</h1>')
        with urlopen(self.viewer.url + 'theme.css', timeout=2) as response:
            self.assertEqual(response.read(), b'body{}')
        with self.assertRaises(HTTPError) as caught:
            urlopen(self.viewer.url + '%2e%2e/world.py', timeout=2)
        self.assertEqual(caught.exception.code, 404)


class HostTests(unittest.TestCase):
    def test_installed_cli_finds_its_packages_without_checkout_pythonpath(self):
        with tempfile.TemporaryDirectory() as temporary:
            subprocess.run(['make', '-s', '-C', str(ROOT / 'asys-swarm'), 'install-host',
                            f'PREFIX={temporary}'], check=True, capture_output=True, text=True)
            result = subprocess.run([str(Path(temporary) / 'bin/asys-swarm'), '--help'],
                                    cwd=temporary, check=True, capture_output=True, text=True,
                                    env={'PATH': '/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE': '1'})
            self.assertIn('{run,view,pause,resume,cancel}', result.stdout)
            self.assertTrue((Path(temporary) / 'share/asys-swarm/examples/terrarium/swarm.json').is_file())

    def test_cli_has_no_named_demo_dispatch_and_accepts_root_anywhere(self):
        args = arguments(['run', '/package/config.json', '/environment', '--world-external', '--root', '/runs', '--workspace', '/work', '--view'])
        self.assertEqual(args.root, Path('/runs'))
        self.assertTrue(args.view)
        args = arguments(['--root', '/runs', 'pause', 'abc'])
        self.assertEqual((args.command, args.run, args.root), ('pause', 'abc', Path('/runs')))
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                arguments(['demo', 'terrarium'])
            for extra in ([], ['--world', '/world', '--world-external'], ['--world-command', 'echo hello']):
                with self.assertRaises(SystemExit):
                    arguments(['run', 'config.json', 'env', *extra])

    def test_host_world_is_explicit_process_with_channel_environment_and_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / 'serve.py'
            marker = root / 'started.json'
            script.write_text('import json,os,pathlib,time\n'
                f'pathlib.Path({str(marker)!r}).write_text(json.dumps({{"root":os.environ["ASYS_RUNTIME_ROOT"],'
                '"channel":os.environ["ASYS_WORLD_CHANNEL"],"cwd":os.getcwd()}))\n'
                'time.sleep(60)\n')
            launcher = Launcher(arguments(['run', 'config.json', 'env', '--world-command', json.dumps([sys.executable, str(script)])]))
            launcher.directory, launcher.id = root, 'run-1'
            launcher.config = {'world': {'channel': 'experiment-world'}}
            launcher.record = {'id': 'run-1', 'manager': 'swarm', 'status': 'starting'}
            launcher.say = lambda *_: None
            launcher.cleanup_components = lambda: True
            try:
                launcher.start_world(root / 'config.json')
                process = launcher.world_process
                deadline = time.monotonic() + 2
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(read_json(marker), {'root': str(root / 'runtime'),
                    'channel': 'experiment-world', 'cwd': str(Path.cwd())})
                self.assertEqual(launcher.record['world_service']['pid'], process.pid)
            finally:
                launcher.close()
            self.assertIsNotNone(process.poll())

    def test_external_overlay_preserves_assets_and_member_commands(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment, external = root / 'env', root / 'external'
            environment.mkdir()
            external.mkdir()
            (external / 'program.py').write_text('pass')
            original = {'version': 1, 'name': 'external-members', 'egress': True,
                        'types': {'step': {'command': ['./program.py'], 'env': {'EXAMPLE': 'value'}, 'timeout': 12}}}
            write_json(external / 'workers.json', original)
            launcher = Launcher(arguments(['run', 'config.json', str(environment), '--world-external', '--external', str(external)]))
            launcher.directory, launcher.record = root, {}
            prepared = []
            launcher.prepare_environment = lambda directory, links, **kwargs: prepared.append((directory, kwargs['external']))
            launcher.prepare_workers(environment)
            self.assertEqual(launcher.record['external_directory'], str(external))
            self.assertEqual(launcher.overlay_target, '/opt/asys/environment/external/workers.json')
            self.assertEqual(read_json(launcher.overlay)['types']['step'], original['types']['step'])
            self.assertEqual(read_json(external / 'workers.json'), original)
            self.assertFalse((launcher.overlay.parent / 'program.py').exists())
            self.assertEqual(prepared, [(environment, launcher.overlay.parent)])

    def test_one_worker_component_and_world_channel_only_mount(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package, workspace, environment, world = [root / name for name in ('package', 'workspace', 'environment', 'world')]
            for path in (package, workspace, environment, world):
                path.mkdir()
            (package / 'experiment.json').write_text('{}')
            (package / 'world.py').write_text('pass')
            (package / 'view.html').write_text('<h1>View</h1>')
            (world / 'component.dcomp').write_text('docker world:dev\n')
            write_json(environment / 'workers.json', {'version': 1, 'name': 'workers', 'types': {
                'agent-step': {'command': ['./programs/decide']}}})
            args = arguments(['run', str(package / 'experiment.json'), str(environment), '--world', str(world),
                              '--root', str(root / 'runs'), '--workspace', str(workspace)])
            launcher = Launcher(args)
            launcher.say = lambda *_: None
            additions = []

            def prepare(*_, **__):
                launcher.definition = 'definition'
                launcher.record.update(environment='workers', links={})

            def command(command, **_):
                if 'add-component' in command:
                    role = next(role for role, name in launcher.names.items() if name in command)
                    additions.append((role, command))
                return 'sha256:pinned'

            launcher.prepare_environment = prepare
            launcher.command = command
            launcher.document = lambda *_: {'components': [{'image_ref': 'world:dev', 'inputs': [], 'outputs': []}]}
            launcher.wait_ready = lambda: None
            launcher.worker_models = lambda: {}
            launcher.cleanup_components = lambda: True
            try:
                with patch('asys.swarm.load_config', return_value={'name': 'my-swarm', 'world': {'channel': 'world'}, 'view': 'view.html'}):
                    launcher.setup()
                world, workers = additions
                self.assertEqual(workers[0], 'workers')
                self.assertEqual(world[0], 'world')
                self.assertNotIn('engine', launcher.names)
                self.assertFalse((launcher.directory / 'package').exists())
                self.assertFalse((launcher.directory / 'view/world.py').exists())
                self.assertEqual((launcher.directory / 'view/view.html').read_text(), '<h1>View</h1>')
                self.assertFalse(any('swarm-package' in option or 'world.py' in option for option in workers[1]))
                self.assertIn(f'{workspace},/var/lib/asys/workspace,rw', workers[1])
                self.assertIn(f"{launcher.directory / 'runtime/channels/world'},/var/lib/asys/runtime/channels/world,rw", world[1])
                self.assertFalse(any('/var/lib/asys/jobs' in option or '/var/lib/asys/workspace' in option for option in world[1]))
                self.assertIn(f'{launcher.overlay},/opt/asys/environment/workers.json,ro', workers[1])
                overlay = read_json(launcher.overlay)
                self.assertEqual(overlay['types']['agent-step']['command'], ['./programs/decide'])
                self.assertEqual(overlay['types']['swarm']['command'], ['/opt/asys/asys-workers/tools/asys-swarm'])
                self.assertEqual(read_json(launcher.directory / 'config.json')['name'], 'my-swarm')
            finally:
                launcher.close()

    def test_execute_submits_one_job_and_preserves_unachieved_completed_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = arguments(['run', 'config.json', 'env', '--world-external'])
            launcher = Launcher(args)
            launcher.directory, launcher.id = root, 'run-1'
            (root / 'workspace').mkdir()
            launcher.record = {'id': launcher.id, 'manager': 'swarm', 'environment': 'workers'}
            launcher.definition, launcher.config = 'abc', {'objective': 'test'}
            # Slow filesystem fsyncs may cross the launcher's health interval;
            # this queue/channel unit test must never inspect a real system.
            launcher.observe = lambda: ({}, {})
            launcher.check_components = lambda *_: None
            output = {'achieved': False, 'reason': 'limit', 'metrics': {'done': 3}}
            errors = []

            def component():
                try:
                    queue = Queue(environment_root(root / 'runtime', 'workers'))
                    deadline = time.monotonic() + 2
                    while not (queue.directory('run-1') / 'request.json').exists() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    request = queue.request('run-1')
                    self.assertEqual(request['type'], 'swarm')
                    self.assertEqual(request['input'], {'id': 'run-1', 'config': {'objective': 'test'}, 'channel': 'swarm'})
                    self.assertEqual(queue.paths('run-1')['directory'], root / 'jobs/run-1')
                    outbound = Writer(direction_root(root / 'runtime', 'swarm', 'out'))
                    for turn in range(1, 151):
                        outbound.send('swarm.snapshot', {'runId': 'run-1', 'turn': turn, 'state': {}})
                    outbound.send('run.result', {'runId': 'run-1', 'status': 'completed', 'output': output})
                    # A terminal event alone must not tear down a parent job
                    # before runtime has durably recorded its result.
                    time.sleep(0.05)
                    self.assertTrue(launcher.active)
                    state = queue.state('run-1')
                    state.update(status='done', result={'status': 'completed', 'output': output, 'exception': None})
                    queue.save(state)
                except BaseException as error:
                    errors.append(error)

            thread = threading.Thread(target=component)
            thread.start()
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                launcher.execute()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(json.loads(stdout.getvalue()), output)
            self.assertEqual(read_json(root / 'result.json')['status'], 'completed')
            self.assertEqual(launcher.record['turn'], 150)
            self.assertFalse(launcher.active)
            self.assertEqual(len((root / 'events.jsonl').read_text().splitlines()), 151)
            self.assertEqual(len(launcher.queue.list()), 1)
            self.assertEqual(Reader(direction_root(root / 'runtime', 'swarm', 'in')).read(0), [])

    def test_viewer_interrupt_after_completion_keeps_terminal_status(self):
        class FakeLauncher:
            def __init__(self, args):
                self.record, self.interrupted = {}, threading.Event()
                self.viewer = type('FakeViewer', (), {'close': lambda self: None})()

            def setup(self):
                pass

            def execute(self):
                self.record['status'] = 'completed'

            def close(self):
                self.interrupted.set()
                self.assertion = self.record['status']
                return True

            def say(self, *_):
                pass

        fake = FakeLauncher(None)
        with patch('asys.swarm.Launcher', return_value=fake):
            self.assertEqual(main(['run', 'x', 'y', '--world-external', '--view']), 0)
        self.assertEqual(fake.record['status'], 'completed')

    def test_worker_failure_without_channel_result_is_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'workspace').mkdir()
            launcher = Launcher(arguments(['run', 'config.json', 'env', '--world-external']))
            launcher.directory, launcher.id = root, 'run-1'
            launcher.record = {'id': 'run-1', 'manager': 'swarm', 'environment': 'workers'}
            launcher.config = {}
            launcher.observe = lambda: ({}, {})
            launcher.check_components = lambda *_: None
            queue = Queue(environment_root(root / 'runtime', 'workers'))

            def worker():
                deadline = time.monotonic() + 2
                while not (queue.directory('run-1') / 'request.json').exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                state = queue.state('run-1')
                state.update(status='failed', error='World identity handshake failed')
                queue.save(state)

            thread = threading.Thread(target=worker)
            thread.start()
            from asys.lifecycle import LaunchError
            with self.assertRaisesRegex(LaunchError, 'World identity handshake failed'):
                launcher.execute()
            thread.join(timeout=3)
            self.assertEqual(read_json(root / 'result.json')['status'], 'failed')
            self.assertFalse(launcher.active)

    def test_interrupt_cancels_through_runtime_before_component_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            launcher = Launcher(arguments(['run', 'config.json', 'env', '--world-external']))
            launcher.directory, launcher.id = root, 'run-1'
            launcher.record = {'id': 'run-1', 'manager': 'swarm', 'status': 'cancelled'}
            launcher.active = True
            launcher.say = lambda *_: None
            launcher.outbound = Reader(direction_root(root / 'runtime', 'swarm', 'out'))
            result = {'runId': 'run-1', 'status': 'cancelled', 'output': {'reason': 'cancelled'}}
            seen = []

            def component():
                inbound = Reader(direction_root(root / 'runtime', 'swarm', 'in'))
                event = next(inbound.follow(0, timeout=2))
                seen.append(event['type'])
                Writer(launcher.outbound.directory).send('run.result', result)

            thread = threading.Thread(target=component)
            thread.start()
            launcher.cleanup_components = lambda: seen.append('cleanup') or True
            self.assertTrue(launcher.close())
            thread.join(timeout=5)
            self.assertEqual(seen, ['cancel', 'cleanup'])
            self.assertEqual(read_json(root / 'result.json'), result)
            self.assertTrue(read_json(root / 'run.json')['components_removed'])


if __name__ == '__main__':
    unittest.main()
