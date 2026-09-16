import assert from 'node:assert/strict';
import { mkdtemp, mkdir, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { setTimeout } from 'node:timers/promises';
import test from 'node:test';
import { humanClient } from '../src/client.mjs';
import { serveHostChannel } from '../src/host-channel.mjs';
import { HumanService } from '../../asys-workers/src/human-service.mjs';
import { humanServer } from '../../asys-workers/src/human-server.mjs';
import { human } from '../../asys-workers/src/human.mjs';
import { PreparedQueue as Queue } from '../../asys-runtime/test/fixtures.mjs';
import { Reader, Writer, directionRoot } from '../../asys-runtime/javascript/channel.mjs';

async function until(check, timeout = 5000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const result = await check();
    if (result) return result;
    await setTimeout(10);
  }
  throw new Error('Timed out');
}

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), 'asys-human-test-'));
  const shutdown = new AbortController();
  const workers = new Map(), records = [], loops = [];
  let component;
  const input = new Writer(directionRoot(root, 'human', 'in'));
  const output = new Reader(directionRoot(root, 'human', 'out'));
  const f = {
    root, workers, input, output,
    async worker(name) {
      const queue = new Queue(join(root, name));
      const socket = join(root, `${name}.sock`);
      const record = { queue, socket };
      records.push(record);
      async function start() {
        record.service = new HumanService(queue.root);
        record.server = humanServer(record.service);
        await new Promise((resolve, reject) => { record.server.once('error', reject); record.server.listen(socket, resolve); });
      }
      await start();
      workers.set(name, humanClient('human', { DCOMP_IN_HUMAN: `unix://${socket}` }));
      return {
        queue,
        get service() { return record.service; },
        async task(id, input = { prompt: `${name} question?` }) {
          await queue.submit('human', id, { input, metadata: { source: name } });
          const { directory, workspace } = await queue.paths(id);
          const result = human({ job: { id, directory, workspace, input }, argv: [], env: { ASYS_REQUEST: join(queue.directory(id), 'request.json') }, signal: shutdown.signal });
          result.catch(() => {}); loops.push(result);
          await until(() => record.service.listTasks().tasks.some(task => task.id === id));
          return { result };
        },
        async restart() {
          record.service.close();
          record.server.closeConnections();
          await new Promise(resolve => record.server.close(resolve));
          await start();
        },
      };
    },
    start() {
      const controller = new AbortController();
      const running = serveHostChannel(workers, { root, signal: controller.signal, retryMs: 20, timeoutMs: 200 });
      running.catch(() => {});
      component = { controller, running };
      return component;
    },
    async stop() { component?.controller.abort(); await component?.running; component = undefined; },
    events() { return output.read(0); },
    async event(predicate) { return until(async () => (await f.events()).find(predicate)); },
    async call(worker, method, body = {}) {
      const request = await input.send('request', { worker, method, body });
      const event = await f.event(e => ['result', 'error'].includes(e.type) && e.data.request === request.sequence);
      return event;
    },
  };
  t.after(async () => {
    component?.controller.abort();
    await component?.running.catch(() => {});
    shutdown.abort();
    await Promise.allSettled(loops);
    for (const record of records) {
      record.service.close(); record.server.closeConnections();
      await new Promise(resolve => record.server.close(resolve));
    }
    await rm(root, { recursive: true, force: true });
  });
  return f;
}

test('one host channel routes duplicate task IDs to independent Human interfaces', { timeout: 10000 }, async t => {
  const f = await fixture(t), a = await f.worker('alpha'), b = await f.worker('beta');
  const first = await a.task('review', { prompt: 'Approve A?', form: { type: 'boolean' } });
  const second = await b.task('review', { prompt: 'Approve B?', form: { type: 'boolean' } });
  f.start();
  for (const worker of ['alpha', 'beta']) {
    const attention = await f.event(e => e.type === 'attention' && e.data.worker === worker);
    assert.equal(attention.data.task.id, 'review');
    assert.equal(JSON.parse(attention.data.task.metadataJson).source, worker);
    const claim = await f.call(worker, 'ClaimTask', { id: 'review', claimant: 'alice', claimId: `claim-${worker}` });
    assert.equal(claim.type, 'result');
    const token = claim.data.result.token;
    const retried = await f.call(worker, 'ClaimTask', { id: 'review', claimant: 'alice', claimId: `claim-${worker}` });
    assert.equal(retried.data.result.token, token);
    const completion = { id: 'review', token, completionId: `answer-${worker}`, resultJson: JSON.stringify(worker === 'alpha') };
    assert.equal((await f.call(worker, 'CompleteTask', completion)).type, 'result');
    assert.equal((await f.call(worker, 'CompleteTask', completion)).type, 'result');
  }
  assert.equal(await first.result, true);
  assert.equal(await second.result, false);
});

test('candidate and form validation stay with the worker; release sends attention again', async t => {
  const f = await fixture(t), a = await f.worker('alpha');
  await a.task('review', { prompt: 'Approve?', candidates: ['alice'], form: { type: 'boolean' } });
  f.start();
  const attention = await f.event(e => e.type === 'attention');
  assert.equal((await f.call('alpha', 'ClaimTask', { id: 'review', claimant: 'bob', claimId: 'wrong' })).data.code, 'PermissionDenied');
  const { token } = (await f.call('alpha', 'ClaimTask', { id: 'review', claimant: 'alice', claimId: 'right' })).data.result;
  assert.equal((await f.call('alpha', 'CompleteTask', { id: 'review', token, completionId: 'invalid', resultJson: '"yes"' })).data.code, 'InvalidArgument');
  assert.equal((await f.call('alpha', 'ReleaseTask', { id: 'review', token })).type, 'result');
  await f.event(e => e.type === 'attention' && e.sequence > attention.sequence);
  assert.equal((await f.call('alpha', 'ListTasks', { status: 'pending', limit: 1 })).data.result.tasks.length, 1);
  assert.equal((await f.call('absent', 'GetTask', { id: 'review' })).data.code, 'NotFound');
  assert.equal((await f.call('alpha', 'WatchAttention')).data.code, 'Unimplemented');
  assert.equal((await f.call('alpha', 'GetTask', { unknown: true })).data.code, 'InvalidArgument');
});

test('a disconnected worker does not block other subscriptions and reconnect recovers outstanding work', async t => {
  const f = await fixture(t), a = await f.worker('alpha');
  f.workers.set('offline', humanClient('human', { DCOMP_IN_HUMAN: `unix://${join(f.root, 'missing.sock')}` }));
  f.start();
  await f.event(e => e.type === 'worker.unavailable' && e.data.worker === 'offline');
  await a.task('review');
  const attention = await f.event(e => e.type === 'attention');
  await a.restart();
  await f.event(e => e.type === 'attention' && e.sequence > attention.sequence && e.data.task.id === 'review');
});

test('component restart replays unanswered commands with their original claim IDs', async t => {
  const f = await fixture(t), a = await f.worker('alpha');
  await a.task('review');
  f.start();
  const first = await f.call('alpha', 'ClaimTask', { id: 'review', claimant: 'alice', claimId: 'stable' });
  await f.stop();
  // Simulate death after committing a claim and reply, before cursor advance.
  await new Reader(f.input.directory).advance(first.data.request - 1);
  f.start();
  const retried = await f.event(e => e.type === 'result' && e.data.request === first.data.request && e.sequence > first.sequence);
  assert.equal(retried.data.result.token, first.data.result.token);
  await f.event(e => e.type === 'attention' && e.sequence > first.sequence && e.data.task.status === 'claimed');
});

test('only one component may consume a host channel, and ownership is released on stop', async t => {
  const f = await fixture(t);
  f.start();
  await f.event(e => e.type === 'ready');
  await assert.rejects(serveHostChannel(new Map(), { root: f.root }), /already has an owner/);
  await f.stop();
  f.start();
  await until(async () => (await f.events()).filter(e => e.type === 'ready').length === 2);
});

test('a failed host publication stops the component instead of losing a notification', async t => {
  const f = await fixture(t), a = await f.worker('alpha');
  const original = Writer.prototype.send;
  t.mock.method(Writer.prototype, 'send', function(type, data) {
    if (type === 'attention') return Promise.reject(new Error('cannot publish attention'));
    return original.call(this, type, data);
  });
  const component = f.start();
  await f.event(e => e.type === 'ready');
  await a.task('review');
  await assert.rejects(component.running, /cannot publish attention/);
});
