import { makeDirectory, prepareFile } from '../../asys-runtime/javascript/permissions.mjs';
import { readdirSync, watch } from 'node:fs';
import { join, resolve } from 'node:path';
import { randomUUID } from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';
import { DatabaseSync } from 'node:sqlite';
import { Code, ConnectError } from '@connectrpc/connect';
import Ajv from 'ajv';
import { readJSON, writeJSON } from './files.mjs';
import { ATTENTION_FILE } from './human-attention.mjs';

const name = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/u;
const statuses = new Set(['pending', 'claimed', 'completed', 'cancelled']);

export class HumanService {
  constructor(root) {
    this.root = resolve(root);
    this.jobs = join(this.root, 'jobs');
    this.subscribers = new Set();
    const state = join(this.root, '.asys-human');
    makeDirectory(state, { recursive: true, mode: 0o700 });
    // Only the bridge writes human decisions. An OS-backed transaction releases
    // ownership on process death; requests and decisions themselves are files.
    try {
      prepareFile(join(state, 'owner.sqlite'));
      this.owner = new DatabaseSync(join(state, 'owner.sqlite'));
      this.owner.exec('CREATE TABLE IF NOT EXISTS owner (id INTEGER); BEGIN EXCLUSIVE');
      // Watch only the internal wakeup directory, never arbitrary job workspaces.
      this.watcher = watch(state, { persistent: false }, (_event, filename) => {
        if (filename === null || filename === ATTENTION_FILE) this.notify();
      });
      this.watcher.on('error', error => { this.watchError = error; this.notify(); });
    } catch (error) {
      this.owner?.close();
      throw new Error(`Cannot own human-task queue: ${error.message}`, { cause: error });
    }
  }
  close() {
    this.closed = true;
    this.watcher?.close();
    this.notify();
    this.owner?.close(); this.owner = undefined;
  }
  notify() { for (const wake of this.subscribers) wake(); }
  async *watchAttention(_request, { signal } = {}) {
    let dirty = true, resume;
    const seen = new Map();
    const wake = () => { dirty = true; resume?.(); resume = undefined; };
    // Subscribe before reading the snapshot so publication cannot fall in a gap.
    this.subscribers.add(wake);
    signal?.addEventListener('abort', wake, { once: true });
    try {
      for (;;) {
        signal?.throwIfAborted();
        if (this.closed || this.watchError) fail(this.watchError
          ? `Human attention watcher failed: ${this.watchError.message}`
          : 'Human service stopped', Code.Unavailable);
        if (!dirty) { await new Promise(resolve => { resume = resolve; }); continue; }
        dirty = false;
        const outstanding = new Set();
        for (const id of this.ids()) {
          signal?.throwIfAborted();
          const record = this.read(id, false);
          if (!record || !['pending', 'claimed'].includes(this.view(record).status)) continue;
          outstanding.add(id);
          // A release needs attention again, even if its claim and release both
          // happened while a subscriber was busy processing the previous event.
          const revision = record.state.releasedToken ?? '';
          if (seen.has(id) && seen.get(id) === revision) continue;
          seen.set(id, revision);
          yield { taskId: id };
        }
        for (const id of seen.keys()) if (!outstanding.has(id)) seen.delete(id);
      }
    } finally {
      this.subscribers.delete(wake);
      signal?.removeEventListener('abort', wake);
    }
  }
  ids() {
    let entries;
    try { entries = readdirSync(this.jobs, { withFileTypes: true }); }
    catch (error) { if (error.code !== 'ENOENT') throw error; entries = []; }
    return entries.filter(e => e.isDirectory() && name.test(e.name)).map(e => e.name).sort();
  }
  listTasks({ status = '', afterId = '', limit = 100 } = {}) {
    if (status && !statuses.has(status)) fail('Unknown human-task status', Code.InvalidArgument);
    limit ||= 100;
    if (!Number.isInteger(limit) || limit < 1 || limit > 1000) fail('limit must be from 1 to 1000', Code.InvalidArgument);
    const tasks = [];
    for (const id of this.ids().filter(id => id > afterId)) {
      const record = this.read(id, false);
      if (!record) continue;
      const task = this.view(record);
      if (!status || status === task.status) tasks.push(task);
      if (tasks.length > limit) break;
    }
    const more = tasks.length > limit;
    tasks.length = Math.min(tasks.length, limit);
    return { tasks, nextAfterId: more ? tasks.at(-1).id : '' };
  }
  getTask({ id }) { return { task: this.view(this.read(id)) }; }
  claimTask({ id, claimant, claimId }) {
    text(claimant, 'claimant'); text(claimId, 'claim_id');
    const record = this.read(id);
    const state = record.state;
    this.active(record);
    if (state.status === 'claimed') {
      if (state.claimant === claimant && state.claimId === claimId) return { task: this.view(record), token: state.token };
      fail('Task is already claimed', Code.AlreadyExists);
    }
    if (state.status !== 'pending') fail('Task is already complete', Code.FailedPrecondition);
    if (record.request.input.candidates?.length && !record.request.input.candidates.includes(claimant)) fail('Claimant is not a candidate for this task', Code.PermissionDenied);
    Object.assign(state, { status: 'claimed', claimant, claimId, token: randomUUID() });
    this.save(record);
    return { task: this.view(record), token: state.token };
  }
  releaseTask({ id, token }) {
    const record = this.read(id);
    this.active(record);
    if (record.state.status === 'pending' && record.state.releasedToken === token && token) return { task: this.view(record) };
    this.token(record, token);
    if (record.state.status !== 'claimed') fail('Task is not claimed', Code.FailedPrecondition);
    Object.assign(record.state, { status: 'pending', claimant: '', claimId: '', token: '', releasedToken: token });
    this.save(record);
    return { task: this.view(record) };
  }
  completeTask({ id, token, completionId, resultJson }) {
    text(completionId, 'completion_id');
    let result;
    try { result = JSON.parse(resultJson); } catch { fail('result_json must contain JSON', Code.InvalidArgument); }
    const record = this.read(id);
    this.token(record, token);
    const state = record.state;
    if (state.status === 'completed') {
      if (state.completionId === completionId && isDeepStrictEqual(state.result, result)) return { task: this.view(record) };
      fail('Task already has a different completion', Code.AlreadyExists);
    }
    this.active(record);
    if (state.status !== 'claimed') fail('Task must be claimed before completion', Code.FailedPrecondition);
    if (record.request.input.form !== undefined) {
      const ajv = new Ajv({ strict: true, allErrors: true });
      const validate = ajv.compile(record.request.input.form);
      if (!validate(result)) fail(`Result does not match task form: ${ajv.errorsText(validate.errors)}`, Code.InvalidArgument);
    }
    Object.assign(state, { status: 'completed', completionId, result });
    this.save(record);
    return { task: this.view(record) };
  }
  read(id, required = true) {
    if (!name.test(id)) fail('Invalid task ID', Code.InvalidArgument);
    const submitted = readJSON(join(this.jobs, id, 'request.json'), null);
    if (!submitted) { if (required) fail('Human task not found', Code.NotFound); return null; }
    const execution = resolve(this.root, submitted.directory);
    const workspace = resolve(this.root, submitted.workspace);
    const directory = join(execution, 'human');
    const request = readJSON(join(directory, 'request.json'), null);
    if (!request) { if (required) fail('Human task not found', Code.NotFound); return null; }
    const job = readJSON(join(this.jobs, id, 'state.json'));
    const metadata = submitted.metadata;
    const path = join(directory, 'state.json');
    const state = readJSON(path, { status: 'pending', createdAt: job.submitted_at, updatedAt: job.updated_at });
    return { id, request, job, metadata, execution, workspace, state, path };
  }
  active(record) {
    if (['failed', 'cancelled', 'done', 'interrupted'].includes(record.job.status)) {
      fail('The job is no longer waiting for a decision', Code.FailedPrecondition);
    }
  }
  token(record, token) {
    if (!token || token !== record.state.token) fail('A current claim token is required', Code.PermissionDenied);
  }
  save(record) {
    record.state.updatedAt = new Date().toISOString();
    writeJSON(record.path, record.state);
    this.notify();
  }
  view({ id, request, job, metadata, execution, workspace, state }) {
    const cancelled = ['failed', 'cancelled', 'interrupted'].includes(job.status);
    const files = { directory: execution, workspace, result: join(execution, 'result.json') };
    return { id, status: cancelled ? 'cancelled' : state.status, inputJson: JSON.stringify(request.input),
      resultJson: state.status === 'completed' ? JSON.stringify(state.result) : '', claimant: state.claimant ?? '',
      createdAt: state.createdAt, updatedAt: state.updatedAt, metadataJson: JSON.stringify({ ...metadata, files }) };
  }
}

function fail(message, code) { throw new ConnectError(message, code); }
function text(value, label) { if (typeof value !== 'string' || !value.trim() || value.length > 256) fail(`${label} must be nonempty text of at most 256 characters`, Code.InvalidArgument); }
