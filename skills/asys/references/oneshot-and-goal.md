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

Use a concrete goal with observable criteria and the location of its inputs.
Avoid making the verifier infer success from prose like “the task is done.”
The goal worker alternates:

1. A fresh `simple` implementation session works on the fixed original goal.
   Later attempts receive prior verification findings and implementation lessons.
2. A fresh `simple` verification session checks the goal against actual artifacts
   and executed checks. It receives the goal and human guidance, without the
   implementation's completion narrative or earlier verdicts.
3. An unmet verdict starts another implementation cycle. A verified verdict
   finishes the job. Either phase can report a blocker and ask a human.

Both phases use the same chosen model and the shared `simple` definition.
Verification is instructed to inspect facts, including actual file contents,
commands and outputs. Documentation, comments, commit messages and agent
reports can identify things to check, but their claims do not establish that
the goal is met. The verifier is instructed not to repair the work. Its normal
workspace access is still available for checks; this is not an OS-enforced
read-only role.

There is **no default attempt limit**. Set `--max-attempts N` only when an
explicit limit is wanted. A cycle is implementation plus verification; retries
of a blocked phase after human help stay in that cycle. Execution failures or
invalid verifier output can still fail the job: unlimited attempts do not
silently retry every transport/parser failure forever.

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
or give it retained memory.

## Human escalation

Either phase can finish with a non-null `exception`, explaining why it cannot
continue. Include a concrete `question` and workspace `review_files` when
useful. The worker composes a Human request and waits for retry guidance or stop.

```sh
asys-human-prompt --system asys
```

Use the same asys/dcomp state as the run. Goal launchers wire the shared Human
input automatically; `-L human=COMPONENT.OUTPUT` selects another service.
BPMN can also use `--human` for a terminal attached to that run.

Retry launches the same phase in a fresh session with the human question and
answer preserved. Stop ends the goal unsuccessfully. A human answer cannot
directly mark the goal verified. Missing service/transport failures and malformed
completion/verdict JSON remain job errors rather than agent-authored requests
for help. Cancellation interrupts an active session or withdraws a waiting Human
request.

## Results, evidence, and retained state

Successful result shape:

```json
{
  "final": "The required behavior was observed in the built program and checks.",
  "exception": null,
  "verified": true,
  "attempts": 2,
  "criteria": [
    {
      "requirement": "Reject an empty identifier",
      "satisfied": true,
      "evidence": [
        {"source": "python3 -m unittest tests.test_identifier", "observation": "The empty-identifier test passed against the modified parser."}
      ]
    }
  ]
}
```

This is illustrative, not evidence that this command was run in your project.
The worker requires nonempty criteria/evidence and consistency between the
criterion booleans and verdict. Those checks enforce the report structure;
the factual judgment is still made by the verifier agent.

An explicit attempt limit reached with unmet criteria, human stop, or execution
failure returns an unsuccessful job. BPMN can use ordinary boundary errors.
Inspect the saved result rather than expecting successful JSON on stdout after
a host failure.

`JOB/goal.json` preserves original goal, selected model, attempts, session
results, lessons, and human replies. Per-phase files live under
`attempts/ATTEMPT/implement-RETRY/` and `attempts/ATTEMPT/verify-RETRY/`:
input, result, transcript, stdout, report, proposed lessons and scratch. Human
request/answer files stay beside the session that needed them.

```sh
asys status latest --json
asys logs latest goal
asys top
```

Lessons remain available in run state for later review; the system agent does
not rewrite its own definition. Another invocation starts a new goal job, not
an automatic continuation of the old transcript. BPMN resume likewise restarts
a failed goal task as a new job while retaining workspace files and old records.
