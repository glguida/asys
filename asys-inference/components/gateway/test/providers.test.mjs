import assert from "node:assert/strict";
import test from "node:test";

import {
  discoverSupportedProviders,
  formatSupportedProviders,
} from "../src/providers.mjs";

function piProvider(id, api, {
  apiKey = true,
  oauth = false,
  name = `${id} models`,
  apiKeyLogin = true,
} = {}) {
  return {
    id,
    name,
    auth: {
      ...(apiKey ? { apiKey: {
        ...(apiKeyLogin ? { login() {} } : {}),
        resolve() {},
      } } : {}),
      ...(oauth ? { oauth: { login() {} } } : {}),
    },
    getModels: () => [{ api }],
    stream() {},
    streamSimple() {},
  };
}

test("provider discovery describes copyable pre-login choices", () => {
  const providers = discoverSupportedProviders({
    providers: [
      piProvider("openai-codex", "openai-responses", { apiKey: false, oauth: true }),
      piProvider("openai", "openai-responses", { name: "OpenAI models" }),
      piProvider("anthropic", "anthropic-messages", {
        oauth: true,
        name: "Anthropic models",
      }),
    ],
  });

  assert.deepEqual(providers, [
    {
      id: "anthropic",
      description: "Anthropic models",
      oauth: true,
      apiKey: true,
    },
    {
      id: "openai",
      description: "OpenAI models",
      oauth: false,
      apiKey: true,
    },
    {
      id: "openai-codex",
      description: "openai-codex models",
      oauth: true,
      apiKey: false,
    },
  ]);
  const output = formatSupportedProviders(providers);
  assert.match(output, /Supported gateway providers/u);
  assert.match(output, /PROVIDER\s+DESCRIPTION\s+AUTH\s+LOGIN COMMAND/u);
  assert.match(output, /anthropic\s+Anthropic models\s+oauth or api-key/u);
  assert.match(output, /openai-codex\s+openai-codex models\s+oauth/u);
  assert.match(output, /Use --as NAME/u);
});

test("provider discovery rejects invalid registries and unsafe Pi names", () => {
  for (const providers of [
    [],
    [piProvider("openai", "openai-responses"), piProvider("openai", "openai-responses")],
    [piProvider("bad.provider", "openai-responses")],
    [piProvider("bad\tprovider", "openai-responses")],
    [42],
  ]) {
    assert.throws(
      () => discoverSupportedProviders({ providers }),
      /invalid provider registry/u,
    );
  }
  assert.throws(
    () => discoverSupportedProviders({
      providers: [piProvider("future-provider", "openai-responses", { name: "unsafe\tname" })],
    }),
    /invalid name/u,
  );
});

test("the pinned pi-ai provider registry is fully described", () => {
  const providers = discoverSupportedProviders();
  assert.ok(providers.length > 0);
  assert.equal(new Set(providers.map(({ id }) => id)).size, providers.length);
  assert.doesNotMatch(formatSupportedProviders(providers), /undefined|null/u);
  assert.equal(providers.some(({ id }) => id === "google"), true);
});

test("provider discovery does not impose a Cyclo API allowlist", () => {
  const providers = discoverSupportedProviders({
    providers: [
      piProvider("openai", "openai-responses"),
      piProvider("google", "google-generative-ai"),
    ],
  });
  assert.deepEqual(providers.map(({ id }) => id), ["google", "openai"]);
});

test("provider discovery includes native ambient API-key auth without a login prompt", () => {
  const [provider] = discoverSupportedProviders({
    providers: [piProvider("ambient", "openai-responses", { apiKeyLogin: false })],
  });
  assert.equal(provider.apiKey, true);
});
