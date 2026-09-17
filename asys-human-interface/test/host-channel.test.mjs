import assert from 'node:assert/strict';
import { mkdtemp, mkdir, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { setTimeout } from 'node:timers/promises';
import test from 'node:test';
import { serveHostChannel } from '../src/host-channel.mjs';
import { HumanService } from '../src/human-service.mjs';
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
  const service = new HumanService(join(root, 'service'));
  const loops = [];
  let component;
  const input = new Writer(directionRoot(root, 'human', 'in'));
  const output = new Reader(directionRoot(root, 'human', 'out'));
  const f = {
    root, service, input, output,
    async worker(name) {
      return {
        async task(id, input = { prompt: `${name} question?` }) {
          const pending = service.ask({ id: `${name}.${id}`, inputJson: JSON.stringify(input), metadataJson: JSON.stringify({ component: name, source: name }) }, { signal: shutdown.signal });
          const result = pending.then(value => JSON.parse(value.resultJson));
          result.catch(() => {}); loops.push(result);
          return { result };
        },
      };
    },
    start() {
      const controller = new AbortController();
      const running = serveHostChannel(service, { root, signal: controller.signal });
      running.catch(() => {});
      component = { controller, running };
      return component;
    },
    async stop() { component?.controller.abort(); await component?.running; component = undefined; },
    events() { return output.read(0); },
    async event(predicate) { return until(async () => (await f.events()).find(predicate)); },
    async call(worker, method, body = {}) {
      const request = await input.send('request', { worker, method, body: { ...body, ...(body.id && { id: `${worker}.${body.id}` }) } });
      const event = await f.event(e => ['result', 'error'].includes(e.type) && e.data.request === request.sequence);
      return event;
    },
  };
  t.after(async () => {
    component?.controller.abort();
    await component?.running.catch(() => {});
    shutdown.abort();
    await Promise.allSettled(loops);
    service.close();
    await rm(root, { recursive: true, force: true });
  });
  return f;
}

test('one host channel routes duplicate task IDs to independent requests in one Human service', { timeout: 10000 }, async t => {
  const f = await fixture(t), a = await f.worker('alpha'), b = await f.worker('beta');
  const first = await a.task('review', { prompt: 'Approve A?', form: { type: 'boolean' } });
  const second = await b.task('review', { prompt: 'Approve B?', form: { type: 'boolean' } });
  f.start();
  for (const worker of ['alpha', 'beta']) {
    const attention = await f.event(e => e.type === 'attention' && e.data.worker === worker);
    assert.equal(attention.data.task.id, `${worker}.review`);
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

test('candidate and form validation stay with the human service; release sends attention again', async t => {
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
  await assert.rejects(serveHostChannel(f.service, { root: f.root }), /already has an owner/);
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
