import assert from "node:assert/strict";
import test from "node:test";

import { createClient } from "@connectrpc/connect";

import { Provider } from "../../provider/gen/cyclo/provider/v1/provider_pb.js";
import { resolveBindings } from "../src/bindings.mjs";
import { parseDeclaration } from "../src/declaration.mjs";
import {
  closeComponentServer,
  createComponentServer,
} from "../src/server.mjs";
import {
  createTestTransport,
  listenTestServer,
} from "../src/test-support.mjs";

test("generated handler and client communicate over ConnectRPC TCP", async () => {
  const declaration = parseDeclaration(`
    component provider-proxy
    provide cyclo.provider.v1.Provider
    require upstream cyclo.provider.v1.Provider
  `);
  const bindings = resolveBindings(declaration, [Provider]);
  const provided = bindings.provides.get(Provider.typeName);
  const required = bindings.requires.get("upstream");
  let id = "fixture/first";

  const server = createComponentServer({
    bindings,
    implementations: new Map([
      [
        provided.typeName,
        {
          listModels() {
            return { models: [{ id }] };
          },
          async *infer() {},
        },
      ],
    ]),
  });

  try {
    const address = await listenTestServer(server);
    const client = createClient(required, createTestTransport(address.port));

    const response = await client.listModels({});
    assert.equal(response.models[0].id, "fixture/first");
    id = "fixture/second";
    assert.equal((await client.listModels({})).models[0].id, "fixture/second");
    await closeComponentServer(server);
    await assert.rejects(client.listModels({}));
  } finally {
    await closeComponentServer(server);
  }
});
