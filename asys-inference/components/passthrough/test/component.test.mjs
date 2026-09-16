import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { createClient } from "@connectrpc/connect";
import { resolveBindings } from "@cyclo/component/bindings";
import { HealthStatus } from "@cyclo/component/contract";
import { parseDeclaration } from "@cyclo/component/declaration";
import {
  closeComponentServer,
  createComponentServer,
} from "@cyclo/component/server";
import {
  createTestTransport,
  listenTestServer,
} from "@cyclo/component/test-support";
import { Modality, Provider } from "@cyclo/provider/contract";
import { PI_INFERENCE_FORMAT } from "@cyclo/provider/protocol";

import { runPassthrough } from "../src/main.mjs";
import { createPassthroughServer } from "../src/server.mjs";
import { createPassthroughServices } from "../src/services.mjs";
import { createUpstreamBinding } from "../src/upstream.mjs";

const declarationUrl = new URL("../component.conf", import.meta.url);

test("declares exactly one upstream Provider", async () => {
  const declaration = parseDeclaration(await readFile(declarationUrl, "utf8"));
  assert.deepEqual(declaration, {
    name: "passthrough",
    provides: [Provider.typeName],
    requires: [{ name: "upstream", service: Provider.typeName }],
  });
});

test("forwards model, request payload, and response payloads exactly", async () => {
  const requestPayload = " { \"tools\": [{\"schema\": {\"anyOf\": []}}], \"x\": 1 } ";
  const responsePayloads = [
    "{\"type\":\"start\",\"partial\":{}}",
    " { \"type\": \"future_pi_event\", \"value\": \"unchanged\" } ",
  ];
  await withPassthrough({ responsePayloads }, async ({ provider, observed }) => {
    const actual = [];
    for await (const response of provider.infer(
      { model: "account/model", payload: requestPayload },
      { headers: { authorization: "Bearer caller-secret", cookie: "caller-secret" } },
    )) actual.push(response.payload);

    assert.equal(observed.requests.length, 1);
    assert.equal(observed.requests[0].model, "account/model");
    assert.equal(observed.requests[0].payload, requestPayload);
    assert.deepEqual(actual, responsePayloads);
    assert.equal(observed.headers[0].get("authorization"), null);
    assert.equal(observed.headers[0].get("cookie"), null);
  });
});

test("forwards the complete typed model catalogue unchanged", async () => {
  const models = [{
    id: "account/model",
    displayName: "Model",
    capabilities: {
      inputModalities: [Modality.TEXT],
      outputModalities: [Modality.TEXT],
      functionTools: true,
      parallelToolCalls: true,
      extensionTypes: [],
    },
    contextWindowTokens: 4096n,
    maxOutputTokens: 1024n,
    extensions: [],
    inferenceFormat: PI_INFERENCE_FORMAT,
  }];
  await withPassthrough({ models }, async ({ provider }) => {
    const [model] = (await provider.listModels({})).models;
    assert.equal(model.id, models[0].id);
    assert.equal(model.inferenceFormat, models[0].inferenceFormat);
    assert.equal(model.contextWindowTokens, models[0].contextWindowTokens);
    assert.equal(model.maxOutputTokens, models[0].maxOutputTokens);
    assert.deepEqual(
      model.capabilities.inputModalities,
      models[0].capabilities.inputModalities,
    );
    assert.deepEqual(
      model.capabilities.outputModalities,
      models[0].capabilities.outputModalities,
    );
  });
});

test("reports upstream health without leaking dependency errors", async () => {
  await withPassthrough({}, async ({ component, observed }) => {
    const signal = new AbortController().signal;
    observed.failHealth = true;
    assert.deepEqual(await component.health({}, { signal }), {
      status: HealthStatus.NOT_READY,
      message: "upstream provider unavailable",
    });
    observed.failHealth = false;
    assert.equal(
      (await component.health({}, { signal })).status,
      HealthStatus.READY,
    );
  });
});

test("propagates cancellation to the upstream without encoding it in JSON", async () => {
  const canceled = deferred();
  await withPassthrough({ wait: true, canceled }, async ({ provider }) => {
    const controller = new AbortController();
    const iterator = provider.infer(
      { model: "account/model", payload: "opaque" },
      { signal: controller.signal },
    )[Symbol.asyncIterator]();
    assert.equal((await iterator.next()).value.payload, "first");
    controller.abort();
    await assert.rejects(iterator.next());
    await withTimeout(canceled.promise);
  });
});

test("SIGTERM aborts active inference and closes the output listener", async () => {
  const canceled = deferred();
  const observed = state();
  const upstream = upstreamServer(observed, { wait: true, canceled });
  const signalSource = new EventEmitter();
  const upstreamFixture = await listenInputFixture(upstream);
  const apiListening = deferred();
  const providerListening = deferred();
  const running = runPassthrough({
    env: {
      DCOMP_IN_UPSTREAM: upstreamFixture.target,
    },
    signalSource,
    apiListenOptions: { host: "127.0.0.1", port: 0 },
    onListening: apiListening.resolve,
    serveOutput: async (server, _name, { signal }) => {
      providerListening.resolve(await listenTestServer(server));
      await aborted(signal);
    },
  });

  try {
    const [apiAddress, providerAddress] = await Promise.all([
      apiListening.promise,
      providerListening.promise,
    ]);
    assert.equal((await fetch(
      `http://127.0.0.1:${apiAddress.port}/health`,
    )).status, 200);
    const provider = createClient(
      Provider,
      createTestTransport(providerAddress.port),
    );
    const iterator = provider.infer({ model: "account/model", payload: "opaque" })[
      Symbol.asyncIterator
    ]();
    assert.equal((await iterator.next()).value.payload, "first");
    signalSource.emit("SIGTERM");
    await assert.rejects(iterator.next());
    await running;
    await withTimeout(canceled.promise);
  } finally {
    signalSource.emit("SIGTERM");
    await running.catch(() => {});
    await closeComponentServer(upstream);
    await upstreamFixture.cleanup();
  }
});

async function withPassthrough(options, run) {
  const observed = state();
  const upstream = upstreamServer(observed, options);
  const upstreamFixture = await listenInputFixture(upstream);
  let passthrough;
  try {
    const binding = createUpstreamBinding({
      env: { DCOMP_IN_UPSTREAM: upstreamFixture.target },
    });
    const services = createPassthroughServices({
      upstream: binding,
      healthTimeoutMs: 100,
    });
    passthrough = await createPassthroughServer({
      services,
    });
    const outputAddress = await listenTestServer(passthrough);
    const transport = createTestTransport(outputAddress.port);
    await run({
      component: services.component,
      provider: createClient(Provider, transport),
      observed,
    });
  } finally {
    if (passthrough) await closeComponentServer(passthrough).catch(() => {});
    await closeComponentServer(upstream).catch(() => {});
    await upstreamFixture.cleanup();
  }
}

function state() {
  return { failHealth: false, headers: [], requests: [] };
}

function upstreamServer(
  observed,
  {
    responsePayloads = [],
    models = [],
    wait = false,
    canceled,
  } = {},
) {
  const bindings = resolveBindings(parseDeclaration(`
    component upstream
    provide cyclo.provider.v1.Provider
  `), [Provider]);
  return createComponentServer({
    bindings,
    implementations: new Map([
      [Provider.typeName, {
        listModels(_request, context) {
          observed.headers.push(new Headers(context.requestHeader));
          if (observed.failHealth) throw new Error("private upstream failure");
          return { models };
        },
        async *infer(request, context) {
          observed.headers.push(new Headers(context.requestHeader));
          observed.requests.push(request);
          if (wait) {
            yield { payload: "first" };
            try {
              await aborted(context.signal);
              throw context.signal.reason;
            } finally {
              canceled?.resolve();
            }
          }
          for (const payload of responsePayloads) yield { payload };
        },
      }],
    ]),
  });
}

function aborted(signal) {
  if (signal.aborted) return Promise.resolve();
  return new Promise((resolve) => signal.addEventListener("abort", resolve, { once: true }));
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

function withTimeout(promise, timeoutMs = 1_000) {
  let timer;
  return Promise.race([
    promise,
    new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error("timed out")), timeoutMs);
    }),
  ]).finally(() => clearTimeout(timer));
}

async function listenInputFixture(server) {
  const root = await mkdtemp(join(tmpdir(), "cyclo-dcomp-input-"));
  const path = join(root, "upstream.sock");
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(path, resolve);
  });
  return {
    target: `unix://${path}`,
    cleanup: () => rm(root, { recursive: true, force: true }),
  };
}
