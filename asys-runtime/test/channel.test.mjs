import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFile, spawn } from 'node:child_process';
import { once } from 'node:events';
import { promisify } from 'node:util';
import { Reader, Writer, directionRoot } from '../javascript/channel.mjs';

const exec = promisify(execFile);
const packageRoot = fileURLToPath(new URL('..', import.meta.url));
const python = (code) => exec('python3', ['-c', code], { cwd: packageRoot, maxBuffer: 1 << 24 });

test('JavaScript and Python endpoints read each other\'s events in order with a shared cursor', async t => {
  const root = await mkdtemp(join(tmpdir(), 'asys-channel-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const out = directionRoot(root, 'demo', 'out');
  const writer = await new Writer(out).ready();
  await writer.send('started', { pid: 1 });
  await writer.send('progress', [1, 2, 3]);
  const { stdout } = await python(`
import json
from asys_runtime.channel import Reader, Writer, direction_root
reader = Reader(${JSON.stringify(out)})
events = reader.read()
reader.advance(events[-1]["sequence"])
Writer(direction_root(${JSON.stringify(root)}, "demo", "in")).send("ack", {"upto": events[-1]["sequence"]})
print(json.dumps([[e["sequence"], e["type"], e["data"]] for e in events]))`);
  assert.deepEqual(JSON.parse(stdout), [[1, 'started', { pid: 1 }], [2, 'progress', [1, 2, 3]]]);
  const reader = await new Reader(out).ready();
  assert.equal(await reader.cursor(), 2, 'the Python reader\'s cursor is visible to JavaScript');
  const inbound = await new Reader(directionRoot(root, 'demo', 'in')).ready();
  const [ack] = await inbound.read();
  assert.deepEqual([ack.sequence, ack.type, ack.data], [1, 'ack', { upto: 2 }]);
  await writer.send('done', null);
  assert.deepEqual((await reader.read()).map(e => e.sequence), [3], 'a restarted writer continues the sequence');
});

test('follow yields live events, honours cancellation, and never advances the cursor itself', async t => {
  const root = await mkdtemp(join(tmpdir(), 'asys-channel-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const directory = directionRoot(root, 'demo', 'out');
  const writer = await new Writer(directory).ready();
  const reader = await new Reader(directory).ready();
  await writer.send('first', 1);
  const controller = new AbortController();
  const seen = [];
  const following = (async () => {
    for await (const event of reader.follow(undefined, { signal: controller.signal })) {
      seen.push(event.type);
      if (event.type === 'second') controller.abort();
    }
  })();
  await new Promise(resolve => global.setTimeout(resolve, 120));
  await writer.send('second', 2);
  await assert.rejects(following, { name: 'AbortError' });
  assert.deepEqual(seen, ['first', 'second']);
  assert.equal(await reader.cursor(), 0);
  const timed = [];
  for await (const event of reader.follow(0, { timeoutMs: 100 })) timed.push(event.sequence);
  assert.deepEqual(timed, [1, 2]);
});

test('concurrent JavaScript writers claim distinct sequence numbers with no gaps', async t => {
  const root = await mkdtemp(join(tmpdir(), 'asys-channel-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const directory = directionRoot(root, 'demo', 'out');
  const writers = await Promise.all(Array.from({ length: 4 }, () => new Writer(directory).ready()));
  await Promise.all(writers.map((writer, w) => (async () => {
    for (let i = 0; i < 20; i++) await writer.send('tick', { w, i });
  })()));
  const events = await new Reader(directory).read(0);
  assert.deepEqual(events.map(e => e.sequence), Array.from({ length: 80 }, (_, i) => i + 1));
  for (let w = 0; w < 4; w++) {
    assert.deepEqual(events.filter(e => e.data.w === w).map(e => e.data.i), Array.from({ length: 20 }, (_, i) => i));
  }
  const reader = await new Reader(directory).ready();
  await reader.advance(80);
  assert.equal(await reader.prune(), 79, 'the latest event survives a full prune');
  assert.equal((await writers[0].send('after', null)).sequence, 81, 'a stale writer never reuses a number');
  assert.deepEqual((await reader.read()).map(e => e.sequence), [81]);
});

test('overlapping sends through one writer cannot overtake an unpublished event', async t => {
  const root = await mkdtemp(join(tmpdir(), 'asys-channel-overlap-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const writer = await new Writer(root).ready();
  const reader = await new Reader(root).ready();
  let finished = false;
  const sending = Promise.all(Array.from({ length: 64 }, (_, i) => writer.send('tick', { i })))
    .finally(() => { finished = true; });
  const received = [];
  let after = 0;
  do {
    for (const event of await reader.read(after)) {
      received.push(event);
      after = event.sequence;
    }
  } while (!finished);
  await sending;
  received.push(...await reader.read(after));
  assert.deepEqual(received.map(e => e.sequence), Array.from({ length: 64 }, (_, i) => i + 1));
  assert.equal(new Set(received.map(e => e.data.i)).size, 64);
});

test('a killed publisher releases its lock without consuming a sequence number', { timeout: 5000 }, async t => {
  const root = await mkdtemp(join(tmpdir(), 'asys-channel-killed-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const child = spawn('python3', ['-c', `
import signal
import asys_runtime.channel as channel
def paused(*args):
    print('publishing', flush=True)
    signal.pause()
channel.publish_json = paused
channel.Writer(${JSON.stringify(root)}).send('unfinished')
`], { cwd: packageRoot });
  const closed = once(child, 'close');
  t.after(async () => { child.kill('SIGKILL'); await closed; });
  await once(child.stdout, 'data');
  const writer = new Writer(root);
  let finished = false;
  const sent = writer.send('after').then(event => { finished = true; return event; });
  await new Promise(resolve => setTimeout(resolve, 50));
  assert.equal(finished, false, 'the live publisher holds the direction lock');
  child.kill('SIGKILL');
  await closed;
  assert.equal((await sent).sequence, 1);
  assert.deepEqual((await new Reader(root).read()).map(e => e.type), ['after']);
});
