import contextlib
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'python'), str(ROOT / 'asys-runtime')]
from asys.runs import Runs
from asys.senate import Senate, arguments, validate_senate


WORKER = '''import json, os
request = json.load(open(os.environ['ASYS_INPUT']))
models = json.load(open(os.environ['ASYS_SYSTEM_MODELS']))
princeps = request['senate']['princeps']['name']
print(json.dumps({'type': 'senate.phase_started', 'round': 1, 'phase': 'introduce', 'participant': princeps, 'status': 'running'}))
print(json.dumps({'type': 'senate.phase_finished', 'round': 1, 'phase': 'introduce', 'participant': princeps, 'status': 'completed'}))
print(json.dumps({'type': 'senate.finished', 'rounds': 1, 'consensus': True, 'decision': 'consensus', 'status': 'completed'}))
json.dump({'final': request['topic'], 'exception': None, 'consensus': True,
           'rounds': 1, 'decision': 'consensus', 'models': models}, open(os.environ['ASYS_RESULT'], 'w'))
'''


class LocalSenate(Senate):
    """Use the actual queue/runtime; replace Docker and inference execution."""
    child = None

    def command(self, command, **kwargs):
        if command[0] == 'docker':
            return 'sha256:fixture'
        operation = command[1]
        if operation == 'version':
            return json.dumps({'api_version': 2, 'version': '0.3.1'})
        if operation == 'add-component':
            self.launch_arguments = command
            self.child = subprocess.Popen([sys.executable, '-c', '''
import signal, sys
sys.path.insert(0, sys.argv[1])
from asys_runtime.environment import Environment
from asys_runtime.runtime import Runtime
environment = Environment(sys.argv[2], external=sys.argv[3])
assert environment.types['senate']['command'] == ['/opt/asys/asys-workers/tools/asys-senate']
environment.types['senate']['command'] = [sys.executable, '-c', sys.argv[5]]
environment.types['senate']['env']['ASYS_SYSTEM_MODELS'] = sys.argv[6]
with environment.register(sys.argv[4]) as root:
    runtime = Runtime(root, environment.types)
    signal.signal(signal.SIGTERM, lambda *_: runtime.stop())
    runtime.run()
''', str(ROOT / 'asys-runtime'), str(self.args.environment), self.record['external_directory'],
                str(self.directory / 'runtime'), WORKER, str(self.directory / 'system-models.json')],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            descriptor = self.directory / 'runtime/environments/testing/environment.json'
            deadline = time.monotonic() + 5
            while not descriptor.exists():
                if self.child.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError('Test runtime did not start')
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
            components = [{'name': 'environment', 'inputs': [
                {'name': 'inference', 'service': 'cyclo.provider.v1.Provider'}],
                'outputs': [], 'image_ref': 'fixture'}]
        else:
            components = [{'name': name, 'status': {'status': 'running', 'health': 'healthy'}} for name in self.owned]
        return json.dumps({'api_version': 2, 'components': components, 'globals': [{'name': 'inference_endpoint'}]})


class SenateHostTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / 'state'
        self.state.mkdir()
        self.config = self.state / 'config.json'
        self.config.write_text(json.dumps({'system_models': {'simple': 'fixture/default'}, 'unrelated': 'private'}))
        self.environment = self.root / 'environment'
        self.environment.mkdir()
        self.workers = {'version': 1, 'name': 'testing', 'egress': True,
                        'types': {'program': {'command': ['true']}}}
        (self.environment / 'workers.json').write_text(json.dumps(self.workers))
        (self.environment / 'component.dcomp').write_text('docker fixture\n')
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        self.configuration = {'version': 1, 'princeps': {'name': 'Cicero', 'prompt': 'Seek a clear agreement.'},
                       'senators': [{'name': 'Cato', 'agent': 'researcher', 'model': 'fixture/research'},
                                    {'name': 'Seneca', 'prompt': 'Consider long-term effects.'}]}
        self.senate = self.root / 'senate.json'
        self.senate.write_text(json.dumps(self.configuration))
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {'ASYS_STATE_ROOT': str(self.state)}).start()
        self.human = patch('asys.execution.ensure_human').start()

    def launcher(self, *extra):
        launcher = LocalSenate(arguments([str(self.environment), 'Choose the next experiment',
            '--senate', str(self.senate), '--workspace', str(self.workspace),
            '--root', str(self.state), *extra]))
        self.addCleanup(launcher.close)
        return launcher

    def test_one_senate_job_snapshots_configuration_and_model_and_uses_environment_provider(self):
        launcher = self.launcher()
        launcher.setup()
        self.assertEqual(launcher.directory.parent, self.state / 'runs')
        self.human.assert_not_called()
        self.senate.write_text('changed after setup')
        self.config.write_text('changed after setup')
        with contextlib.redirect_stdout(io.StringIO()) as output:
            launcher.execute()
        self.assertEqual(json.loads(output.getvalue()), {
            'final': 'Choose the next experiment', 'exception': None, 'consensus': True,
            'rounds': 1, 'decision': 'consensus', 'models': {'simple': 'fixture/default'}})
        self.assertEqual(len(launcher.queue.list()), 1)
        request = launcher.queue.request(launcher.job_id)
        self.assertEqual(request['type'], 'senate')
        self.assertEqual(request['input'], {'topic': 'Choose the next experiment', 'senate': self.configuration})
        self.assertEqual(json.loads((launcher.directory / 'senate.json').read_text()), self.configuration)
        self.assertEqual(json.loads((launcher.directory / 'run.json').read_text())['senate'], self.configuration)
        self.assertEqual(json.loads((launcher.directory / 'system-models.json').read_text()), {'simple': 'fixture/default'})
        self.assertIn('inference=@inference_endpoint', launcher.launch_arguments)
        self.assertIn('--egress', launcher.launch_arguments)
        self.assertNotIn('human=@human_endpoint', launcher.launch_arguments)
        self.assertEqual(Runs(self.state / 'runs').snapshot(launcher.directory)['jobs'][0]['name'], 'senate')
        progress = (launcher.directory / 'run.log').read_text()
        self.assertIn('Round 1: Cicero introduce completed', progress)
        self.assertIn('Senate completed after 1 round(s): consensus', progress)
        self.assertEqual(launcher.record['manager'], 'senate')
        self.assertEqual(launcher.record['status'], 'completed')
        self.assertTrue(launcher.close())

    def test_override_works_without_readable_defaults_and_preserves_participant_models(self):
        self.config.write_text('broken')
        launcher = self.launcher('--model', 'fixture/override')
        launcher.setup()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            launcher.execute()
        self.assertEqual(json.loads(output.getvalue())['models'], {'simple': 'fixture/override'})
        self.assertEqual(launcher.queue.request(launcher.job_id)['input']['senate'], self.configuration)
        self.assertEqual(self.config.read_text(), 'broken')

    def test_explicit_root_selects_models_and_runs_for_launcher_and_observer(self):
        ambient = self.root / 'another-system'
        ambient.mkdir()
        (ambient / 'config.json').write_text(json.dumps({'system_models': {'simple': 'fixture/ambient'}}))
        with patch.dict(os.environ, {'ASYS_STATE_ROOT': str(ambient)}):
            launcher = self.launcher()
            launcher.setup()
            with contextlib.redirect_stdout(io.StringIO()) as output:
                launcher.execute()
            observed = subprocess.run([sys.executable, str(ROOT / 'tools/asys'),
                'status', 'latest', '--root', str(self.state), '--json'],
                check=True, capture_output=True, text=True)
            logs = subprocess.run([sys.executable, str(ROOT / 'tools/asys'),
                'logs', 'latest', '--root', str(self.state)],
                check=True, capture_output=True, text=True)
        self.assertEqual(launcher.directory.parent, self.state / 'runs')
        self.assertEqual(json.loads(output.getvalue())['models'], {'simple': 'fixture/default'})
        record = json.loads(observed.stdout)
        self.assertEqual(record['id'], launcher.id)
        self.assertEqual(record['jobs'][0]['name'], 'senate')
        self.assertEqual(record['jobs'][0]['directory'], str(launcher.directory / 'jobs' / launcher.job_id))
        self.assertIn('Senate completed after 1 round(s): consensus', logs.stdout)
        self.assertFalse((ambient / 'runs').exists())

    def test_all_explicit_models_need_no_default_and_network_policy_is_preserved(self):
        for index, participant in enumerate([self.configuration['princeps'], *self.configuration['senators']]):
            participant['model'] = f'fixture/participant-{index}'
        self.senate.write_text(json.dumps(self.configuration))
        self.config.write_text('broken')
        self.workers['egress'] = False
        (self.environment / 'workers.json').write_text(json.dumps(self.workers))
        launcher = self.launcher()
        launcher.setup()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            launcher.execute()
        self.assertEqual(json.loads(output.getvalue())['models'], {})
        self.assertIsNone(launcher.record['model'])
        self.assertNotIn('--egress', launcher.launch_arguments)
        self.assertFalse(json.loads((launcher.directory / 'external/workers.json').read_text())['egress'])

    def test_missing_fallback_fails_before_creating_a_run(self):
        self.config.write_text('{"system_models": {}}')
        launcher = self.launcher()
        with self.assertRaises(ValueError) as error:
            launcher.setup()
        self.assertIn(f'asys system-model set simple MODEL --root {self.state}', str(error.exception))
        self.assertIsNone(launcher.directory)
        self.assertFalse((self.state / 'runs').exists())

    def test_invalid_config_fails_before_creating_a_run(self):
        variants = [None, {}, {**self.configuration, 'version': True}, {**self.configuration, 'senators': []},
                    {**self.configuration, 'endpoint': 'https://example.test'},
                    {**self.configuration, 'princeps': {'name': ' Cato '}},
                    {**self.configuration, 'princeps': {'name': 'Cicero', 'model': None}},
                    {**self.configuration, 'princeps': {'name': 'Cicero', 'agent': '../escape'}},
                    {**self.configuration, 'princeps': {'name': 'Cicero', 'prompt': None}},
                    {**self.configuration, 'princeps': {'name': 'Cicero', 'apiKey': 'unsupported'}}]
        for config in variants:
            with self.subTest(config=config):
                self.senate.write_text(json.dumps(config))
                launcher = self.launcher()
                with self.assertRaises(ValueError):
                    launcher.setup()
                self.assertIsNone(launcher.directory)
                self.assertFalse((self.state / 'runs').exists())
        self.senate.write_text('{invalid JSON')
        with self.assertRaises(ValueError):
            self.launcher().setup()
        self.assertFalse((self.state / 'runs').exists())

    def test_nul_is_rejected_in_all_participant_strings(self):
        for field in ('name', 'prompt', 'agent', 'model'):
            with self.subTest(field=field):
                configuration = copy.deepcopy(self.configuration)
                configuration['princeps'][field] = 'value\0suffix'
                with self.assertRaises(ValueError):
                    validate_senate(configuration)

    def test_public_arguments_require_a_topic_and_configuration_without_round_override(self):
        with contextlib.redirect_stdout(io.StringIO()) as output, self.assertRaises(SystemExit):
            arguments(['--help'])
        self.assertIn('ENVIRONMENT_DIRECTORY TOPIC', output.getvalue())
        self.assertIn('--senate FILE', output.getvalue())
        self.assertNotIn('--external', output.getvalue())
        self.assertNotIn('--max-attempts', output.getvalue())
        for argv in [['env', 'topic'], ['env', ' ', '--senate', 'senate.json'],
                     ['env', 'topic', '--senate', 'senate.json', '--model', ' '],
                     ['env', 'topic', '--senate', 'senate.json', '--max-attempts', '4']]:
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                arguments(argv)


if __name__ == '__main__':
    unittest.main()
