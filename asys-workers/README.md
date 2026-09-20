# asys-workers

An execution component containing an environment of named agents, installed
programs, skills, and tools. A caller submits an assignment with a prepared job
directory and workspace. Workers execute it and return its result. The caller
owns sequencing, dependencies, branching, and decisions about further work.

The component runs the [asys-runtime](../asys-runtime) process executor. Agent
jobs use the installed Pi coding-agent SDK. Inference uses the unchanged dcomp
Provider interface, connected to `@inference_endpoint`. Human interaction uses
the typed [Human interface](proto/asys/human/v1/human.proto).

## Environment definition

An environment is a directory copied into its image at `/opt/asys/environment`:

```text
env/kicad/
  Dockerfile
  component.dcomp
  workers.json
  agents/
    schematic/
      prompt.md        # optional stable agent instructions
      memory.md
      skills/          # optional skills specific to this agent
      extensions/      # optional additional Pi tools
    simulation/
      memory.md
    pcb/
      memory.md
  skills/              # shared skills, each with a SKILL.md
  extensions/          # shared Pi extension files
  programs/            # environment-specific commands
```

The global behavioral prompt includes [src/system.md](src/system.md) and the
short [Reporting to humans guide](src/reporting-to-humans.md), supplied by asys.
The guide is included in every agent's system prompt, so reporting guidance
does not depend on an individual workflow repeating it. Agent memory contains
retained lessons and principles. Agents read their
definitions during jobs; reports and proposed lessons go into job storage.
Several workers components can use the same environment concurrently. A later
review process can evaluate their evidence and propose a new environment
version. Ordinary execution does not perform that promotion.

`workers.json` maps job types to command vectors. The command selects the named
agent and model; task data supplies the assignment:

```json
{
  "version": 1,
  "name": "kicad",
  "description": "Circuit design and verification tools.",
  "egress": true,
  "types": {
    "pcb": {
      "command": ["/opt/asys/asys-workers/tools/asys-agent",
                  "--agent", "pcb", "--model", "account/model",
                  "--extension", "/opt/asys/asys-workers/extensions/bpmn.mjs"]
    },
    "program": {"command": ["/opt/asys/asys-workers/tools/asys-program"]},
    "human": {"command": ["/opt/asys/asys-workers/tools/asys-human"]}
  }
}
```

Use a model name exported by `asys-inference models`. Job types and agent names
are chosen by the environment author. More than one job type can select the
same agent. `prompt.md` and `memory.md` are optional; the named agent directory
must exist. `prompt.md` supplies stable agent instructions in addition to the
global prompt. Each job still supplies its own assignment in `input.prompt`.
Shared skills and the selected agent's skills are both discovered. Skills of
other agents are not loaded. Pi includes the skill catalogue and reads full
skills with its ordinary tools. Shared and agent-specific `extensions/` folders
contain `.js`, `.mjs`, `.cjs`, or `.ts` Pi extension files. A broken extension
fails initialization rather than silently dropping tools. Installed programs
are available through the shell tool.

`--extension PATH` explicitly loads an additional Pi extension; relative paths
are resolved beside the selected `workers.json`. For an ad-hoc BPMN
assignment, the supplied `extensions/bpmn.mjs` registers `list_actions`, `start_action`, and `wait_action`.
The generic agent runner does not inspect BPMN metadata or choose workflow
activities. Environments for other callers can provide their own extensions.

`egress: true` lets host launchers enable outbound access through dcomp when creating
the workers component. Omitted or false means no outbound network access.
Inference over the dcomp interface works without egress. Network access alone
does not provide a search tool; install the appropriate program or Pi extension.

The [default environment](env/default) is a starting point: copy it into your
project and replace `account/model` in its command with an exported model name.
The [authoring guide](../asys-bpmn/AUTHORING.md) covers complete BPMN projects.

## Execution storage

A run creates its own workers component through dcomp. Its queue, job-storage
root and actual workspace are mounted at creation. New job directories live
beneath the job root; every job receives the chosen workspace.

| Location | Contents | Owner |
| --- | --- | --- |
| Environment image | Named agents, memory, skills, tools | Environment author |
| Queue entry | Assignment, ownership, cancellation, execution status | Runtime |
| Supplied job directory | Reports, scratch work, lessons, logs, transcript, result | This job's execution |
| Supplied workspace | The project or other material being worked on | Caller chooses and prepares it |

The queue stores the two directory references relative to the queue root. The
host and containers mount the same relative layout, so saved records are also
readable by `asys top` and `asys logs` on the host. See the
[runtime contract](../asys-runtime/README.md#execution-contract).

Each execution gets a fresh job ID and directory containing `stdout.log`,
`stderr.log`, `agent.json`, and `result.json`. Pi support files and the effective
system prompt live under `.pi/`. Agents write `report.md`, proposed `lessons.md`,
and temporary files under `scratch/` in that directory. The actual code and
project deliverables stay in the workspace.

The component accepts `--environment DIRECTORY` and `--root DIRECTORY`, defaulting
to `/opt/asys/environment` and `/var/lib/asys/runtime`. Its job types are published
under `runtime/environments/NAME/`. Processes start as jobs arrive, without a
fixed concurrency limit. Runtime supervises them, captures output, and records
exit status. It does not prepare artifact dependencies or decide the next job.

## Agent execution and completion

`asys-agent --agent NAME --model MODEL` constructs the session using the global
system prompt, the selected agent's `prompt.md` and memory, its actual skills and tools, and
the supplied task and execution locations. Project files in the workspace do
not configure the agent's system prompt or automatically install extensions.

The assignment's JSON data is:

```json
{
  "prompt": "Create a PCB for the supplied schematic and board constraints.",
  "timeoutSeconds": 600,
  "options": {}
}
```

`prompt` is required. Positional command arguments can supply it instead.
`--max-steps` and `--timeout` override the corresponding input fields.
There is no step limit unless `--max-steps` or `maxSteps` is explicitly supplied.
The environment command selects the model. Provider options pass through the
existing inference payload.

The agent finishes with a JSON object in its final assistant response:

```json
{"final": "Created and checked pcb/board.kicad_pcb.", "exception": null}
```

It can include task-defined fields such as `approved`, measurements, or file
names. `final` must be nonempty text, and `exception` must be null or a nonempty
reason the task cannot continue. The worker parses this object, writes the
runtime result file, and exits with status 1 when it declares an exception.
Malformed completion gets one format correction in the same session, with the
parser error and previous work still available. An uncorrected response fails
the job. This correction counts toward any inference step limit; execution errors
and cancellation retain their normal handling. Execution metadata and conversation
remain in the transcript, outside the returned task result.

For example, `{"final":"Routing is incomplete","exception":"15 nets remain
unconnected"}` returns that report and a failing exit status. BPMN can catch it
with a boundary error event; other callers choose their own response.

Pi owns the agent loop, local tools, compaction and automatic retries. Transient
inference failures retry up to three times with delays of 2, 4 and 8 seconds.
Provider exhaustion waits until the reported reset time. `timeoutSeconds`
bounds an inference attempt, excluding that exhaustion wait. `maxSteps` bounds
inference calls including compaction and retries. Cancellation interrupts waits.
Provider identities and native message signatures are preserved through the
existing adapter.

A caller retries work by submitting a new job, with a fresh directory and Pi
session. Previous jobs remain unchanged. The same workspace can retain partial
work; the caller decides what the next assignment should do with it. A running
component keeps its environment image for its lifetime.

The host launchers pass their UID/GID through dcomp when creating workers and
the workflow component. Images may retain a default user for independent use;
asys execution uses the caller's identity for shared files.

`JOB/.pi/system-prompt.txt` contains the exact last effective prompt.
`JOB/agent.json` stores Pi session entries, the selected agent directory, and
hashes of its `prompt.md` and memory.
Streaming text, thinking, tool activity, inference retries, exhaustion, and
compaction are emitted on stdout with timestamps. The host monitor combines
these events with the saved transcript without changing execution state.

## Programs and humans

The [goal worker](../asys-goal/README.md) is another ordinary program:
`/opt/asys/asys-workers/tools/asys-goal`. It runs the built-in `simple` agent
in fresh sessions to define and review success criteria, then implement and
verify until the goal is met. Criteria and unresolved findings persist between
attempts. There is no default attempt limit, and every phase can ask for help
through the Human input. Its job
input contains `goal` and optional `maxAttempts`; its model defaults to the
`simple` system-model setting supplied by the launcher, with an optional
`--model MODEL` command override. BPMN can bind a task to a `goal` job type
without implementing the loop itself. The default environment declares this
job type alongside agent, program, and human jobs.


`asys-program` executes the assignment's complete argument vector directly.
Use an explicit shell when shell syntax is needed. Ordinary programs use
`ASYS_WORKSPACE`, `ASYS_JOB_DIR`, `ASYS_INPUT`, and `ASYS_RESULT` from the
runtime contract. Their exit code determines success; a result JSON file is
optional. They do not need Pi or the agent completion format.

`asys-human` accepts an object with required `prompt` and optional `title`,
`summary`, `files`, `context`, `details`, `candidates`, `form` (JSON Schema draft-07),
and `uischema` (JSON Forms). The completed human answer becomes its job result.
The producing workflow or environment writes the question and work summary,
selects the evidence, and defines what the answer means. The shared
[Reporting to humans guide](src/reporting-to-humans.md) explains how to compose
these fields. Follow the
[decision-authoring guide](../asys-bpmn/AUTHORING.md#write-a-decision-the-human-can-understand):
put the question and requested action in `prompt`, completed work in `summary`,
review artifacts in `files`, and machine state in `details`.

For a command that directly runs `asys-human`, host launchers supply
`input asys.human.v1.Human human` in the run's workers definition, correcting
an omitted input or an output declaration without editing the environment's
source files. Custom programs that call Human must declare that input in
`component.dcomp`. Launchers connect
it to `@human_endpoint` by default; `-L human=COMPONENT.OUTPUT` selects a specific
service. `asys-human` calls `Ask` through this dcomp input and waits for the answer.
It does not run a server or share job files with the Human service. Cancellation
withdraws the request; a service or transport failure fails the job.

The request metadata preserves caller metadata and includes the originating
`component`, `job_id`, and `files.workspace` (the worker's actual workspace path).
The terminal resolves that path through dcomp bind metadata to show the host
project directory. Job records and scratch files are not review locations.

[`asys-human-prompt`](../asys-human-interface/README.md) provides the service and
terminal. Start it to bind `@human_endpoint`, or use `asys-bpmn run --human` to
create a handler dedicated to that run. The service validates the answer against
the task's JSON Schema before returning it to the worker.

## Development

From this directory, install dependencies with `npm ci`, then run `npm test`.
Tests cover actual Pi tools, selected memory and skills, strict completion,
compaction, retries, Human attention and restart, cancellation, and execution
through the filesystem runtime. Repository build and installation instructions
are in [INSTALL.md](../INSTALL.md).
