# Cyclo components

This directory is Cyclo's shared component core. It validates declarations,
constructs ConnectRPC servers and clients, adapts them to DComp 0.2 sockets,
and provides small helpers for component-owned host HTTP APIs.

A component is a program with a `component.conf`. A Protobuf service is an
interface, and its fully qualified name is the interface identity. ConnectRPC
generates the HTTP handler and client contract from that service. Cyclo uses
the Connect protocol over HTTP/1.1; gRPC and gRPC-Web are not part of this
component contract.

Private interfaces do not listen on a TCP port. DComp gives a linked input
named `upstream` a target such as
`DCOMP_IN_UPSTREAM=unix:///run/dcomp/in/upstream`, and gives a producer
its corresponding `DCOMP_OUT_*` socket. `createDCompTransport()` uses the
official `@dcomp/component` input options; `serveComponentOutput()` feeds
claimed output connections into the native ConnectRPC HTTP server.

```text
.proto service
    |
    +-- provide SERVICE       register a complete generated handler
    |
    `-- require NAME SERVICE  construct a generated client
```

Host reachability is deliberately separate. A component that needs to be
called from outside the DComp graph owns an ordinary REST API, normally with
`GET /health`. The `HealthStatus` enum is a shared internal health result
vocabulary, but built-in declarations expose only domain interfaces that
another component actually consumes.

## Declaration

```text
component passthrough
provide cyclo.provider.v1.Provider
require upstream cyclo.provider.v1.Provider
```

`require` has a local name because a component may need the same interface
more than once. The declaration contains no URL, target, container, discovery,
or routing policy. The host configuration binds requirement names to component
endpoints.

Unknown directives, duplicate names, malformed interface names, and interfaces
absent from the compiled schema fail validation. A
provided interface is also rejected at startup unless every RPC has an
implementation; Connect's permissive unimplemented-method fallback is not used.

## Interface packages

Interfaces are ordinary versioned packages, not entries in a Cyclo registry.
An interface package contains its `.proto` source, generated descriptors, and
`schema.json`. A component imports the generated descriptors it implements or
calls, then validates its declaration against all installed schemas:

```sh
npx cyclo-component-check component.conf \
  node_modules/@cyclo/component/gen/schema.json \
  node_modules/example-domain/gen/schema.json
```

This package exports its base descriptor as `@cyclo/component/contract` and
includes both `proto/` and `gen/` when installed from a local path or tarball.
Domain interfaces use the same layout. The sibling `../provider` package owns
`cyclo.provider.v1.Provider` while using this package for declaration and
binding machinery. The `../../gateway` program is the root component built
from both interfaces. There is no central runtime discovery service.

## Build and test

Node.js 20 or newer is required for the current ConnectRPC toolchain.

```sh
npm ci
npm test
npm run check -- test/fixtures/valid.conf gen/schema.json
```

Buf lints the contracts, generates native ESM plus declarations in `gen/`, and
emits `gen/schema.json` for language-neutral declaration validation. The tests
exercise generated Connect handlers and DComp socket adapters.

This package deliberately owns only the common Component result schema and
transport helpers. Cyclo's Provider control plane and opaque Pi transport live in the sibling
`../provider` package. It
defines catalogue and inference semantics explicitly rather than disguising a
native model API as `path + headers + bytes` inside Protobuf.
