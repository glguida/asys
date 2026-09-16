import { createServer } from 'node:http';
import { connectNodeAdapter } from '@connectrpc/connect-node';
import { Workflow } from '../gen/asys/workflow/v1/workflow_pb.js';

export function workflowServer(runtime, { signal } = {}) {
  const implementation = Object.fromEntries(Workflow.methods.map(method => [method.localName, runtime[method.localName].bind(runtime)]));
  const adapter = connectNodeAdapter({
    connect: true, grpc: false, grpcWeb: false, shutdownSignal: signal,
    readMaxBytes: 12 * 1024 * 1024, writeMaxBytes: 32 * 1024 * 1024,
    routes(router) { router.service(Workflow, implementation); },
  });
  const server = createServer(adapter);
  // Track reverse-dialled connections for bounded shutdown.
  const sockets = new Set();
  server.on('connection', socket => {
    sockets.add(socket);
    socket.once('close', () => sockets.delete(socket));
  });
  server.closeConnections = () => { for (const socket of sockets) socket.destroy(); };
  return server;
}
