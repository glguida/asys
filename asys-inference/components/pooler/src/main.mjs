#!/usr/bin/env node

import { realpathSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { Code, ConnectError } from "@connectrpc/connect";
import { HealthStatus } from "@cyclo/component/contract";
import {
  closeComponentServer,
  serveComponentOutput,
} from "@cyclo/component/server";
import {
  closeHostAPIServer,
  listenHostAPIServer,
  serverFailure,
} from "@cyclo/component/http";
import { createProviderHTTPServer } from "@cyclo/provider/http";
import { providerIdentity, serveProviderChannel, unexpectedProviderChannelStop } from "@cyclo/provider/host-channel";

import { parseArguments, parseComponentName } from "./config.mjs";
import { createPoolerServer } from "./server.mjs";
import { createPoolerServices } from "./services.mjs";
import { createUpstreamBinding } from "./upstream.mjs";

export async function runPooler({
  argv = process.argv.slice(2),
  env = process.env,
  signalSource = process,
  createUpstream = createUpstreamBinding,
  apiListenOptions,
  onListening,
  createHTTPServer = createProviderHTTPServer,
  serveOutput = serveComponentOutput,
  hostRoot = "/run/asys-host",
  serveChannel = serveProviderChannel,
} = {}) {
  const config = parseArguments(argv);
  const identity = providerIdentity(hostRoot);
  const componentName = parseComponentName(identity?.name ?? env.DCOMP_COMPONENT_NAME);
  const shutdown = new AbortController();
  let resolveSignal;
  const signaled = new Promise((resolve) => { resolveSignal = resolve; });
  const onSignal = () => {
    if (!shutdown.signal.aborted) {
      shutdown.abort(new ConnectError("pooler is shutting down", Code.Unavailable));
    }
    resolveSignal();
  };
  signalSource.once("SIGTERM", onSignal);
  signalSource.once("SIGINT", onSignal);

  let server;
  let httpServer;
  let outputLoop;
  let channelLoop;
  let channelReady = !identity;
  try {
    const upstream = await createUpstream({ env });
    if (shutdown.signal.aborted) return;
    const services = createPoolerServices({ upstream, config, componentName });
    const component = { ...services.component, health: (...args) => channelReady ? services.component.health(...args)
      : { status: HealthStatus.NOT_READY, message: "provider channel starting" } };
    server = await createPoolerServer({
      services: { ...services, component },
      shutdownSignal: shutdown.signal,
    });
    httpServer = createHTTPServer({
      component,
      provider: services.provider,
      shutdownSignal: shutdown.signal,
    });
    const componentFailure = serverFailure(server);
    const httpFailure = serverFailure(httpServer);
    outputLoop = serveOutput(server, "provider", {
      signal: shutdown.signal,
      env,
    });
    const outputFailure = unexpectedOutputStop(outputLoop, shutdown.signal);
    const failures = [componentFailure, httpFailure, outputFailure];
    if (identity) {
      channelLoop = serveChannel(services.provider, hostRoot, { signal: shutdown.signal, onReady: () => { channelReady = true; } });
      failures.push(unexpectedProviderChannelStop(channelLoop, shutdown.signal));
    }
    for (const failure of failures) {
      void failure.catch(() => {});
    }
    const address = await listenHostAPIServer(httpServer, apiListenOptions);
    onListening?.(address);
    await Promise.race([
      signaled,
      ...failures,
    ]);
  } finally {
    signalSource.removeListener("SIGTERM", onSignal);
    signalSource.removeListener("SIGINT", onSignal);
    if (!shutdown.signal.aborted) {
      shutdown.abort(new ConnectError("pooler stopped", Code.Unavailable));
    }
    await Promise.all([
      server ? closeComponentServer(server) : Promise.resolve(),
      httpServer ? closeHostAPIServer(httpServer) : Promise.resolve(),
      outputLoop ? outputLoop.catch(() => {}) : Promise.resolve(),
      channelLoop ? channelLoop.catch(() => {}) : Promise.resolve(),
    ]);
  }
}

export async function main(argv = process.argv.slice(2)) {
  try {
    await runPooler({ argv });
  } catch (error) {
    if (error instanceof TypeError) {
      throw new Error(
        "usage: cyclo-pooler-component PROVIDER PROVIDER...\n"
        + "   or: cyclo-pooler-component MEMBER_MODEL MEMBER_MODEL... "
        + "model=OUTPUT_MODEL\n"
        + error.message,
      );
    }
    throw error;
  }
}

async function unexpectedOutputStop(outputLoop, signal) {
  await outputLoop;
  if (!signal.aborted) throw new Error("pooler Provider output stopped");
}

function isMain() {
  if (!process.argv[1]) return false;
  try {
    return realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url));
  } catch {
    return false;
  }
}

if (isMain()) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  });
}
