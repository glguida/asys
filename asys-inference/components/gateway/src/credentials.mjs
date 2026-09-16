import { routeName } from "./route-name.mjs";
import { readJson, withFileLock, writeJsonAtomic } from "./store.mjs";

function objectDocument(value, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} must be a JSON object`);
  }
  return value;
}

function credentialDocument(value, label) {
  const credential = structuredClone(objectDocument(value, label));
  delete credential.provider;
  if (credential.type !== "api_key" && credential.type !== "oauth") {
    throw new Error(`${label} is not a Pi credential`);
  }
  return credential;
}

function storeDocument(path, { missing = false } = {}) {
  const value = readJson(path);
  if (value === null && missing) return {};
  return objectDocument(value, `credential store ${path}`);
}

function storedAccount(store, account) {
  if (!Object.hasOwn(store, account)) return undefined;
  const raw = objectDocument(store[account], `credential ${account}`);
  const provider = routeName(raw.provider, `provider for account ${account}`);
  return {
    provider,
    credential: credentialDocument(raw, `credential ${account}`),
  };
}

export function readCredentialAccounts(path) {
  const value = readJson(path);
  if (value === null) return Object.freeze([]);
  const store = objectDocument(value, `credential store ${path}`);
  const accounts = [];
  for (const account of Object.keys(store)) {
    routeName(account, "account name");
    const stored = storedAccount(store, account);
    accounts.push(Object.freeze({
      account,
      provider: stored.provider,
      credential: Object.freeze(stored.credential),
    }));
  }
  return Object.freeze(accounts);
}

// Pi keys credentials by Provider.id. Cyclo keys them by public account so one
// provider may be logged in more than once. One adapter per account preserves
// Pi's native CredentialStore contract while keeping Cyclo's on-disk schema.
export function createAccountCredentialStore({ path, account, provider }) {
  if (typeof path !== "string" || !path) {
    throw new TypeError("credential store path is required");
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

  const currentCredential = () => {
    const stored = storedAccount(storeDocument(path, { missing: true }), account);
    return stored?.provider === provider ? stored.credential : undefined;
  };

  return Object.freeze({
    async read(providerId, options = {}) {
      requireProvider(providerId);
      options.signal?.throwIfAborted();
      return structuredClone(currentCredential());
    },

    async list(options = {}) {
      options.signal?.throwIfAborted();
      const credential = currentCredential();
      return credential
        ? Object.freeze([{ providerId: provider, type: credential.type }])
        : Object.freeze([]);
    },

    async modify(providerId, fn, options = {}) {
      requireProvider(providerId);
      if (typeof fn !== "function") {
        throw new TypeError("credential mutation must be a function");
      }
      return withFileLock(path, async () => {
        options.signal?.throwIfAborted();
        const store = storeDocument(path, { missing: true });
        const stored = storedAccount(store, account);
        const current = stored?.provider === provider
          ? stored.credential
          : undefined;
        const next = await fn(structuredClone(current));
        options.signal?.throwIfAborted();
        if (next === undefined) return structuredClone(current);
        const credential = credentialDocument(next, `credential ${account}`);
        const candidate = structuredClone(store);
        candidate[account] = { ...credential, provider };
        writeJsonAtomic(path, candidate);
        return structuredClone(credential);
      });
    },

    async delete(providerId, options = {}) {
      requireProvider(providerId);
      await withFileLock(path, async () => {
        options.signal?.throwIfAborted();
        const store = storeDocument(path, { missing: true });
        const stored = storedAccount(store, account);
        if (stored?.provider !== provider) return;
        const candidate = structuredClone(store);
        delete candidate[account];
        writeJsonAtomic(path, candidate);
      });
    },
  });
}

export function credentialSecretValues(credential) {
  if (!credential || typeof credential !== "object") return Object.freeze([]);
  return Object.freeze([...new Set([
    credential.key,
    credential.access,
  ].filter((value) => typeof value === "string" && value))]);
}
