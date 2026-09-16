import assert from "node:assert/strict";
import { once } from "node:events";
import { mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Readable, Writable } from "node:stream";
import test from "node:test";

import { Code, ConnectError, createClient } from "@connectrpc/connect";
import { createProvider, envApiKeyAuth } from "@earendil-works/pi-ai";
import { openAIResponsesApi } from "@earendil-works/pi-ai/api/openai-responses.lazy";
import { HealthStatus } from "@cyclo/component/contract";
import { AuthenticationMethod } from "@cyclo/gateway-protocol/contract";
import { closeComponentServer } from "@cyclo/component/server";
import {
  createTestTransport,
  listenTestServer,
} from "@cyclo/component/test-support";
import { Modality, Provider } from "@cyclo/provider/contract";
import {
  createResourceExhaustedError,
  resourceExhaustedRetryAt,
} from "@cyclo/provider/errors";
import { PI_INFERENCE_FORMAT } from "@cyclo/provider/protocol";

import { aggregateUsageFile } from "../src/audit.mjs";
import { buildCatalogue } from "../src/catalogue.mjs";
import { login, loginAccount } from "../src/login.mjs";
import { createPiAdapter } from "../src/pi-adapter.mjs";
import { createGatewayServer } from "../src/server.mjs";
import { createGatewayServices } from "../src/services.mjs";

test("admin mutations serialize without blocking inference or discovery", async () => {
  const model = publicModel("work/gpt-test");
  const inferenceStarted = deferred();
  const finishInference = deferred();
  const adminStarted = deferred();
  const commitStarted = deferred();
  const finishLogout = deferred();
  let commitEntered = false;
  const calls = [];
  const catalogue = {
    models: [model],
    routes: Object.assign(Object.create(null), {
      [model.id]: { account: "work", publicModel: model, rawModel: {} },
    }),
  };
  const providers = [{
    id: "openai",
    name: "OpenAI",
    auth: { apiKey: { login() {}, resolve() {} } },
    getModels: () => [{ api: "openai-responses" }],
    stream() {},
    streamSimple() {},
  }];
  const services = await createGatewayServices({
    catalogue,
    loadCatalogue: () => catalogue,
    providers,
    backend: {
      async *infer() {
        inferenceStarted.resolve();
        await finishInference.promise;
        yield { payload: "done" };
      },
    },
    audit: { async record() {} },
    async logout(request, options) {
      calls.push(["logout", request.account]);
      adminStarted.resolve();
      await options.commit(async () => {
        commitEntered = true;
        commitStarted.resolve();
        await finishLogout.promise;
      });
      return { account: request.account };
    },
    async rename(request, options) {
      calls.push(["rename", request.account, request.newAccount]);
      await options.commit(async () => {});
      return { account: request.account, newAccount: request.newAccount };
    },
  });

  const inference = collect(services.provider.infer(
    { model: model.id, payload: "opaque" },
    { signal: new AbortController().signal },
  ));
  await inferenceStarted.promise;

  const administration = services.admin.logout({ account: "work" });
  await adminStarted.promise;
  assert.deepEqual(calls, [["logout", "work"]]);
  assert.deepEqual((await services.discovery.listProviders({})).providers, [{
    id: "openai",
    description: "OpenAI",
    oauth: false,
    apiKey: true,
  }]);

  const rename = services.admin.rename({ account: "work", newAccount: "personal" });
  await Promise.resolve();
  assert.deepEqual(calls, [["logout", "work"]]);
  assert.equal(commitEntered, false);

  finishInference.resolve();
  await inference;
  await commitStarted.promise;
  finishLogout.resolve();
  assert.deepEqual(await administration, { account: "work" });
  assert.deepEqual(await rename, { account: "work", newAccount: "personal" });
  assert.deepEqual(calls, [
    ["logout", "work"],
    ["rename", "work", "personal"],
  ]);
});

test("inference queued during an account commit re-resolves the replaced catalogue", async () => {
  const model = publicModel("work/gpt-test");
  const replacementStarted = deferred();
  const finishReplacement = deferred();
  let backendCalled = false;
  const services = await createGatewayServices({
    catalogue: {
      models: [model],
      routes: Object.assign(Object.create(null), {
        [model.id]: { account: "work", publicModel: model, rawModel: {} },
      }),
    },
    async loadCatalogue() {
      replacementStarted.resolve();
      await finishReplacement.promise;
      return { models: [], routes: Object.create(null) };
    },
    providers: [{
      id: "openai",
      name: "OpenAI",
      auth: { apiKey: { login() {}, resolve() {} } },
      getModels: () => [],
      stream() {},
      streamSimple() {},
    }],
    backend: {
      async *infer() {
        backendCalled = true;
        yield { payload: "unexpected" };
      },
    },
    audit: { async record() {} },
    async logout(request, options) {
      await options.commit(async () => {});
      return { account: request.account };
    },
  });

  const logout = services.admin.logout({ account: "work" });
  await replacementStarted.promise;
  const inference = collect(services.provider.infer({
    model: model.id,
    payload: "opaque",
  }, { signal: new AbortController().signal }));
  finishReplacement.resolve();

  assert.deepEqual(await logout, { account: "work" });
  await assert.rejects(
    inference,
    (error) => error instanceof ConnectError && error.code === Code.NotFound,
  );
  assert.equal(backendCalled, false);
});

test("admin login uses attached component I/O while logout and rename are simple calls", async () => {
  const calls = [];
  const catalogue = { models: [], routes: Object.create(null) };
  const input = { name: "component-stdin" };
  const output = { name: "component-stdout" };
  const services = await createGatewayServices({
    catalogue,
    loadCatalogue: () => catalogue,
    backend: {},
    audit: { check() {} },
    input,
    output,
    async login(request, options) {
      calls.push(["login", request, options.input, options.output]);
      await options.commit(async () => {});
      return { account: "work", credentialType: "api_key" };
    },
    async logout(request, options) {
      calls.push(["logout", request, options.output]);
      await options.commit(async () => {});
      return { account: request.account };
    },
    async rename(request, options) {
      calls.push(["rename", request, options.output]);
      await options.commit(async () => {});
      return { account: request.account, newAccount: request.newAccount };
    },
  });

  assert.deepEqual(await services.admin.login({
    provider: "openai",
    account: "work",
    authentication: AuthenticationMethod.API_KEY,
    interactive: true,
  }), {
    account: "work",
    authentication: AuthenticationMethod.API_KEY,
  });
  assert.deepEqual(await services.admin.logout({ account: "work" }), {
    account: "work",
  });
  assert.deepEqual(await services.admin.rename({
    account: "work",
    newAccount: "personal",
  }), {
    account: "work",
    newAccount: "personal",
  });
  assert.deepEqual(calls, [
    ["login", {
      provider: "openai",
      account: "work",
      authentication: "api_key",
      interactive: true,
      apiKeyStdin: true,
    }, input, output],
    ["logout", { account: "work" }, null],
    ["rename", { account: "work", newAccount: "personal" }, null],
  ]);
  await assert.rejects(
    services.admin.login({ provider: "openai", authentication: 99 }),
    (error) => error instanceof ConnectError && error.code === Code.InvalidArgument,
  );
});

test("real admin API-key login consumes one attached stdin line", async (t) => {
  const directory = await mkdtemp(join(tmpdir(), "cyclo-gateway-admin-login-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const authPath = join(directory, "auth.json");
  const catalogue = { models: [], routes: Object.create(null) };
  const input = Readable.from(["attached-test-key\n"]);
  let display = "";
  const output = new Writable({
    write(chunk, _encoding, callback) {
      display += chunk.toString();
      callback();
    },
  });
  const services = await createGatewayServices({
    env: { CYCLO_GATEWAY_AUTH_JSON: authPath },
    catalogue,
    loadCatalogue: () => catalogue,
    backend: {},
    audit: { check() {} },
    input,
    output,
    login: loginAccount,
  });

  assert.deepEqual(await services.admin.login({
    provider: "openai",
    account: "work",
    authentication: AuthenticationMethod.API_KEY,
    interactive: false,
  }), {
    account: "work",
    authentication: AuthenticationMethod.API_KEY,
  });
  const store = JSON.parse(await readFile(authPath, "utf8"));
  assert.equal(store.work.key, "attached-test-key");
  assert.equal(display.includes("attached-test-key"), false);
});

test("a hard catalogue reload failure rolls back account storage before replying", async (t) => {
  const directory = await mkdtemp(join(tmpdir(), "cyclo-gateway-admin-rollback-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const authPath = join(directory, "auth.json");
  const modelsCachePath = join(directory, "models-cache.json");
  const originalAuth = {
    work: { type: "api_key", key: "private", provider: "openai" },
    other: { type: "api_key", key: "other", provider: "anthropic" },
  };
  const originalModels = {
    work: { provider: "openai", models: [], checkedAt: 1 },
    other: { provider: "anthropic", models: [], checkedAt: 2 },
  };
  await writeFile(authPath, JSON.stringify(originalAuth), { mode: 0o600 });
  await writeFile(modelsCachePath, JSON.stringify(originalModels), { mode: 0o600 });

  const oldModel = publicModel("work/gpt-test");
  const oldCatalogue = {
    models: [oldModel],
    routes: Object.assign(Object.create(null), {
      [oldModel.id]: { account: "work", publicModel: oldModel, rawModel: {} },
    }),
  };
  const newModel = publicModel("personal/gpt-test");
  const newCatalogue = {
    models: [newModel],
    routes: Object.assign(Object.create(null), {
      [newModel.id]: { account: "personal", publicModel: newModel, rawModel: {} },
    }),
  };
  let reloads = 0;
  const services = await createGatewayServices({
    env: {
      CYCLO_GATEWAY_AUTH_JSON: authPath,
      CYCLO_GATEWAY_MODELS_CACHE_JSON: modelsCachePath,
    },
    catalogue: oldCatalogue,
    loadCatalogue() {
      reloads += 1;
      if (reloads === 1) throw new Error("hard catalogue failure");
      return newCatalogue;
    },
    providers: [{
      id: "openai",
      name: "OpenAI",
      auth: { apiKey: { login() {}, resolve() {} } },
      getModels: () => [],
      stream() {},
      streamSimple() {},
    }],
    backend: {},
    audit: { check() {} },
  });

  await assert.rejects(
    services.admin.rename({ account: "work", newAccount: "personal" }),
    /hard catalogue failure/u,
  );
  assert.deepEqual(JSON.parse(await readFile(authPath, "utf8")), originalAuth);
  assert.deepEqual(JSON.parse(await readFile(modelsCachePath, "utf8")), originalModels);
  assert.deepEqual(services.provider.listModels({}).models, [oldModel]);

  assert.deepEqual(
    await services.admin.rename({ account: "work", newAccount: "personal" }),
    { account: "work", newAccount: "personal" },
  );
  assert.deepEqual(JSON.parse(await readFile(authPath, "utf8")), {
    personal: originalAuth.work,
    other: originalAuth.other,
  });
  assert.deepEqual(services.provider.listModels({}).models, [newModel]);
});

test("a failed first login removes the account stores it created", async (t) => {
  const directory = await mkdtemp(join(tmpdir(), "cyclo-gateway-login-rollback-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const authPath = join(directory, "auth.json");
  const modelsCachePath = join(directory, "models-cache.json");
  const input = Readable.from(["attached-test-key\n"]);
  const output = new Writable({
    write(_chunk, _encoding, callback) { callback(); },
  });
  const services = await createGatewayServices({
    env: {
      CYCLO_GATEWAY_AUTH_JSON: authPath,
      CYCLO_GATEWAY_MODELS_CACHE_JSON: modelsCachePath,
    },
    catalogue: { models: [], routes: Object.create(null) },
    loadCatalogue() { throw new Error("hard catalogue failure"); },
    backend: {},
    audit: { check() {} },
    input,
    output,
    login: loginAccount,
  });

  await assert.rejects(
    services.admin.login({
      provider: "openai",
      account: "work",
      authentication: AuthenticationMethod.API_KEY,
      interactive: false,
    }),
    /hard catalogue failure/u,
  );
  await assert.rejects(stat(authPath), { code: "ENOENT" });
  await assert.rejects(stat(modelsCachePath), { code: "ENOENT" });
  assert.deepEqual(services.provider.listModels({}).models, []);
});

test("routes only on model and passes the opaque payload to the endpoint", async () => {
  const directory = await mkdtemp(join(tmpdir(), "cyclo-gateway-services-"));
  const model = publicModel("work/gpt-test");
  const route = {
    account: "work",
    provider: "openai",
    publicModel: model,
    rawModel: { id: "gpt-test", provider: "openai", api: "openai-responses" },
  };
  const calls = [];
  const audit = [];
  const requestPayload = " { \"context\": {\"tools\":[{\"anyOf\":[]}]}, \"x\": 1 } ";
  const responsePayloads = [
    "{\"type\":\"start\",\"partial\":{}}",
    " { \"type\": \"future_pi_event\", \"unknown\": true } ",
    "{\"type\":\"done\",\"message\":{}}",
  ];
  const services = await createGatewayServices({
    catalogue: {
      models: [model],
      routes: Object.freeze(Object.assign(Object.create(null), { [model.id]: route })),
    },
    backend: { infer(selected, payload, signal) {
      calls.push(["infer", selected.publicModel.id, payload, signal.aborted]);
      return backendStream(responsePayloads);
    } },
    audit: { async record(value) { audit.push(value); } },
  });
  const server = await createGatewayServer({ services });

  try {
    const target = await listenTarget(server);
    const client = createClient(Provider, createTestTransport(target));
    const payloads = [];
    for await (const response of client.infer(
      { model: model.id, payload: requestPayload },
      { headers: {
        authorization: "Bearer hostile-caller-token",
        "x-api-key": "hostile-key",
        cookie: "hostile-cookie",
      } },
    )) payloads.push(response.payload);

    assert.deepEqual(payloads, responsePayloads);
    assert.deepEqual(calls, [
      ["infer", model.id, requestPayload, false],
    ]);
    assert.equal(audit.at(-1).outcome, "ok");
    assert.equal(audit.at(-1).input_tokens, 7);
  } finally {
    await closeComponentServer(server);
    await rm(directory, { recursive: true, force: true });
  }
});

test("commits usage before releasing the final opaque response", async () => {
  const model = publicModel("work/gpt-test");
  const auditStarted = deferred();
  const releaseAudit = deferred();
  const services = await createGatewayServices({
    catalogue: {
      models: [model],
      routes: Object.assign(Object.create(null), {
        [model.id]: { publicModel: model, rawModel: {} },
      }),
    },
    backend: {
      async *infer() {
        yield { payload: "streaming" };
        yield {
          payload: "terminal",
          usage: { inputTokens: 7, outputTokens: 3 },
        };
      },
    },
    audit: {
      async record(value) {
        auditStarted.resolve(value);
        await releaseAudit.promise;
      },
    },
  });
  const iterator = services.provider.infer(
    { model: model.id, payload: "opaque" },
    { signal: new AbortController().signal },
  )[Symbol.asyncIterator]();

  assert.deepEqual(await iterator.next(), {
    value: { payload: "streaming" },
    done: false,
  });
  const terminal = iterator.next();
  let terminalSettled = false;
  void terminal.then(
    () => { terminalSettled = true; },
    () => { terminalSettled = true; },
  );
  const record = await auditStarted.promise;
  assert.equal(record.outcome, "ok");
  assert.equal(record.input_tokens, 7);
  await Promise.resolve();
  assert.equal(terminalSettled, false);

  releaseAudit.resolve();
  assert.deepEqual(await terminal, {
    value: { payload: "terminal" },
    done: false,
  });
  assert.deepEqual(await iterator.next(), { value: undefined, done: true });
});

test("preserves typed pre-stream exhaustion through audit and ConnectRPC", async () => {
  const retryAt = new Date("2031-02-03T04:05:06.789Z");
  const model = publicModel("work/gpt-test");
  const audit = [];
  const services = await createGatewayServices({
    catalogue: {
      models: [model],
      routes: Object.assign(Object.create(null), {
        [model.id]: { publicModel: model, rawModel: {} },
      }),
    },
    backend: {
      async *infer() {
        throw createResourceExhaustedError(retryAt);
      },
    },
    audit: { async record(value) { audit.push(value); } },
  });
  const server = await createGatewayServer({ services });

  try {
    const target = await listenTarget(server);
    const client = createClient(Provider, createTestTransport(target));
    await assert.rejects(
      collect(client.infer({ model: model.id, payload: "opaque" })),
      (error) => error instanceof ConnectError
        && error.code === Code.ResourceExhausted
        && resourceExhaustedRetryAt(error)?.toISOString() === retryAt.toISOString(),
    );
    assert.equal(audit.length, 1);
    assert.equal(audit[0].outcome, `rpc_${Code.ResourceExhausted}`);
  } finally {
    await closeComponentServer(server);
  }
});

test("never returns a gateway credential reflected by a native upstream", async () => {
  const directory = await mkdtemp(join(tmpdir(), "cyclo-gateway-secret-boundary-"));
  const authPath = join(directory, "auth.json");
  const usagePath = join(directory, "usage.jsonl");
  const secret = "test-private-secret-9e0a";
  let receivedAuthorization;
  const upstream = createServer((request, response) => {
    receivedAuthorization = request.headers.authorization;
    response.writeHead(401, { "content-type": "application/json" });
    response.end(JSON.stringify({
      error: {
        message: `upstream rejected ${receivedAuthorization}`,
        type: "invalid_request_error",
      },
    }));
  });
  upstream.listen(0, "127.0.0.1");
  await once(upstream, "listening");
  const { port } = upstream.address();

  await writeFile(authPath, JSON.stringify({
    work: { type: "api_key", provider: "echo", key: secret },
  }), { mode: 0o600 });
  const native = createProvider({
    id: "echo",
    name: "echo",
    baseUrl: `http://127.0.0.1:${port}/v1`,
    auth: { apiKey: envApiKeyAuth("Echo key", []) },
    models: [{
      id: "gpt-test",
      provider: "echo",
      api: "openai-responses",
      baseUrl: `http://127.0.0.1:${port}/v1`,
      input: ["text"],
      contextWindow: 4096,
      maxTokens: 1024,
      reasoning: false,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    }],
    api: openAIResponsesApi(),
  });

  const services = await createGatewayServices({
    env: {
      CYCLO_GATEWAY_AUTH_JSON: authPath,
      CYCLO_GATEWAY_USAGE_JSONL: usagePath,
    },
    getProvider: (id) => id === native.id ? native : undefined,
  });
  const server = await createGatewayServer({ services });

  try {
    const target = await listenTarget(server);
    const client = createClient(Provider, createTestTransport(target));
    const payloads = [];
    let failure;
    try {
      for await (const response of client.infer({
        model: "work/gpt-test",
        payload: JSON.stringify({
          context: {
            messages: [{ role: "user", content: "hello", timestamp: 0 }],
          },
          options: { maxTokens: 32 },
        }),
      })) payloads.push(response.payload);
    } catch (error) {
      failure = error;
    }

    assert.equal(receivedAuthorization, `Bearer ${secret}`);
    assert.equal(payloads.length, 0);
    assert.ok(failure instanceof ConnectError);
    assert.equal(failure.code, Code.DataLoss);
    const visibleFailure = [
      failure.message,
      failure.rawMessage,
      ...Array.from(failure.metadata.entries()).flat(),
    ].join("\n");
    assert.equal(visibleFailure.includes(secret), false);
    assert.equal(visibleFailure.includes(`Bearer ${secret}`), false);
  } finally {
    await closeComponentServer(server);
    await new Promise((resolve, reject) => upstream.close((error) => (
      error ? reject(error) : resolve()
    )));
    await rm(directory, { recursive: true, force: true });
  }
});

test("never reconstructs a gateway credential split across native events", async () => {
  const secret = "test-private-secret-9e0a";
  const model = publicModel("work/gpt-test");
  const events = [
    { type: "text_delta", delta: "test-private-" },
    { type: "text_delta", delta: "secret-" },
    { type: "text_delta", delta: "9e0a" },
    { type: "done", message: {} },
  ];
  const services = await createGatewayServices({
    catalogue: {
      models: [model],
      routes: Object.assign(Object.create(null), {
        [model.id]: nativeAdapterRoute(model, () => backendEvents(events), secret),
      }),
    },
    backend: createPiAdapter(),
    audit: { async record() {} },
  });

  const payloads = [];
  let failure;
  try {
    for await (const response of services.provider.infer(
      {
        model: model.id,
        payload: JSON.stringify({ context: {}, options: {} }),
      },
      { signal: new AbortController().signal },
    )) payloads.push(response.payload);
  } catch (error) {
    failure = error;
  }

  const visibleText = payloads
    .map((payload) => JSON.parse(payload).delta ?? "")
    .join("");
  assert.equal(visibleText.includes(secret), false);
  assert.ok(failure instanceof ConnectError);
  assert.equal(failure.code, Code.DataLoss);
  assert.equal(failure.rawMessage.includes(secret), false);
});

test("one public ID survives login, catalogue, inference, audit, and usage", async (t) => {
  const directory = await mkdtemp(join(tmpdir(), "cyclo-gateway-route-contract-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const authPath = join(directory, "auth.json");
  const usagePath = join(directory, "usage.jsonl");
  const account = "a".repeat(64);
  const localModel = "😀".repeat(512);
  const publicId = `${account}/${localModel}`;
  const native = createProvider({
    id: "custom",
    name: "custom",
    baseUrl: "https://example.invalid/v1",
    auth: { apiKey: envApiKeyAuth("Test key", []) },
    models: [{
      id: localModel,
      provider: "custom",
      api: "openai-responses",
      baseUrl: "https://example.invalid/v1",
      input: ["text"],
      contextWindow: 4096,
      maxTokens: 1024,
      reasoning: false,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    }],
    api: { stream() {}, streamSimple() {} },
  });
  await writeFile(authPath, "{}\n", { mode: 0o600 });

  await login(
    ["custom", "--as", account, "--api-key-env", "TEST_KEY"],
    {
      env: {
        CYCLO_GATEWAY_AUTH_JSON: authPath,
        TEST_KEY: "test-only-key",
      },
      output: { write() {} },
      getProvider: () => native,
    },
  );
  const catalogue = await buildCatalogue({
    authPath,
    getProvider: () => native,
  });
  assert.deepEqual(catalogue.models.map(({ id }) => id), [publicId]);

  const services = await createGatewayServices({
    env: {
      CYCLO_GATEWAY_AUTH_JSON: authPath,
      CYCLO_GATEWAY_USAGE_JSONL: usagePath,
    },
    catalogue,
    backend: {
      async *infer(_route, payload) {
        assert.equal(payload, "opaque");
        yield {
          payload: "response",
          usage: { inputTokens: 2, outputTokens: 3 },
        };
      },
    },
  });
  const responses = [];
  for await (const response of services.provider.infer(
    { model: publicId, payload: "opaque" },
    { signal: new AbortController().signal },
  )) responses.push(response.payload);
  assert.deepEqual(responses, ["response"]);

  const usage = await aggregateUsageFile(usagePath);
  assert.equal(usage.by_provider[account].requests, 1);
  assert.equal(usage.by_model[publicId].total_tokens, 5);
});

test("records client abandonment without requiring inference semantics", async () => {
  const model = publicModel("work/gpt-test");
  const audit = [];
  const services = await createGatewayServices({
    catalogue: {
      models: [model],
      routes: Object.assign(Object.create(null), {
        [model.id]: { publicModel: model, rawModel: {} },
      }),
    },
    backend: { infer() { return endlessBackend(); } },
    audit: { async record(value) { audit.push(value); } },
  });
  const iterator = services.provider.infer(
    { model: model.id, payload: "opaque" },
    { signal: new AbortController().signal },
  )[Symbol.asyncIterator]();
  assert.equal((await iterator.next()).value.payload, "first");
  await iterator.return();
  assert.equal(audit.at(-1).outcome, "client_abandoned");
});

test("startup logs models excluded from the usable catalogue", async () => {
  const warnings = [];
  const services = await createGatewayServices({
    catalogue: {
      models: [],
      routes: Object.freeze(Object.create(null)),
      diagnostics: [{
        account: "work",
        model: "broken",
        message: "private diagnostic",
      }],
    },
    backend: {},
    audit: { check() {} },
    warn(message) { warnings.push(message); },
  });

  assert.deepEqual(warnings, [
    "Cyclo gateway excluded unusable catalogue entry work/broken: private diagnostic",
  ]);
  assert.deepEqual(await services.component.health({}), {
    status: HealthStatus.READY,
    message: "ready",
  });
});

test("startup catalogue diagnostics cannot inject multiline log entries", async () => {
  const warnings = [];
  await createGatewayServices({
    catalogue: {
      models: [],
      routes: Object.freeze(Object.create(null)),
      diagnostics: [{
        account: "work",
        model: "bad\nmodel\u0007",
        message: "invalid\ncatalogue\u0007entry",
      }],
    },
    backend: {},
    audit: { check() {} },
    warn(message) { warnings.push(message); },
  });

  assert.deepEqual(warnings, [
    "Cyclo gateway excluded unusable catalogue entry work/bad model: invalid catalogue entry",
  ]);
});

test("default construction publishes Pi models and health uses the loaded snapshot", async () => {
  const directory = await mkdtemp(join(tmpdir(), "cyclo-gateway-default-"));
  const authPath = join(directory, "auth.json");
  try {
    await writeFile(authPath, JSON.stringify({
      openai: { type: "api_key", provider: "openai", key: "test-only-key" },
    }), { mode: 0o600 });
    const services = await createGatewayServices({
      env: {
        CYCLO_GATEWAY_AUTH_JSON: authPath,
        CYCLO_GATEWAY_USAGE_JSONL: join(directory, "usage.jsonl"),
      },
    });
    const models = (await services.provider.listModels()).models;
    assert.ok(models.length > 0);
    assert.ok(models.every(({ id }) => id.startsWith("openai/")));
    assert.ok(models.every(({ inferenceFormat }) => inferenceFormat === PI_INFERENCE_FORMAT));

    await writeFile(authPath, JSON.stringify({
      openai: { type: "api_key", provider: "openai", key: "test-only-key" },
      work: { type: "api_key", provider: "openai", key: "second-test-only-key" },
    }), { mode: 0o600 });
    assert.equal((await services.component.health({})).status, HealthStatus.READY);
    assert.ok((await services.provider.listModels()).models.every(
      ({ id }) => id.startsWith("openai/"),
    ));

    await writeFile(authPath, "not json\n", { mode: 0o600 });
    assert.equal((await services.component.health({})).status, HealthStatus.READY);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

function publicModel(id) {
  return Object.freeze({
    id,
    displayName: id,
    capabilities: Object.freeze({
      inputModalities: Object.freeze([Modality.TEXT]),
      outputModalities: Object.freeze([Modality.TEXT]),
      functionTools: true,
      parallelToolCalls: true,
      extensionTypes: Object.freeze([]),
    }),
    extensions: Object.freeze([]),
    inferenceFormat: PI_INFERENCE_FORMAT,
  });
}

async function* backendStream(payloads) {
  yield { payload: payloads[0] };
  yield { payload: payloads[1] };
  yield {
    payload: payloads[2],
    usage: { inputTokens: 7, outputTokens: 3, cachedInputTokens: 2, reasoningTokens: 1 },
  };
}

async function* endlessBackend() {
  yield { payload: "first" };
  yield { payload: "held" };
  await new Promise(() => {});
}

async function* backendEvents(events) {
  for (const event of events) yield event;
}

function nativeAdapterRoute(publicModel_, streamer, secret) {
  const provider = "openai";
  const rawModel = {
    id: "gpt-test",
    provider,
    api: "openai-responses",
  };
  return {
    publicModel: publicModel_,
    provider,
    rawModel,
    credentialStore: {
      async read() { return { type: "api_key", key: secret }; },
    },
    models: {
      streamSimple: streamer,
    },
  };
}

async function collect(values) {
  const result = [];
  for await (const value of values) result.push(value);
  return result;
}

async function listenTarget(server) {
  const address = await listenTestServer(server, {
    host: "127.0.0.1",
    port: 0,
  });
  return address.port;
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}
