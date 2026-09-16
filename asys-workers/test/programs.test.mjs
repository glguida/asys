import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { connectNodeAdapter } from '@connectrpc/connect-node';
import { Provider } from '@cyclo/provider/contract';
import { PreparedQueue as Queue } from '../../asys-runtime/test/fixtures.mjs';
import { HumanService } from '../src/human-service.mjs';
import { humanServer } from '../src/human-server.mjs';
import { humanClient } from '../src/client.mjs';
import { assistant, model } from './helpers.mjs';

const executor = fileURLToPath(new URL('../../asys-runtime/tools/asys-runtime', import.meta.url));
const agentProgram = fileURLToPath(new URL('../tools/asys-agent', import.meta.url));
const humanProgram = fileURLToPath(new URL('../tools/asys-human', import.meta.url));
const commandProgram = fileURLToPath(new URL('../tools/asys-program', import.meta.url));

async function until(fn) {
  const deadline = Date.now() + 10000;
  for (;;) {
    const result = await fn();
    if (result) return result;
    if (Date.now() > deadline) throw new Error('Timed out waiting for worker');
    await new Promise(resolve => setTimeout(resolve, 20));
  }
}

async function fixture(t, { agentArgs = [] } = {}) {
  const root = await mkdtemp(join(tmpdir(), 'asys-programs-'));
  const queue = new Queue(join(root, 'queue'));
  const environment = join(root, 'environment');
  await mkdir(join(environment, 'agents', 'test'), { recursive: true });
  const config = join(root, 'runtime.json');
  await writeFile(config, JSON.stringify({ version: 1, types: {
    agent: { command: [process.execPath, agentProgram, '--agent', 'test', ...agentArgs] },
    human: { command: [process.execPath, humanProgram] },
    program: { command: ['python3', commandProgram] },
  } }));
  let child, exited;
  const f = { root, queue, environment,
    start(env = {}) {
      child = spawn('python3', [executor, 'run', config, '--root', queue.root], { env: { ...process.env, ASYS_ENVIRONMENT_DIR: environment, ...env }, stdio: ['ignore', 'pipe', 'pipe'] });
      let errors = '';
      child.stderr.on('data', data => { errors += data; });
      exited = new Promise((resolve, reject) => {
        child.once('error', reject);
        child.once('exit', (code, signal) => resolve({ code, signal, errors }));
      });
    },
    async stop() {
      if (!child) return;
      child.kill('SIGTERM');
      const outcome = await exited;
      child = undefined;
      assert.equal(outcome.code, 0, outcome.errors);
    },
  };
  t.after(async () => { await f.stop(); await rm(root, { recursive: true, force: true }); });
  return f;
}

async function listen(t, server, path) {
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(path, resolve); });
  t.after(async () => {
    server.closeConnections?.(); server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  });
  return `unix://${path}`;
}

test('the worker configuration selects the Pi model and inference uses the unchanged Provider protocol', async t => {
  const f = await fixture(t, { agentArgs: ['--model', 'fixture/model'] });
  const requests = [];
  const server = createServer(connectNodeAdapter({ routes(router) { router.service(Provider, {
    listModels() { return { models: [model] }; },
    async *infer(request) {
      const frame = JSON.parse(request.payload);
      requests.push(frame);
      assert.equal(request.model, 'fixture/model');
      const message = requests.length === 1
        ? assistant([{ type: 'toolCall', id: 'proof', name: 'bash', arguments: { command: 'printf "executed by SDK" > proof.txt' } }], 'toolUse')
        : assistant([{ type: 'text', text: JSON.stringify({ final: 'Finished', exception: null }) }]);
      Object.assign(message, { provider: 'openai-codex', model: 'backend-model', api: 'openai-codex-responses' });
      yield { payload: JSON.stringify({ type: 'done', reason: message.stopReason, message }) };
    },
  }); } }));
  f.start({ DCOMP_IN_INFERENCE: await listen(t, server, join(f.root, 'provider.sock')) });
  await f.queue.submit('agent', 'research', { input: { prompt: 'Create the proof file' } });
  const state = await f.queue.wait('research', { timeoutMs: 10000 });
  assert.equal(state.status, 'done', state.error);
  assert.equal(state.result.final, 'Finished');
  assert.equal(Object.hasOwn(state.result, 'success'), false);
  assert.equal(await readFile(join(f.queue.workspace('research'), 'proof.txt'), 'utf8'), 'executed by SDK');
  assert.equal(requests.length, 2);
  assert.ok(requests[1].context.messages.some(m => m.role === 'toolResult' && m.toolCallId === 'proof'));
  assert.equal(requests[1].context.messages.find(m => m.role === 'assistant').provider, 'openai-codex');
});

test('the environment supplies agent memory; job data cannot replace the global system prompt', async t => {
  const f = await fixture(t, { agentArgs: ['--model', 'fixture/model'] });
  await writeFile(join(f.environment, 'agents/test/memory.md'), 'RETAINED_LESSON: Inspect the evidence.');
  const requests = [];
  const server = createServer(connectNodeAdapter({ routes(router) { router.service(Provider, {
    listModels() { return { models: [model] }; },
    async *infer(request) {
      requests.push(JSON.parse(request.payload));
      yield { payload: JSON.stringify({ type: 'done', reason: 'stop',
        message: assistant([{ type: 'text', text: '{"final":"Review finished","exception":null}' }]) }) };
    },
  }); } }));
  f.start({ DCOMP_IN_INFERENCE: await listen(t, server, join(f.root, 'provider.sock')) });
  const prompt = 'Inspect the deliverables.';
  await f.queue.submit('agent', 'review', { input: { prompt, system: 'JOB_DATA_MUST_NOT_CONFIGURE_SYSTEM_PROMPT', model: 'wrong/model' }, metadata: { actions: { version: 1, entries: [{ id: 'unexpected' }] } } });
  const state = await f.queue.wait('review', { timeoutMs: 10000 });
  assert.equal(state.status, 'done', state.error);
  const { context } = requests[0];
  assert.doesNotMatch(context.systemPrompt, /JOB_DATA_MUST_NOT_CONFIGURE_SYSTEM_PROMPT|ASYS_RESULT/);
  assert.match(context.systemPrompt, /RETAINED_LESSON/);
  assert.ok(!context.tools.some(tool => ['list_actions', 'start_action', 'wait_action'].includes(tool.name)));
  const common = (await readFile(new URL('../src/system.md', import.meta.url), 'utf8')).trim();
  assert.ok(context.systemPrompt.startsWith(common + '\n\n'));
  assert.deepEqual(context.messages[0].content, [{ type: 'text', text: prompt }]);
  assert.equal(await readFile(join(f.queue.executionDirectory('review'), '.pi/system-prompt.txt'), 'utf8'), context.systemPrompt);
});

for (const limit of [undefined, 2]) {
  test(limit === undefined ? 'agents can finish after more than 32 steps without a configured limit'
    : 'an environment can explicitly limit agent inference steps', async t => {
    const f = await fixture(t, { agentArgs: ['--model', 'fixture/model',
      ...(limit === undefined ? [] : ['--max-steps', String(limit)])] });
    let requests = 0;
    const server = createServer(connectNodeAdapter({ routes(router) { router.service(Provider, {
      listModels() { return { models: [model] }; },
      async *infer() {
        requests++;
        const message = requests <= 33
          ? assistant([{ type: 'toolCall', id: `step-${requests}`, name: 'bash', arguments: { command: 'true' } }], 'toolUse')
          : assistant([{ type: 'text', text: '{"final":"Finished all steps","exception":null}' }]);
        yield { payload: JSON.stringify({ type: 'done', reason: message.stopReason, message }) };
      },
    }); } }));
    f.start({ DCOMP_IN_INFERENCE: await listen(t, server, join(f.root, 'provider.sock')) });
    await f.queue.submit('agent', 'long-job', { input: { prompt: 'Complete the assignment.' } });
    const state = await f.queue.wait('long-job', { timeoutMs: 20000 });
    if (limit === undefined) {
      assert.equal(state.status, 'done', state.error);
      assert.deepEqual(state.result, { final: 'Finished all steps', exception: null });
      assert.equal(requests, 34);
    } else {
      assert.equal(state.status, 'failed');
      assert.equal(requests, limit);
      assert.match(await readFile(join(f.queue.executionDirectory('long-job'), 'stderr.log'), 'utf8'), /exceeded 2 inference steps/);
    }
  });
}

for (const fields of [
  { approved: false, reason: 'Needs revision' },
  { success: false },
  { status: 'completed', artifacts: ['output/architecture.json'], exception: null },
  { exception: 'Required schematic is missing', report: 'Only architecture.json was supplied' },
]) {
  test(`final JSON preserves job-defined results: ${JSON.stringify(fields)}`, async t => {
    const f = await fixture(t, { agentArgs: ['--model', 'fixture/model'] });
    const result = { final: 'Review report.', exception: null, ...fields };
    const server = createServer(connectNodeAdapter({ routes(router) { router.service(Provider, {
      listModels() { return { models: [model] }; },
      async *infer(request) {
        assert.doesNotMatch(JSON.parse(request.payload).context.systemPrompt, /approved|success\s*[:=]/);
        yield { payload: JSON.stringify({ type: 'done', reason: 'stop',
          message: assistant([{ type: 'text', text: JSON.stringify(result) }]) }) };
      },
    }); } }));
    f.start({ DCOMP_IN_INFERENCE: await listen(t, server, join(f.root, 'provider.sock')) });
    await f.queue.submit('agent', 'review', { input: { prompt: 'Review the supplied design.' } });
    const state = await f.queue.wait('review', { timeoutMs: 10000 });
    assert.equal(state.status, result.exception ? 'failed' : 'done', state.error);
    assert.equal(state.exit_code, result.exception ? 1 : 0);
    assert.deepEqual(state.result, result);
    if (result.exception) assert.equal(state.error, result.exception);
  });
}

test('retrying a failed agent starts the stage again with its original prompt and a fresh budget', async t => {
  const f = await fixture(t, { agentArgs: ['--model', 'fixture/model'] });
  const directory = f.queue.executionDirectory('layout');
  const firstResult = join(directory, 'result.json');
  const nextDirectory = f.queue.executionDirectory('layout-again');
  const nextResult = join(nextDirectory, 'result.json');
  const board = join(f.queue.workspace('layout'), 'partial-board.txt');
  const requests = [];
  const server = createServer(connectNodeAdapter({ routes(router) { router.service(Provider, {
    listModels() { return { models: [model] }; },
    async *infer(request) {
      const frame = JSON.parse(request.payload);
      requests.push(frame);
      const turn = requests.length;
      let message;
      if (turn === 1) message = assistant([
        { type: 'toolCall', id: 'board', name: 'write', arguments: { path: board, content: 'Partially routed board' } },
        { type: 'toolCall', id: 'failure', name: 'write', arguments: { path: firstResult, content: '{"exception":"Routing incomplete","old_field":"do not carry forward"}' } },
      ], 'toolUse');
      else if (turn === 2) message = assistant([{ type: 'text', text: '{"final":"Unable to finish routing","exception":"Routing incomplete","old_field":"do not carry forward"}' }]);
      else if (turn === 3) {
        assert.equal(frame.context.messages.length, 1);
        assert.equal(frame.context.messages[0].role, 'user');
        assert.deepEqual(frame.context.messages[0].content, [{ type: 'text', text: 'Route the board.' }]);
        assert.doesNotMatch(frame.context.systemPrompt, /ASYS_RESULT/);
        await assert.rejects(readFile(nextResult), { code: 'ENOENT' });
        message = assistant([{ type: 'toolCall', id: 'inspect', name: 'read', arguments: { path: board } }], 'toolUse');
      } else if (turn === 4) {
        assert.match(JSON.stringify(frame.context.messages.at(-1)), /Partially routed board/);
        message = assistant([{ type: 'toolCall', id: 'repaired', name: 'write', arguments: { path: nextResult, content: '{"exception":null,"report":"Routed"}' } }], 'toolUse');
      } else message = assistant([{ type: 'text', text: '{"final":"Routing finished","exception":null,"report":"Routed"}' }]);
      yield { payload: JSON.stringify({ type: 'done', reason: message.stopReason, message }) };
    },
  }); } }));
  f.start({ DCOMP_IN_INFERENCE: await listen(t, server, join(f.root, 'provider.sock')) });
  await f.queue.submit('agent', 'layout', { input: { prompt: 'Route the board.', maxSteps: 3 } });
  const failed = await f.queue.wait('layout', { timeoutMs: 10000 });
  assert.equal(failed.status, 'failed');
  const original = await readFile(firstResult, 'utf8');
  const transcript = join(directory, 'agent.json');
  const failedAgent = JSON.parse(await readFile(transcript, 'utf8'));
  await f.queue.resubmit('layout', 'layout-again');
  const retried = await f.queue.wait('layout-again', { timeoutMs: 10000 });
  assert.equal(retried.status, 'done', retried.error);
  assert.equal((await f.queue.state('layout')).status, 'failed');
  assert.equal(retried.result.report, 'Routed');
  assert.equal(retried.result.exception, null);
  assert.equal(retried.result.old_field, undefined);
  assert.equal(requests.length, 5);
  const restartedAgent = JSON.parse(await readFile(join(nextDirectory, 'agent.json'), 'utf8'));
  assert.equal(restartedAgent.agent.steps, 3);
  assert.notEqual(restartedAgent.agent.session.header.id, failedAgent.agent.session.header.id);
  assert.deepEqual(JSON.parse(await readFile(join(directory, 'agent.json'), 'utf8')), failedAgent);
  assert.equal(await readFile(firstResult, 'utf8'), original);
});

test('a failed stage restarts after a partial tool call', async t => {
  const f = await fixture(t, { agentArgs: ['--model', 'fixture/model'] });
  const directory = f.queue.executionDirectory('interrupted-write');
  const target = join(f.queue.workspace('interrupted-write'), 'unfinished.txt');
  const requests = [];
  const server = createServer(connectNodeAdapter({ routes(router) { router.service(Provider, {
    listModels() { return { models: [model] }; },
    async *infer(request) {
      const frame = JSON.parse(request.payload);
      requests.push(frame);
      if (requests.length === 1) {
        const message = { ...assistant([{ type: 'toolCall', id: 'unfinished-write', name: 'write',
          arguments: { path: target, content: 'Incomplete generation' } }], 'error'), errorMessage: 'terminated' };
        yield { payload: JSON.stringify({ type: 'error', reason: 'error', error: message }) };
      } else {
        assert.equal(frame.context.messages.length, 1);
        assert.deepEqual(frame.context.messages[0].content, [{ type: 'text', text: 'Perform the original stage.' }]);
        yield { payload: JSON.stringify({ type: 'done', reason: 'stop', message: assistant([{ type: 'text', text: JSON.stringify({ final: 'Stage rerun.', exception: null }) }]) }) };
      }
    },
  }); } }));
  f.start({ DCOMP_IN_INFERENCE: await listen(t, server, join(f.root, 'provider.sock')) });
  // One inference step ends the first job after the broken response.
  await f.queue.submit('agent', 'interrupted-write', { input: { prompt: 'Perform the original stage.', maxSteps: 1 } });
  assert.equal((await f.queue.wait('interrupted-write', { timeoutMs: 10000 })).status, 'failed');
  const checkpoint = join(directory, 'agent.json');
  const saved = JSON.parse(await readFile(checkpoint, 'utf8'));
  assert.ok(saved.agent.session.entries.some(e => e.message?.content?.some(c => c.id === 'unfinished-write')));
  await f.queue.resubmit('interrupted-write', 'fresh-write');
  const result = await f.queue.wait('fresh-write', { timeoutMs: 10000 });
  assert.equal(result.status, 'done', result.error);
  assert.equal((await f.queue.state('interrupted-write')).status, 'failed');
  assert.equal(requests.length, 2);
  await assert.rejects(readFile(target), { code: 'ENOENT' });
});

test('an interrupted Pi job restarts the assignment without carrying its result forward', async t => {
  const f = await fixture(t, { agentArgs: ['--model', 'fixture/model'] });
  const resultPath = join(f.queue.executionDirectory('review'), 'result.json');
  const waiting = Promise.withResolvers();
  let requests = 0;
  const server = createServer(connectNodeAdapter({ routes(router) { router.service(Provider, {
    listModels() { return { models: [model] }; },
    async *infer(request, context) {
      if (++requests === 3) {
        const frame = JSON.parse(request.payload);
        assert.deepEqual(frame.context.messages.map(m => m.content), [[{ type: 'text', text: 'Review the design.' }]]);
      }
      if (requests === 2) {
        waiting.resolve();
        await new Promise(resolve => context.signal.addEventListener('abort', resolve, { once: true }));
        return;
      }
      const message = requests === 1
        ? assistant([{ type: 'toolCall', id: 'decision', name: 'write', arguments: { path: resultPath, content: '{"approved":false}' } }], 'toolUse')
        : assistant([{ type: 'text', text: JSON.stringify({ final: 'The review requests revision.', exception: null }) }]);
      yield { payload: JSON.stringify({ type: 'done', reason: message.stopReason, message }) };
    },
  }); } }));
  const env = { DCOMP_IN_INFERENCE: await listen(t, server, join(f.root, 'provider.sock')) };
  f.start(env);
  await f.queue.submit('agent', 'review', { input: { prompt: 'Review the design.' } });
  await waiting.promise;
  await f.stop();
  assert.equal((await f.queue.state('review')).status, 'interrupted');
  f.start(env);
  await f.queue.resubmit('review', 'new-review');
  const state = await f.queue.wait('new-review', { timeoutMs: 10000 });
  assert.equal(state.status, 'done', state.error);
  assert.equal(state.attempt, undefined);
  assert.equal(state.result.approved, undefined, 'the interrupted job did not produce the current result');
  assert.equal(Object.hasOwn(state.result, 'success'), false);
  assert.equal(requests, 3);
  assert.equal(await readFile(resultPath, 'utf8'), '{"approved":false}');
});

for (const content of ['{invalid', 'null', '{"exception":""}', '{"exception":false}']) {
  test(`an invalid agent result fails the job: ${content}`, async t => {
    const f = await fixture(t, { agentArgs: ['--model', 'fixture/model'] });
    let requests = 0;
    const server = createServer(connectNodeAdapter({ routes(router) { router.service(Provider, {
      listModels() { return { models: [model] }; },
      async *infer() {
        requests++;
        const message = assistant([{ type: 'text', text: content }]);
        yield { payload: JSON.stringify({ type: 'done', reason: message.stopReason, message }) };
      },
    }); } }));
    f.start({ DCOMP_IN_INFERENCE: await listen(t, server, join(f.root, 'provider.sock')) });
    await f.queue.submit('agent', 'invalid', { input: { prompt: 'Return the result.' } });
    const state = await f.queue.wait('invalid', { timeoutMs: 10000 });
    assert.equal(state.status, 'failed');
    assert.equal(state.exit_code, 1);
    assert.equal(state.artifacts, undefined);
  });
}

test('human claims and decisions survive service restarts through the Human API', { timeout: 15000 }, async t => {
  const f = await fixture(t);
  f.start();
  let service = new HumanService(f.queue.root);
  t.after(() => service.close());
  let server = humanServer(service);
  const target = await listen(t, server, join(f.root, 'human.sock'));
  const client = humanClient('human', { DCOMP_IN_HUMAN: target });
  const stopWatching = new AbortController();
  t.after(() => stopWatching.abort());
  const attention = client.watchAttention({}, { signal: stopWatching.signal })[Symbol.asyncIterator]();
  const announced = attention.next();
  const input = { title: 'Review', prompt: 'Approve this change?', candidates: ['alice'], form: {
    type: 'object', properties: { approved: { type: 'boolean' } }, required: ['approved'], additionalProperties: false,
  } };
  await f.queue.submit('human', 'approval', { input, metadata: { subject: 'change' } });
  assert.equal((await announced).value.taskId, 'approval');
  stopWatching.abort();
  await assert.rejects(attention.next(), /cancel|abort/iu);
  assert.throws(() => new HumanService(f.queue.root), /Cannot own human-task queue/);
  await assert.rejects(client.claimTask({ id: 'approval', claimant: 'bob', claimId: 'c0' }), /candidate/);
  const first = await client.claimTask({ id: 'approval', claimant: 'alice', claimId: 'c1' });
  assert.equal((await client.claimTask({ id: 'approval', claimant: 'alice', claimId: 'c1' })).token, first.token);
  await client.releaseTask({ id: 'approval', token: first.token });
  const claimed = await client.claimTask({ id: 'approval', claimant: 'alice', claimId: 'c2' });
  assert.notEqual(claimed.token, first.token);
  await assert.rejects(client.completeTask({ id: 'approval', token: claimed.token, completionId: 'd1', resultJson: '{}' }), /form/);
  server.closeConnections();
  await new Promise(resolve => server.close(resolve));
  service.close();
  service = new HumanService(f.queue.root);
  server = humanServer(service);
  await listen(t, server, join(f.root, 'human.sock'));
  assert.equal((await f.queue.state('approval')).status, 'running');
  const stopReconnected = new AbortController();
  t.after(() => stopReconnected.abort());
  const reconnected = client.watchAttention({}, { signal: stopReconnected.signal })[Symbol.asyncIterator]();
  assert.equal((await reconnected.next()).value.taskId, 'approval');
  stopReconnected.abort();
  await assert.rejects(reconnected.next(), /cancel|abort/iu);
  const completion = { id: 'approval', token: claimed.token, completionId: 'd1', resultJson: '{"approved":true}' };
  assert.equal((await client.completeTask(completion)).task.status, 'completed');
  const state = await f.queue.wait('approval', { timeoutMs: 10000 });
  assert.equal(state.status, 'done', state.error);
  assert.equal(state.attempt, undefined);
  assert.deepEqual(state.result, { approved: true });
  assert.equal((await client.completeTask(completion)).task.status, 'completed');
  await assert.rejects(client.completeTask({ ...completion, resultJson: '{"approved":false}' }), /different completion/);
});

test('Human attention pushes to subscribers, repeats released work, and snapshots outstanding work on reconnect', { timeout: 15000 }, async t => {
  const f = await fixture(t);
  f.start();
  const service = new HumanService(f.queue.root);
  t.after(() => service.close());
  const server = humanServer(service);
  const target = await listen(t, server, join(f.root, 'attention.sock'));
  const client = humanClient('human', { DCOMP_IN_HUMAN: target });
  const controllers = [];
  t.after(() => controllers.forEach(controller => controller.abort()));
  function subscribe() {
    const controller = new AbortController();
    controllers.push(controller);
    const stream = client.watchAttention({}, {
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]),
    })[Symbol.asyncIterator]();
    return { stream, controller };
  }
  const first = subscribe(), second = subscribe();
  let next = first.stream.next();
  const other = second.stream.next();
  // Ensure both RPCs are already waiting before a new job publishes its request.
  await until(() => service.subscribers.size === 2);
  await f.queue.submit('human', 'first', { input: { prompt: 'First question?' } });
  assert.equal((await next).value.taskId, 'first');
  assert.equal((await other).value.taskId, 'first');
  assert.equal(JSON.parse((await client.getTask({ id: 'first' })).task.inputJson).prompt, 'First question?');

  const disconnected = second.stream.next();
  second.controller.abort();
  await assert.rejects(disconnected, /cancel|abort/iu);
  await until(() => service.subscribers.size === 1);

  next = first.stream.next();
  const claimed = await client.claimTask({ id: 'first', claimant: 'alice', claimId: 'first-claim' });
  await client.releaseTask({ id: 'first', token: claimed.token });
  assert.equal((await next).value.taskId, 'first', 'released work needs attention again');
  const reclaimed = await client.claimTask({ id: 'first', claimant: 'alice', claimId: 'second-claim' });
  await client.completeTask({ id: 'first', token: reclaimed.token, completionId: 'decision', resultJson: 'true' });
  assert.equal((await f.queue.wait('first', { timeoutMs: 5000 })).status, 'done');

  next = first.stream.next();
  await f.queue.submit('human', 'second', { input: { prompt: 'Second question?' } });
  assert.equal((await next).value.taskId, 'second', 'claiming and completing must not repeat attention');
  await client.claimTask({ id: 'second', claimant: 'alice', claimId: 'third-claim' });
  first.controller.abort();
  await assert.rejects(first.stream.next(), /cancel|abort/iu);
  await until(() => service.subscribers.size === 0);

  await f.queue.submit('human', 'third', { input: { prompt: 'Created while disconnected?' } });
  // Subscription races with publication: either the snapshot or the wakeup must deliver it.
  const reconnected = subscribe();
  const outstanding = [(await reconnected.stream.next()).value.taskId, (await reconnected.stream.next()).value.taskId];
  assert.deepEqual(outstanding.sort(), ['second', 'third'], 'reconnect includes claimed and new work, but excludes completed work');
  const stopped = reconnected.stream.next();
  service.close();
  await assert.rejects(stopped, /Human service stopped/);
  assert.equal(service.subscribers.size, 0);
});

test('Human metadata exposes caller-prepared job storage and workspace', async t => {
  const f = await fixture(t);
  f.start();
  const service = new HumanService(f.queue.root);
  t.after(() => service.close());
  const target = await listen(t, humanServer(service), join(f.root, 'files.sock'));
  const client = humanClient('human', { DCOMP_IN_HUMAN: target });
  await mkdir(f.queue.workspace('review-files'), { recursive: true });
  await writeFile(join(f.queue.workspace('review-files'), 'board.kicad_pcb'), '(kicad_pcb)');
  await f.queue.submit('human', 'review-files', { input: { prompt: 'Review the board' }, metadata: { purpose: 'PCB review' } });
  await until(() => service.listTasks().tasks.length);
  const metadata = JSON.parse((await client.getTask({ id: 'review-files' })).task.metadataJson);
  assert.equal(metadata.purpose, 'PCB review');
  const { files } = metadata;
  assert.equal(files.directory, f.queue.executionDirectory('review-files'));
  assert.equal(files.workspace, f.queue.workspace('review-files'));
  assert.equal(files.result, join(files.directory, 'result.json'));
  assert.equal(await readFile(join(files.workspace, 'board.kicad_pcb'), 'utf8'), '(kicad_pcb)');
  await writeFile(join(files.directory, 'report.md'), 'Move the connector.');
  const { token } = await client.claimTask({ id: 'review-files', claimant: 'alice', claimId: 'claim-files' });
  await client.completeTask({ id: 'review-files', token, completionId: 'reviewed-files', resultJson: '{"approved":false}' });
  assert.equal((await f.queue.wait('review-files', { timeoutMs: 5000 })).status, 'done');
  assert.deepEqual(JSON.parse(await readFile(files.result, 'utf8')), { approved: false });
});

test('program jobs execute a supplied command, preserve input, and propagate failures', async t => {
  const f = await fixture(t);
  f.start();
  const code = 'import json,os,sys; json.dump({"input":json.load(sys.stdin),"args":sys.argv[1:]},open(os.environ["ASYS_RESULT"],"w"))';
  await f.queue.submit('program', 'command', { args: ['python3', '-c', code, 'literal spaces', '$(no-shell)'], input: { value: 42 } });
  const outcome = await f.queue.wait('command', { timeoutMs: 5000 });
  assert.equal(outcome.status, 'done', outcome.error);
  assert.deepEqual(outcome.result, { input: { value: 42 }, args: ['literal spaces', '$(no-shell)'] });
  await f.queue.submit('program', 'empty');
  assert.equal((await f.queue.wait('empty', { timeoutMs: 5000 })).status, 'failed');
  await f.queue.submit('program', 'failure', { args: ['python3', '-c', 'raise SystemExit(17)'] });
  assert.equal((await f.queue.wait('failure', { timeoutMs: 5000 })).exit_code, 17);
});

test('cancelling a job withdraws its human decision and rejects late completion', async t => {
  const f = await fixture(t);
  f.start();
  const service = new HumanService(f.queue.root);
  t.after(() => service.close());
  await f.queue.submit('human', 'cancelled', { input: { prompt: 'Wait for me' } });
  await until(() => service.listTasks().tasks.length);
  const { token } = service.claimTask({ id: 'cancelled', claimant: 'alice', claimId: 'claim' });
  await f.queue.cancel('cancelled');
  assert.equal((await f.queue.wait('cancelled', { timeoutMs: 5000 })).status, 'cancelled');
  assert.equal(service.getTask({ id: 'cancelled' }).task.status, 'cancelled');
  assert.throws(() => service.completeTask({ id: 'cancelled', token, completionId: 'decision', resultJson: 'true' }), /no longer waiting/);
});
