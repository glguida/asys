import { parseArgs } from 'node:util';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { serveOutput } from '@dcomp/component';
import { HumanService } from './human-service.mjs';
import { humanServer } from './human-server.mjs';
import { serveHostChannel } from './host-channel.mjs';
import { heartbeat } from '../../asys-runtime/javascript/health.mjs';

export async function main(argv = process.argv.slice(2), env = process.env) {
  const { values } = parseArgs({ args: argv, options: {
    root: { type: 'string', default: '/var/lib/asys-human' },
    'host-channel': { type: 'string', default: 'human' },
  } });
  const shutdown = new AbortController();
  const stop = () => shutdown.abort();
  process.once('SIGINT', stop);
  process.once('SIGTERM', stop);
  const service = new HumanService(values.root);
  const server = humanServer(service, { signal: shutdown.signal });
  const failure = new Promise((_, reject) => server.once('error', reject));
  let stopHealth;
  const output = serveOutput(server, 'human', { env, signal: shutdown.signal });
  const channel = serveHostChannel(service, { root: values.root, name: values['host-channel'], signal: shutdown.signal,
    onReady() {
      stopHealth = heartbeat('/tmp/asys-human-interface-health');
      console.log('Human service ready');
    },
  });
  try {
    await Promise.race([failure, channel, output]);
  } finally {
    shutdown.abort();
    stopHealth?.();
    server.closeConnections();
    await Promise.allSettled([channel, output]);
    service.close();
    process.removeListener('SIGINT', stop);
    process.removeListener('SIGTERM', stop);
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error => { console.error(error.message); process.exitCode = 1; });
}
