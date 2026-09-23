"""Real local member processes stay supervised by their one outer swarm job."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from asys_runtime.environment import Environment
from asys_runtime.files import read_json, write_json
from asys_swarm.executor import Executor, TERMINAL
from asys_swarm.__main__ import runtime_root


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.environment = self.base / 'env'
        self.workspace = self.base / 'workspace'
        self.environment.mkdir()
        self.workspace.mkdir()
        self.executors = []

    def tearDown(self):
        for executor in self.executors:
            executor.close(interrupted=True)
        self.temporary.cleanup()

    def executor(self, code, *, timeout=None, env=None):
        program = self.environment / 'member.py'
        program.write_text(code)
        spec = {'command': [sys.executable, str(program)], 'env': env or {}}
        if timeout is not None:
            spec['timeout'] = timeout
        write_json(self.environment / 'workers.json', {'version': 1, 'name': 'test', 'types': {'member': spec}})
        executor = Executor(self.base / 'job/swarm/decisions', Environment(self.environment), self.base / 'runtime')
        self.executors.append(executor)
        return executor

    def submit(self, executor, identity='decision-one', *, seconds=5):
        path = executor.path(identity)
        path.mkdir(parents=True, exist_ok=True)
        return executor.submit('member', identity, directory=path, workspace=self.workspace,
                               input={'timeoutSeconds': seconds}, metadata={'agent_id': 'agent-001', 'turn': 7})

    def wait(self, executor, identity='decision-one', *, timeout=6):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = executor.state(identity)
            if state['status'] in TERMINAL:
                return state
            time.sleep(0.02)
        self.fail('Local decision did not reach terminal state')

    def wait_file(self, path):
        deadline = time.monotonic() + 5
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(path.exists())

    def assert_stopped(self, pids):
        deadline = time.monotonic() + 2
        while any(Path(f'/proc/{pid}').exists() for pid in pids) and time.monotonic() < deadline:
            time.sleep(0.01)
        for pid in pids:
            self.assertFalse(Path(f'/proc/{pid}').exists(), f'Descendant {pid} survived decision cleanup')

    def test_member_receives_standard_fields_env_and_outer_process_group(self):
        code = '''import json,os
from pathlib import Path
value={key:os.environ[key] for key in ("ASYS_JOB_ID","ASYS_JOB_TYPE","ASYS_JOB_DIR","ASYS_INPUT","ASYS_RESULT","ASYS_REQUEST","ASYS_WORKSPACE","ASYS_ENVIRONMENT_DIR","ASYS_WORKERS_DIR","ASYS_RUNTIME_ROOT","MEMBER_SETTING")}
value["group"]=os.getpgrp()
Path(os.environ["ASYS_RESULT"]).write_text(json.dumps({"actions":[],"memory":value}))
print("member output")
'''
        executor = self.executor(code, env={'MEMBER_SETTING': 'configured'})
        self.submit(executor)
        state = self.wait(executor)
        self.assertEqual(state['status'], 'done', state)
        memory = state['result']['memory']
        self.assertEqual(memory['group'], os.getpgrp())
        self.assertEqual(memory['ASYS_JOB_TYPE'], 'member')
        self.assertEqual(memory['ASYS_JOB_ID'], 'decision-one')
        self.assertEqual(memory['ASYS_JOB_DIR'], str(executor.path('decision-one')))
        self.assertEqual(memory['ASYS_RUNTIME_ROOT'], str(self.base / 'runtime'))
        self.assertEqual(memory['MEMBER_SETTING'], 'configured')
        self.assertEqual((state['agent'], state['turn']), ('agent-001', 7))
        self.assertIn('member output', (executor.path('decision-one') / 'stdout.log').read_text())
        self.assertFalse((self.base / 'runtime/environments/test/jobs').exists())

    CHILDREN = '''import json,os,subprocess,sys,time
from pathlib import Path
child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"])
Path("pids.json").write_text(json.dumps([os.getpid(),child.pid]))
time.sleep(30)
'''

    def test_cancel_cleans_command_and_grandchild(self):
        executor = self.executor(self.CHILDREN)
        self.submit(executor)
        self.wait_file(self.workspace / 'pids.json')
        pids = read_json(self.workspace / 'pids.json')
        executor.cancel('decision-one')
        self.assertEqual(self.wait(executor)['status'], 'cancelled')
        self.assert_stopped(pids)

    def test_member_type_timeout_is_honored_and_cleans_descendants(self):
        executor = self.executor(self.CHILDREN, timeout=0.2)
        self.submit(executor, seconds=5)
        self.wait_file(self.workspace / 'pids.json')
        pids = read_json(self.workspace / 'pids.json')
        state = self.wait(executor)
        self.assertEqual(state['status'], 'failed')
        self.assertIn('timeout', state['error'])
        self.assert_stopped(pids)

    def test_parent_interruption_preserves_interrupted_attempt_for_retry(self):
        executor = self.executor(self.CHILDREN)
        self.submit(executor)
        self.wait_file(self.workspace / 'pids.json')
        pids = read_json(self.workspace / 'pids.json')
        executor.close(interrupted=True)
        recovered = Executor(executor.directory, executor.environment, executor.runtime_root)
        self.executors.append(recovered)
        self.assertEqual(recovered.state('decision-one')['status'], 'interrupted')
        self.assert_stopped(pids)

    def test_completed_result_survives_recovery_and_submit_is_idempotent(self):
        executor = self.executor('''import os,json
from pathlib import Path
with Path("calls").open("a") as stream: stream.write("called\\n")
Path(os.environ["ASYS_RESULT"]).write_text(json.dumps({"actions":[],"memory":"retained"}))
''')
        self.submit(executor)
        original = self.wait(executor)
        executor.close(interrupted=True)
        recovered = Executor(executor.directory, executor.environment, executor.runtime_root)
        self.executors.append(recovered)
        self.assertEqual(self.submit(recovered)['result'], original['result'])
        self.assertEqual((self.workspace / 'calls').read_text().splitlines(), ['called'])

    def test_crash_between_request_and_initial_state_recovers_as_interrupted(self):
        executor = self.executor('raise RuntimeError("must not execute incomplete submission")')
        from asys_swarm import executor as module
        original = module.write_json
        def crash(path, value):
            if Path(path).name == 'input.json':
                raise OSError('simulated submission crash')
            return original(path, value)
        with patch.object(module, 'write_json', side_effect=crash):
            with self.assertRaisesRegex(OSError, 'submission crash'):
                self.submit(executor)
        self.assertTrue((executor.path('decision-one') / 'request.json').exists())
        self.assertFalse((executor.path('decision-one') / 'state.json').exists())
        recovered = Executor(executor.directory, executor.environment, executor.runtime_root)
        self.executors.append(recovered)
        state = self.submit(recovered)
        self.assertEqual(state['status'], 'interrupted')
        self.assertEqual(recovered.processes, {})
        self.assertEqual(read_json(executor.path('decision-one') / 'input.json')['timeoutSeconds'], 5)

    def test_successful_command_cannot_leave_background_descendants(self):
        executor = self.executor('''import json,os,subprocess,sys
from pathlib import Path
child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"])
Path("pids.json").write_text(json.dumps([child.pid]))
Path(os.environ["ASYS_RESULT"]).write_text(json.dumps({"actions":[],"memory":None}))
''')
        self.submit(executor)
        self.wait_file(self.workspace / 'pids.json')
        self.assertEqual(self.wait(executor)['status'], 'done')
        self.assert_stopped(read_json(self.workspace / 'pids.json'))

    def test_runtime_root_derives_from_outer_request_and_allows_explicit_override(self):
        root = self.base / 'runtime'
        request = root / 'environments/test/jobs/outer/request.json'
        self.assertEqual(runtime_root({'ASYS_REQUEST': str(request)}), root)
        self.assertEqual(runtime_root({'ASYS_RUNTIME_ROOT': str(root)}), root)
        with self.assertRaises(ValueError):
            runtime_root({'ASYS_REQUEST': '/tmp/unrelated/request.json'})

    def test_member_failure_preserves_structured_exception(self):
        executor = self.executor('''import json,os,sys
from pathlib import Path
Path(os.environ["ASYS_RESULT"]).write_text(json.dumps({"final":"", "exception":"meaningful member failure"}))
sys.exit(3)
''')
        self.submit(executor)
        state = self.wait(executor)
        self.assertEqual(state['status'], 'failed')
        self.assertEqual(state['exit_code'], 3)
        self.assertEqual(state['error'], 'meaningful member failure')


if __name__ == '__main__':
    unittest.main()
