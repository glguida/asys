import assert from 'node:assert/strict';
import { spawn, execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { once } from 'node:events';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const exec = promisify(execFile);
const cli = fileURLToPath(new URL('../tools/asys-bpmn', import.meta.url));
const workflow = fileURLToPath(new URL('../examples/hello/workflow.bpmn', import.meta.url));
const environment = fileURLToPath(new URL('../examples/hello/env/dummy', import.meta.url));

test('the worker override is internal and does not appear in public help', async () => {
  const { stdout } = await exec('python3', [cli, 'run', '--help']);
  assert.doesNotMatch(stdout, /--external/);
  const { stdout: selected } = await exec('python3', ['-c', `
import runpy, sys
module = runpy.run_path(sys.argv[1])
args = module['arguments'](['run', 'workflow.bpmn', 'env', '--external', 'internal-workers'])
print(args.external)
`, cli]);
  assert.equal(selected.trim(), 'internal-workers');
});

test('resume preserves saved configuration, enforces launcher ownership and skips the previous failure', async () => {
  const child = spawn('python3', [fileURLToPath(new URL('./resume-cli.py', import.meta.url))]);
  let error = '';
  child.stderr.on('data', chunk => { error += chunk; });
  const [code] = await once(child, 'close');
  assert.equal(code, 0, error);
});

async function preparedVariables(t, text, stdin = false) {
  const root = await mkdtemp(join(tmpdir(), 'workflow-cli-request-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const args = ['run', workflow, environment, '--root', root];
  if (text !== undefined) {
    const path = join(root, 'design brief.md');
    if (!stdin) await writeFile(path, text);
    args.push('--input', stdin ? '-' : path);
  }
  // Observe the prepared start variables at the first dcomp call. No Docker or
  // component is needed to check how the host reads and passes the request.
  const child = spawn('python3', ['-c', `
import json, runpy, sys
from unittest.mock import patch
module = runpy.run_path(sys.argv[1])
launcher = module['Launcher'](module['arguments'](sys.argv[2:]))
try:
    with patch.object(launcher, 'command', side_effect=StopIteration):
        launcher.setup()
except StopIteration:
    print(json.dumps(launcher.variables))
finally:
    if launcher.lease:
        launcher.lease.close()
`, cli, ...args]);
  t.after(() => child.kill('SIGTERM'));
  const closed = once(child, 'close');
  let output = '', error = '';
  child.stdout.on('data', data => { output += data; });
  child.stderr.on('data', data => { error += data; });
  child.stdin.end(stdin ? text : undefined);
  assert.equal((await closed)[0], 0, error);
  return JSON.parse(output);
}

test('Markdown input becomes one request string with its formatting and literal text preserved', async t => {
  const text = '# Board brief\n\nDesign a 30×20 mm board.\n\n- Use `USB-C`.\n- Keep $HOME and $(commands) literal.\n';
  assert.deepEqual(await preparedVariables(t, text), { request: text });
});

test('stdin is request text even when it looks like a JSON object', async t => {
  const text = '{"request":"Quoted request","model":"not a model setting"}\n';
  assert.deepEqual(await preparedVariables(t, text, true), { request: text });
});

test('workflows without an input file need no request variables', async t => {
  assert.deepEqual(await preparedVariables(t), {});
});

test('Ctrl-C while waiting for stdin exits without creating components or run state', { timeout: 10000 }, async t => {
  const root = await mkdtemp(join(tmpdir(), 'workflow-cli-input-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const child = spawn('python3', [cli, 'run', '--input', '-', workflow, '--root', root, environment]);
  t.after(() => { child.kill('SIGTERM'); child.stdin.destroy(); });
  let output = '';
  const closed = once(child, 'close');
  const ready = new Promise(resolve => child.stderr.on('data', chunk => {
    output += chunk;
    if (output.includes('Reading workflow request from stdin')) resolve();
  }));
  await Promise.race([ready, closed.then(() => { throw new Error(`Launcher exited early: ${output}`); })]);
  child.kill('SIGINT');
  const [code] = await closed;
  assert.equal(code, 130, output);
  await assert.rejects(readFile(join(root, 'runs')), { code: 'ENOENT' });
});

test('dcomp without container user support is rejected before starting components', { timeout: 10000 }, async t => {
  const root = await mkdtemp(join(tmpdir(), 'workflow-cli-version-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const dcomp = join(root, 'dcomp');
  await writeFile(dcomp, '#!/usr/bin/env python3\nimport sys\nassert sys.argv[1:] == ["version", "--json"]\nprint(\'{"version":"0.3.0","api_version":2}\')\n', { mode: 0o755 });
  const child = spawn('python3', [cli, 'run', workflow, environment, '--root', root], {
    env: { ...process.env, DCOMP_BINARY: dcomp },
  });
  let error = '';
  child.stderr.on('data', chunk => { error += chunk; });
  const [code] = await once(child, 'close');
  assert.equal(code, 1, error);
  assert.match(error, /Requires dcomp 0\.3/);
});

test('the launcher gives both components fixed queue, job and workspace mounts through dcomp', async () => {
  const { stdout } = await exec('python3', ['-c', `
import json, os, runpy, tempfile
from pathlib import Path
module = runpy.run_path(${JSON.stringify(fileURLToPath(new URL('../tools/asys-bpmn', import.meta.url)))})
temporary = tempfile.TemporaryDirectory()
os.environ['ASYS_STATE_ROOT'] = str(Path(temporary.name) / 'other-system')
launcher = module['Launcher'](module['arguments'](['run', 'workflow.bpmn', 'env', '--root', temporary.name]))
launcher.directory = Path(temporary.name)
(launcher.directory / 'config.json').write_text(json.dumps({'system_models': {'simple': 'account/goal'}, 'private': 'not mounted'}))
launcher.record = {'links': {}, 'egress': False, 'workspace': str(Path.cwd())}
launcher.wait_ready = lambda: None
launcher.snapshot = lambda: None
launcher.say = lambda message: None
launcher.channel = launcher.directory / 'runtime/channels/workflow'
calls = []
launcher.add = lambda *args: calls.append(args)
launcher.start_components()
models = json.loads((launcher.directory / 'system-models.json').read_text())
print(json.dumps({'directory': temporary.name, 'calls': calls, 'models': models}))
`]);
  const { directory, calls, models } = JSON.parse(stdout);
  assert.deepEqual(models, { simple: 'account/goal' });
  assert.ok(calls[0][2].includes(`${directory}/system-models.json,/etc/asys/system-models.json,ro`));
  assert.ok(!calls[1][2].includes(`${directory}/system-models.json,/etc/asys/system-models.json,ro`));
  assert.deepEqual(calls.map(call => call[0]), ['workers', 'engine']);
  for (const [, , args] of calls) {
    assert.equal(args[args.indexOf('--user') + 1], `${process.getuid()}:${process.getgid()}`);
    assert.ok(args.includes(`${process.cwd()},/var/lib/asys/workspace,rw`));
    for (const name of ['runtime', 'jobs']) assert.ok(args.includes(`${directory}/${name},/var/lib/asys/${name},rw`));
  }
});
