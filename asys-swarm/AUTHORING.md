# Program a swarm toward a goal

A swarm is one worker job. You supply its mission, member policy and an
independent world service. The world defines what agents can observe and do,
what their actions actually change, how success is measured and what to export.
A prompt alone does not create capabilities or establish correctness.

| Input | Where it belongs |
| --- | --- |
| Mission and population | Swarm configuration passed as job data |
| Agent prompts, models and tools | Worker environment |
| Private member memory and turn scheduling | Swarm worker algorithm |
| Rules, observations, objective evaluation | World program on a runtime channel |
| Browser presentation | Optional host-served view assets |

## Configure the job

```json
{
  "version": 1,
  "name": "Reach the target",
  "mission": "Cooperate to reach the measured target using permitted actions.",
  "world": {"channel": "world", "settings": {}, "timeoutSeconds": 30},
  "objective": {"target": 8},
  "agents": {"count": 4, "type": "swarm-step"},
  "limits": {"turns": 20, "concurrency": 2, "decisions": 80},
  "seed": 7
}
```

The configuration is data. There is no world module, executable import path or
scenario directory mounted into workers. The launcher selects the world process
separately through `--world`, `--world-command` or `--world-external`.

`mission` is natural-language guidance; `objective` is data for the world. Set
`objective` to `null` for exploration with no pass/fail goal. The members can
propose actions and summarize ideas, but the world measures actual outcomes.

| Limit | Default | Range |
| --- | --- | --- |
| `turns` | 100 | 1–10,000 |
| `concurrency` | 4 | 1–64 |
| `decisions` | 1,000 | 1–1,000,000 |
| `seconds` | 600 | 0.1–86,400 |
| `jobSeconds` | 60 | 0.1–3,600 per member decision |
| `actions` | 4 | 1–16 per returned plan |
| `memoryBytes` | 4,096 | 4–65,536 |
| `outputTokens` | 2,048 | 128–16,384 |
| `tickSeconds` | 0.05 | 0–5 |

Population defaults to eight and can contain 1–256 members. Output-token limits
apply to model calls; report measured token use when comparing experiments.
The swarm counts complete decision batches before scheduling a turn so a
budget does not silently omit some members from that turn.

## Supply a world service

A world is an independently running program. It exchanges versioned JSON
requests and responses with the swarm over a named asys-runtime channel.
The [wire specification](WORLD.md) defines the exact envelopes for implementations
in any language.
The swarm writes `out`; the world reads `out` and writes replies to `in`.
Request IDs correlate responses; run IDs identify the experiment. Both
endpoints must see the same channel directory.

Use one swarm client and one world service per channel. Concurrent runs need
distinct channel directories; replay needs its own channel or a stopped run.
Runtime's acknowledgement cursor belongs to that consumer.

The protocol supports a description containing implementation identity and an
action schema, followed by these operations:

| Operation | Arguments | Result |
| --- | --- | --- |
| `initialize` | settings, participant IDs, seed | Initial state object |
| `observe` | state, participant ID | That member's observation |
| `step` | state, actions keyed by participant ID | `{state, events}` |
| `evaluate` | state, objective | `{achieved, metrics, summary}` |
| `artifacts` | state | Exported artifact object |

`action_schema` describes one proposed action using JSON Schema Draft 7.
Schemas may reference their own definitions; remote references are forbidden.
The worker validates structure before submitting actions. The world applies
semantic rules and decides whether a legal-shaped action is admissible.

An observation with `active: false` suspends decisions for that participant and
discards its queued plan. World time can still advance, including after every
agent has left. Other observation fields are world-defined. This supports both
local neighborhoods and globally shared information without changing workers.

World operations receive explicit state and must be deterministic for the same
arguments. Treat the seed and state as the source of randomness. The worker
commits a transition only after it and its evaluation succeed; retries can
repeat operations. Keep side effects outside these pure rules or make them
idempotent. A durable implementation identity and action-schema identity are
checked during recovery and replay.

The Python SDK accepts a callbacks object that your world program imports
normally. It does not load source named by an incoming request. Other languages
can implement the same channel protocol.

For example, `rules.py` inside your own world project:

```python
def action_schema():
    return {"type": "object", "properties": {"add": {"type": "integer", "minimum": 0, "maximum": 2}},
            "required": ["add"], "additionalProperties": False}


def initialize(settings, agents, seed):
    return {"total": 0}


def observe(state, agent):
    return {"active": True, "total": state["total"]}


def step(state, actions):
    return {"state": {"total": state["total"] + sum(a["add"] for a in actions.values())}, "events": []}


def evaluate(state, objective):
    return {"achieved": state["total"] >= objective.get("target", 8),
            "metrics": {"total": state["total"]}, "summary": f"Total: {state['total']}"}


def artifacts(state):
    return {"total": state["total"]}
```

The world executable explicitly supplies that implementation:

```python
import os
from asys_swarm.world_service import Service
import rules

Service(os.environ["ASYS_RUNTIME_ROOT"], rules, identity="counter-v1",
        channel=os.environ.get("ASYS_WORLD_CHANNEL", "world")).serve()
```

For a component deployment, package the program and its dependencies in its
image and declare it in `component.dcomp`. Accept `--root` and `--channel` so the
launcher can pass the runtime location, and supply the Docker health check
required by dcomp. The complete
[terrarium world](examples/terrarium/world) demonstrates both host and component
execution with the same entry point. The implementation identity should change
when rules or their dependencies change; a content digest is useful.

World state is limited to 2 MiB; transitions and pending actions to 4 MiB;
artifacts to 1 MiB. Observations and individual decisions are bounded separately.
The worker reserves room for terminal records beneath runtime's 8 MiB file
limit. A timed-out or invalid world response fails explicitly and retains the
last committed checkpoint.

## Define the member policy

Use the ordinary environment definition. For model decisions:

```json
{
  "version": 1,
  "name": "habitat",
  "types": {
    "swarm-step": {
      "command": ["/opt/asys/asys-workers/tools/asys-swarm-agent",
                  "--agent", "inhabitant", "--model", "account/model"]
    }
  }
}
```

Create `agents/inhabitant/prompt.md` with its standing instructions. The component
uses the existing Provider input. The bounded decision adapter exposes only the
allowed action schema: no shell, arbitrary file access or coding extensions.
It receives mission, objective, participant ID, turn, observation and its own
private memory. It returns `{actions, memory, usage}`. Memory replaces the prior
value; it is retained by the swarm worker and never sent to the world service.

A custom trusted member command can run tools or an independent evaluator
before returning its decision. It uses the normal `ASYS_INPUT`, `ASYS_RESULT`,
`ASYS_JOB_DIR` and `ASYS_WORKSPACE` conventions, within the parent job's process
group. Member invocations are internal executions, not additional runtime jobs.
Their logs and transcripts remain under the swarm job's decision directories.

A population is not a permission boundary. Its trusted commands share a workers
component. The action-only adapter enforces limited model capabilities; arbitrary
coding commands would require an additional design to preserve local knowledge.

## Reuse swarm as one task

An environment can expose the complete algorithm as an ordinary job type:

```json
"swarm": {"command": ["/opt/asys/asys-workers/tools/asys-swarm"]}
```

The job input supplies an experiment `id`, `config` and an optional control
`channel`. The world service must already be available on the configured world
channel. The worker obtains the runtime root from its normal execution context.
The launcher installs this job-type binding for convenience; BPMN or another
runtime producer can submit it directly. Neither needs to manage individual
members or run a second controller.

The worker's result includes normal `final` and `exception` fields and the swarm
status, measurements, artifacts and execution summary. A satisfied objective,
an unmet objective at the budget limit and exploration are distinct outcomes.
Pause/resume and progress use runtime channels; runtime job cancellation also
terminates the member subprocesses.

## Define useful outputs

Implement `artifacts` to export the work worth retaining: constructions, source
references, plans, provenance, measurements or other domain data. Keep large
files in durable application storage and return verified references. Define an
independent acceptance test for engineering work; claims in model prose are
not acceptance evidence.

Local interaction and a shared archive are world policies. Compare them with
matched populations, proposals, evaluator rules and measured token budgets.
The [research notes](../docs/swarm-research.md) describe their different aims and
the limits of the teaching example.
