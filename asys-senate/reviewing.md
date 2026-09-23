# Use a Senate as a review committee

A Senate can review the output of an author agent within a normal BPMN workflow:

```text
author (agent) -> committee (senate) -> verdict gate (program) -> next step
                                           |
                                           +-> rejection: fail the workflow
```

The entire discussion runs inside the committee's single job. The verdict gate
only interprets the answer and applies the workflow's acceptance policy. It does
not coordinate the senators. The example below fails on rejection, without an
automatic author revision or review retry.

For setup, model selection, web research, and reading the debate, see the
[Senate guide](README.md).

## Define the review

Start with [review-senate.json](examples/review-senate.json). It defines a Princeps,
a seasoned software engineer, and a numerical analyst. The reviewers inspect the
actual code, run checks, and discuss correctness and scientific assumptions.
Replace the analyst's persona for a different domain.

Give the author a specification and explicit deliverables. Give the Senate the
same specification, the files to inspect, and acceptance criteria. For example:

```sh
asys-senate ./env/research \
  "Review fit.py against specification.md. Run its tests and independently check numerical edge cases. Do not edit the submitted source or tests." \
  --senate ./review-senate.json --model pool/gpt-5.5 --workspace ./project
```

Here `pool/gpt-5.5` must be a model exported by your Provider; substitute the
exported name used by your installation. This command assumes that the roster has
been copied into the current directory and that `project` contains the submission.
Every participant uses the fallback model unless its configuration overrides it.

Review instructions do not enforce read-only access. Participants share the
workspace and tools. If acceptance requires unchanged submitted files, have the
workflow record their hashes before review and verify them in the gate. Keep
deterministic scientific checks in that gate as well when they are part of the
acceptance criterion.

## Distinguish execution, agreement, and approval

These are separate outcomes:

| Question | Where to read it |
| --- | --- |
| Did the committee execute successfully? | Runtime job status and `review.exception` |
| Did the Princeps report agreement? | `review.consensus` and `review.decision` |
| Did the committee approve the submission? | An explicit verdict requested inside `review.final` |

A unanimous rejection is a successfully completed discussion with
`consensus: true`. A Princeps decision after three rounds can approve or reject
with `consensus: false`. Decide whether your workflow accepts a chair's decision
or additionally requires consensus; the example gate below accepts either kind
of decision when its verdict is approval.

The Senate returns only `final`, `exception`, `consensus`, `rounds`, and
`decision`. Unlike an ordinary agent's result, arbitrary participant result
fields are not propagated. Asking for an outer `approved` field does not make
`review.approved` available to BPMN.

The example roster instead asks the Princeps to encode a small JSON verdict
inside its final answer string. A completed review can therefore return:

```json
{
  "final": "{\"approved\":false,\"reason\":\"Reject: uncertainty estimates fail the supplied reference cases.\"}",
  "exception": null,
  "consensus": true,
  "rounds": 1,
  "decision": "consensus"
}
```

This is a prompt-level contract. Senate does not validate this inner JSON, so a
deterministic program must parse and validate it before acting on it. The roster
requests this format only for the terminal answer; intermediate speeches remain
readable prose.

## Bind the Senate and gate in BPMN

The following fragments belong in an existing workflow with
`xmlns:asys="urn:asys:workflow:1"`. Supply `review_request` as a string and
`senate_config` as the parsed roster object. An earlier setup program can read
the roster from the environment, write it to `ASYS_RESULT`, and bind its output
using `result="senate_config"`. Passing a filename as the Senate input does not
load it; the BPMN launcher's `--input` is text, not an automatic roster parser.

The environment needs both `senate` and `program` types:

```json
{
  "version": 1,
  "name": "review",
  "egress": true,
  "types": {
    "senate": {"command": ["/opt/asys/asys-workers/tools/asys-senate", "--model", "pool/gpt-5.5"]},
    "program": {"command": ["/opt/asys/asys-workers/tools/asys-program"]}
  }
}
```

Keep the author agent's type alongside these entries. Package the gate script
below at `programs/review_gate.py` in the environment, with Python available in
its image. The standard environment layout places it under
`/opt/asys/environment/programs/`.

```xml
<bpmn:serviceTask id="committee_review" name="Review the submission">
  <bpmn:extensionElements>
    <asys:job type="senate"
      input="= {topic: review_request, senate: senate_config}" result="review"/>
  </bpmn:extensionElements>
</bpmn:serviceTask>

<bpmn:sequenceFlow id="review_to_gate"
  sourceRef="committee_review" targetRef="review_gate"/>

<bpmn:serviceTask id="review_gate" name="Require approval">
  <bpmn:extensionElements>
    <asys:job type="program"
      args='= ["python3", "/opt/asys/environment/programs/review_gate.py"]'
      input="= {review: review}" result="approval"/>
  </bpmn:extensionElements>
</bpmn:serviceTask>
```

Connect the author to `committee_review` and `review_gate` to the next activity
using ordinary sequence flows. This gate implements a strict two-field verdict
contract and saves its structured result before exiting:

```python
import json
import os
from pathlib import Path
import sys

try:
    assignment = json.loads(Path(os.environ["ASYS_INPUT"]).read_text(encoding="utf-8"))
    review = assignment["review"]
    if review["exception"] is not None:
        raise ValueError("Senate execution did not succeed")
    if not isinstance(review["final"], str):
        raise ValueError("Senate final must be a JSON string")
    verdict = json.loads(review["final"])
    if not isinstance(verdict, dict) or set(verdict) != {"approved", "reason"}:
        raise ValueError("Verdict must contain exactly approved and reason")
    if type(verdict["approved"]) is not bool:
        raise ValueError("approved must be a JSON boolean")
    if not isinstance(verdict["reason"], str) or not verdict["reason"].strip():
        raise ValueError("reason must be nonempty text")
    result = {**verdict, "exception": None}
    code = 0 if verdict["approved"] else 17
except (KeyError, TypeError, ValueError) as error:
    reason = f"Invalid Senate verdict: {error}"
    result = {"approved": False, "reason": reason, "exception": reason}
    code = 18

Path(os.environ["ASYS_RESULT"]).write_text(
    json.dumps(result, ensure_ascii=False) + "\n", encoding="utf-8")
sys.exit(code)
```

| Gate outcome | Program exit | Workflow behavior without a handler |
| --- | --- | --- |
| Valid approval | 0 | Continue; the next step can use `approval.approved` and `approval.reason`. |
| Valid rejection | 17 | Fail the workflow; retain the rejection reason. |
| Malformed or missing verdict | 18 | Fail the workflow; retain the format error. |

Codes 17 and 18 are choices made by this example, not reserved Senate codes.
The BPMN activity error code is the program's exit code. The host workflow
launcher reports an unhandled workflow failure with exit 1; it does not forward
17 or 18 as its own exit code. A Senate execution failure stops at
`committee_review` before reaching the gate and can be handled on that task.

## Catch rejection when the workflow needs a handler

To fail immediately, leave gate errors unhandled. If a later workflow activity
must record or route a rejection, declare this error under BPMN definitions:

```xml
<bpmn:error id="ReviewRejected" errorCode="17"/>
```

Then attach an error boundary inside the process and connect it to your handler:

```xml
<bpmn:boundaryEvent id="review_rejected" attachedToRef="review_gate">
  <bpmn:errorEventDefinition errorRef="ReviewRejected"/>
</bpmn:boundaryEvent>
<bpmn:sequenceFlow id="handle_review_rejection"
  sourceRef="review_rejected" targetRef="record_rejection"/>
```

Define `record_rejection` as a normal task. It can consume the gate result, for
example with `input="= {verdict: approval}"`, including `approval.reason` from
the failed job. A separate boundary for code 18 can distinguish an unusable
answer from an explicit rejection. Handling an error follows the path you draw;
it no longer implies that the overall workflow ends in failure. Do not connect
that path back to the author unless revision is intended.

The [workflow authoring guide](../asys-bpmn/AUTHORING.md#rejection-and-failure)
describes the general error and result-binding rules. Read the committee's
speeches and evidence in its job records or in `asys top`, as described under
[Read the debate](README.md#read-the-debate).
