import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { PreparedQueue as Queue } from './fixtures.mjs';

const exec = promisify(execFile);
const cli = fileURLToPath(new URL('../tools/asys-runtime', import.meta.url));
const worker = fileURLToPath(new URL('./worker.py', import.meta.url));

test('JavaScript producers and Python executors share the same filesystem contract', async t => {
  const root = await mkdtemp(join(tmpdir(), 'asys-runtime-js-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const queue = new Queue(join(root, 'state'));
  const request = { args: ['space here', '--literal'], input: { hello: 'world' } };
  await Promise.all(Array.from({ length: 8 }, () => queue.submit('program', 'same', request)));
  await assert.rejects(queue.submit('program', 'same', { input: 'different' }), /different input/);
  const config = join(root, 'runtime.json');
  await writeFile(config, JSON.stringify({ version: 1, types: { program: { command: ['python3', worker, 'record'] } } }));
  await exec('python3', [cli, 'run', config, '--root', queue.root, '--once']);
  const state = await queue.wait('same', { timeoutMs: 1000 });
  assert.equal(state.status, 'done');
  assert.deepEqual(state.result.args, request.args);
  assert.deepEqual(state.result.input, request.input);
  assert.deepEqual(state.result.executions, ['1']);
});

test('JavaScript control uses the runtime cancellation with independently submitted jobs', async t => {
  const root = await mkdtemp(join(tmpdir(), 'asys-runtime-js-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const queue = new Queue(root);
  await queue.submit('program', 'job');
  assert.equal((await queue.cancel('job')).status, 'cancelled');
  await queue.resubmit('job', 'new-job');
  assert.equal((await queue.state('job')).status, 'cancelled');
  const abort = new AbortController();
  const waiting = queue.wait('new-job', { signal: abort.signal });
  abort.abort(new Error('Stopping'));
  await assert.rejects(waiting);
  assert.equal((await queue.state('new-job')).status, 'pending');
});
