# Rainkeepers

Eight inhabitants build water collectors and gardens using local observations.
After 24 turns all agents leave and rain stops. The remaining constructions must
keep at least six gardens healthy throughout twelve drought turns.

The world is the supplied `builtin:terrarium` package: a separate component
implementing the world interface plus an embedded DOM viewer. The two environment
variants define the same named swarm with either scripted or model-based members.

From the repository root after installation:

```sh
mkdir -p ./terrarium-work
asys-run ./asys-swarm/examples/terrarium/env/scripted rainkeepers \
  "Build a habitat whose gardens survive after every agent leaves" \
  --workspace ./terrarium-work
asys dashboard --port 8765
```

Open the printed localhost address and select the run and its swarm job. Run the
dashboard in another terminal to follow execution live. Its recorded frames also
work after the components stop. Select `env/agents` with inference configured to
use models through the Provider interface. The full assignment is in `request.md`.

The deterministic policy builds eight healthy gardens, requiring 192 member
decisions over 24 construction turns. Twelve further turns test the constructions
without agents. It tests protocol execution and measured outcomes; it does not
establish an advantage from collective model reasoning.

Inspect [world rules](../../../asys-workers/worlds/terrarium/component/physics.py)
and the [member policy](env/scripted/programs/inhabitant.py). The world supplies
per-agent observations, applies proposed actions and measures survival. It stores
construction provenance and delivered local interactions for inspection.

This teaching example is inspired by research described in the
[research notes](../../../docs/swarm-research.md). It does not reproduce the
original simulator or its reported experiments.
