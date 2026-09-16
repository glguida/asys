#!/usr/bin/env python3
"""Cross-user acceptance test, run as root in an isolated asys-bpmn container.

Mount the source checkout at /source, then run this script with Python.
It creates no host accounts and uses only temporary container storage.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

SOURCE = Path(__file__).resolve().parents[1]


def run(uid, *command, success=True):
    def identity():
        os.setgroups([65534] if uid != 12003 else [])
        os.setgid(12000)
        os.setuid(uid)
        os.umask(0o077)
    env = {**os.environ, 'ASYS_STATE_ROOT': str(state),
           'PYTHONPATH': f'{SOURCE / "python"}:{SOURCE / "asys-runtime"}'}
    result = subprocess.run(command, cwd=homes[uid], env=env, preexec_fn=identity,
                            capture_output=True, text=True, timeout=60)
    if bool(result.returncode == 0) != success:
        raise AssertionError(f'UID {uid}: {command}\n{result.stdout}\n{result.stderr}')
    return result.stdout


with tempfile.TemporaryDirectory(prefix='asys-shared-') as temporary:
    base = Path(temporary)
    base.chmod(0o777)
    state = base / 'nested/state'
    workspace = base / 'workspace'
    workspace.mkdir()
    os.chown(workspace, 12001, 65534)
    workspace.chmod(0o2770)
    homes = {uid: base / str(uid) for uid in (12001, 12002, 12003)}
    for uid, path in homes.items():
        path.mkdir()
        os.chown(path, uid, 12000)
    for uid in (12001, 12002):
        run(uid, sys.executable, str(SOURCE / 'tools/asys'), 'init', str(state), '--group', 'nogroup')
    run(12002, sys.executable, str(SOURCE / 'tools/asys'), 'init', str(state))
    for uid in (12001, 12002):
        selected = run(uid, 'bash', '-ec',
                       'unset ASYS_STATE_ROOT; source "$1"; printf "%s" "$ASYS_STATE_ROOT"',
                       'bash', str(state / 'asys-env'))
        assert selected == str(state), selected
        assert not (homes[uid] / 'asys-env').exists()
    created = run(12001, sys.executable, '-c', f'''
import json, os
from pathlib import Path
from asys.execution import prepare_run, EnvironmentHost
from asys_runtime.permissions import mkdir
from asys_runtime.files import write_json
from asys_runtime.queue import Queue
root = Path(os.environ['ASYS_STATE_ROOT'])
id, directory, workspace = prepare_run(root / 'runs', Path({str(workspace)!r}))
job = directory / 'jobs/example'
mkdir(job)
queue = Queue(directory / 'runtime/environments/test')
queue.submit('program', 'example', directory=job, workspace=workspace, args=['-c',
    "import os; from pathlib import Path; print('shared log'); Path(os.environ['ASYS_RESULT']).write_text('{{\\\"final\\\": \\\"done\\\"}}')"])
write_json(directory / 'run.json', {{'id': id, 'name': 'shared', 'status': 'completed', 'workspace': str(workspace)}})
host = EnvironmentHost.__new__(EnvironmentHost)
host.directory, host.record = directory, {{'workspace': str(workspace)}}
assert host.execution_mounts()[1] == f'{{os.getuid()}}:65534'
print(directory)
''').strip()
    directory = Path(created)
    # Python and JavaScript both participate in this shared state. A second
    # identity must be able to open the queue and locks, execute and inspect.
    run(12002, sys.executable, '-c', f'''
from asys_runtime import Runtime
from asys_runtime.queue import Queue
queue = Queue({str(directory / 'runtime/environments/test')!r})
Runtime(queue.root, {{'program': {{'command': ['python3']}}}}).run(once=True)
state = queue.state('example')
assert state['status'] == 'done', state
assert state['result']['final'] == 'done', state
''')
    for uid in (12001, 12002):
        output = run(uid, sys.executable, str(SOURCE / 'tools/asys'), 'logs', str(directory), 'example')
        assert 'shared log' in output, output
    run(12003, sys.executable, str(SOURCE / 'tools/asys'), 'status', str(directory), success=False)
    queue_module = (SOURCE / 'asys-runtime/javascript/queue.mjs').as_uri()
    permissions_module = (SOURCE / 'asys-runtime/javascript/permissions.mjs').as_uri()
    run(12001, 'node', '--input-type=module', '-e', f'''
import {{ Queue }} from {json.dumps(queue_module)};
import {{ makeDirectory }} from {json.dumps(permissions_module)};
const job = {json.dumps(str(directory / 'jobs/javascript'))};
makeDirectory(job);
const queue = new Queue({json.dumps(str(directory / 'runtime/environments/test'))});
await queue.submit('program', 'javascript', {{ directory: job, workspace: {json.dumps(str(workspace))} }});
''')
    run(12002, sys.executable, '-c', f'''
from asys_runtime import Runtime
from asys_runtime.queue import Queue
queue = Queue({str(directory / 'runtime/environments/test')!r})
Runtime(queue.root, {{'program': {{'command': ['true']}}}}).run(once=True)
assert queue.state('javascript')['status'] == 'done'
''')
    module = (SOURCE / 'asys-bpmn/src/store.mjs').as_uri()
    database = str(directory / 'workflow')
    run(12001, 'node', '--input-type=module', '-e', f'''
import {{ Store }} from {json.dumps(module)};
const store = new Store({json.dumps(database)});
store.loadWorkflow({{ id: 'workflow', name: 'shared' }});
store.save({{ id: 'run', workflowId: 'workflow', status: 'failed' }});
store.close();
''')
    run(12002, 'node', '--input-type=module', '-e', f'''
import {{ Store }} from {json.dumps(module)};
const store = new Store({json.dumps(database)});
if (store.run('run').status !== 'failed') throw new Error('Missing saved run');
store.save({{ id: 'run', workflowId: 'workflow', status: 'completed' }});
store.close();
''')
    print('PASS: shared initialization, job execution, logs, SQLite reopen, container group selection; nonmember denied')
