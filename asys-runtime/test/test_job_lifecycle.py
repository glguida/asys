import json
from pathlib import Path
import sys
import tempfile
import unittest

from asys_runtime.config import validate_types
from asys_runtime.queue import Queue
from asys_runtime.runtime import Runtime


class JobLifecycleTests(unittest.TestCase):
    def test_each_execution_has_its_own_job_and_the_same_real_workspace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / 'repository'
            workspace.mkdir()
            queue = Queue(root / 'queue')
            command = [sys.executable, '-c', r'''import json, os, pathlib
assert 'ASYS_ATTEMPT' not in os.environ
p = pathlib.Path('work.txt')
p.write_text(p.read_text() + 'again\n' if p.exists() else 'first\n')
print('ran', os.environ['ASYS_JOB_ID'])
json.dump({'final': p.read_text(), 'exception': None}, open(os.environ['ASYS_RESULT'], 'w'))
''']
            for job_id in ('first', 'second'):
                directory = root / job_id
                directory.mkdir()
                queue.submit('program', job_id, directory=directory, workspace=workspace)
                Runtime(queue.root, {'program': {'command': command}}).run(once=True)
                state = queue.state(job_id)
                self.assertEqual(state['status'], 'done', state)
                self.assertNotIn('attempt', state)
                self.assertTrue((directory / 'stdout.log').is_file())
                self.assertTrue((directory / 'result.json').is_file())
                self.assertFalse((directory / 'attempts').exists())
            self.assertEqual(workspace.joinpath('work.txt').read_text(), 'first\nagain\n')
            self.assertEqual(json.loads(root.joinpath('first/result.json').read_text())['final'], 'first\n')
            self.assertEqual(sorted(p.name for p in workspace.iterdir()), ['work.txt'])

    def test_runtime_does_not_restart_a_job_whose_runner_disappeared(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory, workspace = root / 'job', root / 'repository'
            directory.mkdir()
            workspace.mkdir()
            queue = Queue(root / 'queue')
            state = queue.submit('program', 'one', directory=directory, workspace=workspace)
            state['status'] = 'running'
            queue.save(state)
            Runtime(queue.root, {'program': {'command': [sys.executable, '-c', 'raise AssertionError("must not execute")']}}).run(once=True)
            self.assertEqual(queue.state('one')['status'], 'interrupted')
            self.assertFalse((directory / 'stdout.log').exists())

    def test_environment_cannot_request_automatic_job_reexecution(self):
        with self.assertRaises(ValueError):
            validate_types({'program': {'command': ['true'], 'resume': True}})


if __name__ == '__main__':
    unittest.main()
