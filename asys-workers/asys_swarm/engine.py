"""One runtime job owns population, private memories, turns, and local decisions."""
from copy import deepcopy
import json
import os
from pathlib import Path
import time
import uuid

from asys_runtime.channel import Reader, Writer, direction_root
from asys_runtime.environment import Environment
from asys_runtime.files import read_json, sync_directory, timestamp, validate_name, write_json
from asys_runtime.permissions import mkdir

from .config import digest, json_value, validate_config
from .limits import (ACTIVE_CHECKPOINT_BYTES, ERROR_CHARACTERS,
                     PUBLICATION_CHECKPOINT_BYTES, TERMINAL_BASE_BYTES)
from .world import World
from .executor import Executor, TERMINAL as JOB_TERMINAL


TERMINAL = {'completed', 'failed', 'cancelled'}


class TimeLimit(TimeoutError):
    """The swarm budget expired, including time spent waiting on the world."""


class Engine:
    def __init__(self, root, state, workspace, environment, *, channel='swarm', external=None,
                 executor_factory=Executor, world_factory=World, world_root=None,
                 stopping=lambda: False, health=lambda: None):
        self.root, self.directory, self.workspace = (
            Path(path).resolve() for path in (root, state, workspace))
        self.environment = Environment(environment, external=external)
        self.world_factory = world_factory
        self.world_root = Path(world_root).resolve() if world_root is not None else self.root
        self.channel = channel
        self.stopping = stopping
        self.health = health
        self.cancel_requested = False
        self.finishing = False
        self.starting_id = None
        self.starting_config = None
        self.start_deadline = None
        mkdir(self.directory, parents=True, exist_ok=True)
        if not self.workspace.is_dir():
            raise ValueError('Workspace must be a prepared directory')
        self.inbound = Reader(direction_root(self.root, channel, 'in'))
        self.outbound = Writer(direction_root(self.root, channel, 'out'))
        self.checkpoint = self.directory / 'checkpoint.json'
        self.journal = self.directory / 'events.jsonl'
        self.state = read_json(self.checkpoint) if self.checkpoint.exists() else None
        self.world = None
        self.queue = executor_factory(self.directory / 'decisions', self.environment, self.root)
        self.last_tick = 0.0
        self.published = self._published_sequence()
        self.recorded = self._recorded_sequence()
        if self.state:
            if self.state.get('version') != 2:
                raise ValueError('Checkpoint version differs; cannot recover')
            if self.state.get('controlChannel') != channel:
                raise ValueError('Checkpoint control channel differs; cannot recover')
            if self.state['config']['world']['channel'] == channel:
                raise ValueError('World and swarm control channels must be different')
            descriptor = self.environment.descriptor
            if descriptor['definition'] != self.state['environmentDefinition']:
                raise ValueError('Worker environment definition differs; cannot recover')
            self.flush()

    def world_poll(self):
        """Controls remain responsive while the external world handles an RPC."""
        self.controls()
        if self.cancel_requested or self.stopping():
            raise InterruptedError('Swarm interrupted while waiting for the world')
        deadline = self.state['deadline'] if self.state else self.start_deadline
        if deadline is not None and time.time() >= deadline:
            raise TimeLimit('Swarm time limit expired while waiting for the world')
        self.health()

    def _published_sequence(self):
        if not self.state:
            return 0  # A new job has its own journal sequence on a reused channel.
        last = self.outbound.last()
        if not last:
            return 0
        data = self.outbound.event(last)['data']
        if data.get('runId') != self.state['id']:
            raise ValueError('Control channel belongs to another swarm run; cannot recover this checkpoint')
        return data.get('store', 0)

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
                if event['type'].startswith('decision.'):
                    data = {key: item for key, item in event['data'].items() if key != 'actions'}
                    print(json.dumps({'type': event['type'], 'time': event['time'], **data}), flush=True)
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
        config = validate_config(data.get('config'))
        self.starting_id, self.starting_config = data['id'], config
        self.start_deadline = self.state['deadline'] if self.state else time.time() + config['limits']['seconds']
        if config['world']['channel'] == self.channel:
            raise ValueError('World and swarm control channels must be different')
        environment = self.environment.name
        descriptor = self.environment.descriptor
        if config['agents']['type'] not in descriptor['types']:
            raise ValueError(f"Environment does not declare {config['agents']['type']}")
        command = self.environment.types[config['agents']['type']]['command']
        if (config['agents']['type'] == 'swarm' or any(Path(argument).name == 'asys-swarm' for argument in command)
                or any(command[index:index + 2] == ['-m', 'asys_swarm'] for index in range(len(command) - 1))):
            raise ValueError('A swarm member type cannot recursively run the swarm worker')
        if any(Path(argument).name == 'asys-worker' for argument in command) and '--definition' in command:
            path = Path(command[command.index('--definition') + 1])
            if not path.is_absolute():
                path = self.environment.workers_directory / path
            if read_json(path).get('kind') == 'swarm':
                raise ValueError('A swarm member type cannot recursively run a named swarm worker')
        expired = self.state and (self.state['status'] in TERMINAL or time.time() >= self.start_deadline)
        if self.world is None and not expired:
            self.world = self.world_factory(self.world_root, config, data['id'], poll=self.world_poll)
            if self.state and self.world.identity != self.state['worldIdentity']:
                raise ValueError('World service identity differs; cannot recover')
        identity = self.state['worldIdentity'] if expired else self.world.identity
        signature = digest({'config': config, 'environment': environment,
                            'definition': descriptor['definition'], 'world': identity,
                            'channel': self.channel})
        if self.state:
            if self.state['id'] != data['id'] or self.state['signature'] != signature:
                raise ValueError('This controller already owns a different run')
            return
        agents = [f'agent-{i + 1:03d}' for i in range(config['agents']['count'])]
        state = self.world.call('initialize', config['world']['settings'], agents, config['seed'])
        if not isinstance(state, dict):
            raise ValueError('initialize() must return a JSON object')
        evaluation = self.world.evaluation(state, config['objective'])
        initial = {'version': 2, 'id': data['id'], 'signature': signature,
            'worldIdentity': self.world.identity, 'controlChannel': self.channel,
            'config': config, 'environment': environment,
            'environmentDefinition': descriptor['definition'], 'status': 'running',
            'turn': 0, 'decisions': 0, 'world': state, 'agents': agents,
            'memories': {agent: None for agent in agents}, 'plans': {agent: [] for agent in agents},
            'pending': None, 'evaluation': evaluation, 'startedAt': timestamp(),
            'deadline': self.start_deadline, 'pauseRequested': False,
            'usage': {'input': 0, 'output': 0, 'totalTokens': 0}, 'sequence': 0, 'outbox': []}
        self.validate_active(initial)
        self.state = initial
        self.event('swarm.started', {'mission': config['mission'], 'objective': config['objective'],
            'config': config, 'worldIdentity': self.world.identity, 'stateHash': digest(state)})
        self.snapshot()
        self.flush()

    def controls(self):
        for event in self.inbound.read(limit=100):
            try:
                data = event['data']
                if not isinstance(data, dict):
                    raise ValueError('Control data must be an object')
                if not self.state and event['type'] == 'cancel' and data.get('id') == self.starting_id:
                    self.cancel_requested = True
                    self.outbound.send('accepted', {'request': event['sequence'], 'runId': self.starting_id,
                                                   'status': 'initializing', 'store': self.published})
                    self.inbound.advance(event['sequence'])
                    continue
                if event['type'] != 'start':
                    if not self.state or data.get('id') != self.state['id']:
                        raise ValueError('Unknown swarm run')
                    kind = event['type']
                    if kind not in {'pause', 'resume', 'cancel', 'snapshot'}:
                        raise ValueError(f'Unknown swarm control {kind}')
                    if self.state['status'] not in TERMINAL:
                        if kind == 'cancel':
                            self.cancel_requested = True
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
                else:
                    raise ValueError('The runtime job input starts this swarm; start is not a control action')
                self.outbound.send('accepted', {'request': event['sequence'], 'runId': self.state['id'],
                    'status': self.state['status'], 'store': self.published})
            except Exception as error:
                self.outbound.send('rejected', {'request': event['sequence'], 'message': str(error),
                                               'runId': self.state['id'] if self.state else self.starting_id,
                                               'store': self.published})
            self.inbound.advance(event['sequence'])

    def pump(self):
        """Make progress without blocking host cancellation on model completion."""
        self.controls()
        if not self.state or self.state['status'] in TERMINAL:
            return
        try:
            if self.stopping():
                raise InterruptedError('Swarm worker interrupted')
            if self.cancel_requested:
                self.finish('cancelled', 'cancelled', 'Cancelled by the host')
                return
            if time.time() >= self.state['deadline']:
                self.finish('completed', 'time_limit')
                return
            self.health()
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
        except TimeLimit:
            self.finish('completed', 'time_limit')
        except InterruptedError:
            if self.stopping():
                raise
            if self.cancel_requested:
                self.finish('cancelled', 'cancelled', 'Cancelled by the host')
            else:
                raise
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
        directory = self.directory / 'decisions' / job_id
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
            # The executor persists local attempt identity before spawning; a
            # crash before checkpoint update cannot duplicate that attempt.
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
        self.queue.close()
        value['pending'] = None
        artifacts = {}
        try:
            self.finishing = True
            if status == 'completed' and reason != 'time_limit':
                artifacts = self.world.call('artifacts', value['world'])
            if not isinstance(artifacts, dict):
                raise ValueError('artifacts() must return an object')
        except TimeLimit:
            status, reason, error, artifacts = 'completed', 'time_limit', '', {}
        except InterruptedError:
            if self.stopping() or not self.cancel_requested:
                raise
            status, reason, error, artifacts = 'cancelled', 'cancelled', 'Cancelled by the host', {}
        except Exception as failure:
            status, reason, error = 'failed', 'error', error or str(failure)
        finally:
            self.finishing = False
        terminal = self.terminal_state(value, status, reason, error, artifacts)
        json_value(terminal, limit=PUBLICATION_CHECKPOINT_BYTES)
        value.update(terminal)
        self.flush()

    def finish_uninitialized(self, status, reason, message):
        """A stopped handshake has no world state or evaluation to fabricate."""
        config = self.starting_config or {}
        output = {'runId': self.starting_id, 'mission': config.get('mission', ''),
                  'achieved': None if status == 'completed' and config.get('objective') is None else False,
                  'reason': reason, 'turns': 0, 'decisions': 0, 'summary': message,
                  'metrics': {}, 'usage': {'input': 0, 'output': 0, 'totalTokens': 0},
                  'artifacts': {}, 'files': {}}
        result = {'runId': self.starting_id, 'status': status, 'output': output, 'error': ''}
        write_json(self.directory / 'result.json', result)
        self.outbound.send('run.result', {**result, 'store': self.published})
        return {'final': message, 'exception': None, **result}

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

    def close(self, *, interrupted=True):
        self.queue.close(interrupted=interrupted)
        self.save()
