# Workflows

Execution order comes from BPMN sequence flows. Diagram coordinates affect
presentation only. Use stable IDs for variables and meaningful names for people.
The dashboard lays out the saved graph, including forks and return paths.

## Complete minimal document

```xml
<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xmlns:asys="urn:asys:workflow:1" id="Delivery"
  targetNamespace="urn:example:delivery" expressionLanguage="feel">
  <bpmn:process id="delivery" isExecutable="true">
    <bpmn:startEvent id="start"/>
    <bpmn:serviceTask id="deliver" name="Implement and verify">
      <bpmn:extensionElements>
        <asys:job type="repair" input="= {request: request}"/>
      </bpmn:extensionElements>
    </bpmn:serviceTask>
    <bpmn:endEvent id="done"/>
    <bpmn:sequenceFlow id="begin" sourceRef="start" targetRef="deliver"/>
    <bpmn:sequenceFlow id="finish" sourceRef="deliver" targetRef="done"/>
  </bpmn:process>
</bpmn:definitions>
```

Declare `repair` in the environment, then run:

```sh
asys-run ./env/development ./workflow.bpmn --input request.md --workspace ./project
```

The Markdown file becomes the literal string `request`. It does not load JSON
variables. `--process ID` selects an executable process when necessary.

## Job bindings

| Attribute | Meaning | Default |
| --- | --- | --- |
| `type` | Exact `workers.json.types` key | Required |
| `input` | FEEL expression producing assignment JSON | `= variables` |
| `args` | FEEL expression producing strings appended to the command | `= []` |
| `result` | Variable receiving parsed job output | Activity ID |

Each executing task needs an explicit binding. The task's BPMN type does not
select a worker kind automatically. An unbound receive task waits for a message.
Keep result names distinct from reserved context names such as `variables`,
`output`, `message`, `item` and `index`.

Examples:

```xml
<asys:job type="editor" input='= {request: "Edit the report. Request: " + request}'/>
<asys:job type="repair" input='= {request: request, parameters: {maxAttempts: 3}}'/>
<asys:job type="review" input='= {request: "Review evidence.md. Return approved as a boolean, with final and exception."}'/>
<asys:job type="search" input='= {request: "Find a candidate meeting the evaluator target."}'/>
<asys:job type="check" input='= {path: "deliverables/report.md"}'/>
<asys:job type="program" args='= ["python3", "/opt/asys/environment/programs/check.py"]'
  input='= {path: "deliverables/report.md"}'/>
```

These are alternative binding fragments, not a complete workflow. Use named
worker `request` for every agentic kind; program inputs follow their own schema.

## Expressions and result contracts

FEEL uses `=`, `and`, `or`, `if ... then ... else ...`, lists and context objects.
It does not use JavaScript operators or shell interpolation. Inputs and args
start with `=`; sequence-flow conditions contain the expression itself. Lists
use one-based indexing. Handle missing values before concatenation:

```text
= {request: request + " Feedback: " + (if review = null then "First pass" else review.reason)}
review.approved = true
```

XML still needs escaping: use `&lt;` and `&amp;`, and single-quoted attributes
around expressions containing double-quoted strings. Use CDATA for script bodies.

Ask a reviewer for exact fields, for example:

```json
{"final":"The report lacks measurements","exception":null,"approved":false,"reason":"Add the measured timing and the command used."}
```

Large artifacts stay in workspace files. Structured results carry routing and
handoff fields. Neither a parseable result nor a successful process exit alone
proves the project meets its acceptance criteria.

## Branch and revise

```xml
<bpmn:exclusiveGateway id="decision" default="revise"/>
<bpmn:sequenceFlow id="accept" sourceRef="decision" targetRef="done">
  <bpmn:conditionExpression xsi:type="bpmn:tFormalExpression" language="feel">
    review.approved = true
  </bpmn:conditionExpression>
</bpmn:sequenceFlow>
<bpmn:sequenceFlow id="revise" sourceRef="decision" targetRef="draft"/>
```

`default` names a sequence flow, not a task. Complete the graph by connecting
draft to check, check to review, and review to decision. A loop visit creates a
fresh job/session while retaining workspace files. Pass findings into the next
assignment. Human feedback should return through both checks and review.

For parallel work, split with a parallel gateway and join before consuming
results. Assign distinct output paths to concurrent writers. An exclusive
gateway does not wait for all parallel branches. Give alternative endings
separate end events with meaningful names.

## Failure paths

A nonzero program exit becomes an activity error with that exit code. An agent's
non-null exception fails its job. Use boundary errors for designed recovery:

```xml
<!-- Declare under definitions. -->
<bpmn:error id="WorkerFailure" errorCode="1"/>
<!-- Attach inside the process, then connect the boundary to a recovery task. -->
<bpmn:boundaryEvent id="draft_failed" attachedToRef="draft">
  <bpmn:errorEventDefinition errorRef="WorkerFailure"/>
</bpmn:boundaryEvent>
```

An error event without `errorRef` catches any attached activity error. A negative
review or human revision is a normal result and should use a gateway. Unhandled
execution failures stop the run; `asys-run --resume RUN` is explicit recovery,
not a substitute for a designed revision branch.

The [team template](../assets/team/workflow.bpmn) is a complete tested example.
Its program and agent environments are interchangeable at the same bindings.
