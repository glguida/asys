# asys-oneshot

Run one assignment with the `simple` system agent in a selected worker environment.

```sh
asys-oneshot ENVIRONMENT_DIRECTORY "prompt" [--model MODEL] [--workspace DIRECTORY]
```

Core asys supplies `simple` and its instructions; one-shot manages the run.
The environment supplies
installed programs, shared skills and extensions. Configure a default inference
model with `asys system-model set simple MODEL`, choosing a name from
`asys-inference models`. `--model MODEL` overrides that default for one run.
Options can appear before or after the positional arguments.

## Run

Install with `make -C asys-oneshot install` from the asys repository root.
See [host installation](../INSTALL.md) for Docker and dcomp requirements.
For an LLM agent, start an inference server exporting `@inference_endpoint`
in the selected dcomp system.

```sh
asys system-model set simple account/model
asys system-model list
asys-oneshot ./env/kicad "Review the connector placement" \
  --workspace ./project
```

Model defaults are saved in `config.json` in the selected asys state directory:
`$ASYS_STATE_ROOT`, or `$XDG_STATE_HOME/asys` (normally `$HOME/.local/state/asys`).
They apply across worker environments. If neither a default nor `--model` is
provided, one-shot exits before creating a run and prints the command to set it.
The chosen model is saved with the run; changing the default affects later runs.

Without `--workspace`, the current directory is the workspace. The directory
must already exist and be writable by the invoking user.
The agent reads and modifies project files there directly. The launcher prints
the run ID and state path to stderr, and the job result JSON to stdout.
A failed job exits with status 1; an interrupted invocation exits with 130.
There is no implicit agent step limit.

Observe the run in another terminal, or inspect it after completion:

```sh
asys status latest
asys logs latest simple
asys top
```

`--root DIRECTORY` selects the asys system root, overriding `ASYS_STATE_ROOT`.
Runs are saved in `ROOT/runs` and model defaults come from `ROOT/config.json`.
`--system NAME` selects
the dcomp system, defaulting to `asys`. `-L inference=COMPONENT.OUTPUT` selects
another Provider output; other declared environment inputs use the same syntax.

## Environment

A minimal environment can contain:

```text
env/review/
  Dockerfile
  component.dcomp
  workers.json
  skills/
    code-review/
      SKILL.md
```

`Dockerfile`:

```dockerfile
FROM asys-workers:dev
COPY . /opt/asys/environment
```

`component.dcomp`:

```text
docker review-environment:dev
input cyclo.provider.v1.Provider inference
```

`workers.json`:

```json
{
  "version": 1,
  "name": "review",
  "types": {
    "program": {
      "command": ["/opt/asys/asys-workers/tools/asys-program"]
    }
  }
}
```

Run `asys-oneshot ./env/review "Review the current changes" --model account/model`
from the project to review, replacing `account/model` with an exported model.
The environment Dockerfile is built on each invocation,
using the current base image and Docker's cache. An environment without a
Dockerfile uses its current declared image.

The `simple` agent uses asys's shared [system prompt](../asys-workers/src/system.md)
and reporting guidance, with no additional role-specific instructions. Its empty
`prompt.md` keeps the agent registered for packaging and launchers.
The shared [system-agent package](../python/asys/system_agents/__init__.py)
owns its worker definition and setup, so other launchers can use the same agent.
`simple` uses the environment's shared resources and has no retained memory. Reports and
proposed lessons belong to the job. See [asys-workers](../asys-workers/README.md)
for environment tools, skills, egress, and the agent result contract.

## Execution and storage

The host launcher starts one workers component through dcomp, submits one job
through the runtime filesystem queue, waits for its result, and removes that
component. It requires no workflow engine component.

```text
asys-oneshot (host) --> runtime queue --> workers component --> simple system agent
```

Run state defaults to `$XDG_STATE_HOME/asys/runs` (normally
`$HOME/.local/state/asys/runs`):

```text
RUN_ID/
  run.json
  events.jsonl
  result.json
  runtime/environments/ENVIRONMENT/jobs/JOB_ID/
    request.json
    state.json
  jobs/JOB_ID/
    input.json
    result.json
    stdout.log
    stderr.log
    agent.json        # agent transcript
    report.md         # written by the agent
    lessons.md        # proposed lessons, if any
    scratch/
  workspace -> /actual/project
```

The workspace link lets the host resolve the same relative queue references
that components use. Dcomp mounts the actual project at `/var/lib/asys/workspace`
and job storage at `/var/lib/asys/jobs`. It does not copy the project or create
input/output directories inside it. Dcomp starts the workers with the invoking
host user's UID/GID, so host and worker can use private job and workspace files.

Each invocation creates a fresh run and job. Previous logs and results stay
intact. The launcher records job creation before submission and records the
outcome when execution ends.

The shared `asys.execution` host library handles environment validation, image
selection, mounts, and dcomp lifecycle. `asys_runtime.queue` supplies submission,
cancellation, and results. BPMN uses the same launch library; another manager
can use both without adopting BPMN control flow.
