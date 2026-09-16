import assert from 'node:assert/strict';
import { cp, mkdir, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import test from 'node:test';

const exec = promisify(execFile);
const source = fileURLToPath(new URL('..', import.meta.url));

test('asys installs and runs independently of BPMN, Docker, and Python site packages', async t => {
  const root = await mkdtemp(join(tmpdir(), 'asys-host-install-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const project = join(root, 'checkout');
  await mkdir(project);
  for (const path of ['Makefile', 'LICENSE', 'tools', 'python', 'asys-runtime/asys_runtime', 'asys-runtime/LICENSE.multiagent',
    'asys-oneshot/Makefile', 'asys-oneshot/tools']) {
    await cp(join(source, path), join(project, path), { recursive: true });
  }
  await exec('make', ['--no-print-directory', '-C', project, 'install-host', 'PREFIX=/opt/asys', `DESTDIR=${root}/staged`]);
  await exec('make', ['--no-print-directory', '-C', join(project, 'asys-oneshot'), 'install-host', 'PREFIX=/opt/asys', `DESTDIR=${root}/staged`]);
  await rm(project, { recursive: true });
  const binary = join(root, 'staged/opt/asys/bin/asys');
  const { stdout } = await exec('python3', ['-I', '-S', binary, 'ps', '--json', '--root', join(root, 'absent')], { cwd: root });
  assert.deepEqual(JSON.parse(stdout), []);
  const { stdout: help } = await exec('python3', ['-I', '-S', join(root, 'staged/opt/asys/bin/asys-oneshot'), '--help'], { cwd: root });
  assert.match(help, /ENVIRONMENT_DIRECTORY AGENT PROMPT/);
  assert.match(help, /--workspace DIRECTORY/);
});
