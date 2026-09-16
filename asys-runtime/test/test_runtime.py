import concurrent.futures
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from asys_runtime import Runtime
from fixtures import PreparedQueue as Queue
from asys_runtime.files import file_lock

ROOT = Path(__file__).resolve().parents[1]
WORKER = Path(__file__).with_name("worker.py")


def until(check, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.02)
    raise AssertionError("timed out waiting for test condition")


def inactive(pid):
    result = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return not result.stdout.strip() or result.stdout.strip().startswith("Z")


class RuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="asys-runtime-test-")
        self.root = Path(self.temporary.name)
        self.queue = Queue(self.root / "state")
        self.processes = []

    def tearDown(self):
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
            try:
                process.communicate(timeout=6)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
        self.temporary.cleanup()

    def type(self, mode="record", **options):
        return {"command": [sys.executable, str(WORKER), mode], **options}

    def start(self, types, *, once=False):
        config = self.root / f"config-{len(self.processes)}.json"
        config.write_text(json.dumps({"version": 1, "types": types}))
        process = subprocess.Popen([sys.executable, str(ROOT / "tools/asys-runtime"), "run", str(config),
            "--root", str(self.queue.root), *(["--once"] if once else [])],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.processes.append(process)
        return process

    def finish(self, process):
        out, err = process.communicate(timeout=12)
        self.assertEqual(process.returncode, 0, out + err)

    def run_once(self, types):
        self.finish(self.start(types, once=True))

    def workspace(self, job_id):
        return self.queue.paths(job_id)["workspace"]

    def test_program_arguments_json_input_output_and_workspace(self):
        args = ["--model", "a/b", "words with spaces", "$(touch SHOULD_NOT_EXIST)", "--flag=value"]
        self.queue.submit("custom-type", "one", args=args, input={"question": "Hello 世界"})
        self.run_once({"custom-type": self.type(env={"EXAMPLE_SETTING": "present"})})
        state = self.queue.state("one")
        self.assertEqual(state["status"], "done")
        self.assertEqual(state["result"]["args"], args)
        self.assertEqual(state["result"]["input"], {"question": "Hello 世界"})
        self.assertEqual(state["result"]["custom"], "present")
        self.assertEqual(state["result"]["cwd"], str(self.workspace("one")))
        self.assertFalse((self.workspace("one") / "SHOULD_NOT_EXIST").exists())
        self.assertEqual((self.queue.paths("one")["directory"] / "stdout.log").read_text(), "finished one\n")

    def test_two_runtimes_claim_each_job_once(self):
        for index in range(16):
            self.queue.submit("worker", f"job-{index}")
        first = self.start({"worker": self.type()}, once=True)
        second = self.start({"worker": self.type()}, once=True)
        self.finish(first)
        self.finish(second)
        self.assertEqual(len(self.queue.list()), 16)
        for state in self.queue.list():
            self.assertEqual(state["status"], "done")
            self.assertEqual(state["result"]["executions"], ["1"])

    def test_different_consumers_share_a_queue_by_type(self):
        self.queue.submit("one", "a")
        self.queue.submit("two", "b")
        self.queue.submit("unhandled", "c")
        a = self.start({"one": self.type()}, once=True)
        b = self.start({"two": self.type()}, once=True)
        self.finish(a)
        self.finish(b)
        self.assertEqual(self.queue.state("a")["status"], "done")
        self.assertEqual(self.queue.state("b")["status"], "done")
        self.assertEqual(self.queue.state("c")["status"], "pending")

    def test_waiting_jobs_can_submit_and_complete_dependent_jobs_dynamically(self):
        parents = [f"parent-{index}" for index in range(8)]
        for job_id in parents:
            self.queue.submit("parent", job_id, args=[str(self.queue.root)])
        self.run_once({"parent": self.type("delegate"), "child": self.type()})
        self.assertEqual(len(self.queue.list()), 16)
        for job_id in parents:
            parent = self.queue.state(job_id)
            child = self.queue.state(f"{job_id}-child")
            self.assertEqual(parent["status"], "done", parent.get("error"))
            self.assertEqual(child["status"], "done", child.get("error"))
            self.assertEqual(parent["result"]["child"], child["result"])
            self.assertEqual(child["result"]["input"], {"parent": job_id})
            self.assertEqual(parent["result"]["executions"], ["1"])
            self.assertEqual(child["result"]["executions"], ["1"])

    def test_concurrent_idempotent_submission_and_conflicting_input(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            states = list(pool.map(lambda _: self.queue.submit("worker", "same", input={"n": 1}), range(20)))
        self.assertTrue(all(s["status"] == "pending" for s in states))
        self.assertEqual(len(self.queue.list()), 1)
        with self.assertRaisesRegex(ValueError, "different input"):
            self.queue.submit("worker", "same", input={"n": 2})

    def test_a_program_can_wait_for_filesystem_input(self):
        self.queue.submit("approval", "human")
        process = self.start({"approval": self.type("wait")})
        until(lambda: (self.workspace("human") / "started").exists())
        self.assertEqual(self.queue.state("human")["status"], "running")
        (self.workspace("human") / "release").write_text("approved")
        self.assertEqual(self.queue.wait("human", timeout=5)["status"], "done")
        process.terminate()
        self.finish(process)

    def test_cancel_pending_never_launches_and_running_cancel_kills_descendants(self):
        self.queue.submit("worker", "pending")
        self.assertEqual(self.queue.cancel("pending")["status"], "cancelled")
        self.queue.submit("worker", "running")
        process = self.start({"worker": self.type("tree")})
        until(lambda: (self.workspace("running") / "child").exists())
        pid = int((self.workspace("running") / "child").read_text())
        self.queue.cancel("running")
        self.assertEqual(self.queue.wait("running", timeout=5)["status"], "cancelled")
        until(lambda: inactive(pid))
        self.assertFalse((self.workspace("pending") / "started").exists())
        process.terminate()
        self.finish(process)

    def test_supervisor_sigkill_stops_program_and_records_interruption(self):
        self.queue.submit("worker", "job")
        process = self.start({"worker": self.type("tree")})
        until(lambda: (self.workspace("job") / "child").exists())
        pid = int((self.workspace("job") / "child").read_text())
        process.kill()
        process.communicate(timeout=8)
        self.assertEqual(self.queue.wait("job", timeout=5)["status"], "interrupted")
        until(lambda: inactive(pid))
        self.run_once({"worker": self.type()})
        self.assertEqual(self.queue.state("job")["status"], "interrupted")
        self.assertEqual((self.workspace("job") / "executions").read_text(), "1\n")

    def test_caller_can_submit_a_new_job_after_interruption(self):
        self.queue.submit("worker", "job", input={"keep": True})
        process = self.start({"worker": self.type("wait")})
        until(lambda: (self.workspace("job") / "started").exists())
        process.terminate()
        self.finish(process)
        self.assertEqual(self.queue.state("job")["status"], "interrupted")
        self.queue.submit("worker", "next", input={"keep": True}, workspace=self.workspace("job"))
        self.run_once({"worker": self.type()})
        state = self.queue.state("next")
        self.assertEqual(state["status"], "done")
        self.assertEqual(state["result"]["executions"], ["1", "2"])
        self.assertEqual(state["result"]["input"], {"keep": True})
        self.assertEqual(self.queue.state("job")["status"], "interrupted")

    def test_exit_failure_invalid_result_missing_program_and_timeout(self):
        types = {"failure": self.type("fail"), "bad-result": self.type("invalid"),
            "missing": {"command": [str(self.root / "nonexistent")]}, "timeout": self.type("wait", timeout=0.1)}
        for name in types:
            self.queue.submit(name, name)
        self.run_once(types)
        for name in types:
            self.assertEqual(self.queue.state(name)["status"], "failed", name)
        self.assertEqual(self.queue.state("failure")["exit_code"], 17)
        self.assertEqual(self.queue.state("timeout")["error"], "Program timed out")

    def test_resubmission_keeps_results_separate_and_preserves_workspace(self):
        self.queue.submit("worker", "job")
        self.run_once({"worker": self.type("fail-once")})
        failed = self.queue.state("job")
        self.queue.submit("worker", "next", workspace=self.workspace("job"))
        self.run_once({"worker": self.type("fail-once")})
        state = self.queue.state("next")
        self.assertEqual(state["status"], "done")
        self.assertEqual(state["result"]["executions"], ["1", "2"])
        self.assertEqual(self.queue.state("job"), failed)

    def test_nonzero_exit_preserves_exception_reason_and_result(self):
        self.queue.submit("worker", "job")
        self.run_once({"worker": self.type("exception")})
        state = self.queue.state("job")
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["exit_code"], 17)
        self.assertEqual(state["error"], "Required schematic is missing")
        self.assertEqual(state["result"]["report"], "Only architecture.json was supplied")
        self.assertNotIn("artifacts", state)
        self.assertEqual((self.workspace("job") / "partial.txt").read_text(), "unfinished")

    def test_exit_code_remains_authoritative_with_exception_or_invalid_result(self):
        for mode in ["exception-zero", "exception-invalid"]:
            self.queue.submit(mode, mode)
        self.run_once({mode: self.type(mode) for mode in ["exception-zero", "exception-invalid"]})
        self.assertEqual(self.queue.state("exception-zero")["status"], "done")
        failed = self.queue.state("exception-invalid")
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["exit_code"], 17)
        self.assertEqual(failed["error"], "Program exited with status 17")

    def test_recovery_waits_for_an_actual_lock_not_a_pid_file(self):
        self.queue.submit("worker", "orphan")
        state = self.queue.state("orphan")
        state["status"] = "running"
        self.queue.save(state)
        with file_lock(self.queue.directory("orphan") / "lease"):
            Runtime(self.queue.root, {"worker": self.type()}).run(once=True)
            self.assertEqual(self.queue.state("orphan")["status"], "running")
        Runtime(self.queue.root, {"worker": self.type()}).run(once=True)
        self.assertEqual(self.queue.state("orphan")["status"], "interrupted")

    def test_partial_job_setup_is_recoverable_and_json_booleans_are_distinct(self):
        self.queue.submit("worker", "partial", input={"approved": True})
        with self.assertRaisesRegex(ValueError, "different input"):
            self.queue.submit("worker", "partial", input={"approved": 1})
        directory = self.queue.paths("partial")["directory"]
        (directory / "input.json").write_text("{unfinished")
        self.run_once({"worker": self.type()})
        state = self.queue.state("partial")
        self.assertEqual(state["status"], "done")
        self.assertEqual(state["result"]["input"], {"approved": True})

    def test_cli_options_can_surround_fixed_arguments_and_program_flags_pass_through(self):
        for index, arguments in enumerate([
            ["--root", str(self.queue.root), "worker", "a", "--model", "vendor/model", "words with spaces"],
            ["worker", "--root", str(self.queue.root), "b", "--model", "vendor/model", "words with spaces"],
            ["worker", "c", "--model", "vendor/model", "--root", str(self.queue.root), "words with spaces"],
        ]):
            storage = self.root / f"cli-{index}"
            job, workspace = storage / 'job', storage / 'work'
            job.mkdir(parents=True)
            workspace.mkdir()
            result = subprocess.run([sys.executable, str(ROOT / "tools/asys-runtime"), "submit", "--directory", str(job), "--workspace", str(workspace), *arguments], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            job_id = chr(ord("a") + index)
            self.assertEqual(self.queue.request(job_id)["args"], ["--model", "vendor/model", "words with spaces"])


if __name__ == "__main__":
    unittest.main()
