#!/usr/bin/env node

import { realpathSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { checkComponentHealth } from "@cyclo/component/health";

export async function checkPoolerHealth({
  url,
  timeoutMs = 1_000,
} = {}) {
  return checkComponentHealth({ url, timeoutMs });
}

function isMain() {
  if (!process.argv[1]) return false;
  try {
    return realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url));
  } catch {
    return false;
  }
}

if (isMain()) {
  checkPoolerHealth()
    .then((ready) => { process.exitCode = ready ? 0 : 1; })
    .catch((error) => {
      console.error(error instanceof Error ? error.message : String(error));
      process.exitCode = 1;
    });
}
