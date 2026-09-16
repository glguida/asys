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
installs only the `asys` observer and requires no Docker. Use
`make -C asys-inference install` for inference management, or
`make -C asys-bpmn install` for the workflow launcher and observer.
`make -C asys-oneshot install` installs the single-agent launcher and observer
and builds the shared worker images.
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
| `bin/asys` | State initialization, run listing, status, logs, and terminal monitor |
| `bin/asys-inference` | Inference server management command |
| `bin/asys-bpmn` | BPMN workflow launcher |
| `bin/asys-oneshot` | Run one named agent in an environment |
| `bin/asys-human-prompt` | Foreground human handler with a queue of text questions |
| `share/asys/python/` | Shared observation, environment launch, and runtime queue packages |
| `share/asys-inference/components/` | Provider component sources and build dependencies |
| `share/asys-inference/gateway-channel` | Private host client for gateway administration and login |
| `share/asys-inference/provider-channel` | Private host client for the exported model catalogue |
| `share/asys-inference/python/` | Bundled runtime channel library |
| `share/asys-bpmn/python/` | BPMN progress formatting |
| `share/asys-human/python/` | Human handler lifecycle, terminal presentation, and runtime channel libraries |

## Select state directories

By default, asys uses `$XDG_STATE_HOME/asys`, or `$HOME/.local/state/asys`;
dcomp uses `$XDG_STATE_HOME/dcomp`, or `$HOME/.local/state/dcomp`. These are
separate directories. `ASYS_STATE_ROOT` selects the asys base, containing
`runs/`, `inference/`, and `human/`. `DCOMP_STATE_ROOT` selects dcomp state.

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

To run a single agent, give its environment directory, its entry in
`workers.json`, and the assignment:

```sh
asys-oneshot ./env/kicad pcb "Review the connector placement" --workspace ./project
```

Both launchers use the current directory as the actual workspace unless
`--workspace DIRECTORY` is given. Agents modify that directory directly.
Workers and BPMN run with the invoking host user's UID, supplied through
dcomp's `--user` option. Private state uses that user's primary group; shared
state uses its selected group. Project files retain the invoking user's ownership.
Job logs, transcripts, results, and reports are kept in the separate run state.
See the [oneshot guide](asys-oneshot/README.md) for a complete environment example.

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

The inference server continues running after the command exits and exports
`@inference_endpoint` in the `asys` dcomp system by default. See the
[inference guide](asys-inference/README.md) for adding poolers and other providers.

Host workflow control, model listing, and gateway administration use filesystem channels.
Component RPC uses dcomp interfaces; workflow jobs and results use the shared
runtime filesystem. Dcomp handles container lifecycle and container logs.

To answer human tasks, run `asys-human-prompt --system asys` in another terminal.
It discovers Human outputs, including workers added by later workflow runs.
`--claimant NAME` selects the identity used by tasks with candidate restrictions.
Approval forms offer approve/disapprove; plain questions accept text. See the
[human handler guide](asys-human-interface/README.md) for the answer protocol.

## Update an existing installation

Update the source checkout, then run from its root:

```sh
make install
```

Run `asys-inference start` to rebuild and apply updated providers to an existing
machine; its account credential volume is retained. Updates use the component
source paths recorded in that machine's configuration.

Every subsequent start or resume uses the updated images. Workflow resumes
automatically rebuild their environment against the installed worker base;
environments without a Dockerfile resolve their current declared image. Keep
the original environment directory available for resume. Running containers
continue using their existing images until explicitly started or resumed.

Saved machine configuration and run state live under the user's XDG state
directory, normally `$HOME/.local/state/asys`. Runs and their job records use
`asys/runs`; project workspaces remain at the paths supplied by the caller. Installation
does not create a machine or start an inference server automatically.

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
