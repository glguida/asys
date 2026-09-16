"""Exercise the real curses interface on a pseudo-terminal."""
import fcntl
import curses
import json
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import sys
import termios
import time

master, slave = pty.openpty()
fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 40, 140, 0, 0))
before = termios.tcgetattr(slave)
child = subprocess.Popen([sys.executable, sys.argv[1], 'top', '--root', sys.argv[2]],
                         stdin=slave, stdout=slave, stderr=slave, env=dict(os.environ, TERM='xterm'))
output = b''

def until(*texts):
    global output
    deadline = time.monotonic() + 5
    while not all(text in output for text in texts):
        if child.poll() is not None or time.monotonic() > deadline:
            raise AssertionError(output.decode(errors='replace'))
        if select.select([master], [], [], .1)[0]:
            output += os.read(master, 65536)

try:
    until(b'Agent prompt must be a nonempty string', b'JOB NAME')
    assert b'pcb-engineer' in output and b'design_module' in output, output
    if len(sys.argv) > 3:
        curses.setupterm(term='xterm')
        until(b'Transcript:', b'LAST SAVED ANSWER')
        os.write(master, curses.tigetstr('khome'))
        until(b'TRANSCRIPT PROMPT', b'I will inspect the board.', b'printf BOARD_CHECK', b'BOARD CHECK SUCCEEDED')
        assert b'UNSELECTED BRANCH' not in output, output
        os.write(master, curses.tigetstr('knp'))
        until(b'Transcript line 20')
        # Saved messages refresh even while the user is reading earlier output.
        checkpoint = Path(sys.argv[3])
        state = json.loads(checkpoint.read_text())
        state['agent']['session']['entries'].append({'type': 'message', 'id': 'new', 'parentId': 'final',
            'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': 'LIVE TRANSCRIPT UPDATE'}]}})
        state['agent']['session']['leafId'] = 'new'
        temporary = checkpoint.with_suffix('.tmp')
        temporary.write_text(json.dumps(state))
        temporary.replace(checkpoint)
        os.write(master, curses.tigetstr('kend'))
        until(b'LIVE TRANSCRIPT UPDATE')
        assert b'agent.tool_started' not in output, output
    os.write(master, b'l')
    until(b'Run log', b'FINISHED')
    os.write(master, b'\tq')
    assert child.wait(timeout=3) == 0, output
    assert termios.tcgetattr(slave) == before, 'monitor did not restore terminal settings'
finally:
    if child.poll() is None:
        child.kill()
        child.wait()
    os.close(master)
    os.close(slave)
