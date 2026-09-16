"""Drive login through a real terminal, including echo and cancellation checks."""
import json
import os
import pty
import select
import subprocess
import termios
import time


def login(command, mode):
    master, slave = pty.openpty()
    original = termios.tcgetattr(slave)
    child = subprocess.Popen(command, stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = b"", b""
    chosen = answered = False
    deadline = time.monotonic() + 15
    try:
        while child.poll() is None:
            assert time.monotonic() < deadline, f"Login hung: {out!r} {err!r}"
            for stream in select.select([child.stdout, child.stderr], [], [], 0.05)[0]:
                data = os.read(stream.fileno(), 4096)
                if stream is child.stdout:
                    out += data
                else:
                    err += data
            if b"Enter number" in err and not chosen:
                os.write(master, b"2\n" if mode == "callback" else b"1\n")
                chosen = True
            if b"Paste authorization code" in err and not answered:
                os.write(master, b"test-code\n")
                answered = True
            if b"Enter test key" in err and not answered:
                assert not termios.tcgetattr(slave)[3] & termios.ECHO, "Secret prompt left terminal echo enabled"
                if mode == "cancel":
                    child.terminate()
                else:
                    os.write(master, b"terminal-test-key\n")
                answered = True
        remaining_out, remaining_err = child.communicate(timeout=3)
        out += remaining_out
        err += remaining_err
        assert termios.tcgetattr(slave) == original, "Login did not restore terminal settings"
        assert b"terminal-test-key" not in out + err, "Secret leaked into output"
        if mode == "cancel":
            assert child.returncode != 0, (out, err)
            return None
        assert child.returncode == 0, (out, err)
        return json.loads(out)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        os.close(master)
        os.close(slave)
