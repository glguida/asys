import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';
import { delimiter, join } from 'node:path';
import test from 'node:test';

test('host queue, prompt formats, TUI, and lifecycle checks', async () => {
  const root = fileURLToPath(new URL('..', import.meta.url));
  await promisify(execFile)('python3', ['-m', 'unittest', 'discover', '-s', 'test', '-p', '*.py', '-v'], {
    cwd: root,
    env: { ...process.env, PYTHONPATH: [join(root, '.host-deps'), process.env.PYTHONPATH].filter(Boolean).join(delimiter) },
  });
});
