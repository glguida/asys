# Author a swarm

Start with the [world-package guide](../skills/asys-authoring/references/worlds.md)
and [named worker schema](../skills/asys-authoring/references/workers.md#swarm).
These are installed in the portable authoring skill. This page connects those
contracts to the implementation resources.

1. Define what the world measures and what constitutes success.
2. Create a named swarm with `asys-workers add ENVIRONMENT swarm NAME`.
3. Implement the generated evaluator and configure a checked initial state.
4. Define member observations, allowed actions, private memory and limits.
5. Validate the deterministic world before spending model calls.
6. Run through `asys-run`; inspect measurements, artifacts and saved decisions.

For a new domain, implement explicit `action_schema`, `initialize`, `observe`,
`step`, `evaluate` and `artifacts` callbacks behind the supplied world service
SDK. The [version-1 protocol](WORLD.md) defines transport, correlation, replay
and bounds. World state and randomness must be explicit; retries must not
introduce duplicate external effects. Change implementation identity when rules
or dependencies change.

The [built-in world guide](../asys-workers/worlds/README.md) describes checked
artifacts, leaderboard visibility and the torus's local neighborhood. The
[terrarium rules](../asys-workers/worlds/terrarium/component/physics.py) demonstrate
an observation policy and evaluation after participants leave the world.

Bounded model members receive only their assignment, local observation, private
memory and action schema. Shared and selected-agent skill instructions are
included, but shell and coding extensions are unavailable. A trusted ordinary
program can provide a deterministic or tool-using policy. Such programs share
the worker environment; member identity alone is not a permissions boundary.

Return useful artifacts and measurements. Declared parent links show claimed
reuse, not proof of causal dependence. Compare approaches using matched tasks,
populations, evaluator rules and measured budgets; see the
[research notes](../docs/swarm-research.md) for the teaching examples' limits.

A viewer exports `mount(element, context)` and returns `update` and `dispose`.
Use `context.theme`, `context.inspect` and `context.selectMember` to share the
dashboard's design, sidebar and conversation selection. See
[design packages](../skills/asys-authoring/references/design.md); rendering saved
frames must never advance execution or submit actions.
