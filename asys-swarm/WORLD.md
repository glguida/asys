# Runtime world protocol, version 1

A world is a separately running program. It can be a host process or a component;
its implementation language and deployment do not change this protocol.
`asys-runtime` supplies the transport. No method carries code or an import path.

Use one client and one service per channel. Concurrent swarms and replay sessions
need separate runtime roots or channel names. Each channel has these streams:

```text
ROOT/channels/NAME/out/   swarm writes requests; world reads
ROOT/channels/NAME/in/    world writes responses; swarm reads
```

These are ordinary runtime events, with atomic publication, sequence numbers
and acknowledgement cursors. Use the Python or JavaScript runtime channel
library rather than writing event files directly. See the
[runtime channel contract](../asys-runtime/README.md#channels).

## Requests and responses

The swarm publishes a `world.request` event. Its `data` is:

```json
{
  "version": 1,
  "runId": "experiment-1",
  "requestId": "request-1",
  "method": "describe",
  "arguments": [],
  "identity": null
}
```

The world publishes a `world.response` event. Successful description:

```json
{
  "version": 1,
  "runId": "experiment-1",
  "requestId": "request-1",
  "method": "describe",
  "identity": "counter-v1",
  "ok": true,
  "result": {
    "identity": "counter-v1",
    "actionSchema": {
      "type": "object",
      "properties": {"add": {"type": "integer", "minimum": 0, "maximum": 2}},
      "required": ["add"],
      "additionalProperties": false
    }
  }
}
```

Subsequent requests include the discovered implementation identity:

```json
{
  "version": 1,
  "runId": "experiment-1",
  "requestId": "request-2",
  "method": "observe",
  "arguments": [{"total": 3}, "agent-001"],
  "identity": "counter-v1"
}
```

The response repeats the version, run, request and method fields, identifies the
implementation and returns its result. Failure sets `ok: false` and replaces
`result` with `error`:

```json
"error": {"code": "world_error", "message": "The world rejected the supplied state"}
```

Unknown fields and protocol versions are rejected. Run and request IDs follow
runtime name validation. Implementation identities are nonempty strings of at
most 256 characters. They should identify immutable rules and dependencies,
using a version or content digest. The worker also hashes the action schema;
recovery and replay require both identities to match.

## Operations

Arguments are positional JSON arrays:

| Method | Arguments | Result |
| --- | --- | --- |
| `describe` | `[]` | `{identity, actionSchema}` |
| `action_schema` | `[]` | Draft 7 JSON Schema for one action |
| `initialize` | `[settings, participantIds, seed]` | State object |
| `observe` | `[state, participantId]` | Observation object; `active: false` suspends that member |
| `step` | `[state, actionsByParticipant]` | `{state, events}` |
| `evaluate` | `[state, objective]` | `{achieved: boolean, metrics: object, summary: string}` |
| `artifacts` | `[state]` | Exported artifact object |

Only `describe` uses `identity: null`. Other methods require the identity from
the handshake. Schema references must remain local; remote schema references
are forbidden. The world decides action admissibility and deterministic conflict
ordering. The worker validates action structure and commits a transition only
when both transition and evaluation succeed.

World state and any seeded randomness are explicit. Operations must return the
same result for the same input; requests can be repeated after crashes. The
world must not depend on member private memory: that remains inside the swarm
worker and is never part of these requests. No implicit mutable service session
is required to reconstruct a run.

The Python SDK takes an explicitly supplied callbacks object:

```python
Service(runtime_root, rules, identity="counter-v1", channel="world").serve()
```

It retains one bounded response cache for recovery around publication and
acknowledgement. This is not a permanent exactly-once transaction log; keep
operations pure or make external side effects idempotent. A service process
releases its channel ownership lock when it exits.

## Bounds and cancellation

The worker accepts only a reply matching its request ID, run ID and method.
Late replies are acknowledged and ignored. A changed implementation identity,
invalid reply, explicit error or exceeded deadline fails the operation.

On timeout or interruption the client may send `world.cancel` with data:

```json
{"version": 1, "runId": "experiment-1", "requestId": "request-2"}
```

The SDK can skip queued cancelled requests. It does not preempt an already
running callback. The caller stops waiting; a later reply is ignored, and the
launcher can terminate an owned service process. Implementations should keep
callbacks bounded and cancellation independent of successful evaluation.

World state is bounded at 2 MiB, transitions/actions at 4 MiB, artifacts at
1 MiB, action schemas at 128 KiB, and ordinary observations at 256 KiB. Bounds
account for JSON encoding and indentation, with a 7 MiB envelope reserve below
the runtime's 8 MiB event-file ceiling. The two endpoints acknowledge and prune
consumed messages; the worker retains committed checkpoints and replay traces
in its own job storage.
