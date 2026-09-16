import assert from "node:assert/strict";
import test from "node:test";

import { createClient } from "@connectrpc/connect";
import { Provider } from "../../provider/gen/cyclo/provider/v1/provider_pb.js";

import { resolveBindings } from "../src/bindings.mjs";
import { parseDeclaration } from "../src/declaration.mjs";
import {
  closeComponentServer,
  createComponentServer,
  serveComponentOutput,
} from "../src/server.mjs";
import {
  createTestTransport,
  listenTestServer,
} from "../src/test-support.mjs";

test("component server handles ConnectRPC in an isolated TCP fixture", async () => {
  const server = providerServer();
  try {
    const address = await listenTestServer(server);
    const client = createClient(Provider, createTestTransport(address.port));
    assert.deepEqual((await client.listModels({})).models, []);
    await Promise.all([
      closeComponentServer(server),
      closeComponentServer(server),
    ]);
    await assert.rejects(client.listModels({}));
  } finally {
    await closeComponentServer(server);
  }
});

test("requires the dcomp output environment", async () => {
  const server = providerServer();
  await assert.rejects(
    serveComponentOutput(server, "provider", { env: {} }),
    /DCOMP_OUT_PROVIDER/u,
  );
  await closeComponentServer(server);
});

function providerServer() {
  const bindings = resolveBindings(
    parseDeclaration(`
      component test-provider
      provide cyclo.provider.v1.Provider
    `),
    [Provider],
  );
  return createComponentServer({
    bindings,
    implementations: new Map([[Provider.typeName, {
      listModels() {
        return { models: [] };
      },
      async *infer() {},
    }]]),
  });
}
