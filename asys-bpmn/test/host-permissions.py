"""Exercise private job/channel files with a host UID other than image UID 1000."""
import json
import os
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "asys-bpmn/tools/asys-bpmn"
EXAMPLE = ROOT / "asys-bpmn/examples/hello"

HOST = '''
import json, runpy, sys
from pathlib import Path
module = runpy.run_path(sys.argv[1])
Launcher = module['Launcher']
args = module['arguments'](['run', sys.argv[2] + '/workflow.bpmn', sys.argv[2] + '/env/dummy', '--root', sys.argv[3]])
launcher = Launcher(args)
launcher.say = lambda *args: None
if sys.argv[4] == 'setup':
    class Prepared(Exception): pass
    def command(*args, **kwargs): raise Prepared()
    launcher.command = command
    try: launcher.setup()
    except Prepared: launcher.snapshot()
    print(json.dumps({'directory': str(launcher.directory), 'definition': launcher.definition}))
else:
    launcher.directory = Path(sys.argv[5])
    launcher.record = json.loads((launcher.directory / 'run.json').read_text())
    launcher.id = launcher.record['id']
    launcher.channel = Path(launcher.record['channel'])
    launcher.names = launcher.record['components']
    launcher.owned = list(launcher.names.values())
    launcher.definition = sys.argv[6]
    launcher.variables = {}
    launcher.xml = Path(sys.argv[2] + '/workflow.bpmn').read_text()
    launcher.observe = lambda **kwargs: ({}, {name: {'status': {'status': 'running', 'health': 'healthy'}} for name in launcher.owned})
    launcher.wait_ready()
    launcher.execute()
'''

COMPONENT = '''
import { Environments } from './asys-runtime/javascript/environments.mjs';
import { Store } from './asys-bpmn/src/store.mjs';
import { WorkflowRuntime } from './asys-bpmn/src/runtime.mjs';
import { serveHostChannel } from './asys-bpmn/src/host-channel.mjs';
const [root] = process.argv.slice(1);
const runtime = new WorkflowRuntime({store:new Store(':memory:'), environments:new Environments(root)});
const controller = new AbortController();
process.on('SIGTERM', () => controller.abort());
console.log('ready');
await serveHostChannel(runtime, {root, signal:controller.signal});
await runtime.close();
'''


def main():
    assert os.getuid() == 0, "Run this test in the Docker test image as root"
    subprocess.run(['python3', str(ROOT / 'asys-oneshot/test/test_oneshot.py')],
                   user=1001, group=1001, extra_groups=[], check=True, timeout=30)
    with tempfile.TemporaryDirectory(prefix="asys-host-permissions-") as temporary:
        root = Path(temporary)
        root.chmod(0o777)
        host = ["python3", "-c", HOST, str(CLI), str(EXAMPLE), str(root / "runs")]
        prepared = subprocess.run(host + ["setup"], user=1001, group=1001, extra_groups=[],
                                  check=True, text=True, capture_output=True, timeout=10)
        info = json.loads(prepared.stdout)
        directory = Path(info["directory"])
        assert directory.stat().st_mode & 0o777 == 0o700
        with tempfile.TemporaryFile(mode="w+") as errors:
            worker = subprocess.Popen(['python3', str(ROOT / 'asys-runtime/tools/asys-runtime'),
                'run', str(EXAMPLE / 'env/dummy'), '--root', str(directory / 'runtime')],
                user=1001, group=1001, extra_groups=[], stdout=subprocess.DEVNULL, stderr=errors)
            component = subprocess.Popen(["node", "--input-type=module", "-e", COMPONENT,
                                          str(directory / "runtime"), info["definition"]],
                                         cwd=ROOT, user=1001, group=1001, extra_groups=[],
                                         stdout=subprocess.PIPE, stderr=errors, text=True)
            try:
                assert component.stdout.readline().strip() == "ready"
                try:
                    completed = subprocess.run(host + ["run", str(directory), info["definition"]],
                                               user=1001, group=1001, extra_groups=[], capture_output=True, text=True, timeout=15)
                except subprocess.TimeoutExpired as error:
                    errors.seek(0)
                    raise AssertionError(f"Host timed out; component status={component.poll()}: {errors.read()}") from error
                errors.seek(0)
                assert completed.returncode == 0, completed.stderr + errors.read()
                assert json.loads(completed.stdout)['greet']['message'] == 'Hello from asys.'
                jobs = list((directory / 'jobs').iterdir())
                assert len(jobs) == 1
                assert jobs[0].stat().st_uid == 1001
                assert (jobs[0] / 'result.json').stat().st_uid == 1001
                print("PASS: host, workflow, and worker share private job files as UID 1001")
            finally:
                component.terminate()
                component.wait(timeout=10)
                component.stdout.close()
                worker.terminate()
                worker.wait(timeout=10)


if __name__ == "__main__":
    main()
