# asys-inference — asys inference machine

`asys-inference` creates and updates a provider network whose public output is
`@inference_endpoint`. It is a short-lived tool with saved configuration;
the providers and dcomp proxy keep running after a command exits.

It reuses Cyclo's gateway, passthrough, pooler, and exact
`cyclo.provider.v1.Provider` contract. `ListModels`, streaming `Infer`, opaque
payloads, cancellation, and typed capacity-exhaustion errors retain their
existing semantics. There is no new inference API, team controller, or central
asys service.

## Build

Requirements: Linux, Go 1.25+, Python 3.10+, Docker CLI, a local Docker Engine, and dcomp 0.3.1.
Install dcomp separately; `dcomp-proxy` must be on PATH or selected with
`DCOMP_PROXY_BINARY=/absolute/path/to/dcomp-proxy`. Docker
builds the Node dependencies inside the component images; host Node is not
needed. Host channel commands use Python's standard library and the bundled
asys-runtime channel client.

From the repository root:

```sh
cd asys-inference
make build
bin/asys-inference version
```

The build produces `bin/asys-inference`. `make install` copies that executable
into `bin/` under `$HOME/.local` by default, and copies the component directories
and their shared dependencies into `share/asys-inference/components/`.
The private channel clients and their Python runtime package are installed under
`share/asys-inference/` too.
`PREFIX` and `DESTDIR` override the installation destination. Dcomp's binaries
are supplied by the separate dcomp installation.

## Create the server

```sh
bin/asys-inference init
bin/asys-inference show
bin/asys-inference start
bin/asys-inference status
bin/asys-inference gateway providers
```

`init` saves a configuration with one gateway and selects `gateway.provider`.
`start` builds the images and starts the provider network. Every invocation
rebuilds providers from their configured sources using Docker's cache, or
resolves the current declared image for sources without a Dockerfile. Updated
images are applied even when the provider configuration is unchanged. Installing
asys leaves existing containers running until a lifecycle command applies the
update.

The start command waits for the selected provider and its local dependencies to
pass their health checks before publishing `@inference_endpoint`. A provider
that exits or fails its health
check makes the command fail, with its exit code and recent log output. Startup
waits are bounded to 90 seconds.

The default machine state is `$XDG_STATE_HOME/asys/inference` or
`$HOME/.local/state/asys/inference`. When `ASYS_STATE_ROOT` is set, the default
is `$ASYS_STATE_ROOT/inference`. Use `asys-inference --root DIR ...` or `ASYS_INFERENCE_STATE_ROOT` for
another machine. The tool saves the configuration; ordinary use does not involve editing its
state files.

The default dcomp system is `asys`, using dcomp's normal state-root rules.

| Command | What it shows |
| --- | --- |
| `components` | Available source directories that can be added to the network |
| `show` | Configured providers, their arguments and connections, and the selected output |
| `status` | Live provider health and the output currently serving `@inference_endpoint` |
| `models` | Model IDs exported by the live `@inference_endpoint`; `--json` includes their metadata |

Each supports `--json`. `show --json` returns configuration without internal
recovery records. Dcomp manifests have no description field; `components`
shows declared inputs and outputs alongside each source name.

Its components are named `asys-inference-gateway`, `asys-inference-NAME`, and so on. You can select
these settings at initialization:

```sh
bin/asys-inference --root /absolute/path/my-machine init \
  --system asys \
  --dcomp-state-root /absolute/path/shared-dcomp \
  --prefix asys-inference
```

`--runtime-root` selects a nondefault proxy root. These settings bind the
machine to its shared system; later commands use the saved values.
`--components-root` or `ASYS_INFERENCE_COMPONENTS_ROOT` selects a different
default component directory at initialization. `init --empty` starts with
no providers or selected output, for a custom root provider.

## Authenticate the gateway

Cyclo's existing account and native Pi authentication flows are retained:

```sh
bin/asys-inference gateway login openai-codex --as work
bin/asys-inference gateway models
bin/asys-inference gateway usage
bin/asys-inference gateway rename work personal
bin/asys-inference gateway logout personal
```

For API-key authentication, pipe the secret through stdin, or name a host
environment variable using `--api-key-env VARIABLE`. The variable name, not
its contents, is passed on the command line:

```sh
bin/asys-inference gateway login openai --as work --api-key-env OPENAI_API_KEY
```

`--authentication auto|oauth|api_key` chooses an explicit login method when
needed. OAuth instructions, device codes, questions, and answers travel through
the filesystem channel. The host displays prompts and hides secret input in
the terminal. Ctrl-C cancels the login; a callback can dismiss a pending prompt
without requiring another keypress. Container-local browser callback limitations
still apply. `gateway --name NAME ...` selects another gateway instance.

Only the gateway receives its own persistent credential volume. Login updates
the live gateway catalogue without restarting providers. All gateway commands
use its filesystem channel, after verifying the live dcomp component and its
mount. A private host directory contains each provider's channel; only that
provider receives the directory. Authentication answers are pruned after
consumption. Inference traffic uses the unchanged dcomp Provider interface.
No asys-inference command stores credential values in `machine.json`.

Run `asys-inference start` to build and apply updated providers. The gateway
retains its credential volume.
The [gateway channel contract](docs/gateway-channel.md) describes
requests, interactive login, cancellation, and restart behaviour.

## Build a provider network

```sh
# Newly added providers become the selected public output by default.
bin/asys-inference add trace passthrough -L upstream=gateway.provider

# Pool two authenticated accounts, using the same pooler options as Cyclo.
bin/asys-inference add pool pooler work personal -L upstream=trace.provider

bin/asys-inference show
bin/asys-inference status --json
bin/asys-inference models
```

Inputs use logical component names within the machine. `trace.provider` maps
to `asys-inference-trace.provider` in dcomp. An external `@GLOBAL` target stays symbolic
and is validated against the shared system at apply time.

Adding a wrapper should bind its upstream directly to the previous output.
Its selected output then becomes `@inference_endpoint`; consumers keep their
existing symbolic wires. `add` selects the new provider; `select NAME` chooses
another existing provider.

Exact-model pooling works unchanged:

```sh
bin/asys-inference add balanced pooler work/model personal/model model=balanced \
  -L upstream=trace.provider
```

The pooler's public model prefix is its logical name, the first argument to
`add`. The provider named `pool` exports `pool/MODEL`; the provider named
`balanced` above exports `balanced/balanced`. The dcomp component prefix does
not affect model IDs. Original account-qualified models remain available.

`asys-inference models` reads the catalogue from the provider currently serving
`@inference_endpoint`, including through passthroughs. `gateway models` lists
only the gateway's account models. Catalogue requests use each provider's
filesystem host channel and call its existing `ListModels` implementation.

A source name is a directory name under the default component directory:

```text
components/
  gateway/component.dcomp
  passthrough/component.dcomp
  pooler/component.dcomp
  myfilter/component.dcomp
```

`add filter myfilter` loads `components/myfilter/component.dcomp`. The tool has
no list of permitted source names. In a source checkout the default directory
is `components/` beside `bin/`; an installed copy uses
`share/asys-inference/components/` under the installation prefix. An explicit
path such as `./myfilter` or `/opt/components/myfilter` selects that directory
directly. Named sources use the default component directory as their Docker
build context; explicit paths use the source directory.

A component may include `runtime.json` with dcomp runtime defaults such as
`external_egress`, `volumes`, `ports`, and `args`. These are copied into the
machine configuration on addition; explicit component arguments replace the
default arguments. An optional `host_channel` name declares a filesystem host
interface. On start, the tool prepares its `channels/NAME/in` and `out`
directories and mounts that provider's host root at `/run/asys-host`.
It also writes `provider.json` there with the provider's logical name.
The gateway declares `"host_channel": "gateway"` alongside its credential
volume and egress settings; it publishes no host TCP port.
Pooler and passthrough declare `"host_channel": "provider"` for catalogue
access. Custom providers can implement the same
[catalogue channel contract](docs/provider-channel.md) to support `models`.

Custom providers use the same `component.dcomp` manifest as Cyclo:

```text
docker example/policy:1
input cyclo.provider.v1.Provider upstream
output cyclo.provider.v1.Provider provider
```

```sh
bin/asys-inference add policy /absolute/path/policy -L upstream=pool.provider
```

A source with a `Dockerfile` is built using Docker's native layer cache on
start or addition; otherwise its image must already exist locally. Built images use machine-specific tags and are resolved to immutable
IDs before dcomp applies them. Each provider must expose exactly one Provider
output and declare a meaningful image health check. Additional typed interfaces
are allowed. The optional port-8080 Provider REST server remains unchanged in
the bundled components, but asys-inference does not require or publish an outer-provider
HTTP API: its public contract is solely `@inference_endpoint`.

The add grammar is:

```text
asys-inference add NAME SOURCE [COMPONENT_ARGUMENTS...] [-L INPUT=TARGET ...]
```

`asys-inference` extracts `-L INPUT=TARGET` or `--link INPUT=TARGET` (also
`--link=INPUT=TARGET` and `-L=INPUT=TARGET`) anywhere after `add`, including
before or between `NAME` and `SOURCE`. The first two remaining tokens are
`NAME` and `SOURCE`. Every subsequent token is passed unchanged to the component, including
flags, `--`, and values such as `model=balanced`. There is no `--arg` wrapper
for `add`.

## Modify the running server

```sh
bin/asys-inference select policy
bin/asys-inference select gateway.provider
bin/asys-inference remove policy
bin/asys-inference select -
bin/asys-inference start
```

Each mutation validates and saves desired configuration, then applies it.
Changes are applied explicitly. `start` also applies provider source or image
changes. When adding a provider or selecting a different output, the previous
public output is retained until the selected provider and its local dependencies
pass their health checks. A failed addition stays configured for inspection
and removal, while the previous output remains selected in dcomp.

`select -` leaves `@inference_endpoint` declared but unbound. Removing the
selected provider does the same. Removing a provider drops direct wires to
its removed outputs; symbolic consumers retain their links and their calls
fail while the global is unbound.

Reassignment closes streams whose concrete route changes; new connections
resolve the global's new output. Surviving endpoint sockets retain their
inodes. Unchanged providers retain their containers.

## Independent consumers and the dashboard

Another program can join the same dcomp system with its own component:

```sh
dcomp add-component --link inference=@inference_endpoint \
  asys team /path/to/team-component
dcomp dashboard asys
```

The consumer's manifest must declare an input named `inference` of type
`cyclo.provider.v1.Provider`. Use the same dcomp state root and runtime root as
shown by `asys-inference show`. The example input name is local to that consumer; it is
not imposed by asys-inference.

The dashboard shows the combined system. Clicking `@inference_endpoint` in
the sidebar highlights the selected output and symbolic consumers. Team
components and their wires are owned by their contributing program.

## Stop and recovery

```sh
bin/asys-inference stop
bin/asys-inference start
```

`stop` records a stopped desired state, removes asys-inference's components, and leaves
the global unbound. Configuration and credential volumes survive. `start`
recreates the provider network from that configuration. Neither command calls
whole-system `dcomp down`, so other programs' components remain in place.

Failed updates preserve the saved configuration. After correcting a missing
image or source dependency, `start` finishes applying it. To correct a provider's
arguments, remove it and add it again with the right arguments. Interrupted
updates are recovered internally by the next operation.

`status` distinguishes the requested output from the output currently serving.
An unused failed provider remains visible in the table, but does not prevent
selecting a healthy provider. A nonoperational requested endpoint makes
`status` exit 1, while still printing its requested output format. Health checks
verify provider readiness; exported models can be inspected with `models`.

## Verification

```sh
make test
go test -race ./...
make integration
```

The Docker test uses fresh state, no credentials, and no paid model calls. It
runs the actual gateway, passthrough, and pooler, plus a test endpoint using the
same Provider protocol. It checks opaque streaming, cancellation, typed
pre-stream failover, startup failure reporting, preservation of the serving
endpoint during failed additions, independent consumers with explicit UID/GID
and moved bind sources,
concurrent dcomp additions, recovery after killing a journaled update,
saved-intent retry, and stop/start with persistent credential-volume identity.
It does not authenticate a real account or make live model requests.

`components/` contains the provider implementations, protocol sources, and
generated bindings. The Go library in `third_party/dcomp/` includes dcomp's
shared-state permission support; the component transport uses the dcomp Node SDK 0.3.0. License notices are
included with the sources.
