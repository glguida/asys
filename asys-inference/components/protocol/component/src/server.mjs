import { createServer } from "node:http";

import { connectNodeAdapter } from "@connectrpc/connect-node";
import { serveOutput } from "@dcomp/component";

import { registerProvides } from "./bindings.mjs";

const closePromises = new WeakMap();
const activeResponses = new WeakMap();

export function createComponentServer({ bindings, implementations, shutdownSignal } = {}) {
  if (!bindings?.provides || !bindings?.requires) {
    throw new TypeError("resolved component bindings are required");
  }
  const server = createServer(
    connectNodeAdapter({
      connect: true,
      grpc: false,
      grpcWeb: false,
      shutdownSignal,
      routes(router) {
        registerProvides(router, bindings, implementations);
      },
    }),
  );
  const responses = new Set();
  activeResponses.set(server, responses);
  server.on("request", (_request, response) => {
    responses.add(response);
    const finished = () => {
      responses.delete(response);
      response.off("finish", finished);
      response.off("close", finished);
    };
    response.once("finish", finished);
    response.once("close", finished);
  });
  return server;
}

/** Serve a private ConnectRPC interface through one DComp 0.2 output socket. */
export function serveComponentOutput(
  server,
  name,
  { signal, env = process.env, retryDelayMs } = {},
) {
  return serveOutput(server, name, {
    signal,
    env,
    ...(retryDelayMs === undefined ? {} : { retryDelayMs }),
  });
}

export function closeComponentServer(server) {
  const existing = closePromises.get(server);
  if (existing) return existing;

  const closing = closeNodeServer(server).finally(() => {
    closePromises.delete(server);
  });
  closePromises.set(server, closing);
  return closing;
}

async function closeNodeServer(server) {
  if (!server.listening) {
    await Promise.all(
      [...(activeResponses.get(server) ?? [])].map(waitForResponse),
    );
    server.closeAllConnections?.();
    return;
  }
  const closed = new Promise((resolve, reject) => {
    server.close((error) => (error ? reject(error) : resolve()));
  });
  server.closeIdleConnections?.();
  await closed;
}

function waitForResponse(response) {
  if (response.writableFinished || response.destroyed) return Promise.resolve();
  return new Promise((resolve) => {
    const finished = () => {
      response.off("finish", finished);
      response.off("close", finished);
      resolve();
    };
    response.once("finish", finished);
    response.once("close", finished);
  });
}
