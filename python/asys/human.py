"""A terminal Human service owned by one foreground run."""
import os
from pathlib import Path
import subprocess
import sys
import time

from .lifecycle import LaunchError


class HumanHandler:
    def __init__(self, host, name):
        self.host, self.name = host, name
        self.process = None

    def start(self):
        source = Path(__file__).resolve().parents[2] / 'asys-human-interface/tools/asys-human-prompt'
        executable = source if source.is_file() else Path(sys.argv[0]).resolve().parent / 'asys-human-prompt'
        if not executable.is_file():
            raise LaunchError('--human requires asys-human-prompt; install the human interface')
        command = [sys.executable, str(executable), '--private', '--name', self.name,
                   '--system', self.host.args.system, '--root', str(self.host.args.root)]
        options = self.host.dcomp[1:]
        for index in range(0, len(options), 2):
            flag = '--dcomp-state-root' if options[index] == '--state-root' else options[index]
            command += [flag, options[index + 1]]
        self.process = subprocess.Popen(command, env={**os.environ, 'DCOMP_BINARY': self.host.dcomp[0]})
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            self.host.check_interrupt()
            self.check()
            document = self.host.document('view', '--json', self.host.args.system)
            component = next((item for item in document['components'] if item['name'] == self.name), None)
            if component:
                self.host.check_components({self.name: component}, [self.name])
                if component['status'].get('health') == 'healthy':
                    return
            self.host.interrupted.wait(0.2)
        raise LaunchError('Timed out starting the terminal human handler')

    def check(self):
        if self.process and self.process.poll() is not None:
            raise LaunchError(f'The terminal human handler stopped (exit {self.process.returncode})')

    def close(self):
        if self.process is None:
            return True
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
            self.host.say(f'Human handler cleanup did not finish; inspect component {self.name}')
            return False
        return self.process.returncode in (0, 130)
