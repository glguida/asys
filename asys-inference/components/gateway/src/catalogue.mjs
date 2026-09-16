import { dirname, join } from "node:path";

import { createModels } from "@earendil-works/pi-ai";
import {
  isLocalModelId,
  PI_INFERENCE_FORMAT,
} from "@cyclo/provider/protocol";

import {
  createAccountCredentialStore,
  readCredentialAccounts,
} from "./credentials.mjs";
import { createAccountModelsStore } from "./models-store.mjs";
import { getPiProvider, validatePiProviders } from "./pi-registry.mjs";
import { routeName } from "./route-name.mjs";

const TEXT = 1;
const IMAGE = 2;

export function createAccountRuntime({
  account,
  provider,
  authPath,
  modelsCachePath,
  credentialStore,
  modelsStore,
  getProvider = getPiProvider,
  modelsFactory = createModels,
}) {
  routeName(account, "account name");
  routeName(provider, "provider name");
  requirePath(authPath, "authPath");
  const cachePath = modelsCachePath ?? join(dirname(authPath), "models-cache.json");
  requirePath(cachePath, "modelsCachePath");

  const nativeProvider = getProvider(provider);
  if (!nativeProvider) throw new Error(`unknown provider ${provider}`);
  validatePiProviders([nativeProvider]);

  const credentials = credentialStore ?? createAccountCredentialStore({
    path: authPath,
    account,
    provider,
  });
  const modelCache = modelsStore ?? createAccountModelsStore({
    path: cachePath,
    account,
    provider,
  });
  const models = modelsFactory({ credentials, modelsStore: modelCache });
  for (const method of [
    "setProvider",
    "refresh",
    "checkAuth",
    "getAvailable",
    "login",
    "streamSimple",
  ]) {
    if (typeof models?.[method] !== "function") {
      throw new Error("Pi returned an invalid Models instance");
    }
  }
  models.setProvider(nativeProvider);

  return Object.freeze({
    account,
    providerId: provider,
    provider: nativeProvider,
    models,
    credentialStore: credentials,
    modelsStore: modelCache,
  });
}

export function hasPublishableModel(runtime, models) {
  return models.some((model) => {
    try {
      publicModel(runtime.account, nativeModel(runtime.providerId, model));
      return true;
    } catch {
      return false;
    }
  });
}

export async function buildCatalogue({
  authPath,
  modelsCachePath,
  getProvider,
  modelsFactory,
  signal,
}) {
  requirePath(authPath, "authPath");
  const cachePath = modelsCachePath ?? join(dirname(authPath), "models-cache.json");
  requirePath(cachePath, "modelsCachePath");

  const models = [];
  const routes = Object.create(null);
  const diagnostics = [];

  for (const { account, provider } of readCredentialAccounts(authPath)) {
    const runtime = createAccountRuntime({
      account,
      provider,
      authPath,
      modelsCachePath: cachePath,
      ...(getProvider === undefined ? {} : { getProvider }),
      ...(modelsFactory === undefined ? {} : { modelsFactory }),
    });

    const restored = await runtime.models.refresh({
      allowNetwork: false,
      providers: [provider],
      signal,
    });
    if (restored.aborted) signal?.throwIfAborted();
    if (restored.errors.get(provider)) {
      diagnostics.push(diagnostic(account, undefined, "model cache restore failed"));
    }

    let available;
    try {
      available = await runtime.models.getAvailable(provider, { signal });
    } catch {
      diagnostics.push(diagnostic(account, undefined, "availability check failed"));
      continue;
    }

    const seen = new Set();
    for (const candidate of available) {
      let native;
      let published;
      try {
        native = nativeModel(provider, candidate);
        if (seen.has(native.id)) throw new Error(`repeats model ${native.id}`);
        seen.add(native.id);
        published = publicModel(account, native);
      } catch (error) {
        diagnostics.push(diagnostic(account, candidate?.id, error));
        continue;
      }
      if (Object.hasOwn(routes, published.id)) {
        throw new Error(`duplicate public model id ${published.id}`);
      }
      models.push(published);
      routes[published.id] = Object.freeze({
        account,
        provider,
        api: native.api,
        baseUrl: native.baseUrl,
        publicModel: published,
        rawModel: native,
        models: runtime.models,
        credentialStore: runtime.credentialStore,
      });
    }
  }

  models.sort((left, right) => left.id.localeCompare(right.id));
  return Object.freeze({
    models: Object.freeze(models),
    routes: Object.freeze(routes),
    diagnostics: Object.freeze(diagnostics),
  });
}

function nativeModel(provider, value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`provider ${provider} returned an invalid model`);
  }
  const model = structuredClone(value);
  if (!isLocalModelId(model.id)) {
    throw new Error(`provider ${provider} has an invalid model id`);
  }
  if (
    !Array.isArray(model.input)
    || !model.input.includes("text")
    || model.input.some((modality) => modality !== "text" && modality !== "image")
  ) {
    throw new Error(`model ${provider}/${model.id} has invalid input modalities`);
  }
  if (typeof model.api !== "string" || !model.api) {
    throw new Error(`model ${provider}/${model.id} has no API`);
  }
  return deepFreeze({ ...model, provider });
}

function publicModel(account, model) {
  const capabilities = Object.freeze({
    inputModalities: Object.freeze(model.input.includes("image") ? [TEXT, IMAGE] : [TEXT]),
    outputModalities: Object.freeze([TEXT]),
    functionTools: true,
    parallelToolCalls: true,
    reasoningSummaries: false,
    temperature: model.reasoning !== true && model.compat?.supportsTemperature !== false,
    topP: false,
    stopSequences: false,
    extensionTypes: Object.freeze([]),
    reasoning: model.reasoning === true,
  });
  return Object.freeze({
    id: `${account}/${model.id}`,
    displayName: typeof model.name === "string" && model.name ? model.name : model.id,
    capabilities,
    extensions: Object.freeze([]),
    inferenceFormat: PI_INFERENCE_FORMAT,
    contextWindowTokens: BigInt(tokenLimit(model.contextWindow, "context window", account, model.id)),
    maxOutputTokens: BigInt(tokenLimit(model.maxTokens, "output limit", account, model.id)),
  });
}

function tokenLimit(value, label, account, model) {
  if (!Number.isSafeInteger(value) || value <= 0) {
    throw new Error(`model ${account}/${model} has no usable ${label}`);
  }
  return value;
}

function diagnostic(account, model, error) {
  const detail = error instanceof Error ? error.message : String(error);
  const message = detail
    .replace(/[\u0000-\u001f\u007f]/gu, " ")
    .replace(/\s+/gu, " ")
    .trim()
    .slice(0, 512) || "invalid catalogue entry";
  return Object.freeze({
    account,
    ...(typeof model === "string" && model ? { model } : {}),
    message,
  });
}

function requirePath(value, label) {
  if (typeof value !== "string" || !value) throw new TypeError(`${label} is required`);
}

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  for (const child of Object.values(value)) deepFreeze(child);
  return Object.freeze(value);
}
