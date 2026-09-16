// A test component owns the Provider input and reports through a host channel.
import { stat } from 'node:fs/promises';
import { fromJson, toJson } from '@bufbuild/protobuf';
import { createClient } from '@connectrpc/connect';
import { inputPath } from '../../vendor/dcomp-component/src/index.mjs';
import { createDCompTransport } from '@cyclo/component/transport';
import { Provider } from '@cyclo/provider/contract';
import * as runtime from '../../vendor/asys-runtime/channel.mjs';
import { serveTestChannel } from './channel-server.mjs';

const client = createClient(Provider, createDCompTransport('provider'));
await serveTestChannel(runtime, async function* (request, signal) {
  if (request.method === 'InputIdentity') {
    yield { inode: String((await stat(inputPath('provider'))).ino) };
    return;
  }
  const method = Provider.methods.find(method => method.name === request.method);
  if (!method) throw new Error('Unknown Provider method');
  const result = client[method.localName](fromJson(method.input, request.body), { signal, timeoutMs: 5000 });
  if (method.methodKind === 'unary') yield toJson(method.output, await result);
  else for await (const item of result) yield toJson(method.output, item);
});
