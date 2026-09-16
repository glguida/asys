# Provider catalogue channel

`asys-inference models` resolves the live `@inference_endpoint` and requests
the catalogue from that provider's host channel. It uses the provider's existing
`ListModels` implementation. The dcomp Provider interface is unchanged.
The gateway uses its existing `gateway` channel; pooler and passthrough use
the `provider` channel described here.

A source opts in with `"host_channel": "provider"` in `runtime.json`.
Asys prepares and mounts a private directory at `/run/asys-host`, containing:

```text
provider.json                 {"version":1,"name":"pool"}
channels/provider/in/         Host requests
channels/provider/out/        Provider replies
```

The name is the logical provider name given to `add`, independent of the
dcomp component prefix. The pooler uses it as the prefix for its added models.

The streams use the [runtime channel protocol](../../asys-runtime/README.md#channels).
The component consumes `in`; the host consumes `out`. The CLI serializes
requests with its machine lock. Each reader advances and prunes its own stream.
Events have these types and data:

| Direction | Type | Data |
| --- | --- | --- |
| Out | `ready` | `{ "instance": "INSTANCE" }` |
| In | `models` | `{ "instance": "INSTANCE" }` |
| Out | `result` | `{ "instance": "INSTANCE", "request": SEQUENCE, "result": { "models": [...] } }` |
| Out | `error` | `{ "instance": "INSTANCE", "request": SEQUENCE, "message": "..." }` |

The component publishes `ready` with a fresh instance ID before reporting
healthy. Every reply carries that ID; `request` is the input event's sequence.
The host obtains the current ID from the last output event and starts reading
after that event. A restart changes the ID, rejects queued requests for the
previous instance, and makes any waiting host command fail with a retry message.

The bundled server bounds `ListModels` calls to ten seconds; the host waits
up to twenty seconds for a reply. Shutdown cancels the call. A failed catalogue
request returns an error and leaves the channel available for the next request.

The JSON catalogue preserves model IDs, capabilities and metadata. Token
counts are decimal strings. Protobuf `Any` extensions remain explicit
`{ "typeUrl": "...", "value": "BASE64" }` pairs, including unknown types.
`models --json` returns this document; `models` prints its IDs, one per line.
The shared implementation is `@cyclo/provider/host-channel`.
