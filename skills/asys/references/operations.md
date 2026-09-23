# Operations and recovery

## Inspect the run and its actual artifacts

```sh
asys ps
asys status latest --json
asys logs latest
asys logs latest -f
asys logs latest review
asys logs latest --source components
asys top
```

Selectors accept run IDs, unique prefixes, `latest`, or saved run paths. A job
selector can be its name, ID or unique prefix. `status --json` includes job and
workspace locations. A completed stage means its executable succeeded; inspect
its result and the domain artifacts to assess the requested outcome.

If the run used `--root ./state`, supply the same system root to observers or
select its absolute run path (`./state/runs/RUN_ID`). Every host tool uses
`--root` for the asys system root, containing `runs/`, `inference/`, `human/`,
and `config.json`. An explicit root overrides `ASYS_STATE_ROOT`.

Logs default to the saved run log. `--source events` shows the event stream,
`jobs` worker stdout/stderr, `components` saved component logs, and `commands`
host launch command logs. `--stream stderr` selects job stderr; `-n 0 -f` starts
following without old lines. With no explicit count, run logs are complete and
worker/component sources default to their recent lines.

`top` reads run/job records and agent transcripts, including phase sessions of
a goal. The monitor does not run a workflow or change execution. Use it to
distinguish active tools, model retries/exhaustion, and human waits from a
process that actually failed.

## Storage

```text
RUN/
  run.json                       launcher identity and selected paths
  run.log                        readable progress
  events.jsonl                   recorded events
  result.json                    terminal output when available
  workspace -> /actual/project   host reference, not a copy
  system-models.json             snapshot for this workers component
  runtime/
    environments/NAME/jobs/JOB/
      request.json
      state.json
    channels/workflow/in/        BPMN host requests
    channels/workflow/out/       BPMN events and replies
  jobs/JOB/
    input.json
    result.json
    stdout.log
    stderr.log
    agent.json                   for ordinary agent sessions
    report.md                    if written by the agent
    lessons.md                   proposed lessons, if any
    scratch/
    .pi/system-prompt.txt         last effective agent system prompt
  workflow/workflow.sqlite       BPMN definitions/checkpoints/events
```

Not every manager produces every file; one-shot/goal do not create a BPMN
workflow database/channel. A goal job adds `goal.json`, the continuing
implementation conversation in `implementation/session.jsonl`, and per-turn
artifacts under `attempts/`. Version 3 goal state supports consecutive work
turns: `goal_status: continue` schedules another implementation turn; `review`
starts a fresh verifier. The observer also reads archived version 1 and 2 state,
which the current worker cannot resume. Ordinary programs may have no transcript
or report. State is evidence to inspect, not an invitation to manually mark jobs
successful or edit checkpoints.

Project deliverables belong in the actual workspace. Job directories contain
execution records. A failed/cancelled job may have changed workspace files;
asys does not roll them back. Preserve that distinction when preparing a retry
or human briefing.

## Why host stage progress works

The BPMN launcher creates two components: workers and workflow. Both receive
the shared runtime/job/workspace mounts. The host sends start/resume/cancel
requests to the workflow filesystem channel and reads the engine's events.
That event stream tells it which named stage started or finished; the host
does not reinterpret BPMN to guess progress.

One-shot and the goal launcher create workers only and submit directly to the
runtime queue. Inside a goal job, the goal worker manages its phase sessions.
Both direct launchers and the workflow engine are queue producers with access
to the appropriate mounted filesystem, not privileged because they created a
component.

Runtime supervises ordinary programs and records state/results. Dcomp manages
container lifecycle, mounts, and typed component inputs/outputs. Inference uses
the Provider interface; human requests use the Human interface. Filesystem
channels and queues are separate from those RPC connections. Do not access
another component's private socket to imitate a host control channel.

## Resume a failed BPMN run

```sh
asys status RUN
asys logs RUN
# Correct the observed problem in the workspace/environment/setup.
asys-bpmn resume RUN
```

Resume restores the saved original XML, variables, and checkpoint. Completed
stages retain their results. Failed/unfinished affected stages receive new
jobs/directories and fresh agent sessions, with the original saved requests.
The workspace keeps prior changes. Previous attempts and logs remain inspectable.

The original environment source directory and workspace must remain available.
Keep the environment name and required job types. Resume refreshes its image
and the installed BPMN engine image, so corrected commands/tools/models can
apply to new jobs. Engine state versions must be compatible.

Editing the original BPMN file does not modify the saved workflow resumed by
this command. Run a new workflow if the definition itself must change.
Completed or explicitly cancelled runs cannot be resumed. One-shot/goal host
launchers do not expose a resume command; invoke a new job with an appropriate
assignment if continuation is needed. A goal worker can restore its controller
checkpoint and implementation conversation when explicitly re-executed for the
same job directory. This is separate from BPMN resume or a new goal invocation,
which create new jobs. Runtime does not automatically re-execute terminal jobs.

## Cancellation, restart, and lifecycle

Ctrl-C cancels an active launcher run and normally exits 130. The launcher
removes the components it owns on exit and retains run state. Shared inference
and Human services have separate lifecycles. Hard process death may prevent
cleanup; inspect `run.json` and component logs to identify owned components
before doing manual lifecycle work.

A restarted workflow component can reattach to already submitted jobs. Stopping
workers interrupts their active programs; terminal jobs are not automatically
executed again. Explicit cancellation of a run cancels outstanding jobs. Repeated
execution of external operations needs the program's own idempotency/recovery
contract; the runtime cannot undo an external side effect.

Updating installed files/images leaves existing run containers on their current
image until a later launch/resume. It also does not automatically refresh skill
copies exported to user/project directories.

## Diagnose by evidence

| Symptom | First useful checks |
| --- | --- |
| Model default missing | `asys system-model list`, selected `ASYS_STATE_ROOT`, explicit `--model` |
| Model not exported | `asys-inference status`, `asys-inference models`, selected system and Provider link |
| Missing job type | `asys:job type` versus selected `workers.json`, including called/child processes |
| Executable not found | Image contents, absolute container path, executable mode, `PATH` |
| Wrong environment behavior | Environment source used by launcher, image build logs, exact command/arguments |
| File missing/wrong artifact | Actual `--workspace`, producer result, path contract, join/sequence ordering |
| Fields are null / invalid FEEL | Structured result file, actual types, first-pass optional fields, XML escaping |
| Agent fails at completion | Last assistant response must be a JSON object with `final` and `exception` |
| Reviewer says approved but flow repeats | JSON boolean versus string, actual gateway condition/default flow |
| Goal keeps working | `goal.json`, latest `goal_status`, current findings and tool/check activity; `continue` deliberately skips review and default attempts are unlimited |
| Agent waiting for capacity | Retry/exhaustion messages and provider reset time; distinguish waiting from a crash |
| Human job waiting | Same asys/dcomp state/system, `asys-human-prompt`, candidates/claimant, current claim |
| Human request is incomprehensible | Producing assignment and actual input fields; fix briefing/evidence at the producer |
| Ad-hoc action rejected | Input shape under `message`, enabled prerequisites, already active same activity |
| No outbound network | Environment `egress` and actual installed network/search tool |
| Permission failure | Invoking UID/GID, workspace/parent access, shared-group setup, host/container mounts |
| Resume cannot continue | Failed rather than cancelled/completed state, saved checkpoint, original environment/workspace, compatible engine |

When reporting a failure, include the concrete failing operation, observed
error, affected workspace paths, preserved work, and the correction needed.
State which checks were run and which remain unverified. Avoid replacing the
diagnosis with counters, internal IDs, or a generic “stage failed” message.
