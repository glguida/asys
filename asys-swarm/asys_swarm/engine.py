"""Deterministic world turns, parallel runtime decisions, durable host control."""
from copy import deepcopy
import json
import os
from pathlib import Path
import time
import uuid

from asys_runtime.channel import Reader, Writer, direction_root
from asys_runtime.environment import describe, environment_root
from asys_runtime.files import read_json, sync_directory, timestamp, validate_name, write_json
from asys_runtime.permissions import mkdir
from asys_runtime.queue import Queue, TERMINAL as JOB_TERMINAL

from .config import digest, json_value, validate_config
from .limits import (ACTIVE_CHECKPOINT_BYTES, ERROR_CHARACTERS,
                     PUBLICATION_CHECKPOINT_BYTES, TERMINAL_BASE_BYTES)
from .world import World, package_digest


TERMINAL = {'completed', 'failed', 'cancelled'}


class Engine:
    def __init__(self, root, state, workspace, package, *, channel='swarm'):
        self.root, self.directory, self.workspace, self.package = (
            Path(path).resolve() for path in (root, state, workspace, package))
        mkdir(self.directory, parents=True, exist_ok=True)
        if not self.workspace.is_dir():
            raise ValueError('Workspace must be a prepared directory')
        self.inbound = Reader(direction_root(self.root, channel, 'in'))
        self.outbound = Writer(direction_root(self.root, channel, 'out'))
        self.checkpoint = self.directory / 'checkpoint.json'
        self.journal = self.directory / 'events.jsonl'
        self.state = read_json(self.checkpoint) if self.checkpoint.exists() else None
        self.world = None
        self.queue = None
        self.last_tick = 0.0
        self.package_hash = package_digest(self.package)
        self.published = self._published_sequence()
        self.recorded = self._recorded_sequence()
        if self.state:
            if self.state.get('version') != 1 or self.state.get('packageHash') != self.package_hash:
                raise ValueError('Checkpoint version or experiment package differs; cannot recover')
            descriptor = describe(self.root, self.state['environment'])
            if descriptor['definition'] != self.state['environmentDefinition']:
                raise ValueError('Worker environment definition differs; cannot recover')
            self.world = World(self.package, self.state['config'])
            self.queue = Queue(environment_root(self.root, self.state['environment']))
            self.flush()

    def _published_sequence(self):
        last = self.outbound.last()
        return self.outbound.event(last)['data'].get('store', 0) if last else 0

    def _recorded_sequence(self):
        if not self.journal.exists():
            return 0
        # A crash can truncate only the final append. Discard it; the checkpoint
        # outbox retains the full event and will publish it again exactly once.
        last = 0
        with self.journal.open('r+b') as stream:
            while True:
                position = stream.tell()
                line = stream.readline()
                if not line:
                    break
                if not line.endswith(b'\n'):
                    stream.truncate(position)
                    stream.flush()
                    os.fsync(stream.fileno())
                    break
                row = json.loads(line)
                if row.get('sequence') != last + 1:
                    raise ValueError('Invalid swarm journal sequence')
                last = row['sequence']
        return last

    def save(self):
        if self.state:
            json_value(self.state, limit=PUBLICATION_CHECKPOINT_BYTES)
            write_json(self.checkpoint, self.state)

    def event(self, kind, data, *, state=None):
        value = self.state if state is None else state
        value['sequence'] += 1
        value['outbox'].append({'sequence': value['sequence'], 'type': kind,
                               'time': timestamp(), 'data': {'runId': value['id'], **data}})

    def validate_active(self, value):
        json_value(value, limit=ACTIVE_CHECKPOINT_BYTES)
        # Pending jobs disappear at termination, but memories/queued plans and
        # the committed world remain. Ensure a later failure can always publish
        # that state and the bounded artifacts without exceeding runtime files.
        terminal = self.terminal_state({**value, 'outbox': []}, 'failed', 'error',
                                       '\0' * ERROR_CHARACTERS, {})
        json_value(terminal, limit=TERMINAL_BASE_BYTES)

    def flush(self):
        if not self.state:
            return
        self.save()
        for event in self.state['outbox']:
            sequence = event['sequence']
            if sequence > self.recorded:
                with self.journal.open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + '\n')
                    stream.flush()
                    os.fsync(stream.fileno())
                self.recorded = sequence
        # Materialize terminal artifacts before notifying the host, which may
        # immediately tear down the controller after receiving run.result.
        # Repeating this on recovery also repairs a crash during export.
        if self.state['status'] in TERMINAL:
            self.export_result()
        for event in self.state['outbox']:
            sequence = event['sequence']
            if sequence > self.published:
                self.outbound.send(event['type'], {**event['data'], 'store': sequence})
                self.published = sequence
        if self.state['outbox']:
            self.state['outbox'] = []
            self.save()

    def snapshot(self, events=(), *, state=None):
        value = self.state if state is None else state
        self.event('swarm.snapshot', {'turn': value['turn'], 'state': value['world'],
            'metrics': value['evaluation']['metrics'], 'evaluation': value['evaluation'],
            'status': value['status'], 'decisions': value['decisions'], 'usage': value['usage'],
            'events': list(events), 'mission': value['config']['mission'],
            'objective': value['config']['objective']}, state=value)

    def start(self, data):
        validate_name('run ID', data.get('id'))
        config = validate_config(data.get('config'), self.package)
        environment = data.get('environment')
        descriptor = describe(self.root, environment)
        if descriptor['definition'] != data.get('environmentDefinition'):
            raise ValueError('Worker environment definition differs from the selected environment')
        if config['agents']['type'] not in descriptor['types']:
            raise ValueError(f"Environment does not declare {config['agents']['type']}")
        signature = digest({'config': config, 'environment': environment,
                            'definition': descriptor['definition'], 'package': self.package_hash})
        if self.state:
            if self.state['id'] != data['id'] or self.state['signature'] != signature:
                raise ValueError('This controller already owns a different run')
            return
        self.world = World(self.package, config)
        agents = [f'agent-{i + 1:03d}' for i in range(config['agents']['count'])]
        state = self.world.call('initialize', config['world']['settings'], agents, config['seed'])
        if not isinstance(state, dict):
            raise ValueError('initialize() must return a JSON object')
        evaluation = self.world.evaluation(state, config['objective'])
        self.queue = Queue(environment_root(self.root, environment))
        initial = {'version': 1, 'id': data['id'], 'signature': signature,
            'packageHash': self.package_hash, 'config': config, 'environment': environment,
            'environmentDefinition': descriptor['definition'], 'status': 'running',
            'turn': 0, 'decisions': 0, 'world': state, 'agents': agents,
            'memories': {agent: None for agent in agents}, 'plans': {agent: [] for agent in agents},
            'pending': None, 'evaluation': evaluation, 'startedAt': timestamp(),
            'deadline': time.time() + config['limits']['seconds'], 'pauseRequested': False,
            'usage': {'input': 0, 'output': 0, 'totalTokens': 0}, 'sequence': 0, 'outbox': []}
        self.validate_active(initial)
        self.state = initial
        self.event('swarm.started', {'mission': config['mission'], 'objective': config['objective'],
            'config': config, 'packageHash': self.package_hash, 'stateHash': digest(state)})
        self.snapshot()
        self.flush()

    def controls(self):
        for event in self.inbound.read(limit=100):
            try:
                data = event['data']
                if not isinstance(data, dict):
                    raise ValueError('Control data must be an object')
                if event['type'] == 'start':
                    self.start(data)
                else:
                    if not self.state or data.get('id') != self.state['id']:
                        raise ValueError('Unknown swarm run')
                    kind = event['type']
                    if kind not in {'pause', 'resume', 'cancel', 'snapshot'}:
                        raise ValueError(f'Unknown swarm control {kind}')
                    if self.state['status'] not in TERMINAL:
                        if kind == 'cancel':
                            self.finish('cancelled', 'cancelled', 'Cancelled by the host')
                        elif kind == 'pause':
                            self.state['pauseRequested'] = True
                            if self.state['pending'] is None:
                                self.state['status'] = 'paused'
                            self.event('swarm.paused' if self.state['status'] == 'paused' else 'swarm.pause_requested',
                                       {'turn': self.state['turn']})
                            self.snapshot()
                        elif kind == 'resume':
                            self.state['pauseRequested'] = False
                            self.state['status'] = 'running'
                            self.event('swarm.resumed', {'turn': self.state['turn']})
                            self.snapshot()
                    if kind == 'snapshot':
                        self.snapshot()
                    self.flush()
                self.outbound.send('accepted', {'request': event['sequence'], 'runId': self.state['id'],
                    'status': self.state['status'], 'store': self.published})
            except Exception as error:
                self.outbound.send('rejected', {'request': event['sequence'], 'message': str(error),
                                               'store': self.published})
            self.inbound.advance(event['sequence'])

    def pump(self):
        """Make progress without blocking host cancellation on model completion."""
        self.controls()
        if not self.state or self.state['status'] in TERMINAL:
            return
        try:
            if time.time() >= self.state['deadline']:
                self.finish('completed', 'time_limit')
                return
            if self.state['status'] == 'paused':
                return
            if self.state['pending'] is None:
                if self.state['evaluation']['achieved'] is True:
                    self.finish('completed', 'objective')
                    return
                if self.state['turn'] >= self.state['config']['limits']['turns']:
                    self.finish('completed', 'turn_limit')
                    return
                if time.monotonic() - self.last_tick < self.state['config']['limits']['tickSeconds']:
                    return
                if not self.begin_turn():
                    return
            if self.decisions_ready():
                self.commit_turn()
        except Exception as error:
            self.finish('failed', 'error', f'{type(error).__name__}: {error}')

    def begin_turn(self):
        value, world = self.state, self.world
        limits = value['config']['limits']
        decisions, inactive = {}, []
        for agent in value['agents']:
            observation = world.call('observe', value['world'], agent)
            if not isinstance(observation, dict):
                raise ValueError('observe() must return an object')
            if observation.get('active') is False:
                inactive.append(agent)
                continue
            if value['plans'][agent]:
                continue
            decisions[agent] = {'id': uuid.uuid4().hex, 'status': 'planned', 'attempt': 1,
                'input': {'mission': value['config']['mission'], 'objective': value['config']['objective'],
                    'agent': agent, 'turn': value['turn'], 'observation': observation,
                    'memory': value['memories'][agent], 'actionSchema': world.schema,
                    'maxActions': limits['actions'], 'memoryBytes': limits['memoryBytes'],
                    'timeoutSeconds': limits['jobSeconds'], 'options': {'maxTokens': limits['outputTokens']}}}
        if value['decisions'] + len(decisions) > limits['decisions']:
            self.finish('completed', 'decision_limit')
            return False
        pending = {'turn': value['turn'], 'decisions': decisions, 'inactive': inactive}
        self.validate_active({**value, 'pending': pending})
        value['decisions'] += len(decisions)
        value['pending'] = pending
        self.save()  # Persist identities and inputs before publishing any job.
        return True

    def job_paths(self, job_id, agent):
        directory = self.root.parent / 'jobs' / job_id
        workspace = self.workspace / '.asys-swarm' / self.state['id'] / 'agents' / agent
        for path in (directory, workspace):
            mkdir(path, parents=True, exist_ok=True)
        return directory, workspace

    def decisions_ready(self):
        value = self.state
        limits = value['config']['limits']
        decisions = value['pending']['decisions']
        running = 0
        for agent, decision in decisions.items():
            if decision['status'] != 'submitted':
                continue
            state = self.queue.state(decision['id'])
            if state['status'] not in JOB_TERMINAL:
                if time.time() - decision['submittedAt'] > limits['jobSeconds']:
                    self.queue.cancel(decision['id'])
                    raise TimeoutError(f"Decision {decision['id']} exceeded jobSeconds")
                running += 1
                continue
            if state['status'] == 'interrupted' and decision['attempt'] < 3 and value['decisions'] < limits['decisions']:
                previous = decision['id']
                decision.update(id=uuid.uuid4().hex, status='planned', attempt=decision['attempt'] + 1)
                decision.pop('submittedAt', None)
                value['decisions'] += 1
                self.event('decision.retry', {'agent': agent, 'jobId': decision['id'], 'retryOf': previous})
                self.flush()
                continue
            if state['status'] != 'done':
                raise RuntimeError(f"Decision {decision['id']} {state['status']}: {state.get('error', '')}")
            result = self.world.decision(state.get('result'), max_actions=limits['actions'],
                                         memory_bytes=limits['memoryBytes'])
            candidate = deepcopy(value)
            candidate['pending']['decisions'][agent].update(status='completed', result=result)
            self.validate_active(candidate)
            decision.update(status='completed', result=result)
            # Charge completed calls immediately, even if a later agent fails
            # or the host cancels before this turn can be committed.
            for key in value['usage']:
                value['usage'][key] += result.get('usage', {}).get(key, 0)
            self.event('decision.completed', {'agent': agent, 'jobId': decision['id'], 'turn': value['turn'],
                                             'actions': result['actions'], 'usage': result.get('usage', {})})
            self.flush()
        for agent, decision in decisions.items():
            if running >= limits['concurrency']:
                break
            if decision['status'] != 'planned':
                continue
            directory, workspace = self.job_paths(decision['id'], agent)
            # Reusing this job identity with identical inputs is idempotent if a
            # crash occurred between queue publication and checkpoint update.
            self.queue.submit(value['config']['agents']['type'], decision['id'], directory=directory,
                workspace=workspace, input=decision['input'], metadata={
                    'name': f"{agent} turn {value['turn']}", 'run_id': value['id'],
                    'agent_id': agent, 'turn': value['turn'], 'environment': value['environment']})
            decision.update(status='submitted', submittedAt=time.time())
            self.event('decision.submitted', {'agent': agent, 'jobId': decision['id'], 'turn': value['turn']})
            self.flush()
            running += 1
        return all(item['status'] == 'completed' for item in decisions.values())

    def commit_turn(self):
        value = self.state
        pending = value['pending']
        # Work on a copy until the complete world transition and its evaluation
        # have succeeded. An exception leaves the last committed turn intact.
        plans = deepcopy(value['plans'])
        memories = deepcopy(value['memories'])
        for agent, decision in pending['decisions'].items():
            result = decision['result']
            plans[agent], memories[agent] = deepcopy(result['actions']), result['memory']
        for agent in pending['inactive']:
            plans[agent] = []
        actions = {agent: plans[agent].pop(0) for agent in value['agents'] if plans[agent]}
        stepped = self.world.call('step', value['world'], actions)
        if not isinstance(stepped, dict) or not isinstance(stepped.get('state'), dict):
            raise ValueError('step() must return an object with state and events')
        events = stepped.get('events')
        if not isinstance(events, list) or any(not isinstance(event, dict) for event in events):
            raise ValueError('step().events must be an array of objects')
        evaluation = self.world.evaluation(stepped['state'], value['config']['objective'])
        committed = {**value, 'world': stepped['state'], 'plans': plans, 'memories': memories,
                     'evaluation': evaluation, 'turn': value['turn'] + 1, 'pending': None,
                     'outbox': list(value['outbox'])}
        self.validate_active(committed)
        if committed['pauseRequested']:
            committed['status'] = 'paused'
            self.event('swarm.paused', {'turn': committed['turn']}, state=committed)
        self.event('swarm.tick', {'turn': committed['turn'], 'actions': actions, 'events': events,
            'stateHash': digest(committed['world']), 'metrics': evaluation['metrics']}, state=committed)
        self.snapshot(events, state=committed)
        # A large aggregate event batch can exceed the publication budget even
        # when every callback payload fits. Reject it before committing actions.
        json_value(committed, limit=PUBLICATION_CHECKPOINT_BYTES)
        value.update(committed)
        self.last_tick = time.monotonic()
        self.flush()

    def cancel_jobs(self):
        if not self.state or not self.state['pending']:
            return
        for decision in self.state['pending']['decisions'].values():
            if decision['status'] == 'completed':
                continue
            try:
                self.queue.cancel(decision['id'])
            except FileNotFoundError:
                pass  # The pending decision was never submitted.

    def finish(self, status, reason, error=''):
        value = self.state
        if not value or value['status'] in TERMINAL:
            return
        self.cancel_jobs()
        value['pending'] = None
        artifacts = {}
        try:
            artifacts = self.world.call('artifacts', value['world'])
            if not isinstance(artifacts, dict):
                raise ValueError('artifacts() must return an object')
        except Exception as failure:
            status, reason, error = 'failed', 'error', error or str(failure)
        terminal = self.terminal_state(value, status, reason, error, artifacts)
        json_value(terminal, limit=PUBLICATION_CHECKPOINT_BYTES)
        value.update(terminal)
        self.flush()

    def terminal_state(self, value, status, reason, error, artifacts):
        """Construct the entire terminal publication without mutating live state."""
        error = error[:ERROR_CHARACTERS]
        relative = Path('swarm-runs') / value['id']
        evaluation = value['evaluation']
        output = {'runId': value['id'], 'mission': value['config']['mission'],
            'achieved': evaluation['achieved'] if status == 'completed' else False,
            'reason': reason, 'turns': value['turn'], 'decisions': value['decisions'],
            'summary': evaluation['summary'], 'metrics': evaluation['metrics'], 'usage': value['usage'],
            'artifacts': artifacts, 'files': {key: str(relative / name) for key, name in
                [('world', 'world.json'), ('artifacts', 'artifacts.json'), ('result', 'result.json'), ('trace', 'trace.jsonl')]}}
        if error:
            output['error'] = error
        result = {'runId': value['id'], 'status': status, 'output': output, 'error': error}
        terminal = {**value, 'status': status, 'result': result, 'finishedAt': timestamp(),
                    'pending': None, 'outbox': list(value['outbox'])}
        self.event(f'swarm.{status}', {'turn': value['turn'], 'reason': reason, 'achieved': output['achieved']},
                   state=terminal)
        self.snapshot(state=terminal)
        self.event('run.result', result, state=terminal)
        return terminal

    def export_result(self):
        value = self.state
        export = self.workspace / 'swarm-runs' / value['id']
        mkdir(export, parents=True, exist_ok=True)
        result = value['result']
        write_json(export / 'world.json', value['world'])
        write_json(export / 'artifacts.json', result['output']['artifacts'])
        write_json(export / 'result.json', result['output'])
        write_json(self.directory / 'result.json', result)
        self.export_trace(export / 'trace.jsonl')

    def export_trace(self, destination):
        # Copy only after the terminal journal event is durable.
        import shutil
        temporary = destination.with_suffix('.tmp')
        with self.journal.open('rb') as source, temporary.open('wb') as target:
            shutil.copyfileobj(source, target)
            target.flush()
            os.fsync(target.fileno())
        temporary.replace(destination)
        sync_directory(destination.parent)

    def close(self):
        if self.state and self.state['status'] not in TERMINAL:
            self.finish('cancelled', 'cancelled', 'Controller stopped')
