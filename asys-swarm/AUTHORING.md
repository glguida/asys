# Program a swarm towards a goal

A swarm experiment has four parts: instructions, a world with permitted actions,
a measurable objective, and exported work. A mission alone tells agents what to
try; it cannot define tools, change physics or certify that a complex system is
correct. You program those capabilities and checks in the world and environment.

| Part | Where it lives | Example |
| --- | --- | --- |
| Mission prompt | `swarm.json` → `mission` | Build a habitat that survives drought. |
| Agent instructions and model | Environment `workers.json` and `agents/NAME/prompt.md` | Use local observations, retain useful memory and submit actions. |
| Objective parameters | `swarm.json` → `objective` | `{"healthyGardens": 6}` |
| Success test | World `evaluate(state, objective)` | Count gardens that stayed healthy after builders left. |
| Output | World `artifacts(state)` plus automatic state/trace/result files | Construction plans, provenance and measured survival. |

The mission and objective parameters are both passed to each model decision.
Keep them consistent: changing `objective.healthyGardens` changes the check;
changing “six” to “ten” in prose alone does not. An evaluator can require several
conditions and withheld tests. If success is qualitative, record a proposed
result and arrange an independent review instead of returning `achieved: true`
because an agent claims to be finished.

## Package and configuration

You implement a world module, not another runtime or controller component.
The same `asys-swarm` controller loads each world's rules. The worker environment
is the existing asys environment contract: it supplies the decision command,
prompts, models and any evaluation tools. Worlds describe the problem; worker
environments supply capabilities. They can be reused independently when their
action and observation contracts are compatible.

For example, `observe()` can expose nearby habitat cells or a shared archive of
checked results. `step()` applies contributions according to the world's rules.
An environment can wrap the decision worker with a trusted evaluator before
returning a result. These information and validation policies belong to the
world and environment rather than the engine.

Put each experiment in a dedicated directory:

```text
my-experiment/
  swarm.json
  world.py
  view.html                       optional custom browser view
  env/
    component.dcomp
    Dockerfile
    workers.json
    agents/researcher/prompt.md
```

Run `asys-swarm run ./my-experiment/swarm.json ./my-experiment/env --workspace
./work --view`, with an existing `./work` directory. The launcher snapshots the
configuration's directory, not just the JSON file. Paths to world/view files are
relative to it, cannot escape it and cannot traverse symlinks. The snapshot is
limited to 2,000 files and 32 MiB; VCS, dependency, cache and run-state directories
are excluded. Keep generated work outside the package.

```json
{
  "version": 1,
  "name": "Target builder",
  "mission": "Cooperate to raise the shared count to 12 using legal additions.",
  "world": {"module": "world.py", "settings": {}},
  "objective": {"target": 12},
  "agents": {"count": 4, "type": "swarm-step"},
  "limits": {
    "turns": 20, "decisions": 80, "concurrency": 2,
    "seconds": 300, "jobSeconds": 30,
    "actions": 2, "memoryBytes": 2048, "outputTokens": 1024,
    "tickSeconds": 0.05
  },
  "seed": 7
}
```

Only `version`, `mission` and `world.module` are required. Name defaults to the
configuration filename, objective to `null`, world settings to `{}`, population
to 8, job type to `swarm-step` and seed to 0. Unknown configuration keys fail
validation, helping catch misspellings.

| Limit | Default | Allowed | Meaning |
| --- | ---: | --- | --- |
| `turns` | 100 | 1–10,000 | Maximum committed world transitions. |
| `decisions` | 1,000 | 1–1,000,000 | Reserved decision attempts, including interrupted retries. |
| `concurrency` | 4 | 1–64 | Maximum outstanding runtime decision jobs. |
| `seconds` | 600 | 0.1–86,400 | Whole-run wall time, including pauses. |
| `jobSeconds` | 60 | 0.1–3,600 | Per-job time, including queueing and inference. |
| `actions` | 4 | 1–16 | Maximum proposed actions per decision. |
| `memoryBytes` | 4,096 | 4–65,536 | Maximum compact UTF-8 JSON private memory; `null` uses 4 bytes. |
| `outputTokens` | 2,048 | 128–16,384 | Requested maximum generated tokens per model call. |
| `tickSeconds` | 0.05 | 0–5 | Minimum delay between world turns; independent of physical rules. |

`agents.count` permits 1–256; `seed` is an integer from 0 through 2³²−1. Agent
count does not require that many simultaneous model calls. Model context windows
and provider rate limits still apply. There is no aggregate token-budget setting.

## Implement the world

`world.module` names a trusted Python file implementing six functions. The
controller image includes the standard library and `jsonschema`; extra world
dependencies need a derived image selected through `ASYS_SWARM_IMAGE`.

The host snapshots the experiment directory and mounts it read-only at
`/opt/asys/swarm-package` inside the controller. The controller uses Python's
`importlib` to load the configured module from that directory, then calls its
functions in the controller's own Python process. The host does not import the
world. Editing ordinary world code requires no controller image rebuild.

Here is the complete `world.py` for the target example above:

```python
def action_schema():
    return {
        "type": "object",
        "properties": {"add": {"type": "integer", "minimum": 1, "maximum": 2}},
        "required": ["add"],
        "additionalProperties": False,
    }

def initialize(settings, agent_ids, seed):
    return {"count": 0, "turn": 0, "contributions": {a: 0 for a in agent_ids}}

def observe(state, agent_id):
    return {"count": state["count"], "yourContribution": state["contributions"][agent_id]}

def step(state, actions):
    events = []
    for agent_id, action in sorted(actions.items()):
        amount = action["add"]
        state["count"] += amount
        state["contributions"][agent_id] += amount
        events.append({"type": "added", "agent": agent_id, "amount": amount})
    state["turn"] += 1
    return {"state": state, "events": events}

def evaluate(state, objective):
    target = objective.get("target", 12)
    return {"achieved": state["count"] >= target,
            "metrics": {"count": state["count"], "target": target},
            "summary": f"Count is {state['count']}; target is {target}."}

def artifacts(state):
    return {"count": state["count"], "contributions": state["contributions"]}
```

This intentionally small example shows exactly where a prompt becomes a checked
objective. Replace its counter with your engineering state and actions. You do
not need to edit asys to introduce a new world.

The callback contracts are:

- `initialize(settings, agent_ids, seed) → state`: return an initial JSON object.
  Initialize randomness from `seed` and store any evolving random state in it.
- `observe(state, agent_id) → object`: provide only what this agent may know.
  Return `{"active": false}` to discard its queued plan and skip decisions and
  actions for this turn. World time still advances, enabling agent-free tests.
- `action_schema() → object`: return a JSON Schema Draft 7 schema for **one**
  action, not the whole plan. References must be local `#` fragments. Close the
  action object with `additionalProperties: false` where appropriate.
- `step(state, actions) → {state, events}`: consume at most one action per active
  agent. `actions` maps stable IDs such as `agent-001` to their actions; agents
  with empty plans are absent. Return a state object and a list of event objects.
  Validate physical preconditions here, resolve conflicts deterministically and
  record rejected actions. Valid shape does not imply legal execution.
- `evaluate(state, objective) → {achieved, metrics, summary}`: return a boolean,
  a metrics object and text, evaluated initially and after every committed turn.
  With no objective, the function receives `{}` for useful metrics, and the
  controller changes `achieved` to `null` so exploration cannot auto-complete.
- `artifacts(state) → object`: export the useful work as JSON. Large external
  artifacts need a separately designed storage/submission mechanism; this
  callback cannot simply return arbitrary host file paths for the engine to copy.

All inputs and returns are JSON copies. Observation and evaluation cannot mutate
the authoritative state through their arguments. Each callback argument/result
is limited to 256 KiB in compact and indented JSON; schemas/configurations are
limited to 128 KiB. Aggregate in-progress controller state is limited to 2 MiB
before decisions are submitted or a new turn is committed. This first version
is intended for compact worlds and references to larger artifacts, rather than
embedding source repositories or datasets in every observation.

Callbacks run in the controller container and must terminate promptly. Store all
evolving information in `state`, not module globals. Avoid wall-clock-dependent
physics, unordered conflict resolution and unrecorded external side effects if
you need deterministic recovery/replay. JSON files and queued jobs are durable;
an arbitrary external action made by a callback is not automatically transactional.

## Define the decision environment

The standard [worker environment contract](../asys-workers/README.md) applies.
For a model-driven environment use:

`component.dcomp`:

```text
docker asys-workers:dev
input cyclo.provider.v1.Provider inference
```

`Dockerfile`:

```dockerfile
FROM asys-workers:dev
COPY . /opt/asys/environment
```

`workers.json`:

```json
{
  "version": 1,
  "name": "target-builders",
  "types": {
    "swarm-step": {
      "command": ["/opt/asys/asys-workers/tools/asys-swarm-agent", "--agent", "researcher"]
    }
  }
}
```

`agents/researcher/prompt.md`:

```text
Work towards the supplied mission and objective using the available actions.
Use the observation as evidence. Retain useful information in private memory.
Follow the supplied action schema and plan length limit.
```

The prompt defines a behavioral policy, while the mission is specific to a run.
The worker additionally supplies its structured-response instructions. It loads
the selected agent prompt and optional environment `tools.md`, but no coding
tools, extensions, workspace instructions or `memory.md`. The name `researcher`
only selects a file; the swarm itself has no prescribed scientist hierarchy.

You can replace the model worker with any ordinary runtime command declaring
the same job type. The scripted terrarium demonstrates this. Runtime input is:

```json
{
  "mission": "...", "objective": {"target": 12},
  "agent": "agent-001", "turn": 0,
  "observation": {"count": 0}, "memory": null,
  "actionSchema": {"type": "object"},
  "maxActions": 2, "memoryBytes": 2048,
  "timeoutSeconds": 30, "options": {"maxTokens": 1024}
}
```

Write the result to the runtime's `ASYS_RESULT` file:

```json
{
  "actions": [{"add": 2}, {"add": 1}],
  "memory": {"lastObservedCount": 0},
  "usage": {"input": 120, "output": 30, "totalTokens": 150}
}
```

`usage` is optional for scripted programs. `actions` and `memory` are required;
memory can be any JSON value and fully replaces the previous memory. An empty
plan is allowed and causes another decision on the next active turn.

At each turn, agents with no remaining plan get observations and decision jobs.
The controller waits for all needed decisions, then applies one action from each
plan together in a single world transition. Remaining actions are queued for
later turns without another model call. Therefore plans can become stale: the
world must validate each action against current state. Runtime response order
does not establish action order. Private memory is only supplied to that agent,
but workers share an execution container; use the standard action-only worker
when local-observation boundaries matter.

## Engineering goals

The same interface can represent a code investigation or a construction task,
provided the world exposes real capabilities and independent acceptance checks.

| Engineering task | Useful actions | Evidence for completion | Export |
| --- | --- | --- | --- |
| Find a bug | Inspect allowed code, propose an input, request an isolated test | Reproducible failing case and a checked explanation of violated behavior | Reproducer, test log, proposed patch |
| Optimize a design | Propose a design, request a simulator trial, reuse a candidate | Required constraints plus measured improvement on separate test cases | Design, parameters, measurements |
| Build a complex system | Propose a task, submit a patch, request tests, integrate a candidate | Passing acceptance tests, interfaces and integration checks; review for qualitative requirements | Source changes, test evidence and build artifacts |

Those actions are examples to implement, not built-in tools shipped by this
feature. The terrarium's actions only affect its simulated habitat. Giving its
agents a prompt saying “build an operating system” does not give them compilers,
repository access or a meaningful completion test.

For code work, give each candidate an isolated workspace, control which commands
and dependencies it can use, accept immutable submissions, and run trusted tests
outside the submitting agent's write access. Use a fast deterministic world
transition to accept a proposal; long-running test/build jobs need a recorded,
asynchronous work/result mechanism instead of blocking `step()`. The current
engine schedules decision jobs; that extra engineering workflow is application
work. Existing goal/BPMN workflows remain useful for explicit build plans.

To ask whether interaction improves results, compare it with independent agents
and a sequential agent under matched budgets and seeds. Measure correct accepted
artifacts, reuse and cost. A moving graph or a single success does not establish
an advantage from collaboration. The shipped drought checks persistent
constructions after agent removal; held-out disturbances and repeated trials
require additional experiments.
