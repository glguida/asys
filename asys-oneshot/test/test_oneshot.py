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

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'python'), str(ROOT / 'asys-runtime')]
from asys.execution import prepare_run
from asys.lifecycle import LaunchError
from asys.oneshot import OneShot, arguments
from asys.runs import Runs
from asys_runtime.queue import Queue


class LocalEnvironment(OneShot):
    """Exercise the real queue/runtime; replace only the container transport."""
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
            self.child = subprocess.Popen([sys.executable, str(ROOT / 'asys-runtime/tools/asys-runtime'),
                'run', str(self.args.environment), '--root', str(self.directory / 'runtime')],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            descriptor = self.directory / 'runtime/environments/testing/environment.json'
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
        self.environment = self.root / 'environment'
        self.environment.mkdir()
        self.workspace = self.root / 'actual repository'
        self.workspace.mkdir()
        self.workspace.joinpath('source.txt').write_text('original')
        self.environment.joinpath('component.dcomp').write_text('docker fixture\n')
        command = [sys.executable, '-c', r'''import json, os, pathlib, sys
request = json.load(sys.stdin)
p = pathlib.Path('source.txt')
p.write_text(p.read_text() + ':edited')
print('worker log')
if request['prompt'] == 'fail':
    json.dump({'final': 'Stopped after saving partial work.', 'exception': 'Missing specification'}, open(os.environ['ASYS_RESULT'], 'w'))
    sys.exit(1)
json.dump({'final': request['prompt'], 'exception': None}, open(os.environ['ASYS_RESULT'], 'w'))
''']
        self.environment.joinpath('workers.json').write_text(json.dumps({'version': 1, 'name': 'testing', 'types': {'writer': {'command': command}}}))

    def launcher(self, prompt='Finish the change.'):
        args = arguments([str(self.environment), '--workspace', str(self.workspace), 'writer', prompt, '--root', str(self.root / 'runs')])
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
        observed = Runs(self.root / 'runs').snapshot(launcher.directory)
        self.assertEqual(observed['status'], 'completed')
        self.assertEqual(observed['jobs'][0]['workspace'], str(self.workspace))
        self.assertEqual(observed['jobs'][0]['directory'], str(job))
        events = [json.loads(line) for line in launcher.directory.joinpath('events.jsonl').read_text().splitlines()]
        self.assertEqual([event['type'] for event in events], ['job.created', 'job.completed'])
        self.assertEqual({event['data']['jobId'] for event in events}, {launcher.job_id})
        self.assertTrue(launcher.close())
        self.assertEqual(launcher.child.returncode, 0)

    def test_unknown_agent_is_rejected_before_creating_a_run(self):
        launcher = self.launcher()
        launcher.args.agent = 'absent'
        with self.assertRaisesRegex(Exception, 'no agent entry'):
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
