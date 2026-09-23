"""Replay recorded world transitions without any worker or model calls."""
import json
from pathlib import Path

from .config import digest, validate_config
from .world import World, package_digest


def replay(package, config, trace):
    config = validate_config(config, package)
    # This developer helper executes trusted world code in its caller. It is
    # deliberately not part of the host launcher/control surface.
    world = World(package, config)
    agents = [f'agent-{i + 1:03d}' for i in range(config['agents']['count'])]
    state = world.call('initialize', config['world']['settings'], agents, config['seed'])
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
                if started or data['packageHash'] != package_digest(package) or data['config'] != config:
                    raise ValueError('Replay package or configuration differs from the trace')
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
                if data['status'] == 'completed' and output['artifacts'] != world.call('artifacts', state):
                    raise ValueError('Replay artifacts differ from the recorded result')
                terminal = data
    if not started:
        raise ValueError('Trace contains no swarm.started event')
    return {'verified': True, 'turns': turns, 'stateHash': digest(state),
            'evaluation': world.evaluation(state, config['objective']), 'terminal': terminal is not None}
