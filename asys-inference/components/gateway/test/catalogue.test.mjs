import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { buildCatalogue } from "../src/catalogue.mjs";

async function fixture(t, auth) {
  const root = await mkdtemp(join(tmpdir(), "cyclo-gateway-catalogue-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const authPath = join(root, "auth.json");
  await writeFile(authPath, JSON.stringify(auth));
  return { authPath };
}

function provider(id, models, options = {}) {
  const apiKey = {
    async resolve({ credential }) {
      return credential?.key ? { auth: { apiKey: credential.key } } : undefined;
    },
  };
  return {
    id,
    name: id,
    baseUrl: options.baseUrl,
    auth: options.oauth
      ? {
          apiKey,
          oauth: {
            ...options.oauth,
            async toAuth(credential) {
              return { apiKey: credential.access };
            },
          },
        }
      : { apiKey },
    getModels: () => models,
    ...(options.filterModels ? { filterModels: options.filterModels } : {}),
    stream() {},
    streamSimple() {},
  };
}

test("publishes account/model IDs while keeping native routes private", async (t) => {
  const { authPath } = await fixture(t, {
    work: { type: "api_key", provider: "openai", key: "credential-secret" },
  });
  const native = provider("openai", [
    {
      id: "fast",
      name: "Fast",
      provider: "openai",
      api: "openai-responses",
      baseUrl: "https://one.invalid/v1",
      input: ["text", "image"],
      compat: { supportsTemperature: false },
      contextWindow: 4096,
      maxTokens: 1024,
    },
    {
      id: "reasoning",
      provider: "openai",
      api: "openai-responses",
      reasoning: true,
      input: ["text"],
      contextWindow: 8192,
      maxTokens: 2048,
    },
  ]);
  const catalogue = await buildCatalogue({
    authPath,
    getProvider: (id) => id === native.id ? native : undefined,
  });

  assert.deepEqual(catalogue.models.map(({ id }) => id), [
    "work/fast",
    "work/reasoning",
  ]);
  assert.deepEqual(catalogue.models[0].capabilities.inputModalities, [1, 2]);
  assert.equal(catalogue.models[0].capabilities.temperature, false);
  assert.equal(catalogue.models[1].capabilities.reasoning, true);
  assert.equal(catalogue.models[0].contextWindowTokens, 4096n);
  assert.equal(catalogue.routes["work/fast"].rawModel.baseUrl, "https://one.invalid/v1");
  assert.equal(catalogue.routes["work/fast"].models.streamSimple instanceof Function, true);
  const publicJson = JSON.stringify(catalogue.models, (_key, value) => (
    typeof value === "bigint" ? value.toString() : value
  ));
  assert.equal(publicJson.includes("credential-secret"), false);
  assert.equal(publicJson.includes("one.invalid"), false);
});

test("bad models are isolated without hiding valid models", async (t) => {
  const { authPath } = await fixture(t, {
    work: { type: "api_key", provider: "openai", key: "key" },
  });
  const native = provider("openai", [
    { ...usableModel("bad-input"), input: ["audio"] },
    { ...usableModel("missing-limit"), maxTokens: undefined },
    usableModel("usable"),
  ]);
  const catalogue = await buildCatalogue({
    authPath,
    getProvider: () => native,
  });
  assert.deepEqual(catalogue.models.map(({ id }) => id), ["work/usable"]);
  assert.deepEqual(catalogue.diagnostics.map(({ model }) => model), [
    "bad-input",
    "missing-limit",
  ]);
});

test("invalid accounts and unknown providers fail closed", async (t) => {
  const invalid = await fixture(t, {
    _legacy: { type: "api_key", provider: "openai", key: "key" },
  });
  await assert.rejects(buildCatalogue({
    authPath: invalid.authPath,
    getProvider: () => provider("openai", []),
  }), /account name/u);

  const unknown = await fixture(t, {
    work: { type: "api_key", provider: "missing", key: "key" },
  });
  await assert.rejects(buildCatalogue({
    authPath: unknown.authPath,
    getProvider: () => undefined,
  }), /unknown provider missing/u);
});

test("provider failures become bounded diagnostics", async (t) => {
  const { authPath } = await fixture(t, {
    work: { type: "api_key", provider: "broken", key: "key" },
  });
  const broken = provider("broken", []);
  broken.getModels = () => {
    throw new Error("provider leaked private-key");
  };
  const catalogue = await buildCatalogue({
    authPath,
    getProvider: () => broken,
  });
  assert.deepEqual(catalogue.models, []);
  assert.equal(catalogue.diagnostics[0].message, "availability check failed");
  assert.doesNotMatch(JSON.stringify(catalogue.diagnostics), /private-key/u);
});

test("Pi OAuth filtering defines the account catalogue", async (t) => {
  const { authPath } = await fixture(t, {
    copilot: {
      type: "oauth",
      provider: "github-copilot",
      access: "access-token",
      refresh: "refresh-token",
      expires: Date.now() + 3_600_000,
      availableModelIds: ["kept"],
    },
  });
  const native = provider(
    "github-copilot",
    [usableModel("kept", "github-copilot"), usableModel("hidden", "github-copilot")],
    {
      oauth: { async refresh(credential) { return credential; } },
      filterModels: (models, credential) => models.filter(
        ({ id }) => credential.availableModelIds.includes(id),
      ),
    },
  );
  const catalogue = await buildCatalogue({
    authPath,
    getProvider: () => native,
  });
  assert.deepEqual(catalogue.models.map(({ id }) => id), ["copilot/kept"]);
});

function usableModel(id, providerId = "openai") {
  return {
    id,
    provider: providerId,
    api: "openai-responses",
    input: ["text"],
    contextWindow: 4096,
    maxTokens: 1024,
  };
}
