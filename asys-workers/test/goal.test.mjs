import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';
import { goal } from '../src/goal.mjs';
import { systemModel } from '../src/system-model.mjs';
import { assistant, model } from './helpers.mjs';

const implemented = { final: 'IMPLEMENTER_CLAIMS_SUCCESS', exception: null };
const checked = (verified = true) => ({ final: verified ? 'Observed the required output.' : 'Output is still missing.',
  exception: null, verified, criteria: [{ requirement: 'Produce the required output', satisfied: verified,
    evidence: [{ source: 'cat output.txt', observation: verified ? 'The file contains the required output.' : 'File does not exist.' }] }] });
const blocked = { final: 'Inspected the inputs. The required specification is missing; existing work is preserved.',
  exception: 'Missing specification', question: 'Please supply the expected output format.' };

async function fixture(t, input = {}) {
  const root = await mkdtemp(join(tmpdir(), 'asys-goal-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const directory = join(root, 'job'), workspace = join(root, 'workspace'), environment = join(root, 'environment');
  for (const path of [directory, workspace, environment]) await mkdir(path);
  await mkdir(join(environment, 'agents/simple'), { recursive: true });
  await writeFile(join(environment, 'agents/simple/prompt.md'), 'ENVIRONMENT_PRIVATE_PROMPT');
  await writeFile(join(environment, 'agents/simple/memory.md'), 'ENVIRONMENT_PRIVATE_MEMORY');
  const models = join(root, 'models.json');
  await writeFile(models, JSON.stringify({ simple: model.id }));
  const controller = new AbortController(), events = [];
  const context = { job: { id: 'goal-test', directory, workspace, result: join(directory, 'result.json'),
    input: { goal: 'Produce the required output', ...input } }, argv: [],
    env: { ASYS_ENVIRONMENT_DIR: environment, ASYS_SYSTEM_MODELS: models }, signal: controller.signal };
  return { root, directory, workspace, models, controller, context, events,
    run(dependencies = {}) { return goal(context, { event: (type, data) => events.push({ type, ...data }), ...dependencies }); },
    state: () => readFile(join(directory, 'goal.json'), 'utf8').then(JSON.parse),
  };
}

test('goal repeats fresh phases using the same simple agent and model, preserving findings and lessons', async t => {
  const f = await fixture(t), calls = [];
  const result = await f.run({ async executeAgent(context, dependencies) {
    calls.push(context);
    assert.match(dependencies.definition.prompt, /simple system agent supplied by asys/);
    assert.equal(dependencies.definition.memory, '');
    assert.deepEqual(context.argv, ['--agent', 'simple', '--model', model.id]);
    if (calls.length === 1) {
      await writeFile(join(context.job.directory, 'lessons.md'), 'CHECK_THE_OUTPUT_FILE');
      return implemented;
    }
    if (calls.length === 2) return checked(false);
    if (calls.length === 3) return implemented;
    return checked();
  } });
  assert.equal(result.verified, true);
  assert.equal(result.attempts, 2);
  assert.equal(new Set(calls.map(call => call.job.id)).size, 4);
  assert.equal(new Set(calls.map(call => call.job.directory)).size, 4);
  assert.equal(new Set(calls.map(call => call.job.workspace)).size, 1);
  assert.match(calls[2].job.input.prompt, /Output is still missing/);
  assert.match(calls[2].job.input.prompt, /CHECK_THE_OUTPUT_FILE/);
  for (const call of [calls[1], calls[3]]) {
    assert.doesNotMatch(call.job.input.prompt, /IMPLEMENTER_CLAIMS_SUCCESS|CHECK_THE_OUTPUT_FILE|Output is still missing/);
    assert.match(call.job.input.prompt, /not evidence|not proof/);
  }
  const state = await f.state();
  assert.equal(state.goal, f.context.job.input.goal);
  assert.equal(state.status, 'completed');
  assert.equal(state.sessions[0].lessons, 'CHECK_THE_OUTPUT_FILE');
  for (const session of state.sessions) {
    assert.deepEqual(JSON.parse(await readFile(join(f.directory, session.directory, 'result.json'))), session.result);
  }
});

test('attempt exhaustion is unsuccessful and keeps the final observed gaps', async t => {
  const f = await fixture(t, { maxAttempts: 2 });
  let calls = 0;
  const result = await f.run({ async executeAgent() { return ++calls % 2 ? implemented : checked(false); } });
  assert.equal(calls, 4);
  assert.equal(result.verified, false);
  assert.match(result.exception, /after 2 attempts/);
  assert.deepEqual(result.criteria, checked(false).criteria);
  assert.equal((await f.state()).status, 'exhausted');
});

test('the default keeps working beyond three attempts until verification succeeds', async t => {
  const f = await fixture(t);
  let calls = 0;
  const result = await f.run({ async executeAgent() {
    calls++;
    return calls % 2 ? implemented : checked(calls === 8);
  } });
  assert.equal(calls, 8);
  assert.equal(result.verified, true);
  assert.equal(result.attempts, 4);
  assert.equal((await f.state()).maxAttempts, null);
});

for (const phase of ['implement', 'verify']) {
  test(`${phase} asks a human, waits, and retries that phase in a fresh session`, async t => {
    const f = await fixture(t), calls = [];
    let answer, asked;
    const attention = new Promise(resolve => { asked = resolve; });
    const pending = f.run({ async executeAgent(context) {
      calls.push(context);
      const current = context.job.input.prompt.startsWith('Implement') ? 'implement' : 'verify';
      if (current === phase && calls.filter(call => call.job.input.prompt.startsWith(current === 'implement' ? 'Implement' : 'Verify')).length === 1) return blocked;
      return current === 'implement' ? implemented : checked();
    }, askHuman(context) {
      assert.equal(context.signal, f.controller.signal);
      assert.match(context.job.input.prompt, /expected output format/);
      assert.match(context.job.input.prompt, /Retry starts a fresh/);
      assert.equal(context.job.input.summary, blocked.final);
      assert.equal(context.job.input.context['Original goal'], f.context.job.input.goal);
      asked();
      return new Promise(resolve => { answer = resolve; });
    } });
    await attention;
    assert.equal((await f.state()).status, 'needs_human');
    assert.equal(calls.length, phase === 'implement' ? 1 : 2);
    answer({ action: 'retry', guidance: 'Use the supplied specification.' });
    const result = await pending;
    assert.equal(result.verified, true);
    assert.equal(result.attempts, 1);
    assert.equal(calls.length, 3);
    assert.match(calls[phase === 'implement' ? 1 : 2].job.input.prompt, /Use the supplied specification/);
    const assignment = JSON.parse(calls[phase === 'implement' ? 1 : 2].job.input.prompt.split('Assignment data:\n')[1]);
    assert.match(assignment.humanGuidance[0].question, /expected output format/);
    assert.equal(assignment.humanGuidance[0].answer.action, 'retry');
    assert.equal((await f.state()).human[0].phase, phase);
    assert.equal(new Set(calls.map(call => call.job.id)).size, 3);
  });
}

test('a human stop never becomes a successful verification', async t => {
  const f = await fixture(t);
  let calls = 0;
  const result = await f.run({ async executeAgent() { calls++; return blocked; },
    async askHuman() { return { action: 'stop', guidance: 'The requested hardware is unavailable.' }; } });
  assert.equal(calls, 1);
  assert.equal(result.verified, false);
  assert.equal(result.exception, 'Stopped by human');
  assert.equal((await f.state()).status, 'stopped');
});

for (const waiting of ['agent', 'human']) {
  test(`cancellation interrupts an active ${waiting} and never starts another phase`, async t => {
    const f = await fixture(t);
    let ready, calls = 0;
    const started = new Promise(resolve => { ready = resolve; });
    const wait = ({ signal }) => new Promise((resolve, reject) => {
      signal.addEventListener('abort', () => reject(signal.reason), { once: true });
      ready();
    });
    const pending = f.run({ executeAgent(context) { calls++; return waiting === 'agent' ? wait(context) : blocked; }, askHuman: wait });
    const rejected = assert.rejects(pending, /Cancelled by test/);
    await started;
    f.controller.abort(new Error('Cancelled by test'));
    await rejected;
    assert.equal(calls, 1);
    assert.equal((await f.state()).status, 'cancelled');
  });
}

test('unsupported, empty or inconsistent verification cannot claim completion', async t => {
  for (const verdict of [
    { final: 'Done', exception: null },
    { ...checked(), verified: 'true' },
    { ...checked(), criteria: [] },
    { ...checked(), criteria: [{ ...checked().criteria[0], evidence: [] }] },
    { ...checked(false), verified: true },
  ]) {
    const f = await fixture(t);
    let count = 0;
    const result = await f.run({ async executeAgent() { return ++count === 1 ? implemented : verdict; } });
    assert.equal(result.verified, false);
    assert.ok(result.exception);
    assert.equal((await f.state()).status, 'failed');
  }
});

test('model defaults and overrides are resolved once, and invalid limits fail before inference', async t => {
  const f = await fixture(t);
  assert.equal(systemModel('simple', undefined, f.context.env), model.id);
  await writeFile(f.models, '{}');
  assert.throws(() => systemModel('simple', undefined, f.context.env), /asys system-model set simple MODEL/);
  await writeFile(f.models, 'broken');
  f.context.argv = ['--model', model.id, '--max-attempts', '1'];
  let calls = 0;
  assert.equal((await f.run({ async executeAgent() { return ++calls % 2 ? implemented : checked(); } })).verified, true);
  for (const maxAttempts of [0, -1, true, '2', 1.5]) {
    const invalid = await fixture(t, { maxAttempts });
    await assert.rejects(invalid.run({ executeAgent() { assert.fail('must not start'); } }), /positive integer/);
  }
});

test('real simple sessions use tools in the workspace and remain independent, with observable transcripts', async t => {
  const f = await fixture(t);
  const initial = [], requests = [];
  const provider = {
    async listModels() { return { models: [model] }; },
    async *infer(request) {
      assert.equal(request.model, model.id);
      const frame = JSON.parse(request.payload);
      requests.push(frame);
      const prompt = frame.context.messages[0].content[0].text;
      const verify = prompt.startsWith('Verify');
      const fresh = frame.context.messages.length === 1;
      if (fresh) initial.push(frame);
      assert.match(frame.context.systemPrompt, /simple system agent supplied by asys/);
      assert.doesNotMatch(frame.context.systemPrompt, /ENVIRONMENT_PRIVATE/);
      const message = fresh
        ? assistant([{ type: 'toolCall', id: `check-${requests.length}`, name: 'bash', arguments: {
          command: verify ? 'cat output.txt' : 'printf required > output.txt',
        } }], 'toolUse')
        : assistant([{ type: 'text', text: JSON.stringify(verify ? checked() : implemented) }]);
      yield { payload: JSON.stringify({ type: 'done', reason: message.stopReason, message }) };
    },
  };
  const result = await f.run({ provider });
  assert.equal(result.verified, true);
  assert.equal(initial.length, 2);
  assert.equal(await readFile(join(f.workspace, 'output.txt'), 'utf8'), 'required');
  assert.doesNotMatch(initial[1].context.messages[0].content[0].text, /IMPLEMENTER_CLAIMS_SUCCESS/);
  const state = await f.state();
  for (const session of state.sessions) {
    const transcript = JSON.parse(await readFile(join(f.directory, session.directory, 'agent.json')));
    assert.equal(transcript.agent.name, 'simple');
    assert.equal(transcript.agent.steps, 2);
  }
  const { stdout } = await promisify(execFile)('python3', ['-c', `
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from asys.transcript import JobOutput
title, lines = JobOutput().read({'directory': sys.argv[2]})
print(json.dumps({'title': title, 'text': '\\n'.join(lines)}))
`, fileURLToPath(new URL('../../python', import.meta.url)), f.directory]);
  const observed = JSON.parse(stdout);
  assert.equal(observed.title, 'Goal');
  assert.match(observed.text, /Attempt 1: implement/);
  assert.match(observed.text, /Attempt 1: verify/);
  assert.match(observed.text, /Tool call: bash/);
});
