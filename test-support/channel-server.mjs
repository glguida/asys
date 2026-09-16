// Test harness only: the host sends requests through a runtime channel; the
// fixture's handler exercises its own typed dcomp inputs inside the container.
export async function serveTestChannel(runtime, handle, { root = '/run/asys-test', onReady } = {}) {
  const { Reader, Writer, directionRoot } = runtime;
  const input = new Reader(directionRoot(root, 'test', 'in'));
  const output = new Writer(directionRoot(root, 'test', 'out'));
  const stopped = new AbortController();
  const failed = new AbortController();
  const signal = AbortSignal.any([stopped.signal, failed.signal]);
  const active = new Map();
  const stop = () => stopped.abort();
  process.once('SIGINT', stop);
  process.once('SIGTERM', stop);
  const send = (type, data) => {
    const pending = output.send(type, data);
    void pending.catch(error => failed.abort(error));
    return pending;
  };
  async function call(request, controller) {
    const callSignal = AbortSignal.any([signal, controller.signal]);
    try {
      for await (const value of handle(request, callSignal)) {
        await send('item', { id: request.id, value });
      }
    } catch (error) {
      if (!callSignal.aborted) await send('error', { id: request.id, message: error.message, code: error.code });
    } finally {
      await send('end', { id: request.id, cancelled: controller.signal.aborted });
      active.delete(request.id);
    }
  }
  try {
    await input.ready();
    await send('ready', {});
    onReady?.();
    for await (const event of input.follow(undefined, { signal })) {
      const request = event.data;
      if (event.type === 'cancel') active.get(request.id)?.controller.abort();
      if (event.type === 'call') {
        const controller = new AbortController();
        const pending = call(request, controller);
        active.set(request.id, { controller, pending });
        void pending.catch(error => failed.abort(error));
      }
      await input.advance(event.sequence);
      await input.prune();
    }
  } catch (error) {
    if (failed.signal.aborted) throw failed.signal.reason;
    if (!stopped.signal.aborted) throw error;
  } finally {
    stopped.abort();
    await Promise.allSettled([...active.values()].map(call => call.pending));
    process.removeListener('SIGINT', stop);
    process.removeListener('SIGTERM', stop);
  }
}
