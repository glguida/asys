import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";

import { Code, ConnectError, createClient } from "@connectrpc/connect";
import { HealthStatus } from "@cyclo/component/contract";
import { Admin, Discovery, Usage } from "@cyclo/gateway-protocol/contract";
import { closeComponentServer } from "@cyclo/component/server";
import {
  createTestTransport,
  listenTestServer,
} from "@cyclo/component/test-support";
import { Provider } from "@cyclo/provider/contract";

import { checkGatewayHealth } from "../src/healthcheck.mjs";
import { runGateway } from "../src/main.mjs";
import { createGatewayServer } from "../src/server.mjs";

test("serves only the private Provider interface", async () => {
  await withGateway(async ({ target, services }) => {
    const transport = createTestTransport(target);
    const provider = createClient(Provider, transport);

    assert.deepEqual((await provider.listModels({})).models, []);
    const payloads = [];
    for await (const response of provider.infer({
      model: "test-model",
      payload: "request",
    })) {
      payloads.push(response.payload);
    }
    assert.deepEqual(payloads, ["start", "done"]);

    for (const [schema, request] of [
      [Discovery, {}],
      [Usage, {}],
      [Admin, { account: "work" }],
    ]) {
      const client = createClient(schema, transport);
      const call = schema === Admin
        ? client.logout(request)
        : schema === Discovery
          ? client.listProviders(request)
          : client.getUsage(request);
      await assert.rejects(
        call,
        (error) => error instanceof ConnectError && error.code === Code.Unimplemented,
      );
    }
    assert.equal(services.status, HealthStatus.READY);
  });
});

for (const signalName of ["SIGTERM", "SIGINT"]) {
  test(`${signalName} aborts active RPCs and closes the listener`, async () => {
    const signalSource = new EventEmitter();
    const apiListening = deferred();
    const providerListening = deferred();
    const running = runGateway({
      services: fakeServices({ waitForCancellation: true }),
      signalSource,
      serveChannel: async (_services, _root, { signal, onReady }) => { onReady(); await aborted(signal); },
      apiListenOptions: { host: "127.0.0.1", port: 0 },
      onListening: apiListening.resolve,
      serveOutput: async (server, _name, { signal }) => {
        providerListening.resolve(await listenTestServer(server));
        await aborted(signal);
      },
    });
    const [apiAddress, providerAddress] = await Promise.all([
      apiListening.promise,
      providerListening.promise,
    ]);
    const target = providerAddress.port;
    const healthURL = `http://127.0.0.1:${apiAddress.port}/health`;

    try {
      assert.equal(await checkGatewayHealth({ url: healthURL }), true);
      assert.equal((await fetch(`http://127.0.0.1:${apiAddress.port}/v1/providers`)).status, 404);
      const provider = createClient(Provider, createTestTransport(target));
      const iterator = provider.infer({
        model: "test-model",
        payload: "request",
      })[Symbol.asyncIterator]();
      assert.equal((await iterator.next()).value.payload, "start");

      signalSource.emit(signalName);
      await assert.rejects(
        iterator.next(),
        (error) => error instanceof ConnectError && error.code === Code.Unavailable,
      );
      await running;
      await assert.rejects(checkGatewayHealth({ url: healthURL, timeoutMs: 50 }));
    } finally {
      signalSource.emit(signalName);
      await running.catch(() => {});
    }
  });
}

async function withGateway(run) {
  const services = fakeServices();
  const server = await createGatewayServer({ services });
  try {
    const address = await listenTestServer(server, {
      host: "127.0.0.1",
      port: 0,
    });
    await run({
      target: address.port,
      services,
    });
  } finally {
    await closeComponentServer(server);
  }
}

function fakeServices({ waitForCancellation = false } = {}) {
  const services = {
    status: HealthStatus.READY,
    component: {
      health() {
        return { status: services.status, message: "test" };
      },
    },
    discovery: {
      listProviders() {
        return { providers: [] };
      },
    },
    usage: {
      getUsage() {
        return { version: 1 };
      },
    },
    admin: {
      login(request) {
        return { account: request.account || request.provider };
      },
      logout(request) {
        return { account: request.account };
      },
      rename(request) {
        return { account: request.account, newAccount: request.newAccount };
      },
    },
    provider: {
      listModels() {
        return { models: [] };
      },
      async *infer(_request, context) {
        yield { payload: "start" };
        if (waitForCancellation) {
          await aborted(context.signal);
          throw context.signal.reason;
        }
        yield { payload: "done" };
      },
    },
  };
  return services;
}

function aborted(signal) {
  if (signal.aborted) return Promise.resolve();
  return new Promise((resolve) => {
    signal.addEventListener("abort", resolve, { once: true });
  });
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
