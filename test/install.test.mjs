import assert from 'node:assert/strict';
import { cp, mkdir, mkdtemp, readFile, readdir, rm, stat, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import test from 'node:test';

const exec = promisify(execFile);
const source = fileURLToPath(new URL('..', import.meta.url));

async function checkout(t, extra = []) {
  const root = await mkdtemp(join(tmpdir(), 'asys-host-install-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const project = join(root, 'checkout');
  await mkdir(project);
  for (const path of ['Makefile', 'LICENSE', 'tools', 'python', 'skills', 'asys-runtime/asys_runtime',
    'asys-runtime/LICENSE.multiagent', ...extra]) {
    await cp(join(source, path), join(project, path), { recursive: true });
  }
  return { root, project, library: join(root, 'staged/opt/asys/share/asys/python') };
}

test('core asys installs and prepares system agents without a launcher or source checkout', async t => {
  const { root, project, library } = await checkout(t);
  await exec('make', ['--no-print-directory', '-C', project, 'install-host', 'PREFIX=/opt/asys', `DESTDIR=${root}/staged`]);
  await rm(project, { recursive: true });
  const binary = join(root, 'staged/opt/asys/bin/asys');
  const { stdout } = await exec('python3', ['-I', '-S', binary, 'ps', '--json', '--root', join(root, 'absent')], { cwd: root });
  assert.deepEqual(JSON.parse(stdout), []);
  const state = join(root, 'state');
  const { stdout: unset } = await exec('python3', ['-I', '-S', binary, 'system-model', 'list', '--json', '--root', state], { cwd: root });
  assert.deepEqual(JSON.parse(unset), { simple: null });
  await exec('python3', ['-I', '-S', binary, 'system-model', 'set', 'simple', 'account/model', '--root', state], { cwd: root });
  const { stdout: models } = await exec('python3', ['-I', '-S', binary, '--root', state, 'system-model', 'list', '--json'], { cwd: root });
  assert.deepEqual(JSON.parse(models), { simple: 'account/model' });
  const { stdout: prepared } = await exec('python3', ['-I', '-S', '-c', `
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from asys.config import system_model
from asys.system_agents import agent_names, agent_prompt, prepare_agent
from asys_runtime.environment import Environment
environment = Path('environment')
environment.mkdir()
(environment / 'workers.json').write_text(json.dumps({
    'version': 1, 'name': 'fixture', 'egress': True, 'types': {'program': {'command': ['true']}}}))
bundle = prepare_agent('simple', Path('run'), Environment(environment), system_model('simple'))
assert 'asys.oneshot' not in sys.modules
print(json.dumps({'agents': agent_names(), 'prompt': agent_prompt('simple'),
    'snapshot': (bundle / 'agents/simple/prompt.md').read_text(),
    'workers': Environment(environment, external=bundle).config}))
`, library], { cwd: root, env: { ...process.env, ASYS_STATE_ROOT: state } });
  const definition = JSON.parse(prepared);
  assert.deepEqual(definition.agents, ['simple']);
  const prompt = await readFile(join(source, 'python/asys/system_agents/simple/prompt.md'), 'utf8');
  assert.equal(definition.prompt, prompt);
  assert.equal(definition.snapshot, prompt);
  assert.equal(definition.workers.name, 'fixture');
  assert.equal(definition.workers.egress, true);
  assert.deepEqual(definition.workers.types, { agent: { command: [
    '/opt/asys/asys-workers/tools/asys-agent', '--agent', 'simple', '--model', 'account/model',
  ] } });
});

test('the portable skill exports from a staged installation without the source checkout or agent configuration', async t => {
  const { root, project } = await checkout(t);
  const prefix = join(root, 'staged/opt/asys');
  await exec('make', ['--no-print-directory', '-C', project, 'install-host', 'PREFIX=/opt/asys', `DESTDIR=${root}/staged`]);
  await rm(project, { recursive: true });
  const binary = join(prefix, 'bin/asys');
  const invoke = (...args) => exec('python3', ['-I', '-S', binary, 'skill', ...args], { cwd: root });
  const bundled = join(prefix, 'share/asys/skills/asys');
  assert.equal((await invoke()).stdout.trim(), bundled);
  await assert.rejects(invoke(join(bundled, 'nested')), error => error.code === 1 && /outside/.test(error.stderr));

  const parent = join(root, 'agent settings/skills');
  const exported = join(parent, 'asys');
  assert.equal((await invoke(parent)).stdout.trim(), exported);
  async function compare(relative = '') {
    for (const entry of await readdir(join(source, 'skills/asys', relative), { withFileTypes: true })) {
      const path = join(relative, entry.name);
      if (entry.isDirectory()) await compare(path);
      else {
        assert.deepEqual(await readFile(join(exported, path)), await readFile(join(source, 'skills/asys', path)), path);
        assert.equal((await stat(join(bundled, path))).mode & 0o777, 0o644, path);
      }
    }
  }
  await compare();
  // Local edits and symlinked destinations must not be overwritten by a copy.
  await writeFile(join(exported, 'SKILL.md'), 'locally maintained instructions\n');
  await assert.rejects(invoke(parent), error => error.code === 1 && /already exists/.test(error.stderr));
  assert.equal(await readFile(join(exported, 'SKILL.md'), 'utf8'), 'locally maintained instructions\n');
  const linked = join(root, 'linked');
  await mkdir(linked);
  await symlink(exported, join(linked, 'asys'));
  await assert.rejects(invoke(linked), error => error.code === 1 && /already exists/.test(error.stderr));
  await rm(bundled, { recursive: true });
  assert.match(await readFile(join(exported, 'references/environments-and-teams.md'), 'utf8'), /workers.json/);
  await assert.rejects(invoke(), error => error.code === 1 && /reinstall asys/.test(error.stderr));
});

test('one-shot installs as a consumer of the core simple agent', async t => {
  const { root, project, library } = await checkout(t, ['asys-oneshot/Makefile', 'asys-oneshot/tools']);
  await exec('make', ['--no-print-directory', '-C', join(project, 'asys-oneshot'), 'install-host', 'PREFIX=/opt/asys', `DESTDIR=${root}/staged`]);
  await rm(project, { recursive: true });
  const { stdout: help } = await exec('python3', ['-I', '-S', join(root, 'staged/opt/asys/bin/asys-oneshot'), '--help'], { cwd: root });
  assert.match(help, /ENVIRONMENT_DIRECTORY PROMPT/);
  assert.match(help, /--workspace DIRECTORY/);
  assert.match(help, /--model MODEL/);
  assert.match(help, /simple system agent/);
  assert.doesNotMatch(help, /AGENT|--external/);
  const { stdout: prompt } = await exec('python3', ['-I', '-S', '-c', `
import sys
sys.path.insert(0, sys.argv[1])
from asys.oneshot import AGENT
from asys.system_agents import agent_prompt
assert AGENT == 'simple'
print(agent_prompt(AGENT), end='')
`, library], { cwd: root });
  assert.equal(prompt, await readFile(join(source, 'python/asys/system_agents/simple/prompt.md'), 'utf8'));
});

test('the goal launcher installs independently of one-shot and uses the simple default', async t => {
  const { root, project } = await checkout(t, ['asys-goal/Makefile', 'asys-goal/tools']);
  await exec('make', ['--no-print-directory', '-C', join(project, 'asys-goal'), 'install-host', 'PREFIX=/opt/asys', `DESTDIR=${root}/staged`]);
  await rm(project, { recursive: true });
  const binary = join(root, 'staged/opt/asys/bin/asys-goal');
  const { stdout: help } = await exec('python3', ['-I', '-S', binary, '--help'], { cwd: root });
  assert.match(help, /ENVIRONMENT_DIRECTORY GOAL/);
  assert.match(help, /--max-attempts N/);
  assert.doesNotMatch(help, /AGENT|--external/);
  await assert.rejects(exec('python3', ['-I', '-S', binary, 'absent-environment', 'Achieve the goal'],
    { cwd: root, env: { ...process.env, ASYS_STATE_ROOT: join(root, 'empty-state') } }),
    error => error.code === 1 && /asys system-model set simple MODEL/.test(error.stderr));
});
