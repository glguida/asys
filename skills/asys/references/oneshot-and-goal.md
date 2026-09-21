# One-shot and goal

## One assignment

```sh
asys-inference models
asys system-model set simple account/model
asys-oneshot ./env/development "Inspect the current changes and report concrete defects" \
  --workspace ./project
```

One-shot supplies the built-in `simple` agent; there is no `AGENT` positional
argument. `--model MODEL` overrides the configured `simple` model for this run.
The environment supplies installed tools, shared skills/extensions, and the
Provider input. It need not define an environment agent named `simple` or an
agent job type for this launcher.

The assignment is literal text. State deliverables, allowed work, and expected
checks in that text. The workspace defaults to the current directory and must
exist. Each invocation creates one fresh run/job/session. There is no implicit
agent step limit. The host prints progress/state paths to stderr and successful
result JSON to stdout; failure exits 1 and interruption exits 130.

One-shot does not automatically turn an agent exception into a human dialogue.
Use goal for its built-in escalation behavior or BPMN with an explicit Human
path when that is required.

## Implement until independently verified

```sh
asys-goal ./env/development \
  "Implement the requested parser behavior and verify the specified examples and error cases" \
  --workspace ./project
```

Use a concrete goal with observable outcomes and the location of its inputs.
The original request and its governing sources determine scope. There is no
mandatory preliminary planner, generated contract, or separate commit stage.

The goal worker uses two roles with the same selected model and built-in
`simple` definition:

1. Implementation inspects, plans, edits and checks the work in one continuing
   conversation. It retains its observations and receives verifier findings
   and human guidance in later turns. Pi compacts context normally when needed.
   A successful report requires `goal_status: "continue"` to start another
   implementation turn automatically, or `goal_status: "review"` to request
   independent verification.
2. A fresh verifier starts only after an explicit `review` report. It checks
   the current workspace independently against the original request. It receives
   governing references, previous criteria for continuity, open findings and
   human guidance, without the implementer's
   completion narrative. It can identify missing requirements; earlier criteria
   do not replace or narrow the original goal.

Unmet work and the verification result return to the continuing implementer.
It can take further work turns before requesting another fresh review.
Implementation can return useful partial progress with `continue`; this does
not launch a verifier or complete the goal. Only independent verification
can establish that the whole requested outcome is complete. Findings stay open
until a verifier explicitly explains their resolution.

Verification inspects actual artifacts and behavior. Documentation may establish
requirements. Comments, commit messages and agent reports can identify things to
check, but completion claims do not prove success. The verifier is instructed
not to repair the work; its normal workspace access remains available for
checks. Separation of assignments is not an OS-enforced restriction on which
workspace files it can read.

There is **no default attempt limit**. Set `--max-attempts N` only when an
explicit limit is wanted. Each successfully reported implementation turn counts
as an attempt, including a `continue` turn. Human retries and protocol
corrections stay in the same attempt. A `review` on the last permitted turn still
launches verification and can succeed; a final `continue` exhausts the limit.

Malformed completion JSON gets one correction in the same session. A missing
or invalid `goal_status` gets one correction in the same implementation
conversation. Invalid verification fields get one fresh verification session to
correct the report within the current attempt. Execution failures and
uncorrected reports can fail the job: unlimited attempts do not silently retry
every transport or parser failure forever.

The implementation status uses the existing final-response protocol; no new
goal-management tool is required. For example:

```json
{"final":"Completed the first component; integration remains.","exception":null,"goal_status":"continue"}
```

Change the status to `review` when requesting independent whole-goal
verification. A status claim alone can never set `verified: true`.

## Use the goal worker from BPMN

Declare a normal type:

```json
"goal": {"command": ["/opt/asys/asys-workers/tools/asys-goal"]}
```

Bind it normally:

```xml
<bpmn:task id="deliver" name="Implement and verify">
  <bpmn:extensionElements>
    <asys:job type="goal" input="= {goal: request}"/>
  </bpmn:extensionElements>
</bpmn:task>
```

`assets/goal.bpmn` is a complete version with a diagram. The input contract is
`{"goal":"nonempty goal text"}` plus optional positive `maxAttempts`.
The worker command accepts `--model MODEL` and `--max-attempts N`; command
options override its model default and corresponding input limit. `model` is
not a field of the goal assignment input.

The loop is one runtime job inside workers. BPMN does not run a host shell
command named `asys-goal` or launch a new environment on every cycle. Host
launchers snapshot system-model defaults for workers at
`/etc/asys/system-models.json`. Other producers can supply that mapping, use
`ASYS_SYSTEM_MODELS` for another settings path, or set worker `--model`.

The goal worker explicitly loads the packaged `simple` agent, plus shared
environment resources. An environment's `agents/simple/` does not replace it
or give it retained memory. The implementation conversation belongs to the
current goal job; later goals do not inherit it.

## Human escalation

Either role can finish with a non-null `exception`, explaining why it cannot
continue. Include a concrete `question` and workspace `review_files` when
useful. The worker composes a Human request and waits for retry guidance or stop.

```sh
asys-human-prompt --system asys
```

Use the same asys/dcomp state as the run. Goal launchers wire the shared Human
input automatically; `-L human=COMPONENT.OUTPUT` selects another service.
BPMN can also use `--human` for a terminal attached to that run.

Retry preserves the human question and answer. Implementation continues in its
existing conversation; verification starts a fresh session. Stop ends the goal
unsuccessfully. A human answer cannot directly mark the goal verified.
Uncorrected reports and service/transport failures fail the job instead of
opening a Human request. Cancellation interrupts an active session or withdraws
a waiting Human request.

## Results, evidence, and retained state

A successful result contains `verified: true`, `exception: null`, a factual
`final` report, the attempt count, criterion evidence and no open findings.
Verification uses a coverage judgment of `complete`, `gap` or `unverified` and
a nonempty list of criteria. Each criterion identifies its requirement and
source basis, with a status of `satisfied`, `unmet` or `unverified` and recorded
evidence. An unverified criterion must explain what remains unverified.

The controller permits completion only when coverage is complete, all criteria
are satisfied with evidence and all earlier findings have been resolved.
Resolutions refer to finding IDs, explain why they are resolved and include
observed evidence with a source and observation. Omitting a finding from a
later report does not close it. Previous criteria help maintain
continuity; they are not a separate accepted contract. The verifier must still
check that the criteria cover the original request and governing sources.

An explicit attempt limit reached with unfinished work, human stop, or execution
failure returns an unsuccessful job. BPMN can use ordinary boundary errors.
Inspect the saved result rather than expecting successful JSON on stdout after
a host failure. Agents judge scope, appropriate checks and evidence; structural
result checks alone do not prove an implementation correct.

`JOB/goal.json` version 3 preserves the original goal, selected model, current
phase, findings, attempts, session results and human replies. The implementer's
Pi conversation persists in `JOB/implementation/session.jsonl`. Per-turn files
remain under `attempts/ATTEMPT/implement-SESSION/` and
`attempts/ATTEMPT/verify-SESSION/`: input, result, transcript, report, proposed
lessons and scratch. Human request/answer files stay beside the turn that
needed them.

```sh
asys status latest --json
asys logs latest goal
asys top
```

Re-executing the goal worker for the **same job and saved job directory**
restores its implementation conversation and controller progress. Interrupted
verification restarts fresh. Runtime does not automatically rerun terminal jobs,
and the host launcher does not expose a goal resume command. A new goal
invocation starts a new job and conversation. BPMN resume likewise submits a
new job for a failed goal task, retaining workspace files and old run records.
Do not describe either operation as restoration of the old conversation.

Lessons remain in run state for later review; ordinary execution does not
rewrite the system agent's definition. Version 1 and 2 goal state cannot be
resumed by this controller; keep those records for inspection and start a new
job. The observer continues to display all three versions.
