import { routeName } from "./route-name.mjs";
import { readJson, withFileLock, writeJsonAtomic } from "./store.mjs";

function objectDocument(value, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} must be a JSON object`);
  }
  return value;
}

function storeDocument(path) {
  const value = readJson(path);
  return value === null
    ? {}
    : objectDocument(value, `model cache ${path}`);
}

function modelEntry(value, label) {
  const entry = structuredClone(objectDocument(value, label));
  if (!Array.isArray(entry.models) || entry.models.some(
    (model) => !model || typeof model !== "object" || Array.isArray(model),
  )) {
    throw new Error(`${label} has an invalid models array`);
  }
  for (const field of ["lastModified", "checkedAt"]) {
    if (
      entry[field] !== undefined
      && (!Number.isFinite(entry[field]) || entry[field] < 0)
    ) {
      throw new Error(`${label} has an invalid ${field}`);
    }
  }
  if (entry.etag !== undefined && typeof entry.etag !== "string") {
    throw new Error(`${label} has an invalid etag`);
  }
  return entry;
}

function storedAccount(store, account) {
  if (!Object.hasOwn(store, account)) return undefined;
  const raw = structuredClone(
    objectDocument(store[account], `model cache entry ${account}`),
  );
  const provider = routeName(
    raw.provider,
    `provider for model cache entry ${account}`,
  );
  delete raw.provider;
  return {
    provider,
    entry: modelEntry(raw, `model cache entry ${account}`),
  };
}

// Pi keys ModelsStore entries by provider. Cyclo permits multiple accounts for
// one provider, so each Models runtime gets the same account-scoped projection
// used by the credential adapter.
export function createAccountModelsStore({ path, account, provider }) {
  if (typeof path !== "string" || !path) {
    throw new TypeError("model cache path is required");
  }
  routeName(account, "account name");
  routeName(provider, "provider name");

  const requireProvider = (providerId) => {
    if (providerId !== provider) {
      throw new Error(
        `account ${account} belongs to provider ${provider}, not ${providerId}`,
      );
    }
  };

  return Object.freeze({
    async read(providerId, options = {}) {
      requireProvider(providerId);
      options.signal?.throwIfAborted();
      const stored = storedAccount(storeDocument(path), account);
      return stored?.provider === provider
        ? structuredClone(stored.entry)
        : undefined;
    },

    async write(providerId, entry, options = {}) {
      requireProvider(providerId);
      const clean = modelEntry(entry, `model cache entry ${account}`);
      await withFileLock(path, async () => {
        options.signal?.throwIfAborted();
        const store = storeDocument(path);
        const candidate = structuredClone(store);
        candidate[account] = { ...clean, provider };
        writeJsonAtomic(path, candidate);
      });
    },

    async delete(providerId, options = {}) {
      requireProvider(providerId);
      await withFileLock(path, async () => {
        options.signal?.throwIfAborted();
        const store = storeDocument(path);
        const stored = storedAccount(store, account);
        if (stored?.provider !== provider) return;
        const candidate = structuredClone(store);
        delete candidate[account];
        writeJsonAtomic(path, candidate);
      });
    },
  });
}

export async function copyAccountModels({ path, source, target }) {
  if (typeof path !== "string" || !path) {
    throw new TypeError("model cache path is required");
  }
  routeName(source, "source account name");
  routeName(target, "target account name");
  await withFileLock(path, async () => {
    const store = storeDocument(path);
    const stored = storedAccount(store, source);
    const candidate = structuredClone(store);
    if (!stored) {
      if (!Object.hasOwn(candidate, target)) return;
      delete candidate[target];
      writeJsonAtomic(path, candidate);
      return;
    }
    candidate[target] = {
      provider: stored.provider,
      ...stored.entry,
    };
    writeJsonAtomic(path, candidate);
  });
}

export async function deleteAccountModels({ path, account }) {
  if (typeof path !== "string" || !path) {
    throw new TypeError("model cache path is required");
  }
  routeName(account, "account name");
  await withFileLock(path, async () => {
    const store = storeDocument(path);
    if (!Object.hasOwn(store, account)) return;
    const candidate = structuredClone(store);
    delete candidate[account];
    writeJsonAtomic(path, candidate);
  });
}
