"""Named dispatch uses separately deployed components through private sessions."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from asys_worker import job_channels, prepare
from asys_runtime.channel import Writer, direction_root
from asys_runtime.environment import Environment
from asys_runtime.files import read_json, write_json
from asys_runtime.queue import Queue

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / 'asys-workers/tools/asys-worker'

WORLD = '''import os,sys
from asys_swarm.world_service import Component
def action_schema():
    return {"type":"object","properties":{"add":{"type":"integer"}},"required":["add"],"additionalProperties":False}
def initialize(settings,agents,seed):
    return {"total":0,"turn":0}
def observe(state,agent):
    return {"active":True,"total":state["total"],"agent":agent}
def step(state,actions):
    return {"state":{"total":state["total"]+sum(a["add"] for a in actions.values()),"turn":state["turn"]+1},"events":[]}
def evaluate(state,objective):
    return {"achieved":False,"metrics":{"total":state["total"]},"summary":str(state["total"])}
def artifacts(state):
    return {"total":state["total"]}
Component(os.environ["ASYS_WORLD_ROOT"],sys.modules[__name__],identity="component-test-world").serve()
'''


class NamedWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.env, self.job, self.workspace, self.runtime = (self.base / name for name in ('env', 'job', 'workspace', 'runtime'))
        for directory in (self.env, self.job, self.workspace, self.runtime):
            directory.mkdir()
        self.environment = {**os.environ, 'ASYS_JOB_ID': 'job-one', 'ASYS_JOB_DIR': str(self.job),
            'ASYS_ENVIRONMENT_DIR': str(self.env), 'ASYS_WORKERS_DIR': str(self.env),
            'ASYS_WORKSPACE': str(self.workspace), 'ASYS_INPUT': str(self.job / 'input.json'),
            'ASYS_RESULT': str(self.job / 'result.json'), 'ASYS_RUNTIME_ROOT': str(self.runtime),
            'PYTHONPATH': os.pathsep.join(str(ROOT / name) for name in ('python', 'asys-runtime', 'asys-workers'))}
        self.children = []

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        self.temporary.cleanup()

    def definition(self, kind, config):
        return {'version': 1, 'kind': kind, 'config': config}

    def prepare(self, kind, config, payload=None, **kwargs):
        return prepare(self.definition(kind, config), payload or {'request': 'Find a useful result'}, self.environment, **kwargs)

    def wait(self, predicate, message='Condition did not become true', timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result
            time.sleep(0.02)
        self.fail(message)

    def test_agent_dispatch_preserves_prompt_and_only_allows_public_budget_override(self):
        command, env = self.prepare('agent', {'agent': 'writer', 'model': 'original/model', 'maxSteps': 4},
                                    {'request': 'Exact request', 'parameters': {'maxSteps': 7}}, model='override/model')
        self.assertEqual(command[1:], ['--agent', 'writer', '--model', 'override/model'])
        self.assertEqual(read_json(env['ASYS_INPUT']), {'prompt': 'Exact request', 'maxSteps': 7})
        self.assertEqual(read_json(self.job / 'worker.json')['kind'], 'agent')
        self.assertFalse((self.env / 'agents').exists())

    def test_builtin_simple_uses_system_assets_without_environment_mutation(self):
        command, _ = self.prepare('agent', {'agent': 'simple', 'system': True})
        self.assertIn('--system-agent', command)
        self.assertEqual(list(self.env.iterdir()), [])

    def test_named_agent_assets_remain_in_primary_environment_with_an_external_bundle(self):
        external = self.base / 'bundle'
        external.mkdir()
        self.environment['ASYS_WORKERS_DIR'] = str(external)
        _, env = self.prepare('agent', {'agent': 'writer'})
        self.assertEqual(env['ASYS_WORKERS_DIR'], str(self.env))

    def test_private_channels_fit_every_valid_job_id_without_literal_hash_aliases(self):
        long_id = 'x' * 128
        channels = job_channels(long_id)
        self.assertTrue(all(len(channel) <= 128 for channel in channels))
        self.assertEqual(job_channels(long_id), channels)
        self.assertNotEqual(channels, job_channels('x' * 127 + 'y'))
        self.assertNotEqual(channels, job_channels(channels[0].removeprefix('swarm-')))
        self.assertEqual(job_channels('job-one'), ('swarm-job-one', 'world-job-one'))

    def test_goal_and_senate_keep_existing_structured_contracts(self):
        for kind, config, expected in [
            ('goal', {'maxAttempts': 3}, {'goal': 'Find a useful result', 'maxAttempts': 3}),
            ('senate', {'version': 1, 'princeps': {'name': 'Chair'}, 'senators': [{'name': 'Reviewer'}]}, None),
        ]:
            with self.subTest(kind=kind):
                directory = self.base / kind
                directory.mkdir()
                env = {**self.environment, 'ASYS_JOB_DIR': str(directory)}
                command, adapted = prepare(self.definition(kind, config), {'request': 'Find a useful result'}, env)
                value = read_json(adapted['ASYS_INPUT'])
                self.assertTrue(command[0].endswith(f'asys-{kind}'))
                self.assertEqual(value, expected if expected else {'topic': 'Find a useful result', 'senate': config})

    def test_parameters_cannot_replace_models_programs_world_or_request(self):
        for parameters in ({'model': 'untrusted/model'}, {'command': ['sh']}, {'request': 'changed'}, [], {'maxSteps': True}):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                self.prepare('agent', {'agent': 'writer'}, {'request': 'Request', 'parameters': parameters})
        self.assertFalse((self.job / 'worker-input.json').exists())

    def test_recovery_rejects_changed_definition_or_request(self):
        self.prepare('agent', {'agent': 'writer'})
        self.prepare('agent', {'agent': 'writer'})
        with self.assertRaisesRegex(ValueError, 'recovery input differs'):
            self.prepare('agent', {'agent': 'writer'}, {'request': 'A different request'})
        with self.assertRaisesRegex(ValueError, 'recovery input differs'):
            self.prepare('agent', {'agent': 'other'})

    def swarm(self, *, member_delay=0, turns=1, world=WORLD):
        path = self.env / 'world.py'
        path.write_text(f'#!{sys.executable}\n' + world)
        path.chmod(0o755)
        member = self.env / 'member.py'
        member.write_text('import json,os,time\nfrom pathlib import Path\n'
            f'time.sleep({member_delay!r})\n'
            'Path(os.environ["ASYS_RESULT"]).write_text(json.dumps({"actions":[{"add":1}],"memory":None}))\n')
        config = {'version': 1, 'world': {'package': 'worlds/test', 'timeoutSeconds': 1},
                  'agents': {'count': 2, 'type': 'member'},
                  'limits': {'turns': turns, 'concurrency': 2, 'seconds': 30, 'tickSeconds': 0}}
        definition = self.env / 'population.json'
        write_json(definition, self.definition('swarm', config))
        write_json(self.env / 'workers.json', {'version': 1, 'name': 'test', 'types': {
            'population': {'command': [str(TOOL), '--definition', str(definition)]},
            'member': {'command': [sys.executable, str(member)]}}})
        write_json(self.job / 'input.json', {'request': 'Improve the measured result'})
        self.world_root = self.runtime / 'worlds/test'
        self.world_root.mkdir(parents=True, exist_ok=True)
        binding = self.base / 'world-bindings.json'
        write_json(binding, {'version': 1, 'packages': {'worlds/test': {'runtime': 'worlds/test'}}})
        self.environment['ASYS_WORLD_BINDINGS'] = str(binding)
        self.component = subprocess.Popen([sys.executable, str(path)],
            env={**self.environment, 'ASYS_WORLD_ROOT': str(self.world_root),
                 'ASYS_WORLD_HEALTH_FILE': str(self.base / 'world-health')},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.children.append(self.component)
        self.wait(lambda: (self.world_root / 'ready.json').exists())
        return definition, config

    def start(self, definition):
        child = subprocess.Popen([str(TOOL), '--definition', str(definition)], env=self.environment,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.children.append(child)
        return child

    def test_swarm_uses_private_channels_and_model_defaults(self):
        _, config = self.swarm()
        command, env = self.prepare('swarm', config, model='chosen/model')
        value = read_json(env['ASYS_INPUT'])
        self.assertEqual(value['channel'], 'swarm-job-one')
        self.assertEqual(value['config']['world']['channel'], 'world-job-one')
        self.assertEqual(value['config']['mission'], 'Find a useful result')
        self.assertEqual(read_json(env['ASYS_SYSTEM_MODELS']), {'simple': 'chosen/model'})
        self.assertEqual(read_json(self.job / 'worker.json')['world_channel'], 'world-job-one')

    def test_named_swarm_is_one_runtime_job_using_a_separate_component(self):
        _, _ = self.swarm()
        queue = Queue(self.runtime / 'environments/test')
        with Environment(self.env).register(self.runtime):
            queue.submit('population', 'job-one', directory=self.job, workspace=self.workspace,
                         input={'request': 'Improve the measured result'})
            completed = subprocess.run([sys.executable, str(ROOT / 'asys-runtime/tools/asys-runtime'),
                'run', str(self.env), '--root', str(self.runtime), '--once'],
                env={**os.environ, 'PYTHONPATH': self.environment['PYTHONPATH'],
                     'ASYS_WORLD_BINDINGS': self.environment['ASYS_WORLD_BINDINGS']}, capture_output=True, text=True, timeout=15)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            state = queue.state('job-one')
            self.assertEqual(state['status'], 'done', state)
            self.assertEqual(state['result']['output']['decisions'], 2)
            self.assertEqual([row['id'] for row in queue.list()], ['job-one'])
        checkpoint = read_json(self.job / 'swarm/checkpoint.json')
        self.assertEqual(checkpoint['world']['total'], 2)
        self.assertEqual(checkpoint['controlChannel'], 'swarm-job-one')
        self.assertFalse((self.job / 'swarm/world').exists())
        self.assertIsNone(self.component.poll())
        self.assertTrue((self.world_root / 'channels/world-job-one/.world-response.json').is_file())

    def test_swarm_recovery_preserves_view_generation_while_new_jobs_select_current_view(self):
        _, config = self.swarm()
        path = Path(self.environment['ASYS_WORLD_BINDINGS'])
        bindings = read_json(path)
        bindings['packages']['worlds/test']['view'] = 'a' * 64
        write_json(path, bindings)
        self.prepare('swarm', config)
        self.assertEqual(read_json(self.job / 'worker.json')['world_view'], 'a' * 64)
        bindings['packages']['worlds/test']['view'] = 'b' * 64
        write_json(path, bindings)
        self.prepare('swarm', config)
        self.assertEqual(read_json(self.job / 'worker.json')['world_view'], 'a' * 64)
        new_job = self.base / 'new-job'
        prepare(self.definition('swarm', config), {'request': 'Explore'},
                {**self.environment, 'ASYS_JOB_ID': 'job-two', 'ASYS_JOB_DIR': str(new_job)})
        self.assertEqual(read_json(new_job / 'worker.json')['world_view'], 'b' * 64)

    def test_swarm_recovery_keeps_legacy_view_lookup_and_rejects_invalid_generation(self):
        from asys_swarm.world_binding import bound_world_root
        _, config = self.swarm()
        self.prepare('swarm', config)
        path = Path(self.environment['ASYS_WORLD_BINDINGS'])
        bindings = read_json(path)
        bindings['packages']['worlds/test']['view'] = 'a' * 64
        write_json(path, bindings)
        self.prepare('swarm', config)
        self.assertNotIn('world_view', read_json(self.job / 'worker.json'))
        for value in ('../view', '', 'not-a-digest', None, 123):
            bindings['packages']['worlds/test']['view'] = value
            write_json(path, bindings)
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'generation ID'):
                bound_world_root(self.runtime, config, self.environment)

    def test_cancel_stops_members_and_leaves_shared_component_running(self):
        definition, _ = self.swarm(member_delay=10)
        child = self.start(definition)
        self.wait(lambda: list((self.job / 'swarm/decisions').glob('*/state.json')))
        Writer(direction_root(self.runtime, 'swarm-job-one', 'in')).send('cancel', {'id': 'job-one'})
        self.assertEqual(child.wait(timeout=8), 0)
        self.assertEqual(read_json(self.job / 'result.json')['status'], 'cancelled')
        self.assertIsNone(self.component.poll())
        self.assertTrue(all(read_json(path)['status'] in {'cancelled', 'interrupted'}
                            for path in (self.job / 'swarm/decisions').glob('*/state.json')))

    def test_two_named_jobs_share_one_component_with_isolated_sessions(self):
        definition, _ = self.swarm(member_delay=0.2)
        first = self.start(definition)
        second_job = self.base / 'second-job'
        second_job.mkdir()
        write_json(second_job / 'input.json', {'request': 'A separate assignment'})
        environment = {**self.environment, 'ASYS_JOB_ID': 'job-two', 'ASYS_JOB_DIR': str(second_job),
                       'ASYS_INPUT': str(second_job / 'input.json'), 'ASYS_RESULT': str(second_job / 'result.json')}
        second = subprocess.Popen([str(TOOL), '--definition', str(definition)], env=environment,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.children.append(second)
        for child, directory in ((first, self.job), (second, second_job)):
            self.assertEqual(child.wait(timeout=10), 0)
            self.assertEqual(read_json(directory / 'swarm/checkpoint.json')['world']['total'], 2)
        self.assertNotEqual(read_json(self.job / 'worker.json')['world_channel'],
                            read_json(second_job / 'worker.json')['world_channel'])

    def test_world_failure_during_member_inference_preserves_committed_world(self):
        definition, _ = self.swarm(member_delay=0.2)
        child = self.start(definition)
        self.wait(lambda: list((self.job / 'swarm/decisions').glob('*/state.json')))
        self.component.kill()
        self.component.wait(timeout=5)
        self.assertEqual(child.wait(timeout=8), 1)
        checkpoint = read_json(self.job / 'swarm/checkpoint.json')
        self.assertEqual(checkpoint['status'], 'failed')
        self.assertEqual(checkpoint['world']['total'], 0)
        self.assertEqual(checkpoint['result']['output']['artifacts'], {})

    def test_worker_crash_keeps_component_and_resumes_completed_checkpoint(self):
        definition, _ = self.swarm(member_delay=0.3, turns=2)
        child = self.start(definition)
        checkpoint = self.job / 'swarm/checkpoint.json'
        self.wait(lambda: checkpoint.exists() and read_json(checkpoint)['turn'] >= 1)
        child.kill()
        child.wait(timeout=5)
        self.assertIsNone(self.component.poll())
        completed_before = {path.parent.name for path in (self.job / 'swarm/decisions').glob('*/state.json')
                            if read_json(path)['status'] == 'done'}
        resumed = self.start(definition)
        self.assertEqual(resumed.wait(timeout=10), 0, read_json(self.job / 'result.json'))
        self.assertEqual(read_json(checkpoint)['world']['total'], 4)
        self.assertIsNone(self.component.poll())
        self.assertFalse((self.job / 'swarm/world').exists())
        events = [json.loads(line) for line in (self.job / 'swarm/events.jsonl').read_text().splitlines()]
        for identity in completed_before:
            self.assertEqual(sum(event['type'] == 'decision.completed' and event['data']['jobId'] == identity
                                 for event in events), 1)

    def test_missing_binding_fails_without_launching_a_world(self):
        definition, _ = self.swarm()
        self.environment['ASYS_WORLD_BINDINGS'] = str(self.base / 'missing.json')
        child = self.start(definition)
        self.assertEqual(child.wait(timeout=5), 1)
        self.assertIn('bindings are missing', read_json(self.job / 'result.json')['exception'])
        self.assertFalse((self.job / 'swarm/world').exists())
        self.assertIsNone(self.component.poll())

    def test_binding_cannot_escape_runtime_or_follow_symlinks(self):
        from asys_swarm.world_binding import bound_world_root
        _, config = self.swarm()
        path = Path(self.environment['ASYS_WORLD_BINDINGS'])
        for runtime in ('/tmp/elsewhere', '../escape', 'worlds/../escape', 'worlds/test/child'):
            write_json(path, {'version': 1, 'packages': {'worlds/test': {'runtime': runtime}}})
            with self.subTest(runtime=runtime), self.assertRaises(ValueError):
                bound_world_root(self.runtime, config, self.environment)
        (self.runtime / 'worlds/link').symlink_to(self.world_root, target_is_directory=True)
        write_json(path, {'version': 1, 'packages': {'worlds/test': {'runtime': 'worlds/link'}}})
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            bound_world_root(self.runtime, config, self.environment)


if __name__ == '__main__':
    unittest.main()
