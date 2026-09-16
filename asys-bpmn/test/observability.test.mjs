import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

test('BPMN launcher progress preserves stages, failures and provider notices', async () => {
  await promisify(execFile)('python3', [fileURLToPath(new URL('./journal.py', import.meta.url))]);
});
