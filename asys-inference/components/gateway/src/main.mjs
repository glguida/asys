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

import { aggregateUsageFile } from "./audit.mjs";
import { formatSupportedProviders } from "./providers.mjs";
import { createGatewayHTTPServer } from "./http.mjs";
import { createGatewayServer } from "./server.mjs";
import { serveGatewayChannel } from "./host-channel.mjs";

const DEFAULT_USAGE_PATH = "/var/lib/cyclo-gateway/usage.jsonl";

export async function runGateway({
  services,
  createServices = loadDefaultServices,
  env = process.env,
  signalSource = process,
  apiListenOptions,
  onListening,
  createHTTPServer = createGatewayHTTPServer,
  serveOutput = serveComponentOutput,
  hostRoot = "/run/asys-host",
  serveChannel = serveGatewayChannel,
} = {}) {
  const shutdown = new AbortController();
  let notifySignal;
  const signaled = new Promise((resolve) => {
    notifySignal = resolve;
  });
  const onSignal = (name) => {
    if (!shutdown.signal.aborted) {
      shutdown.abort(new ConnectError("gateway is shutting down", Code.Unavailable));
    }
    notifySignal(name);
  };
  const onSigterm = () => onSignal("SIGTERM");
  const onSigint = () => onSignal("SIGINT");
  signalSource.once("SIGTERM", onSigterm);
  signalSource.once("SIGINT", onSigint);

  let server;
  let httpServer;
  let outputLoop;
  let channelLoop;
  let channelReady = false;
  try {
    const resolvedServices = services
      ?? await createServices({ env, signal: shutdown.signal });
    if (shutdown.signal.aborted) return;

    server = await createGatewayServer({
      services: resolvedServices,
      shutdownSignal: shutdown.signal,
    });
    httpServer = createHTTPServer({
      services: {
        ...resolvedServices,
        component: {
          health: (...args) => channelReady ? resolvedServices.component.health(...args)
            : { status: HealthStatus.NOT_READY, message: "gateway channel starting" },
        },
      },
      shutdownSignal: shutdown.signal,
      healthOnly: true,
    });
    const componentFailure = serverFailure(server);
    const httpFailure = serverFailure(httpServer);
    outputLoop = serveOutput(server, "provider", {
      signal: shutdown.signal,
      env,
    });
    const outputFailure = unexpectedOutputStop(outputLoop, shutdown.signal);
    channelLoop = serveChannel(resolvedServices, hostRoot, { signal: shutdown.signal, onReady: () => { channelReady = true; } });
    const channelFailure = unexpectedChannelStop(channelLoop, shutdown.signal);
    for (const failure of [componentFailure, httpFailure, outputFailure, channelFailure]) {
      void failure.catch(() => {});
    }
    const address = await listenHostAPIServer(httpServer, { host: "127.0.0.1", ...apiListenOptions });
    onListening?.(address);

    await Promise.race([
      signaled,
      outputFailure,
      componentFailure,
      httpFailure,
      channelFailure,
    ]);
  } finally {
    signalSource.removeListener("SIGTERM", onSigterm);
    signalSource.removeListener("SIGINT", onSigint);
    if (!shutdown.signal.aborted) {
      shutdown.abort(new ConnectError("gateway stopped", Code.Unavailable));
    }
    await Promise.all([
      server ? closeComponentServer(server) : Promise.resolve(),
      httpServer ? closeHostAPIServer(httpServer) : Promise.resolve(),
      outputLoop ? outputLoop.catch(() => {}) : Promise.resolve(),
      channelLoop ? channelLoop.catch(() => {}) : Promise.resolve(),
    ]);
  }
}

export async function main(argv = process.argv.slice(2), options = {}) {
  const env = options.env ?? process.env;
  const input = options.input ?? process.stdin;
  const output = options.output ?? process.stdout;
  const command = argv[0];
  if (command === undefined) {
    await (options.runGateway ?? runGateway)({ env });
    return;
  }
  if (command === "login" && argv.length >= 2) {
    const loginCommand = options.login
      ?? (await import("./login.mjs")).login;
    await loginCommand(argv.slice(1), { env, input, output });
    return;
  }
  if (command === "logout" && argv.length === 2) {
    const logoutCommand = options.logout
      ?? (await import("./accounts.mjs")).logout;
    await logoutCommand(argv.slice(1), { env, output });
    return;
  }
  if (command === "rename" && argv.length === 3) {
    const renameCommand = options.rename
      ?? (await import("./accounts.mjs")).rename;
    await renameCommand(argv.slice(1), { env, output });
    return;
  }
  if (command === "providers" && argv.length === 1) {
    output.write(`${(options.formatProviders ?? formatSupportedProviders)()}\n`);
    return;
  }
  if (command === "usage" && argv.length === 1) {
    const path = env.CYCLO_GATEWAY_USAGE_JSONL ?? DEFAULT_USAGE_PATH;
    const report = await (options.aggregateUsage ?? aggregateUsageFile)(path);
    output.write(`${JSON.stringify(report, null, 2)}\n`);
    return;
  }
  throw new Error(
    "usage: cyclo-gateway-component [providers | usage | login PROVIDER [OPTIONS]"
    + " | logout ACCOUNT | rename OLD_ACCOUNT NEW_ACCOUNT]",
  );
}

async function loadDefaultServices(options) {
  const { createGatewayServices } = await import("./services.mjs");
  return createGatewayServices(options);
}

async function unexpectedOutputStop(outputLoop, signal) {
  await outputLoop;
  if (!signal.aborted) throw new Error("gateway Provider output stopped");
}

async function unexpectedChannelStop(channelLoop, signal) {
  await channelLoop;
  if (!signal.aborted) throw new Error("gateway host channel stopped");
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
