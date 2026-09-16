import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { PassThrough } from "node:stream";
import test from "node:test";

import { createModels } from "@earendil-works/pi-ai";

import {
  gatewayAuthOptionSupported,
  login,
  loginAccount,
  parseLoginArgs,
} from "../src/login.mjs";
import { createAuthInteraction } from "../src/oauth-ui.mjs";
import { getPiProvider } from "../src/pi-registry.mjs";

test("native API-key login persists Pi's credential without displaying it", async () => {
  const directory = await mkdtemp(join(tmpdir(), "cyclo-gateway-login-"));
  const path = join(directory, "auth.json");
  const output = captureStream();
  try {
    await login(["openai", "--as", "work", "--api-key-env", "TEST_KEY"], {
      env: { CYCLO_GATEWAY_AUTH_JSON: path, TEST_KEY: "private-key" },
      output,
    });
    assert.deepEqual(JSON.parse(await readFile(path, "utf8")), {
      work: { type: "api_key", key: "private-key", provider: "openai" },
    });
    assert.match(output.text(), /stored api_key credential for work/u);
    assert.doesNotMatch(output.text(), /private-key/u);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("cancellation stops an interactive prompt without changing credentials", async () => {
  const directory = await mkdtemp(join(tmpdir(), "cyclo-gateway-login-cancel-"));
  const path = join(directory, "auth.json");
  const input = new PassThrough();
  const output = new PassThrough();
  const controller = new AbortController();
  try {
    const pending = login(["openai", "--api-key-stdin"], {
      env: { CYCLO_GATEWAY_AUTH_JSON: path },
      input,
      output,
      interactive: true,
      signal: controller.signal,
    });
    controller.abort();
    await assert.rejects(pending, { name: "AbortError" });
    await assert.rejects(readFile(path, "utf8"), { code: "ENOENT" });
  } finally {
    input.destroy();
    output.destroy();
    await rm(directory, { recursive: true, force: true });
  }
});

test("channel login cannot ignore an explicit key for ambient-only authentication", async () => {
  const directory = await mkdtemp(join(tmpdir(), "asys-channel-ambient-"));
  const path = join(directory, "auth.json");
  const provider = apiKeyProvider('ambient');
  delete provider.auth.apiKey.login;
  try {
    await assert.rejects(loginAccount({ provider: 'ambient', authentication: 'api_key' }, {
      env: { CYCLO_GATEWAY_AUTH_JSON: path }, getProvider: () => provider,
      apiKeyInput: true, interaction: { async prompt() { throw new Error('unexpected prompt'); }, notify() {} },
    }), /does not accept an entered key/);
    await assert.rejects(readFile(path), { code: 'ENOENT' });
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test("an unknown provider cannot replace an existing account", async () => {
  const directory = await mkdtemp(join(tmpdir(), "cyclo-gateway-login-unknown-"));
  const path = join(directory, "auth.json");
  const original = {
    work: { type: "api_key", key: "old-private-key", provider: "known" },
  };
  try {
    await writeFile(path, `${JSON.stringify(original)}\n`);
    await assert.rejects(login(["unknown", "--api-key-env", "TEST_KEY"], {
      env: { CYCLO_GATEWAY_AUTH_JSON: path, TEST_KEY: "new-private-key" },
      output: new PassThrough(),
      getProvider: () => undefined,
    }), /unknown provider/u);
    assert.deepEqual(JSON.parse(await readFile(path, "utf8")), original);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("login syntax is strict and supports account aliases", () => {
  assert.deepEqual(parseLoginArgs(["anthropic", "--as", "claude-work"]), {
    provider: "anthropic",
    account: "claude-work",
    apiKeyEnv: undefined,
    apiKeyStdin: false,
  });
  assert.throws(() => parseLoginArgs(["../escape"]), /provider name/u);
  assert.throws(() => parseLoginArgs(["_legacy"]), /provider name/u);
  assert.throws(() => parseLoginArgs(["openai", "--wat"]), /unknown argument/u);
  assert.throws(
    () => parseLoginArgs(["openai", "--api-key", "must-not-enter-argv"]),
    /unknown argument/u,
  );
});

test("the interaction supports Pi text, secret, selection, and manual-code prompts", async () => {
  const asked = [];
  const secrets = [];
  const interaction = createAuthInteraction({
    async ask(question) {
      asked.push(question);
      return question.startsWith("Enter number") ? "2" : "visible-answer";
    },
    async askSecret(question) {
      secrets.push(question);
      return "secret-answer";
    },
    write() {},
  });

  assert.equal(await interaction.prompt({ type: "text", message: "Text" }), "visible-answer");
  assert.equal(await interaction.prompt({ type: "manual_code", message: "Code" }), "visible-answer");
  assert.equal(await interaction.prompt({ type: "secret", message: "Secret" }), "secret-answer");
  assert.equal(await interaction.prompt({
    type: "select",
    message: "Select account",
    options: [{ id: "one", label: "One" }, { id: "two", label: "Two" }],
  }), "two");
  assert.equal(asked.length, 3);
  assert.equal(secrets.length, 1);
});

test("container-only login hides host ambient credential choices", async () => {
  assert.equal(
    gatewayAuthOptionSupported("amazon-bedrock", { id: "bearer-token" }),
    true,
  );
  assert.equal(
    gatewayAuthOptionSupported("amazon-bedrock", { id: "aws-profile" }),
    false,
  );
  assert.equal(
    gatewayAuthOptionSupported("amazon-bedrock", { id: "credential-chain" }),
    false,
  );
  assert.equal(
    gatewayAuthOptionSupported("google-vertex", { id: "api-key" }),
    true,
  );
  assert.equal(
    gatewayAuthOptionSupported("google-vertex", { id: "adc" }),
    false,
  );
  assert.equal(
    gatewayAuthOptionSupported("google-vertex", { id: "service-account" }),
    false,
  );

  const displayed = [];
  const interaction = createAuthInteraction({
    async ask() { return ""; },
    write(message) { displayed.push(message); },
    allowOption: (option) => gatewayAuthOptionSupported("google-vertex", option),
  });
  assert.equal(await interaction.prompt({
    type: "select",
    message: "Select Vertex authentication",
    options: [
      { id: "api-key", label: "API key" },
      { id: "adc", label: "Application Default Credentials" },
      { id: "service-account", label: "Service account file" },
    ],
  }), "api-key");
  assert.match(displayed.join("\n"), /unavailable inside the gateway/u);
  assert.doesNotMatch(displayed.join("\n"), /Application Default|Service account/u);
});

test("gateway login filters the pinned provider choices before displaying them", async (t) => {
  const directory = await mkdtemp(join(tmpdir(), "cyclo-gateway-login-policy-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const cases = [
    ["amazon-bedrock", ["bearer-token"], /Bearer token/u, /AWS profile|credential chain/u],
    ["google-vertex", ["api-key"], /Google Cloud API key/u, /Default Credentials|Service account/u],
  ];
  for (const [providerId, expected, visible, hidden] of cases) {
    const provider = getPiProvider(providerId);
    const stopped = new Error("prompt captured");
    let prompt;
    await assert.rejects(provider.auth.apiKey.login({
      signal: new AbortController().signal,
      async prompt(value) {
        prompt = value;
        throw stopped;
      },
      notify() {},
    }), (error) => error === stopped);
    assert.equal(prompt.type, "select");
    assert.deepEqual(
      prompt.options
        .filter((option) => gatewayAuthOptionSupported(providerId, option))
        .map(({ id }) => id),
      expected,
    );

    const input = new PassThrough();
    input.end("q\n");
    const output = captureStream();
    await assert.rejects(login([providerId], {
      env: { CYCLO_GATEWAY_AUTH_JSON: join(directory, `${providerId}.json`) },
      input,
      output,
      interactive: true,
    }), /Login cancelled/u);
    assert.match(output.text(), visible);
    assert.doesNotMatch(output.text(), hidden);
  }
});

test("native OAuth login receives every notification channel and its signal", async () => {
  const directory = await mkdtemp(join(tmpdir(), "cyclo-gateway-oauth-"));
  const path = join(directory, "auth.json");
  const output = captureStream();
  const native = oauthProvider("openai-codex", async (interaction) => {
    assert.equal(interaction.signal.aborted, false);
    interaction.notify({ type: "info", message: "Information" });
    interaction.notify({ type: "auth_url", url: "https://example.invalid/auth" });
    interaction.notify({
      type: "device_code",
      verificationUri: "https://example.invalid/device",
      userCode: "TEST-CODE",
    });
    interaction.notify({ type: "progress", message: "Authorizing" });
    return oauthCredential("oauth-access", "oauth-refresh");
  });
  try {
    await login(["openai-codex", "--as", "work"], {
      env: { CYCLO_GATEWAY_AUTH_JSON: path },
      output,
      getProvider: (id) => id === native.id ? native : undefined,
    });
    const stored = JSON.parse(await readFile(path, "utf8"));
    assert.equal(stored.work.access, "oauth-access");
    assert.match(output.text(), /Information|TEST-CODE|Authorizing/u);
    assert.doesNotMatch(output.text(), /oauth-access|oauth-refresh/u);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("Pi OAuth credentials are stored opaquely", async () => {
  const directory = await mkdtemp(join(tmpdir(), "cyclo-gateway-oauth-opaque-"));
  const path = join(directory, "auth.json");
  const native = oauthProvider("openrouter", async () => ({
    type: "oauth",
    access: "permanent-key",
    refresh: "",
    expires: Number.MAX_SAFE_INTEGER,
    providerField: "preserved",
  }));
  try {
    await login(["openrouter"], {
      env: { CYCLO_GATEWAY_AUTH_JSON: path },
      output: new PassThrough(),
      getProvider: () => native,
    });
    assert.deepEqual(JSON.parse(await readFile(path, "utf8")).openrouter, {
      type: "oauth",
      access: "permanent-key",
      refresh: "",
      expires: Number.MAX_SAFE_INTEGER,
      providerField: "preserved",
      provider: "openrouter",
    });
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("ambient authentication uses Pi's native resolver", async () => {
  const directory = await mkdtemp(join(tmpdir(), "cyclo-gateway-ambient-"));
  const path = join(directory, "auth.json");
  const native = {
    ...apiKeyProvider("ambient"),
    auth: {
      apiKey: {
        async resolve({ ctx }) {
          return await ctx.env("AMBIENT_TOKEN")
            ? { auth: { apiKey: "ambient" }, source: "AMBIENT_TOKEN" }
            : undefined;
        },
      },
    },
  };
  try {
    await login(["ambient"], {
      env: { CYCLO_GATEWAY_AUTH_JSON: path },
      output: new PassThrough(),
      getProvider: () => native,
      modelsFactory: (options) => createModels({
        ...options,
        authContext: {
          async env(name) { return name === "AMBIENT_TOKEN" ? "configured" : undefined; },
          async fileExists() { return false; },
        },
      }),
    });
    assert.deepEqual(JSON.parse(await readFile(path, "utf8")).ambient, {
      type: "api_key",
      provider: "ambient",
    });
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

function apiKeyProvider(id) {
  return {
    id,
    name: id,
    baseUrl: "https://provider.invalid/v1",
    auth: {
      apiKey: {
        async login(interaction) {
          return {
            type: "api_key",
            key: await interaction.prompt({ type: "secret", message: "Enter test key" }),
          };
        },
        async resolve({ credential }) {
          return credential?.key ? { auth: { apiKey: credential.key } } : undefined;
        },
      },
    },
    getModels() { return [model(id)]; },
    stream() {},
    streamSimple() {},
  };
}

function oauthProvider(id, oauthLogin) {
  return {
    ...apiKeyProvider(id),
    auth: {
      oauth: {
        login: oauthLogin,
        async refresh(credential) { return credential; },
        async toAuth(credential) { return { apiKey: credential.access }; },
      },
    },
  };
}

function model(provider) {
  return {
    id: "model",
    provider,
    api: "openai-responses",
    input: ["text"],
    contextWindow: 4096,
    maxTokens: 1024,
  };
}

function oauthCredential(access, refresh) {
  return {
    type: "oauth",
    access,
    refresh,
    expires: Date.now() + 3_600_000,
  };
}

function captureStream() {
  const output = new PassThrough();
  let text = "";
  output.setEncoding("utf8");
  output.on("data", (chunk) => { text += chunk; });
  output.text = () => text;
  return output;
}
