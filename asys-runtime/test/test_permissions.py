import os
from pathlib import Path
import tempfile
import unittest

from asys_runtime.files import file_lock, write_json
from asys_runtime.permissions import mkdir, open_file
from asys_runtime.queue import Queue


class PermissionsTest(unittest.TestCase):
    def test_shared_queue_inherits_group_access_under_restrictive_umask(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o2770)
            previous = os.umask(0o077)
            try:
                mkdir(root / 'job')
                mkdir(root / 'workspace')
                queue = Queue(root / 'runtime/environments/test')
                queue.submit('program', 'one', directory=root / 'job', workspace=root / 'workspace')
                with file_lock(root / 'lock'):
                    write_json(root / 'record.json', {'done': True})
                for path in root.rglob('*'):
                    self.assertEqual(path.stat().st_gid, root.stat().st_gid)
                    self.assertEqual(path.stat().st_mode & 0o7777, 0o2770 if path.is_dir() else 0o660, str(path))
            finally:
                os.umask(previous)

    def test_private_permissions_and_exclusive_creation_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mkdir(root / 'private')
            path = root / 'private/record'
            os.close(open_file(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            with self.assertRaises(FileExistsError):
                open_file(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o7777, 0o700)
            with self.assertRaises(FileNotFoundError):
                open_file(root / 'missing', os.O_RDONLY)
