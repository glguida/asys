# Workers, environments and project files

An environment packages executable programs, dependencies, prompts and skills.
A named worker selects an agentic behavior within it. `workers.json` is the
runtime command map; `workers/NAME.json` is the named behavior definition.
Many workers and every stage of a workflow can share one environment.

```sh
asys-workers add ./env/development agent editor
asys-workers add ./env/development goal repair
asys-workers add ./env/development senate review
asys-workers add ./env/development swarm explore
asys-workers list ./env/development
asys-workers describe ./env/development explore
asys-workers edit ./env/development explore
```

The generated files are starting points. Configure prompts, participant roles,
models and evaluation before running. Programs and Human handlers are ordinary
command bindings; workflows select those bindings with explicit `asys:job` types.

The workspace is the existing project directory supplied to `asys-run`.
Jobs edit it directly and share files. Run state records execution separately.
The dcomp system is the namespace used to connect components, not another name
for either directory.

The maintained authoring manual ships inside the portable skill:

- [Create an environment and programs](../skills/asys-authoring/references/environments.md)
- [Define every worker kind](../skills/asys-authoring/references/workers.md)
- [Compose a workflow](../skills/asys-authoring/references/workflows.md)
- [World packages](../skills/asys-authoring/references/worlds.md)
- [Commands and directory semantics](../skills/asys/references/commands.md)
