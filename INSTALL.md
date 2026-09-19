# Host installation

Use a Linux host with a local Docker Engine (25 or newer), the Docker CLI,
GNU Make, Go 1.25 or newer, and Python 3.10 or newer. The user running asys
must be able to run `docker info` successfully. Builds download their Go,
Node, and image dependencies as needed.

Dcomp 0.3.1 or newer is installed separately. Its `dcomp` and `dcomp-proxy` commands must
be on `PATH`. Check an existing installation with:

```sh
dcomp version
command -v dcomp-proxy
```

To build dcomp from source, run these commands from the root of its separate
source checkout (0.3.1 or newer):

```sh
make build
make install PREFIX="$HOME/.local"
```

## Install asys

Obtain an asys source checkout and open a terminal at its repository root.
Run the remaining commands in this guide from that directory:

```sh
export PATH="$HOME/.local/bin:$PATH"
make install
```

Keep `$HOME/.local/bin` on `PATH` in the shell startup configuration.
`PREFIX` selects another installation directory; the asys default is
`$HOME/.local`. `DESTDIR` is available for packaging.

```sh
make install PREFIX=/opt/asys
```

The destination must be writable by the installing user. `make build` builds
the host commands and container images without installing them. `make install-host`
installs the shared `asys` command and requires no Docker. Use
`make -C asys-inference install` for inference management, or
`make -C asys-bpmn install` for the workflow launcher and observer.
`make -C asys-oneshot install` installs the single-agent launcher and observer
and builds the shared worker images.
`make -C asys-goal install` installs the goal launcher and human handler and
builds the worker and Human images.
`make -C asys-human-interface install` builds the human interface component and
installs the `asys-human-prompt` terminal handler; its `install-host` target
installs just the host tool when the image is already available.

The inference installer builds the Go command and installs provider component
sources and their shared dependencies. Provider images are built when the
inference server is started or a provider is added.

The workflow installer installs its Python launcher and builds the runtime,
worker, workflow, and bundled environment images in the local Docker Engine.
An environment's own Dockerfile is also built on each run and resume, using
the current installed base images and Docker's build cache. Node, Pi, and the
worker execution dependencies live in those images.

The host commands include the Python packages they need. No separate Python
package installation is needed to use them. The optional standalone
`asys-runtime` CLI has its own [installation instructions](asys-runtime/README.md#run).

| Installed path under `PREFIX` | Contents |
| --- | --- |
| `bin/asys` | State initialization, shared-service updates, run listing, status, logs, and terminal monitor |
| `bin/asys-inference` | Inference server management command |
| `bin/asys-bpmn` | BPMN workflow launcher |
| `bin/asys-oneshot` | Run an assignment with the simple system agent in an environment |
| `bin/asys-goal` | Implement and verify a goal, repeating with the simple system agent |
| `bin/asys-human-prompt` | Foreground human handler with a queue of text questions |
| `share/asys/python/` | Shared observation, environment launch, and runtime queue packages |
| `share/asys/python/asys/system_agents/` | Built-in agent prompts and worker setup shared by launchers |
| `share/asys/skills/asys/` | Portable Agent Skill, topic references, and runnable team/goal templates |
| `share/asys-inference/components/` | Provider component sources and build dependencies |
| `share/asys-inference/gateway-channel` | Private host client for gateway administration and login |
| `share/asys-inference/provider-channel` | Private host client for the exported model catalogue |
| `share/asys-inference/python/` | Bundled runtime channel library |
| `share/asys-bpmn/python/` | BPMN progress formatting |
| `share/asys-human/python/` | Human handler lifecycle, terminal presentation, and runtime channel libraries |

## Agent skill

Every host installation includes a portable [Agent Skills](https://agentskills.io/specification)
guide for agents using asys and building teams. It covers environments,
`workers.json`, role prompts, tools/skills/memory, BPMN and FEEL, one-shot,
goal loops, human briefings, model setup, observation, and recovery. It includes
an implementer/reviewer starter with real and dummy environments, plus a goal
workflow. The installed copy works without this source checkout.

```sh
asys skill                         # show PREFIX/share/asys/skills/asys
asys skill ~/.agents/skills        # copy into ~/.agents/skills/asys
asys skill ~/.claude/skills        # copy into ~/.claude/skills/asys
asys skill ./env/development/skills
```

`DEST` is a parent directory. The command creates `DEST/asys`, including all
references and assets. It refuses an existing directory or symlink so local
edits are preserved. Copies are snapshots; to update one, deliberately remove
the previous copy or choose a new destination and copy again.

The format is portable; discovery paths are consumer-specific.
[Codex](https://developers.openai.com/codex/skills#where-to-save-skills) supports
`~/.agents/skills`, project `.agents/skills`, and machine-wide
`/etc/codex/skills`. [Claude Code](https://code.claude.com/docs/en/skills#choose-where-skills-load)
supports `~/.claude/skills`, project `.claude/skills`, and managed skill
locations. Asys workers discover skills packaged in their environment's
`skills/` directory or selected agent's `skills/` directory.

For a system installation such as `PREFIX=/usr/local`, the shared original is
`/usr/local/share/asys/skills/asys`, readable by all users, and `asys skill`
locates it for them. Administrators can use `asys skill /etc/codex/skills` for
machine-wide Codex discovery, or the corresponding location for another
consumer. Consumers supporting symlinked skill directories can link to the
shared original to track upgrades. Installation itself stays within
`PREFIX`/`DESTDIR` and does not modify users' agent configuration.

## Select state directories

By default, asys uses `$XDG_STATE_HOME/asys`, or `$HOME/.local/state/asys`;
dcomp uses `$XDG_STATE_HOME/dcomp`, or `$HOME/.local/state/dcomp`. These are
separate directories. `ASYS_STATE_ROOT` selects the asys base, containing
`runs/`, `inference/`, `human/`, and `config.json` for system-model defaults.
`DCOMP_STATE_ROOT` selects dcomp state.

Initialize a setup in directories you can write:

```sh
asys init /path/to/asys-state --dcomp /path/to/dcomp-state
source /path/to/asys-state/asys-env
```

The command initializes or reuses the explicit dcomp directory, creates asys
state, prints both environment variables, and writes `asys-env` inside the asys
state directory. Source that file in each shell that will use this setup. Without
`--dcomp`, it selects `DCOMP_STATE_ROOT` or dcomp's default without initializing
or changing that directory. Repeating initialization preserves existing state.
When the shell has a prompt, sourcing the file prefixes it with `{ NAME }`,
using the final name of the asys state directory. Repeated sourcing and switching
setups preserve the original prompt without accumulating labels.

For a shared setup, use an existing Unix group that all participating users
belong to:

```sh
asys init /path/to/shared/host --dcomp /path/to/shared/dcomp --group asys
source /path/to/shared/host/asys-env
```

New directories inherit the selected group and allow its members to read,
write, and traverse them; new state files keep group access. Existing initialized
directories retain their permissions. A conflicting `--group` is rejected.
The parent directories and project workspace must also be accessible to these
users, and each user must have access to the same local Docker Engine.
The group shares control of the setup and access to its state, including
provider credentials. This does not create Unix users or groups.

Dcomp can also be initialized independently:

```sh
dcomp init /path/to/dcomp-state --group asys
```

It prints the `DCOMP_STATE_ROOT` export and does not contact Docker. The init
commands require builds containing this feature; reinstall both projects from
their updated source before using them. Initialization does not start components.

## Check the workflow installation

The hello example uses its own dummy environment and needs no inference server:

```sh
asys-bpmn run \
  asys-bpmn/examples/hello/workflow.bpmn \
  asys-bpmn/examples/hello/env/dummy
```

It should print a JSON result containing `Hello from asys.` The launcher starts
its components, waits for completion, and removes them when the run finishes.

Inspect its saved output and job status with:

```sh
asys logs latest
asys status latest
asys top
```

The [monitoring guide](docs/monitoring.md) covers log sources, agent transcripts,
custom state directories, and terminal controls.

Workflows and environment definitions remain ordinary files in their project
repositories. The launcher accepts their paths directly.

To run one assignment with the `simple` system agent, give the environment
directory and assignment. `--model` can select the inference model for this run:

```sh
asys-oneshot ./env/kicad "Review the connector placement" \
  --model account/model --workspace ./project
```

The launchers use the current directory as the actual workspace unless
`--workspace DIRECTORY` is given. Agents modify that directory directly.
Workers and BPMN run with the invoking host user's UID, supplied through
dcomp's `--user` option. Private state uses that user's primary group; shared
state uses its selected group. Project files retain the invoking user's ownership.
Job logs, transcripts, results, and reports are kept in the separate run state.
See the [oneshot guide](asys-oneshot/README.md) for a complete environment example.

For repeated implementation and verification, use the same environment and
`simple` model setting:

```sh
asys-goal ./env/development "Implement the requested behavior" \
  --workspace ./project
```

The [goal guide](asys-goal/README.md) explains human help, evidence-based
verification, and using the goal worker from BPMN. Host launchers supply a
read-only snapshot of model defaults to workers; reinstall and rebuild the
workers image to make the new program and built-in agent prompts available.

## Start the inference server

Create a machine once, then start it:

```sh
asys-inference init
asys-inference start
asys-inference gateway providers
```

For an existing machine, use `start` directly. Authenticate a provider with its
listed identifier, for example:

```sh
asys-inference gateway login openai-codex --as work
asys-inference models
```

Choose a model from that list as the default for the `simple` system agent:

```sh
asys system-model set simple account/model
asys system-model list
```

This writes `config.json` in the selected asys state directory:

```json
{
  "system_models": {
    "simple": "account/model"
  }
}
```

Core asys installs the agent definitions, including the
[`simple` prompt](python/asys/system_agents/simple/prompt.md), independently of
the launchers. Defaults apply across worker environments in that asys setup. `asys-oneshot`
uses the `simple` entry unless `--model MODEL` is supplied. If neither is set,
it exits before creating a run and prints the command to configure it.
`asys system-model list --json` prints the mapping as JSON, including unset
system models as `null`. Add `--root DIRECTORY` to either configuration command
to select another asys state base; a launcher's `--root` selects saved runs only.

The inference server continues running after the command exits and exports
`@inference_endpoint` in the `asys` dcomp system by default. See the
[inference guide](asys-inference/README.md) for adding poolers and other providers.

Host workflow control, model listing, and gateway administration use filesystem channels.
Component RPC uses dcomp interfaces; workflow jobs and results use the shared
runtime filesystem. Dcomp handles container lifecycle and container logs.

To answer human tasks, run `asys-human-prompt --system asys` in another terminal.
Workflows automatically start the shared service at `@human_endpoint`; the
prompt can attach later, and closing it leaves pending jobs waiting. Alternatively, add
`--human` to `asys-bpmn run` to answer just that run in the current terminal.
`--claimant NAME` selects the identity used by tasks with candidate restrictions.
Approval forms offer approve/disapprove; plain questions accept text. See the
[human handler guide](asys-human-interface/README.md) for the answer protocol.

## Update an existing installation

Update the source checkout, then reinstall from its root using the same `PREFIX`
as the original installation. For the default per-user installation:

```sh
make install
```

For a system-wide installation under `/usr/local`:

```sh
sudo make install PREFIX=/usr/local
```

In the shell used to manage the installation, load its generated `asys-env` if
you use a named or shared state directory. For example:

```sh
source /srv/asys/host/asys-env
```

This selects both asys and dcomp state. With the default per-user state, no
environment file is needed. After installing, refresh running shared services:

```sh
asys update
```

This reapplies inference components using their saved configuration and refreshes
the shared Human service's installed image. Account credential volumes and
configuration are retained. Unchanged images are not restarted. `ASYS_STATE_ROOT`
selects the installation; `asys update --root /srv/asys/host` selects it explicitly.
For a running inference installation, the update also starts the shared Human
service if it is missing; no terminal is required. Stopped inference services
remain stopped. Workflow and worker containers, including their
private `--human` handlers, are not restarted. Replacing a shared service can
interrupt calls currently using it; complete pending human decisions before updating.

Every subsequent start or resume uses the updated images. Workflow resumes
automatically rebuild their environment against the installed worker base;
environments without a Dockerfile resolve their current declared image. Keep
the original environment directory available for resume. Running containers
continue using their existing images until explicitly started, resumed, or updated.

Saved machine configuration and run state live under the user's XDG state
directory, normally `$HOME/.local/state/asys`. Runs and their job records use
`asys/runs`; project workspaces remain at the paths supplied by the caller. Installation
does not create a machine or start an inference server automatically.

### Upgrade from 0.1.3

Version 0.1.4 adds definition and review of success criteria to goal jobs, with
accepted contracts and unresolved findings retained between attempts. The goal
command and BPMN job input stay the same; the goal result adds criterion IDs,
statuses and an open-findings list alongside the existing verdict and evidence.

Reinstall with the original `PREFIX` (`make install` rebuilds the images and
updates the host tools and bundled skill). Rebuild environments that use a
prebuilt workers image against the new base. Refresh any separately exported
skill copies. New goal jobs use the updated prompts and loop; running jobs
continue with their existing version. BPMN engine checkpoint compatibility is
unchanged from 0.1.3.

### Upgrade from 0.1.2

Version 0.1.3 changes the BPMN engine adapter and its saved checkpoint format.
It cannot recover or resume BPMN runs recorded by the previous adapter,
including failed runs from 0.1.2. Finish or resume those runs with the previous
installation before upgrading, or start new runs after upgrading. Saved logs,
results, and project files remain available. See the
[engine adapter guide](asys-bpmn/ENGINE.md) for the compatibility boundary.

Reinstall with the original `PREFIX` (`make install` rebuilds the images and
updates the host tools). Restart terminal Human prompts to use the updated
briefing and file preview interface.

### Upgrade from 0.1.1

Version 0.1.2 changes one-shot to use the built-in `simple` agent. Remove the
agent-name argument from existing commands and configure its model, or supply
`--model MODEL` for the invocation:

```sh
asys-inference models
asys system-model set simple account/model
asys-oneshot ./env/development "Implement the requested change" --workspace ./project
```

Use an exported model ID in place of `account/model`. Named environment agents
remain available through their `workers.json` job types; one-shot uses the
system agent with the environment's shared tools, skills, and extensions.

Reinstall with the original `PREFIX` and rebuild the workers image to include
the goal program and system-agent prompts (`make install` does both). New runs
and BPMN resume rebuild environments that have a Dockerfile. Environments using
only a prebuilt image must have that image rebuilt against the new workers base.
The new `asys-goal` launcher and BPMN goal jobs share the `simple` model default.

The installation also includes the [portable agent skill](#agent-skill).
Previously exported copies are independent; update them explicitly when needed.

### Upgrade from 0.1.0

Version 0.1.1 changes the direction of human requests: workers call the Human
service through an input. The current host launcher supplies the correct input
for the built-in `asys-human` worker and for `--human`, including environments
that omit it or declare it as an output. New environments and custom human
programs should declare the input in `component.dcomp`:

```text
input asys.human.v1.Human human
```

Let runs that still need the 0.1.0 human handler finish, then stop that handler.
After reinstalling, start it again in a terminal with the same asys state loaded:

```sh
asys-human-prompt --system asys
```

The shared service binds `@human_endpoint` and continues running without a
terminal. Existing 0.1.0 handlers and foreground-owned 0.1.1 shared handlers
require a one-time restart to adopt this lifetime. Subsequent shared Human
service image updates use `asys update`, with or without a terminal attached.

After reinstalling, environments with a Dockerfile rebuild automatically on
start or resume; running workers keep their current image and wiring.
Use `asys-bpmn run ... --human` or `asys-bpmn resume RUN --human` for
a handler dedicated to one run instead of the shared handler. The BPMN itself
does not need changing.

## Development tests

Tests additionally require Node 22.19 or newer and npm on the host. From the
repository root:

```sh
make test-deps
make test
```

This runs the Go, Python, and Node suites. Docker acceptance tests are separate:
`make -C asys-inference integration` and `asys-bpmn/tools/integration-test`.

The shared-state acceptance test runs two Unix identities in an isolated
container, checking initialization, job execution, logs, and saved workflow
state. After building the images, run from the repository root:

```sh
docker run --rm --user 0:0 --entrypoint python3 \
  --mount "type=bind,src=$PWD,dst=/source,readonly" \
  asys-bpmn:dev /source/test/shared_state.py
```
