import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { execFile } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import { agentDefinition, agentResult } from '../src/agent-definition.mjs';
import { runAgent } from '../src/agent-session.mjs';
import { agent } from '../src/agent.mjs';
import { assistant, model } from './helpers.mjs';

test('named agents share environment resources and load their own memory and skills', async t => {
  const root = await mkdtemp(join(tmpdir(), 'asys-agent-definition-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const environment = join(root, 'environment'), workspace = join(root, 'work'), jobDirectory = join(root, 'job');
  await mkdir(workspace);
  for (const name of ['pcb', 'schematic']) {
    await mkdir(join(environment, 'agents', name, 'skills', name), { recursive: true });
    await writeFile(join(environment, 'agents', name, 'memory.md'), `${name} retained lesson`);
    await writeFile(join(environment, 'agents', name, 'prompt.md'), `${name} base instructions`);
    await writeFile(join(environment, 'agents', name, 'skills', name, 'SKILL.md'), `---\nname: ${name}\ndescription: ${name} techniques\n---\n${name} procedure`);
  }
  await mkdir(join(environment, 'skills', 'common'), { recursive: true });
  await writeFile(join(environment, 'skills', 'common', 'SKILL.md'), '---\nname: common\ndescription: Common techniques\n---\nCommon procedure');
  const requests = [], job = { id: 'board' };
  const provider = {
    async listModels() { return { models: [model] }; },
    async *infer(request) {
      requests.push(JSON.parse(request.payload));
      const message = assistant([{ type: 'text', text: '{"final":"Board complete","exception":null,"approved":false}' }]);
      yield { payload: JSON.stringify({ type: 'done', reason: 'stop', message }) };
    },
  };
  const definition = agentDefinition(environment, 'pcb');
  const result = await runAgent({ config: { model: model.id, prompt: 'Route the board.', maxSteps: 2, options: {} },
    job, workspace, jobDirectory, definition, provider,
    signal: new AbortController().signal, save() {}, event() {} });
  assert.deepEqual(result, { final: 'Board complete', exception: null, approved: false });
  const prompt = requests[0].context.systemPrompt;
  assert.match(prompt, /pcb retained lesson/);
  assert.match(prompt, /pcb base instructions/);
  assert.match(prompt, /Common techniques/);
  assert.match(prompt, /pcb techniques/);
  assert.doesNotMatch(prompt, /schematic retained lesson|schematic techniques|schematic base instructions/);
  assert.match(prompt, /report\.md/);
  assert.match(prompt, /lessons\.md/);
  const reportingGuide = await readFile(new URL('../src/reporting-to-humans.md', import.meta.url), 'utf8');
  assert.ok(prompt.includes(reportingGuide.trim()), 'the model receives the complete shared reporting guide');
  assert.doesNotMatch(prompt, /ASYS_RESULT/);
  assert.equal(job.agent.name, 'pcb');
  assert.equal(await readFile(join(environment, 'agents/pcb/memory.md'), 'utf8'), 'pcb retained lesson');
  assert.equal(await readFile(join(jobDirectory, '.pi/system-prompt.txt'), 'utf8'), prompt);
  await assert.rejects(readFile(join(workspace, '.pi/system-prompt.txt')), { code: 'ENOENT' });
});

test('an external agent uses its own prompt and memory alongside shared environment resources', async t => {
  const root = await mkdtemp(join(tmpdir(), 'asys-external-agent-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const environment = join(root, 'environment'), external = join(root, 'bundle');
  const workspace = join(root, 'work'), jobDirectory = join(root, 'job');
  await mkdir(workspace);
  for (const directory of [environment, external]) {
    await mkdir(join(directory, 'agents', 'reviewer'), { recursive: true });
  }
  await writeFile(join(environment, 'agents/reviewer/prompt.md'), 'Builtin reviewer instructions');
  await writeFile(join(environment, 'agents/reviewer/memory.md'), 'Builtin private memory');
  await writeFile(join(external, 'agents/reviewer/prompt.md'), 'External reviewer instructions');
  await writeFile(join(external, 'agents/reviewer/memory.md'), 'External retained lesson');
  for (const [directory, name] of [[environment, 'environment-common'], [external, 'bundle-common'],
    [join(external, 'agents/reviewer'), 'reviewer-specific']]) {
    await mkdir(join(directory, 'skills', name), { recursive: true });
    await writeFile(join(directory, 'skills', name, 'SKILL.md'), `---\nname: ${name}\ndescription: ${name} techniques\n---\nProcedure`);
  }
  await writeFile(join(external, 'extra.mjs'), `export default pi => {
    pi.on('before_agent_start', event => ({ systemPrompt: event.systemPrompt + '\\nExternal explicit extension' }));
  };`);
  const requests = [];
  const provider = {
    async listModels() { return { models: [model] }; },
    async *infer(request) {
      requests.push(JSON.parse(request.payload));
      yield { payload: JSON.stringify({ type: 'done', reason: 'stop',
        message: assistant([{ type: 'text', text: '{"final":"Reviewed","exception":null}' }]) }) };
    },
  };
  const result = await agent({
    job: { id: 'review', input: { prompt: 'Review the latest result.' }, workspace,
      directory: jobDirectory, result: join(jobDirectory, 'result.json') },
    argv: ['--agent', 'reviewer', '--model', model.id, '--extension', 'extra.mjs'],
    env: { ASYS_ENVIRONMENT_DIR: environment, ASYS_WORKERS_DIR: external },
    signal: new AbortController().signal,
  }, { provider });
  assert.deepEqual(result, { final: 'Reviewed', exception: null });
  const { context } = requests[0];
  for (const text of ['External reviewer instructions', 'External retained lesson', 'External explicit extension',
    'environment-common techniques', 'bundle-common techniques', 'reviewer-specific techniques']) {
    assert.ok(context.systemPrompt.includes(text), text);
  }
  assert.doesNotMatch(context.systemPrompt, /Builtin reviewer instructions|Builtin private memory/);
  assert.deepEqual(context.messages[0].content, [{ type: 'text', text: 'Review the latest result.' }]);
  const state = JSON.parse(await readFile(join(jobDirectory, 'agent.json'), 'utf8'));
  assert.equal(state.agent.environment, environment);
  assert.equal(state.agent.directory, join(external, 'agents/reviewer'));
  assert.equal(state.agent.promptHash, agentDefinition(environment, 'reviewer', external).promptHash);
  assert.equal(await readFile(join(external, 'agents/reviewer/memory.md'), 'utf8'), 'External retained lesson');
  await mkdir(join(environment, 'agents', 'builtin-only'));
  assert.throws(() => agentDefinition(environment, 'builtin-only', external), { code: 'ENOENT' });
});

test('completion accepts only the agreed final JSON and preserves task-specific results', () => {
  const message = text => assistant([{ type: 'text', text }]);
  assert.deepEqual(agentResult(message('{"final":"Missing input","exception":"No schematic"}')),
    { final: 'Missing input', exception: 'No schematic' });
  for (const text of ['Finished', '{}', '[]', '{"final":"Done"}', '{"final":"Done","exception":""}', '{"final":"Done","exception":false}']) {
    assert.throws(() => agentResult(message(text)), /Agent/);
  }
});

test('the shared simple agent supplies its packaged instructions and model to a real session', async t => {
  const root = await mkdtemp(join(tmpdir(), 'asys-system-agent-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const environment = join(root, 'environment'), workspace = join(root, 'work');
  const run = join(root, 'run'), jobDirectory = join(run, 'job');
  await mkdir(workspace);
  await mkdir(join(environment, 'agents/simple'), { recursive: true });
  await writeFile(join(environment, 'agents/simple/prompt.md'), 'UNSELECTED_ENVIRONMENT_AGENT');
  await writeFile(join(environment, 'agents/simple/memory.md'), 'UNSELECTED_ENVIRONMENT_MEMORY');
  await writeFile(join(environment, 'workers.json'), JSON.stringify({ version: 1, name: 'test',
    types: { program: { command: ['false'] } } }));
  await promisify(execFile)('python3', ['-c', `
import sys
from pathlib import Path
sys.path[:0] = sys.argv[4:]
from asys.system_agents import prepare_agent
from asys_runtime.environment import Environment
prepare_agent('simple', Path(sys.argv[1]), Environment(sys.argv[2]), sys.argv[3])
assert 'asys.oneshot' not in sys.modules
`, run, environment, model.id, fileURLToPath(new URL('../../python', import.meta.url)),
  fileURLToPath(new URL('../../asys-runtime', import.meta.url))]);
  const external = join(run, 'external');
  const { types } = JSON.parse(await readFile(join(external, 'workers.json'), 'utf8'));
  const requests = [];
  const provider = {
    async listModels() { return { models: [model] }; },
    async *infer(request) {
      assert.equal(request.model, model.id);
      requests.push(JSON.parse(request.payload));
      yield { payload: JSON.stringify({ type: 'done', reason: 'stop',
        message: assistant([{ type: 'text', text: '{"final":"Assignment finished","exception":null}' }]) }) };
    },
  };
  await agent({ job: { id: 'one', input: { prompt: 'Complete the assignment.' }, workspace,
    directory: jobDirectory, result: join(jobDirectory, 'result.json') },
    argv: types.agent.command.slice(1), env: { ASYS_ENVIRONMENT_DIR: environment, ASYS_WORKERS_DIR: external },
    signal: new AbortController().signal,
  }, { provider });
  const packaged = await readFile(new URL('../../python/asys/system_agents/simple/prompt.md', import.meta.url), 'utf8');
  assert.ok(requests[0].context.systemPrompt.includes(packaged.trim()));
  assert.doesNotMatch(requests[0].context.systemPrompt, /UNSELECTED_ENVIRONMENT_AGENT|UNSELECTED_ENVIRONMENT_MEMORY/);
  assert.deepEqual(requests[0].context.messages[0].content, [{ type: 'text', text: 'Complete the assignment.' }]);
  const { agent: state } = JSON.parse(await readFile(join(jobDirectory, 'agent.json'), 'utf8'));
  assert.equal(state.name, 'simple');
  assert.equal(state.model, model.id);
});
