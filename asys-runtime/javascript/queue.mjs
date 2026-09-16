import { makeDirectory, fileMode } from './permissions.mjs';
import { chmod, mkdtemp, open, realpath, rename, rm, stat } from 'node:fs/promises';
import { resolve, join, dirname, relative, isAbsolute } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFile } from 'node:child_process';
import { promisify, isDeepStrictEqual } from 'node:util';
import { setTimeout } from 'node:timers/promises';

const exec = promisify(execFile);
const CLI = fileURLToPath(new URL('../tools/asys-runtime', import.meta.url));
const LIMIT = 8 * 1024 * 1024;
export const TERMINAL = new Set(['done', 'failed', 'cancelled', 'interrupted']);

export class Queue {
  constructor(root, { python = 'python3' } = {}) {
    this.root = resolve(root);
    this.jobs = join(this.root, 'jobs');
    this.python = python;
  }
  directory(id) { return join(this.jobs, name(id, 'Job ID')); }
  async paths(id) {
    const request = await this.request(id);
    return Object.fromEntries(['directory', 'workspace'].map(key => [key, resolve(this.root, request[key])]));
  }
  async submit(type, id, { directory: jobDirectory, workspace, args = [], input = null, metadata = {} }) {
    name(type, 'Job type');
    const directory = this.directory(id);
    if (!Array.isArray(args) || args.some(arg => typeof arg !== 'string' || arg.includes('\0'))) throw new Error('Job args must be strings without NUL characters');
    if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)) throw new Error('Job metadata must be an object');
    const paths = {};
    for (const [key, value] of Object.entries({ directory: jobDirectory, workspace })) {
      if (typeof value !== 'string' || !value) throw new Error(`Job ${key} must be a prepared directory`);
      const path = resolve(value);
      if (!(await stat(path)).isDirectory()) throw new Error(`Job ${key} must be a prepared directory: ${path}`);
      paths[key] = relative(this.root, path) || '.';
    }
    if (await realpath(jobDirectory) === await realpath(workspace)) throw new Error('Job directory and workspace must be separate');
    const request = JSON.parse(JSON.stringify({ version: 1, id, type, ...paths, args, input, metadata }));
    makeDirectory(this.jobs, { recursive: true, mode: 0o700 });
    const staging = await mkdtemp(join(this.jobs, '.submit-'));
    await chmod(staging, fileMode(staging, 0o700) | ((await stat(this.jobs)).mode & 0o2000));
    try {
      await writeJSON(join(staging, 'request.json'), request);
      const time = new Date().toISOString().replace(/Z$/u, '000+00:00');
      await writeJSON(join(staging, 'state.json'), { version: 1, id, type, status: 'pending', submitted_at: time, updated_at: time });
      await syncDirectory(staging);
      try {
        await rename(staging, directory);
        await syncDirectory(this.jobs);
      } catch (error) {
        if (!['ENOTEMPTY', 'EEXIST'].includes(error.code)) throw error;
        if (!isDeepStrictEqual(await this.request(id), request)) throw new Error(`Job ${id} already exists with different input`);
      }
    } finally { await rm(staging, { recursive: true, force: true }); }
    return this.state(id);
  }
  async request(id) {
    const value = await readJSON(join(this.directory(id), 'request.json'));
    if (value.version !== 1 || value.id !== id) throw new Error(`Job ${id}: invalid request`);
    for (const key of ['directory', 'workspace']) {
      if (typeof value[key] !== 'string' || !value[key] || value[key].includes('\0') || isAbsolute(value[key])) {
        throw new Error(`Job ${id}: ${key} must be a relative directory reference`);
      }
    }
    return value;
  }
  async state(id) {
    const state = await readJSON(join(this.directory(id), 'state.json'));
    if (state.version !== 1 || state.id !== id || ![...TERMINAL, 'pending', 'running'].includes(state.status)) {
      throw new Error(`Job ${id}: invalid state`);
    }
    return state;
  }
  async wait(id, { signal, timeoutMs } = {}) {
    const deadline = timeoutMs === undefined ? Infinity : Date.now() + timeoutMs;
    for (;;) {
      signal?.throwIfAborted();
      const state = await this.state(id);
      if (TERMINAL.has(state.status)) return state;
      if (Date.now() >= deadline) throw new Error(`Timed out waiting for job ${id}`);
      await setTimeout(50, undefined, { signal });
    }
  }
  async cancel(id) {
    name(id, 'Job ID');
    try {
      const { stdout } = await exec(this.python, [CLI, 'cancel', id, '--root', this.root], { maxBuffer: LIMIT });
      return JSON.parse(stdout);
    } catch (error) { throw new Error(error.stderr?.trim() || error.message, { cause: error }); }
  }
}

function name(value, label) {
  if (typeof value !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/u.test(value)) throw new Error(`${label} is not a valid runtime name`);
  return value;
}

export async function readJSON(path) {
  const file = await open(path, 'r');
  try {
    const { size } = await file.stat();
    if (size > LIMIT) throw new Error(`${path}: JSON exceeds 8 MiB`);
    const buffer = Buffer.alloc(Math.min(size + 1, LIMIT + 1));
    const { bytesRead } = await file.read(buffer, 0, buffer.length, 0);
    if (bytesRead > LIMIT) throw new Error(`${path}: JSON exceeds 8 MiB`);
    return JSON.parse(buffer.subarray(0, bytesRead).toString('utf8'));
  } finally { await file.close(); }
}

export async function writeJSON(path, value, { mode = 0o600 } = {}) {
  const text = JSON.stringify(value, null, 2) + '\n';
  if (Buffer.byteLength(text) > LIMIT) throw new Error(`${path}: JSON exceeds 8 MiB`);
  const temporary = `${path}.tmp.${process.pid}.${crypto.randomUUID()}`;
  try {
    const file = await open(temporary, 'wx', mode);
    try { await file.chmod(fileMode(path, mode)); await file.writeFile(text); await file.sync(); }
    finally { await file.close(); }
    await rename(temporary, path);
    await syncDirectory(dirname(path));
  } finally { await rm(temporary, { force: true }); }
}

async function syncDirectory(path) {
  const directory = await open(path, 'r');
  try { await directory.sync(); } finally { await directory.close(); }
}
