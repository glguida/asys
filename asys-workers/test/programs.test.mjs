import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { connectNodeAdapter } from '@connectrpc/connect-node';
import { Provider } from '@cyclo/provider/contract';
import { PreparedQueue as Queue } from '../../asys-runtime/test/fixtures.mjs';
import { HumanService } from '../../asys-human-interface/src/human-service.mjs';
import { humanServer } from '../../asys-human-interface/src/human-server.mjs';
import { assistant, model } from './helpers.mjs';

const executor = fileURLToPath(new URL('../../asys-runtime/tools/asys-runtime', import.meta.url));
const agentProgram = fileURLToPath(new URL('../tools/asys-agent', import.meta.url));
const humanProgram = fileURLToPath(new URL('../tools/asys-human', import.meta.url));
const goalProgram = fileURLToPath(new URL('../tools/asys-goal', import.meta.url));
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
    goal: { command: [process.execPath, goalProgram] },
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
  test(`a persistently invalid agent result fails the job after correction: ${content}`, async t => {
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
    assert.equal(requests, 2);
    assert.equal(state.exit_code, 1);
    assert.equal(state.artifacts, undefined);
  });
}

test('human jobs call their dcomp input and publish the answer into their own result file', async t => {
  const f = await fixture(t);
  const service = new HumanService(join(f.root, 'human-service'));
  t.after(() => service.close());
  const target = await listen(t, humanServer(service), join(f.root, 'human.sock'));
  f.start({ DCOMP_IN_HUMAN: target, DCOMP_COMPONENT_NAME: 'workers' });
  await f.queue.submit('human', 'approval', { input: { prompt: 'Review the board', form: { type: 'boolean' } }, metadata: { purpose: 'PCB review' } });
  const task = await until(() => service.listTasks().tasks[0]);
  assert.equal(task.id, 'workers.approval');
  const metadata = JSON.parse(task.metadataJson);
  assert.equal(metadata.component, 'workers');
  assert.equal(metadata.purpose, 'PCB review');
  assert.deepEqual(metadata.files, { workspace: f.queue.workspace('approval') });
  const { token } = service.claimTask({ id: task.id, claimant: 'alice', claimId: 'claim' });
  service.completeTask({ id: task.id, token, completionId: 'answer', resultJson: 'false' });
  const outcome = await f.queue.wait('approval', { timeoutMs: 5000 });
  assert.equal(outcome.status, 'done', outcome.error);
  assert.equal(outcome.result, false);
  assert.equal(JSON.parse(await readFile(join(f.queue.executionDirectory('approval'), 'result.json'), 'utf8')), false);
});

for (const phase of ['define', 'review', 'implement', 'verify']) {
  for (const action of ['retry', 'stop', 'cancel']) {
    test(`one runtime goal job handles ${phase} help and human ${action}`, async t => {
      const f = await fixture(t);
      const models = join(f.root, 'models.json');
      await writeFile(models, JSON.stringify({ simple: model.id }));
      const counts = { define: 0, review: 0, implement: 0, verify: 0 };
      const service = new HumanService(join(f.root, 'human-service'));
      t.after(() => service.close());
      const provider = createServer(connectNodeAdapter({ routes(router) { router.service(Provider, {
        listModels() { return { models: [model] }; },
        async *infer(request) {
          assert.equal(request.model, model.id);
          const { context } = JSON.parse(request.payload);
          assert.equal(context.messages.length, 1, 'every phase and human retry is a fresh session');
          const { phase: current } = JSON.parse(context.messages[0].content[0].text.split('Assignment data:\n')[1]);
          const help = ++counts[current] === 1 && current === phase;
          const result = help
            ? { final: 'Checked the workspace. The required input is unavailable.', exception: 'Input missing', question: 'Please supply the input.' }
            : {
              define: { final: 'Defined the required artifact.', exception: null, contract: { criteria: [
                { id: 'C1', requirement: 'Produce an artifact', basis: 'User request', verification: 'Inspect artifact.txt' },
              ] } },
              review: { final: 'Criteria cover the request.', exception: null, decision: 'accept' },
              implement: { final: 'Implementation finished.', exception: null },
              verify: { final: 'Inspected the required artifact.', exception: null, coverage: 'complete',
                criteria: [{ id: 'C1', status: 'satisfied',
                  evidence: [{ source: 'artifact.txt', observation: 'Inspected its complete contents.' }] }] },
            }[current];
          yield { payload: JSON.stringify({ type: 'done', reason: 'stop', message: assistant([{ type: 'text', text: JSON.stringify(result) }]) }) };
        },
      }); } }));
      f.start({ ASYS_SYSTEM_MODELS: models, DCOMP_COMPONENT_NAME: 'goal-workers',
        DCOMP_IN_INFERENCE: await listen(t, provider, join(f.root, 'provider.sock')),
        DCOMP_IN_HUMAN: await listen(t, humanServer(service), join(f.root, 'human.sock')) });
      await f.queue.submit('goal', 'design', { input: { goal: 'Produce an artifact', maxAttempts: 1 }, metadata: { run_id: 'test-run' } });
      const task = await until(() => service.listTasks().tasks[0]);
      assert.equal((await f.queue.state('design')).status, 'running');
      assert.deepEqual(await readdir(f.queue.jobs), ['design']);
      const metadata = JSON.parse(task.metadataJson);
      assert.equal(metadata.job_id, 'design');
      assert.equal(metadata.goal_phase, phase);
      assert.equal(metadata.run_id, 'test-run');
      assert.deepEqual(metadata.files, { workspace: f.queue.workspace('design') });
      const { token } = service.claimTask({ id: task.id, claimant: 'alice', claimId: 'claim' });
      if (action === 'cancel') await f.queue.cancel('design');
      else service.completeTask({ id: task.id, token, completionId: 'answer', resultJson: JSON.stringify({ action, guidance: 'The input is now available.' }) });
      const outcome = await f.queue.wait('design', { timeoutMs: 10000 });
      assert.equal(outcome.status, { retry: 'done', stop: 'failed', cancel: 'cancelled' }[action], outcome.error);
      if (action === 'retry') {
        assert.equal(outcome.result.verified, true);
        assert.equal(counts[phase], 2);
      } else if (action === 'stop') {
        assert.equal(outcome.result.verified, false);
        assert.equal(outcome.error, 'Stopped by human');
      } else await until(() => service.getTask({ id: task.id }).task.status === 'cancelled');
    });
  }
}

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
  const service = new HumanService(join(f.root, 'human-service'));
  t.after(() => service.close());
  const target = await listen(t, humanServer(service), join(f.root, 'cancel.sock'));
  f.start({ DCOMP_IN_HUMAN: target, DCOMP_COMPONENT_NAME: 'workers' });
  await f.queue.submit('human', 'cancelled', { input: { prompt: 'Wait for me' } });
  const task = await until(() => service.listTasks().tasks[0]);
  const { token } = service.claimTask({ id: task.id, claimant: 'alice', claimId: 'claim' });
  await f.queue.cancel('cancelled');
  assert.equal((await f.queue.wait('cancelled', { timeoutMs: 5000 })).status, 'cancelled');
  await until(() => service.getTask({ id: task.id }).task.status === 'cancelled');
  assert.throws(() => service.completeTask({ id: task.id, token, completionId: 'decision', resultJson: 'true' }), /no longer waiting/);
});
