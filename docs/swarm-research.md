# Swarms in asys: research and implementation

The implementation is documented in the [swarm guide](../asys-swarm/README.md) and
[goal authoring guide](../asys-swarm/AUTHORING.md).

## Local interaction and persistent artifacts

[SwarmWorld: Stigmergic technological evolution in societies of language-model agents](https://arxiv.org/abs/2608.26081),
by Subhadeep Pal, Fiona Y. Wang and Markus J. Buehler, studies collective
construction in simulated worlds. The MIT preprint was submitted in August 2026.

SwarmWorld uses initially equivalent agents with fixed model weights, local
observations and private memory. Agents propose bounded plans; deterministic
simulation applies their consequences. Persistent artifacts let subsequent
work build on earlier work. Coordination through environmental changes is
stigmergy. Experiments compare interaction with communication/culture ablations
and isolated search, then test artifacts under unseen disturbances after
removing agents. The reported advantage concerns portfolio breadth and
resilience; isolated search can still produce the strongest individual artifact.
The main study uses 50–200 agents and four world seeds per condition. Decision
opportunities are matched, but token consumption can differ. This is preprint
evidence about a simulation, not a guarantee that swarms solve arbitrary tasks
better. [Paper and supplementary methods](https://arxiv.org/html/2608.26081v1).

## Swarms with globally shared information

Local observations are a choice made by SwarmWorld, not a requirement of every
multi-agent system. Two independent architectural questions matter: **which
information can agents access**, and **who chooses their next work**. A common
store can expose information globally while agents reason independently. A
runtime that validates updates, orders writes and enforces budgets is not, by
itself, a central reasoning agent that chooses everyone's strategy.

Three primary research examples make these distinctions concrete:

- [DeLM: Decentralized Multi-Agent Systems with Shared Context](https://arxiv.org/html/2606.10662v1),
  Yuzhen Mao and Azalia Mirhoseini, June 2026. Independent agents claim ready
  tasks asynchronously from a shared queue. Their discoveries, failed
  hypotheses and constraints become compact shared summaries after an
  admission check; every agent can access this global context and retrieve
  supporting detail when needed. Verification checks summary support using
  source references and an LLM verifier; it is not a formal proof of every
  claim. Appendix A.4 specifies atomic publication and snapshots at dispatch:
  an agent sees later updates on its next snapshot, without a barrier waiting
  for every worker. The paper reports software-repair and long-context QA
  experiments, not a universal benefit from sharing. This is the closest of
  these examples to independent workers building on a common, checked problem
  state without routing each finding through a lead reasoning agent.
- [LbMAS: Exploring Advanced LLM Multi-Agent Systems Based on Blackboard Architecture](https://arxiv.org/html/2507.01701v1),
  Bochen Han and Songmao Zhang, July 2025. Agents read and write a public
  blackboard containing shared messages and results; selected agents receive
  that board as context. Communication happens through the board, with
  additional restricted spaces for particular debates. An LLM control unit
  chooses which agents act, so global information here coexists with central
  scheduling. The reported tests concern knowledge, reasoning and mathematics.
- [CSI: Towards Cybersecurity SuperIntelligence, section 5.3](https://arxiv.org/html/2605.28334v1#S5.SS3),
  May 2026. Four agent scaffolds execute concurrently with a common
  `/blackboard/notes.md` mounted into their containers. They can read peers'
  findings and publish their own artifacts. The paper reports 19 of 33
  controlled challenges solved with the board versus 17 without communication.
  Cooperation instructions and actual participation are asymmetric: one agent
  never reads or writes the board. This is evidence from one experimental
  setting, not a guarantee that broadcasting all information improves every
  task.

Global access also does not require copying all raw history into every prompt.
What is shared, how it is checked, which summaries are shown, and when a worker
observes updates are distinct communication-policy decisions.

## What is implemented

The implementation provides an extensible world interface and a teaching
simulation. It does **not** reproduce MIT's simulator, numerical results or
artifact-controller evolution. Reproducing the research would require its world
semantics, matched-budget ablations and held-out evaluation protocols.

| Part | Responsibility | Contract |
| --- | --- | --- |
| Host launcher/viewer | Launch, inspect, pause, resume, cancel | Runtime channel and saved run records |
| `asys-swarm` component | World authority, scheduling, limits, durable turns, evaluation | Producer of ordinary runtime jobs |
| Worker environment | Agent prompt, model and decision command | Existing `workers.json` |
| Decision job | Observation/private memory to bounded actions/private memory | Existing runtime input/result/cancellation |
| Inference service | Model catalogue and inference | Unchanged `cyclo.provider.v1.Provider` |
| User world module | Observations, schemas, rules, objective and artifacts | Six documented Python functions |

This follows the existing [BPMN pattern](../asys-bpmn/README.md): orchestration
lives inside asys, while the host communicates through runtime channels. No new
runtime type system or Provider service is needed. A controller owns execution
authority without prescribing scientific roles or individual plans. Population
size and concurrent decisions are separate settings.

One turn works as follows:

1. The controller observes each active agent's permitted world state. Agents
   without queued plans get ordinary runtime jobs of the configured worker type
   (`swarm-step` by default), with identities and inputs persisted before
   submission.
2. The standard worker loads its prompt, mission, objective, observation and
   private memory. It makes one bounded Provider call through the existing
   adapter, without coding tools or extensions. An application can select a
   different worker command that independently evaluates a proposal before
   returning its result.
3. The worker returns `{actions, memory, usage}`. Job identity and run/agent/turn
   metadata stay in the existing runtime envelope rather than a new protocol.
4. After all required decisions complete, the controller applies one action per
   plan as a single transition. Schemas enforce shape; world rules determine
   admissibility and deterministic conflict ordering.
5. The evaluator checks actual state. Events and snapshots pass through the
   runtime channel; the checkpoint retains world, memories and queued plans.

Malformed output fails the run explicitly. A structurally valid but physically
illegal action can be rejected and recorded by the world. Interrupted jobs retry
under fresh IDs within bounded attempts. Time, decisions, concurrency, plan
length, memory and per-call output tokens are bounded. Reported usage is recorded;
this version does not enforce aggregate token or money limits.

World state is in a controller-only mount and the frozen package is read-only.
Workers still share their environment container and workspace. Stronger
isolation is needed before exposing arbitrary coding tools to competing agents.

## Different algorithms on the same execution framework

A common execution framework can support different coordination algorithms.
The framework supplies agent identities, private memory, bounded decisions,
runtime jobs, turn scheduling, persistence, cancellation and evaluation hooks.
The world defines observations, valid contributions, communication visibility,
state transitions and success criteria. The worker environment supplies prompts,
models, commands and evaluation tools. Compatible worlds can reuse an
environment without sharing the same communication policy.

For local interaction, `observe(state, agent_id)` can expose nearby objects and
delivered messages, as in the terrarium. For global sharing, it can expose a
common archive of checked findings or accepted artifacts. World rules determine
which contributions enter that archive and how agents can reuse them. Neither
choice requires a lead LLM to select each agent's strategy.

The current engine has synchronous turns. Agents make decisions from their
permitted observations, and the engine waits for the turn's required decisions
before applying contributions. New shared information becomes available to
subsequent decisions; agents with queued plans continue until another decision
is required. This timing differs from DeLM's asynchronous task claiming and
publication. Implementing that scheduling, verified summary admission and
on-demand evidence retrieval requires additional orchestration; a global
observation alone does not reproduce DeLM. Likewise, the terrarium's local rules
do not reproduce the full MIT simulator.

## Demo and useful outcomes

The [Rainkeepers terrarium](../asys-swarm/examples/terrarium/README.md) is an
ordinary package of configuration, world code, HTML and worker environments.
The launcher has no demo registry or compiled-in scenario:

```sh
asys-swarm run ./asys-swarm/examples/terrarium/swarm.json \
  ./asys-swarm/examples/terrarium/env/scripted --view
```

Eight identical inhabitants have 24 turns to build rain collectors, gardens and
channels. The world then removes every agent and runs twelve rainless turns.
Success requires at least six gardens to remain healthy throughout this test.
Its graph derives from delivered messages and physical reuse, without prescribed
hubs. The scripted baseline demonstrates the runtime path. Selecting `env/agents`
makes real Provider-backed decisions under the same rules.

The drought is declared in advance. It demonstrates persistent function after
agent removal, not generalization to hidden disturbances. Output includes world
state, construction artifacts, provenance, metrics, trace and objective result.
It is neither a trained model nor evidence of a universal swarm advantage.

For engineering work, define meaningful actions and independent acceptance
tests. Bug finding needs code observations, controlled tests and a reproducer;
system construction needs candidate patches, integration tests and artifact
handling. Those capabilities do not appear merely by changing the prompt. The
[authoring guide](../asys-swarm/AUTHORING.md) gives the world API, a complete
minimal objective example and the additional workflow needed for engineering.

Validation covers real Docker/dcomp host-to-runtime execution, a local fixture
using the actual Provider protocol, cancellation, concurrency, budgets,
checkpoint recovery, deterministic replay and agent-free evaluation. It does
not establish live-model quality or reproduce the paper's benchmarks.
