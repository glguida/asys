import { createHash } from 'node:crypto';
const RESERVED = new Set(['fields', 'content', 'properties', 'variables', 'output', 'item', 'index', 'message', '_data', '__proto__', 'constructor', 'prototype']);

export function object(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

export function json(value) {
  return JSON.stringify(value ?? null);
}

export function parseJSON(value, label = 'JSON', { record = false } = {}) {
  let parsed;
  try { parsed = JSON.parse(value); } catch { throw new Error(`${label} is not valid JSON`); }
  if (record && !object(parsed)) throw new Error(`${label} must be an object`);
  return parsed;
}

export function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (object(value)) return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`;
  return JSON.stringify(value);
}

export function digest(value) {
  return createHash('sha256').update(typeof value === 'string' ? value : canonical(value)).digest('hex');
}

export function requiredString(value, label) {
  if (typeof value !== 'string' || !value.trim()) throw new Error(`${label} must be a nonempty string`);
  return value;
}

export function safeName(value, label) {
  requiredString(value, label);
  if (!/^[A-Za-z_][A-Za-z0-9_.-]{0,127}$/u.test(value) || ['__proto__', 'constructor', 'prototype'].includes(value)) {
    throw new Error(`${label} is not a valid name`);
  }
  return value;
}

export function variableName(value, label) {
  safeName(value, label);
  if (RESERVED.has(value)) throw new Error(`${label}: ${value} is reserved by the workflow runtime`);
  return value;
}

export function variables(value) {
  if (!object(value)) throw new Error('Workflow variables must be an object');
  for (const name of Object.keys(value)) variableName(name, 'Variable name');
  return value;
}

export function publicVariables(environment) {
  return Object.fromEntries(Object.entries(environment.variables).filter(([key]) => !RESERVED.has(key)));
}

export function clone(value) { return JSON.parse(JSON.stringify(value)); }

export function errorRecord(error) {
  return { message: error?.message ?? String(error), code: error?.code ?? 'WORKFLOW_ERROR' };
}
