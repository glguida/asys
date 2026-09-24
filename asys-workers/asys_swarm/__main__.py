"""Run one swarm as an ordinary workers runtime job."""
import argparse
import os
from pathlib import Path
import signal
import time

from asys_runtime.files import acquire_lock, read_json, write_json
from asys_runtime.channel import channel_root

from .engine import Engine, TERMINAL, TimeLimit
from .config import validate_config
from .world_binding import bound_world_root


def runtime_root(environment):
    if environment.get('ASYS_RUNTIME_ROOT'):
        return Path(environment['ASYS_RUNTIME_ROOT']).resolve()
    request = Path(environment['ASYS_REQUEST']).resolve()
    if request.parent.parent.name != 'jobs' or request.parents[3].name != 'environments':
        raise ValueError('Cannot derive the runtime root from ASYS_REQUEST')
    return request.parents[4]


def result_value(state):
    result = state['result']
    return {'final': result['error'] or result['output']['summary'] or f"Swarm {result['status']}.",
            'exception': result['error'] if result['status'] == 'failed' else None, **result}


def main(argv=None, *, environment=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', help='Runtime channel root; normally derived from the outer job')
    args = parser.parse_args(argv)
    environment = os.environ if environment is None else environment
    directory = Path(environment['ASYS_JOB_DIR']).resolve()
    state_directory = directory / 'swarm'
    state_directory.mkdir(parents=True, exist_ok=True)
    lease = acquire_lock(state_directory / 'engine.lease')
    if lease is None:
        raise ValueError('Another swarm worker owns this job checkpoint')
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    engine, output, code = None, None, 1
    channel_leases = []
    try:
        payload = read_json(environment['ASYS_INPUT'])
        if not isinstance(payload, dict) or set(payload) - {'id', 'config', 'channel'}:
            raise ValueError('Swarm job input accepts only id, config, and channel')
        root = args.root or runtime_root(environment)
        configuration = validate_config(payload.get('config'))
        world_root = bound_world_root(root, configuration, environment)
        channels = (payload.get('channel', 'swarm'), configuration['world']['channel'])
        if channels[0] == channels[1]:
            raise ValueError('World and swarm control channels must be different')
        for channel_runtime, channel in ((root, channels[0]), (world_root, channels[1])):
            channel_directory = channel_root(channel_runtime, channel)
            channel_directory.mkdir(parents=True, exist_ok=True)
            client_lease = acquire_lock(channel_directory / '.swarm-client.lease')
            if client_lease is None:
                raise ValueError(f'Another swarm job owns channel {channel}; choose distinct control and world channels')
            channel_leases.append(client_lease)
        definition = Path(environment['ASYS_ENVIRONMENT_DIR']).resolve()
        workers = Path(environment.get('ASYS_WORKERS_DIR', definition)).resolve()
        engine = Engine(root, state_directory,
                        environment['ASYS_WORKSPACE'], definition, channel=payload.get('channel', 'swarm'),
                        external=workers if workers != definition else None, stopping=lambda: stopping,
                        world_root=world_root)
        engine.start(payload)
        while not stopping and engine.state['status'] not in TERMINAL:
            engine.pump()
            time.sleep(0.025)
        if stopping and engine.state['status'] not in TERMINAL:
            raise InterruptedError('Swarm worker interrupted; its checkpoint can be resumed')
        output = result_value(engine.state)
        code = 1 if output['exception'] else 0
    except (InterruptedError, TimeLimit) as error:
        if engine is not None and (engine.cancel_requested or isinstance(error, TimeLimit)) and not stopping:
            cancelled = engine.cancel_requested
            status, reason = ('cancelled', 'cancelled') if cancelled else ('completed', 'time_limit')
            message = 'Swarm cancelled before initialization completed.' if cancelled else str(error)
            if engine.state is None:
                output = engine.finish_uninitialized(status, reason, message)
            else:
                engine.finish(status, reason, message if cancelled else '')
                output = result_value(engine.state)
            code = 0
        else:
            output = {'final': str(error) or 'Swarm worker interrupted.',
                      'exception': f'{type(error).__name__}: {error}', 'status': 'interrupted'}
    except Exception as error:
        output = {'final': str(error) or 'Swarm worker failed.', 'exception': f'{type(error).__name__}: {error}',
                  'status': 'interrupted' if isinstance(error, InterruptedError) else 'failed'}
    finally:
        try:
            if engine is not None:
                engine.close(interrupted=stopping or engine.state is None or engine.state['status'] not in TERMINAL)
            if output is not None:
                write_json(environment['ASYS_RESULT'], output)
        finally:
            for client_lease in channel_leases:
                os.close(client_lease)
            os.close(lease)
            for sig, handler in previous.items():
                signal.signal(sig, handler)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
