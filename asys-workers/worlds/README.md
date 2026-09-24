# Built-in world packages

Each package contains a world component and an observational dashboard viewer.
Named swarms reference `world.package`; the shared host preparation deploys the
component and snapshots its view assets. Member private memory remains in the
worker. Requests and replies use isolated `world-JOB_ID` runtime sessions.

The portable [world authoring guide](../../skills/asys-authoring/references/worlds.md)
covers package creation, evaluators, the version-1 protocol and viewer hooks.
The [full protocol](../../asys-swarm/WORLD.md) gives implementation limits and
recovery details. `asys-workers add ENVIRONMENT swarm NAME` copies a complete
leaderboard starter; configure its evaluator before running.

| Package | Observation policy | Evaluation |
| --- | --- | --- |
| `builtin:leaderboard` | Shared accepted artifacts, optionally best-k | Configured trusted evaluator |
| `builtin:torus` | Artifacts within wrapped Manhattan distance | Configured trusted evaluator |
| `builtin:terrarium` | Local Rainkeepers habitat | Garden survival after inhabitants leave |

## Checked-artifact settings

The evaluator consumes `candidate` and `problem` on stdin and returns either
`{accepted:true,score,details}` or `{accepted:false,reason}`. Invalid reports,
failures and timeouts reject the candidate. Members cannot select the evaluator.
A configured `problem.initial` candidate is checked before member decisions.

Scores minimize by default; set `direction: "maximize"` for maximization.
Objectives `scoreAtMost` and `scoreAtLeast` establish measured success. A null
objective requests exploration. Leaderboard `top_k: 0` exposes all retained
artifacts; positive values expose that many best submissions.

Common capacities are `max_artifacts: 256`, `archive_bytes: 131072` and
`candidate_bytes: 4096`. New submissions are rejected when capacity is exhausted,
preserving existing work. An unrestricted leaderboard requires an archive of
at most 128 KiB. Torus defaults are an 8 by 8 grid, radius 1 and 8 visible artifacts.

Torus actions may move north, south, east, west or stay. Publication happens
before movement; valid movement still applies to a rejected candidate. A member
can name an observed parent, but simultaneous submissions cannot name one
another. Source selection combines best and recent distinct candidates so
intermediate work remains visible. Parent links record declared reuse.

## Route example

The deterministic environment in `samples/route/env` supplies a route policy
and task-specific evaluators for both artifact worlds. Every city must be visited
once, including the return edge. Manhattan distance starts at 44; the perimeter
route measures 16. No model is needed.

From the repository root after building the images:

```sh
tools/asys-run asys-workers/worlds/samples/route/env route-global \
  "Shorten the complete delivery loop while visiting every city once."
tools/asys-run asys-workers/worlds/samples/route/env route-local \
  "Share useful route variants with nearby members and shorten the loop."
tools/asys dashboard
```

Installed copies live under `share/asys/workers/worlds/samples/route/env`.
Copy them before adapting the evaluator. A workflow binds either named type
with `{request: request}`. These programs verify execution and evaluation;
they do not demonstrate superior model reasoning.

## View and service integration

Views export `mount(element, context)` and return `update(frame)` and `dispose()`.
`context.theme` supplies the shared palette, fonts and task path.
`context.inspect` opens the common sidebar; `context.selectMember` selects a
saved member conversation without scrolling the page. Keep selections stable
across updates and provide pointer and keyboard access. Rendering never submits
world actions. The shared [design package](../../designs/default/design.json)
contains the built-in views' CSS.

The Python `Component` SDK supplies the handshake, readiness, bounded concurrent
sessions and correlated reply caching. Callbacks receive explicit JSON state
and must be safe for concurrent invocation. A crash before durable reply storage
can repeat a callback. Implementation identity includes rules and evaluator
dependencies; change it when their behavior changes.
