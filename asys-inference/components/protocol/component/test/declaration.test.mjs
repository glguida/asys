import assert from "node:assert/strict";
import test from "node:test";

import {
  DeclarationError,
  parseDeclaration,
  validateInterfaces,
} from "../src/declaration.mjs";
import { servicesFromDescriptorSet } from "../src/schema.mjs";

const EXAMPLE_INTERFACE = "example.v1.Primary";

test("parses provided interfaces and named requirements", () => {
  const declaration = parseDeclaration(`
    # Interface direction is explicit.
    component health-proxy
    provide ${EXAMPLE_INTERFACE}
    require upstream ${EXAMPLE_INTERFACE}
  `);

  assert.deepEqual(declaration, {
    name: "health-proxy",
    provides: [EXAMPLE_INTERFACE],
    requires: [{ name: "upstream", service: EXAMPLE_INTERFACE }],
  });
});

test("does not impose a lifecycle interface on the DComp graph", () => {
  assert.deepEqual(
    parseDeclaration("component terminal\nrequire api example.v1.Other\n"),
    {
      name: "terminal",
      provides: [],
      requires: [{ name: "api", service: "example.v1.Other" }],
    },
  );
});

test("rejects duplicate requirement names", () => {
  assert.throws(
    () =>
      parseDeclaration(`
        component duplicate
        provide ${EXAMPLE_INTERFACE}
        require upstream example.v1.First
        require upstream example.v1.Second
      `),
    /duplicate requirement name upstream/u,
  );
});

test("rejects unknown directives", () => {
  assert.throws(
    () => parseDeclaration(`component bad\nprovide ${EXAMPLE_INTERFACE}\nport 8080\n`),
    /unknown directive port/u,
  );
});

test("validates declarations against compiled services", () => {
  const declaration = parseDeclaration(`
    component unknown
    provide ${EXAMPLE_INTERFACE}
    require upstream example.v1.Missing
  `);

  assert.throws(
    () => validateInterfaces(declaration, new Set([EXAMPLE_INTERFACE])),
    /unknown required interface example\.v1\.Missing/u,
  );
});

test("extracts fully-qualified services from descriptor JSON", () => {
  const services = servicesFromDescriptorSet({
    file: [
      { package: "example.v1", service: [{ name: "Primary" }] },
      { package: "example.v1", service: [{ name: "Echo" }] },
    ],
  });

  assert.deepEqual(services, new Set([EXAMPLE_INTERFACE, "example.v1.Echo"]));
});
