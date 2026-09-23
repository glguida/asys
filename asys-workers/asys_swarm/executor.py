"""Local decision supervision inside one runtime-owned swarm process group."""
from contextlib import contextmanager
import ctypes
import fcntl
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'asys-runtime'))

from asys_runtime.files import read_json, timestamp, validate_name, write_json
from asys_runtime.permissions import mkdir


TERMINAL = {'done', 'failed', 'cancelled', 'interrupted'}


@contextmanager
def lease(directory):
    descriptor = os.open(Path(directory) / 'lease', os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield None
        else:
            yield descriptor
    finally:
        os.close(descriptor)


class Executor:
    """Queue-shaped adapter; its entries are local attempts, never runtime jobs."""
    def __init__(self, directory, environment, runtime_root):
        self.directory = Path(directory).resolve()
        self.environment = environment
        self.runtime_root = Path(runtime_root).resolve()
        self.processes = {}
        mkdir(self.directory, parents=True, exist_ok=True)

    def path(self, identity):
        return self.directory / validate_name('decision ID', identity)

    def submit(self, kind, identity, *, directory, workspace, input, metadata):
        path = self.path(identity)
        if Path(directory).resolve() != path:
            raise ValueError('Decision directory must belong to this swarm job')
        if kind not in self.environment.types:
            raise ValueError(f'Environment does not declare {kind}')
        spec = self.environment.types[kind]
        timeout = input['timeoutSeconds']
        if spec['timeout'] is not None:
            timeout = min(timeout, spec['timeout'])
        request = {'version': 1, 'id': identity, 'type': kind, 'directory': str(path),
                   'workspace': str(Path(workspace).resolve()), 'input': input,
                   'metadata': metadata, 'command': spec['command'], 'env': spec['env'],
                   'timeout': timeout, 'runtimeRoot': str(self.runtime_root)}
        state = {'version': 1, 'id': identity, 'type': kind, 'status': 'pending',
                 'agent': metadata['agent_id'], 'turn': metadata['turn'],
                 'directory': str(path), 'workspace': request['workspace'],
                 'submitted_at': timestamp(), 'updated_at': timestamp()}
        mkdir(path, parents=True, exist_ok=True)
        if (path / 'request.json').exists():
            if read_json(path / 'request.json') != request:
                raise ValueError('Decision identity already exists with different input')
            if not (path / 'state.json').exists():
                # A crash can interrupt the request/input/state publication
                # before any subprocess exists. Recover as an interrupted
                # attempt; the engine owns retry identity and budget accounting.
                write_json(path / 'input.json', input)
                state.update(status='interrupted', finished_at=timestamp(),
                             error='Decision submission stopped before its initial state was durable')
                write_json(path / 'state.json', state)
            return self.state(identity)
        write_json(path / 'request.json', request)
        write_json(path / 'input.json', input)
        write_json(path / 'state.json', state)
        read_end, write_end = os.pipe()
        try:
            # Inherit the outer runtime job's process group. Its cancellation
            # and final SIGKILL therefore cover supervisors and all descendants.
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                str(path), str(read_end)], pass_fds=(read_end,))
        except BaseException:
            os.close(write_end)
            raise
        finally:
            os.close(read_end)
        self.processes[identity] = (process, write_end)
        return state

    def state(self, identity):
        path = self.path(identity)
        state = read_json(path / 'state.json')
        tracked = self.processes.get(identity)
        if tracked and tracked[0].poll() is not None:
            os.close(tracked[1])
            del self.processes[identity]
        if state['status'] not in TERMINAL and identity not in self.processes:
            with lease(path) as descriptor:
                if descriptor is not None:
                    state = read_json(path / 'state.json')
                    if state['status'] not in TERMINAL:
                        state.update(status='interrupted', error='Decision supervisor stopped before completion',
                                     finished_at=timestamp(), updated_at=timestamp())
                        write_json(path / 'state.json', state)
        return state

    def list(self):
        return [self.state(path.name) for path in sorted(self.directory.iterdir())
                if path.is_dir() and (path / 'state.json').is_file()]

    def cancel(self, identity):
        path = self.path(identity)
        state = self.state(identity)
        if state['status'] not in TERMINAL:
            write_json(path / 'cancel.json', {'requested_at': timestamp()})
        return state

    def close(self, *, interrupted=False):
        for identity, (_, write_end) in list(self.processes.items()):
            if not interrupted:
                self.cancel(identity)
            if identity in self.processes:
                os.close(write_end)  # cancel/state may already have reaped it.
        for process, _ in self.processes.values():
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        self.processes.clear()


def descendants():
    """Read only live children of this supervisor, never persisted PIDs."""
    found, pending = set(), [os.getpid()]
    while pending:
        parent = pending.pop()
        try:
            children = Path(f'/proc/{parent}/task/{parent}/children').read_text().split()
        except FileNotFoundError:
            continue
        for text in children:
            pid = int(text)
            if pid not in found:
                found.add(pid)
                pending.append(pid)
    return found


def stop_children(process):
    # Become the adoptive parent of orphaned grandchildren before starting the
    # member command. This keeps even an exited command's children attributable.
    deadline = time.monotonic() + 1
    while True:
        children = descendants()
        if not children:
            break
        force = time.monotonic() >= deadline
        for pid in children:
            try:
                descriptor = os.pidfd_open(pid)
            except ProcessLookupError:
                continue
            try:
                signal.pidfd_send_signal(descriptor, signal.SIGKILL if force else signal.SIGTERM)
            except ProcessLookupError:
                pass
            finally:
                os.close(descriptor)
        if process is not None:
            process.poll()
        while True:
            try:
                child, _ = os.waitpid(-1, os.WNOHANG)
                if child == 0:
                    break
            except ChildProcessError:
                break
        time.sleep(0.01)


def supervise(directory, parent):
    directory = Path(directory)
    process, stopping = None, False
    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    def parent_gone():
        return bool(select.select([parent], [], [], 0)[0]) and not os.read(parent, 1)
    with lease(directory) as descriptor:
        if descriptor is None:
            return
        state = read_json(directory / 'state.json')
        if state['status'] in TERMINAL:
            return
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER, Linux worker container.
                raise OSError(ctypes.get_errno(), 'Cannot supervise member descendants')
            request = read_json(directory / 'request.json')
            if parent_gone() or stopping:
                state.update(status='interrupted', error='Swarm stopped before decision started')
                return
            if (directory / 'cancel.json').exists():
                state.update(status='cancelled')
                return
            environment = {**os.environ, **request['env'], 'ASYS_JOB_ID': request['id'],
                'ASYS_JOB_TYPE': request['type'], 'ASYS_JOB_DIR': str(directory),
                'ASYS_INPUT': str(directory / 'input.json'), 'ASYS_RESULT': str(directory / 'result.json'),
                'ASYS_REQUEST': str(directory / 'request.json'), 'ASYS_WORKSPACE': request['workspace'],
                'ASYS_RUNTIME_ROOT': request['runtimeRoot']}
            with (directory / 'input.json').open('rb') as stdin, (directory / 'stdout.log').open('wb') as stdout, \
                    (directory / 'stderr.log').open('wb') as stderr:
                process = subprocess.Popen(request['command'], cwd=request['workspace'], env=environment,
                                           stdin=stdin, stdout=stdout, stderr=stderr)
                state.update(status='running', pid=process.pid, started_at=timestamp(), updated_at=timestamp())
                write_json(directory / 'state.json', state)
                started = time.monotonic()
                while process.poll() is None:
                    if (directory / 'cancel.json').exists():
                        state.update(status='cancelled')
                        break
                    if stopping or parent_gone():
                        state.update(status='interrupted', error='Swarm stopped during decision')
                        break
                    if time.monotonic() - started >= request['timeout']:
                        state.update(status='failed', error='Member command exceeded its timeout')
                        break
                    time.sleep(0.02)
                if state['status'] == 'running':
                    state['exit_code'] = process.returncode
                    if process.returncode:
                        state.update(status='failed', error=f'Program exited with status {process.returncode}')
                        try:
                            result = read_json(directory / 'result.json')
                        except (OSError, ValueError):
                            pass
                        else:
                            state['result'] = result
                            exception = result.get('exception') if isinstance(result, dict) else None
                            if isinstance(exception, str) and exception.strip():
                                state['error'] = exception
                    elif stopping or parent_gone():
                        state.update(status='interrupted', error='Swarm stopped before recording decision')
                    elif (directory / 'cancel.json').exists():
                        state.update(status='cancelled')
                    else:
                        state.update(status='done', result=read_json(directory / 'result.json'))
        except Exception as error:
            state.update(status='failed', error=str(error))
        finally:
            stop_children(process)
            state.update(finished_at=timestamp(), updated_at=timestamp())
            write_json(directory / 'state.json', state)
            os.close(parent)


if __name__ == '__main__':
    supervise(sys.argv[1], int(sys.argv[2]))
