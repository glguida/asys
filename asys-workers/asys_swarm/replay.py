"""Replay recorded transitions through a world channel, without model calls."""
import json
from pathlib import Path
import uuid

from .config import digest, validate_config
from .world import World


def replay(root, config, trace, *, package=None, poll=None):
    config = validate_config(config, package)
    world = None
    agents = [f'agent-{i + 1:03d}' for i in range(config['agents']['count'])]
    turns = 0
    started = False
    terminal = None
    run_id = None
    sequence = 0
    with Path(trace).open(encoding='utf-8') as stream:
        for line in stream:
            row = json.loads(line)
            data = row['data']
            if row.get('sequence') != sequence + 1:
                raise ValueError('Replay event sequence is missing or out of order')
            sequence += 1
            if row['type'] == 'swarm.started':
                if started or data['config'] != config:
                    raise ValueError('Replay configuration differs from the trace')
                world = World(root, config, 'replay-' + uuid.uuid4().hex, poll=poll)
                if data.get('worldIdentity') != world.identity:
                    raise ValueError('Replay world identity differs from the trace')
                state = world.call('initialize', config['world']['settings'], agents, config['seed'])
                if data['stateHash'] != digest(state):
                    raise ValueError('Initial world state differs from the trace')
                started = True
                run_id = data['runId']
            elif not started or data.get('runId') != run_id:
                raise ValueError('Replay event belongs to an unknown run')
            elif row['type'] == 'swarm.tick':
                if terminal is not None or data['turn'] != turns + 1:
                    raise ValueError('Replay turns are missing or out of order')
                state = world.call('step', state, data['actions'])['state']
                if digest(state) != data['stateHash']:
                    raise ValueError(f"Replay diverged at turn {data['turn']}")
                turns += 1
            elif row['type'] == 'run.result':
                if terminal is not None:
                    raise ValueError('Replay contains duplicate terminal results')
                evaluation = world.evaluation(state, config['objective'])
                expected = evaluation['achieved'] if data['status'] == 'completed' else False
                output = data['output']
                if (data['status'] not in {'completed', 'failed', 'cancelled'}
                    or output['turns'] != turns or output['achieved'] is not expected
                    or output['metrics'] != evaluation['metrics']
                    or (output['reason'] == 'objective' and expected is not True)):
                    raise ValueError('Replay result contradicts the evaluated world')
                if data['status'] == 'completed':
                    # Exhausted wall time forbids another world RPC during
                    # termination; the worker records an empty artifact export.
                    artifacts = {} if output['reason'] == 'time_limit' else world.call('artifacts', state)
                    if output['artifacts'] != artifacts:
                        raise ValueError('Replay artifacts differ from the recorded result')
                terminal = data
    if not started:
        raise ValueError('Trace contains no swarm.started event')
    return {'verified': True, 'turns': turns, 'stateHash': digest(state),
            'evaluation': world.evaluation(state, config['objective']), 'terminal': terminal is not None}
