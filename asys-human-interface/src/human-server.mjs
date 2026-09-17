import { createServer } from 'node:http';
import { connectNodeAdapter } from '@connectrpc/connect-node';
import { Human } from '@asys/human-protocol';

export function humanServer(service, { signal } = {}) {
  const implementation = Object.fromEntries(Human.methods.map(method => [method.localName, service[method.localName].bind(service)]));
  const server = createServer(connectNodeAdapter({
    connect: true, grpc: false, grpcWeb: false, shutdownSignal: signal,
    readMaxBytes: 8 * 1024 * 1024, writeMaxBytes: 32 * 1024 * 1024,
    routes(router) { router.service(Human, implementation); },
  }));
  const sockets = new Set();
  server.on('connection', socket => {
    sockets.add(socket);
    socket.once('close', () => sockets.delete(socket));
  });
  server.closeConnections = () => { for (const socket of sockets) socket.destroy(); };
  return server;
}
