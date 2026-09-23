# asys-goal

Work toward the original request in one continuing implementation session.
The implementer can continue automatically across turns and explicitly request
independent verification when ready. Each verifier starts fresh. Unmet work
and verifier findings return to the implementer until verification succeeds.
There is no default attempt limit and no mandatory contract-definition,
planning, or commit stage.

```sh
asys system-model set simple account/model
asys-goal ./env/development "Implement the requested behavior and its tests" \
  --workspace ./project
```

`--model MODEL` overrides the `simple` default for this run. An unset default
without an override fails before creating the run and prints setup instructions.
`--max-attempts N` optionally caps successfully reported implementation turns,
including turns that continue without review. Omitted, the goal keeps working
until verified, stopped by a human, cancelled, or failed. Human retries and protocol
corrections stay within the current attempt. If the last permitted turn requests
review, that verification still runs; it must pass for the goal to succeed.
The workspace defaults to the current directory and must already exist.

`--root DIRECTORY` selects the asys system root, overriding `ASYS_STATE_ROOT`.
Runs are saved in `ROOT/runs` and model defaults come from `ROOT/config.json`.
Pass the same root to `asys status`, `asys logs`, and `asys top`.

Install everything with `make install` from the repository root, or use
`make -C asys-goal install` to install the goal launcher and human handler and
build their worker and Human images. The selected environment needs the
inference Provider input and the installed tools needed for the goal; see the
[environment example](../asys-oneshot/README.md#environment).

## Worker, launcher, and BPMN

The loop is an ordinary program in `asys-workers`, alongside `asys-agent` and
`asys-human`. The host launcher starts a workers component, submits one goal
job, follows its progress, and removes the component when execution ends.
Implementation, verification, retries, and human waits happen inside that job.

An environment can expose it in `workers.json`:

```json
{
  "version": 1,
  "name": "development",
  "types": {
    "goal": {
      "command": ["/opt/asys/asys-workers/tools/asys-goal"]
    }
  }
}
```

Its input is:

```json
{"goal": "Implement the requested behavior and its tests"}
```

To cap a particular job, add `maxAttempts: N` to its input. The worker also
accepts `--model MODEL` and `--max-attempts N` in its command;
command options override the corresponding default or input setting. Model
selection is configuration, not part of the goal input.

A BPMN task uses the same job without special goal logic in the engine:

```xml
<bpmn:task id="implement_goal" name="Implement and verify the goal">
  <bpmn:extensionElements>
    <asys:job type="goal" input="= {goal: request}"/>
  </bpmn:extensionElements>
</bpmn:task>
```

Asys launchers mount a read-only snapshot of system-model defaults at
`/etc/asys/system-models.json`, containing a mapping such as
`{"simple":"account/model"}`. This makes the default available when BPMN
submits the goal job too. Changes to host defaults affect later component
launches. Other runtime clients can supply that file, set `ASYS_SYSTEM_MODELS`
to another settings file, or configure an explicit worker `--model`.

The goal worker explicitly selects the built-in `simple` definition and uses
the environment's shared tools and skills. It does not inherit an environment's
same-named agent or retained memory. The implementer's ongoing conversation
belongs to this goal job; it is not shared memory for later goals. Standalone
one-shot assignments still start fresh.

## Implementation and independent verification

The original request and its governing sources remain authoritative throughout
the job. The worker does not first generate a replacement acceptance contract.

1. Implementation inspects the workspace, plans as needed, edits and checks the
   work. Its conversation continues across turns, retaining observations,
   previous work, verifier feedback and human guidance. Pi manages normal
   context compaction when necessary.
2. Each successful implementation report includes `goal_status`. `continue`
   starts another implementation turn automatically in the same conversation.
   `review` requests independent verification. A partial-progress report does
   not automatically launch a verifier.
3. A fresh verifier receives the original request, governing references, current
   workspace, unresolved findings and human guidance. It does not receive the
   implementer's completion narrative. It independently inspects artifacts and
   runs checks against the whole request.
4. If verification finds defects, missing work or insufficient evidence, its
   findings return to the continuing implementer. The implementer can work for
   further turns before requesting another fresh review. Earlier findings stay
   open until a verifier explicitly explains their resolution.

For example, an implementation report can request another work turn:

```json
{"final":"Implemented parsing; error cases still need work.","exception":null,"goal_status":"continue"}
```

When ready for independent verification, it returns `goal_status: "review"`.
The status is a required field in successful implementation reports; it uses
the existing result protocol, not a new agent tool. Neither value completes the
goal. Only verification can establish completion of the whole requested outcome,
with no unresolved findings. Planning and committing are ordinary actions when
the task calls for them, not mandatory agent stages.

Documentation can establish requirements. Completion claims in documentation,
comments, reports, or prior agent output do not establish success. The verifier
must check them. Verification is an agent assessment with recorded evidence,
not an independently proven guarantee. The verifier is instructed to avoid
repairs; it retains the environment's normal workspace access to run checks.

Malformed completion JSON gets one format correction in the same session,
preserving its work and observations. A missing or invalid `goal_status` gets
one correction in the same implementation conversation. Invalid verification
fields get one fresh verification session to correct the report, without
repeating implementation.
Uncorrected responses and execution errors fail the job rather than silently
starting an unlimited error-retry loop.

## Human help

Either implementation or verification can report a blocker through the standard
non-null `exception` field, including a `question` and optional `review_files`.
The goal worker submits the explanation to the Human service and waits. Answer
in another terminal using the same asys and dcomp state:

```sh
asys-human-prompt --system asys
```

The request explains completed work, the blocker, and the requested help.
Retry returns human guidance to the blocked phase: implementation keeps its
conversation, while verification starts fresh. Stop ends the goal
unsuccessfully. Human guidance cannot directly mark the goal verified.
Launchers automatically connect goal workers to the shared Human endpoint;
`-L human=COMPONENT.OUTPUT` can select another service.

## Results and state

Successful independent verification returns `verified: true`, a factual `final`
report, `exception: null`, and the attempt count and verification evidence.
Verification reports coverage as `complete`, `gap` or `unverified`, and provides
criteria with IDs, requirements, source bases, statuses and evidence. Completion
requires complete coverage, all criteria satisfied and every open finding
explicitly resolved with a reason and observed evidence. Previous criteria support
continuity; they do not become a replacement contract or prevent the verifier
from identifying missing outcomes.
An explicit attempt limit reached before completion, a human stop, or an
execution failure returns `verified: false` with a non-null exception. This is
a failing job that BPMN can handle with its normal error events. The host exits
with 1 on failure, or 130 on interruption. Cancellation interrupts the active
agent or withdraws the pending human request.

```sh
asys status latest
asys logs latest
asys logs latest goal
asys top
```

The job saves version 3 controller state in `goal.json` and the implementation
conversation in `implementation/session.jsonl`. Saved state includes the original
request, selected model, current phase, attempts, findings, results and human
replies. Per-turn artifacts remain under `attempts/ATTEMPT/implement-SESSION/`
and `attempts/ATTEMPT/verify-SESSION/`, including input, result, transcript,
report, proposed lessons and scratch files. Project deliverables remain in the
workspace.

Re-executing the same goal job against its saved job directory restores the
implementation conversation and controller progress. A verifier always starts
fresh, including after interruption. This recovery does not imply that a new
launcher invocation or a newly submitted BPMN job resumes an older job: a new
job starts a separate conversation. Runtime does not automatically rerun
terminal jobs, and the host launcher has no goal resume command. Existing
workspace changes and old run records remain available. Version 1 and 2 state
cannot be resumed by this controller; start a new job and retain those records
for inspection. The observer can still display all three state versions.

The [goal prompts](../asys-workers/src/goal/) describe implementation and
verification responsibilities and their result formats. Agents assess scope
and evidence; the controller preserves continuity, returns findings and permits
completion only after verification.

The [design rationale](design.md) explains the review timing and the limits of
the evidence supporting these defaults.
