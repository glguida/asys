import assert from "node:assert/strict";
import test from "node:test";

import {
  Admin,
  AuthenticationMethod,
  Discovery,
  Usage,
} from "@cyclo/gateway-protocol/contract";

test("publishes separate discovery and administration services", () => {
  assert.equal(Discovery.typeName, "cyclo.gateway.v1.Discovery");
  assert.deepEqual(Discovery.methods.map(({ name }) => name), ["ListProviders"]);
  assert.equal(Admin.typeName, "cyclo.gateway.v1.Admin");
  assert.deepEqual(Admin.methods.map(({ name }) => name), [
    "Login",
    "Logout",
    "Rename",
  ]);
  assert.equal(AuthenticationMethod.OAUTH, 1);
  assert.equal(AuthenticationMethod.API_KEY, 2);
  assert.equal(Usage.typeName, "cyclo.gateway.v1.Usage");
  assert.deepEqual(Usage.methods.map(({ name }) => name), ["GetUsage"]);
});
