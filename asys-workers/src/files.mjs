import { fileMode } from '../../asys-runtime/javascript/permissions.mjs';
import { fchmodSync, closeSync, fsyncSync, openSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import { randomUUID } from 'node:crypto';

export function readJSON(path, fallback) {
  let data;
  try { data = readFileSync(path); }
  catch (error) { if (error.code === 'ENOENT' && arguments.length > 1) return fallback; throw error; }
  return JSON.parse(data.toString('utf8'));
}

export function writeJSON(path, value) {
  const text = JSON.stringify(value) + '\n';
  const temporary = `${path}.tmp.${randomUUID()}`;
  try {
    const file = openSync(temporary, 'wx', 0o600);
    try { fchmodSync(file, fileMode(path)); writeFileSync(file, text); fsyncSync(file); } finally { closeSync(file); }
    renameSync(temporary, path);
    const directory = openSync(dirname(path), 'r');
    try { fsyncSync(directory); } finally { closeSync(directory); }
  } finally { rmSync(temporary, { force: true }); }
}
