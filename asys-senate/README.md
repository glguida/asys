# asys-senate

Ask a group of agents for one answer. The Princeps senatus introduces the topic,
senators intervene one by one, and the Princeps checks for consensus after each
round. Discussion ends when the Princeps reports consensus. After three rounds
without consensus, the Princeps decides the answer and explains the remaining
disagreement.

The whole Senate is **one runtime job**, usable as **one BPMN task**, like a goal
job. Its deliberation is controlled inside the worker. It can answer a question,
compare proposals, or act as a review committee. See
[using a Senate for reviews](reviewing.md) for structured approval and BPMN
failure handling.

```sh
asys-senate ENVIRONMENT_DIRECTORY "topic" --senate senate.json \
  [--model MODEL] [--workspace DIRECTORY]
```

Install with `make install` from the repository root, or
`make -C asys-senate install` for the Senate launcher and shared worker images.
Start an inference server exporting `@inference_endpoint` in the selected dcomp
system. See [host installation](../INSTALL.md) and the
[environment example](../asys-oneshot/README.md#environment).

## Describe the participants

The host reads `senate.json` and passes a parsed snapshot in the job input:

```json
{
  "version": 1,
  "princeps": {
    "name": "Princeps senatus",
    "prompt": "Seek a clear answer supported by evidence. Preserve material dissent."
  },
  "senators": [
    {
      "name": "Cicero",
      "prompt": "Examine the strongest case for each proposal and its practical consequences."
    },
    {
      "name": "Cato",
      "prompt": "Test assumptions, identify risks, and challenge unsupported claims."
    }
  ]
}
```

`version` must be `1`. The Princeps and every senator require a nonempty, unique
`name`; uniqueness is checked after trimming whitespace and is case-sensitive.
At least one senator is required. Array order determines speaking order.
Each participant can also specify:

| Field | Meaning |
| --- | --- |
| `prompt` | Additional instructions describing this participant's perspective. |
| `agent` | A named agent from the selected worker environment. Omitted, use the built-in `simple` agent. |
| `model` | An exported Provider model name for this participant. |

Only the fields shown here are supported; unknown fields are rejected. Named
agents must exist under `ENVIRONMENT_DIRECTORY/agents/NAME/`. Agent names contain
letters, digits, `.`, `_`, or `-`, start with a letter or digit, and are at most
128 characters. Omitting `agent` selects the bundled system `simple` definition,
even if the environment has an agent named `simple`.

A participant's `model` takes precedence over the launcher's `--model` fallback,
which takes precedence over the `simple` system-model default. If every participant
specifies a model, no fallback or system default is needed. All models use the
same Provider interface; configure providers and credentials through
`asys-inference`. The Senate file describes participants, not inference endpoints.

The selected agent contributes its usual instructions, memory, skills, tools and
extensions. Participants using `simple` get the environment's shared resources.
Their conversations remain separate and each intervention receives the shared
discussion transcript, including earlier interventions in that round.
All participants share the job's workspace and access. A review-only persona is
an instruction, not a separate filesystem permission boundary.

Copy the [example configuration](examples/senate.json), configure a model and run:

```sh
asys system-model set simple account/model
asys-senate ./env/research "Which option best satisfies the supplied requirements?" \
  --senate ./senate.json --workspace ./project
```

The workspace defaults to the current directory and must already exist. It is
the ordinary worker workspace, with the environment's normal tools and access.
`--system NAME` selects the dcomp system; `-L inference=COMPONENT.OUTPUT` selects
another Provider output.

`--root DIRECTORY` selects the asys system root, overriding `ASYS_STATE_ROOT`.
Runs are saved in `ROOT/runs` and model defaults come from `ROOT/config.json`.
Pass the same root to `asys system-model`, `asys status`, `asys logs`, and
`asys top`.

## Online research

Senators use `asys-agent` directly and inherit its existing web-search
capabilities, tools, skills and extensions. Each senator can research during its
own intervention and cite sources in the discussion. There is no Quaestor or
separate Senate search implementation.

The selected environment's normal network and tool configuration applies:
`egress: true` enables outbound access, and the environment supplies the tools
available to its agents. Enabling egress does not install a search tool. An
environment already configured for agent web research needs no Senate-specific
search setup.

## Worker and runtime

The host starts one workers component, submits one Senate job through the runtime
filesystem queue, waits for its result and removes the component. The Senate
controller runs inside `asys-workers`, just like the goal controller. Runtime
supervises execution; the worker controls deliberation; the Provider interface
supplies inference. No Senate-specific inference service or BPMN engine is needed.

```text
Host launcher or BPMN task
  -> runtime queue: one senate job
     -> Senate controller in asys-workers
        -> Princeps / senators: ordinary asys-agent calls
           -> common Provider interface
```

The standalone launcher supplies the Senate job type automatically. For BPMN,
expose the worker in the selected environment's `workers.json` and use an image
built with the Senate worker:

```json
{
  "version": 1,
  "name": "research",
  "egress": true,
  "types": {
    "senate": {
      "command": ["/opt/asys/asys-workers/tools/asys-senate"]
    }
  }
}
```

Its input contains `topic` and the complete parsed `senate` configuration:

```json
{
  "topic": "Which option best satisfies the supplied requirements?",
  "senate": {
    "version": 1,
    "princeps": {"name": "Princeps senatus"},
    "senators": [{"name": "Cicero"}, {"name": "Cato"}]
  }
}
```

The worker command accepts `--model MODEL` as the fallback for participants
without their own model. Otherwise it reads the `simple` default from the
system-model settings snapshot supplied by Asys launchers. Other runtime clients
can supply `/etc/asys/system-models.json`, select a settings file with
`ASYS_SYSTEM_MODELS`, or set participant models explicitly.

A BPMN task submits the same job directly; it does not run the host launcher or
create a nested workflow. For example, with the request text in `request` and
the parsed configuration in `senate_config`:

```xml
<bpmn:serviceTask id="deliberate" name="Ask the Senate">
  <bpmn:extensionElements>
    <asys:job type="senate"
      input="= {topic: request, senate: senate_config}" result="review"/>
  </bpmn:extensionElements>
</bpmn:serviceTask>
```

Declare `xmlns:asys="urn:asys:workflow:1"` on the BPMN definitions. Here,
`senate_config` is a workflow variable containing the JSON object, not a filename.
For example, an earlier program task can read a roster packaged in the environment
and return it into that variable. The host's `--senate FILE` option is specific to
`asys-senate`; a BPMN task supplies the parsed object in its input. See
[workflow authoring](../asys-bpmn/AUTHORING.md) for environment and data bindings.

## Deliberation and results

1. Princeps introduces the topic and frames the issues.
2. Every senator intervenes in configuration order, using the discussion so far.
3. Princeps assesses consensus with a required boolean `consensus` field. A
   positive assessment supplies the final answer; otherwise another round begins.
4. After the third negative assessment, Princeps makes a separate final decision.

The introduction is round zero; discussion rounds are numbered one through three.
The three-round limit and sequential speaking order are fixed. Consensus is the
Princeps's assessment of agreement, not a deterministic vote count.

A decision without consensus is a successful answer, distinguished from a
consensus by the result fields:

```json
{
  "final": "The Senate's answer to the original question.",
  "exception": null,
  "consensus": false,
  "rounds": 3,
  "decision": "princeps"
}
```

| Field | Meaning |
| --- | --- |
| `final` | Final answer from the Princeps, or an explanation of an execution failure. |
| `exception` | `null` on successful execution; an error string on failure. |
| `consensus` | `true` only when an assessment ended the discussion with consensus. |
| `rounds` | Discussion round reached; normally 1–3 on success, possibly 0 on failure. |
| `decision` | `"consensus"`, `"princeps"`, or `null` on failure. |

An agreed rejection of a proposal can have `consensus: true`. Successful
execution means the Senate produced an answer; it does not mean that answer
approves the proposal.

The result preserves the terminal Princeps report's structured fields alongside
the ordinary `final` and `exception` envelope. Request fields such as `approved`,
`reason`, or `findings` in the topic or Princeps instructions. A BPMN binding with
`result="review"` makes them available directly as `review.approved`,
`review.reason`, and `review.findings`, just like an ordinary agent's result.
Keep `final` as readable text; no JSON encoding inside that string is needed.
See the [review guide](reviewing.md) for an approval contract and ordinary BPMN
routing or validation.

Only the report that ends the discussion supplies these fields: the successful
consensus assessment or final Princeps decision. Intermediate reports are not
merged into the result. When a participant explicitly reports an exception, its
report's fields are preserved in the failed result. The controller owns
`consensus`, `rounds`, and `decision` and overwrites any participant-supplied values
for that metadata. Task-defined fields have no built-in schema or approval
semantics; request their meaning and validate them as your workflow requires.

A participant exception fails the Senate instead of becoming a Princeps
decision. Invalid phase reports can receive one format correction; an unresolved
report error fails execution. The worker saves its result and exits nonzero on
failure. The standalone host prints result JSON to stdout on success, exits with
1 on job failure and 130 on interruption. Cancellation interrupts the active
agent. Early setup failures may occur before a Senate result is available.

## Read the debate

```sh
asys status latest
asys status latest --json
asys logs latest
asys top
```

In `asys top`, select the run and its Senate job. The transcript view shows phase,
round and speaker headings with agent messages and tool activity, including
streaming output. It works during execution and after components are removed.
Use `asys top --root ./state` if the launcher used `--root ./state`; those runs
are stored under `./state/runs`.

`asys logs RUN` shows the launcher log; `asys logs RUN JOB -f` follows the job's
stdout and stderr. These are execution logs, not a Markdown debate export.

The debate belongs to the **job directory**. Find its exact path in
`asys status RUN --json` under `jobs[].directory`. With the normal launchers the
layout is:

```text
RUN_DIRECTORY/
  senate.json                       # standalone host's input roster snapshot
  jobs/JOB_ID/
    senate.json                     # controller state and completed debate
    result.json                     # the Senate's returned result
    participants/
      princeps/session.jsonl        # Princeps's persistent conversation
      senator-1/session.jsonl       # first senator's persistent conversation
      ...
    phases/
      1-introduce/
        input.json                  # this turn's assignment
        result.json                 # this turn's report
        agent.json                  # full agent transcript
        stdout.log                  # this turn's events
      2-intervene/
      ...
```

The run-level `senate.json` is only the standalone launcher's roster copy; the
job-level file contains the discussion. Its `transcript` array stores completed
contributions in speaking order, each with `phase`, `round`, `participant` and
`final`, plus `consensus` on assessments. The full tool conversations are in the
phase and participant records. Runtime queue state remains under
`runtime/environments/ENVIRONMENT/jobs/JOB_ID/`. Project deliverables remain in
the workspace. See [monitoring](../docs/monitoring.md) for observer controls.

Phase events include `senate.phase_started` and `senate.phase_finished`, with the
phase, round and participant. `senate.finished` records the outcome, rounds,
consensus and decision. These records make the discussion inspectable alongside
the ordinary agent events.

### Export a readable copy

The worker saves JSON records and agent conversations automatically. It does not
currently create `deliberation.md` or provide a Markdown export command. To make
a readable copy of the completed contributions, run this on the host after the
job finishes, using the job directory reported by `asys status`:

```sh
python3 - /path/to/run/jobs/JOB_ID <<'PY'
import json
from pathlib import Path
import sys

job = Path(sys.argv[1])
state = json.loads((job / "senate.json").read_text(encoding="utf-8"))
lines = ["# Senate deliberation", "", state["topic"], ""]
for turn in state["transcript"]:
    lines += [f"## Round {turn['round']} — {turn['phase']} — {turn['participant']}",
              "", turn["final"], ""]
    if "consensus" in turn:
        lines += [f"Consensus: {str(turn['consensus']).lower()}", ""]
if state.get("result"):
    lines += ["## Result", "", "```json",
              json.dumps(state["result"], indent=2, ensure_ascii=False), "```", ""]
output = job / "deliberation.md"
output.write_text("\n".join(lines), encoding="utf-8")
print(output)
PY
```

This only formats saved records; it does not call a model. It exports the
completed speeches and outcome, not every tool call or an unfinished speech.
The original job records remain the detailed execution history.

## Interruption and recovery

Ordinary agent inference recovery applies within each speech. A transient
inference failure or provider-capacity wait keeps that speech pending until
recovery or cancellation; it does not consume a discussion round. Permanent
agent exceptions still fail the Senate.

Re-executing an interrupted job against its existing directory restores saved
controller state and participant conversations. The topic, resolved roster and
workspace must match. Checkpointed contributions are retained; the interrupted
turn may continue with partial tool effects already present. Recovery is not a
rollback or a guarantee that every tool operation runs exactly once.

Completed and failed checkpoints return their saved result; re-execution does
not reopen their discussion or transform an older result into a new schema.
A new launcher invocation creates a new job. There is no host Senate resume
command, and runtime does not automatically rerun terminal jobs.
