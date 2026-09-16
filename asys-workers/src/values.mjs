import { createHash } from 'node:crypto';

export function clone(value) { return JSON.parse(JSON.stringify(value)); }
export function digest(value) { return createHash('sha256').update(typeof value === 'string' ? value : JSON.stringify(value)).digest('hex'); }
export function requiredString(value, label) {
  if (typeof value !== 'string' || !value.trim()) throw new Error(`${label} must be a nonempty string`);
  return value;
}
