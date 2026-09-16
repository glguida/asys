import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { resolveBindings } from "@cyclo/component/bindings";
import { parseDeclaration } from "@cyclo/component/declaration";
import {
  closeComponentServer,
  createComponentServer,
} from "@cyclo/component/server";
import { Provider } from "@cyclo/provider/contract";

import { createUpstreamBinding } from "../src/upstream.mjs";

test("createUpstreamBinding uses only its DComp link and never carries caller credentials", async (t) => {
  let requestHeaders;
  const bindings = resolveBindings(
    parseDeclaration(`
      component test-upstream
      provide cyclo.provider.v1.Provider
    `),
    [Provider],
  );
  const server = createComponentServer({
    bindings,
    implementations: new Map([
      [Provider.typeName, {
        listModels(_request, context) {
          requestHeaders = new Headers(context.requestHeader);
          return { models: [] };
        },
        async *infer() {},
      }],
    ]),
  });
  const fixture = await listenInputFixture(server);
  t.after(async () => {
    await closeComponentServer(server);
    await fixture.cleanup();
  });

  const binding = await createUpstreamBinding({
    env: {
      DCOMP_IN_UPSTREAM: fixture.target,
    },
  });
  const controller = new AbortController();
  const poisoned = binding.callOptions(controller.signal, 500);
  poisoned.headers = new Headers({
    authorization: "Bearer caller-secret",
    "x-api-key": "caller-secret",
    cookie: "session=caller-secret",
  });

  const options = binding.callOptions(controller.signal, 500);
  assert.equal(options.signal, controller.signal);
  assert.equal(options.timeoutMs, 500);
  assert.equal(Object.hasOwn(options, "headers"), false);
  await binding.client.listModels({}, options);

  for (const name of ["authorization", "x-api-key", "cookie"]) {
    assert.equal(requestHeaders.get(name), null);
  }
  assert.equal([...requestHeaders.values()].some((value) => value.includes("caller-secret")), false);
});

test("createUpstreamBinding requires a canonical DComp target", () => {
  assert.throws(
    () => createUpstreamBinding({
      env: {
        DCOMP_IN_UPSTREAM: "upstream.sock",
      },
    }),
    /DCOMP_IN_UPSTREAM/u,
  );
  assert.throws(
    () => createUpstreamBinding({ env: {} }),
    /DCOMP_IN_UPSTREAM is empty/u,
  );
});

async function listenInputFixture(server) {
  const root = await mkdtemp(join(tmpdir(), "cyclo-dcomp-input-"));
  const path = join(root, "upstream.sock");
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(path, resolve);
  });
  return {
    target: `unix://${path}`,
    cleanup: () => rm(root, { recursive: true, force: true }),
  };
}
