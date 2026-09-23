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
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'python'), str(ROOT / 'asys-runtime')]
from asys.goal import Goal, arguments
from asys.runs import Runs


WORKER = '''import json, os
from pathlib import Path
request = json.load(open(os.environ['ASYS_INPUT']))
model = json.load(open(os.environ['ASYS_SYSTEM_MODELS']))['simple']
print(json.dumps({'type': 'goal.phase_started', 'attempt': 1, 'phase': 'implement'}))
json.dump({'final': request['goal'], 'exception': None, 'verified': True,
           'model': model, 'maxAttempts': request.get('maxAttempts')}, open(os.environ['ASYS_RESULT'], 'w'))
'''


class LocalGoal(Goal):
    """Use the actual queue/runtime; replace only Docker and inference execution."""
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
import os, signal, sys
sys.path.insert(0, sys.argv[1])
from asys_runtime.environment import Environment
from asys_runtime.runtime import Runtime
environment = Environment(sys.argv[2], external=sys.argv[3])
assert environment.types['goal']['command'] == ['/opt/asys/asys-workers/tools/asys-goal']
environment.types['goal']['command'] = [sys.executable, '-c', sys.argv[5]]
environment.types['goal']['env']['ASYS_SYSTEM_MODELS'] = sys.argv[6]
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
            components = [{'name': 'environment', 'inputs': [], 'outputs': [], 'image_ref': 'fixture'}]
        else:
            components = [{'name': name, 'status': {'status': 'running', 'health': 'healthy'}} for name in self.owned]
        return json.dumps({'api_version': 2, 'components': components, 'globals': [{'name': 'human_endpoint'}]})


class GoalHostTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / 'state'
        self.state.mkdir()
        self.config = self.state / 'config.json'
        self.config.write_text(json.dumps({'system_models': {'simple': 'fixture/model'}, 'unrelated': 'private'}))
        self.environment = self.root / 'environment'
        self.environment.mkdir()
        (self.environment / 'workers.json').write_text(json.dumps({'version': 1, 'name': 'testing',
            'types': {'program': {'command': ['true']}}}))
        (self.environment / 'component.dcomp').write_text('docker fixture\n')
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {'ASYS_STATE_ROOT': str(self.state)}).start()
        self.human = patch('asys.execution.ensure_human').start()

    def launcher(self, *extra):
        launcher = LocalGoal(arguments([str(self.environment), 'Produce the required output',
            '--workspace', str(self.workspace), '--root', str(self.state), *extra]))
        self.addCleanup(launcher.close)
        return launcher

    def test_host_submits_one_goal_job_with_default_model_snapshot_and_human_access(self):
        launcher = self.launcher('--max-attempts', '4')
        launcher.setup()
        self.assertEqual(launcher.directory.parent, self.state / 'runs')
        self.human.assert_called_once_with(launcher)
        self.config.write_text(json.dumps({'system_models': {'simple': 'other/model'}}))
        with contextlib.redirect_stdout(io.StringIO()) as output:
            launcher.execute()
        result = json.loads(output.getvalue())
        self.assertEqual(result, {'final': 'Produce the required output', 'exception': None, 'verified': True,
                                  'model': 'fixture/model', 'maxAttempts': 4})
        self.assertEqual(len(launcher.queue.list()), 1)
        request = launcher.queue.request(launcher.job_id)
        self.assertEqual(request['type'], 'goal')
        self.assertEqual(request['input'], {'goal': 'Produce the required output', 'maxAttempts': 4})
        self.assertIn(f'{launcher.directory}/system-models.json,/etc/asys/system-models.json,ro', launcher.launch_arguments)
        self.assertIn('human=@human_endpoint', launcher.launch_arguments)
        self.assertIn('input asys.human.v1.Human human', (launcher.directory / 'environment/component.dcomp').read_text())
        self.assertEqual(json.loads((launcher.directory / 'system-models.json').read_text()), {'simple': 'fixture/model'})
        self.assertEqual(Runs(self.state / 'runs').snapshot(launcher.directory)['jobs'][0]['name'], 'goal')
        self.assertIn('Attempt 1: implement', (launcher.directory / 'run.log').read_text())
        self.assertEqual(launcher.record['manager'], 'goal')
        self.assertEqual(launcher.record['agent'], 'simple')
        self.assertEqual(launcher.record['status'], 'completed')
        self.assertTrue(launcher.close())

    def test_explicit_override_works_without_readable_defaults(self):
        self.config.write_text('broken')
        launcher = self.launcher('--model', 'fixture/override')
        launcher.setup()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            launcher.execute()
        self.assertEqual(json.loads(output.getvalue())['model'], 'fixture/override')
        self.assertNotIn('maxAttempts', launcher.queue.request(launcher.job_id)['input'])
        self.assertEqual(self.config.read_text(), 'broken')

    def test_explicit_root_selects_the_goal_model_snapshot(self):
        with patch.dict(os.environ, {'ASYS_STATE_ROOT': str(self.root / 'another-system')}):
            launcher = self.launcher()
            launcher.setup()
        self.assertEqual(launcher.directory.parent, self.state / 'runs')
        self.assertEqual(json.loads((launcher.directory / 'system-models.json').read_text()), {'simple': 'fixture/model'})

    def test_missing_default_fails_before_starting_or_creating_a_run(self):
        self.config.write_text('{"system_models": {"goal": "unused/model"}}')
        result = subprocess.run([sys.executable, str(ROOT / 'asys-goal/tools/asys-goal'),
            str(self.environment), 'Achieve the goal', '--root', str(self.state)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn(f'asys system-model set simple MODEL --root {self.state}', result.stderr)
        self.assertFalse((self.state / 'runs').exists())

    def test_public_arguments_select_a_goal_and_optional_model_and_limit(self):
        with contextlib.redirect_stdout(io.StringIO()) as output, self.assertRaises(SystemExit):
            arguments(['--help'])
        self.assertIn('ENVIRONMENT_DIRECTORY GOAL', output.getvalue())
        self.assertNotIn('--external', output.getvalue())
        self.assertNotIn('AGENT', output.getvalue())
        self.assertIn('unlimited', output.getvalue())
        self.assertIsNone(arguments(['env', 'goal']).max_attempts)
        for argv in [['env', 'goal', '--max-attempts', '0'], ['env', 'goal', '--max-attempts', '1.5'],
                     ['env', ' '], ['env', 'goal', '--model', ' ']]:
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                arguments(argv)


if __name__ == '__main__':
    unittest.main()
