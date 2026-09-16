import { dirname, join } from "node:path";

import { routeName } from "./route-name.mjs";
import { copyAccountModels, deleteAccountModels } from "./models-store.mjs";
import { readJson, withFileLock, writeJsonAtomic } from "./store.mjs";

const DEFAULT_AUTH_PATH = "/var/lib/cyclo-gateway/auth.json";

export async function logout(argv, options = {}) {
  if (argv.length !== 1) throw new Error("usage: logout ACCOUNT");
  return logoutAccount({ account: argv[0] }, options);
}

export async function logoutAccount(request, options = {}) {
  const account = routeName(request.account, "account name");
  const env = options.env ?? process.env;
  const output = options.output === undefined ? process.stdout : options.output;
  const commit = commitOperation(options);
  await commit(async () => {
    await updateCredentialStore(authPath(env), (candidate) => {
      if (!Object.hasOwn(candidate, account)) {
        throw new Error(`account ${account} is not stored`);
      }
      delete candidate[account];
    });
    await bestEffortDeleteAccountModels(modelsCachePath(env), account);
  });
  output?.write(`removed stored credential for ${account}\n`);
  return { account };
}

export async function rename(argv, options = {}) {
  if (argv.length !== 2) throw new Error("usage: rename OLD_ACCOUNT NEW_ACCOUNT");
  return renameAccount({ account: argv[0], newAccount: argv[1] }, options);
}

export async function renameAccount(request, options = {}) {
  const source = routeName(request.account, "source account name");
  const target = routeName(request.newAccount, "target account name");
  if (source === target) throw new Error("source and target account names must differ");
  const env = options.env ?? process.env;
  const output = options.output === undefined ? process.stdout : options.output;
  const commit = commitOperation(options);
  await commit(async () => {
    await updateCredentialStore(authPath(env), async (candidate) => {
      if (!Object.hasOwn(candidate, source)) {
        throw new Error(`account ${source} is not stored`);
      }
      if (Object.hasOwn(candidate, target)) {
        throw new Error(`account ${target} is already stored`);
      }
      const credential = candidate[source];
      if (!credential || typeof credential !== "object" || Array.isArray(credential)) {
        throw new Error(`credential ${source} must be a JSON object`);
      }
      const moved = structuredClone(credential);
      routeName(moved.provider, `provider for account ${source}`);
      try {
        await copyAccountModels({ path: modelsCachePath(env), source, target });
      } catch {
        // Model discovery state is disposable; it cannot block credential rename.
      }
      candidate[target] = moved;
      delete candidate[source];
    });
    await bestEffortDeleteAccountModels(modelsCachePath(env), source);
  });
  output?.write(`renamed stored credential ${source} to ${target}\n`);
  return { account: source, newAccount: target };
}

function commitOperation(options) {
  const commit = options.commit ?? ((operation) => operation());
  if (typeof commit !== "function") throw new TypeError("commit must be a function");
  return commit;
}

function authPath(env) {
  return env.CYCLO_GATEWAY_AUTH_JSON ?? DEFAULT_AUTH_PATH;
}

function modelsCachePath(env) {
  return env.CYCLO_GATEWAY_MODELS_CACHE_JSON
    ?? join(dirname(authPath(env)), "models-cache.json");
}

async function bestEffortDeleteAccountModels(path, account) {
  try {
    await deleteAccountModels({ path, account });
  } catch {
    // The cache contains no authentication material and is never authoritative.
    // A stale entry is ignored when its auth account is absent.
  }
}

async function updateCredentialStore(path, update) {
  await withFileLock(path, async () => {
    const store = readJson(path) ?? {};
    if (!store || typeof store !== "object" || Array.isArray(store)) {
      throw new Error("credential store must be a JSON object");
    }
    const candidate = structuredClone(store);
    await update(candidate);
    writeJsonAtomic(path, candidate);
  });
}
