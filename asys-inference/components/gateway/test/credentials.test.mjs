import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  createAccountCredentialStore,
  credentialSecretValues,
  readCredentialAccounts,
} from "../src/credentials.mjs";

async function credentialFixture(t, store) {
  const root = await mkdtemp(join(tmpdir(), "cyclo-gateway-credentials-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const path = join(root, "auth.json");
  if (store !== undefined) {
    await writeFile(path, `${JSON.stringify(store)}\n`, { mode: 0o600 });
  }
  return path;
}

test("the account projection preserves Pi credentials and explicit provider names", async (t) => {
  const path = await credentialFixture(t, {
    openai: { type: "api_key", provider: "openai", key: "first-secret" },
    work: {
      type: "api_key",
      provider: "cloudflare-ai-gateway",
      key: "cloudflare-secret",
      env: {
        CLOUDFLARE_ACCOUNT_ID: "account",
        CLOUDFLARE_GATEWAY_ID: "gateway",
      },
    },
  });

  assert.deepEqual(readCredentialAccounts(path).map(({ account, provider }) => ({
    account,
    provider,
  })), [
    { account: "openai", provider: "openai" },
    { account: "work", provider: "cloudflare-ai-gateway" },
  ]);
  const store = createAccountCredentialStore({
    path,
    account: "work",
    provider: "cloudflare-ai-gateway",
  });
  assert.deepEqual(await store.read("cloudflare-ai-gateway"), {
    type: "api_key",
    key: "cloudflare-secret",
    env: {
      CLOUDFLARE_ACCOUNT_ID: "account",
      CLOUDFLARE_GATEWAY_ID: "gateway",
    },
  });
  assert.deepEqual(await store.list(), [{
    providerId: "cloudflare-ai-gateway",
    type: "api_key",
  }]);
});

test("Pi modify replaces one account without touching another", async (t) => {
  const path = await credentialFixture(t, {
    personal: { type: "api_key", provider: "openai", key: "untouched" },
  });
  const store = createAccountCredentialStore({
    path,
    account: "work",
    provider: "openai",
  });

  await store.modify("openai", async () => ({
    type: "api_key",
    key: "work-secret",
  }));
  const document = JSON.parse(await readFile(path, "utf8"));
  assert.deepEqual(document.personal, {
    type: "api_key",
    provider: "openai",
    key: "untouched",
  });
  assert.deepEqual(document.work, {
    type: "api_key",
    key: "work-secret",
    provider: "openai",
  });
});

test("separate account projections serialize Pi's OAuth refresh mutation", async (t) => {
  const path = await credentialFixture(t, {
    work: {
      type: "oauth",
      provider: "openai-codex",
      access: "expired",
      refresh: "refresh-secret",
      expires: 1,
    },
  });
  const make = () => createAccountCredentialStore({
    path,
    account: "work",
    provider: "openai-codex",
  });
  let refreshes = 0;
  const refresh = (store) => store.modify("openai-codex", async (current) => {
    if (current.expires > 1) return undefined;
    refreshes += 1;
    await new Promise((resolve) => setTimeout(resolve, 20));
    return {
      ...current,
      access: "fresh",
      refresh: "fresh-refresh",
      expires: Date.now() + 3_600_000,
    };
  });

  const [left, right] = await Promise.all([refresh(make()), refresh(make())]);
  assert.equal(refreshes, 1);
  assert.equal(left.access, "fresh");
  assert.equal(right.access, "fresh");
});

test("the account projection rejects a different Pi provider id", async (t) => {
  const path = await credentialFixture(t, {
    work: { type: "api_key", provider: "anthropic", key: "secret" },
  });
  const store = createAccountCredentialStore({
    path,
    account: "work",
    provider: "anthropic",
  });
  await assert.rejects(store.read("openai"), /belongs to provider anthropic/u);
});

test("Pi credential payloads remain opaque to the account store", async (t) => {
  const path = await credentialFixture(t);
  const store = createAccountCredentialStore({
    path,
    account: "bedrock",
    provider: "amazon-bedrock",
  });
  await store.modify("amazon-bedrock", async () => ({ type: "api_key" }));
  assert.deepEqual(await store.read("amazon-bedrock"), { type: "api_key" });

  await store.modify("amazon-bedrock", async () => ({
    type: "api_key",
    env: { "provider-owned-field": "value" },
  }));
  assert.deepEqual(await store.read("amazon-bedrock"), {
    type: "api_key",
    env: { "provider-owned-field": "value" },
  });

  await store.modify("amazon-bedrock", async () => ({
    type: "oauth",
    access: "permanent-key",
    refresh: "",
    expires: Number.MAX_SAFE_INTEGER,
  }));
  assert.equal((await store.read("amazon-bedrock")).refresh, "");
});

test("the response guard receives only credential secret values", () => {
  assert.deepEqual(credentialSecretValues({
    type: "oauth",
    access: "access-secret",
    refresh: "refresh-secret",
    expires: 1,
  }), ["access-secret"]);
  assert.deepEqual(credentialSecretValues({
    type: "api_key",
    key: "api-secret",
    env: { ACCOUNT_ID: "non-secret-routing-value" },
  }), ["api-secret"]);
});

test("stored credentials require an explicit provider", async t => {
  const path = await credentialFixture(t, { openai: { type: "api_key", key: "key" } });
  assert.throws(() => readCredentialAccounts(path), /provider for account openai/);
});
