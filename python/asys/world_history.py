"""Indexed durable world history used by the shared dashboard."""
import json
from pathlib import Path
import threading
from asys_runtime.files import read_json


class WorldHistory:
    def __init__(self, job_directory):
        self.state_directory = Path(job_directory) / 'swarm'
        self.journal = self.state_directory / 'events.jsonl'
        self._index = {}
        self._position = 0
        self._inode = None
        self._lock = threading.RLock()

    def _scan_journal(self):
        """Index complete durable lines without retaining large state payloads."""
        with self._lock:
            if not self.journal.is_file():
                return False
            stat = self.journal.stat()
            inode = (stat.st_dev, stat.st_ino)
            if inode != self._inode or stat.st_size < self._position:
                self._index, self._position, self._inode = {}, 0, inode
            with self.journal.open('rb') as stream:
                stream.seek(self._position)
                while True:
                    offset = stream.tell()
                    line = stream.readline(8 * 1024 * 1024 + 1)
                    if len(line) > 8 * 1024 * 1024:
                        raise ValueError('Journal event exceeds 8 MiB')
                    if not line or not line.endswith(b'\n'):
                        break  # an interrupted final append is not a frame
                    row = json.loads(line)
                    if not isinstance(row, dict) or not isinstance(row.get('data'), dict):
                        raise ValueError('Invalid journal event')
                    sequence = row.get('sequence')
                    if type(sequence) is not int or sequence <= next(reversed(self._index), 0):
                        raise ValueError('Invalid journal sequence')
                    data = row.get('data', {})
                    self._index[sequence] = {'offset': offset, 'length': len(line), 'sequence': sequence,
                        'type': row.get('type'), 'time': row.get('time'), 'turn': data.get('turn'),
                        'status': data.get('status')}
                    self._position = stream.tell()
            return True

    def _journal_event(self, sequence):
        row = self._index[sequence]
        with self.journal.open('rb') as stream:
            stream.seek(row['offset'])
            return json.loads(stream.read(row['length']))

    def events(self, after=0):
        with self._lock:
            if not self._scan_journal():
                return []
            rows, total = [], 0
            for sequence, metadata in self._index.items():
                if sequence <= after:
                    continue
                if rows and (len(rows) >= 500 or total + metadata['length'] > 4 * 1024 * 1024):
                    break
                rows.append(self._journal_event(sequence))
                total += metadata['length']
            return rows

    def frames(self):
        with self._lock:
            if self._scan_journal():
                return [{key: value for key, value in row.items() if key not in {'offset', 'length', 'type'}}
                        for row in self._index.values() if row['type'] == 'swarm.snapshot']
            return []

    def state(self, sequence=None):
        if sequence is not None:
            with self._lock:
                if self._scan_journal():
                    if sequence not in self._index or self._index[sequence]['type'] != 'swarm.snapshot':
                        raise ValueError('Select a recorded snapshot sequence')
                    return self._journal_event(sequence)['data']
                raise ValueError('No recorded frames')
        # New runs have an authoritative checkpoint in the outer job. Prefer it
        # to a channel whose old snapshots may have been pruned.
        checkpoint = self.state_directory / 'checkpoint.json'
        if checkpoint.is_file():
            value = read_json(checkpoint)
            evaluation = value.get('evaluation', {})
            config = value.get('config', {})
            return {'runId': value.get('id'), 'turn': value.get('turn'), 'state': value.get('world', {}),
                'metrics': evaluation.get('metrics', {}), 'evaluation': evaluation,
                'status': value.get('status'), 'decisions': value.get('decisions', 0),
                'usage': value.get('usage', {}), 'events': [],
                'mission': config.get('mission'), 'objective': config.get('objective')}
        frames = self.frames()
        if frames:
            return self.state(frames[-1]['sequence'])
        return {}
