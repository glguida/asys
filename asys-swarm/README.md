# Swarm workers

A named swarm coordinates bounded member decisions against a separate world
component. It owns member identities, private memory, turns, budgets and
checkpoints. The world owns observations, actions, state transitions and measured
evaluation. A workflow sees the whole swarm as an ordinary job.

```sh
asys-workers add ./env/research swarm search
asys-workers edit ./env/research search
```

Configure the generated world evaluator, initial candidate and limits before
running. The starter rejects unconfigured work before inference. Then:

```sh
asys-run ./env/research search "Find a candidate satisfying the measured target" --workspace ./project
asys dashboard
```

Use [authoring](AUTHORING.md) for package and member contracts. The
[route sample](../asys-workers/worlds/README.md#route-example) provides deterministic
leaderboard and local torus environments. [Rainkeepers](examples/terrarium/README.md)
provides scripted and model inhabitants in a persistent habitat.

World state, evaluated metrics, exported artifacts and status determine the
outcome. An exhausted budget is not proof of achievement. Pausing a swarm stops
new decisions while retaining execution state; freezing the dashboard only stops
display updates. Replay reads recorded frames after components have stopped.
Member selection exposes saved conversations and decision outputs separately.

World components are provisioned by the shared host lifecycle for standalone
and workflow execution. Workers do not import world code or start world processes.
The [World protocol](WORLD.md) defines the isolated runtime session contract.
Python swarm tests run with `make test` in this directory; the root test suite
also exercises host preparation, saved views, installation and the dashboard.
