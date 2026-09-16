import {
  readJson,
  removeJsonAtomic,
  withFileLock,
  writeJsonAtomic,
} from "./store.mjs";

export function createAccountTransactions({ authPath, modelsCachePath, replace }) {
  if (typeof replace !== "function") throw new TypeError("replace must be a function");
  const barrier = accountBarrier();

  return Object.freeze({
    read(account, signal) {
      return barrier.read(account, signal);
    },

    async commit(accounts, operation, signal) {
      if (typeof operation !== "function") {
        throw new TypeError("gateway account mutation must be a function");
      }
      await barrier.write(accounts, async () => {
        const snapshots = [
          accountSnapshot(authPath, accounts),
          accountSnapshot(modelsCachePath, accounts),
        ];
        try {
          await operation();
          await replace();
        } catch (error) {
          try {
            await restoreAccountSnapshots(snapshots);
          } catch (rollbackError) {
            throw new AggregateError(
              [error, rollbackError],
              "gateway account mutation failed and could not be rolled back",
            );
          }
          throw error;
        }
      }, signal);
    },
  });
}

function accountBarrier() {
  const readers = new Map();
  const drained = new Map();
  const blocked = new Map();

  return Object.freeze({
    async read(account, signal) {
      while (blocked.has(account)) {
        await waitFor(blocked.get(account).promise, signal);
      }
      readers.set(account, (readers.get(account) ?? 0) + 1);
      let held = true;
      return () => {
        if (!held) return;
        held = false;
        const remaining = readers.get(account) - 1;
        if (remaining === 0) {
          readers.delete(account);
          drained.get(account)?.resolve();
          drained.delete(account);
        } else {
          readers.set(account, remaining);
        }
      };
    },

    async write(accounts, operation, signal) {
      const names = [...new Set(accounts)].sort();
      const gates = names.map((account) => [account, deferred()]);
      if (gates.some(([account]) => blocked.has(account))) {
        throw new Error("gateway account mutation overlaps another writer");
      }
      for (const [account, gate] of gates) blocked.set(account, gate);
      try {
        await Promise.all(names.map(async (account) => {
          if (!readers.has(account)) return;
          const ready = deferred();
          drained.set(account, ready);
          await waitFor(ready.promise, signal);
        }));
        signal?.throwIfAborted();
        return await operation();
      } finally {
        for (const [account, gate] of gates) {
          blocked.delete(account);
          gate.resolve();
        }
      }
    },
  });
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

function waitFor(promise, signal) {
  signal?.throwIfAborted();
  if (!signal) return promise;
  return new Promise((resolve, reject) => {
    let settled = false;
    const finish = (settle, value) => {
      if (settled) return;
      settled = true;
      signal.removeEventListener("abort", onAbort);
      settle(value);
    };
    const onAbort = () => finish(reject, signal.reason);
    signal.addEventListener("abort", onAbort, { once: true });
    promise.then(
      (value) => finish(resolve, value),
      (error) => finish(reject, error),
    );
  });
}

function accountSnapshot(path, accounts) {
  const document = readJson(path);
  const store = accountDocument(document, path);
  return Object.freeze({
    path,
    missing: document === null,
    document: structuredClone(store),
    entries: Object.freeze(accounts.map((account) => Object.freeze({
      account,
      present: Object.hasOwn(store, account),
      value: Object.hasOwn(store, account)
        ? structuredClone(store[account])
        : undefined,
    }))),
  });
}

async function restoreAccountSnapshots(snapshots) {
  const failures = [];
  for (const snapshot of [...snapshots].reverse()) {
    try {
      await restoreAccountSnapshot(snapshot);
    } catch (error) {
      failures.push(error);
    }
  }
  if (failures.length > 0) {
    throw new AggregateError(failures, "failed to restore gateway account storage");
  }
}

async function restoreAccountSnapshot(snapshot) {
  await withFileLock(snapshot.path, async () => {
    const currentDocument = readJson(snapshot.path);
    const current = accountDocument(currentDocument, snapshot.path);
    if (!snapshot.missing && currentDocument === null) {
      writeJsonAtomic(snapshot.path, snapshot.document);
      return;
    }
    if (!accountEntriesChanged(current, snapshot.entries)) {
      if (snapshot.missing && currentDocument !== null && Object.keys(current).length === 0) {
        removeJsonAtomic(snapshot.path);
      }
      return;
    }
    const restored = structuredClone(current);
    for (const entry of snapshot.entries) {
      if (entry.present) restored[entry.account] = structuredClone(entry.value);
      else delete restored[entry.account];
    }
    if (snapshot.missing && Object.keys(restored).length === 0) {
      removeJsonAtomic(snapshot.path);
    } else {
      writeJsonAtomic(snapshot.path, restored);
    }
  });
}

function accountEntriesChanged(store, entries) {
  return entries.some((entry) => {
    const present = Object.hasOwn(store, entry.account);
    return present !== entry.present
      || (present && JSON.stringify(store[entry.account]) !== JSON.stringify(entry.value));
  });
}

function accountDocument(value, path) {
  if (value === null) return {};
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`gateway account store ${path} must be a JSON object`);
  }
  return value;
}
