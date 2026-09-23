# Rainkeepers

Eight agents build water infrastructure for a garden habitat. At the end of
24 discovery turns every agent leaves. Rain stops for twelve turns, and the
world checks whether at least six gardens remain healthy throughout the drought.
The useful output is the construction and its measured survival.

This is a teaching simulation inspired by the local interaction and persistent
artifacts studied in [SwarmWorld](../../../docs/swarm-research.md). It is not the
MIT simulator or a reproduction of that paper's numerical results.

## Run

From the repository root:

```sh
make -C asys-swarm build
mkdir -p ./terrarium-work
asys-swarm/tools/asys-swarm run \
  asys-swarm/examples/terrarium/swarm.json \
  asys-swarm/examples/terrarium/env/scripted \
  --world asys-swarm/examples/terrarium/world \
  --workspace ./terrarium-work --view
```

The launcher prints the browser address. The deterministic policy uses no
inference calls and demonstrates that the environment can meet its goal. Select
`env/agents` with a configured inference endpoint to use model decisions.

To run the world on the host, replace `--world ...` with:

```sh
--world-command '["python3","asys-swarm/examples/terrarium/world/serve.py"]'
```

Both modes use the same runtime channel protocol. The world service is a
separate executable; its source is never imported into the swarm worker.

## What lives here

| File or directory | Purpose |
| --- | --- |
| `swarm.json` | Mission, objective, population, limits and world channel |
| `world/` | Independently packaged physics, evaluator and service executable |
| `env/scripted/` | Deterministic member policy |
| `env/agents/` | Model member policy and Provider input |
| `view.html` | Browser presentation of published state |

The complete swarm runs as one job in workers. Its algorithm retains identities,
private memory and plans and schedules member decisions. Members see only their
local observation. The world implements local physics, messages, construction
rules and evaluation; it receives participant IDs but no private member memory.

## Rules and outputs

Rain collectors store water. Gardens consume water from adjacent collectors;
channels can connect more distant constructions. Agents gather materials, build,
move and communicate through the world's allowed actions. Construction persists
after the agents leave.

During the drought the world advances with no model decisions. The exported
artifacts contain the habitat, constructions and measured outcome. Check
`achieved` and the metrics rather than treating an agent's success claim as proof.
The scripted run makes 192 member decisions across 36 world turns, all within
one runtime job.

Edit `swarm.json` to change the mission and budgets. Change `world/physics.py`
and rebuild the world image to change rules. Its content digest identifies the
implementation during recovery and replay. Different local or global observation
policies require no change to the swarm algorithm.

See [the authoring guide](../../AUTHORING.md) for the world service contract and
worker input/output formats.
