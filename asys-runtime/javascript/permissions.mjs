import { chmodSync, closeSync, fchmodSync, mkdirSync, openSync, statSync } from 'node:fs';
import { dirname } from 'node:path';

export function shared(path) { return (statSync(path).mode & 0o2070) === 0o2070; }

export function fileMode(path, mode = 0o600) {
  return shared(dirname(path)) ? mode | ((mode & 0o700) >> 3) : mode;
}

export function prepareFile(path) {
  let fd;
  try { fd = openSync(path, 'wx', fileMode(path)); }
  catch (error) { if (error.code === 'EEXIST') return; throw error; }
  try { fchmodSync(fd, fileMode(path)); } finally { closeSync(fd); }
}

export function makeDirectory(path, { recursive = false, mode = 0o700 } = {}) {
  if (recursive) {
    try { if (statSync(path).isDirectory()) return; }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
    makeDirectory(dirname(path), { recursive: true, mode });
  }
  mode = fileMode(path, mode) | (shared(dirname(path)) ? 0o2000 : 0);
  try { mkdirSync(path, { mode }); }
  catch (error) {
    if (recursive && error.code === 'EEXIST' && statSync(path).isDirectory()) return;
    throw error;
  }
  chmodSync(path, mode);
}
