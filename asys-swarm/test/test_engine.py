"""Exercise authoritative turns and recovery over the real filesystem runtime."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from asys_runtime.channel import Reader, Writer, direction_root
from asys_runtime.environment import Environment, describe
from asys_runtime.files import read_json, write_json
from asys_swarm.config import load_config, validate_config
from asys_swarm.engine import Engine
from asys_swarm.limits import PUBLICATION_CHECKPOINT_BYTES, WORLD_BYTES
from asys_swarm.replay import replay

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
            'world': {'module': 'world.py'}, 'objective': {'target': 4},
            'agents': {'count': 2}, 'limits': {'tickSeconds': 0, 'concurrency': 1, 'turns': 4}}
        self.engine = Engine(self.root, self.storage, self.workspace, self.package)
        self.inbound = Writer(direction_root(self.root, 'swarm', 'in'))

    def tearDown(self):
        self.registration.__exit__(None, None, None)
        self.temporary.cleanup()

    def start(self):
        self.inbound.send('start', {'id': 'test-run', 'environment': 'test',
            'environmentDefinition': describe(self.root, 'test')['definition'], 'config': self.config})
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
        self.engine = Engine(self.root, self.storage, self.workspace, self.package)

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
        report = replay(self.package, self.engine.state['config'], self.engine.journal)
        self.assertTrue(report['verified'])
        self.assertTrue(report['evaluation']['achieved'])
        self.assertEqual(report['turns'], 2)

    def test_real_runtime_decision_process(self):
        self.start()
        for _ in range(10):
            if self.engine.state['status'] == 'completed':
                break
            response = subprocess.run([sys.executable, str(ROOT / 'asys-runtime/tools/asys-runtime'),
                'run', str(self.env), '--root', str(self.root), '--once'],
                text=True, capture_output=True, timeout=10)
            self.assertEqual(response.returncode, 0, response.stderr)
            self.engine.pump()
        self.assertTrue(self.engine.state['result']['output']['achieved'])

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
        with self.assertRaisesRegex(ValueError, 'package differs'):
            self.recover()

    def test_recovery_rejects_changed_worker_definition(self):
        self.start()
        descriptor = describe(self.root, 'test')
        descriptor['definition'] = '0' * 64
        write_json(self.root / 'environments/test/environment.json', descriptor)
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
            replay(self.package, self.engine.state['config'], trace)

    def test_replay_rejects_fabricated_outcome_and_missing_events(self):
        self.start()
        self.run_to_end()
        rows = self.events()
        rows[-1]['data']['output']['achieved'] = False
        trace = self.base / 'changed.jsonl'
        trace.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        with self.assertRaisesRegex(ValueError, 'contradicts'):
            replay(self.package, self.engine.state['config'], trace)
        trace.write_text(''.join(json.dumps(row) + '\n' for row in rows[1:]))
        with self.assertRaisesRegex(ValueError, 'sequence'):
            replay(self.package, self.engine.state['config'], trace)

    def test_oversized_observation_fails_before_submitting_jobs(self):
        self.start()
        self.answer()
        self.answer()
        self.engine.world.module.observe = lambda *_: {'oversized': 'x' * (256 * 1024)}
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
        self.engine.world.module.observe = lambda *_: {'context': 'x' * (120 * 1024)}
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
        # No run has started, so replace the controller's package fingerprint.
        self.engine = Engine(self.root, self.storage, self.workspace, self.package)

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
        self.assertTrue(replay(self.package, self.engine.state['config'], self.engine.journal)['verified'])

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
        self.assertTrue(replay(self.package, self.engine.state['config'], self.engine.journal)['verified'])

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
        self.start()
        self.assertIsNone(self.engine.state)
        messages = Reader(direction_root(self.root, 'swarm', 'out')).read(0)
        self.assertEqual(messages[-1]['type'], 'rejected')
        self.assertIn('exceeds', messages[-1]['data']['message'])
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
        with self.assertRaisesRegex(ValueError, 'inside'):
            validate_config({**self.config, 'world': {'module': '../escape.py'}}, self.package)


if __name__ == '__main__':
    unittest.main()
