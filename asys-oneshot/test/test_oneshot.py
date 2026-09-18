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
from asys.execution import prepare_run
from asys.lifecycle import LaunchError
from asys.oneshot import OneShot, arguments
from asys.runs import Runs
from asys.system_agents import agent_prompt
from asys_runtime.environment import Environment
from asys_runtime.queue import Queue


WORKER = r'''import json, os, pathlib, sys
if '--agent' in sys.argv:
    assert sys.argv[sys.argv.index('--agent') + 1] == 'simple'
    assert sys.argv[sys.argv.index('--model') + 1] == 'fixture/model'
    agent = pathlib.Path(os.environ['ASYS_WORKERS_DIR']) / 'agents/simple'
    assert 'You are the simple system agent supplied by asys.' in (agent / 'prompt.md').read_text()
    assert not (agent / 'memory.md').exists()
assert (pathlib.Path(os.environ['ASYS_ENVIRONMENT_DIR']) / 'shared.txt').read_text() == 'environment resource'
request = json.load(sys.stdin)
p = pathlib.Path('source.txt')
p.write_text(p.read_text() + ':edited')
print('worker log')
if request['prompt'] == 'fail':
    json.dump({'final': 'Stopped after saving partial work.', 'exception': 'Missing specification'}, open(os.environ['ASYS_RESULT'], 'w'))
    sys.exit(1)
json.dump({'final': request['prompt'], 'exception': None}, open(os.environ['ASYS_RESULT'], 'w'))
'''


class LocalEnvironment(OneShot):
    """Exercise the host and runtime with a local transport and fixture agent."""
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
import signal, sys
sys.path.insert(0, sys.argv[1])
from asys_runtime.environment import Environment
from asys_runtime.runtime import Runtime
environment = Environment(sys.argv[2], external=sys.argv[3])
command = environment.types['agent']['command']
if command[0] == '/opt/asys/asys-workers/tools/asys-agent':
    environment.types['agent']['command'] = [sys.executable, '-c', sys.argv[5], *command[1:]]
with environment.register(sys.argv[4]) as root:
    runtime = Runtime(root, environment.types)
    signal.signal(signal.SIGTERM, lambda *_: runtime.stop())
    runtime.run()
''', str(ROOT / 'asys-runtime'), str(self.args.environment), self.record['external_directory'],
                str(self.directory / 'runtime'), WORKER],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            descriptor = self.directory / 'runtime/environments' / self.record['environment'] / 'environment.json'
            deadline = time.monotonic() + 5
            while not descriptor.exists():
                if self.child.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError('Local runtime did not start')
                time.sleep(.02)
            return ''
        if operation == 'rm-component':
            self.child.terminate()
            self.child.communicate(timeout=5)
            return ''
        if operation == 'logs':
            return ''
        assert operation == 'view', command
        if command[-1].endswith('preview.dcomp'):
            components = [{'name': 'environment', 'inputs': [], 'outputs': [], 'image_ref': 'fixture'}]
        else:
            components = [{'name': name, 'status': {'status': 'running', 'health': 'healthy'}} for name in self.owned]
        return json.dumps({'api_version': 2, 'components': components, 'globals': []})


class OneShotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'
        state_environment = patch.dict(os.environ, {'ASYS_STATE_ROOT': str(self.state)})
        state_environment.start()
        self.addCleanup(state_environment.stop)
        self.environment = self.root / 'environment'
        self.environment.mkdir()
        self.workspace = self.root / 'actual repository'
        self.workspace.mkdir()
        self.workspace.joinpath('source.txt').write_text('original')
        self.environment.joinpath('component.dcomp').write_text('docker fixture\n')
        self.environment.joinpath('shared.txt').write_text('environment resource')
        self.environment.joinpath('workers.json').write_text(json.dumps({'version': 1, 'name': 'testing',
            'egress': True, 'types': {'writer': {'command': ['false']}}}))
        self.environment.joinpath('agents/simple').mkdir(parents=True)
        self.environment.joinpath('agents/simple/prompt.md').write_text('Do not use this environment agent')
        self.environment.joinpath('agents/simple/memory.md').write_text('Do not inherit this memory')

    def launcher(self, prompt='Finish the change.', model='fixture/model'):
        argv = [str(self.environment), '--workspace', str(self.workspace), prompt,
                '--root', str(self.root / 'runs')]
        if model is not None:
            argv += ['--model', model]
        args = arguments(argv)
        launcher = LocalEnvironment(args)
        self.addCleanup(launcher.close)
        return launcher

    def test_one_job_edits_the_real_workspace_and_is_visible_to_asys(self):
        launcher = self.launcher()
        launcher.setup()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            launcher.execute()
        self.assertEqual(json.loads(output.getvalue())['final'], 'Finish the change.')
        self.assertEqual(self.workspace.joinpath('source.txt').read_text(), 'original:edited')
        self.assertEqual(sorted(p.name for p in self.workspace.iterdir()), ['source.txt'])
        job = launcher.directory / 'jobs' / launcher.job_id
        self.assertTrue(job.joinpath('stdout.log').is_file())
        self.assertFalse(job.joinpath('attempts').exists())
        self.assertIn(f'{self.workspace},/var/lib/asys/workspace,rw', launcher.launch_arguments)
        self.assertEqual(launcher.launch_arguments[launcher.launch_arguments.index('--user') + 1],
                         f'{os.getuid()}:{os.getgid()}')
        self.assertEqual((launcher.directory / 'runtime').stat().st_mode & 0o777, 0o700)
        self.assertEqual(launcher.queue.request(launcher.job_id)['workspace'], '../../../workspace')
        self.assertEqual(launcher.queue.request(launcher.job_id)['type'], 'agent')
        self.assertEqual(launcher.record['agent'], 'simple')
        self.assertEqual(launcher.record['manager'], 'oneshot')
        self.assertEqual(launcher.record['model'], 'fixture/model')
        self.assertIn('--egress', launcher.launch_arguments)
        external = launcher.directory / 'external'
        self.assertEqual(launcher.record['external_directory'], str(external))
        self.assertIn(f'{external},/opt/asys/environment/external,ro', launcher.launch_arguments)
        self.assertEqual(external.joinpath('agents/simple/prompt.md').read_text(), agent_prompt('simple'))
        self.assertFalse(external.joinpath('agents/simple/memory.md').exists())
        observed = Runs(self.root / 'runs').snapshot(launcher.directory)
        self.assertEqual(observed['status'], 'completed')
        self.assertEqual(observed['jobs'][0]['workspace'], str(self.workspace))
        self.assertEqual(observed['jobs'][0]['directory'], str(job))
        self.assertEqual(observed['jobs'][0]['name'], 'simple')
        events = [json.loads(line) for line in launcher.directory.joinpath('events.jsonl').read_text().splitlines()]
        self.assertEqual([event['type'] for event in events], ['job.created', 'job.completed'])
        self.assertEqual({event['data']['jobId'] for event in events}, {launcher.job_id})
        self.assertEqual(events[0]['data']['agent'], 'simple')
        self.assertTrue(launcher.close())
        self.assertEqual(launcher.child.returncode, 0)

    def test_public_interface_uses_prompt_and_model_and_hides_the_internal_override(self):
        args = arguments(['env', 'Complete this assignment', '--model', 'provider/model'])
        self.assertEqual(args.prompt, 'Complete this assignment')
        self.assertEqual(args.model, 'provider/model')
        self.assertFalse(hasattr(args, 'agent'))
        with contextlib.redirect_stdout(io.StringIO()) as output, self.assertRaises(SystemExit) as exited:
            arguments(['--help'])
        self.assertEqual(exited.exception.code, 0)
        self.assertIn('ENVIRONMENT_DIRECTORY PROMPT', output.getvalue())
        self.assertNotIn('AGENT', output.getvalue())
        self.assertNotIn('--external', output.getvalue())
        self.assertIsNone(arguments(['env', 'prompt']).model)
        for argv in [['env', 'writer', 'old prompt', '--model', 'fixture/model'],
                     ['env', 'prompt', '--model', ' ']]:
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                arguments(argv)

    def set_model(self, model):
        subprocess.run([sys.executable, str(ROOT / 'tools/asys'), 'system-model', 'set', 'simple', model],
                       check=True, capture_output=True, text=True)

    def test_default_model_is_selected_once_and_saved_with_the_run(self):
        self.set_model('fixture/model')
        launcher = self.launcher(model=None)
        launcher.setup()
        self.set_model('account/next-run')
        with contextlib.redirect_stdout(io.StringIO()) as output:
            launcher.execute()
        self.assertEqual(json.loads(output.getvalue())['final'], 'Finish the change.')
        self.assertEqual(json.loads((launcher.directory / 'run.json').read_text())['model'], 'fixture/model')
        config = json.loads((launcher.directory / 'external/workers.json').read_text())
        command = config['types']['agent']['command']
        self.assertEqual(command[command.index('--model') + 1], 'fixture/model')

    def test_explicit_model_overrides_configuration_without_changing_it(self):
        self.set_model('account/default')
        config = self.state / 'config.json'
        before = config.read_bytes()
        launcher = self.launcher()
        launcher.setup()
        with contextlib.redirect_stdout(io.StringIO()):
            launcher.execute()
        self.assertEqual(launcher.record['model'], 'fixture/model')
        self.assertEqual(config.read_bytes(), before)

    def test_explicit_model_does_not_require_readable_default_settings(self):
        self.state.mkdir()
        (self.state / 'config.json').write_text('invalid JSON')
        launcher = self.launcher()
        launcher.setup()
        self.assertEqual(launcher.record['model'], 'fixture/model')

    def test_missing_model_explains_setup_before_creating_a_run(self):
        result = subprocess.run([sys.executable, str(ROOT / 'asys-oneshot/tools/asys-oneshot'),
            str(self.root / 'absent-environment'), 'Complete the work', '--root', str(self.root / 'runs')],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('asys system-model set simple MODEL', result.stderr)
        self.assertIn('asys-inference models', result.stderr)
        self.assertIn('--model MODEL', result.stderr)
        self.assertIn(str(self.state / 'config.json'), result.stderr)
        self.assertFalse((self.root / 'runs').exists())
        self.assertFalse(self.state.exists())

    def test_internal_override_runs_with_a_readonly_bundle_mount(self):
        external = self.root / 'portable agents'
        external.mkdir()
        config = {'version': 1, 'name': 'portable', 'egress': True,
                  'types': {'agent': {'command': [sys.executable, '-c', WORKER]}}}
        (external / 'workers.json').write_text(json.dumps(config))
        args = arguments([str(self.environment), 'Use the internal worker.', '--model', 'fixture/model',
            '--external', str(external), '--workspace', str(self.workspace), '--root', str(self.root / 'runs')])
        launcher = LocalEnvironment(args)
        self.addCleanup(launcher.close)
        launcher.setup()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            launcher.execute()
        self.assertEqual(json.loads(output.getvalue())['final'], 'Use the internal worker.')
        self.assertEqual(launcher.record['environment'], 'portable')
        self.assertEqual(launcher.record['environment_directory'], str(self.environment))
        self.assertEqual(launcher.record['external_directory'], str(external))
        self.assertEqual(launcher.definition, Environment(external).descriptor['definition'])
        self.assertIn(f'{external},/opt/asys/environment/external,ro', launcher.launch_arguments)
        self.assertIn('--arg=--external', launcher.launch_arguments)
        self.assertIn('--arg=/opt/asys/environment/external', launcher.launch_arguments)
        self.assertIn('--egress', launcher.launch_arguments)
        self.assertEqual(self.workspace.joinpath('source.txt').read_text(), 'original:edited')
        self.assertEqual(json.loads((self.environment / 'workers.json').read_text())['name'], 'testing')
        self.assertEqual(json.loads((launcher.directory / 'run.json').read_text())['external_directory'], str(external))

    def test_external_bundle_does_not_inherit_an_environment_agent(self):
        external = self.root / 'portable'
        external.mkdir()
        (external / 'workers.json').write_text(json.dumps({'version': 1, 'name': 'portable',
            'types': {'other': {'command': ['true']}}}))
        launcher = self.launcher()
        launcher.args.external = external
        with self.assertRaisesRegex(LaunchError, "must define the 'agent' job type"):
            launcher.setup()
        self.assertIsNone(launcher.directory)

    def test_failed_job_is_retained_when_a_new_invocation_uses_the_same_workspace(self):
        failed = self.launcher('fail')
        failed.setup()
        with self.assertRaisesRegex(LaunchError, 'Missing specification'):
            failed.execute()
        failed.close()
        original = failed.queue.state(failed.job_id)
        self.assertEqual(original['status'], 'failed')
        self.assertEqual(json.loads((failed.directory / 'result.json').read_text())['exception'], 'Missing specification')
        next_run = self.launcher()
        next_run.setup()
        with contextlib.redirect_stdout(io.StringIO()):
            next_run.execute()
        self.assertNotEqual(failed.job_id, next_run.job_id)
        self.assertNotEqual(failed.directory, next_run.directory)
        self.assertEqual(failed.queue.state(failed.job_id), original)
        self.assertEqual(self.workspace.joinpath('source.txt').read_text(), 'original:edited:edited')

    def test_directory_references_work_on_each_side_of_the_mount(self):
        _, run, _ = prepare_run(self.root / 'runs', self.workspace)
        job = run / 'jobs/one'
        job.mkdir()
        queue = Queue(run / 'runtime/environments/test')
        queue.submit('writer', 'one', directory=job, workspace=run / 'workspace')
        request = queue.request('one')
        self.assertEqual(request['workspace'], '../../../workspace')
        self.assertEqual(queue.paths('one')['workspace'], self.workspace)
        container_queue = Path('/var/lib/asys/runtime/environments/test')
        self.assertEqual((container_queue / request['workspace']).resolve(), Path('/var/lib/asys/workspace'))


if __name__ == '__main__':
    unittest.main()
