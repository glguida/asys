# Rainkeepers: a swarm terrarium

This directory is an ordinary scenario package. Its name has no meaning to the
launcher. Copy it anywhere, rename it, change its configuration and run the new
path. The Python world and browser view live here, not in the engine.

Eight initially equivalent inhabitants have 24 turns to build a small habitat.
Collectors store rain, gardens consume water and channels extend connections.
Then **every agent is removed and rain stops**. The completed constructions must
keep at least six gardens at health 50 or above throughout twelve drought turns.
The authoritative simulation measures the outcome; an agent claiming success
cannot complete the objective.

This is a teaching simulation inspired by the persistent-artifact idea in MIT's
SwarmWorld research. It is not the MIT simulator, a reproduction of the paper's
results, or a realistic ecology model. A successful scripted run demonstrates
the runtime and evaluation path, not emergent intelligence or a swarm advantage.

## Run it

Install/build asys and its runtime image using the repository's installation
instructions, then run from the repository root:

```sh
asys-swarm run ./asys-swarm/examples/terrarium/swarm.json ./asys-swarm/examples/terrarium/env/scripted --view
```

The scripted environment runs a short deterministic Python program through
ordinary runtime jobs. It requires no model account or network. Every inhabitant
uses the same policy: build a collector and garden, announce the collector
locally, then wait. This makes a predictable installation check and a clear
baseline. The browser address is printed by the launcher; pause, resume and stop
controls go through the host's runtime channel bridge.

For actual model decisions, set up and authenticate inference in the usual way,
choose an exported model, and select the agent environment:

```sh
asys system-model set simple account/model
asys-swarm run ./asys-swarm/examples/terrarium/swarm.json ./asys-swarm/examples/terrarium/env/agents --view
```

Replace `account/model` with a name from `asys-inference models`. The environment
uses the configured `simple` model by default. To select a different model only
for this environment, add `--model`, `account/model` to the command vector in its
`workers.json`. Inference uses the unchanged Provider endpoint. The population
size and simultaneous decisions are independent; this example allows eight
agents and four concurrent decisions. The real-model policy is not guaranteed
to meet the objective. The configured half-second minimum between world turns
makes the autonomous drought visible; changing it does not change the physics.

## What the view shows

- A grid of agents, material pockets and persistent constructions. Select a
  cell to inspect its builder, water and health, or an agent to see its material
  and latest local message.
- A graph made from delivered local messages and physical connections to other
  agents' constructions. No hubs or roles are prescribed. Proximity alone does
  not create a graph edge.
- Measured healthy gardens, water reserves and a journal of actual world events,
  including rejected actions. The chart continues when the agents are gone.
- The automatic drought phase. There are no agent decisions or model calls
  during this evaluation. The objective remains unverified until it finishes.

`Save snapshot` downloads the observed state for inspection. The controller's
saved run records include world state, events and exported artifacts. The world's
artifact export contains constructions, builders, physical connections,
measurement history and the agent-free evaluation result. It is not a saved
trained model or proof that the same design will work under different rules.

## Rules and customization

Edit `swarm.json` to change the mission, world dimensions, local observation
radius, population, seed, budgets, discovery duration or drought duration.
`objective.healthyGardens` sets the minimum number of healthy gardens required
throughout the drought. The health threshold is fixed at 50 in this world's
evaluation function. Objective wording alone does not change simulator rules.

All agents start with four units of material and the same instructions. On each
turn an agent proposes one action:

| Action | Effect |
| --- | --- |
| `move` with `dx`, `dy` | Move one cardinal cell; agents may share cells. |
| `gather` with `x`, `y` | Take one material from a reachable resource pocket. |
| `build` with `x`, `y`, `kind` | Build on reachable bare ground: collector or garden costs 2, channel costs 1. |
| `repair` with `x`, `y` | Spend one material to restore up to 30 health. |
| `message` with `message` | Deliver up to 240 characters to agents within local observation range. |
| `wait` | Advance without changing the world. |

Reach means the current cell or one cardinal neighbor. Rain adds three water per
collector per discovery turn, capped at 32. A garden draws one water from an
adjacent collector or one reached through cardinally connected channels.
Collectors are considered in row order, as are gardens competing for water.
Supplied gardens recover four health; dry gardens lose twelve. Actions commit
in sorted agent-ID order, so runtime response order cannot decide conflicts.
Rejected actions are recorded and have no physical effect.

Modify `world.py` to change the physics or goal evaluator. Its module implements
the generic `initialize`, `observe`, `step`, `evaluate`, `artifacts` and
`action_schema` functions; it uses only Python's standard library. Initialization
is seeded, and every later transition depends only on serialized state and
actions. World code is trusted scenario code. Its state stays with the controller;
decision workers receive only their local observation and private memory.

The drought is a separate agent-free evaluation phase with a declared rule; it
is not a hidden randomly sampled benchmark. To investigate generalization,
define extra held-out disturbances and repeated trials, and compare interacting
agents with independent agents, a sequential agent and the scripted baseline
under matched inference and experiment budgets. This demo alone establishes no
performance advantage from communication.
