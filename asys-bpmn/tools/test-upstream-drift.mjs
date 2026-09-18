import { cp, mkdtemp, readFile, rm, symlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';

const source = fileURLToPath(new URL('..', import.meta.url));
const [elements = 'latest', serializer = 'latest'] = process.argv.slice(2);
const root = await mkdtemp(join(tmpdir(), 'asys-bpmn-drift-'));
const project = join(root, 'asys-bpmn');

// Install into an isolated copy. The working lockfile and node_modules remain
// untouched even when an install, test, or interruption fails the check.
try {
  for (const name of ['package.json', 'package-lock.json', '.npmrc', 'src', 'test', 'gen']) {
    await cp(join(source, name), join(project, name), { recursive: true });
  }
  for (const name of ['asys-runtime', 'asys-workers', 'asys-inference']) {
    await symlink(join(source, '..', name), join(root, name), 'dir');
  }
  await run('npm', ['ci', '--omit=dev', '--ignore-scripts', '--no-audit', '--no-fund']);
  await run('npm', ['install', '--no-save', '--package-lock=false', '--omit=dev', '--ignore-scripts', '--no-audit', '--no-fund',
    `bpmn-elements@${elements}`, `moddle-context-serializer@${serializer}`]);
  for (const name of ['bpmn-elements', 'moddle-context-serializer']) {
    const { version } = JSON.parse(await readFile(join(project, 'node_modules', name, 'package.json'), 'utf8'));
    console.log(`Testing ${name}@${version}`);
  }
  await run(process.execPath, ['--test', '--test-concurrency=1',
    'test/engine-contract.test.mjs', 'test/engine.test.mjs', 'test/workflow.test.mjs']);
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
} finally {
  await rm(root, { recursive: true, force: true });
}

function run(command, args) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { cwd: project, stdio: 'inherit' });
    child.once('error', reject);
    child.once('exit', (code, signal) => {
      if (code === 0) resolve();
      else reject(new Error(`${command} failed (${signal ?? code})`));
    });
  });
}
