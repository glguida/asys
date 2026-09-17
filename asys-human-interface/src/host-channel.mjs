import { DatabaseSync } from 'node:sqlite';
import { join } from 'node:path';
import { create, fromJson, toJson } from '@bufbuild/protobuf';
import { Code, ConnectError } from '@connectrpc/connect';
import { Human, TaskSchema } from '@asys/human-protocol';
import { Reader, Writer, directionRoot } from '../../asys-runtime/javascript/channel.mjs';

const methods = new Map(Human.methods.filter(method => method.methodKind === 'unary' && method.localName !== 'ask').map(method => [method.name, method]));

// The human service owns requests and decisions. Its terminal uses this durable
// host channel; workers call the exported Human interface.
export async function serveHostChannel(service, { root, name = 'human', signal, onReady } = {}) {
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
  const context = () => ({ signal: stopping });

  async function watch() {
    for await (const { taskId } of service.watchAttention({}, context())) {
      const { task } = service.getTask({ id: taskId });
      await send('attention', { worker: JSON.parse(task.metadataJson).component ?? '', task: toJson(TaskSchema, create(TaskSchema, task)) });
    }
  }

  async function request(event) {
    const data = event.data;
    if (event.type !== 'request') throw new ConnectError('Expected a request event', Code.InvalidArgument);
    if (!data || typeof data !== 'object' || Array.isArray(data)) throw new ConnectError('Request data must be an object', Code.InvalidArgument);
    const method = methods.get(data.method);
    if (!method) throw new ConnectError('Unknown Human method (attention is subscribed automatically)', Code.Unimplemented);
    let body;
    try { body = fromJson(method.input, data.body ?? {}); }
    catch (error) { throw new ConnectError(error.message, Code.InvalidArgument); }
    return toJson(method.output, create(method.output, await service[method.localName](body, context())));
  }

  let subscriptions = [];
  try {
    await send('ready', {});
    onReady?.();
    const watching = watch();
    void watching.catch(error => { if (!stopping.aborted) failed.abort(error); });
    subscriptions = [watching];
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
