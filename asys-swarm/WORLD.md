# Runtime world protocol, version 1

A world package supplies a separate dcomp component implementing this interface.
Its implementation language does not change the runtime protocol.
`asys-runtime` supplies the transport. No method carries code or an import path.

Each job uses one isolated session channel. The component SDK multiplexes these
sessions; each has one client and one service consumer. Each channel has these streams:

```text
ROOT/channels/NAME/out/   swarm writes requests; world reads
ROOT/channels/NAME/in/    world writes responses; swarm reads
```

These are ordinary runtime events, with atomic publication, sequence numbers
and acknowledgement cursors. Use the Python or JavaScript runtime channel
library rather than writing event files directly. See the
[runtime channel contract](../asys-runtime/README.md#channels).

The host supplies a protected package binding to workers and mounts only that
package's runtime subtree into its component at `/var/lib/asys-world`. Session
channels are named from the ordinary runtime job ID. `Component` publishes
`ready.json` with `protocolVersion: 1` and implementation `identity`, and maintains
the image health marker. Components can serve independent sessions concurrently;
operations within each swarm remain synchronous.

Requests and results are JSON data. Larger files can live within the shared
world runtime subtree and travel as explicitly validated relative references.
The world implementation defines the artifact schema and must constrain paths
to its shared storage; a reference is not permission to read an arbitrary host
file. Private member memory and worker job directories are not mounted there.

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
from asys_swarm.world_service import Component

Component("/var/lib/asys-world", rules, identity="counter-v1").serve()
```

Each session retains one bounded response cache for recovery around publication and
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
running callback. The caller stops waiting; a later reply is ignored. The
enclosing deployment owns the shared component's lifetime. Implementations should keep
callbacks bounded and cancellation independent of successful evaluation.

World state is bounded at 2 MiB, transitions/actions at 4 MiB, artifacts at
1 MiB, action schemas at 128 KiB, and ordinary observations at 256 KiB. Bounds
account for JSON encoding and indentation, with a 7 MiB envelope reserve below
the runtime's 8 MiB event-file ceiling. The two endpoints acknowledge and prune
consumed messages; the worker retains committed checkpoints and replay traces
in its own job storage.
