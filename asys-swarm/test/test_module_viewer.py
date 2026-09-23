"""Embedded renderers, per-job controls, durable replay, and legacy views."""
import json
from pathlib import Path
import tempfile
import unittest
from urllib.request import Request, urlopen

from asys_runtime.channel import Reader, Writer, direction_root
from asys_runtime.files import write_json
from asys.swarm_view import Viewer, control, run_record, SCRIPT, FALLBACK


class ViewerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.record = {'id': 'run-1', 'job_id': 'job-1', 'manager': 'run', 'worker_name': 'search', 'worker_kind': 'swarm',
            'status': 'running', 'swarm_state': 'jobs/job-1/swarm', 'control_channel': 'swarm-job-1',
            'view_directory': 'view', 'view': 'view.mjs', 'view_format': 'module'}
        self.package = self.root / 'view'
        self.package.mkdir()
        (self.package / 'view.mjs').write_text('export function mount(){return {update(){},dispose(){}}}\n')
        self.state = self.root / self.record['swarm_state']
        self.state.mkdir(parents=True)
        self.save()

    def save(self):
        write_json(self.root / 'run.json', self.record)

    def viewer(self):
        value = Viewer(self.root)
        self.addCleanup(value.close)
        return value

    def journal(self, rows, tail=b''):
        (self.state / 'events.jsonl').write_bytes(b''.join(json.dumps(row).encode() + b'\n' for row in rows) + tail)

    def rows(self):
        return [{'sequence': i + 1, 'type': 'swarm.snapshot', 'time': f'time-{i}',
                 'data': {'runId': 'run-1', 'turn': i, 'state': {'value': i}, 'status': 'running'}} for i in range(3)]

    def test_named_run_uses_embedded_module_and_private_control_channel(self):
        viewer = self.viewer()
        self.assertEqual(run_record(self.root)['worker_name'], 'search')
        self.assertEqual(viewer.format, 'module')
        control(self.root, 'pause')
        messages = Reader(direction_root(self.root / 'runtime', 'swarm-job-1', 'in')).read()
        self.assertEqual(messages[0]['type'], 'pause')
        self.assertEqual(messages[0]['data']['id'], 'job-1')
        self.assertNotIn(b'<iframe', FALLBACK)
        self.assertIn(b'module.mount', SCRIPT)
        self.assertIn(b'renderer?.dispose()', SCRIPT)

    def test_recorded_frames_survive_channel_pruning_and_support_replay_selection(self):
        rows = self.rows()
        self.journal(rows)
        writer = Writer(direction_root(self.root / 'runtime', 'swarm-job-1', 'out'))
        for row in rows:
            writer.send(row['type'], row['data'])
        reader = Reader(writer.directory)
        reader.advance(3)
        reader.prune()
        viewer = self.viewer()
        self.assertEqual([frame['turn'] for frame in viewer.frames()], [0, 1, 2])
        self.assertEqual(viewer.state(1)['state'], {'value': 0})
        self.assertEqual([row['sequence'] for row in viewer.events(1)], [2, 3])
        self.assertEqual(viewer.state()['turn'], 2)

    def test_http_serves_embedded_module_frames_and_controls_without_an_iframe(self):
        self.journal(self.rows())
        viewer = self.viewer().start()
        with urlopen(viewer.url) as response:
            html = response.read()
        self.assertIn(b'/viewer.mjs', html)
        self.assertNotIn(b'<iframe', html)
        with urlopen(viewer.url + 'api/view') as response:
            self.assertEqual(json.load(response)['module'], '/view/view.mjs')
        with urlopen(viewer.url + 'view/view.mjs') as response:
            self.assertEqual(response.headers.get_content_type(), 'text/javascript')
            self.assertIn(b'export function mount', response.read())
        with urlopen(viewer.url + 'api/state?sequence=1') as response:
            self.assertEqual(json.load(response)['turn'], 0)
        with urlopen(Request(viewer.url + 'api/control', data=b'{"type":"pause"}',
                             headers={'Content-Type': 'application/json'})) as response:
            self.assertEqual(json.load(response)['data']['id'], 'job-1')

    def test_partial_final_journal_line_becomes_visible_only_after_completion(self):
        rows = self.rows()
        self.journal(rows[:1], json.dumps(rows[1]).encode()[:20])
        viewer = self.viewer()
        self.assertEqual(len(viewer.frames()), 1)
        with (self.state / 'events.jsonl').open('ab') as stream:
            stream.write(json.dumps(rows[1]).encode()[20:] + b'\n')
        self.assertEqual(len(viewer.frames()), 2)

    def test_checkpoint_supplies_latest_authoritative_status(self):
        self.journal(self.rows())
        write_json(self.state / 'checkpoint.json', {'id': 'run-1', 'turn': 3, 'world': {'value': 3},
            'status': 'completed', 'evaluation': {'metrics': {'score': 12}}, 'config': {'mission': 'Request text'}})
        viewer = self.viewer()
        self.assertEqual(viewer.state()['turn'], 3)
        self.assertEqual(viewer.state()['status'], 'completed')
        self.assertEqual(viewer.state(1)['turn'], 0)

    def test_legacy_saved_html_remains_readable(self):
        self.record.update(manager='swarm', view='old.html')
        self.record.pop('view_format')
        (self.package / 'old.html').write_text('<h1>Legacy view</h1>')
        self.save()
        viewer = self.viewer()
        self.assertEqual(viewer.format, 'legacy')
        self.assertEqual(viewer.view.read_text(), '<h1>Legacy view</h1>')

    def test_paths_cannot_escape_saved_run_and_terminal_controls_reject(self):
        self.record['view_directory'] = '..'
        self.save()
        with self.assertRaises(ValueError):
            self.viewer()
        self.record['view_directory'] = 'view'
        self.record['swarm_state'] = '../elsewhere'
        self.save()
        with self.assertRaises(ValueError):
            self.viewer()
        self.record['status'] = 'completed'
        self.save()
        with self.assertRaisesRegex(ValueError, 'already completed'):
            control(self.root, 'cancel')


if __name__ == '__main__':
    unittest.main()
