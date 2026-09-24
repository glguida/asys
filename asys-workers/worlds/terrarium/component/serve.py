#!/usr/bin/env python3
"""Provide Rainkeepers world operations for independent swarm sessions."""
import argparse
import hashlib
import os
from pathlib import Path
import signal

from asys_swarm.world_service import Component
import physics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default=os.environ.get('ASYS_WORLD_ROOT', '/var/lib/asys-world'))
    args = parser.parse_args()
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, stop)
    identity = 'rainkeepers-' + hashlib.sha256(Path(physics.__file__).read_bytes()).hexdigest()
    Component(args.root, physics, identity=identity).serve(stop=lambda: stopping)


if __name__ == '__main__':
    main()
