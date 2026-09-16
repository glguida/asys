import { parseArgs } from 'node:util';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { humanClient } from './client.mjs';
import { serveHostChannel } from './host-channel.mjs';
import { readJSON } from '../../asys-runtime/javascript/queue.mjs';
import { heartbeat } from '../../asys-runtime/javascript/health.mjs';

export async function main(argv = process.argv.slice(2), env = process.env) {
  const { values } = parseArgs({ args: argv, options: {
    root: { type: 'string', default: '/var/lib/asys-human' },
    'host-channel': { type: 'string', default: 'human' },
    config: { type: 'string' },
  } });
  const config = values.config ? await readJSON(values.config) : { workers: [{ id: 'human', input: 'human' }] };
  if (!Array.isArray(config.workers)) throw new Error('Configuration must contain a workers array');
  const workers = new Map(), inputs = new Set();
  for (const binding of config.workers) {
    if (!binding || typeof binding.id !== 'string' || !binding.id || workers.has(binding.id) || inputs.has(binding.input)) {
      throw new Error('Each worker must have a unique id and input');
    }
    workers.set(binding.id, humanClient(binding.input, env));
    inputs.add(binding.input);
  }
  const shutdown = new AbortController();
  const stop = () => shutdown.abort();
  process.once('SIGINT', stop);
  process.once('SIGTERM', stop);
  let stopHealth;
  try {
    await serveHostChannel(workers, { root: values.root, name: values['host-channel'], signal: shutdown.signal,
      onReady() {
        stopHealth = heartbeat('/tmp/asys-human-interface-health');
        console.log(`Human interface ready (${workers.size} workers)`);
      },
    });
  } finally {
    shutdown.abort();
    stopHealth?.();
    process.removeListener('SIGINT', stop);
    process.removeListener('SIGTERM', stop);
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error => { console.error(error.message); process.exitCode = 1; });
}
