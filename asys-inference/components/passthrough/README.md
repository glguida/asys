# Pass-through provider component

This provider forwards the model
catalogue, opaque inference request, and streamed opaque responses to one named
upstream:

```text
docker example/passthrough:local
input cyclo.provider.v1.Provider upstream
output cyclo.provider.v1.Provider provider
```

It uses the dcomp 0.3 SDK to connect `DCOMP_IN_UPSTREAM` and serve
`DCOMP_OUT_PROVIDER`; both are proxy-owned Unix sockets carrying ConnectRPC
over HTTP/1.1. It has no private-interface TCP listener.

When added with asys-inference, it serves its catalogue through a filesystem
host channel. Selecting it as `@inference_endpoint` makes
`asys-inference models` list the models from its upstream, including pooled
model IDs. It also retains `GET /health` and `GET /v1/models` on the image's
local port 8080; asys publishes no host TCP port.

The pass-through never parses or reserializes `Infer.payload`. Whitespace,
property order, unknown Pi fields, and future events are preserved as strings.
It forwards no caller HTTP headers and owns no bearer, API key, URL, or model
credential. ConnectRPC propagates streaming, backpressure, cancellation,
and transport errors. The pass-through adds no `Infer` deadline; its bounded
deadline is used only while probing the upstream catalogue for health.

`GET /health` makes a bounded private `ListModels` call and reports only
`ready` or a generic dependency failure.

```sh
make -C ../.. prepare-runtime
npm ci --ignore-scripts
npm test
```

Tests exercise the complete two-component path and assert exact request and
response payload equality, header isolation, cancellation, health recovery,
and shutdown cleanup.
