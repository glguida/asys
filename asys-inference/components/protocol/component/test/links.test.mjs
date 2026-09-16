import assert from "node:assert/strict";
import test from "node:test";

import { inputEnv, inputHttpOptions, inputPath } from "@dcomp/component";

import { createDCompTransport } from "../src/transport.mjs";

test("component inputs use the DComp 0.2 SDK environment", () => {
  const env = {
    DCOMP_IN_UPSTREAM: "unix:///run/dcomp/in/upstream",
    DCOMP_IN_MODEL_POOL: "unix:///run/dcomp/in/model-pool",
  };
  assert.equal(inputEnv("upstream"), "DCOMP_IN_UPSTREAM");
  assert.equal(inputPath("model-pool", env), "/run/dcomp/in/model-pool");
  assert.deepEqual(inputHttpOptions("upstream", env), {
    socketPath: "/run/dcomp/in/upstream",
  });
  const transport = createDCompTransport("upstream", env);
  assert.equal(typeof transport.unary, "function");
  assert.equal(typeof transport.stream, "function");
});

test("component input names and Unix targets fail closed", () => {
  for (const name of ["", "UPSTREAM", "../upstream", "up_stream"]) {
    assert.throws(() => createDCompTransport(name, {}), /interface name/u);
  }
  assert.throws(
    () => createDCompTransport("upstream", {}),
    /DCOMP_IN_UPSTREAM is empty/u,
  );
  for (const value of [
    "",
    " unix:///run/dcomp/in/upstream",
    "unix://host/run/dcomp/in/upstream",
    "unix:///run/dcomp/../upstream.sock",
    "https://component.invalid/upstream",
  ]) {
    assert.throws(
      () => createDCompTransport("upstream", { DCOMP_IN_UPSTREAM: value }),
      /DCOMP_IN_UPSTREAM/u,
    );
  }
});
