"""Run the controller inside its asys component."""
import argparse
import os
from pathlib import Path
import signal
import time

from asys_runtime.files import acquire_lock

from .engine import Engine


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='/var/lib/asys/runtime')
    parser.add_argument('--state', default='/var/lib/asys-swarm')
    parser.add_argument('--workspace', default='/var/lib/asys/workspace')
    parser.add_argument('--package', default='/opt/asys/swarm-package')
    parser.add_argument('--channel', default='swarm')
    args = parser.parse_args(argv)
    Path(args.state).mkdir(parents=True, exist_ok=True)
    lease = acquire_lock(Path(args.state) / 'engine.lease')
    if lease is None:
        parser.error('Another swarm controller owns this state')
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    engine = None
    health = Path('/tmp/asys-swarm-health')
    try:
        engine = Engine(args.root, args.state, args.workspace, args.package, channel=args.channel)
        while not stopping:
            health.touch()
            engine.pump()
            time.sleep(0.025)
    finally:
        try:
            if engine is not None:
                engine.close()
        finally:
            health.unlink(missing_ok=True)
            os.close(lease)
            for sig, handler in previous.items():
                signal.signal(sig, handler)


if __name__ == '__main__':
    main()
