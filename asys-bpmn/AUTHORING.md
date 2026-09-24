# Author environments and workflows

The authoring documentation is shared by people and the installed
[asys-authoring skill](../skills/asys-authoring/SKILL.md). Start with the
acceptance criteria, choose the worker types, and define exact input/output
contracts before drawing control flow.

1. [Create the environment](../skills/asys-authoring/references/environments.md):
   dependencies, command mappings, programs, tools, prompts, skills and extensions.
2. [Define workers](../skills/asys-authoring/references/workers.md): agents, goals,
   professional Senate roles, swarm members and world configuration.
3. [Write the workflow](../skills/asys-authoring/references/workflows.md): complete
   XML, FEEL inputs, shared artifacts, branches, joins, revision and failures.
4. [Prepare Human help](../skills/asys-authoring/references/human.md): concrete
   evidence, typed choices, comments and a real revision path.
5. [Use advanced patterns](../skills/asys-authoring/references/advanced-workflows.md)
   when collections, ad-hoc coordination, data mappings or called processes help.
6. [Validate the result](../skills/asys-authoring/references/validation.md):
   bindings, deterministic checks, meaningful acceptance and recorded evidence.

The [team template](../skills/asys-authoring/assets/team/workflow.bpmn) includes
program and agent environments with matching bindings. Its deterministic
fixture exercises a rejected review followed by revision. The
[goal template](../skills/asys-authoring/assets/goal.bpmn) binds the same named
goal used by the public runner. The [mixed review example](examples/mixed-review/README.md)
adds parallel swarms, an independent checker, a Senate and Human feedback.

```sh
asys-workers add ./env/development agent editor
asys-workers add ./env/development goal repair
asys-workers add ./env/development senate review
asys-run ./env/development workflow.bpmn --input request.md --workspace ./project
```

Execution order comes from sequence flows, not diagram coordinates. A shared
workspace carries artifacts; job results carry compact routing and handoff data.
A review must inspect the specification and artifacts independently. Keep
revision feedback in the next request and repeat validation before acceptance.

For reusable domains, also read [world packages](../skills/asys-authoring/references/worlds.md).
For presentation, read [dashboard design packages](../skills/asys-authoring/references/design.md).
Neither requires changing the workflow execution semantics.
