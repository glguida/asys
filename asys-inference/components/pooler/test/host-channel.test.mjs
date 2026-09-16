import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import test from 'node:test';
import { serveProviderChannel } from '@cyclo/provider/host-channel';
import { Reader, Writer, directionRoot } from '../../vendor/asys-runtime/channel.mjs';

const helper = fileURLToPath(new URL('../../../tools/provider-channel', import.meta.url));
const execute = promisify(execFile);

async function fixture(t, provider) {
  const root = await mkdtemp(join(tmpdir(), 'asys-catalogue-'));
  let shutdown, running;
  const start = async () => {
    shutdown = new AbortController();
    const ready = Promise.withResolvers();
    running = serveProviderChannel(provider, root, { signal: shutdown.signal, onReady: ready.resolve });
    await Promise.race([ready.promise, running]);
  };
  const stop = async () => { shutdown.abort(); await running; };
  t.after(async () => { await stop(); await rm(root, { recursive: true, force: true }); });
  await start();
  const input = await new Writer(directionRoot(root, 'provider', 'in')).ready();
  const output = await new Reader(directionRoot(root, 'provider', 'out')).ready();
  return { root, input, output, start, stop, async catalogue() {
    const { stdout } = await execute('python3', [helper, root], { timeout: 5000 });
    return JSON.parse(stdout);
  } };
}

test('catalogue errors reach the host and a later request preserves unknown metadata', { timeout: 10_000 }, async t => {
  let fail = true;
  const f = await fixture(t, { async listModels() {
    if (fail) throw new Error('Upstream catalogue unavailable');
    return { models: [{ id: 'pool/model', contextWindowTokens: 9007199254740993n,
      extensions: [{ typeUrl: 'example.test/Unknown', value: Uint8Array.from([0, 255, 1]) }] }] };
  } });
  await assert.rejects(f.catalogue(), error => /Upstream catalogue unavailable/u.test(error.stderr));
  fail = false;
  const { models } = await f.catalogue();
  assert.equal(models[0].id, 'pool/model');
  assert.equal(models[0].contextWindowTokens, '9007199254740993');
  assert.deepEqual(models[0].extensions, [{ typeUrl: 'example.test/Unknown', value: 'AP8B' }]);
});

test('restart rejects requests from the old instance and accepts a fresh host request', { timeout: 10_000 }, async t => {
  let calls = 0;
  const f = await fixture(t, { async listModels() { calls++; return { models: [] }; } });
  const after = await f.output.last();
  const instance = (await f.output.event(after)).data.instance;
  await f.stop();
  const pending = await f.input.send('models', { instance });
  await f.start();
  let rejected;
  for await (const event of f.output.follow(after, { timeoutMs: 2000 })) {
    if (event.data.request === pending.sequence) { rejected = event; break; }
  }
  assert.equal(rejected?.type, 'error');
  assert.match(rejected.data.message, /restarted/u);
  assert.notEqual(rejected.data.instance, instance);
  assert.equal(calls, 0);
  assert.deepEqual(await f.catalogue(), { models: [] });
  assert.equal(calls, 1);
});
