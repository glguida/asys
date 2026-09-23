#!/usr/bin/env python3
"""Serve Rainkeepers rules over an asys runtime channel."""
import argparse
import hashlib
import os
from pathlib import Path
import signal
import sys

# These paths support the checkout and the normal host installation. The
# component image supplies the same SDK through its Python search path.
source = Path(__file__).resolve()
for depth, suffix in ((4, 'asys-workers'), (4, 'asys-runtime'), (4, 'asys/python'), (3, 'python')):
    if depth >= len(source.parents):
        continue
    directory = source.parents[depth] / suffix
    if directory.is_dir():
        sys.path.insert(0, str(directory))

from asys_swarm.world_service import Service
import physics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default=os.environ.get('ASYS_RUNTIME_ROOT', '/var/lib/asys/runtime'))
    parser.add_argument('--channel', default=os.environ.get('ASYS_WORLD_CHANNEL', 'world'))
    args = parser.parse_args()
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, stop)
    identity = 'rainkeepers-' + hashlib.sha256(Path(physics.__file__).read_bytes()).hexdigest()
    health = Path(os.environ.get('ASYS_WORLD_HEALTH_FILE',
                  str(Path(args.root) / 'channels' / args.channel / '.health')))

    def poll():
        health.touch()
        return stopping

    try:
        Service(args.root, physics, identity=identity, channel=args.channel).serve(stop=poll)
    finally:
        health.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
