"""Refresh shared services using the installed component images and sources."""
import fcntl
import json
from pathlib import Path
import subprocess
import sys
import time

from asys_runtime.channel import Reader, Writer, direction_root


def inference_command():
    source = Path(__file__).resolve().parents[2] / 'asys-inference/bin/asys-inference'
    return str(source) if source.is_file() else str(Path(sys.argv[0]).resolve().parent / 'asys-inference')


def refresh_human(directory, record):
    image = record['image_ref']
    inspected = subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', image],
                               check=True, capture_output=True, text=True).stdout.strip()
    if inspected == record['image']:
        print(f"Human service {record['component']} is current.", flush=True)
        return
    incoming = Writer(direction_root(directory, 'control', 'in'))
    outgoing = Reader(direction_root(directory, 'control', 'out'))
    after = max(outgoing.sequences(), default=0)
    request = incoming.send('update', {'image': inspected})
    print(f"Updating human service {record['component']}…", flush=True)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        for event in outgoing.read(after):
            after = event['sequence']
            if event['data'].get('request') != request['sequence']:
                continue
            if event['type'] == 'error':
                raise ValueError(event['data']['message'])
            print(f"Updated human service {record['component']}.", flush=True)
            return
        time.sleep(0.1)
    raise ValueError(f"Human handler did not confirm its update; inspect {directory}")


def update(args):
    root = args.root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f'asys state directory does not exist: {root}')
    changed = False
    machine = root / 'inference/machine.json'
    if machine.is_file() and json.loads(machine.read_text())['config']['running']:
        print('Updating inference services…', flush=True)
        subprocess.run([inference_command(), '--root', str(machine.parent), 'start'], check=True)
        changed = True
    for path in sorted((root / 'human').glob('*/session.json')):
        record = json.loads(path.read_text())
        if record['component_removed'] or record.get('global') != 'human_endpoint':
            continue
        with (path.parent / 'handler.lock').open('rb') as lease:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                refresh_human(path.parent, record)
                changed = True
    if not changed:
        print('No running shared services in this asys state.')
