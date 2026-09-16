import assert from "node:assert/strict";
import test from "node:test";

import { Provider } from "../../provider/gen/cyclo/provider/v1/provider_pb.js";
import { registerProvides, resolveBindings } from "../src/bindings.mjs";
import { parseDeclaration } from "../src/declaration.mjs";

test("resolves provided handlers and required clients from one descriptor", () => {
  const declaration = parseDeclaration(`
    component provider-proxy
    provide cyclo.provider.v1.Provider
    require upstream cyclo.provider.v1.Provider
  `);

  const bindings = resolveBindings(declaration, [Provider]);

  assert.equal(bindings.provides.get(Provider.typeName), Provider);
  assert.equal(bindings.requires.get("upstream"), Provider);
});

test("fails when generated descriptors do not satisfy the declaration", () => {
  const declaration = parseDeclaration(`
    component incomplete
    provide cyclo.provider.v1.Provider
    require models cyclo.models.v1.Catalog
  `);

  assert.throws(
    () => resolveBindings(declaration, [Provider]),
    /unknown required interface cyclo\.models\.v1\.Catalog/u,
  );
});

test("a provided interface must implement every RPC", () => {
  const declaration = parseDeclaration(`
    component incomplete
    provide cyclo.provider.v1.Provider
  `);
  const bindings = resolveBindings(declaration, [Provider]);
  const router = { service() { assert.fail("incomplete service was registered"); } };

  assert.throws(
    () => registerProvides(router, bindings, new Map([[Provider.typeName, {}]])),
    /missing implementation for cyclo\.provider\.v1\.Provider\/ListModels/u,
  );
});

test("all provided interfaces are validated before any is registered", () => {
  const Other = {
    typeName: "example.v1.Other",
    methods: [{ localName: "run", name: "Run" }],
  };
  const declaration = parseDeclaration(`
    component atomic
    provide cyclo.provider.v1.Provider
    provide example.v1.Other
  `);
  const bindings = resolveBindings(declaration, [Provider, Other]);
  let registrations = 0;
  const router = { service() { registrations += 1; } };

  assert.throws(
    () =>
      registerProvides(
        router,
        bindings,
        new Map([
          [Provider.typeName, { listModels() {}, async *infer() {} }],
          [Other.typeName, {}],
        ]),
      ),
    /missing implementation for example\.v1\.Other\/Run/u,
  );
  assert.equal(registrations, 0);
});
