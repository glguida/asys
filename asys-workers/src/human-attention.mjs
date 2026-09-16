import { makeDirectory } from '../../asys-runtime/javascript/permissions.mjs';
import { join } from 'node:path';
import { writeJSON } from './files.mjs';

export const ATTENTION_FILE = 'attention.json';

// An internal wakeup, not the request store. Concurrent writers may coalesce:
// subscribers always examine the durable requests after being notified.
export function notifyHuman(root) {
  const directory = join(root, '.asys-human');
  makeDirectory(directory, { recursive: true, mode: 0o700 });
  writeJSON(join(directory, ATTENTION_FILE), { version: 1 });
}
