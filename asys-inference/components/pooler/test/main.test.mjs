import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";

import { createClient } from "@connectrpc/connect";
import {
  createTestTransport,
  listenTestServer,
} from "@cyclo/component/test-support";
import { Provider } from "@cyclo/provider/contract";
import { PI_INFERENCE_FORMAT } from "@cyclo/provider/protocol";

import { main, runPooler } from "../src/main.mjs";

test("the image command is the pool configuration, with no serve subcommand", async () => {
  await assert.rejects(main([]), /usage: cyclo-pooler-component/u);
  await assert.rejects(main(["serve"]), /at least two members/u);
});

test("the logical provider name survives physical prefixes and is listed through its host channel", async t => {
  const hostRoot = await mkdtemp(join(tmpdir(), "asys-pool-name-"));
  t.after(() => rm(hostRoot, { recursive: true, force: true }));
  await writeFile(join(hostRoot, "provider.json"), JSON.stringify({ version: 1, name: "pool" }));
  const signalSource = new EventEmitter(), listening = deferred();
  const running = runPooler({
    argv: ["one", "two"], env: { DCOMP_COMPONENT_NAME: "deployment-pool" }, hostRoot, signalSource,
    createUpstream: async () => ({ client: { listModels: async () => ({ models: models() }) }, callOptions: signal => ({ signal }) }),
    apiListenOptions: { host: "127.0.0.1", port: 0 },
    serveOutput: async (server, _name, { signal }) => { listening.resolve(await listenTestServer(server)); await aborted(signal); },
  });
  t.after(async () => { signalSource.emit("SIGTERM"); await running; });
  const address = await listening.promise;
  const client = createClient(Provider, createTestTransport(address.port));
  assert.deepEqual((await client.listModels({})).models.map(model => model.id), ["one/model", "two/model", "pool/model"]);
  const helper = fileURLToPath(new URL("../../../tools/provider-channel", import.meta.url));
  const { stdout } = await promisify(execFile)("python3", [helper, hostRoot]);
  assert.deepEqual(JSON.parse(stdout).models.map(model => model.id), ["one/model", "two/model", "pool/model"]);
});

test("SIGTERM cancels active inference and closes the component listener", async () => {
  const signalSource = new EventEmitter();
  const apiListening = deferred();
  const providerListening = deferred();
  const canceled = deferred();
  const upstream = {
    client: {
      listModels: async () => ({ models: models() }),
      async *infer(_request, options) {
        yield { payload: "first" };
        try {
          await aborted(options.signal);
          throw options.signal.reason;
        } finally {
          canceled.resolve();
        }
      },
    },
    callOptions(signal, timeoutMs) {
      const options = { signal };
      if (timeoutMs !== undefined) options.timeoutMs = timeoutMs;
      return options;
    },
  };
  const running = runPooler({
    argv: ["one/model", "two/model", "model=balanced"],
    env: { DCOMP_COMPONENT_NAME: "pool" },
    signalSource,
    createUpstream: async () => upstream,
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
  assert.equal((await fetch(
    `http://127.0.0.1:${apiAddress.port}/health`,
  )).status, 200);
  const client = createClient(
    Provider,
    createTestTransport(providerAddress.port),
  );
  const iterator = client.infer({ model: "pool/balanced", payload: "opaque" })[
    Symbol.asyncIterator
  ]();
  assert.equal((await iterator.next()).value.payload, "first");
  signalSource.emit("SIGTERM");
  await assert.rejects(iterator.next());
  await running;
  await withTimeout(canceled.promise);
  assert.equal(signalSource.listenerCount("SIGTERM"), 0);
  assert.equal(signalSource.listenerCount("SIGINT"), 0);
});

function models() {
  return [model("one/model"), model("two/model")];
}

function model(id) {
  return {
    id,
    displayName: id,
    capabilities: {
      inputModalities: [1],
      outputModalities: [1],
      functionTools: true,
      parallelToolCalls: true,
      reasoningSummaries: true,
      temperature: false,
      topP: false,
      stopSequences: false,
      extensionTypes: [],
      reasoning: true,
    },
    contextWindowTokens: 100_000n,
    maxOutputTokens: 8_000n,
    extensions: [],
    inferenceFormat: PI_INFERENCE_FORMAT,
  };
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
