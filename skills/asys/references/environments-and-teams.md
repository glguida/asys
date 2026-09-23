# Environments and teams

## Design responsibilities before role names

A team is a set of role definitions and job contracts, coordinated by a workflow.
Declaring a worker type does not create a permanent agent process or a fixed
worker pool. Each enabled task creates a job; runtime starts its configured
program. Different roles may use the same model and still be different agents.

For each role specify:

- The decision or artifact it owns, and which files it may change.
- The inputs it can rely on and the prerequisites that make them available.
- Checks that establish useful completion, including negative results.
- Result fields the workflow consumes, with their exact types and meanings.
- What constitutes a blocker and which workflow path handles it.

A practical team often has an implementer, a deterministic validation program,
and a reviewer. Add a coordinator when it must select work dynamically. Add
human decisions when the requested process needs judgment or intervention.
Use `goal` when the desired behavior is simply implement/verify until satisfied;
there is no need to reproduce that loop with separate role jobs.

## Runnable starter

This skill's `assets/team/` contains a report-writing example. Copy it into a
new project directory rather than editing the installed skill:

```sh
asys_skill_dir=$(asys skill)
cp -R "$asys_skill_dir/assets/team" ./report-team
mkdir -p ./report-workspace
asys-bpmn run ./report-team/workflow.bpmn ./report-team/env/dummy \
  --input ./report-team/request.md --workspace ./report-workspace
```

If using an exported copy of the skill, use that copy's absolute directory for
`asys_skill_dir`. No model or Human service is needed for the dummy. It writes
a report, deliberately omits the evidence section on its first pass, requests
revision, and completes after a second pass adds the evidence. The workflow
runs an independent artifact check on each pass.

To use real agents, replace **both** `account/model` placeholders in
`report-team/env/agents/workers.json` with IDs from `asys-inference models`.
Start with a fresh workspace, then use the same workflow and input with
`./report-team/env/agents`. This environment also declares `goal`, `program`,
and `human` for extending the example. The supplied role prompts are for report
writing; adapt them and the artifact checks to the actual project before using
the team for another domain.

The separate `assets/goal.bpmn` can use this agents environment with a configured
`simple` model. Copy it into the project, supply a goal via `--input`, and adapt
the environment's installed tools to the work.

## Environment directory

There is no required repository layout or team manifest. A reusable layout is:

```text
project/
  workflow.bpmn
  request.md
  env/development/
    workers.json
    tools.md
    component.dcomp
    Dockerfile
    agents/
      implementer/prompt.md
      reviewer/prompt.md
      reviewer/memory.md
      reviewer/skills/domain-review/SKILL.md
    skills/toolchain/SKILL.md
    extensions/search.mjs
    programs/check.py
```

The launcher receives workflow and environment paths independently; they can be
in different repositories. One workflow run uses one environment, including
called processes and ad-hoc children. The workspace is independently selected
by `--workspace`. A run does not turn the environment source directory into the
workspace unless that is the path you choose.

## Environment tool list

Optionally add `tools.md` beside the environment's `workers.json`. Asys includes
its contents in every agent's system prompt, including the built-in `simple`
agent used by one-shot and goal, and agents from an external worker bundle.
It reads the file from `ASYS_ENVIRONMENT_DIR`, not from the workspace or the
external bundle. Missing or blank files add nothing; no configuration flag is
needed. Package the file in the environment image with the other resources.

Keep it a short inventory of installed software and libraries: names, versions,
and essential availability constraints. For example, if the image installs
these versions:

```markdown
- Python 3.12
- GCC 13.2
- CMake 3.28
```

Keep the list current when rebuilding the image. It is author-provided context,
not an automatic capability probe, and does not install software or register
callable agent tools. Put usage instructions in tool documentation or skills.

## workers.json

```json
{
  "version": 1,
  "name": "development",
  "description": "Implementation, review, and project checks.",
  "egress": false,
  "types": {
    "implementer": {
      "command": ["/opt/asys/asys-workers/tools/asys-agent",
                  "--agent", "implementer", "--model", "account/model"]
    },
    "reviewer": {
      "command": ["/opt/asys/asys-workers/tools/asys-agent",
                  "--agent", "reviewer", "--model", "account/model"]
    },
    "check": {
      "command": ["python3", "/opt/asys/environment/programs/check.py"],
      "timeout": 300
    },
    "program": {"command": ["/opt/asys/asys-workers/tools/asys-program"]},
    "human": {"command": ["/opt/asys/asys-workers/tools/asys-human"]},
    "goal": {"command": ["/opt/asys/asys-workers/tools/asys-goal"]}
  }
}
```

`version` is 1. Give the environment a stable name and at least one job type.
Use simple alphanumeric names with hyphens/underscores for portability. Type
names are project-defined; `implementer` is not a built-in role. More than one
type can choose the same agent, and the agent name need not equal the type name.

| Type field | Meaning |
| --- | --- |
| `command` | Nonempty string array; task `args` are appended |
| `env` | Additional program environment variables |
| `timeout` | Optional whole-program timeout in seconds |

Arguments are literal. Relative executables containing `/` resolve beside
`workers.json`; a bare executable uses `PATH`. Relative *arguments* are still
interpreted by that program, normally against the workspace, so use absolute
container paths for bundled scripts. Do not put secrets in the image or this
configuration; inference authentication belongs to the gateway.

`egress` controls outbound worker-container access and defaults to false.
All jobs in the component share that setting. Inference RPC works without
egress. A network connection does not itself supply a search tool: install a
program or extension as well.

## Image and component interfaces

```dockerfile
FROM asys-workers:dev
COPY . /opt/asys/environment
```

Install project compilers, test tools, libraries, and extension dependencies in
the image. Use `USER root` for system package installation when needed and
restore `USER 1000` afterward. Retain the base entrypoint; it starts workers and
component services. Host launchers run components with the invoking user's
UID/GID for shared files, so programs must work for that identity.

`component.dcomp`:

```text
docker project-development:dev
input cyclo.provider.v1.Provider inference
input asys.human.v1.Human human
```

The `docker` name is the environment image tag. A Dockerfile is built on each
run/resume with the current base and cache. Without a Dockerfile, that image
must already exist locally. Program-only environments can omit both inputs.

Launchers connect `inference` to `@inference_endpoint` and Human to
`@human_endpoint`; `-L INPUT=TARGET` overrides a link. Start inference in the
same dcomp system first. The shared Human service is started when needed;
`asys-human-prompt` can attach later. For direct `asys-human` or `asys-goal`
commands, the launcher supplies a missing Human input in its run definition.
Declare it explicitly for custom programs that call Human.

## Agent definitions and prompts

`agents/NAME/` must exist for `asys-agent --agent NAME`. `prompt.md` and
`memory.md` are optional. Use the former for stable role responsibilities,
evidence standards, and output conventions; use the assignment's `prompt` for
the particular goal, files, and current feedback.

For example, a reviewer definition can say:

```markdown
Inspect the requested artifact and run the relevant checks. Identify specific
unmet requirements using file locations and observed results. Do not repair
the artifact during review. Return final, exception, approved (boolean), and
reason (text). A completed negative review has exception null. Report a blocker
with a non-null exception when you cannot perform the review.
```

This is behavior guidance, not a filesystem sandbox: ordinary reviewer tools
retain the same workspace access as other agents.

Shared environment skills and selected-agent skills are discovered together;
other agents' private skills are not loaded. The prompt includes asys's global
instructions, the environment's optional tool list, the selected role
definition/memory, actual resources, and the
job assignment. Files in the workspace do not automatically replace this system
prompt or install extensions.

`memory.md` is reviewed retained knowledge packaged with the agent. Jobs can
write proposed `lessons.md` under their job directory. Review those lessons,
edit the environment definition deliberately, and rebuild for subsequent runs
when promotion is wanted. Several runs may share an environment definition;
ordinary jobs do not mutate it. Built-in `simple` has no retained memory; its
reports and goal lessons stay in run state.

## Agent input and execution options

Ordinary `asys-agent` input is an object with required `prompt` and optional
`maxSteps` and `options` (an object passed to the inference
provider). Positional worker arguments can supply the prompt instead. These
are worker options, not extra flags accepted by the one-shot host launcher:

| Worker option | Meaning |
| --- | --- |
| `--agent NAME` | Select the environment's agent definition |
| `--model MODEL` | Required exported inference model ID |
| `--extension PATH` | Additional Pi extension; repeatable |
| `--max-steps N` | Override input `maxSteps`; positive integer; no default cap |

The agent's inference client retries inactive or interrupted RPC attempts while
the assignment remains pending. `ASYS_INFERENCE_IDLE_TIMEOUT_MS` controls the
inactivity limit (default ten minutes); progressing requests have no absolute
deadline. Recovery works through any Provider endpoint, with or without a pooler.
`maxSteps` counts logical inference calls, including compaction, but not transport
retries. `workers.json` type `timeout` independently
bounds the whole program. Cancellation interrupts waits. Avoid imposing these
limits simply because a role exists; choose them when the task needs a bound.

## Skills and extensions

A skill is reusable tool/task knowledge:

```markdown
---
name: project-checks
description: Build and validate this environment's report format.
---

Run report-build for rendering. Run report-check against the generated report.
The check requires all requested sections and evidence references; an empty
output is a failure. Keep generated artifacts under deliverables/.
```

Put it at `skills/project-checks/SKILL.md`, or under a particular agent's
`skills/`. The skill describes tools; install the actual executable separately.
To make this asys guide available to an asys worker, copy it into the environment
with `asys skill ENVIRONMENT/skills` before building. Host orchestration commands
and Docker access are not added to that worker just by installing this guide.

Pi extensions in shared or selected-agent `extensions/` directories can add
tools and hooks. Supported files are `.js`, `.mjs`, `.cjs`, and `.ts`; install and
pin their dependencies beside the extension. Initialization errors fail the job.
`asys-agent --extension PATH` adds an explicit extension, with relative paths
resolved beside `workers.json`.

For an ad-hoc BPMN coordinator, select the shipped extension:

```json
"--extension", "/opt/asys/asys-workers/extensions/bpmn.mjs"
```

Append those arguments to its agent command. This supplies `list_actions`,
`start_action`, and `wait_action`. Generic agents do not acquire those tools
merely because their job was created by BPMN.

## Implement an ordinary worker program

A custom executable can be a type directly, without `asys-program`. Use the
wrapper when the workflow itself supplies the complete executable/arguments.
Each job starts in the actual workspace and receives JSON input on stdin.

| Environment variable | Purpose |
| --- | --- |
| `ASYS_INPUT` | JSON input file, also supplied on stdin |
| `ASYS_RESULT` | Write the structured JSON result here |
| `ASYS_WORKSPACE` | Project files, shared across jobs |
| `ASYS_JOB_DIR` | This execution's logs/reports/scratch |
| `ASYS_JOB_ID`, `ASYS_JOB_TYPE` | Identity/type |
| `ASYS_REQUEST` | Queue request file with metadata |
| `ASYS_ENVIRONMENT_DIR` | Installed environment resources |

Example program:

```python
import json
import os
import sys
from pathlib import Path

assignment = json.load(sys.stdin)
artifact = Path(assignment["path"])
if not artifact.is_file() or artifact.stat().st_size == 0:
    print(f"Missing or empty artifact: {artifact}", file=sys.stderr)
    raise SystemExit(17)
Path(os.environ["ASYS_RESULT"]).write_text(json.dumps({
    "path": str(artifact), "bytes": artifact.stat().st_size,
}), encoding="utf-8")
```

Exit zero means success; nonzero fails the job. Stdout/stderr are retained logs,
not a JSON result channel. A result file is optional for ordinary programs;
avoid having later tasks depend on absent fields. A non-null `exception` in an
object result also reports failure. Do not write success and then mask a failed
command. Account for interrupted/retried external operations in the program's
own design.

## Keep shared work coherent

Sequence flows establish when a consumer can rely on a producer. Files stay in
the workspace; results should carry small summaries, paths, measurements, and
decisions. Assign independent paths to concurrent jobs. A parallel gateway does
not isolate filesystem writes, and there is no fixed concurrency cap merely
from declaring types. Use sequential work or explicit partitioning when needed.

Use domain checks that reject missing content, not just syntactic validity:
required components, interfaces, constraints, evidence, and meaningful output
counts matter. Keep dummy and real environments' job types, arguments, result
fields, and artifact paths compatible, then test both success and revision/error
branches before trusting a larger team.
