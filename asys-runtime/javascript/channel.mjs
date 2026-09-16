import { makeDirectory, fileMode } from './permissions.mjs';
// Ordered event streams between a component and the host over the shared root.
// Mirrors asys_runtime/channel.py: one file per event named by sequence number,
// published by an atomic link so numbers are claimed at publish time, and a
// per-direction cursor kept by the reader. No long-lived owner, nothing executed.
import { link, open, readdir, rm, stat, unlink } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { setTimeout } from 'node:timers/promises';
import { DatabaseSync } from 'node:sqlite';
import { readJSON, writeJSON } from './queue.mjs';

export const DIRECTIONS = ['in', 'out'];
export const POLL_MS = 50;
const WIDTH = 9;
const LIMIT = 10 ** WIDTH - 1;
const BYTES = 8 * 1024 * 1024;

export function channelRoot(root, name) {
  return join(resolve(root), 'channels', validName(name, 'Channel name'));
}

export function directionRoot(root, name, direction) {
  if (!DIRECTIONS.includes(direction)) throw new Error(`Channel direction must be one of ${DIRECTIONS.join(', ')}`);
  return join(channelRoot(root, name), direction);
}

class Stream {
  constructor(directory) { this.directory = resolve(directory); }
  async ready() {
    makeDirectory(this.directory, { recursive: true, mode: 0o700 });
    this.mode = (await stat(this.directory)).mode & 0o666;
    return this;
  }
  async exclusive(action) {
    await this.ready();
    // A short SQLite transaction supplies the same OS-backed lock to Python
    // and Node. The database stores no channel data. Never unlink it.
    const path = join(this.directory, '.write-lock.sqlite');
    await publishText(path, '', this.mode);
    const database = new DatabaseSync(path);
    try {
      const deadline = Date.now() + 30_000;
      for (;;) {
        try { database.exec('BEGIN EXCLUSIVE'); break; }
        catch (error) {
          if (![5, 6].includes(error.errcode) || Date.now() >= deadline) throw error;
          await setTimeout(10);
        }
      }
      const result = await action();
      database.exec('COMMIT');
      return result;
    } finally { database.close(); }
  }
  path(sequence) { return join(this.directory, `${String(sequence).padStart(WIDTH, '0')}.json`); }
  async sequences() {
    const sequences = [];
    for (const entry of await readdir(this.directory)) {
      const sequence = parseSequence(entry);
      if (sequence !== null) sequences.push(sequence);
    }
    return sequences.sort((a, b) => a - b);
  }
  async last() { const sequences = await this.sequences(); return sequences.length ? sequences.at(-1) : 0; }
  async event(sequence) { return validEvent(await readJSON(this.path(sequence)), this.directory, sequence); }
}

export class Writer extends Stream {
  constructor(directory) { super(directory); this.pending = Promise.resolve(); }
  async send(type, data = null) {
    validName(type, 'Event type');
    data = JSON.parse(JSON.stringify(data ?? null));
    const sending = this.pending.then(() => this.exclusive(async () => {
      const sequence = await this.last() + 1;
      if (sequence > LIMIT) throw new Error(`${this.directory}: channel sequence exhausted`);
      const event = { version: 1, sequence, type, time: timestamp(), data };
      if (!await publishJSON(this.path(sequence), event, this.mode)) throw new Error(`${this.directory}: concurrent publisher did not hold the channel lock`);
      return event;
    }));
    this.pending = sending.catch(() => {});
    return sending;
  }
}

export class Reader extends Stream {
  constructor(directory) { super(directory); this.cursorPath = join(this.directory, 'cursor.json'); }
  async cursor() {
    let value;
    try { value = await readJSON(this.cursorPath); }
    catch (error) { if (error.code === 'ENOENT') return 0; throw error; }
    if (!value || typeof value !== 'object' || !Number.isSafeInteger(value.after) || value.after < 0) throw new Error(`${this.cursorPath}: invalid cursor`);
    return value.after;
  }
  async advance(sequence) {
    if (!Number.isSafeInteger(sequence) || sequence < 0) throw new Error('Cursor must be a non-negative integer');
    await this.ready();
    await writeJSON(this.cursorPath, { version: 1, after: sequence }, { mode: this.mode });
  }
  async read(after, { limit } = {}) {
    await this.ready();
    if (after === undefined) after = await this.cursor();
    const events = [];
    for (const sequence of await this.sequences()) {
      if (sequence <= after) continue;
      if (limit !== undefined && events.length >= limit) break;
      try { events.push(await this.event(sequence)); }
      catch (error) { if (error.code !== 'ENOENT') throw error; }
    }
    return events;
  }
  // Yield events past `after` (default: the cursor) as they are published.
  // Ends after `timeoutMs` without a new event, or on `signal`. Never advances
  // the cursor: callers acknowledge with advance().
  async *follow(after, { signal, timeoutMs, pollMs = POLL_MS } = {}) {
    await this.ready();
    let position = after === undefined ? await this.cursor() : after;
    let deadline = timeoutMs === undefined ? Infinity : Date.now() + timeoutMs;
    for (;;) {
      signal?.throwIfAborted();
      const events = await this.read(position);
      if (events.length) {
        for (const event of events) { position = event.sequence; yield event; }
        deadline = timeoutMs === undefined ? Infinity : Date.now() + timeoutMs;
        continue;
      }
      if (Date.now() >= deadline) return;
      await setTimeout(pollMs, undefined, { signal });
    }
  }
  // Delete events up to and including `before` (default: the cursor). The
  // latest event always survives: it carries the high-water mark writers use.
  async prune(before) {
    if (before === undefined) before = await this.cursor();
    return this.exclusive(async () => {
      let removed = 0;
      for (const sequence of (await this.sequences()).slice(0, -1)) {
        if (sequence > before) break;
        await unlink(this.path(sequence)); removed++;
      }
      if (removed) await syncDirectory(this.directory);
      return removed;
    });
  }
}

function parseSequence(name) {
  const match = /^(\d{9})\.json$/u.exec(name);
  return match ? Number(match[1]) : null;
}

function validName(value, label) {
  if (typeof value !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/u.test(value)) throw new Error(`${label} is not a valid runtime name`);
  return value;
}

function validEvent(value, directory, sequence) {
  if (!value || typeof value !== 'object' || value.version !== 1 || !Number.isSafeInteger(value.sequence)
      || typeof value.type !== 'string' || typeof value.time !== 'string' || !('data' in value)) {
    throw new Error(`${directory}: invalid event ${sequence}`);
  }
  if (value.sequence !== sequence) throw new Error(`${directory}: event file ${sequence} claims sequence ${value.sequence}`);
  validName(value.type, 'Event type');
  return value;
}

function timestamp() {
  // Same shape as the Python side: ISO 8601 with microseconds and a UTC offset.
  return new Date().toISOString().replace(/Z$/u, '000+00:00');
}

// Create `path` with `value` atomically; false if it already exists.
async function publishJSON(path, value, mode) {
  const text = JSON.stringify(value, null, 2) + '\n';
  if (Buffer.byteLength(text) > BYTES) throw new Error(`${path}: JSON exceeds 8 MiB`);
  return publishText(path, text, mode);
}

async function publishText(path, text, mode) {
  const temporary = join(dirname(path), `.${path.split('/').at(-1)}.tmp.${process.pid}.${crypto.randomUUID()}`);
  try {
    const file = await open(temporary, 'wx', mode);
    try { await file.chmod(fileMode(path, mode)); await file.writeFile(text); await file.sync(); }
    finally { await file.close(); }
    try { await link(temporary, path); }
    catch (error) { if (error.code === 'EEXIST') return false; throw error; }
    await syncDirectory(dirname(path));
    return true;
  } finally { await rm(temporary, { force: true }); }
}

async function syncDirectory(path) {
  const directory = await open(path, 'r');
  try { await directory.sync(); } finally { await directory.close(); }
}
