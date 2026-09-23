"""Exercise authoritative turns and recovery over the real filesystem runtime."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from asys_runtime.channel import Reader, Writer, direction_root
from asys_runtime.environment import Environment, describe
from asys_runtime.files import read_json, write_json
from asys_runtime.queue import Queue as RuntimeQueue
from asys_swarm.config import load_config, validate_config
from asys_swarm.engine import Engine
from asys_swarm.__main__ import main as worker_main
from asys_swarm.limits import PUBLICATION_CHECKPOINT_BYTES, WORLD_BYTES
from asys_swarm.replay import replay
from asys_swarm.world_service import Service
from asys_swarm.executor import Executor
from asys_swarm.config import digest

ROOT = Path(__file__).resolve().parents[2]
WORLD = '''
def action_schema():
    return {"type":"object","properties":{"add":{"type":"integer","minimum":0,"maximum":2}},"required":["add"],"additionalProperties":False}
def initialize(settings, agents, seed):
    return {"total":0,"turn":0,"stopAgents":settings.get("stopAgents",False)}
def observe(state, agent):
    active = not (state["stopAgents"] and state["turn"] >= 1)
    state["total"] = 999  # The controller must isolate accidental mutation.
    return {"active":active,"agent":agent}
def step(state, actions):
    state["total"] += sum(action["add"] for action in actions.values())
    state["turn"] += 1
    return {"state":state,"events":[{"type":"added","count":len(actions)}]}
def evaluate(state, objective):
    return {"achieved":state["total"] >= objective.get("target",999),"metrics":{"total":state["total"]},"summary":str(state["total"])}
def artifacts(state):
    return {"built":state["total"]}
'''


class ManualExecutor:
    """Deterministic decision fixture; process lifecycle has its own real tests."""
    def __init__(self, directory, environment, root):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
    def list(self):
        return [read_json(path / 'state.json') for path in sorted(self.directory.iterdir()) if (path / 'state.json').exists()]
    def state(self, identity):
        return read_json(self.directory / identity / 'state.json')
    def save(self, state):
        write_json(self.directory / state['id'] / 'state.json', state)
    def submit(self, kind, identity, *, directory, workspace, input, metadata):
        if (Path(directory) / 'state.json').exists():
            return self.state(identity)
        state = {'id': identity, 'status': 'pending', 'input': input, 'metadata': metadata}
        self.save(state)
        return state
    def cancel(self, identity):
        state = self.state(identity)
        if state['status'] not in {'done', 'failed', 'cancelled', 'interrupted'}:
            state['status'] = 'cancelled'
            self.save(state)
    def close(self, *, interrupted=False):
        if not interrupted:
            for state in self.list():
                self.cancel(state['id'])


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root, self.storage, self.workspace, self.package, self.env = (
            self.base / path for path in ('runtime', 'state', 'workspace', 'package', 'env'))
        for path in (self.workspace, self.package, self.env):
            path.mkdir()
        (self.package / 'world.py').write_text(WORLD)
        write_json(self.env / 'workers.json', {'version': 1, 'name': 'test', 'types': {
            'swarm-step': {'command': [sys.executable, '-c',
                'import os,json; json.dump({"actions":[{"add":1}],"memory":None},open(os.environ["ASYS_RESULT"],"w"))']}}})
        self.registration = Environment(self.env).register(self.root)
        self.registration.__enter__()
        self.config = {'version': 1, 'mission': 'Reach the target together',
            'world': {'channel': 'world', 'timeoutSeconds': 2}, 'objective': {'target': 4},
            'agents': {'count': 2}, 'limits': {'tickSeconds': 0, 'concurrency': 1, 'turns': 4}}
        self.service_stop = None
        self.reset_service()
        self.engine = self.make_engine()
        self.inbound = Writer(direction_root(self.root, 'swarm', 'in'))

    def tearDown(self):
        self.engine.close()
        self.service_stop.set()
        self.service_thread.join(timeout=3)
        self.assertFalse(self.service_thread.is_alive())
        self.registration.__exit__(None, None, None)
        self.temporary.cleanup()

    def reset_service(self):
        if self.service_stop is not None:
            self.service_stop.set()
            self.service_thread.join(timeout=3)
        namespace = {}
        source = (self.package / 'world.py').read_text()
        exec(source, namespace)
        self.callbacks = SimpleNamespace(**namespace)
        self.service = Service(self.root, self.callbacks, identity='fixture-' + digest(source))
        self.service_stop = threading.Event()
        self.service_thread = threading.Thread(target=self.service.serve, args=(self.service_stop.is_set,), daemon=True)
        self.service_thread.start()

    def make_engine(self, *, real=False):
        return Engine(self.root, self.storage, self.workspace, self.env,
                      executor_factory=Executor if real else ManualExecutor)

    def start(self):
        self.engine.start({'id': 'test-run', 'config': self.config})
        self.engine.pump()

    def answer(self, actions=None, *, status='done', result=None):
        jobs = [row for row in self.engine.queue.list() if row['status'] == 'pending']
        self.assertTrue(jobs, 'No submitted decisions to answer')
        for job in jobs:
            job.update(status=status, result=result if result is not None else {
                'actions': [{'add': 1}] if actions is None else actions, 'memory': {'seen': 1},
                'usage': {'input': 2, 'output': 3, 'totalTokens': 5}})
            self.engine.queue.save(job)
        self.engine.pump()

    def run_to_end(self):
        for _ in range(30):
            if self.engine.state['status'] in {'completed', 'failed', 'cancelled'}:
                return
            pending = [row for row in self.engine.queue.list() if row['status'] == 'pending']
            if pending:
                self.answer()
            else:
                self.engine.pump()
        self.fail('Engine failed to finish')

    def events(self):
        return [json.loads(line) for line in self.engine.journal.read_text().splitlines()]

    def recover(self):
        self.engine = self.make_engine()
        self.engine.start({'id': 'test-run', 'config': self.config})

    def test_goal_concurrency_and_replay(self):
        self.start()
        self.assertEqual(len(self.engine.queue.list()), 1)
        self.assertEqual(self.engine.state['world']['total'], 0)
        self.answer()
        self.assertEqual(self.engine.state['turn'], 0)  # Barrier waits for every agent.
        self.assertEqual(len(self.engine.queue.list()), 2)
        self.answer()
        self.assertEqual(self.engine.state['world']['total'], 2)
        self.run_to_end()
        result = self.engine.state['result']['output']
        self.assertEqual((result['achieved'], result['reason'], result['turns']), (True, 'objective', 2))
        self.assertEqual(result['usage']['totalTokens'], 20)
        for filename in result['files'].values():
            self.assertTrue((self.workspace / filename).is_file())
        report = replay(self.root, self.engine.state['config'], self.engine.journal)
        self.assertTrue(report['verified'])
        self.assertTrue(report['evaluation']['achieved'])
        self.assertEqual(report['turns'], 2)

    def test_real_local_decision_process(self):
        self.engine = self.make_engine(real=True)
        self.start()
        deadline = time.monotonic() + 8
        while self.engine.state['status'] not in {'completed', 'failed'} and time.monotonic() < deadline:
            self.engine.pump()
            time.sleep(0.01)
        self.assertEqual(self.engine.state['status'], 'completed', self.engine.state)
        self.assertTrue(self.engine.state['result']['output']['achieved'])
        self.assertFalse((self.root / 'environments/test/jobs').exists(), 'Member attempts are not runtime jobs')
        self.assertEqual(len(list((self.storage / 'decisions').glob('*/state.json'))), 4)

    def test_whole_population_is_one_standard_runtime_job(self):
        definition = read_json(self.env / 'workers.json')
        definition['types']['swarm'] = {'command': [str(ROOT / 'asys-workers/tools/asys-swarm')]}
        self.registration.__exit__(None, None, None)
        write_json(self.env / 'workers.json', definition)
        self.registration = Environment(self.env).register(self.root)
        self.registration.__enter__()
        directory = self.base / 'parent-job'
        directory.mkdir()
        config = {**self.config, 'objective': None,
                  'limits': {**self.config['limits'], 'turns': 1}}
        queue = RuntimeQueue(self.root / 'environments/test')
        queue.submit('swarm', 'outer-swarm', directory=directory, workspace=self.workspace,
                     input={'id': 'test-run', 'config': config, 'channel': 'swarm'})
        completed = subprocess.run([sys.executable, str(ROOT / 'asys-runtime/tools/asys-runtime'),
            'run', str(self.env), '--root', str(self.root), '--once'],
            capture_output=True, text=True, timeout=15)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        state = queue.state('outer-swarm')
        self.assertEqual(state['status'], 'done', state)
        self.assertIsNone(state['result']['exception'])
        self.assertEqual(state['result']['status'], 'completed')
        self.assertEqual(state['result']['output']['decisions'], 2)
        self.assertEqual([entry['id'] for entry in queue.list()], ['outer-swarm'])
        self.assertTrue((directory / 'swarm/checkpoint.json').is_file())
        self.assertEqual(len(list((directory / 'swarm/decisions').glob('*/state.json'))), 2)

    def test_completed_local_attempt_survives_checkpoint_recovery_without_repeating(self):
        self.config['limits']['turns'] = 1
        self.engine = self.make_engine(real=True)
        self.start()
        identity = next(iter(self.engine.state['pending']['decisions'].values()))['id']
        deadline = time.monotonic() + 5
        while self.engine.queue.state(identity)['status'] != 'done' and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.engine.queue.state(identity)['status'], 'done')
        # The process result is durable, but this controller has not consumed it.
        self.assertEqual(self.engine.state['pending']['decisions']['agent-001']['status'], 'submitted')
        self.engine.close(interrupted=True)
        self.engine = self.make_engine(real=True)
        self.engine.start({'id': 'test-run', 'config': self.config})
        while self.engine.state['status'] not in {'completed', 'failed'} and time.monotonic() < deadline:
            self.engine.pump()
            time.sleep(0.01)
        self.assertEqual(self.engine.state['status'], 'completed')
        self.assertEqual(self.engine.state['decisions'], 2)
        self.assertEqual(len(self.engine.queue.list()), 2)
        self.assertEqual(sum(event['type'] == 'decision.completed' and event['data']['jobId'] == identity
                             for event in self.events()), 1)

    def test_cancel_interrupts_world_wait_without_recursive_rpc(self):
        self.engine.start({'id': 'test-run', 'config': self.config})
        entered = threading.Event()
        original = self.callbacks.observe
        def slow(*args):
            entered.set()
            time.sleep(0.5)
            return original(*args)
        self.callbacks.observe = slow
        def cancel():
            if entered.wait(2):
                self.inbound.send('cancel', {'id': 'test-run'})
        sender = threading.Thread(target=cancel)
        sender.start()
        started = time.monotonic()
        self.engine.pump()
        sender.join(timeout=2)
        self.assertLess(time.monotonic() - started, 0.4)
        self.assertEqual(self.engine.state['status'], 'cancelled')
        self.assertEqual(self.engine.state['world']['total'], 0)
        self.assertEqual(self.engine.queue.list(), [])

    def worker_environment(self, config):
        directory = self.base / 'parent-job'
        directory.mkdir()
        write_json(directory / 'input.json', {'id': 'test-run', 'config': config, 'channel': 'swarm'})
        return {**os.environ, 'ASYS_JOB_DIR': str(directory), 'ASYS_JOB_ID': 'outer',
                'ASYS_INPUT': str(directory / 'input.json'), 'ASYS_RESULT': str(directory / 'result.json'),
                'ASYS_ENVIRONMENT_DIR': str(self.env), 'ASYS_WORKERS_DIR': str(self.env),
                'ASYS_WORKSPACE': str(self.workspace), 'ASYS_RUNTIME_ROOT': str(self.root)}

    def test_channel_cancel_during_startup_returns_normal_cancelled_result(self):
        environment = self.worker_environment(self.config)
        self.inbound.send('cancel', {'id': 'test-run'})
        started = time.monotonic()
        code = worker_main([], environment=environment)
        self.assertLess(time.monotonic() - started, 0.4)
        self.assertEqual(code, 0)
        result = read_json(environment['ASYS_RESULT'])
        self.assertEqual(result['status'], 'cancelled')
        self.assertIsNone(result['exception'])
        self.assertTrue(result['final'])
        self.assertEqual(result['output']['decisions'], 0)
        messages = Reader(direction_root(self.root, 'swarm', 'out')).read(0)
        self.assertTrue(any(event['type'] == 'accepted' for event in messages))
        self.assertEqual(messages[-1]['type'], 'run.result')

    def test_deadline_covers_world_initialization(self):
        original = self.callbacks.initialize
        def slow(*args):
            time.sleep(1.5)
            return original(*args)
        self.callbacks.initialize = slow
        self.config['limits']['seconds'] = 0.1
        environment = self.worker_environment(self.config)
        started = time.monotonic()
        self.assertEqual(worker_main([], environment=environment), 0)
        self.assertLess(time.monotonic() - started, 1.0)
        result = read_json(environment['ASYS_RESULT'])
        self.assertEqual((result['status'], result['output']['reason']), ('completed', 'time_limit'))
        self.assertEqual(result['output']['turns'], 0)
        self.assertEqual(result['output']['metrics'], {})

    def test_deadline_interrupts_world_observation_without_extra_artifact_call(self):
        self.engine.start({'id': 'test-run', 'config': self.config})
        original = self.callbacks.observe
        def slow(*args):
            time.sleep(0.5)
            return original(*args)
        self.callbacks.observe = slow
        self.engine.state['deadline'] = time.time() + 0.1
        started = time.monotonic()
        self.engine.pump()
        self.assertLess(time.monotonic() - started, 0.4)
        self.assertEqual(self.engine.state['result']['output']['reason'], 'time_limit')
        self.assertEqual(self.engine.state['world']['total'], 0)
        self.assertEqual(self.engine.state['result']['output']['artifacts'], {})

    def test_cancel_interrupts_final_artifact_collection(self):
        self.config['objective'] = {'target': 0}
        self.engine.start({'id': 'test-run', 'config': self.config})
        entered = threading.Event()
        def slow(_):
            entered.set()
            time.sleep(1.5)
            return {'late': True}
        self.callbacks.artifacts = slow
        def cancel():
            if entered.wait(2):
                self.inbound.send('cancel', {'id': 'test-run'})
        sender = threading.Thread(target=cancel)
        sender.start()
        started = time.monotonic()
        self.engine.pump()
        sender.join(timeout=2)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(self.engine.state['status'], 'cancelled')
        self.assertEqual(self.engine.state['world']['total'], 0)
        self.assertEqual(self.engine.state['result']['output']['artifacts'], {})

    def test_deadline_interrupts_final_artifact_collection(self):
        self.config['objective'] = {'target': 0}
        self.engine.start({'id': 'test-run', 'config': self.config})
        def slow(_):
            time.sleep(1.5)
            return {'late': True}
        self.callbacks.artifacts = slow
        self.engine.state['deadline'] = time.time() + 0.1
        started = time.monotonic()
        self.engine.pump()
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(self.engine.state['result']['output']['reason'], 'time_limit')
        self.assertEqual(self.engine.state['world']['total'], 0)
        self.assertEqual(self.engine.state['result']['output']['artifacts'], {})

    def test_failed_world_callback_does_not_request_more_world_work(self):
        self.engine.start({'id': 'test-run', 'config': self.config})
        requested = []
        def fail(*_):
            raise ValueError('world unavailable')
        self.callbacks.observe = fail
        self.callbacks.artifacts = lambda *_: requested.append(True) or {}
        self.engine.pump()
        self.assertEqual(self.engine.state['status'], 'failed')
        self.assertEqual(requested, [])
        self.assertIn('world unavailable', self.engine.state['result']['error'])

    def test_worker_failure_has_nonempty_final_and_exception(self):
        environment = self.worker_environment({**self.config, 'agents': {'type': 'missing'}})
        self.assertEqual(worker_main([], environment=environment), 1)
        result = read_json(environment['ASYS_RESULT'])
        self.assertTrue(result['final'])
        self.assertTrue(result['exception'])
        self.assertEqual(result['status'], 'failed')

    def test_runtime_interruption_keeps_a_resumable_checkpoint(self):
        self.engine.start({'id': 'test-run', 'config': self.config})
        self.engine.stopping = lambda: True
        with self.assertRaises(InterruptedError):
            self.engine.pump()
        self.engine.close(interrupted=True)
        self.assertEqual(read_json(self.engine.checkpoint)['status'], 'running')
        self.engine = self.make_engine()
        self.engine.start({'id': 'test-run', 'config': self.config})
        self.engine.pump()
        self.run_to_end()
        self.assertEqual(self.engine.state['status'], 'completed')

    def test_member_type_cannot_launch_recursive_swarm(self):
        definition = read_json(self.env / 'workers.json')
        definition['types']['swarm-step']['command'] = ['/opt/asys/asys-workers/tools/asys-swarm']
        write_json(self.env / 'workers.json', definition)
        self.engine = self.make_engine()
        with self.assertRaisesRegex(ValueError, 'recursively'):
            self.start()

    def test_host_cannot_start_a_second_population_via_controls(self):
        self.start()
        self.inbound.send('start', {'id': 'other', 'config': self.config})
        self.engine.controls()
        messages = Reader(direction_root(self.root, 'swarm', 'out')).read(0)
        self.assertEqual(messages[-1]['type'], 'rejected')
        self.assertEqual(messages[-1]['data']['runId'], 'test-run')
        self.assertEqual(self.engine.state['id'], 'test-run')
        self.recover()
        self.run_to_end()
        self.assertEqual(self.engine.state['status'], 'completed')

    def test_world_channel_cannot_share_custom_control_channel(self):
        self.engine = Engine(self.root, self.storage, self.workspace, self.env,
                             channel='custom', executor_factory=ManualExecutor)
        self.config['world']['channel'] = 'custom'
        with self.assertRaisesRegex(ValueError, 'channels must be different'):
            self.start()

    def test_recovery_rejects_changed_control_channel(self):
        self.start()
        with self.assertRaisesRegex(ValueError, 'control channel differs'):
            Engine(self.root, self.storage, self.workspace, self.env,
                   channel='other', executor_factory=ManualExecutor)

    def test_fresh_job_reusing_control_channel_publishes_its_own_events(self):
        self.config['limits']['turns'] = 1
        self.start()
        self.run_to_end()
        self.engine.close()
        self.engine = Engine(self.root, self.base / 'second-state', self.workspace, self.env,
                             executor_factory=ManualExecutor)
        self.engine.start({'id': 'second-run', 'config': self.config})
        self.engine.pump()
        self.run_to_end()
        messages = Reader(direction_root(self.root, 'swarm', 'out')).read(0)
        for identity in ('test-run', 'second-run'):
            own = [event for event in messages if event['data'].get('runId') == identity]
            self.assertEqual(own[0]['type'], 'swarm.started')
            self.assertEqual(own[0]['data']['store'], 1)
            self.assertEqual(own[-1]['type'], 'run.result')

    def test_recovery_rejects_channel_reused_by_another_run(self):
        self.start()
        self.engine.outbound.send('swarm.started', {'runId': 'other-run', 'store': 1})
        with self.assertRaisesRegex(ValueError, 'another swarm run'):
            self.recover()

    def test_two_worker_processes_cannot_share_the_world_response_cursor(self):
        definition = read_json(self.env / 'workers.json')
        definition['types']['swarm-step']['command'] = [sys.executable, '-c', 'import time;time.sleep(30)']
        write_json(self.env / 'workers.json', definition)
        environment = self.worker_environment(self.config)
        first = subprocess.Popen([str(ROOT / 'asys-workers/tools/asys-swarm')], env=environment,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            checkpoint = Path(environment['ASYS_JOB_DIR']) / 'swarm/checkpoint.json'
            deadline = time.monotonic() + 5
            while not checkpoint.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(checkpoint.exists(), first.poll())
            second_directory = self.base / 'second-job'
            second_directory.mkdir()
            write_json(second_directory / 'input.json', {'id': 'second-run', 'config': self.config,
                                                        'channel': 'second-control'})
            second_environment = {**environment, 'ASYS_JOB_DIR': str(second_directory),
                'ASYS_INPUT': str(second_directory / 'input.json'),
                'ASYS_RESULT': str(second_directory / 'result.json')}
            second = subprocess.run([str(ROOT / 'asys-workers/tools/asys-swarm')], env=second_environment,
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(second.returncode, 1)
            result = read_json(second_directory / 'result.json')
            self.assertIn('owns channel world', result['exception'])
            self.assertTrue(result['final'])
            self.inbound.send('cancel', {'id': 'test-run'})
            self.assertEqual(first.wait(timeout=5), 0)
            self.assertEqual(read_json(environment['ASYS_RESULT'])['status'], 'cancelled')
        finally:
            if first.poll() is None:
                first.terminate()
                try:
                    first.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    first.kill()
                    first.wait()
            first.stderr.close()

    def test_exploration_and_unsatisfied_objective_have_distinct_results(self):
        self.config['objective'] = None
        self.config['limits']['turns'] = 1
        self.start()
        self.run_to_end()
        result = self.engine.state['result']['output']
        self.assertIsNone(result['achieved'])
        self.assertEqual(result['reason'], 'turn_limit')

    def test_unmet_goal_stops_at_turn_limit(self):
        self.config['limits']['turns'] = 1
        self.start()
        self.run_to_end()
        self.assertFalse(self.engine.state['result']['output']['achieved'])
        self.assertEqual(self.engine.state['result']['output']['reason'], 'turn_limit')

    def test_decision_budget_never_submits_a_partial_turn(self):
        self.config['limits']['decisions'] = 1
        self.start()
        self.assertEqual(self.engine.state['result']['output']['reason'], 'decision_limit')
        self.assertEqual(self.engine.queue.list(), [])

    def test_empty_plans_still_advance_world(self):
        self.config['limits']['turns'] = 1
        self.start()
        self.answer([])
        self.answer([])
        self.engine.pump()
        self.assertEqual(self.engine.state['world']['turn'], 1)
        self.assertEqual(self.engine.state['world']['total'], 0)

    def test_inactive_agents_discard_queued_plans_and_stop_inference(self):
        self.config['world']['settings'] = {'stopAgents': True}
        self.config['limits']['turns'] = 3
        self.start()
        self.answer([{'add': 1}, {'add': 2}])
        self.answer([{'add': 1}, {'add': 2}])
        self.run_to_end()
        self.assertEqual(self.engine.state['world']['total'], 2)
        self.assertEqual(self.engine.state['decisions'], 2)
        self.assertEqual(self.engine.state['turn'], 3)

    def test_pause_commits_current_turn_then_resume(self):
        self.start()
        self.inbound.send('pause', {'id': 'test-run'})
        self.engine.pump()
        self.assertTrue(self.engine.state['pauseRequested'])
        self.answer()
        self.answer()
        self.assertEqual(self.engine.state['status'], 'paused')
        self.engine.pump()
        self.assertEqual(self.engine.state['turn'], 1)
        self.inbound.send('resume', {'id': 'test-run'})
        self.engine.pump()
        self.run_to_end()
        self.assertTrue(self.engine.state['result']['output']['achieved'])

    def test_cancel_does_not_apply_partial_agent_actions(self):
        self.start()
        self.answer()
        self.inbound.send('cancel', {'id': 'test-run'})
        self.engine.pump()
        self.assertEqual(self.engine.state['status'], 'cancelled')
        self.assertEqual(self.engine.state['world']['total'], 0)
        self.assertFalse(any(row['status'] == 'pending' for row in self.engine.queue.list()))
        self.assertEqual(self.engine.state['result']['output']['usage']['totalTokens'], 5)

    def test_wall_clock_limit_cancels_pending_jobs(self):
        self.start()
        self.engine.state['deadline'] = 0
        self.engine.pump()
        self.assertEqual(self.engine.state['result']['output']['reason'], 'time_limit')
        self.assertEqual(self.engine.queue.list()[0]['status'], 'cancelled')

    def test_job_timeout_and_bad_actions_fail_without_world_mutation(self):
        self.start()
        self.answer([{'add': 200}])
        self.assertEqual(self.engine.state['status'], 'failed')
        self.assertEqual(self.engine.state['world']['total'], 0)
        self.assertIn('200', self.engine.state['result']['error'])

    def test_pending_job_timeout(self):
        self.start()
        decision = next(iter(self.engine.state['pending']['decisions'].values()))
        decision['submittedAt'] = 0
        self.engine.pump()
        self.assertEqual(self.engine.state['status'], 'failed')
        self.assertIn('jobSeconds', self.engine.state['result']['error'])

    def test_recovery_reuses_job_and_retries_only_interrupted_decisions(self):
        self.start()
        first = self.engine.queue.list()[0]['id']
        self.recover()
        self.engine.pump()
        self.assertEqual(self.engine.queue.list()[0]['id'], first)
        self.answer(status='interrupted')
        self.assertEqual(len(self.engine.queue.list()), 2)
        self.assertEqual(self.engine.state['decisions'], 3)
        self.run_to_end()
        self.assertTrue(self.engine.state['result']['output']['achieved'])

    def test_crash_after_queue_publish_does_not_create_duplicate_job(self):
        self.start()
        first = self.engine.queue.list()[0]['id']
        pending = self.engine.state['pending']['decisions']['agent-001']
        pending['status'] = 'planned'
        pending.pop('submittedAt')
        self.engine.save()
        self.recover()
        self.engine.pump()
        self.assertEqual([row['id'] for row in self.engine.queue.list()], [first])

    def test_journal_recovery_republishes_durable_outbox_once(self):
        self.start()
        self.engine.event('test.fault', {'value': 42})
        with patch.object(self.engine.outbound, 'send', side_effect=OSError('crash')):
            with self.assertRaises(OSError):
                self.engine.flush()
        self.recover()
        self.assertEqual(sum(row['type'] == 'test.fault' for row in self.events()), 1)
        messages = Reader(direction_root(self.root, 'swarm', 'out')).read(0)
        self.assertEqual(sum(row['type'] == 'test.fault' for row in messages), 1)
        with self.engine.journal.open('ab') as stream:
            stream.write(b'{"partial')
        self.recover()
        self.assertEqual(sum(row['type'] == 'test.fault' for row in self.events()), 1)

    def test_terminal_exports_exist_before_result_publication_and_are_repaired(self):
        self.start()
        original = self.engine.outbound.send
        export = self.workspace / 'swarm-runs/test-run'
        def check(kind, data):
            if kind == 'run.result':
                self.assertEqual(json.loads((export / 'trace.jsonl').read_text().splitlines()[-1])['type'], 'run.result')
                self.assertTrue((export / 'world.json').is_file())
            return original(kind, data)
        with patch.object(self.engine.outbound, 'send', side_effect=check):
            self.run_to_end()
        (export / 'world.json').unlink()
        self.recover()
        self.assertEqual(read_json(export / 'world.json'), self.engine.state['world'])

    def test_recovery_rejects_modified_world(self):
        self.start()
        with (self.package / 'world.py').open('a') as stream:
            stream.write('\n# modified\n')
        self.reset_service()
        with self.assertRaisesRegex(ValueError, 'World service identity differs'):
            self.recover()

    def test_recovery_rejects_changed_worker_definition(self):
        self.start()
        definition = read_json(self.env / 'workers.json')
        definition['types']['swarm-step']['env'] = {'CHANGED': 'yes'}
        write_json(self.env / 'workers.json', definition)
        with self.assertRaisesRegex(ValueError, 'Worker environment definition differs'):
            self.recover()

    def test_replay_detects_changed_actions(self):
        self.start()
        self.run_to_end()
        rows = self.events()
        next(row for row in rows if row['type'] == 'swarm.tick')['data']['actions']['agent-001']['add'] = 0
        trace = self.base / 'changed.jsonl'
        trace.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        with self.assertRaisesRegex(ValueError, 'diverged'):
            replay(self.root, self.engine.state['config'], trace)

    def test_replay_rejects_fabricated_outcome_and_missing_events(self):
        self.start()
        self.run_to_end()
        rows = self.events()
        rows[-1]['data']['output']['achieved'] = False
        trace = self.base / 'changed.jsonl'
        trace.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        with self.assertRaisesRegex(ValueError, 'contradicts'):
            replay(self.root, self.engine.state['config'], trace)
        trace.write_text(''.join(json.dumps(row) + '\n' for row in rows[1:]))
        with self.assertRaisesRegex(ValueError, 'sequence'):
            replay(self.root, self.engine.state['config'], trace)

    def test_oversized_observation_fails_before_submitting_jobs(self):
        self.start()
        self.answer()
        self.answer()
        self.callbacks.observe = lambda *_: {'oversized': 'x' * (256 * 1024)}
        self.engine.pump()
        self.assertEqual(self.engine.state['status'], 'failed')
        self.assertEqual(self.engine.state['turn'], 1)
        self.assertEqual(len(self.engine.queue.list()), 2)
        self.assertIn('exceeds', self.engine.state['result']['error'])

    def test_aggregate_pending_limit_fails_cleanly_before_submitting(self):
        self.config['agents']['count'] = 40
        self.start()
        # Roll forward to a fresh run setup with large but individually valid
        # observations. This exercises the aggregate checkpoint guard.
        self.engine.cancel_jobs()
        self.engine.state['pending'] = None
        existing = len(self.engine.queue.list())
        self.callbacks.observe = lambda *_: {'context': 'x' * (120 * 1024)}
        self.engine.pump()
        self.assertEqual(self.engine.state['status'], 'failed')
        self.assertEqual(len(self.engine.queue.list()), existing)
        self.assertTrue((self.storage / 'result.json').is_file())

    def large_world(self, *, state_bytes=1200 * 1024, observation_bytes=90 * 1024,
                    artifact_bytes=900 * 1024, event_bytes=0):
        (self.package / 'world.py').write_text(WORLD + f'''
def action_schema():
    return {{"type":"object","properties":{{"add":{{"type":"integer"}},"payload":{{"type":"string"}}}},"required":["add","payload"],"additionalProperties":False}}
def initialize(settings, agents, seed):
    return {{"total":0,"turn":0,"payload":"w" * {state_bytes}}}
def observe(state, agent):
    return {{"context":"o" * {observation_bytes}}}
def step(state, actions):
    state["total"] += sum(action["add"] for action in actions.values())
    state["turn"] += 1
    return {{"state":state,"events":[{{"payload":"e" * {event_bytes}}}]}}
def artifacts(state):
    return {{"payload":"a" * {artifact_bytes},"total":state["total"]}}
''')
        self.reset_service()
        self.engine = self.make_engine()

    def test_large_world_sixteen_decisions_recovery_and_replay(self):
        self.large_world()
        self.config.update(agents={'count': 16}, objective={'target': 32})
        self.config['limits'].update(concurrency=16, turns=2)
        self.start()
        self.assertEqual(len(self.engine.queue.list()), 16)
        self.assertGreater(self.engine.checkpoint.stat().st_size, 2 * 1024 * 1024)
        job_ids = {job['id'] for job in self.engine.queue.list()}
        self.recover()
        self.engine.pump()
        self.assertEqual({job['id'] for job in self.engine.queue.list()}, job_ids)
        action = {'add': 1, 'payload': 'd' * (24 * 1024)}
        self.answer([action])
        self.assertEqual(self.engine.state['world']['total'], 16)
        self.assertGreater(len(json.dumps(self.events()[-2]['data']['actions'])), 256 * 1024)
        self.engine.pump()
        self.answer([action])
        self.engine.pump()
        self.assertEqual(self.engine.state['status'], 'completed')
        self.assertEqual(self.engine.state['result']['output']['decisions'], 32)
        self.assertEqual(len(read_json(self.workspace / 'swarm-runs/test-run/artifacts.json')['payload']), 900 * 1024)
        self.assertTrue(replay(self.root, self.engine.state['config'], self.engine.journal)['verified'])

    def test_large_terminal_outbox_recovery_preserves_artifacts(self):
        self.large_world(state_bytes=WORLD_BYTES - 1024, observation_bytes=0,
                         artifact_bytes=1024 * 1024 - 1024)
        self.config['agents']['count'] = 1
        self.config['limits']['turns'] = 1
        self.start()
        self.answer([{'add': 1, 'payload': ''}])
        with patch.object(self.engine.outbound, 'send', side_effect=OSError('crash')):
            with self.assertRaisesRegex(OSError, 'crash'):
                self.engine.finish('completed', 'turn_limit')
        saved = read_json(self.engine.checkpoint)
        self.assertEqual(saved['status'], 'completed')
        self.assertEqual([event['type'] for event in saved['outbox']],
                         ['swarm.completed', 'swarm.snapshot', 'run.result'])
        checkpoint_bytes = self.engine.checkpoint.stat().st_size
        self.assertGreater(checkpoint_bytes, 5 * 1024 * 1024)
        self.assertLess(checkpoint_bytes, PUBLICATION_CHECKPOINT_BYTES)
        self.recover()
        self.recover()
        self.assertEqual(sum(row['type'] == 'run.result' for row in self.events()), 1)
        messages = Reader(direction_root(self.root, 'swarm', 'out')).read(0)
        self.assertEqual(sum(row['type'] == 'run.result' for row in messages), 1)
        exported = read_json(self.workspace / 'swarm-runs/test-run/artifacts.json')
        self.assertEqual(exported, saved['result']['output']['artifacts'])
        self.assertTrue(replay(self.root, self.engine.state['config'], self.engine.journal)['verified'])

    def test_large_step_outbox_rejected_before_world_commit(self):
        # The callback result fits 4 MiB, but events appear in both tick and
        # snapshot; the complete durable publication must also fit its budget.
        self.large_world(state_bytes=1024 * 1024, observation_bytes=0,
                         artifact_bytes=0, event_bytes=3 * 1024 * 1024 - 1024)
        self.config['agents']['count'] = 1
        self.start()
        self.answer([{'add': 1, 'payload': ''}])
        self.assertEqual(self.engine.state['status'], 'failed')
        self.assertEqual(self.engine.state['world']['total'], 0)
        self.assertEqual(self.engine.state['turn'], 0)
        self.assertFalse(any(row['type'] == 'swarm.tick' for row in self.events()))
        self.assertIn(str(PUBLICATION_CHECKPOINT_BYTES), self.engine.state['result']['error'])
        self.assertTrue((self.storage / 'result.json').is_file())

    def test_world_and_artifact_caps_are_still_enforced(self):
        self.large_world(state_bytes=WORLD_BYTES, observation_bytes=0, artifact_bytes=0)
        with self.assertRaisesRegex(Exception, 'exceeds'):
            self.start()
        self.assertIsNone(self.engine.state)
        self.large_world(state_bytes=300 * 1024, observation_bytes=0,
                         artifact_bytes=1024 * 1024)
        self.config['agents']['count'] = 1
        self.config['limits']['turns'] = 1
        self.start()
        self.answer([{'add': 1, 'payload': ''}])
        self.engine.pump()
        self.assertEqual(self.engine.state['status'], 'failed')
        self.assertIn('exceeds', self.engine.state['result']['error'])

    def test_individual_decision_cap_is_unchanged(self):
        self.large_world(state_bytes=300 * 1024, observation_bytes=0, artifact_bytes=0)
        self.config['agents']['count'] = 1
        self.start()
        self.answer([{'add': 1, 'payload': 'd' * (256 * 1024)}])
        self.assertEqual(self.engine.state['status'], 'failed')
        self.assertEqual(self.engine.state['world']['total'], 0)
        self.assertIn(str(256 * 1024), self.engine.state['result']['error'])

    def test_config_validation_does_not_execute_module(self):
        (self.package / 'world.py').write_text('raise RuntimeError("must not import")')
        write_json(self.package / 'swarm.json', self.config)
        self.assertEqual(load_config(self.package / 'swarm.json')['agents']['count'], 2)
        for limits in ({'memoryBytes': 0}, {'seconds': True}, {'concurrency': 65}, {'surprise': 1}):
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                validate_config({**self.config, 'limits': limits}, self.package)
        with self.assertRaisesRegex(ValueError, 'module'):
            validate_config({**self.config, 'world': {'module': '../escape.py'}}, self.package)


if __name__ == '__main__':
    unittest.main()
