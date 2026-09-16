import { DatabaseSync } from 'node:sqlite';
import { join } from 'node:path';
import { setTimeout } from 'node:timers/promises';
import { fromJson, toJson } from '@bufbuild/protobuf';
import { Code, ConnectError } from '@connectrpc/connect';
import { Human, TaskSchema } from '@asys/human-protocol';
import { Reader, Writer, directionRoot } from '../../asys-runtime/javascript/channel.mjs';

const methods = new Map(Human.methods.filter(method => method.methodKind === 'unary').map(method => [method.name, method]));

// Workers own tasks, claims and decisions. This component owns only transport:
// one subscription per Human input and one durable channel for the host.
export async function serveHostChannel(workers, { root, name = 'human', signal, retryMs = 250, timeoutMs = 5000, onReady } = {}) {
  const input = await new Reader(directionRoot(root, name, 'in')).ready();
  const output = await new Writer(directionRoot(root, name, 'out')).ready();
  const owner = new DatabaseSync(join(input.directory, '..', '.owner.sqlite'));
  try { owner.exec('CREATE TABLE IF NOT EXISTS owner (id INTEGER); BEGIN EXCLUSIVE'); }
  catch (error) { owner.close(); throw new Error(`Human channel already has an owner: ${error.message}`, { cause: error }); }
  const failed = new AbortController();
  const stopped = new AbortController();
  const stopping = AbortSignal.any([failed.signal, stopped.signal, ...(signal ? [signal] : [])]);
  const send = (type, data) => {
    const pending = output.send(type, data);
    void pending.catch(error => failed.abort(error));
    return pending;
  };
  const context = () => ({ signal: stopping, timeoutMs });

  async function watch(worker, client) {
    let unavailable = false, delay = retryMs;
    while (!stopping.aborted) {
      try {
        for await (const { taskId } of client.watchAttention({}, { signal: stopping })) {
          let task;
          try { ({ task } = await client.getTask({ id: taskId }, context())); }
          catch (error) { if (error.code === Code.NotFound) continue; throw error; }
          if (unavailable) await send('worker.available', { worker });
          unavailable = false; delay = retryMs;
          if (task && ['pending', 'claimed'].includes(task.status)) {
            await send('attention', { worker, task: toJson(TaskSchema, task) });
          }
        }
        if (!stopping.aborted) throw new ConnectError('Human subscription ended', Code.Unavailable);
      } catch (error) {
        if (stopping.aborted) return;
        if (!unavailable) await send('worker.unavailable', { worker, message: error.message });
        unavailable = true;
        await setTimeout(delay, undefined, { signal: stopping });
        delay = Math.min(delay * 2, 5000);
      }
    }
  }

  async function request(event) {
    const data = event.data;
    if (event.type !== 'request') throw new ConnectError('Expected a request event', Code.InvalidArgument);
    if (!data || typeof data !== 'object' || Array.isArray(data)) throw new ConnectError('Request data must be an object', Code.InvalidArgument);
    const client = workers.get(data.worker);
    if (!client) throw new ConnectError('Unknown worker Human interface', Code.NotFound);
    const method = methods.get(data.method);
    if (!method) throw new ConnectError('Unknown Human method (attention is subscribed automatically)', Code.Unimplemented);
    let body;
    try { body = fromJson(method.input, data.body ?? {}); }
    catch (error) { throw new ConnectError(error.message, Code.InvalidArgument); }
    return toJson(method.output, await client[method.localName](body, context()));
  }

  let subscriptions = [];
  try {
    await send('ready', { workers: [...workers.keys()] });
    onReady?.();
    subscriptions = [...workers].map(([worker, client]) => {
      const running = watch(worker, client);
      void running.catch(error => { if (!stopping.aborted) failed.abort(error); });
      return running;
    });
    for await (const event of input.follow(undefined, { signal: stopping })) {
      let reply;
      try { reply = { type: 'result', data: { request: event.sequence, result: await request(event) } }; }
      catch (error) {
        stopping.throwIfAborted(); // Leave an interrupted operation for replay.
        reply = { type: 'error', data: { request: event.sequence,
          code: error instanceof ConnectError ? Code[error.code] : 'Unknown', message: error.message } };
      }
      // A crash between reply and acknowledgement replays the same operation.
      // The host supplies stable claimId/completionId values for that reason.
      await send(reply.type, reply.data);
      await input.advance(event.sequence);
      await input.prune();
    }
  } catch (error) {
    if (failed.signal.aborted) throw failed.signal.reason;
    if (!signal?.aborted) throw error;
  } finally {
    stopped.abort();
    await Promise.allSettled(subscriptions);
    await output.pending;
    owner.close();
  }
}
