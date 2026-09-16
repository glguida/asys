import { readdir } from 'node:fs/promises';
import { resolve, join } from 'node:path';
import { Queue, readJSON } from './queue.mjs';

export function environmentName(value) {
  if (typeof value !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/u.test(value)) {
    throw new Error('Environment name must contain 1–128 letters, numbers, dots, underscores or hyphens, starting with a letter or number');
  }
  return value;
}

// Descriptors are published by workers from workers.json. They declare types,
// not availability or proof that a tool, model, or task will succeed.
export class Environments {
  constructor(root) { this.root = resolve(root); }
  directory(name) { return join(this.root, 'environments', environmentName(name)); }
  queue(name) { return new Queue(this.directory(name)); }
  async get(name) {
    let value;
    try { value = await readJSON(join(this.directory(name), 'environment.json')); }
    catch (error) {
      if (error.code === 'ENOENT') throw Object.assign(new Error(`Environment ${name} is not registered`), { code: 'ENVIRONMENT_NOT_FOUND' });
      throw error;
    }
    if (!value || typeof value !== 'object' || Array.isArray(value) || value.version !== 1 || value.name !== name || typeof value.description !== 'string' ||
      !Array.isArray(value.types) || !value.types.length || new Set(value.types).size !== value.types.length ||
      value.types.some(type => typeof type !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/u.test(type)) ||
      typeof value.definition !== 'string' || !/^[0-9a-f]{64}$/u.test(value.definition)) {
      throw new Error(`Invalid environment descriptor for ${name}`);
    }
    return value;
  }
  async list() {
    let entries;
    try { entries = await readdir(join(this.root, 'environments'), { withFileTypes: true }); }
    catch (error) { if (error.code === 'ENOENT') return []; throw error; }
    const result = [];
    for (const entry of entries.sort((a, b) => a.name.localeCompare(b.name))) {
      if (!entry.isDirectory() || entry.name.startsWith('.')) continue;
      try { result.push(await this.get(entry.name)); }
      catch (error) { if (error.code !== 'ENVIRONMENT_NOT_FOUND') throw error; }
    }
    return result;
  }
}
