# BPMN authoring

## Start from a complete document

Use `assets/team/workflow.bpmn` for implement/review/revise, or
`assets/goal.bpmn` for a goal inside a workflow. Both include BPMN Diagram
Interchange (DI), so the XML is also a displayable diagram. Copy the asset and
change its process, bindings, and layout together.

The essential namespaces and executable process declaration are:

```xml
<bpmn:definitions
  xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xmlns:asys="urn:asys:workflow:1"
  id="DeliveryWorkflow" name="Delivery workflow"
  targetNamespace="urn:example:delivery" expressionLanguage="feel">
  <bpmn:process id="delivery" isExecutable="true">
    <!-- start, activities, sequence flows, and end -->
  </bpmn:process>
</bpmn:definitions>
```

Use stable unique IDs and meaningful names. IDs identify variables and repeated
executions; names appear in progress and the monitor. Diagram coordinates do not
establish execution order. Sequence flows do. An ordinary editor can preserve
`asys:job` extensions, but check exported XML still contains the bindings.

An executing task needs an explicit job binding. Its BPMN kind does not select
a worker automatically. An unbound `receiveTask` is the exception: it waits for
a message. A subprocess groups execution; a call activity selects a process
inside the same document. See the advanced reference for those cases.

## Bind a task to a job type

```xml
<bpmn:serviceTask id="draft" name="Write the report">
  <bpmn:extensionElements>
    <asys:job type="implementer"
      input='= {prompt: "Write deliverables/report.md for this request: " + request}'
      args="= []" result="draft"/>
  </bpmn:extensionElements>
</bpmn:serviceTask>
```

| Binding attribute | Contract | Default |
| --- | --- | --- |
| `type` | Declared key in `workers.json.types` | Required |
| `input` | FEEL expression producing the worker's JSON assignment | `= variables` |
| `args` | FEEL expression producing an array of strings appended to the command | `= []` |
| `result` | Variable storing the parsed job result | Activity ID |

`input` and `args` must begin with `=`. There is one `asys:job` per bound
activity. The type identifies a program, not an agent's model. Compatibility
checks ensure required types are declared for reachable processes; they do not
check that executables, model accounts, or project tools actually work.

Avoid reserved result names such as `variables`, `output`, `message`, `item`,
and `index`. Use ordinary names like `draft`, `review`, and `checks`.

## Request text and FEEL values

```sh
asys-bpmn run workflow.bpmn env/development --input request.md --workspace ./project
```

`request.md` becomes the literal string `request`. It is not decoded as JSON.
Use it in `prompt`/`goal`, or use a worker program to parse a structured project
file explicitly. No public `--variables` option is required or implied.

Workflow variables are accessible directly and as `variables.NAME`. The
expression context also supplies `output`, `message`, task `inputs`, `task`
(the parsed BPMN activity), multi-instance `item`/`index`, and loop counters
where applicable. `message` is especially useful for ad-hoc action input.

| Intent | FEEL example |
| --- | --- |
| Agent assignment | `= {prompt: request}` |
| Goal assignment | `= {goal: request}` |
| Command arguments | `= ["python3", "/opt/asys/environment/programs/check.py"]` |
| Compare a boolean | `review.approved = true` |
| Missing prior feedback on the first pass | `if review = null then "First pass" else review.reason` |
| Small structured handoff | `= {path: draft.path, required: ["overview", "evidence"]}` |

Use FEEL `=`, `and`, `or`, `not(...)`, `if ... then ... else ...`, context
literals, and lists; do not write JavaScript `===`, `&&`, template strings, or
Python expressions. Conditions must produce a boolean. Missing fields can yield
`null`, so handle optional values before concatenating or doing arithmetic.
Lists in FEEL use one-based indexing.

For XML attributes, use single quotes around FEEL containing double-quoted
strings, or escape them with `&quot;`. Escape XML `<` and `&` as `&lt;` and
`&amp;`. In script bodies, CDATA is useful for Python/shell text.

## Agent assignments and results

Specify inputs, deliverables, acceptance checks, and structured output:

```xml
<asys:job type="reviewer" input='= {
  prompt: "Inspect deliverables/report.md against this request. Check actual sources. Return approved as a boolean and reason as concrete findings, alongside final and exception. Request: " + request
}'/>
```

The agent's final assistant response is JSON, for example:

```json
{
  "final": "Inspected the report; its latency claim has no supporting measurement.",
  "exception": null,
  "approved": false,
  "reason": "Add the measured latency and its command/output under Evidence."
}
```

With result variable `review`, a gateway can read `review.approved`. The worker
requires `final` and `exception`; fields such as `approved`, `reason`,
`review_summary`, and `review_files` are supplied because the assignment asks
for them, not generated by the runtime. Define or validate their contracts when
downstream routing depends on them. A generic `success` flag is not interpreted.

Keep large artifacts and exact data in workspace files. Result JSON should
contain the fields needed for routing and handoff, not a full transcript.

## Programs and inline scripts

Bind a fixed validation type when the environment owns the check command:

```xml
<bpmn:serviceTask id="checks" name="Check the artifact">
  <bpmn:extensionElements>
    <asys:job type="check" input='= {path: "deliverables/report.md"}'/>
  </bpmn:extensionElements>
</bpmn:serviceTask>
```

With `type="program"` configured to `asys-program`, provide the full command
vector through `args`:

```xml
<asys:job type="program"
  args='= ["python3", "/opt/asys/environment/programs/check.py"]'
  input='= {path: "deliverables/report.md"}'/>
```

A tiny program can live in a script task. The BPMN body is passed to the worker;
the engine does not execute Python itself:

```xml
<bpmn:scriptTask id="count_lines" name="Measure report" scriptFormat="python">
  <bpmn:extensionElements>
    <asys:job type="program" args='= ["python3", "-c", task.script]'/>
  </bpmn:extensionElements>
  <bpmn:script><![CDATA[
import json, os
from pathlib import Path
report = Path("deliverables/report.md")
Path(os.environ["ASYS_RESULT"]).write_text(json.dumps({
    "lines": len(report.read_text(encoding="utf-8").splitlines())
}))
]]></bpmn:script>
</bpmn:scriptTask>
```

Use named programs in the image once scripts become substantial. Shell
redirection/pipes only work if the argument vector explicitly invokes a shell.
Write result JSON to `ASYS_RESULT`, not stdout. Missing files or failed checks
should fail visibly, not silently produce a replacement artifact.

## Sequence, branch, repeat

Connect tasks with `sequenceFlow` elements:

```xml
<bpmn:sequenceFlow id="draft_to_review" sourceRef="draft" targetRef="review"/>
<bpmn:sequenceFlow id="review_to_decision" sourceRef="review" targetRef="decision"/>
<bpmn:exclusiveGateway id="decision" default="revise"/>
<bpmn:sequenceFlow id="accept" sourceRef="decision" targetRef="end">
  <bpmn:conditionExpression xsi:type="bpmn:tFormalExpression" language="feel">
    review.approved = true
  </bpmn:conditionExpression>
</bpmn:sequenceFlow>
<bpmn:sequenceFlow id="revise" sourceRef="decision" targetRef="draft"/>
```

`default` names the **sequence flow ID**, not its target task. Returning to a
task creates a fresh job/session. The workspace retains the previous attempt's
files; pass review findings deliberately in the next assignment. The starter
handles absent review feedback on the first pass.

Use a parallel gateway to split independent work and another to join it before
assembly. An exclusive gateway does not wait for all branches. All jobs still
share files: assign distinct paths to concurrent writers or serialize them.

Completing a review with `approved: false` is normal execution and should route
through a gateway. It does not need a BPMN error. Human disapproval behaves the
same way: the question was successfully answered.

## Catch execution failure

A program's nonzero exit becomes an activity error whose code is its exit code.
An agent's non-null `exception` exits 1. To handle that case, declare an error
under definitions and attach a boundary event inside the process:

```xml
<!-- Under definitions: -->
<bpmn:error id="AgentException" errorCode="1"/>

<!-- Inside the process; draft is an existing task: -->
<bpmn:boundaryEvent id="draft_failed" attachedToRef="draft">
  <bpmn:errorEventDefinition errorRef="AgentException"/>
</bpmn:boundaryEvent>
<bpmn:sequenceFlow id="explain_failure" sourceRef="draft_failed" targetRef="help"/>
<bpmn:userTask id="help" name="Resolve the draft blocker">
  <bpmn:extensionElements>
    <asys:job type="human" input='= {
      title: "Draft blocked",
      prompt: "The drafting job could not complete. " + draft.exception + ". Describe the correction or missing information needed before it runs again.",
      summary: draft.final
    }'/>
  </bpmn:extensionElements>
</bpmn:userTask>
```

This is a fragment: connect `help` onward and explicitly pass its answer into
the retry prompt. Expand the briefing with work/evidence/choices as described in
the human reference. A boundary error with no `errorRef` catches any attached
activity error. A validator exiting 17 can instead have a specific error with
`errorCode="17"`.

The failed task's result variable retains its structured result when one exists,
including `exception`. If the process failed before producing JSON, do not assume
agent-specific fields exist; handle missing values or use a diagnostic program
to prepare the request. Unhandled failures terminate the run. BPMN resume is
an explicit recovery operation; it is not a replacement for designed retry paths.

## Human decisions and goal tasks

Human input is an object with required `prompt` and optional briefing/form
fields. Its answer becomes the task's result variable. Describe choice
consequences, wire every advertised choice, and ask the preceding worker for
`review_summary`/`review_files` when they are needed. See the human reference
for a complete JSON example and gateway contract.

For a goal, the engine simply submits one ordinary job:

```xml
<bpmn:task id="deliver" name="Implement and verify">
  <bpmn:extensionElements>
    <asys:job type="goal" input="= {goal: request}"/>
  </bpmn:extensionElements>
</bpmn:task>
```

The goal worker owns implementation/verification cycles and human escalation.
Do not add an arbitrary attempt limit. A requested limit is
`input="= {goal: request, maxAttempts: 5}"`, or a worker command option.
Success returns `verified: true`; a stopped/failed goal is an ordinary job
failure and can be caught with a boundary error.

## Validate what you authored

1. Check namespaces, unique IDs, executable process, reference targets, job
   types, FEEL syntax, result names, and diagram coverage.
2. Run a deterministic environment in a disposable workspace. Exercise positive
   and negative decisions, repetition, and any intended failure handler.
3. Inspect result JSON and actual files, not only stage completion messages.
   Check that no parallel consumer starts before its files are ready.
4. Verify real tools, agent prompts, and human briefing/choice behavior with
   relevant inputs. Record which parts remain untested.

The public launcher runs/resumes workflows; it does not have a separate
`validate` or `check` CLI command. The component API has `CheckWorkflow` for
type compatibility. BPMN XML support is bounded by the installed engine; a
diagram that parses is not proof that every BPMN standard feature executes.
