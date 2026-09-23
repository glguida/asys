"""Supervise a job-owned world executable without importing its implementation."""
import ctypes
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time
import uuid

if __package__ in (None, ''):
    sys.path[:0] = [str(Path(__file__).resolve().parents[1]),
                    str(Path(__file__).resolve().parents[2] / 'asys-runtime')]

from asys_runtime.files import read_json, timestamp, write_json
from asys_swarm.executor import lease, stop_children


class WorldProcess:
    def __init__(self, directory, command, environment, runtime_root, channel):
        directory = Path(directory).resolve()
        # A recovered parent can acquire its job lease before the previous
        # supervisor has finished EOF cleanup. Wait for its live lease, never
        # signal persisted PIDs or start a competing world channel consumer.
        deadline = time.monotonic() + 3
        if directory.exists():
            for attempt in directory.iterdir():
                if not attempt.is_dir():
                    continue
                while True:
                    with lease(attempt) as descriptor:
                        available = descriptor is not None
                    if available:
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Previous world process is still stopping; retry this job')
                    time.sleep(0.02)
        self.directory = directory / uuid.uuid4().hex
        self.directory.mkdir(parents=True)
        root = Path(environment['ASYS_ENVIRONMENT_DIR']).resolve(strict=True)
        command = list(command)
        executable = Path(command[0])
        if not executable.is_absolute() and '/' in command[0]:
            executable = (root / executable).resolve(strict=True)
            if not executable.is_relative_to(root):
                raise ValueError('Relative world executable must remain inside the environment')
            command[0] = str(executable)
        env = {**environment, 'ASYS_RUNTIME_ROOT': str(Path(runtime_root).resolve()),
               'ASYS_WORLD_CHANNEL': channel, 'ASYS_WORLD_DIR': str(self.directory)}
        # No environment credentials are copied into durable process metadata.
        write_json(self.directory / 'request.json', {'command': command, 'cwd': str(root),
                    'channel': channel, 'runtimeRoot': str(Path(runtime_root).resolve())})
        write_json(self.directory / 'state.json', {'status': 'pending', 'created_at': timestamp()})
        parent, self.liveness = os.pipe()
        try:
            self.process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
                str(self.directory), str(parent)], env=env, pass_fds=(parent,))
        except BaseException:
            os.close(self.liveness)
            raise
        finally:
            os.close(parent)

    def check(self):
        state = read_json(self.directory / 'state.json')
        if self.process.poll() is not None or state['status'] in {'failed', 'stopped'}:
            raise RuntimeError(state.get('error') or 'World process stopped before the swarm finished')

    def close(self):
        if self.liveness is not None:
            os.close(self.liveness)
            self.liveness = None
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()


def supervise(directory, parent):
    directory = Path(directory)
    process, stopping = None, False
    def stop(*_):
        nonlocal stopping
        stopping = True
    def parent_gone():
        return bool(select.select([parent], [], [], 0)[0]) and not os.read(parent, 1)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with lease(directory) as descriptor:
        if descriptor is None:
            return
        state = read_json(directory / 'state.json')
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            if libc.prctl(36, 1, 0, 0, 0) != 0:
                raise OSError(ctypes.get_errno(), 'Cannot supervise world descendants')
            request = read_json(directory / 'request.json')
            if parent_gone() or stopping:
                return
            with (directory / 'stdout.log').open('wb') as stdout, (directory / 'stderr.log').open('wb') as stderr:
                process = subprocess.Popen(request['command'], cwd=request['cwd'], env=os.environ,
                                           stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
                state.update(status='running', pid=process.pid, started_at=timestamp())
                write_json(directory / 'state.json', state)
                while process.poll() is None and not stopping and not parent_gone():
                    time.sleep(0.02)
                if process.poll() is not None and not stopping and not parent_gone():
                    state.update(status='failed', exit_code=process.returncode,
                                 error=f'World executable exited with status {process.returncode}')
        except Exception as error:
            state.update(status='failed', error=str(error))
        finally:
            stop_children(process)
            if state['status'] != 'failed':
                state['status'] = 'stopped'
            state['finished_at'] = timestamp()
            write_json(directory / 'state.json', state)
            os.close(parent)


if __name__ == '__main__':
    supervise(sys.argv[1], int(sys.argv[2]))
