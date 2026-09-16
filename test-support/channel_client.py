"""Filesystem client for integration fixtures; never opens a component socket."""
from collections import deque
from contextlib import contextmanager
from pathlib import Path
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'asys-runtime'))
from asys_runtime.channel import Reader, Writer, direction_root


class RPCError(AssertionError):
    pass


class Client:
    def __init__(self, root):
        self.root = Path(root)
        channel = self.root / 'channels/test'
        for path in [self.root, self.root / 'channels', channel, channel / 'in', channel / 'out']:
            path.mkdir(parents=True, exist_ok=True)
            path.chmod(0o777)
        self.input = Writer(direction_root(root, 'test', 'in'))
        self.output = Reader(direction_root(root, 'test', 'out'))
        self.pending = {}
        self.ready = False

    def pump(self):
        for event in self.output.read():
            if event['type'] == 'ready':
                self.ready = True
            else:
                queue = self.pending.get(event['data'].get('id'))
                if queue is not None:
                    queue.append(event)
            self.output.advance(event['sequence'])
        self.output.prune()

    def wait(self, predicate, timeout):
        deadline = time.monotonic() + timeout
        while not predicate():
            self.pump()
            if predicate():
                return
            if time.monotonic() >= deadline:
                raise RPCError('Timed out waiting for test component')
            time.sleep(.025)

    @contextmanager
    def stream(self, service, method, body, timeout=40):
        self.wait(lambda: self.ready, timeout)
        identifier = uuid.uuid4().hex
        queue = self.pending[identifier] = deque()
        ended = False
        self.input.send('call', {'id': identifier, 'service': service, 'method': method, 'body': body})

        def receive():
            nonlocal ended
            if ended:
                raise StopIteration
            self.wait(lambda: bool(queue), timeout)
            event = queue.popleft()
            if event['type'] == 'error':
                raise RPCError(f"{service}.{method}: {event['data']['message']}")
            if event['type'] == 'end':
                ended = True
                raise StopIteration
            return event['data']['value']

        try:
            yield receive
        finally:
            try:
                if not ended:
                    self.input.send('cancel', {'id': identifier})
                    # Waiting for end proves the container has closed the RPC.
                    while not ended:
                        try:
                            receive()
                        except StopIteration:
                            break
                        except RPCError:
                            if not queue:
                                raise
            finally:
                del self.pending[identifier]

    def call(self, service, method, body, timeout=40):
        with self.stream(service, method, body, timeout) as receive:
            result = receive()
            try:
                receive()
            except StopIteration:
                return result
            raise RPCError(f'{service}.{method}: expected one response')
