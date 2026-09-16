import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { Reader, Writer, directionRoot } from '../../../vendor/asys-runtime/channel.mjs';
import { providerCatalogueDocument } from './http.mjs';
import { isProviderPrefix } from './protocol.mjs';

// Optional for standalone dcomp deployments. Asys supplies a logical provider
// name independently of the physical dcomp instance name.
export function providerIdentity(root) {
  let contents;
  try { contents = readFileSync(join(root, 'provider.json'), 'utf8'); }
  catch (error) { if (error.code === 'ENOENT') return; throw error; }
  const identity = JSON.parse(contents);
  if (identity?.version !== 1 || !isProviderPrefix(identity.name)) throw new TypeError('Invalid asys provider identity');
  return identity;
}

// A host request calls the same ListModels implementation as the Provider
// output. Inter-component calls remain typed dcomp RPC.
export async function serveProviderChannel(provider, root, { signal, onReady } = {}) {
  const input = await new Reader(directionRoot(root, 'provider', 'in')).ready();
  const output = await new Writer(directionRoot(root, 'provider', 'out')).ready();
  const instance = randomUUID();
  const send = (type, data) => output.send(type, { ...data, instance });
  await send('ready', {});
  onReady?.();
  try {
    for await (const event of input.follow(undefined, { signal })) {
      const request = event.sequence;
      let result, problem;
      if (event.data?.instance !== instance) problem = 'Provider restarted; retry the command';
      else if (event.type !== 'models') problem = 'Unknown provider channel request';
      else {
        const timeout = AbortSignal.timeout(10_000);
        const callSignal = AbortSignal.any([timeout, ...(signal ? [signal] : [])]);
        try { result = providerCatalogueDocument(await provider.listModels({}, { signal: callSignal })); }
        catch (error) { problem = timeout.aborted ? 'Provider catalogue timed out' : (error.rawMessage ?? error.message ?? 'Provider catalogue failed'); }
      }
      await send(problem ? 'error' : 'result', problem ? { request, message: problem } : { request, result });
      await input.advance(request);
      await input.prune();
    }
  } catch (error) {
    if (!signal?.aborted) throw error;
  }
}

export async function unexpectedProviderChannelStop(running, signal) {
  await running;
  if (!signal.aborted) throw new Error('Provider host channel stopped');
}
