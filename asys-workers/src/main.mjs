import { parseArgs } from 'node:util';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn, execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { heartbeat } from '../../asys-runtime/javascript/health.mjs';

const runtimeTool = fileURLToPath(new URL('../../asys-runtime/tools/asys-runtime', import.meta.url));

export async function main(argv = process.argv.slice(2), env = process.env) {
  const { values } = parseArgs({ args: argv, options: {
    root: { type: 'string', default: '/var/lib/asys/runtime' },
    environment: { type: 'string', default: '/opt/asys/environment' },
  } });
  const directory = resolve(values.environment);
  const { stdout } = await promisify(execFile)('python3', [runtimeTool, 'describe', directory], { env, maxBuffer: 8 * 1024 * 1024 });
  const descriptor = JSON.parse(stdout);
  const shutdown = new AbortController();
  let stop;
  const stopped = new Promise(resolve => { stop = resolve; });
  const onSignal = () => { shutdown.abort(new Error('Worker component stopping')); stop(); };
  process.once('SIGINT', onSignal);
  process.once('SIGTERM', onSignal);
  let runtime, exited, stopHealth;
  try {
    runtime = spawn('python3', [runtimeTool, 'run', directory, '--root', resolve(values.root)], { env, stdio: ['ignore', 'inherit', 'inherit'] });
    exited = new Promise((resolve, reject) => {
      runtime.once('error', reject);
      runtime.once('exit', (code, signal) => resolve({ code, signal }));
    });
    stopHealth = heartbeat('/tmp/asys-workers-health');
    console.log(`Worker environment ${descriptor.name} ready`);
    await Promise.race([stopped, exited.then(({ code, signal }) => {
      if (!shutdown.signal.aborted) throw new Error(`Job runtime stopped (${signal ?? code})`);
    })]);
  } finally {
    shutdown.abort(new Error('Worker component stopped'));
    stopHealth?.();
    runtime?.kill('SIGTERM');
    await exited?.catch(() => {});
    process.removeListener('SIGINT', onSignal);
    process.removeListener('SIGTERM', onSignal);
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error => { console.error(error.message); process.exitCode = 1; });
}
