import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { runTestAgent as runAgent } from './helpers.mjs';
import { groupModels, streamProvider } from '../src/provider-adapter.mjs';
import { createResourceExhaustedError } from '@cyclo/provider/errors';
import { assistant, model } from './helpers.mjs';

test('the worker publishes actual text and thinking before the message finishes', { timeout: 10000 }, async t => {
  const workspace = await mkdtemp(join(tmpdir(), 'asys-live-transcript-'));
  t.after(() => rm(workspace, { recursive: true, force: true }));
  const release = Promise.withResolvers(), visible = Promise.withResolvers();
  const controller = new AbortController(), events = [];
  let saved;
  const provider = {
    async listModels() { return { models: [{ ...model, capabilities: { ...model.capabilities, reasoning: true } }] }; },
    async *infer(request) {
      assert.equal(JSON.parse(request.payload).options.reasoning, 'medium');
      const partial = assistant([], 'pending');
      const send = event => ({ payload: JSON.stringify(event) });
      yield send({ type: 'start', partial });
      partial.content.push({ type: 'thinking', thinking: '' });
      yield send({ type: 'thinking_start', contentIndex: 0, partial });
      partial.content[0].thinking = 'I need to check the board connections.';
      yield send({ type: 'thinking_delta', contentIndex: 0, delta: partial.content[0].thinking, partial });
      partial.content.push({ type: 'text', text: '' });
      yield send({ type: 'text_start', contentIndex: 1, partial });
      partial.content[1].text = 'I am inspecting the board now.';
      yield send({ type: 'text_delta', contentIndex: 1, delta: partial.content[1].text, partial });
      await release.promise;
      partial.content[1].text = JSON.stringify({ final: 'I am inspecting the board now.', exception: null });
      partial.stopReason = 'stop';
      yield send({ type: 'done', reason: 'stop', message: partial });
    },
  };
  const job = { id: 'live' };
  const running = runAgent({ config: { model: 'fixture/model', prompt: 'Check the board.', maxSteps: 3, options: {} },
    job, workspace, signal: controller.signal, provider,
    save() { saved = structuredClone(job); },
    event(type, data) { events.push({ type, ...data }); if (type === 'agent.message_delta' && data.kind === 'text') visible.resolve(); },
  });
  let timer, result;
  try {
    await Promise.race([visible.promise, running.then(() => { throw new Error('No live transcript before completion'); }),
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('Missing live agent text')), 2000); })]);
    assert.ok(events.some(e => e.type === 'agent.message_delta' && e.kind === 'thinking' && e.delta === 'I need to check the board connections.'));
    assert.ok(events.some(e => e.type === 'agent.message_delta' && e.kind === 'text' && e.delta === 'I am inspecting the board now.'));
    assert.ok(!saved.agent.session.entries.some(e => e.message?.role === 'assistant'), 'message is still streaming');
  } finally { clearTimeout(timer); release.resolve(); result = await running; }
  assert.equal(result.final, 'I am inspecting the board now.');
});

test('Pi discovers and reads skills supplied by the selected environment', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'asys-environment-skills-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const environmentDirectory = join(directory, 'env');
  const skill = join(environmentDirectory, 'skills', 'fixture', 'SKILL.md');
  await mkdir(join(environmentDirectory, 'skills', 'fixture'), { recursive: true });
  await writeFile(skill, '---\nname: fixture\ndescription: Environment-specific synthesis instructions.\n---\nUse the ENVIRONMENT_SKILL_MARKER command.\n');
  const workspace = join(directory, 'workspace');
  await mkdir(join(workspace, '.pi'), { recursive: true });
  await writeFile(join(workspace, 'AGENTS.md'), 'PROJECT_INSTRUCTIONS_MARKER');
  await writeFile(join(workspace, '.pi/SYSTEM.md'), 'PROJECT_SYSTEM_MARKER');
  await writeFile(join(workspace, '.pi/APPEND_SYSTEM.md'), 'PROJECT_APPEND_SYSTEM_MARKER');
  const requests = [];
  const provider = {
    async listModels() { return { models: [model] }; },
    async *infer(request) {
      const frame = JSON.parse(request.payload);
      requests.push(frame);
      const message = requests.length === 1
        ? assistant([{ type: 'toolCall', id: 'read-skill', name: 'read', arguments: { path: skill } }], 'toolUse')
        : assistant([{ type: 'text', text: JSON.stringify({ final: 'Read the environment instructions.', exception: null }) }]);
      yield { payload: JSON.stringify({ type: 'done', reason: message.stopReason, message }) };
    },
  };
  await runAgent({ config: { model: 'fixture/model', prompt: 'Read the synthesis skill.', maxSteps: 3, options: {} },
    job: { id: 'skills' }, workspace, environmentDirectory,
    signal: new AbortController().signal, provider, save() {}, event() {} });
  assert.match(requests[0].context.systemPrompt, /Environment-specific synthesis instructions/);
  for (const { context } of requests) {
    assert.match(context.systemPrompt, /^You are a Renaissance man:/);
    assert.doesNotMatch(context.systemPrompt, /operating inside pi|Pi documentation|approved|success\s*[:=]/);
    assert.doesNotMatch(context.systemPrompt, /PROJECT_(?:INSTRUCTIONS|SYSTEM|APPEND_SYSTEM)_MARKER/);
    assert.doesNotMatch(context.systemPrompt, /ASYS_RESULT/);
    assert.match(context.systemPrompt, /"exception"/);
    for (const name of ['read', 'bash', 'edit', 'write']) {
      assert.ok(context.tools.some(tool => tool.name === name && tool.parameters));
      assert.match(context.systemPrompt, new RegExp(`^- ${name}: .+`, 'm'));
    }
  }
  assert.equal(await readFile(join(workspace, 'test-job/.pi/system-prompt.txt'), 'utf8'), requests.at(-1).context.systemPrompt);
  assert.ok(requests[1].context.messages.some(message => message.role === 'toolResult' && JSON.stringify(message.content).includes('ENVIRONMENT_SKILL_MARKER')));
});

test('Pi loads and executes tools from the selected environment extensions directory', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'asys-environment-extensions-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const environmentDirectory = join(directory, 'env');
  await mkdir(join(environmentDirectory, 'extensions'), { recursive: true });
  await writeFile(join(environmentDirectory, 'extensions', 'lookup.ts'), `
    export default function (pi) {
      pi.registerTool({ name: 'environment_lookup', label: 'Lookup', description: 'Look up environment reference data.',
        promptGuidelines: ['Verify the returned reference version.'],
        parameters: { type: 'object', properties: { query: { type: 'string' } }, required: ['query'] },
        async execute(_id, { query }) { return { content: [{ type: 'text', text: 'REFERENCE: ' + query }], details: {} }; }
      });
      pi.on('before_agent_start', event => {
        pi.setActiveTools(pi.getActiveTools().filter(name => name !== 'edit'));
        return { systemPrompt: event.systemPrompt + '\\nENVIRONMENT_PROMPT_MARKER' };
      });
    }
  `);
  const requests = [];
  const provider = {
    async listModels() { return { models: [model] }; },
    async *infer(request) {
      const frame = JSON.parse(request.payload);
      requests.push(frame);
      assert.ok(frame.context.tools.some(tool => tool.name === 'environment_lookup'));
      assert.match(frame.context.systemPrompt, /^- environment_lookup: Look up environment reference data\./m);
      assert.match(frame.context.systemPrompt, /Verify the returned reference version/);
      assert.match(frame.context.systemPrompt, /ENVIRONMENT_PROMPT_MARKER/);
      assert.doesNotMatch(frame.context.systemPrompt, /^- edit:|edits\[\]/m);
      assert.ok(!frame.context.tools.some(tool => tool.name === 'edit'));
      const message = requests.length === 1
        ? assistant([{ type: 'toolCall', id: 'lookup', name: 'environment_lookup', arguments: { query: 'timer' } }], 'toolUse')
        : assistant([{ type: 'text', text: JSON.stringify({ final: 'Looked up the reference.', exception: null }) }]);
      yield { payload: JSON.stringify({ type: 'done', reason: message.stopReason, message }) };
    },
  };
  await runAgent({ config: { model: 'fixture/model', prompt: 'Look up the timer.', maxSteps: 3, options: {} },
    job: { id: 'extensions' }, workspace: join(directory, 'workspace'), environmentDirectory,
    signal: new AbortController().signal, provider, save() {}, event() {} });
  assert.equal(requests.length, 2);
  assert.ok(requests[1].context.messages.some(message => message.role === 'toolResult' && JSON.stringify(message.content).includes('REFERENCE: timer')));
});

test('a broken environment extension fails the job instead of silently omitting its tools', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'asys-broken-extension-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const environmentDirectory = join(directory, 'env');
  await mkdir(join(environmentDirectory, 'extensions'), { recursive: true });
  await writeFile(join(environmentDirectory, 'extensions', 'broken.ts'), 'export default () => { throw new Error("Reference tool unavailable"); };');
  const provider = {
    async listModels() { return { models: [model] }; },
    async *infer() { assert.fail('Inference must not start with missing environment tools'); },
  };
  await assert.rejects(runAgent({ config: { model: 'fixture/model', prompt: 'Look up a reference.', maxSteps: 1, options: {} },
    job: { id: 'broken' }, workspace: join(directory, 'workspace'), environmentDirectory,
    signal: new AbortController().signal, provider, save() {}, event() {} }), /Reference tool unavailable/);
});

test('Pi SDK runs its real shell tool and sends native frames through Provider', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'asys-bpmn-pi-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const requests = [], events = [];
  const provider = {
    async listModels() { return { models: [model] }; },
    async *infer(request) {
      assert.equal(request.model, 'fixture/model');
      const frame = JSON.parse(request.payload);
      requests.push(frame);
      assert.ok(frame.context.tools.some(tool => tool.name === 'bash'));
      const message = requests.length === 1
        ? assistant([{ type: 'toolCall', id: 'write-file', name: 'bash', arguments: { command: 'printf "from Pi\\n" > proof.txt' } }], 'toolUse')
        : assistant([{ type: 'text', text: JSON.stringify({ final: 'Created proof.txt', exception: null }) }]);
      yield { payload: JSON.stringify({ type: 'done', reason: message.stopReason, message }) };
    },
  };
  const job = { id: 'agent-execution' };
  let saves = 0;
  const result = await runAgent({
    config: { model: 'fixture/model', prompt: 'Create proof.txt', maxSteps: 4, options: {} },
    job,
    signal: new AbortController().signal, provider, workspace: directory,
    save: () => saves++, event: (type, data) => events.push({ type, data }),
  });
  assert.equal(await readFile(join(directory, 'proof.txt'), 'utf8'), 'from Pi\n');
  assert.equal(requests.length, 2);
  assert.equal(result.final, 'Created proof.txt');
  assert.ok(requests[1].context.messages.some(message => message.role === 'toolResult' && message.toolCallId === 'write-file'));
  assert.ok(events.some(event => event.type === 'agent.tool_completed'));
  assert.ok(job.agent.session.entries.some(entry => entry.type === 'message' && entry.message.role === 'toolResult'));
  assert.ok(saves > 1);
});

test('an incomplete Provider stream fails promptly instead of leaving Pi waiting for a terminal frame', async () => {
  const route = groupModels([model]).get('fixture')[0];
  const provider = { async *infer() {} };
  const stream = streamProvider(provider, model.id, route.model, { messages: [] });
  const result = await stream.result();
  assert.equal(result.stopReason, 'error');
  assert.match(result.errorMessage, /terminal Pi event/);
});

test('the Provider RPC iterator is released when its terminal Pi event arrives', async () => {
  const route = groupModels([model]).get('fixture')[0];
  let closed = false;
  const message = assistant([{ type: 'text', text: JSON.stringify({ final: 'Finished', exception: null }) }]);
  const provider = { async *infer() {
    try {
      yield { payload: JSON.stringify({ type: 'done', reason: 'stop', message }) };
      throw new Error('Consumer continued after the terminal frame');
    } finally { closed = true; }
  } };
  const stream = streamProvider(provider, model.id, route.model, { messages: [] });
  assert.equal((await stream.result()).stopReason, 'stop');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(closed, true);
});

test('an exhausted Provider is replayed after its reset time, however far beyond the attempt timeout that is', async () => {
  const route = groupModels([model]).get('fixture')[0];
  const start = 1_700_000_000_000, resetAfterMs = 3 * 60 * 60 * 1000;
  let clock = start, calls = 0;
  const sleeps = [], waits = [];
  const provider = { async *infer(_request, { signal }) {
    assert.equal(signal.aborted, false, 'a fresh attempt must not inherit an expired timeout');
    if (++calls === 1) throw createResourceExhaustedError(new Date(start + resetAfterMs));
    yield { payload: JSON.stringify({ type: 'done', reason: 'stop', message: assistant([{ type: 'text', text: JSON.stringify({ final: 'Back', exception: null }) }]) }) };
  } };
  const stream = streamProvider(provider, model.id, route.model, { messages: [] }, { attemptTimeoutMs: 50 }, {
    now: () => clock,
    async sleep(delayMs, signal) { sleeps.push(delayMs); signal?.throwIfAborted(); clock += delayMs; },
    onExhaustion: wait => waits.push(wait),
  });
  const result = await stream.result();
  assert.equal(result.stopReason, 'stop');
  assert.equal(calls, 2);
  assert.deepEqual(sleeps, [resetAfterMs]);
  assert.equal(waits.length, 1);
  assert.equal(waits[0].retryAt.getTime(), start + resetAfterMs);
  assert.equal(waits[0].delayMs, resetAfterMs);
});

test('an exhaustion wait ends only on operator cancellation and reports as aborted', async () => {
  const route = groupModels([model]).get('fixture')[0];
  const controller = new AbortController();
  let calls = 0;
  const provider = { async *infer() {
    calls++;
    throw createResourceExhaustedError(new Date(Date.now() + 24 * 60 * 60 * 1000));
  } };
  const stream = streamProvider(provider, model.id, route.model, { messages: [] },
    { attemptTimeoutMs: 20, signal: controller.signal }, {
      async sleep(_delayMs, signal) { controller.abort(); signal.throwIfAborted(); },
    });
  const result = await stream.result();
  assert.equal(result.stopReason, 'aborted');
  assert.equal(calls, 1, 'no replay after cancellation');
});


// A fake in-flight request: a real one holds a socket open, so the event loop
// stays alive while the attempt waits for its signal.
async function stallUntilAborted(signal) {
  signal.throwIfAborted();
  const keepAlive = setInterval(() => {}, 1000);
  try {
    await new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(signal.reason), { once: true }));
  } finally { clearInterval(keepAlive); }
}

test('an inference attempt that stalls past the attempt timeout is a provider failure, not a cancellation', async () => {
  const route = groupModels([model]).get('fixture')[0];
  let calls = 0;
  const provider = { async *infer(_request, { signal }) {
    calls++;
    await stallUntilAborted(signal);
  } };
  const stream = streamProvider(provider, model.id, route.model, { messages: [] }, { attemptTimeoutMs: 20 });
  const result = await stream.result();
  assert.equal(result.stopReason, 'error');
  assert.match(result.errorMessage, /no complete response within 20 ms/);
  assert.equal(calls, 1, 'a stalled attempt is not replayed');
});

test('operator cancellation during an attempt still reports as aborted', async () => {
  const route = groupModels([model]).get('fixture')[0];
  const controller = new AbortController();
  const provider = { async *infer(_request, { signal }) {
    controller.abort();
    await stallUntilAborted(signal);
  } };
  const stream = streamProvider(provider, model.id, route.model, { messages: [] },
    { attemptTimeoutMs: 60_000, signal: controller.signal });
  const result = await stream.result();
  assert.equal(result.stopReason, 'aborted');
});

test('the attempt timeout never enters the Provider payload', async () => {
  const route = groupModels([model]).get('fixture')[0];
  let frame;
  const provider = { async *infer(request) {
    frame = JSON.parse(request.payload);
    yield { payload: JSON.stringify({ type: 'done', reason: 'stop', message: assistant([{ type: 'text', text: JSON.stringify({ final: 'ok', exception: null }) }]) }) };
  } };
  await streamProvider(provider, model.id, route.model, { messages: [] }, { attemptTimeoutMs: 1000, temperature: 0.2 }).result();
  assert.equal(frame.options.attemptTimeoutMs, undefined);
  assert.equal(frame.options.temperature, 0.2);
});
