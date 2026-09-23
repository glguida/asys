"""World RPCs use real Runtime channels and a separately running host process."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from asys_runtime.channel import Reader, Writer, direction_root
from asys_swarm.config import digest, validate_config
from asys_swarm.replay import replay
from asys_swarm.world import World, WorldError
from asys_swarm import world_protocol as protocol
from asys_swarm.world_service import Service


ROOT = Path(__file__).resolve().parents[2]


class Rules:
    def __init__(self):
        self.initializations = 0

    def action_schema(self):
        return {'type': 'object', 'properties': {'add': {'type': 'integer'}},
                'required': ['add'], 'additionalProperties': False}

    def initialize(self, settings, agents, seed):
        self.initializations += 1
        return {'total': settings.get('total', 0), 'turn': 0, 'agents': agents, 'seed': seed}

    def observe(self, state, agent):
        if state.get('slow'):
            time.sleep(10)
        if state.get('oversized'):
            return {'text': 'x' * (256 * 1024)}
        if state.get('fail'):
            raise ValueError('invalid experiment state')
        state['total'] += 999  # Must not mutate the client's authoritative state.
        return {'active': True, 'agent': agent, 'seen': state['total']}

    def step(self, state, actions):
        state['total'] += sum(item['add'] for item in actions.values())
        state['turn'] += 1
        return {'state': state, 'events': [{'type': 'advance'}]}

    def evaluate(self, state, objective):
        return {'achieved': state['total'] >= objective.get('target', 10),
                'metrics': {'total': state['total']}, 'summary': str(state['total'])}

    def artifacts(self, state):
        return {'total': state['total']}


def config(**world):
    return validate_config({'version': 1, 'mission': 'Reach two',
                            'world': {'timeoutSeconds': 2, **world},
                            'objective': {'target': 2}, 'agents': {'count': 1}})


def request(method='initialize', *, request_id='request-1', run_id='run-1', args=None):
    return {'version': 1, 'runId': run_id, 'requestId': request_id, 'method': method,
            'identity': None if method == 'describe' else 'rules-v1',
            'arguments': ([{}, ['agent-001'], 0] if args is None else args)}


class ChannelsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'runtime'


class HostWorldTests(ChannelsTest):
    def start_service(self, identity='rules-v1'):
        ready = self.root.parent / 'ready'
        ready.unlink(missing_ok=True)
        env = dict(os.environ)
        env['PYTHONPATH'] = os.pathsep.join(map(str, (ROOT / 'asys-workers', ROOT / 'asys-runtime',
                                                    ROOT / 'asys-swarm/test')))
        script = ('from pathlib import Path; import sys; '
                  'from asys_swarm.world_service import Service; '
                  'from test_world_protocol import Rules; '
                  'service=Service(sys.argv[1],Rules(),identity=sys.argv[2]); '
                  'Path(sys.argv[3]).write_text("ready"); service.serve()')
        process = subprocess.Popen([sys.executable, '-c', script, str(self.root), identity, str(ready)],
                                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop_service, process)
        deadline = time.monotonic() + 5
        while not ready.exists():
            if process.poll() is not None:
                self.fail(process.stderr.read())
            if time.monotonic() >= deadline:
                self.fail('World host did not start')
            time.sleep(0.01)
        return process

    @staticmethod
    def stop_service(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        if process.stderr:
            process.stderr.close()

    def test_all_operations_cross_process_boundary_without_loading_world_code(self):
        self.start_service()
        world = World(self.root, config(), 'run-1')
        self.assertEqual(world.identity, {'implementation': 'rules-v1', 'actionSchema': digest(Rules().action_schema())})
        self.assertEqual(world.call('action_schema'), world.schema)
        state = world.call('initialize', {}, ['agent-001'], 7)
        observation = world.call('observe', state, 'agent-001')
        self.assertEqual(observation['seen'], 999)
        self.assertEqual(state['total'], 0)
        result = world.call('step', state, {'agent-001': {'add': 2}})
        self.assertEqual(state['turn'], 0)
        self.assertTrue(world.evaluation(result['state'], {'target': 2})['achieved'])
        self.assertIsNone(world.evaluation(result['state'], None)['achieved'])
        self.assertEqual(world.call('artifacts', result['state']), {'total': 2})
        world.decision({'actions': [{'add': 1}], 'memory': None}, max_actions=1, memory_bytes=32)

    def test_bad_callback_and_oversized_observation_fail_without_killing_service(self):
        self.start_service()
        world = World(self.root, config(), 'run-1')
        for flag in ('oversized', 'fail'):
            with self.subTest(flag=flag), self.assertRaises(WorldError):
                world.call('observe', {'total': 0, flag: True}, 'agent-001')
        self.assertEqual(world.call('artifacts', {'total': 9}), {'total': 9})

    def test_timeout_and_cancellation_return_without_waiting_for_slow_callback(self):
        self.start_service()
        world = World(self.root, config(), 'run-1')
        world.timeout = 0.06
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            world.call('observe', {'total': 0, 'slow': True}, 'agent-001')
        self.assertLess(time.monotonic() - started, 1)
        last = world.requests.event(world.requests.last())
        self.assertEqual(last['type'], 'world.cancel')
        def cancelled():
            raise InterruptedError('host cancelled')
        world.poll = cancelled
        with self.assertRaisesRegex(InterruptedError, 'host cancelled'):
            world.call('artifacts', {'total': 0})

    def test_restart_preserves_identity_and_changed_implementation_is_rejected(self):
        first = self.start_service()
        world = World(self.root, config(), 'run-1')
        state = world.call('initialize', {}, ['agent-001'], 0)
        self.stop_service(first)
        second = self.start_service()
        recovered = World(self.root, config(), 'run-1')
        self.assertEqual(world.identity, recovered.identity)
        self.assertEqual(recovered.call('artifacts', state), {'total': 0})
        self.stop_service(second)
        self.start_service('rules-v2')
        with self.assertRaisesRegex(ValueError, 'identity changed'):
            recovered.call('artifacts', state)

    def test_acknowledged_requests_and_replies_are_pruned(self):
        self.start_service()
        world = World(self.root, config(), 'run-1')
        for index in range(30):
            self.assertEqual(world.call('artifacts', {'total': index}), {'total': index})
        self.assertLessEqual(len(world.requests.sequences()), 2)
        self.assertEqual(len(world.responses.sequences()), 1)
        self.assertLess((self.root / 'channels/world/.world-response.json').stat().st_size, 4096)

    def test_replay_uses_world_channel_and_checks_implementation_identity(self):
        self.start_service()
        settings = config()
        world = World(self.root, settings, 'run-1')
        state = world.call('initialize', {}, ['agent-001'], 0)
        actions = {'agent-001': {'add': 2}}
        final = world.call('step', state, actions)['state']
        rows = [
            {'sequence': 1, 'type': 'swarm.started', 'data': {'runId': 'run-1', 'config': settings,
                'worldIdentity': world.identity, 'stateHash': digest(state)}},
            {'sequence': 2, 'type': 'swarm.tick', 'data': {'runId': 'run-1', 'turn': 1,
                'actions': actions, 'stateHash': digest(final)}},
            {'sequence': 3, 'type': 'run.result', 'data': {'runId': 'run-1', 'status': 'completed',
                'output': {'turns': 1, 'achieved': True, 'reason': 'objective', 'metrics': {'total': 2},
                           'artifacts': {'total': 2}}}},
        ]
        trace = self.root.parent / 'trace.jsonl'
        def save():
            trace.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        save()
        self.assertTrue(replay(self.root, settings, trace)['verified'])
        rows[0]['data']['worldIdentity']['implementation'] = 'different-world'
        save()
        with self.assertRaisesRegex(ValueError, 'identity differs'):
            replay(self.root, settings, trace)

    def test_replay_time_limit_accepts_only_empty_artifacts_and_still_checks_state(self):
        self.start_service()
        settings = config()
        world = World(self.root, settings, 'run-1')
        state = world.call('initialize', {}, ['agent-001'], 0)
        rows = [
            {'sequence': 1, 'type': 'swarm.started', 'data': {'runId': 'run-1', 'config': settings,
                'worldIdentity': world.identity, 'stateHash': digest(state)}},
            {'sequence': 2, 'type': 'run.result', 'data': {'runId': 'run-1', 'status': 'completed',
                'output': {'turns': 0, 'achieved': False, 'reason': 'time_limit',
                           'metrics': {'total': 0}, 'artifacts': {}}}},
        ]
        trace = self.root.parent / 'timed-out.jsonl'
        def save():
            trace.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        save()
        self.assertTrue(replay(self.root, settings, trace)['verified'])
        rows[1]['data']['output']['artifacts'] = {'total': 0}
        save()
        with self.assertRaisesRegex(ValueError, 'artifacts differ'):
            replay(self.root, settings, trace)
        rows[1]['data']['output']['artifacts'] = {}
        rows[1]['data']['output']['metrics']['total'] = 1
        save()
        with self.assertRaisesRegex(ValueError, 'contradicts'):
            replay(self.root, settings, trace)


class ProtocolTests(ChannelsTest):
    def test_configuration_is_data_only_and_rejects_module_paths(self):
        self.assertEqual(config()['world']['channel'], 'world')
        for values in ({'module': 'world.py'}, {'channel': '../escape'}, {'channel': 'swarm'},
                       {'timeoutSeconds': 0}, {'timeoutSeconds': True}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                config(**values)

    def test_wrong_correlations_cannot_supply_a_result(self):
        service = Service(self.root, Rules(), identity='rules-v1')
        delivered = set()
        def peer():
            for event in service.requests.read():
                value = event['data']
                if event['sequence'] in delivered or event['type'] != 'world.request':
                    continue
                delivered.add(event['sequence'])
                good = service._answer(value, set())
                for key, replacement in [('runId', 'other-run'), ('requestId', 'other-request'), ('method', 'other')]:
                    bad = {**good, key: replacement}
                    protocol.send(service.responses, 'world.response', bad)
                protocol.send(service.responses, 'world.response', good)
                service.requests.advance(event['sequence'])
        world = World(self.root, config(), 'run-1', poll=peer)
        self.assertEqual(world.call('artifacts', {'total': 3}), {'total': 3})

    def test_malformed_reply_fails_visibly(self):
        service = Service(self.root, Rules(), identity='rules-v1')
        def peer():
            for event in service.requests.read():
                value = service._answer(event['data'], set())
                value['version'] = 99
                service.responses.send('world.response', value)
                service.requests.advance(event['sequence'])
        with self.assertRaisesRegex(ValueError, 'protocol version'):
            World(self.root, config(), 'run-1', poll=peer)

    def test_world_is_not_evaluated_for_unknown_operations_or_invalid_arity(self):
        rules = Rules()
        service = Service(self.root, rules, identity='rules-v1')
        writer = Writer(direction_root(self.root, 'world', 'out'))
        for index, value in enumerate((request('exec', args=['evil.py']), request(args=[]),
                                        {**request(), 'version': 2})):
            value['requestId'] = f'invalid-{index}'
            writer.send('world.request', value)
            service.pump()
            response = service.responses.event(service.responses.last())['data']
            self.assertFalse(response['ok'])
        self.assertEqual(rules.initializations, 0)

    def test_cancelled_queued_request_is_not_executed(self):
        rules = Rules()
        service = Service(self.root, rules, identity='rules-v1')
        writer = Writer(direction_root(self.root, 'world', 'out'))
        value = request()
        writer.send('world.request', value)
        writer.send('world.cancel', {key: value[key] for key in ('version', 'runId', 'requestId')})
        service.pump()
        self.assertEqual(rules.initializations, 0)
        self.assertEqual(service.responses.event(service.responses.last())['data']['error']['code'], 'cancelled')

    def test_restart_republishes_cached_response_after_publish_before_ack_crash(self):
        rules = Rules()
        service = Service(self.root, rules, identity='rules-v1')
        writer = Writer(direction_root(self.root, 'world', 'out'))
        value = request()
        writer.send('world.request', value)
        with patch.object(service.requests, 'advance', side_effect=RuntimeError('crash')):
            with self.assertRaisesRegex(RuntimeError, 'crash'):
                service.pump()
        self.assertEqual(rules.initializations, 1)
        recovered_rules = Rules()
        recovered = Service(self.root, recovered_rules, identity='rules-v1')
        recovered.pump()
        self.assertEqual(recovered_rules.initializations, 0)
        self.assertTrue(recovered.responses.event(recovered.responses.last())['data']['ok'])
        changed = deepcopy(value)
        changed['arguments'][0] = {'total': 8}
        writer.send('world.request', changed)
        recovered.pump()
        self.assertEqual(recovered.responses.event(recovered.responses.last())['data']['error']['code'], 'request_conflict')


if __name__ == '__main__':
    unittest.main()
