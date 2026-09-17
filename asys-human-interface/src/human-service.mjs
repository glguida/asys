import { makeDirectory, prepareFile } from '../../asys-runtime/javascript/permissions.mjs';
import { join, resolve } from 'node:path';
import { randomUUID } from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';
import { DatabaseSync } from 'node:sqlite';
import { Code, ConnectError } from '@connectrpc/connect';
import Ajv from 'ajv';

const name = /^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$/u;
const statuses = new Set(['pending', 'claimed', 'completed', 'cancelled']);

export class HumanService {
  constructor(root) {
    this.root = resolve(root);
    makeDirectory(this.root, { recursive: true, mode: 0o700 });
    const path = join(this.root, 'tasks.sqlite');
    prepareFile(path);
    this.database = new DatabaseSync(path);
    this.database.exec('CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, record TEXT NOT NULL)');
    this.subscribers = new Set();
    this.waiters = new Map();
    // In-flight RPCs do not survive a service restart. Keep their history, but
    // do not offer questions whose worker is no longer waiting for an answer.
    for (const id of this.ids()) {
      const record = this.read(id);
      if (['pending', 'claimed'].includes(record.state.status)) {
        record.state.status = 'cancelled';
        this.save(record);
      }
    }
  }
  close() {
    if (this.closed) return;
    this.closed = true;
    this.notify();
    this.database.close();
  }
  async ask({ id, inputJson, metadataJson = '{}' }, { signal } = {}) {
    if (!name.test(id)) fail('Invalid task ID', Code.InvalidArgument);
    let input, metadata;
    try {
      input = JSON.parse(inputJson);
      metadata = JSON.parse(metadataJson || '{}');
      if (!input || typeof input !== 'object' || Array.isArray(input)) throw new Error('Human-task input must be an object');
      if (typeof input.prompt !== 'string' || !input.prompt.trim()) throw new Error('Human-task prompt must be nonempty text');
      if (input.title !== undefined && typeof input.title !== 'string') throw new Error('Human-task title must be text');
      if (input.candidates !== undefined && (!Array.isArray(input.candidates) || input.candidates.some(v => typeof v !== 'string' || !v))) throw new Error('Candidates must be a list of names');
      if (input.form !== undefined) new Ajv({ strict: true }).compile(input.form);
      if (!metadata || typeof metadata !== 'object' || Array.isArray(metadata)) throw new Error('Human metadata must be an object');
    } catch (error) { fail(error.message, Code.InvalidArgument); }
    let record = this.read(id, false);
    const request = { input, metadata };
    if (record && !isDeepStrictEqual(record.request, request)) fail('Task ID already has different input', Code.AlreadyExists);
    if (!record) {
      record = { id, request, state: { status: 'pending', createdAt: new Date().toISOString() } };
      this.save(record);
    }
    let wake;
    const changed = () => wake?.();
    this.subscribers.add(changed);
    signal?.addEventListener('abort', changed, { once: true });
    this.waiters.set(id, (this.waiters.get(id) ?? 0) + 1);
    try {
      for (;;) {
        signal?.throwIfAborted();
        if (this.closed) fail('Human service stopped', Code.Unavailable);
        record = this.read(id);
        if (record.state.status === 'completed') return { resultJson: JSON.stringify(record.state.result) };
        if (record.state.status === 'cancelled') fail('Human request was cancelled', Code.FailedPrecondition);
        await new Promise(resolve => { wake = resolve; });
      }
    } finally {
      this.subscribers.delete(changed);
      signal?.removeEventListener('abort', changed);
      const remaining = this.waiters.get(id) - 1;
      if (remaining) this.waiters.set(id, remaining);
      else {
        this.waiters.delete(id);
        if (!this.closed) {
          record = this.read(id);
          if (['pending', 'claimed'].includes(record.state.status)) {
            record.state.status = 'cancelled';
            this.save(record);
          }
        }
      }
    }
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
        if (this.closed) fail('Human service stopped', Code.Unavailable);
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
  ids() { return this.database.prepare('SELECT id FROM tasks ORDER BY id').all().map(row => row.id); }
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
    const row = this.database.prepare('SELECT record FROM tasks WHERE id = ?').get(id);
    if (!row) { if (required) fail('Human task not found', Code.NotFound); return null; }
    return JSON.parse(row.record);
  }
  active(record) {
    if (record.state.status === 'cancelled') fail('The job is no longer waiting for a decision', Code.FailedPrecondition);
  }
  token(record, token) {
    if (!token || token !== record.state.token) fail('A current claim token is required', Code.PermissionDenied);
  }
  save(record) {
    record.state.updatedAt = new Date().toISOString();
    this.database.prepare('INSERT INTO tasks (id, record) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET record = excluded.record').run(record.id, JSON.stringify(record));
    this.notify();
  }
  view({ id, request, state }) {
    return { id, status: state.status, inputJson: JSON.stringify(request.input),
      resultJson: state.status === 'completed' ? JSON.stringify(state.result) : '', claimant: state.claimant ?? '',
      createdAt: state.createdAt, updatedAt: state.updatedAt, metadataJson: JSON.stringify(request.metadata) };
  }
}

function fail(message, code) { throw new ConnectError(message, code); }
function text(value, label) { if (typeof value !== 'string' || !value.trim() || value.length > 256) fail(`${label} must be nonempty text of at most 256 characters`, Code.InvalidArgument); }
