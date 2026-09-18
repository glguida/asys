# Engine adapter rules and dependency contracts

Register constructors with the `TypeResolver` extender, supply job services
through `behaviour.Service`, and use the environment's expression, script,
and extension interfaces. Never replace methods, define properties, or edit
prototypes on objects constructed by `bpmn-elements`.

Adapt serialized definition data only in `adaptDefinition` in
[`src/engine.mjs`](src/engine.mjs). Each transformation must have a dependency
contract test and a test of the resulting asys behavior. The preserved BPMN
artifact is independent of this executable projection.

Broker messages remain a dependency even with registered constructors. Pin
their routing keys, content fields, and recovery behavior with tests rather
than treating them as version-independent guarantees.

## Inventory

| Dependency or adaptation | Reason | Contract |
| --- | --- | --- |
| Registered loop constructors | Strict cardinality and array checks using retained constructor arguments, without reading parent instance fields | P1, adapter tests |
| Initial test-before completion | Native sequential loops leave an initially stopped loop open | P2, P3 |
| `fields.redelivered` | Do not replace recovery of an existing iteration with empty completion | P4 |
| Ad-hoc coordinator and synthetic inbound flows | Native ad-hoc scopes are ordinary subprocesses; inbound flows keep children from starting automatically | P5, adapter and workflow tests |
| Registered ad-hoc flow `take`/`discard` | Intercept only flows into selectable actions; gateways and events retain native token delivery and discard propagation | P5, workflow join/event/recovery/condition/discard tests |
| Native compensation associations | Keep compensation handlers idle until triggered; reject unconnected ad-hoc handlers before execution | P14, adapter and workflow recovery tests |
| Remove serialized I/O/properties | Native BpmnIO consumes the serializer's single-source projection; asys handles preserved associations | P6 |
| `activity.run(content)` | Carry selected input and coordinator/action identity into execution | P7 |
| Normalize language-less conditional expressions | The serializer emits bare bodies; asys expressions need `=` | P8 |
| Conditional `scripts.register` result | Evaluate explicit FEEL scripts without converting them to expressions | P8s |
| Multi-instance execution events | Track iteration counts and standard-loop results; exclude the root scope | P9 |
| Negate standard-loop conditions | Native conditions mean stop, BPMN conditions mean continue | P10 |
| Normalize loop expressions and map collection/item | Native loops consume expressions, collection, and elementVariable | P11 |
| Normalize flow condition language | Route conditions through the FEEL script provider | P12 |
| Active selection discard events | Mark cancelled selections failed so the coordinator can continue | P13 |

The post-serialization I/O removal is retained in the centralized adapter
and pinned by P6. Explicit conditional scripts are no longer rewritten.
Synthetic inbound flows only suppress automatic starts; coordinator completion
does not deliver them. Intercepted flows deliberately keep their native
`take`/`discard` counters at zero in saved state. Those counters do not measure
coordinator selections; use the controller's action records and activity events.
Only selectable work is intercepted, using the same action list as the
coordinator. Gateways and events execute normally between selections, including
joins and message waits across recovery. Compensation handlers require an
association from a compensation catch event in the same ad-hoc scope, because
native handlers without inbound associations would start on scope entry.
No instance-wrapper fallback is used. The separate moddle XML reader adaptation
is outside this rule's `bpmn-elements` scope.

## Upgrading dependencies

`test/engine-contract.test.mjs` exercises the libraries directly, without the
asys runtime. P2 deliberately fails if upstream fixes the empty-loop defect:
remove the workaround and update that pin after verifying the fixed behavior.
`test/engine.test.mjs` checks the adapter; `test/workflow.test.mjs` checks real
filesystem jobs, selection, cancellation, and recovery.

After installing the normal development dependencies, run:

```sh
make -C asys-bpmn test-upstream-drift
make -C asys-bpmn test-upstream-drift BPMN_ELEMENTS=17.3.0 MODDLE_CONTEXT_SERIALIZER=4.4.1
```

The check installs the requested dependencies with `npm install --no-save`
in a temporary copy and runs all three suites. It leaves the checkout's
lockfile and installed dependencies untouched. CI checks both pinned and
latest dependencies; the latest-dependency job is advisory and runs weekly
as well as on relevant pull requests.

The adapter identity is `bpmn-elements@17.3.0+asys.2`. Synthetic flows change
the checkpoint topology, so recovery/resume rejects records from the old
adapter. Keep the previous installation to finish old runs, or start new
runs with the new version. An upstream-drift pass does not change that
identity or authorize recovery across adapter versions.
