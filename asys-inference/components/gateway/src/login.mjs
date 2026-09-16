import { createInterface } from "node:readline/promises";
import { dirname, join } from "node:path";
import { stdin, stdout } from "node:process";
import { Writable } from "node:stream";
import { pathToFileURL } from "node:url";

import {
  InMemoryCredentialStore,
  InMemoryModelsStore,
} from "@earendil-works/pi-ai";

import {
  createAccountRuntime,
  hasPublishableModel,
} from "./catalogue.mjs";
import { createAccountCredentialStore } from "./credentials.mjs";
import {
  createAccountModelsStore,
  deleteAccountModels,
} from "./models-store.mjs";
import { createAuthInteraction } from "./oauth-ui.mjs";
import { routeName } from "./route-name.mjs";

const DEFAULT_AUTH_PATH = "/var/lib/cyclo-gateway/auth.json";
const UNAVAILABLE_GATEWAY_AUTH_OPTIONS = Object.freeze({
  "amazon-bedrock": new Set(["aws-profile", "credential-chain"]),
  "google-vertex": new Set(["adc", "service-account"]),
});

export function login(argv, options = {}) {
  return loginAccount({
    ...parseLoginArgs(argv),
    authentication: options.authentication,
    interactive: options.interactive,
  }, options);
}

export async function loginAccount(request, options = {}) {
  const env = options.env ?? process.env;
  const input = options.input ?? stdin;
  const output = options.output ?? stdout;
  const announce = options.announce ?? true;
  const commit = options.commit ?? ((operation) => operation());
  if (typeof commit !== "function") throw new TypeError("commit must be a function");
  const signal = options.signal;
  const selected = normalizeLoginRequest(request);
  const interactive = selected.interactive ?? input.isTTY;
  const account = selected.account ?? selected.provider;
  const hasApiKeySource = !options.interaction && (selected.apiKeyEnv !== undefined || selected.apiKeyStdin);
  const requestedAuthentication = selected.authentication;
  if (hasApiKeySource && requestedAuthentication === "oauth") {
    throw new Error("API-key input cannot be used for OAuth login");
  }
  const staged = new InMemoryCredentialStore();
  const stagedModels = new InMemoryModelsStore();
  const authPath = env.CYCLO_GATEWAY_AUTH_JSON ?? DEFAULT_AUTH_PATH;
  const modelsCachePath = env.CYCLO_GATEWAY_MODELS_CACHE_JSON
    ?? join(dirname(authPath), "models-cache.json");
  const runtime = createAccountRuntime({
    account,
    provider: selected.provider,
    authPath,
    modelsCachePath,
    credentialStore: staged,
    modelsStore: stagedModels,
    ...(options.getProvider === undefined ? {} : { getProvider: options.getProvider }),
    ...(options.modelsFactory === undefined ? {} : { modelsFactory: options.modelsFactory }),
  });
  const authentication = requestedAuthentication
    ?? (hasApiKeySource || typeof runtime.provider.auth.oauth?.login !== "function"
      ? "api_key"
      : "oauth");
  const authMethod = authentication === "oauth"
    ? runtime.provider.auth.oauth
    : runtime.provider.auth.apiKey;
  if (!authMethod || (authentication === "oauth" && typeof authMethod.login !== "function")) {
    throw new Error(`provider ${selected.provider} does not support ${authentication} login`);
  }
  const seed = hasApiKeySource
    ? await apiKeySeed(selected, { env, input, interactive, signal })
    : undefined;
  const terminalInteraction = options.interaction ?? createAuthInteraction({
    ask: (question, promptSignal) => visibleQuestion(
      input,
      output,
      question,
      promptSignal,
    ),
    askSecret: (question, promptSignal) => hiddenQuestion(
      input,
      output,
      question,
      promptSignal,
    ),
    write: (message) => output.write(`${message}\n`),
    signal,
    allowOption: (option) => gatewayAuthOptionSupported(selected.provider, option),
  });
  const interaction = seed === undefined
    ? terminalInteraction
    : seededApiKeyInteraction(terminalInteraction, seed, selected.provider);
  let credential;
  if (authentication === "api_key" && typeof authMethod.login !== "function") {
    if (seed !== undefined || options.apiKeyInput) {
      throw new Error(
        `provider ${selected.provider} uses ambient authentication and does not accept an entered key`,
      );
    }
    const configured = await runtime.models.checkAuth(selected.provider, { signal });
    if (!configured) {
      throw new Error(
        `provider ${selected.provider} ambient authentication is not configured in the gateway`,
      );
    }
    credential = { type: "api_key" };
    await staged.modify(
      selected.provider,
      async () => credential,
      { signal },
    );
  } else {
    credential = await runtime.models.login(
      selected.provider,
      authentication,
      interaction,
    );
  }
  const refreshed = await runtime.models.refresh({
    providers: [selected.provider],
    force: true,
    signal,
  });
  if (refreshed.aborted) signal?.throwIfAborted();
  const refreshError = refreshed.errors.get(selected.provider);
  if (refreshError) {
    throw new Error(`provider ${selected.provider} model discovery failed`, {
      cause: refreshError,
    });
  }
  const available = await runtime.models.getAvailable(selected.provider, { signal });
  if (!hasPublishableModel(runtime, available)) {
    throw new Error(
      `provider ${selected.provider} exposes no usable models for account ${account}`,
    );
  }

  const finalCredential = await staged.read(selected.provider, { signal })
    ?? credential;
  const modelCache = await stagedModels.read(selected.provider, { signal });
  await commit(async () => {
    if (modelCache) {
      await createAccountModelsStore({
        path: modelsCachePath,
        account,
        provider: selected.provider,
      }).write(selected.provider, modelCache, { signal });
    } else {
      try {
        await deleteAccountModels({ path: modelsCachePath, account });
      } catch {
        // A cache for an old provider is non-authoritative and will be ignored.
      }
    }
    const destination = createAccountCredentialStore({
      path: authPath,
      account,
      provider: selected.provider,
    });
    await destination.modify(
      selected.provider,
      async () => structuredClone(finalCredential),
      { signal },
    );
  });
  if (announce) output.write(`stored ${finalCredential.type} credential for ${account}\n`);
  return { account, credentialType: finalCredential.type };
}

export function gatewayAuthOptionSupported(provider, option) {
  return !UNAVAILABLE_GATEWAY_AUTH_OPTIONS[provider]?.has(option?.id);
}

function normalizeLoginRequest(value) {
  const provider = routeName(value.provider, "provider name");
  const account = value.account === undefined || value.account === ""
    ? undefined
    : routeName(value.account, "account name");
  return Object.freeze({
    provider,
    account,
    apiKeyEnv: value.apiKeyEnv,
    apiKeyStdin: value.apiKeyStdin ?? false,
    authentication: loginAuthentication(value.authentication),
    interactive: value.interactive,
  });
}

function loginAuthentication(value) {
  if (value === undefined) return undefined;
  if (value !== "oauth" && value !== "api_key") {
    throw new TypeError("authentication must be oauth or api_key");
  }
  return value;
}

export function parseLoginArgs(argv) {
  const provider = routeName(argv[0], "provider name");
  const result = {
    provider,
    account: undefined,
    apiKeyEnv: undefined,
    apiKeyStdin: false,
  };
  for (let index = 1; index < argv.length; index += 1) {
    const flag = argv[index];
    if (flag === "--as") {
      result.account = routeName(requireValue(argv, ++index, flag), "account name");
    } else if (flag === "--api-key-env") {
      result.apiKeyEnv = requireValue(argv, ++index, flag);
    } else if (flag === "--api-key-stdin") {
      result.apiKeyStdin = true;
    } else {
      throw new Error(`unknown argument: ${flag}`);
    }
  }
  return result;
}

async function apiKeySeed(parsed, {
  env,
  input,
  interactive,
  signal,
}) {
  const sources = [
    parsed.apiKeyEnv !== undefined,
    parsed.apiKeyStdin,
  ].filter(Boolean).length;
  if (sources !== 1) throw new Error("use exactly one API-key source");
  if (parsed.apiKeyEnv !== undefined) {
    const value = env[parsed.apiKeyEnv];
    if (!value) {
      throw new Error(`environment variable ${parsed.apiKeyEnv} is empty or unset`);
    }
    return value;
  }
  if (interactive) return undefined;
  const value = await plainQuestion(input, signal);
  if (!value) throw new Error("no API key on stdin");
  return value;
}

function seededApiKeyInteraction(interaction, seed, provider) {
  let consumed = false;
  return Object.freeze({
    signal: interaction.signal,
    notify: interaction.notify,
    async prompt(prompt) {
      if (!consumed && prompt?.type === "secret") {
        consumed = true;
        return seed;
      }
      throw new Error(
        `provider ${provider} requires interactive API-key setup; use a terminal`,
      );
    },
  });
}

async function visibleQuestion(input, output, prompt, signal) {
  const terminal = createInterface({ input, output });
  try {
    return await question(terminal, prompt, signal);
  } finally {
    terminal.close();
  }
}

async function hiddenQuestion(input, output, prompt, signal) {
  output.write(prompt);
  const muted = new Writable({
    write(_chunk, _encoding, callback) { callback(); },
  });
  const terminal = createInterface({ input, output: muted, terminal: true });
  try {
    const value = (await question(terminal, "", signal)).trim();
    if (!value) throw new Error("no value entered");
    return value;
  } finally {
    terminal.close();
    output.write("\n");
  }
}

async function plainQuestion(input, signal) {
  const muted = new Writable({
    write(_chunk, _encoding, callback) { callback(); },
  });
  const terminal = createInterface({ input, output: muted, terminal: false });
  try {
    return (await question(terminal, "", signal)).trim();
  } finally {
    terminal.close();
  }
}

function question(terminal, prompt, signal) {
  if (signal === undefined) return terminal.question(prompt);
  return terminal.question(prompt, { signal });
}

function requireValue(argv, index, flag) {
  const value = argv[index];
  if (value === undefined || value.startsWith("-")) {
    throw new Error(`${flag} requires a value`);
  }
  return value;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  login(process.argv.slice(2)).catch((error) => {
    console.error(`login failed: ${error.message}`);
    process.exitCode = 1;
  });
}
