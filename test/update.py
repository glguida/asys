import fcntl
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'python'), str(ROOT / 'asys-runtime')]
from asys.update import update
from argparse import Namespace


class UpdateTests(unittest.TestCase):
    def test_only_running_inference_and_live_shared_handlers_are_updated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'inference').mkdir()
            (root / 'inference/machine.json').write_text(json.dumps({'config': {'running': True}}))
            leases = []
            for name, private, live in [('shared', False, True), ('private', True, True), ('stale', False, False)]:
                directory = root / 'human' / name
                directory.mkdir(parents=True)
                (directory / 'session.json').write_text(json.dumps({'global': None if private else 'human_endpoint', 'component_removed': False, 'component': name}))
                lease = (directory / 'handler.lock').open('a+b')
                leases.append(lease)
                if live:
                    fcntl.flock(lease, fcntl.LOCK_EX)
            # Runs are deliberately not part of this command's scope.
            (root / 'runs').mkdir()
            (root / 'runs/not-a-service').write_text('untouched')
            try:
                with patch('asys.update.subprocess.run') as run, patch('asys.update.refresh_human') as human:
                    update(Namespace(root=root))
                self.assertEqual(run.call_args.args[0][-3:], ['--root', str(root / 'inference'), 'start'])
                self.assertEqual(human.call_count, 1)
                self.assertEqual(human.call_args.args[0], root / 'human/shared')
                self.assertEqual((root / 'runs/not-a-service').read_text(), 'untouched')
            finally:
                for lease in leases:
                    lease.close()

    def test_update_does_not_start_a_stopped_installation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'inference').mkdir()
            (root / 'inference/machine.json').write_text(json.dumps({'config': {'running': False}}))
            with patch('asys.update.subprocess.run') as run:
                update(Namespace(root=root))
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
