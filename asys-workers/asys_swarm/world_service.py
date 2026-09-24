"""Optional Python SDK for the language-independent Runtime world protocol.

Supply an explicitly imported callbacks object. Callbacks must be deterministic
and stateless: retries can repeat one after a process dies before its response
cache reaches durable storage. No operation selects code or an executable.
"""
import fcntl
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import time

from asys_runtime.channel import Reader, Writer, channel_root, direction_root
from asys_runtime.files import read_json, write_json

from .config import digest, json_value
from . import world_protocol as protocol


class Service:
    def __init__(self, root, callbacks, *, identity, channel='world'):
        self.implementation = protocol.identity(identity)
        for name in protocol.CALLBACKS:
            if not callable(getattr(callbacks, name, None)):
                raise ValueError(f'World callbacks must define {name}()')
        self.callbacks = callbacks
        self.schema = protocol.action_schema(callbacks.action_schema())
        self.identity = {'implementation': self.implementation, 'actionSchema': digest(self.schema)}
        self.requests = Reader(direction_root(root, channel, 'out'))
        self.responses = Writer(direction_root(root, channel, 'in'))
        self.directory = channel_root(root, channel)
        self.cache_path = self.directory / '.world-response.json'
        self.cache = read_json(self.cache_path) if self.cache_path.exists() else None
        if self.cache is not None and self.cache.get('identity') != self.identity:
            self.cache = None

    def _error(self, request, code, message):
        method = request.get('method', '')
        return {'version': protocol.VERSION, 'runId': request['runId'], 'requestId': request['requestId'],
                'method': method[:64] if isinstance(method, str) else '', 'identity': self.implementation,
                'ok': False, 'error': {'code': code, 'message': str(message)[:2000]}}

    def _answer(self, value, cancelled):
        try:
            request = protocol.request(value)
        except (ValueError, TypeError) as error:
            protocol.correlation(value)  # No meaningful reply without valid IDs.
            return self._error(value, 'invalid_request', error)
        key = {name: request[name] for name in ('runId', 'requestId')}
        fingerprint = digest(request)
        if self.cache is not None and self.cache['correlation'] == key:
            if self.cache['requestHash'] != fingerprint:
                return self._error(request, 'request_conflict', 'Request ID was reused with different input')
            return self.cache['response']
        try:
            if (request['runId'], request['requestId']) in cancelled:
                response = self._error(request, 'cancelled', 'Request cancelled before execution')
            elif request['method'] != 'describe' and request['identity'] != self.implementation:
                response = self._error(request, 'identity_changed', 'World implementation differs from the handshake')
            else:
                if request['method'] == 'describe':
                    result = {'identity': self.implementation, 'actionSchema': self.schema}
                elif request['method'] == 'action_schema':
                    result = self.schema
                else:
                    result = getattr(self.callbacks, request['method'])(*request['arguments'])
                response = {**{name: request[name] for name in ('version', 'runId', 'requestId', 'method')},
                    'identity': self.implementation, 'ok': True,
                    'result': protocol.result(request['method'], result)}
            protocol.response(response)
            json_value({'identity': self.identity, 'correlation': key, 'requestHash': fingerprint,
                        'response': response}, limit=protocol.WIRE_BYTES)
        except Exception as error:
            response = self._error(request, 'world_error', f'{type(error).__name__}: {error}')
        self.cache = {'identity': self.identity, 'correlation': key,
                      'requestHash': fingerprint, 'response': response}
        write_json(self.cache_path, self.cache)  # Durable before response publication.
        return response

    def pump(self):
        events = self.requests.read(limit=32)
        cancelled = set()
        for event in events:
            if event['type'] == 'world.cancel':
                try:
                    cancelled.add(protocol.correlation(event['data']))
                except (ValueError, TypeError):
                    pass
        for event in events:
            if event['type'] == 'world.request':
                try:
                    response = self._answer(event['data'], cancelled)
                except (ValueError, TypeError):
                    response = None  # malformed uncorrelatable input
                if response is not None:
                    protocol.send(self.responses, 'world.response', response)
            self.requests.advance(event['sequence'])
            self.requests.prune()
        return len(events)

    def serve(self, stop=None):
        # One consumer owns a world channel; process death releases the lease.
        with (self.directory / '.world-service.lock').open('a+b') as lease:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError('Another world service owns this channel') from error
            while stop is None or not stop():
                if not self.pump():
                    time.sleep(0.02)


class Component:
    """Multiplex independent job sessions in one separately deployed component.

    Each session has its own runtime cursors, response cache, and exclusive
    consumer lease. At most ``concurrency`` callback batches run simultaneously;
    callbacks therefore must be stateless and safe to call concurrently.
    A slow session does not block the component's readiness or other sessions.
    """
    def __init__(self, root, callbacks, *, identity, concurrency=8, health_file=None):
        if type(concurrency) is not int or not 1 <= concurrency <= 64:
            raise ValueError('World component concurrency must be an integer between 1 and 64')
        self.root = Path(root).resolve()
        self.callbacks = callbacks
        self.identity = protocol.identity(identity)
        self.concurrency = concurrency
        self.health_file = Path(health_file or os.environ.get('ASYS_WORLD_HEALTH_FILE', '/tmp/asys-world-health'))
        # Validate the implementation before declaring the component ready.
        for name in protocol.CALLBACKS:
            if not callable(getattr(callbacks, name, None)):
                raise ValueError(f'World callbacks must define {name}()')
        protocol.action_schema(callbacks.action_schema())

    def _pump(self, channel):
        directory = channel_root(self.root, channel)
        with (directory / '.world-service.lock').open('a+b') as lease:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError('Another world service owns this session') from error
            return Service(self.root, self.callbacks, identity=self.identity, channel=channel).pump()

    def serve(self, stop=None):
        self.root.mkdir(parents=True, exist_ok=True)
        channels = self.root / 'channels'
        channels.mkdir(exist_ok=True)
        ready = self.root / 'ready.json'
        with (self.root / '.world-component.lease').open('a+b') as lease:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError('Another world component owns this runtime root') from error
            pending = {}
            last_health = 0
            try:
                write_json(ready, {'protocolVersion': protocol.VERSION, 'identity': self.identity})
                with ThreadPoolExecutor(max_workers=self.concurrency, thread_name_prefix='world-session') as pool:
                    while stop is None or not stop():
                        for name, future in list(pending.items()):
                            if future.done():
                                future.result()  # Protocol/storage corruption must fail component health.
                                del pending[name]
                        if time.monotonic() - last_health >= 1:
                            self.health_file.parent.mkdir(parents=True, exist_ok=True)
                            self.health_file.touch()
                            last_health = time.monotonic()
                        for path in sorted(channels.iterdir()):
                            if len(pending) >= self.concurrency:
                                break
                            if path.name in pending or not path.name.startswith('world-'):
                                continue
                            if path.is_symlink() or not path.is_dir():
                                raise ValueError('World session must be a regular directory')
                            reader = Reader(direction_root(self.root, path.name, 'out'))
                            if reader.last() > reader.cursor:
                                pending[path.name] = pool.submit(self._pump, path.name)
                        time.sleep(0.02)
            finally:
                ready.unlink(missing_ok=True)
                self.health_file.unlink(missing_ok=True)
