"""Swarm-side client for a separately running world over Runtime channels."""
import time
import uuid

from jsonschema import Draft7Validator

from asys_runtime.channel import Reader, Writer, direction_root
from asys_runtime.files import validate_name


from .config import digest, json_value
from . import world_protocol as protocol


class WorldError(RuntimeError):
    """A correlated world operation returned an explicit error."""


class World:
    def __init__(self, root, config, run_id, *, poll=None):
        validate_name('world run ID', run_id)
        settings = config['world']
        self.run_id = run_id
        self.timeout = settings.get('timeoutSeconds', 30)
        self.poll = poll
        channel = settings.get('channel', 'world')
        self.requests = Writer(direction_root(root, channel, 'out'))
        self.responses = Reader(direction_root(root, channel, 'in'))
        self.implementation = None
        description = self._exchange('describe', [])
        if not isinstance(description, dict) or set(description) != {'identity', 'actionSchema'}:
            raise ValueError('World describe must return identity and actionSchema')
        self.implementation = protocol.identity(description['identity'])
        self.schema = protocol.action_schema(description['actionSchema'])
        self.identity = {'implementation': self.implementation, 'actionSchema': digest(self.schema)}
        self.validator = Draft7Validator(self.schema)

    def _exchange(self, method, args):
        request = protocol.request({'version': protocol.VERSION, 'runId': self.run_id,
            'requestId': uuid.uuid4().hex, 'method': method, 'arguments': list(args),
            'identity': self.implementation})
        deadline = time.monotonic() + self.timeout
        protocol.send(self.requests, 'world.request', request)
        try:
            while True:
                if self.poll is not None:
                    self.poll()
                for event in self.responses.read(limit=32):
                    # Acknowledge late replies without accepting another run's
                    # result; malformed replies fail this operation visibly.
                    self.responses.advance(event['sequence'])
                    self.responses.prune()
                    if event['type'] != 'world.response':
                        raise ValueError('Unexpected event on world response channel')
                    value = protocol.response(event['data'])
                    if any(value[key] != request[key] for key in ('runId', 'requestId', 'method')):
                        continue
                    if self.implementation is not None and value['identity'] != self.implementation:
                        raise ValueError('World implementation identity changed during execution')
                    if not value['ok']:
                        raise WorldError(f"World {method}: {value['error']['code']}: {value['error']['message']}")
                    if method == 'describe' and (not isinstance(value['result'], dict)
                            or value['result'].get('identity') != value['identity']):
                        raise ValueError('World describe identity differs from response identity')
                    return protocol.result(method, value['result'])
                if time.monotonic() >= deadline:
                    raise TimeoutError(f'World {method} exceeded {self.timeout:g} seconds')
                time.sleep(min(0.02, max(0, deadline - time.monotonic())))
        except BaseException:
            # Queued work can be skipped. A running stateless callback may
            # finish, but the client never waits for its cancellation or reply.
            try:
                protocol.send(self.requests, 'world.cancel', {key: request[key]
                    for key in ('version', 'runId', 'requestId')})
            except (OSError, ValueError):
                pass
            raise

    def call(self, name, *args):
        if name not in protocol.CALLBACKS:
            raise ValueError('Unknown world callback')
        return self._exchange(name, args)

    def evaluation(self, state, objective):
        value = self.call('evaluate', state, objective or {})
        if not isinstance(value, dict) or type(value.get('achieved')) is not bool:
            raise ValueError('evaluate() must return an object with boolean achieved')
        if not isinstance(value.get('metrics'), dict) or not isinstance(value.get('summary'), str):
            raise ValueError('evaluate() must return metrics (object) and summary (text)')
        if objective is None:
            value['achieved'] = None
        return value

    def decision(self, result, *, max_actions, memory_bytes):
        if not isinstance(result, dict) or not isinstance(result.get('actions'), list) or 'memory' not in result:
            raise ValueError('Decision must contain actions and memory')
        if len(result['actions']) > max_actions:
            raise ValueError('Decision exceeds its action limit')
        json_value(result['memory'], limit=memory_bytes, indented=False)
        for action in result['actions']:
            self.validator.validate(action)
        usage = result.get('usage', {})
        if not isinstance(usage, dict):
            raise ValueError('Decision usage must be an object')
        for key in ('input', 'output', 'totalTokens'):
            if key in usage and (type(usage[key]) is not int or usage[key] < 0):
                raise ValueError('Decision token counts must be nonnegative integers')
        return json_value(result)
