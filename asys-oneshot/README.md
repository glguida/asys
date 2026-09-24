# Single-agent work

Create a named agent in an environment and execute it with `asys-run`:

```sh
asys-workers add ./env/development agent editor
asys-environment edit ./env/development agents/editor/prompt.md
asys system-model set simple account/model
asys-run ./env/development editor "Implement the requested change and check it" --workspace ./project
```

The bundled `simple` agent also works without adding a definition:
`asys-run ENVIRONMENT simple REQUEST`. It receives the environment's shared
tools and skills. An environment definition named `simple` takes precedence.

The named definition selects agent assets and optional model/step limits.
`--model MODEL` overrides the fallback for this run. `--parameters limits.json`,
with `{"maxSteps":30}` in that file, sets a positive inference-step budget. With no configured limit, steps are
unlimited. Completion returns `final`, `exception` and any requested extra fields.
A completion narrative alone does not independently verify the artifact.

Read [environment authoring](../skills/asys-authoring/references/environments.md),
[worker definitions](../skills/asys-authoring/references/workers.md) and
[operation](../skills/asys/references/operations.md). The agent algorithm is
implemented inside `asys-workers`; this directory contains no public launcher.
