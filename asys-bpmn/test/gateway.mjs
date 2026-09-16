// Integration fixture: host events arrive through files; only this component
// calls its typed Workflow and Human dcomp inputs.
import { fromJson, toJson } from '@bufbuild/protobuf';
import { createClient } from '@connectrpc/connect';
import { createConnectTransport } from '@connectrpc/connect-node';
import { inputHttpOptions } from '@dcomp/component';
import { Workflow } from './gen/asys/workflow/v1/workflow_pb.js';
import { Human } from './human_pb.js';
import { heartbeat } from '../asys-runtime/javascript/health.mjs';
import * as runtime from '../asys-runtime/javascript/channel.mjs';
import { serveTestChannel } from './channel-server.mjs';

const bindings = Object.fromEntries([[Workflow, 'workflow'], [Human, 'human']].map(([service, input]) => [input, {
  service, client: createClient(service, createConnectTransport({
    baseUrl: 'http://dcomp', httpVersion: '1.1', nodeOptions: inputHttpOptions(input),
  })),
}]));
let stopHealth;
try {
  await serveTestChannel(runtime, async function* (request, signal) {
    const binding = bindings[request.service];
    const method = binding?.service.methods.find(method => method.name === request.method);
    if (!method) throw new Error('Unknown test method');
    const result = binding.client[method.localName](fromJson(method.input, request.body), { signal });
    if (method.methodKind === 'unary') yield toJson(method.output, await result);
    else for await (const item of result) yield toJson(method.output, item);
  }, { onReady: () => { stopHealth = heartbeat('/tmp/asys-gateway-health'); } });
} finally { stopHealth?.(); }
