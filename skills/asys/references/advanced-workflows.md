# Advanced workflows and integration

Use these features when a fixed sequence or ordinary goal job cannot express
the task cleanly. Examples below are fragments to place inside a complete BPMN
document with the usual namespaces and sequence flows.

## Multi-instance and standard loops

For a known collection, bind each task to its named input item:

```xml
<bpmn:task id="review_parts" name="Review each part">
  <bpmn:extensionElements>
    <asys:job type="reviewer" input='= {prompt: "Review part " + part.name}'/>
  </bpmn:extensionElements>
  <bpmn:multiInstanceLoopCharacteristics isSequential="false">
    <bpmn:loopDataInputRef>parts</bpmn:loopDataInputRef>
    <bpmn:inputDataItem id="part" name="part"/>
  </bpmn:multiInstanceLoopCharacteristics>
</bpmn:task>
```

`parts` must already be a collection variable; Markdown `request` is not such a
collection. Produce it with a preceding job/result or explicit data mapping.
Each instance gets a fresh job and the same workspace. Results are collected in
collection order. `isSequential="true"` serializes instances; use it for writes
to shared files. Parallel instance creation is not a configured worker pool.

Cardinality and early completion use standard fields, for example:

```xml
<bpmn:multiInstanceLoopCharacteristics isSequential="true">
  <bpmn:loopCardinality xsi:type="bpmn:tFormalExpression">10</bpmn:loopCardinality>
  <bpmn:completionCondition xsi:type="bpmn:tFormalExpression">
    numberOfCompletedInstances = 2
  </bpmn:completionCondition>
</bpmn:multiInstanceLoopCharacteristics>
```

A standard loop can use `loopCounter`:

```xml
<bpmn:standardLoopCharacteristics testBefore="true" loopMaximum="10">
  <bpmn:loopCondition xsi:type="bpmn:tFormalExpression">
    loopCounter &lt; count
  </bpmn:loopCondition>
</bpmn:standardLoopCharacteristics>
```

Set counters/conditions for the actual process; these illustrative bounds are
not goal defaults. A gateway back to a task is often clearer for review/repair
because it shows the decision explicitly.

## Ad-hoc coordinator

An `adHocSubProcess` has its own job binding for the coordinator and ordinary
bound activities inside it:

```xml
<bpmn:adHocSubProcess id="team" name="Coordinate module work"
  ordering="Parallel" cancelRemainingInstances="false">
  <bpmn:extensionElements>
    <asys:job type="coordinator"
      input='= {prompt: "Inspect the request, list available actions, start design_module for each required module, and wait for their results. Check the resulting files. Request: " + request}'/>
  </bpmn:extensionElements>
  <bpmn:task id="design_module" name="Design a module">
    <bpmn:extensionElements>
      <asys:job type="implementer"
        input='= {prompt: "Design the module " + message.module.name + " in its own module directory."}'/>
    </bpmn:extensionElements>
  </bpmn:task>
</bpmn:adHocSubProcess>
```

The coordinator's environment command must explicitly select the shipped
`/opt/asys/asys-workers/extensions/bpmn.mjs` extension. Its tools are:

- `list_actions`: inspect activities, eligibility, and referenced `message`
  input paths.
- `start_action`: select an activity with an input object, for example
  `{"action":"design_module","input":{"module":{"name":"power"}}}`.
- `wait_action`: wait for the selected invocation using the returned handle
  and the tool's advertised schema.

The input object becomes `message` unchanged. Do not wrap it again in an
`input` or `message` field unless the binding explicitly expects that nesting.
Directly bound actions reject invalid expression types/argument arrays before
starting a child, so the coordinator can correct the call.

Activities with no incoming flows start enabled. Sequence flows can make
children depend on one another, but the coordinator still selects each enabled
child explicitly. Sequential ordering allows one active selection. Parallel
ordering allows different activities together. **Concurrent invocations of
the same activity are rejected as busy**: in the example above, wait for one
`design_module` before selecting it again, or model a multi-instance task for
the collection.

Without a completion condition, coordinator completion requests the end of the
subprocess. A supplied completion condition is evaluated on child completion.
`cancelRemainingInstances="false"` waits for active parallel work; otherwise
remaining work can be cancelled on completion. The subprocess result contains
inner outputs and the coordinator result under `result`.

## Data mapping and called processes

Use standard BPMN `ioSpecification`, `dataInputAssociation`, and
`dataOutputAssociation` to map values between task I/O and data objects.
FEEL transformations are supported, including multiple sources. A mapping is
JSON/business data transfer, not a filesystem artifact publication mechanism.
Files remain in the shared workspace.

A `callActivity` uses `calledElement="PROCESS_ID"`. The called process must be
present in the loaded document and uses the same worker environment. A dynamic
FEEL process selection conservatively requires worker types for every possible
process in the document. It does not dynamically launch another environment.

Prefer a result binding plus explicit next-task input for simple handoff; use
standard associations when they make an existing BPMN model clearer. Check a
complete association example against the installed engine before building a
large data model around it.

## Messages, timers, and waiting

An unbound receive task waits without consuming an agent process. Messages and
signals arrive through the Workflow component API or the host channel. Timers
can bound a wait or interrupt an activity. These are workflow semantics;
`asys-human` is a separate waiting **job** using the Human interface.

There is no public `asys-bpmn message` command. Embedding clients use
`SendMessage`, or publish a `message` request through the runtime channel. Use
the installed runtime channel library rather than inventing filenames or
writing incomplete queue entries by hand.

## Embed the engine or supply another producer

The workflow component exports `asys.workflow.v1.Workflow` as `workflow`.
Operations include:

| Operations | Purpose |
| --- | --- |
| `LoadWorkflow`, `ListWorkflows`, `Describe` | Load/inspect preserved BPMN definitions |
| `ListEnvironments`, `CheckWorkflow` | Inspect registered type declarations and compatibility |
| `StartRun`, `GetRun`, `ListRuns` | Create/read runs |
| `ResumeRun`, `CancelRun` | Recover failure or cancel outstanding work |
| `SendMessage` | Deliver messages/signals to waiting execution |
| `GetEvents` | Read the durable sequenced event journal |

A component client declares an input of that type and links it to the workflow
output. It owns its own dcomp input; it does not open another component's
private sockets. Definitions are content-addressed. Caller-chosen run/message
IDs are idempotency keys; conflicting reuse is rejected.

The host launcher instead uses `RUN/runtime/channels/workflow`. Requests on
`in` include:

| Request | Principal data fields |
| --- | --- |
| `start` | `id`, `bpmnXml`, `processId`, `environment`, `variables`; optional expected `environmentDefinition` hash |
| `resume` | `id` |
| `cancel` | `id` |
| `message` | `runId`, `target`, `id`, `payload` |

Acknowledgements on `out` are `accepted`/`rejected` correlated with the request
sequence. Committed events carry run/activity identity, time, event data and
store sequence; terminal events are followed by `run.result`. The saved run
record includes the channel path. Use the library's envelope/sequence contract;
the table describes request data, not an arbitrary standalone message file.

The engine submits ordinary runtime jobs to the chosen environment. Other
producers, including one-shot and goal launchers, can submit directly without
BPMN. A producer prepares a job directory and workspace reference, submits type,
args/input/metadata, waits for terminal state, and decides whether to retry.
Retry is a new job; runtime does not reexecute terminal jobs automatically.

## Known boundaries

- Parsing/preserving BPMN does not promise full BPMN execution conformance.
- External imports must be resolved into the loaded document.
- Data-association assignment blocks are rejected; use FEEL transformations.
- Multi-instance event behavior other than `All` is rejected.
- Execution extensions other than the supported asys binding are rejected.
- An executing ordinary task requires a binding even if it has a script body.
- Ad-hoc repeated selection is supported after completion, not simultaneous
  instances of the same inner activity.

When extending beyond the recipes, inspect the installed/source BPMN binding,
engine tests and component contracts for that version. Do not substitute a
different BPMN engine's dialect or claim an unsupported host CLI option.
