"""Exercise the shared launcher against an actual local runtime queue."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'python'), str(ROOT / 'asys-runtime'), str(ROOT / 'asys-workers')]
from asys.run import Run, arguments, execution_models, retain_view, selected_definition
from asys.execution import human_workers
from asys.runs import Runs
from asys.worker_definitions import bind_definition


PROGRAM = '''import json, os, pathlib, sys
request = json.load(sys.stdin)
target = pathlib.Path('source.txt')
target.write_text(target.read_text() + ':edited')
json.dump({'final': request['request'], 'parameters': request.get('parameters'),
           'workers': os.environ['ASYS_WORKERS_DIR']}, open(os.environ['ASYS_RESULT'], 'w'))
'''


class LocalRun(Run):
    child = None

    def command(self, command, **kwargs):
        if command[0] == 'docker':
            assert command[1:3] == ['image', 'inspect']
            return 'sha256:fixture'
        operation = command[1]
        if operation == 'version':
            return json.dumps({'api_version': 2, 'version': '0.3.1'})
        if operation == 'add-component':
            self.launch_arguments = command
            self.child = subprocess.Popen([sys.executable, '-c', '''
import json, signal, sys
sys.path.insert(0, sys.argv[1])
from asys_runtime.environment import Environment
from asys_runtime.runtime import Runtime
environment = Environment(sys.argv[2], external=sys.argv[3])
for spec in environment.types.values():
    # Only the container transport and model executable are substituted.
    if spec['command'][0] == '/opt/asys/asys-workers/tools/asys-worker':
        spec['command'] = [sys.executable, '-c', sys.argv[5]]
    spec['env']['ASYS_WORKERS_DIR'] = sys.argv[2]
with environment.register(sys.argv[4]) as root:
    runtime = Runtime(root, environment.types)
    signal.signal(signal.SIGTERM, lambda *_: runtime.stop())
    runtime.run()
''', str(ROOT / 'asys-runtime'), str(self.args.environment), str(self.overlay.parent),
                str(self.directory / 'runtime'), PROGRAM], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            descriptor = self.directory / 'runtime/environments' / self.record['environment'] / 'environment.json'
            deadline = time.monotonic() + 5
            while not descriptor.exists():
                if self.child.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError('Fixture runtime did not start')
                time.sleep(.02)
            return ''
        if operation == 'rm-component':
            self.child.terminate()
            _, error = self.child.communicate(timeout=5)
            assert self.child.returncode == 0, error
            return ''
        if operation == 'logs':
            return ''
        assert operation == 'view', command
        if command[-1].endswith('preview.dcomp'):
            components = [{'name': 'environment', 'inputs': [], 'outputs': [], 'image_ref': 'fixture'}]
        else:
            components = [{'name': name, 'status': {'status': 'running', 'health': 'healthy'}} for name in self.owned]
        return json.dumps({'api_version': 2, 'components': components, 'globals': []})


class RunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.environment = self.root / 'env'
        self.environment.mkdir()
        self.workspace = self.root / 'actual project'
        self.workspace.mkdir()
        (self.workspace / 'source.txt').write_text('original')
        (self.environment / 'component.dcomp').write_text('docker fixture\n')
        (self.environment / 'workers.json').write_text(json.dumps({'version': 1, 'name': 'testing',
            'types': {'writer': {'command': [sys.executable, '-c', PROGRAM]},
                      'other': {'command': ['./tools/another']}}}))

    def launcher(self, worker='writer', *options):
        launcher = LocalRun(arguments([str(self.environment), worker, 'Update this project.',
            '--workspace', str(self.workspace), '--root', str(self.root / 'state'), *options]))
        self.addCleanup(launcher.close)
        return launcher

    def execute(self, launcher):
        launcher.setup()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            launcher.execute()
        return json.loads(output.getvalue())

    def test_program_uses_actual_workspace_and_is_visible_in_standard_status(self):
        launcher = self.launcher()
        result = self.execute(launcher)
        self.assertEqual(result['final'], 'Update this project.')
        self.assertEqual(result['workers'], str(self.environment))
        self.assertEqual((self.workspace / 'source.txt').read_text(), 'original:edited')
        self.assertEqual(sorted(p.name for p in self.workspace.iterdir()), ['source.txt'])
        self.assertEqual(launcher.queue.request(launcher.job_id)['type'], 'writer')
        self.assertIsNone(launcher.record['external_directory'])
        config = json.loads(launcher.overlay.read_text())
        self.assertEqual(config['types']['other']['command'], ['./tools/another'])
        self.assertIn(f'{launcher.overlay},/opt/asys/environment/workers.json,ro', launcher.launch_arguments)
        observed = Runs(self.root / 'state/runs').snapshot(launcher.directory)
        self.assertEqual(observed['status'], 'completed')
        self.assertEqual(observed['jobs'][0]['name'], 'writer')

    def test_named_definition_is_snapshotted_and_preserves_other_bindings(self):
        definition = {'version': 1, 'kind': 'goal', 'config': {'maxAttempts': 2}}
        (self.environment / 'workers').mkdir()
        (self.environment / 'workers/repair.json').write_text(json.dumps(definition))
        bind_definition(self.environment, 'repair', definition)
        launcher = self.launcher('repair', '--model', 'fixture/model', '-L', 'human=-')
        self.execute(launcher)
        config = json.loads(launcher.overlay.read_text())
        self.assertEqual(set(config['types']), {'writer', 'other', 'repair'})
        self.assertEqual(config['types']['repair']['command'][-1], '/opt/asys/environment/.asys-run/repair.json')
        self.assertEqual(json.loads(launcher.builtin_file.read_text()), definition)
        self.assertEqual(launcher.queue.request(launcher.job_id)['args'], ['--model', 'fixture/model'])
        self.assertEqual(launcher.record['worker_kind'], 'goal')
        self.assertEqual(launcher.record['links']['human'], '-')
        self.assertIn('input asys.human.v1.Human human', (launcher.directory / 'environment/component.dcomp').read_text())
        self.assertTrue(human_workers(self.environment, json.loads((self.environment / 'workers.json').read_text())))

    def test_builtin_and_explicit_model_need_no_source_or_settings_changes(self):
        state = self.root / 'state'
        state.mkdir()
        (state / 'config.json').write_text('broken settings')
        original = (self.environment / 'workers.json').read_bytes()
        launcher = self.launcher('simple', '--model', 'fixture/model')
        self.execute(launcher)
        self.assertEqual(launcher.worker_models(), {'simple': 'fixture/model'})
        self.assertEqual((self.environment / 'workers.json').read_bytes(), original)
        self.assertFalse((self.environment / 'workers').exists())
        self.assertEqual((state / 'config.json').read_text(), 'broken settings')

    def test_declared_type_owns_its_name_before_builtin_fallback(self):
        self.assertEqual(selected_definition(self.environment, 'goal', {'goal'}), (None, False))
        definition, builtin = selected_definition(self.environment, 'goal')
        self.assertTrue(builtin)
        self.assertEqual(definition['kind'], 'goal')

    def test_existing_agent_command_model_needs_no_system_default(self):
        self.assertEqual(execution_models(self.root / 'absent', None, 'agent',
            command=['asys-agent', '--agent', 'editor', '--model', 'fixture/explicit']),
            {'simple': 'fixture/explicit'})
        self.assertFalse((self.root / 'absent').exists())

    def test_unknown_worker_and_invalid_parameters_fail_before_provisioning(self):
        launcher = self.launcher('missing')
        with self.assertRaisesRegex(ValueError, 'Unknown worker'):
            launcher.setup()
        self.assertIsNone(launcher.directory)
        params = self.root / 'parameters.json'
        params.write_text('[]')
        launcher = self.launcher('writer', '--parameters', str(params))
        with self.assertRaisesRegex(ValueError, 'JSON object'):
            launcher.setup()
        self.assertIsNone(launcher.directory)

    def test_program_ignores_unrelated_broken_model_defaults(self):
        state = self.root / 'state'
        state.mkdir()
        (state / 'config.json').write_text('broken settings')
        launcher = self.launcher()
        self.execute(launcher)
        self.assertEqual(launcher.worker_models(), {})

    def test_renderer_assets_are_retained_without_world_executables(self):
        view = self.environment / 'worlds/example'
        view.mkdir(parents=True)
        (view / 'view.mjs').write_text('export function mount() {}')
        (view / 'style.css').write_text('body {}')
        (view / 'serve.py').write_text('not a browser resource')
        saved = self.root / 'saved'
        saved.mkdir()
        meta = retain_view(self.environment, 'worlds/example/view.mjs', saved)
        self.assertEqual(meta['view_format'], 'module')
        self.assertEqual(sorted(p.name for p in (saved / 'view').iterdir()), ['style.css', 'view.mjs'])
        (view / 'escape.js').symlink_to(self.root / 'private.js')
        second = self.root / 'second'
        second.mkdir()
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            retain_view(self.environment, 'worlds/example/view.mjs', second)
        with self.assertRaisesRegex(ValueError, 'relative'):
            retain_view(self.environment, '../private.js', second)


if __name__ == '__main__':
    unittest.main()
