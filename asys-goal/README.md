# asys-goal

Implement a goal and independently verify it, repeating until verification
succeeds. There is no default attempt limit. Both phases use the built-in `simple`
agent and the same model, with a fresh session for every invocation.

```sh
asys system-model set simple account/model
asys-goal ./env/development "Implement the requested behavior and its tests" \
  --workspace ./project
```

`--model MODEL` overrides the `simple` default for this run. An unset default
without an override fails before creating the run and prints setup instructions.
`--max-attempts N` optionally caps implementation/verification cycles; omitted,
the goal keeps working until verified, stopped by a human, or cancelled.
Human-assisted retries repeat the blocked phase within its current cycle.
The workspace defaults to the current directory and must already exist.

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

The workers image includes the same built-in `simple` prompt that core asys
installs. The goal worker selects that definition explicitly and uses the
environment's shared tools and skills. It does not inherit an environment's
same-named agent or retained memory.

## Verification and human help

The original goal stays fixed. Implementation gets previous verification
findings and implementation lessons. Verification gets the goal and human
guidance, without the implementation's completion report or previous verdicts.
Each criterion must report an observed source, finding, and whether it is met.
Malformed or contradictory verdicts fail the job. An unmet criterion starts
another implementation cycle, subject only to an explicitly configured limit.

Verification instructions require inspecting artifacts and running checks.
Completion claims in documentation, comments, reports, or prior agent output
do not establish success. The verifier must check them. This is an agent
assessment with recorded evidence, not an independently proven guarantee.
Verification is instructed to avoid repairs; it has the environment's normal
workspace access so it can run checks.

Either phase can report a blocker through the standard non-null `exception`
field, including a `question` and optional `review_files`. The goal worker
submits the explanation to the Human service and waits. Answer in another
terminal using the same asys and dcomp state:

```sh
asys-human-prompt --system asys
```

The request explains completed work, the blocker, and the requested help.
Retry starts a fresh session for that phase with the human's guidance. Stop
ends the goal unsuccessfully. Human guidance cannot directly mark the goal
verified. Launchers automatically connect goal workers to the shared Human
endpoint; `-L human=COMPONENT.OUTPUT` can select another service.

## Results and state

Successful verification returns `verified: true`, the factual `final` report,
`exception: null`, the attempt count, and evidence in `criteria`. Exhaustion,
a human stop, or an execution failure returns `verified: false` and a non-null
exception, producing a failing job that BPMN can handle with its normal error
events. The host exits with 1 on failure, or 130 on interruption. Cancellation
interrupts the active agent or withdraws the pending human request.

```sh
asys status latest
asys logs latest
asys logs latest goal
asys top
```

`asys top` shows phase progress and the sessions' transcripts. The goal job
keeps `goal.json` with the goal, selected model, attempts, results, lessons,
and human replies. Each session has its own directory under
`attempts/ATTEMPT/implement-RETRY` or `attempts/ATTEMPT/verify-RETRY`, containing
its input, result, transcript, report, proposed lessons, and scratch space.
Human requests and answers are saved beside the session that needed help.
The agent definition remains unchanged. Starting another goal invocation
creates a new job; existing workspace changes and old run records remain.
