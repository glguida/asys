import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { dirname, delimiter, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { HealthStatus } from "@cyclo/component/contract";
import {
  closeHostAPIServer,
  listenHostAPIServer,
} from "@cyclo/component/http";
import { AuthenticationMethod } from "@cyclo/gateway-protocol/contract";
import { Modality } from "@cyclo/provider/contract";
import { PI_INFERENCE_FORMAT } from "@cyclo/provider/protocol";

import { createGatewayHTTPServer } from "../src/http.mjs";

const execute = promisify(execFile);
const testDirectory = dirname(fileURLToPath(import.meta.url));

test("the Python host client consumes the real JavaScript gateway contract", async () => {
  const account = `${"a".repeat(63)}-`;
  const publicModel = `${account}/${"m".repeat(1024)}`;
  const calls = [];
  const counters = { requests: 1n };
  const services = {
    component: {
      health() {
        return { status: HealthStatus.READY, message: "ready" };
      },
    },
    discovery: {
      listProviders() {
        return {
          providers: [{
            id: "work-",
            description: "d".repeat(1025),
            oauth: false,
            apiKey: true,
          }],
        };
      },
    },
    usage: {
      getUsage() {
        return {
          version: 1,
          byProvider: { [account]: counters },
          byModel: { [publicModel]: counters },
        };
      },
    },
    admin: {
      login(request) {
        calls.push(["login", request]);
        return {
          account: request.account,
          authentication: AuthenticationMethod.API_KEY,
        };
      },
      logout(request) {
        calls.push(["logout", request]);
        return { account: request.account };
      },
      rename(request) {
        calls.push(["rename", request]);
        return { account: request.account, newAccount: request.newAccount };
      },
    },
    provider: {
      listModels() {
        return {
          models: [{
            id: publicModel,
            displayName: "Generated contract model",
            capabilities: {
              inputModalities: [Modality.TEXT, Modality.IMAGE],
              outputModalities: [Modality.TEXT],
              functionTools: true,
              parallelToolCalls: true,
              reasoning: true,
            },
            contextWindowTokens: 1_048_576n,
            maxOutputTokens: 65_536n,
            inferenceFormat: PI_INFERENCE_FORMAT,
          }],
        };
      },
      async *infer() {},
    },
  };
  const server = createGatewayHTTPServer({ services });

  try {
    const address = await listenHostAPIServer(server, {
      host: "127.0.0.1",
      port: 0,
    });
    const sourceRoot = resolve(testDirectory, "../../../..");
    await execute(
      process.env.CYCLO_TEST_PYTHON ?? "python3",
      [join(testDirectory, "python-client-contract.py"), String(address.port)],
      {
        env: {
          ...process.env,
          PYTHONPATH: [sourceRoot, process.env.PYTHONPATH].filter(Boolean).join(delimiter),
        },
      },
    );
  } finally {
    await closeHostAPIServer(server);
  }

  assert.equal(calls.length, 3);
  assert.equal(calls[0][0], "login");
  assert.equal(calls[0][1].provider, "work-");
  assert.equal(calls[0][1].account, "team_");
  assert.equal(calls[0][1].authentication, AuthenticationMethod.API_KEY);
  assert.equal(calls[1][0], "logout");
  assert.equal(calls[1][1].account, "team_");
  assert.equal(calls[2][0], "rename");
  assert.equal(calls[2][1].newAccount, "personal-");
});
