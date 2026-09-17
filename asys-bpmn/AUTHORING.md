# Writing workflows and environments

A workflow describes the work: its order, prompts, commands, questions, and
decisions. An environment supplies the programs, models, installed software, and
skills that perform it. Run the pair with:

```sh
asys-bpmn run workflow.bpmn env/dummy --input examples/request.md
```

This guide builds on the runnable [shared-workspace example](examples/shared-workspace).
See [installation](../INSTALL.md) for host commands and base images.

## Project structure

There is no required repository layout. A project can keep everything together:

```text
report-project/
  workflow.bpmn
  examples/request.md
  env/
    dummy/
      workers.json
      component.dcomp
      Dockerfile
      programs/
    production/
      workers.json
      component.dcomp
      Dockerfile
      programs/
      skills/reporting/SKILL.md
```

An organisation can keep many workflows and environments in one repository,
or share environments between repositories. The launcher takes two paths;
directory nesting does not determine execution. No project manifest is required.
The actual project workspace defaults to the current directory. Use
`--workspace DIRECTORY` to choose another repository or working directory.
Execution records live separately under the run state directory.

## Component boundaries

The launcher starts two dcomp components for a run:

| Component | Responsibility |
| --- | --- |
| Workflow | Interpret BPMN, select ready activities, create and log jobs, consume results, and preserve flow state |
| Worker environment | Execute assignments in supplied directories and preserve logs, results, and job state |

`asys-runtime` runs inside the worker environment. It understands job types,
arguments, JSON input, directory references, and exit codes. It does not interpret
agents, prompts, or BPMN. Programs start dynamically as jobs arrive; there is no
fixed worker-count limit or permanent pool created by declaring a type.

The workflow and environment share the defined runtime filesystem protocol.
They are separate containers. Each selected environment has its own queue. One
workflow run uses one environment, including subprocesses and selected actions.
The same workflow can run in several environments as separate runs.

Pi inference uses the environment's dcomp Provider input. Human jobs use its
Human input, linked to `@human_endpoint` or a selected handler. The host launcher uses a runtime filesystem channel to
start the workflow and receive events. Applications do not open another
component's private RPC sockets.

## Define an environment

`workers.json` maps job types to executable programs:

```json
{
  "version": 1,
  "name": "production",
  "description": "Agents and programs with the project toolchain.",
  "types": {
    "agent": {
      "command": ["/opt/asys/asys-workers/tools/asys-agent", "--agent", "general", "--model", "account/model",
                  "--extension", "/opt/asys/asys-workers/extensions/bpmn.mjs"]
    },
    "program": {
      "command": ["/opt/asys/asys-workers/tools/asys-program"],
      "timeout": 600
    },
    "human": {
      "command": ["/opt/asys/asys-workers/tools/asys-human"]
    }
  }
}
```

Create `agents/general/` in the environment; its optional `memory.md` contains
reviewed lessons and principles. Shared `skills/` and `extensions/` supplement
resources in the selected agent's own directory. The global `system.md` belongs
to asys. Agent reports and proposed `lessons.md` are written to job storage.

The explicitly selected `bpmn.mjs` extension supplies tools for ad-hoc assignments. It keeps workflow protocols out of the generic
agent runner.

Replace `account/model` with a name printed by `asys-inference models`. Model
selection belongs here. Another environment can map `agent` to a different
model, another agent runner, or a deterministic dummy without changing the
workflow's prompt.

Set `"egress": true` alongside `name` and `types` when this environment's jobs
need outbound internet access, for example to search documentation or download
datasheets. It defaults to false. The launcher configures a dcomp egress network
for the worker container; every job in that container shares this access. No
port is published. The workflow component and the Provider interface do not
require internet access in the worker container.

Type names are ordinary configuration entries. Define `designer`, `reviewer`, or
`synthesize` when distinct implementations or execution policies are useful.

| Type setting | Meaning |
| --- | --- |
| `command` | Nonempty executable and argument array; workflow arguments are appended |
| `env` | Additional program environment variables |
| `timeout` | Optional program execution timeout, in seconds |

Arguments are literal strings. Shell expansion occurs only when the command
explicitly runs a shell. A relative executable containing `/` resolves from the
configuration directory; other executable names use `PATH`.

Compatibility checks that every reachable job type is declared, including types
in nested and called processes. It does not prove that a program exists, a model
is available, or a tool will accept the design. Test those during execution.

### Package software and interfaces

A basic environment Dockerfile extends the supplied image:

```dockerfile
FROM asys-workers:dev
COPY . /opt/asys/environment
```

Install compilers, synthesis tools, libraries, and other dependencies in the
image. If installation needs root, use `USER root` for that build step and
restore `USER 1000` afterward. Normally retain the base image's entrypoint: it
starts the runtime and declared component services.

For inference and human review, `component.dcomp` contains:

```text
docker report-production:dev
input cyclo.provider.v1.Provider inference
output asys.human.v1.Human human
```

The launcher builds the environment Dockerfile. Without one, the declared image
must already exist locally. A program-only environment can omit both interfaces;
the shared-workspace example does this.

The `inference` input is automatically connected to `@inference_endpoint` in the
selected dcomp system. Start that provider endpoint before running an environment
that needs it. `-L inference=@another_endpoint` selects a different endpoint.

### Put tool knowledge in skills

Pi discovers skills in the selected environment's `skills/` directory. Each skill
has a `SKILL.md`, for example:

```markdown
---
name: reporting
description: Build and validate this environment's report format.
---

Use report-build to render a project directory. Validate the generated report
with report-check before publishing it.
```

A skill explains how to use the environment's tools. The workflow defines the
specific task, expected deliverable, and criterion for advancing.

### Add agent tools with Pi extensions

Put ordinary Pi extensions in the environment's `extensions/` directory. Pi
loads these when starting an agent and registers their tools. Install any npm
dependencies in the environment image, alongside the extension files. For a
packaged extension, a small file can re-export its default entry point:

```typescript
export { default } from "../node_modules/your-extension/index.ts";
```

Use the entry point supplied by that package and pin its version in the
environment's package manifest and lockfile. Extension initialization errors
fail the job visibly. A web-search extension supplies the search tool;
`"egress": true` in `workers.json` supplies its outbound connection.

For interchangeable environments, preserve command entry points and result
formats. All environments might supply `synthesize INPUT OUTPUT`; one uses
Yosys, another a proprietary tool, and a dummy writes a fixture. This is a
convention of your project, not an asys toolchain registry.

## Write a BPMN document

Start from a supplied `.bpmn` example. The document contains ordinary BPMN 2.0
process elements and diagram information, plus a small `asys:job` execution
binding for tasks that run programs.

```xml
<bpmn:definitions
  xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xmlns:asys="urn:asys:workflow:1"
  id="ReportWorkflow" name="Report workflow"
  targetNamespace="urn:example:reports" expressionLanguage="feel">
  <bpmn:process id="report" isExecutable="true">
    <!-- Events, tasks, gateways, and sequence flows go here. -->
  </bpmn:process>
</bpmn:definitions>
```

This is a structural excerpt. A displayable document also needs BPMN Diagram
Interchange: a `BPMNPlane`, shapes, and connector waypoints. The
[shared-workspace example](examples/shared-workspace/workflow.bpmn) includes all of them and
opens in bpmn.io. Geometry describes the drawing; sequence flows describe
execution. Moving a task does not change its dependencies.

Use stable IDs such as `architecture`, `integrate`, and `validate`. IDs identify
result variables and stages in the execution journal. Task names are labels shown in
logs and `top`. Definitions' `name`, then `id`, then the filename provide the
default component name. `--name NAME` overrides it.

### Bind a task

```xml
<bpmn:serviceTask id="draft" name="Draft the report">
  <bpmn:extensionElements>
    <asys:job type="agent"
      input='= {prompt: "Prepare the project report in reports/report.md. Request: " + request}'
      result="draft"/>
  </bpmn:extensionElements>
</bpmn:serviceTask>
```

| Binding | Meaning | Default |
| --- | --- | --- |
| `type` | Name in the selected environment's `types` | Required |
| `input` | FEEL expression producing the program's JSON input | `= variables` |
| `args` | FEEL expression producing an array of argument strings | `= []` |
| `result` | Variable receiving the completed JSON result | Task ID |

The job's JSON result describes the outcome. Project files live in the shared
workspace; later jobs read them directly. The agent's `final` field is a concise
report, and its full transcript remains in the separate job directory.

Define structured decision fields and their meaning in the job's prompt. The
agent includes them in its final JSON response alongside `final` and `exception`:
`{"final":"Review complete","exception":null,"approved":false,"reason":"Missing tests"}`.
With result variable `review`, those become `review.final`, `review.approved`,
and `review.reason`. The worker validates completion and writes the runtime
result. No `success` field is added or interpreted.

The command-line request is UTF-8 Markdown. `--input examples/request.md` makes
its literal text available as `request`; `--input -` reads stdin. The text is
not parsed as JSON. Omit the option for workflows that need no request.

### Expressions and small results

FEEL expressions can use workflow variables, `variables`, `output`, `message`,
`inputs`, `item`, `index`, and loop counters. `task` is the parsed BPMN element.
Bindings must produce the input shape expected by their selected program.

After `draft` finishes, a review can use `draft.final`:

```xml
<asys:job type="agent"
  input='= {prompt: "Review reports/report.md in the workspace. Draft summary: " + draft.final}'/>
```

Standard BPMN data input/output associations map structured values between task
I/O and data objects, including FEEL transformations. They remain useful for
business data. Files remain in the workspace; BPMN data associations do not
copy or publish them.

Prefer a deliberate summary such as `draft.final` over stringifying its entire
result. The complete agent transcript is in job storage. For large or exact data, write
a JSON or Markdown file in the workspace and have the next program read it.

## Workspace and execution records

A workflow runs against a caller-selected working directory, typically a Git
repository. Every job starts there. The workflow manages execution order, while
task descriptions specify which files to read, write, and validate.

```text
project/                         actual workspace
  source files, designs, reports  the project's own layout

run-state/
  runtime/                       queue requests, statuses and host channel
  workspace -> project/          host reference to the actual workspace
  jobs/
    <job-id>/
      input.json                 task data serialized for the program
      stdout.log
      stderr.log
      result.json                structured outcome
      agent.json                 agent transcript
      report.md
      lessons.md
      scratch/
```

The directory structure inside the project is yours. Asys creates no `input/`
or `output/` directories and does not copy files between jobs. If a drafting
job writes `reports/draft.md`, its reviewer reads `reports/draft.md` after the
sequence flow enables it.

Parallel jobs share this directory. Assign independent files or subdirectories
to concurrent jobs. Use sequence flows, sequential multi-instance tasks, or a
coordination program when jobs must modify the same files. Use Git or an explicit
project operation when the workflow needs snapshots or independent branches.

A failed job can leave partial changes in the workspace. A repair or resumed
job sees those changes. Its assignment should say whether to inspect, continue,
or replace them. Each execution has a fresh job directory; previous logs and
results remain available.

## Agents, programs, and human decisions

### Agents

The bundled `asys-agent` uses the Pi SDK library. It runs local tools and makes
multiple inference calls. The environment supplies the model and software; task
input supplies instructions:

```xml
<asys:job type="agent" input='= {
  prompt: "Read architecture.json in the workspace. Design the assigned module in pcb/.",
  timeoutSeconds: 600
}'/>
```

The result is the agent's final JSON object with `final`, `exception`, and any
task-specific fields. Conversation and execution metadata stay in the job
transcript. Refer to workspace files by their project paths. Agents have no step
limit unless `maxSteps` is explicitly supplied; that limit includes compaction
and retry calls. `timeoutSeconds` bounds one inference attempt; provider
exhaustion can put the agent into a visible waiting state until capacity returns.
The environment's `timeout` separately limits program execution.

Prompts should identify inputs, deliverables, checks, and when to report inability
to complete the task. An agent's claim of completion is not domain validation.

### Programs

With the bundled `program` type, task arguments are the command:

```xml
<bpmn:serviceTask id="validate" name="Validate the project">
  <bpmn:extensionElements>
    <asys:job type="program"
      args='= ["/opt/asys/environment/programs/validate-project"]'/>
  </bpmn:extensionElements>
</bpmn:serviceTask>
```

Programs start in the project workspace. They receive JSON input on stdin and can
write a small JSON result to `ASYS_RESULT`. Standard output is a log, not the
structured result. Exit zero means success; nonzero exit is a BPMN activity
error with the exit code as its error code.

| Environment variable | Purpose |
| --- | --- |
| `ASYS_WORKSPACE` | Actual project directory shared by the workflow's jobs |
| `ASYS_JOB_DIR` | This execution's logs, result, report, lessons, and scratch files |
| `ASYS_INPUT` | JSON task input file |
| `ASYS_RESULT` | Optional JSON result file to write |
| `ASYS_JOB_ID`, `ASYS_JOB_TYPE` | Current job identity and type |

A small script can live in a standard BPMN `scriptTask` body. The shared-workspace
example invokes it with `args='= ["python3", "-c", task.script]'`. For external
programs, put the implementation in the environment and invocation in the
workflow.

### Human decisions

A human task uses a program too. The supplied `human` program accepts:

```xml
<bpmn:userTask id="approval" name="Approve the report">
  <bpmn:extensionElements>
    <asys:job type="human" input='= {
      title: "Report approval",
      prompt: "Review the report and approve or request changes.",
      context: {summary: draft.final},
      form: {type: "object", properties: {
        approved: {type: "boolean"}, comments: {type: "string"}
      }, required: ["approved"], additionalProperties: false}
    }'/>
  </bpmn:extensionElements>
</bpmn:userTask>
```

The program calls `Ask` through the environment's Human input and waits for an
answer. The Human service queues the question, presents it through its terminal
handler, and validates the submitted result. That answer becomes the job result,
for example `approval.approved`.

Start `asys-human-prompt` in another terminal to bind `@human_endpoint`, or add
`--human` to `asys-bpmn run` for a handler connected only to this run. The
worker environment declares `input asys.human.v1.Human human` in its manifest.
The terminal shows the actual workspace, mapped from the worker's dcomp binds;
it does not use job scratch directories as review locations. Cancelling a human
job withdraws its request. An unavailable service fails the job and follows the
workflow's ordinary error handling.

Completing a human task with `approved: false` successfully executes the question.
Add a gateway afterward to proceed or return to revision.

## Parallelism, repetition, and waiting

### Split and join

Use a parallel gateway to split independent work and another to join it. The
join waits for its branches before issuing the next job. The shared-workspace
example demonstrates this with actual files and checks.

For a known collection, use multi-instance characteristics with
`loopDataInputRef` and `inputDataItem`. Each instance gets a new job
directory and uses the actual workspace. Parallel instances suit independent
files; sequential instances serialize changes to shared files.

### Ad-hoc selection

An `adHocSubProcess` binds its coordinator program and declares individually
bound child tasks. The coordinator receives `list_actions`, `start_action`, and
`wait_action` tools. If a child reads `message.module`, call:

```json
{"action": "design_module", "input": {"module": {"name": "power", "pins": []}}}
```

The input object becomes `message` without an automatic wrapper. Invalid
expression inputs are rejected before starting a directly bound action, so the
coordinator can correct its call.

Actions without incoming flows start enabled. Completing prerequisites enables
dependent actions, which the coordinator still selects explicitly. Sequential
ordering allows one active selection. Parallel ordering allows different child
activities together; simultaneous selections of the same activity are currently
rejected as busy. Wait for its invocation to finish before selecting it again,
or use a multi-instance task for a known collection.

The coordinator and selected jobs see the same workspace. Waiting for an action
confirms that its execution finished; the coordinator can then inspect the files
that job produced.

Without a completion condition, coordinator completion requests the end of the
subprocess. With a condition, the expression determines when it can end.
`cancelRemainingInstances="false"` waits for active work in a parallel scope;
otherwise the scope can cancel that work when completing.

### Rejection and failure

Use an exclusive gateway for a successful review that requests revision:

```xml
<bpmn:exclusiveGateway id="decision" default="revise"/>
<bpmn:sequenceFlow id="accept" sourceRef="decision" targetRef="publish">
  <bpmn:conditionExpression xsi:type="bpmn:tFormalExpression" language="feel">
    approval.approved = true
  </bpmn:conditionExpression>
</bpmn:sequenceFlow>
<bpmn:sequenceFlow id="revise" sourceRef="decision" targetRef="draft"/>
```

For execution failure, use a boundary error event. Define a BPMN error with
`errorCode="17"` to catch a validator exiting 17, attach its boundary to the
task, and connect it to remediation. An unhandled failure ends the run.
Partial workspace changes remain available to the handler.

An agent declares an execution exception in its final JSON response, for example
`{"final":"Only architecture.json was supplied","exception":"Required schematic is missing"}`. `asys-agent` exits 1, and
the failed task's result variable contains `exception` and the supplied result
fields. A recovery task can consume them using the same
result-variable mechanism as any other task. An invalid final JSON response also fails
the job. Use `approved: false` for a completed review requesting changes;
`exception` means the assigned work could not be completed.

For example, declare this error under the BPMN definitions:

```xml
<bpmn:error id="AgentException" errorCode="1"/>
```

Inside the process, attach a handler to an existing agent task named `design`
(whose result variable is also `design`):

```xml
<bpmn:boundaryEvent id="design_failed" attachedToRef="design">
  <bpmn:errorEventDefinition errorRef="AgentException"/>
</bpmn:boundaryEvent>
<bpmn:sequenceFlow id="handle_design_error"
  sourceRef="design_failed" targetRef="recover_design"/>
<bpmn:serviceTask id="recover_design" name="Investigate the design failure">
  <bpmn:extensionElements>
    <asys:job type="agent"
      input='= {prompt: "Investigate this design failure in the workspace. Explain the correction needed. Reason: " + design.exception}'/>
  </bpmn:extensionElements>
</bpmn:serviceTask>
```

The recovery path can contain agents, programs, or human tasks. Add ordinary
sequence flows and gateways to retry the failed activity, choose an alternative,
or end the process. A loop back creates a new task execution. A boundary error
without an `errorRef` catches any error from its attached activity. The handler
sees the same workspace, including partial changes from the failed job. It can
repair those files directly.

A timer can bound a wait or interrupt an activity. An unbound receive task waits
for a message through the Workflow interface or host channel without consuming
an agent process.

### Restarts and retries

After a run fails, continue it with `asys-bpmn resume RUN`, using its ID, unique
prefix or saved state path. The runner restores the original BPMN and variables
from the run's database and automatically refreshes its engine and environment
images. It rebuilds the original environment directory with Docker's cache when
a Dockerfile is present, or resolves the currently declared image otherwise.
The environment directory must remain available with the same environment name
and the job types needed by the workflow.
Updated tools and programs take effect while the saved workflow and job state
are retained.

Completed stages retain their results; the failed stage and work cancelled
alongside it receive new job IDs and directories. An agent stage starts again with its original
prompt and a fresh session. Its earlier transcript and logs remain available.
A second failure leaves the run available for another explicit resume. This
does not turn failure into an automatic workflow loop:
use BPMN error boundaries for designed repair paths. See
[resume requirements](README.md#resume-a-failed-workflow) for checkpoint and
image requirements. Already running containers keep their images until their
next start or resume.

The workflow journals every job creation and completion. A replacement job's
creation records the ID it replaces, before submission. Restarting the workflow
manager reattaches to already submitted jobs; it does not submit duplicates.
Worker shutdown interrupts active programs. The runtime records that outcome
and never reexecutes a terminal job. The workflow manager decides what to retry.

Programs must account for interruption when repeating an external side effect.
The runtime cannot undo a sent email or published release. Keep irreversible
operations in explicit stages with appropriate review.

## Validate the result

Artifact delivery establishes which files arrive. Domain checks establish that
they represent the intended result. For a PCB project, a useful chain is:

```text
architecture → module designs → integration → layout → validation → approval
```

Architecture defines modules and connections. Integration checks that every
module arrived and preserves its circuitry. Layout consumes the complete
integrated schematic. Validation compares expected components and connectivity
in addition to running ERC and DRC.

A wire-only replacement sheet can pass a narrow check while losing the design.
An empty board can report no unrouted nets. Check required files, components,
connectivity, and meaningful output counts as appropriate. Fail clearly when
required input is missing; do not invent a replacement.

Start with a dummy environment. Test file handoff and branch decisions without
inference, then run the same workflow in a real toolchain environment. A dummy
proves the orchestration contract exercised by its fixtures, not toolchain or
agent correctness.

## Run and inspect

```sh
asys-bpmn run workflow.bpmn env/dummy --input examples/request.md
asys status latest
asys status latest --json
asys logs latest
asys logs latest -f
asys logs latest integrate
asys top
```

`asys status --json` includes each job ID, status, job directory and actual
workspace. Logs, results and transcripts remain after component cleanup.

Workflow logs show stage starts, finishes, waits, failures, agent exhaustion,
and compaction. Select a job for stdout/stderr. In `asys top`, selecting an agent
shows its transcript, including assistant text, tools, and readable thinking
when the provider supplies it. Press `l` to switch between the transcript and
the saved run log.

`--root ./state` chooses the run-state parent. Pass the same option to `status`,
`logs`, and `top`. A run selector can be `latest`, an ID, a unique ID prefix, or
the run directory.

| Symptom | Check |
| --- | --- |
| Missing job type | Binding type against selected `workers.json` |
| Executable not found | Image, executable path, permissions, and `PATH` |
| No usable model | Configured model against `asys-inference models` |
| Expected file missing | Project path, predecessor result, and sequence-flow ordering |
| One branch missing | Synchronising join before assembly |
| Required file missing | Producer deliverable contract and domain validation |
| Action not enabled | Incoming prerequisites or an active invocation of the same activity |
| Human waiting indefinitely | A connected frontend received and answered the question |
| Provider exhausted | Provider capacity and reset time |

## References and limits

The [workflow reference](README.md) documents the CLI and Workflow interface.
The [worker reference](../asys-workers/README.md) documents runner input and the
Human interface. The [runtime reference](../asys-runtime/README.md) defines jobs,
execution and filesystem protocols.

[BPMN 2.0.2](https://www.omg.org/spec/BPMN/2.0.2/PDF/) defines process elements,
control flow, data associations, and diagram interchange. The asys binding is
specified in [asys.xsd](asys.xsd) and the [moddle descriptor](src/moddle.json).

Parsing and preserving BPMN does not imply complete execution conformance.
Current limits include external process imports, data-association assignment
blocks, some multi-instance event behaviours, and simultaneous ad-hoc selections
of the same child activity. Called processes must be in the loaded document.
Other engines' execution bindings are not used. Artifact copying and directory
layout are asys conventions; other engines need their own file delivery.
