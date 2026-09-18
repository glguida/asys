# Setup and commands

## Skill distribution and discovery

Asys installs this portable [Agent Skills](https://agentskills.io/specification)
directory under `PREFIX/share/asys/skills/asys`. `PREFIX` defaults to
`$HOME/.local`; a machine installation can use `/usr/local`. The files are
readable by all users who can traverse that installation directory.

```sh
asys skill                         # print the bundled directory
asys skill ~/.agents/skills        # creates ~/.agents/skills/asys/
asys skill ~/.claude/skills        # creates ~/.claude/skills/asys/
asys skill ./env/development/skills
```

`DEST` is the parent skills directory, not the final `asys` directory. Missing
parents are created. An existing `DEST/asys` is left untouched and reported as
an error, including symlinks. Copy into a new destination or deliberately remove
your previous copy when updating. Copies are independent snapshots; installing
a newer asys updates the bundled original, not copies made earlier.

Discovery directories belong to each agent product; the standard specifies
the skill format, not one machine-wide search path. Current documented examples:

| Consumer | User/project destination | Machine-wide installation |
| --- | --- | --- |
| Codex | `~/.agents/skills`, `.agents/skills` | `/etc/codex/skills` |
| Claude Code | `~/.claude/skills`, `.claude/skills` | Use its documented managed skill location |
| Asys workers | `ENVIRONMENT/skills`, or `ENVIRONMENT/agents/NAME/skills` | Package it in the environment image |

For example, an administrator can run `asys skill /etc/codex/skills` with
permission to write there. For a supported agent that follows symlinked skill
folders, a symlink to the shared original can instead track asys upgrades.
Asys does not edit every user's home directory during installation.

Check the consumer's current discovery instructions if a copy is not found:
[Codex](https://developers.openai.com/codex/skills#where-to-save-skills) and
[Claude Code](https://code.claude.com/docs/en/skills#choose-where-skills-load).
The payload uses standard `name`/`description` frontmatter and Markdown; it does
not require Codex-specific metadata or invocation syntax.

## Host installation

Running asys needs Linux, Python 3.10+, Docker CLI with a local Docker Engine,
and separately installed dcomp 0.3.1+ with Machine API 2. `dcomp-proxy` must be
on `PATH` or selected with `DCOMP_PROXY_BINARY`. Building the inference command
requires Go 1.25+. Worker Node/Pi dependencies are installed in container images;
running the host tools does not require host Node. Installing the Human terminal
requires pip for its private pinned dependencies.

From an asys source checkout:

```sh
make install                       # default PREFIX=$HOME/.local
make install PREFIX=/usr/local     # destination and Docker must be accessible
```

Add `PREFIX/bin` to `PATH`. `make build` builds commands and images without
installing. `make install-host` installs core asys, built-in agent definitions,
and this skill without Docker; it does not install every launcher or build
their images. `PREFIX` and `DESTDIR` apply to host packaging. Dcomp is a separate
project and installation.

Per-component installation is available with `make -C asys-bpmn install`,
`make -C asys-oneshot install`, `make -C asys-goal install`,
`make -C asys-inference install`, and
`make -C asys-human-interface install`. Goal includes the Human handler.
Launchers' `install-host` targets are useful when the images already exist.

## Select an asys setup

Keep installed software, runtime state, and the project workspace separate.

```sh
asys init /path/to/asys-state --dcomp /path/to/dcomp-state
source /path/to/asys-state/asys-env
```

Initialization preserves existing state. It does not start services. With
`--dcomp`, it initializes or reuses that dcomp state; without it, it selects
the existing/default dcomp state without initializing it. Source `asys-env` in
each shell using the setup, including the shell running the Human terminal.

| Setting | Default / purpose |
| --- | --- |
| `ASYS_STATE_ROOT` | Asys base; otherwise `$XDG_STATE_HOME/asys`, normally `~/.local/state/asys` |
| `DCOMP_STATE_ROOT` | Dcomp state; otherwise its XDG state directory |
| `STATE/runs/` | Workflow, one-shot, and goal run records |
| `STATE/config.json` | `system_models` defaults |
| `STATE/inference/` | Inference-machine state |
| `STATE/human/` | Human service and terminal state |

For users sharing the same setup:

```sh
asys init /srv/asys/host --dcomp /srv/asys/dcomp --group asys
source /srv/asys/host/asys-env
```

The Unix group must already exist and include the participating users. The
workspace, parent directories, and local Docker Engine must also be accessible.
The group shares control and state, including provider credentials. This is
separate from making the installed skill readable by all users. New state
inherits group permissions; initialization does not recursively change existing
directories.

## Start inference and choose models

For a new inference machine:

```sh
asys-inference init
asys-inference start
asys-inference gateway providers
asys-inference gateway login openai-codex --as work
asys-inference models
```

Choose an actual provider identifier from `gateway providers`. The OAuth login
above is an example. For an API-key provider, pass the name of an existing host
environment variable rather than the secret value on the command line:

```sh
asys-inference gateway login openai --as work --api-key-env OPENAI_API_KEY
```

The running machine exports `@inference_endpoint` in dcomp system `asys` by
default. `models` queries that endpoint's catalogue. Use an exact exported
model ID rather than guessing the account/model spelling.

```sh
asys system-model set simple account/model
asys system-model list
asys system-model list --json
```

`account/model` is a placeholder to replace with an exported ID. The `simple`
setting is shared by one-shot and goal. Their explicit `--model MODEL` wins over
the default. An unset default without an override fails before a single-job run
starts and explains how to configure it. BPMN role jobs usually select their
models in `workers.json`; a BPMN goal job uses the `simple` setting unless its
worker command overrides it. Model settings are snapshotted when workers start;
editing defaults does not change an already running component.

## Command and parameter reference

Use each installed command's `--help` for the exact version present on the
machine. The public execution commands are:

```text
asys-oneshot ENVIRONMENT_DIRECTORY PROMPT [OPTIONS]
asys-goal ENVIRONMENT_DIRECTORY GOAL [OPTIONS]
asys-bpmn run WORKFLOW.bpmn ENVIRONMENT_DIRECTORY [OPTIONS]
asys-bpmn resume RUN [--root DIRECTORY] [--human]
```

| Option | Applies to | Meaning |
| --- | --- | --- |
| `--workspace DIRECTORY` | New runs of all three launchers | Existing actual project directory; defaults to current directory |
| `--root DIRECTORY` | All launchers | Parent of saved run directories, **not** the model-configuration state base |
| `--model MODEL` | One-shot, goal | Override the `simple` inference model for this run |
| `--max-attempts N` | Goal | Explicit positive implementation/verification cycle limit; default unlimited |
| `--input FILE` | BPMN run | UTF-8 text becomes `request`; `-` reads stdin |
| `--process ID` | BPMN run | Select an executable process from the document |
| `--name NAME` | BPMN run | Override display/component name derived from definitions |
| `--human` | BPMN run/resume | Private Human service and terminal for this run |
| `--system NAME` | New runs | Dcomp system; default `asys` |
| `-L INPUT=TARGET`, `--link INPUT=TARGET` | New runs | Repeatable component input link; target `@GLOBAL` or `COMPONENT.OUTPUT`, `-` leaves it unconnected |
| `--dcomp-state-root DIRECTORY` | New runs | Override dcomp state location |
| `--runtime-root DIRECTORY` | New runs | Override dcomp proxy root; this is not the workspace or run storage |

Options may be interspersed with positional arguments. One-shot and goal accept
the assignment as literal text, not a filename option. BPMN's `--input` is not
a JSON variables file. Do not combine `--input -` and `--human`: the terminal is
needed for human answers. Resume reuses stored workspace, environment and dcomp
settings; it accepts only its own documented options.

Other host commands:

| Command | Purpose / options |
| --- | --- |
| `asys init DIR` | Initialize state; `--dcomp DIR`, `--group GROUP` |
| `asys skill [DEST]` | Locate or export this portable guide |
| `asys system-model set NAME MODEL` | Set a default; `--root` selects the state **base** |
| `asys system-model list [--json]` | List defaults and unset packaged system-model names |
| `asys ps [--json]` | List saved runs |
| `asys status [RUN] [--json]` | Inspect jobs and outcomes |
| `asys logs [RUN] [JOB]` | Run log, or worker output when a job is selected |
| `asys top [RUN]` | Interactive run/job/transcript monitor |
| `asys update` | Apply installed images to running shared services; `--root` selects state base |

For `ps/status/logs/top`, `--root` selects saved runs. `RUN` can be an ID,
unique prefix, `latest`, or run directory. Logs accept `-f/--follow`,
`-n/--lines N`, `--stream both|stdout|stderr`, and
`--source run|events|jobs|components|commands`. JOB and `--stream` select job
logs and cannot be combined with a different source.

## Inference networks and updates

Use `asys-inference show`, `status`, `components`, and `models` (also `--json`)
to distinguish saved configuration, live health, installed sources, and models
actually exported. `gateway models` shows only the gateway's models.

```sh
asys-inference add trace passthrough -L upstream=gateway.provider
asys-inference add pool pooler work personal -L upstream=trace.provider
asys-inference models
```

Adding a provider selects its output; `select NAME` chooses an existing one.
Wire wrappers to the previous provider directly, not back to their own public
endpoint. A pooler named `pool` exports `pool/MODEL`; select from the live
catalogue. Account-qualified models remain available. `--root DIR` or
`ASYS_INFERENCE_STATE_ROOT` selects a machine; initialization can set its
system, dcomp state, runtime root, prefix, and components root.

After updating source, reinstall with the original `PREFIX`. Existing run
containers keep their images. New runs and BPMN resume rebuild environments
with Docker's cache. `asys-inference start` applies current provider images;
`asys update` refreshes running shared services. Replacing a Human service can
interrupt unanswered requests, so finish decisions before updating that service.
