import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from asys_runtime import Queue, Runtime


class ExecutionTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.queue = Queue(self.root / "runtime/environments/test")
        self.job = self.root / "jobs/one"
        self.workspace = self.root / "workspaces/project"
        self.job.mkdir(parents=True)
        self.workspace.mkdir(parents=True)

    def test_caller_supplies_storage_and_runtime_only_executes(self):
        (self.workspace / "prepared.txt").write_text("caller input")
        code = """
import json, os
from pathlib import Path
assert Path.cwd() == Path(os.environ['ASYS_WORKSPACE'])
assert Path('prepared.txt').read_text() == 'caller input'
assert not Path('input').exists() and not Path('output').exists()
assert 'ASYS_INPUT_DIR' not in os.environ
job = Path(os.environ['ASYS_JOB_DIR'])
(job / 'lessons.md').write_text('A lesson')
Path('design.txt').write_text('work')
print('execution log')
Path(os.environ['ASYS_RESULT']).write_text(json.dumps({'final': 'Finished', 'exception': None}))
"""
        self.queue.submit("program", "one", directory=self.job, workspace=self.workspace, args=["-c", code])
        Runtime(self.queue.root, {"program": {"command": [sys.executable]}}).run(once=True)
        state = self.queue.state("one")
        self.assertEqual(state["status"], "done", state)
        self.assertEqual(state["result"], {"final": "Finished", "exception": None})
        self.assertEqual((self.job / "stdout.log").read_text(), "execution log\n")
        self.assertEqual((self.job / "lessons.md").read_text(), "A lesson")
        self.assertEqual((self.workspace / "design.txt").read_text(), "work")
        self.assertFalse((self.queue.directory("one") / "workspace").exists())
        self.assertFalse((self.queue.directory("one") / "attempts").exists())

    def test_directory_references_survive_moving_the_shared_tree(self):
        self.queue.submit("program", "one", directory=self.job, workspace=self.workspace)
        request = self.queue.request("one")
        self.assertFalse(Path(request["directory"]).is_absolute())
        self.assertFalse(Path(request["workspace"]).is_absolute())
        moved = self.root / "moved"
        moved.mkdir()
        for name in ("runtime", "jobs", "workspaces"):
            shutil.move(self.root / name, moved / name)
        queue = Queue(moved / "runtime/environments/test")
        self.assertEqual(queue.paths("one"), {"directory": moved / "jobs/one", "workspace": moved / "workspaces/project"})

    def test_missing_storage_is_rejected_before_publication(self):
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.queue.submit("program", "missing", directory=self.job, workspace=self.root / "missing")
        self.assertFalse(self.queue.directory("missing").exists())

    def test_two_executors_share_an_environment_but_keep_job_storage_separate(self):
        for run in ("first", "second"):
            queue = Queue(self.root / run / "runtime")
            job, workspace = self.root / run / "job", self.root / run / "workspace"
            job.mkdir(parents=True)
            workspace.mkdir()
            queue.submit("program", "same-id", directory=job, workspace=workspace,
                         args=["-c", "from pathlib import Path; Path('run.txt').write_text('" + run + "')"])
            Runtime(queue.root, {"program": {"command": [sys.executable]}}).run(once=True)
            self.assertEqual(queue.state("same-id")["status"], "done")
        self.assertEqual((self.root / "first/workspace/run.txt").read_text(), "first")
        self.assertEqual((self.root / "second/workspace/run.txt").read_text(), "second")
