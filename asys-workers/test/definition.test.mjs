import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { agentDefinition, agentResult } from '../src/agent-definition.mjs';
import { runAgent } from '../src/agent-session.mjs';
import { assistant, model } from './helpers.mjs';

test('named agents share environment resources and load their own memory and skills', async t => {
  const root = await mkdtemp(join(tmpdir(), 'asys-agent-definition-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const environment = join(root, 'environment'), workspace = join(root, 'work'), jobDirectory = join(root, 'job');
  await mkdir(workspace);
  for (const name of ['pcb', 'schematic']) {
    await mkdir(join(environment, 'agents', name, 'skills', name), { recursive: true });
    await writeFile(join(environment, 'agents', name, 'memory.md'), `${name} retained lesson`);
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
  assert.match(prompt, /Common techniques/);
  assert.match(prompt, /pcb techniques/);
  assert.doesNotMatch(prompt, /schematic retained lesson|schematic techniques/);
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

test('completion accepts only the agreed final JSON and preserves task-specific results', () => {
  const message = text => assistant([{ type: 'text', text }]);
  assert.deepEqual(agentResult(message('{"final":"Missing input","exception":"No schematic"}')),
    { final: 'Missing input', exception: 'No schematic' });
  for (const text of ['Finished', '{}', '[]', '{"final":"Done"}', '{"final":"Done","exception":""}', '{"final":"Done","exception":false}']) {
    assert.throws(() => agentResult(message(text)), /Agent/);
  }
});
