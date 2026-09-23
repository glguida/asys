# Supplied worlds

Each directory contains an ordinary executable world program and an optional
renderer module. The swarm worker starts the configured program in its job and
exchanges JSON through a private Runtime channel. It does not import world code.

- `leaderboard/serve.py`: shared checked-artifact archive; `top_k: 0` shows all
  retained submissions, while positive `top_k` shows that many best submissions.
- `torus/serve.py`: a wrapping grid with Manhattan local visibility. Artifacts
  persist where they were submitted. The global viewer ranking is not sent to members.

Both programs require an explicitly configured evaluator. They have no default
domain, task, or score. The optional `problem` setting supplies domain data;
without an initial candidate the archive starts empty.

The explicit `samples/route/env` environment configures a small delivery-route
problem. A candidate is
`{"tour":[0,1,2,3,4,5,6,7]}`. The separate `samples/route/evaluate.py` command checks
that every city is visited exactly once, including return to the start, then
measures Manhattan distance. `samples/route/participant.py` is a deterministic
member that searches route reversals and uses a nearest-neighbor branch when
progress stops; it uses no model. The initial route has
length 44 and the perimeter route has length 16. Objective `{"scoreAtMost":16}`
defines a measurable stopping condition. This sample checks execution and
evaluation; it is not evidence that a model population improves search quality.

From a checkout after building the worker image:

```sh
tools/asys-run asys-workers/worlds/samples/route/env route-global \
  "Shorten the complete delivery loop while visiting every city once." --view
tools/asys-run asys-workers/worlds/samples/route/env route-local \
  "Share useful route variants with nearby members and shorten the loop." --view
```

After installation, replace `tools/asys-run` with `asys-run`; copy the supplied
sample environment from the installed `share/asys-workers/worlds/samples/route/env`
directory if you want to edit it. Both named definitions expose ordinary Runtime
job types, so a workflow can submit the same `{request: "..."}` assignment.

Configure a custom evaluator with `--evaluator '["/path/to/checker","argument"]'`.
It receives `{"candidate":...,"problem":...}` on stdin and returns JSON:
`{"accepted":true,"score":12,"details":{...}}`, or
`{"accepted":false,"reason":"..."}`. Exit failures, malformed reports and timeouts
reject the submission. Set `--evaluator-id` when the command depends on resources
whose version is not captured by its executable files. Commands are trusted
configuration, never proposed by members.

The `problem` setting can supply an `initial` candidate, which must pass the same
independent evaluator. Scores default to minimization; set
`direction: "maximize"` and objective `scoreAtLeast` for maximization. The stored
best artifact includes the candidate, measurement and declared parent. Parent
links record declared reuse, not proof that the candidate's content derives from it.

An action supplies `candidate` and optional `parent`. A torus action may also
choose `move` from north, south, east, west or stay. Parents must appear in that
member's starting observation. Publication precedes movement; valid movement
also applies when evaluation rejects the candidate. Simultaneous submissions
cannot use each other's new artifacts as parents.

Common limits are `max_artifacts` (256), `archive_bytes` (131072) and
`candidate_bytes` (4096). Capacity exhaustion rejects new artifacts and keeps
existing work. Leaderboard `top_k: 0` requires an archive no larger than 128 KiB
so the complete archive fits its observation. Other observation sizes remain
subject to the worker's ordinary payload limit.

Torus settings default to `width: 8`, `height: 8`, `radius: 1` and
`visible_artifacts: 8`. Local source slots retain the best half and fill the rest
with recent distinct submissions, including worse intermediate work. The initial
candidate is available separately to every member. Valid artifacts are never
removed because their score is worse.

Renderers export `mount(element, context)` returning `update(frame)` and
`dispose()`. They own only the supplied element. The host supplies controls,
recorded frames, the journal and replay selection.
