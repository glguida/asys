import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { setTimeout } from 'node:timers/promises';
import test from 'node:test';
import { HumanService } from '../src/human-service.mjs';
import { humanServer } from '../src/human-server.mjs';
import { humanClient } from '../../asys-workers/src/client.mjs';

async function until(check) {
  for (let i = 0; i < 500; i++) {
    const value = check();
    if (value) return value;
    await setTimeout(10);
  }
  throw new Error('Timed out');
}

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), 'asys-human-service-'));
  const service = new HumanService(root);
  const server = humanServer(service);
  const socket = join(root, 'rpc.sock');
  await new Promise(resolve => server.listen(socket, resolve));
  const client = humanClient('human', { DCOMP_IN_HUMAN: `unix://${socket}` });
  t.after(async () => {
    server.closeConnections();
    service.close();
    await new Promise(resolve => server.close(resolve));
    await rm(root, { recursive: true, force: true });
  });
  return { root, service, client };
}

test('workers push requests over Human and receive validated answers without sharing job storage', async t => {
  const { service, client } = await fixture(t);
  const request = { id: 'worker.job', inputJson: JSON.stringify({ prompt: 'Approve?', candidates: ['alice'], form: { type: 'boolean' } }),
    metadataJson: JSON.stringify({ component: 'worker', files: { workspace: '/work' } }) };
  const outcome = client.ask(request);
  const task = await until(() => service.listTasks().tasks[0]);
  assert.equal(task.id, request.id);
  assert.equal(JSON.parse(task.metadataJson).files.workspace, '/work');
  assert.throws(() => service.claimTask({ id: task.id, claimant: 'bob', claimId: 'bob' }), /candidate/);
  const { token } = service.claimTask({ id: task.id, claimant: 'alice', claimId: 'claim' });
  assert.equal(service.claimTask({ id: task.id, claimant: 'alice', claimId: 'claim' }).token, token);
  assert.throws(() => service.completeTask({ id: task.id, token, completionId: 'bad', resultJson: '"yes"' }), /form/);
  const completion = { id: task.id, token, completionId: 'answer', resultJson: 'false' };
  service.completeTask(completion);
  assert.equal((await outcome).resultJson, 'false');
  assert.equal(service.completeTask(completion).task.status, 'completed');
  assert.equal((await client.ask(request)).resultJson, 'false', 'a lost response can be retrieved without asking twice');
  await assert.rejects(client.ask({ ...request, inputJson: '{"prompt":"Different"}' }), /different input/);
  service.close();
  const recovered = new HumanService(service.root);
  t.after(() => recovered.close());
  assert.equal((await recovered.ask(request)).resultJson, 'false', 'completed answers survive a service restart');
});

test('disconnecting a waiting worker withdraws its request and rejects late answers', async t => {
  const { service, client } = await fixture(t);
  const stopped = new AbortController();
  const outcome = client.ask({ id: 'worker.job', inputJson: '{"prompt":"Wait"}', metadataJson: '{}' }, { signal: stopped.signal });
  const rejected = assert.rejects(outcome, /cancel|abort/i);
  const task = await until(() => service.listTasks().tasks[0]);
  const { token } = service.claimTask({ id: task.id, claimant: 'alice', claimId: 'claim' });
  stopped.abort();
  await rejected;
  await until(() => service.getTask({ id: task.id }).task.status === 'cancelled');
  assert.throws(() => service.completeTask({ id: task.id, token, completionId: 'late', resultJson: 'true' }), /no longer waiting/);
});

test('separate human services cannot see or answer each other’s requests', async t => {
  const global = await fixture(t), local = await fixture(t);
  const outcome = local.client.ask({ id: 'worker.job', inputJson: '{"prompt":"Local only"}', metadataJson: '{}' });
  const task = await until(() => local.service.listTasks().tasks[0]);
  assert.deepEqual(global.service.listTasks().tasks, []);
  assert.throws(() => global.service.getTask({ id: task.id }), /not found/);
  const { token } = local.service.claimTask({ id: task.id, claimant: 'alice', claimId: 'claim' });
  local.service.completeTask({ id: task.id, token, completionId: 'answer', resultJson: 'true' });
  assert.equal((await outcome).resultJson, 'true');
});
