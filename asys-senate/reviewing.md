# Use a Senate as a review committee

A Senate can review the output of an author agent within a normal BPMN workflow:

```text
author (agent) -> committee (senate) -> next task or approval gateway
```

The entire discussion runs inside the committee's single job. Its structured
result is available to subsequent tasks and gateways directly, using the same
contract as an ordinary agent. No Senate-specific decoding step is needed.

For a policy that fails the workflow on rejection, the optional program gate
below validates the verdict and exits nonzero. It works with ordinary agent
results too and does not coordinate the senators. This example has no automatic
author revision or review retry.

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
| Did the committee approve the submission? | The requested boolean `review.approved`, explained by `review.reason` |

A unanimous rejection is a successfully completed discussion with
`consensus: true`. A Princeps decision after three rounds can approve or reject
with `consensus: false`. Decide whether your workflow accepts a chair's decision
or additionally requires consensus; the example gate below accepts either kind
of decision when its verdict is approval.

The example roster requests `approved` and `reason` as top-level fields alongside
`final` and `exception`. The Senate preserves the terminal Princeps report and
adds its deliberation metadata. A completed review can return:

```json
{
  "final": "The committee recommends rejecting this submission until its uncertainty calculation is corrected.",
  "exception": null,
  "approved": false,
  "reason": "Uncertainty estimates fail the supplied reference cases.",
  "consensus": true,
  "rounds": 1,
  "decision": "consensus"
}
```

The task defines the meaning and types of `approved` and `reason`, as it would
for an ordinary agent. Senate validates the ordinary report envelope but does
not impose a schema on these extra fields. A workflow can validate its required
fields before acting on them. Keep `final` as readable prose.

Only the terminating assessment or final Princeps decision supplies the result's
custom fields; the controller does not combine individual senators' verdicts.
The fields `consensus`, `rounds`, and `decision` are reserved controller metadata.
An explicit execution exception preserves the failing participant's report,
including any supplied diagnostic fields, and fails the Senate job.

With `result="review"`, an exclusive gateway can use the verdict directly:

```xml
<bpmn:exclusiveGateway id="review_decision" default="review_declined"/>
<bpmn:sequenceFlow id="review_accepted" sourceRef="review_decision" targetRef="next_step">
  <bpmn:conditionExpression xsi:type="bpmn:tFormalExpression" language="feel">
    review.approved = true
  </bpmn:conditionExpression>
</bpmn:sequenceFlow>
<bpmn:sequenceFlow id="review_declined" sourceRef="review_decision" targetRef="record_rejection"/>
```

Declare `xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"` on the definitions
for this expression. Connect the Senate task to the gateway and define the
`next_step` and `record_rejection` activities. This routes a successful answer;
a rejection path is not itself an execution failure. Use the following optional
gate when rejection must fail the workflow instead.

## Fail on rejection with an ordinary program gate

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
using ordinary sequence flows:

```text
author -> committee -> approval gate -> next step
                            |
                            +-> rejection: fail the workflow
```

This gate validates the ordinary report envelope and the required approval
fields, retains all result fields, and saves its result before exiting. It
consumes `review` directly and can follow any worker that returns this contract:

```python
import json
import os
from pathlib import Path
import sys

try:
    assignment = json.loads(Path(os.environ["ASYS_INPUT"]).read_text(encoding="utf-8"))
    review = assignment["review"]
    if not isinstance(review, dict):
        raise ValueError("Review must be an object")
    if review["exception"] is not None:
        raise ValueError("Review execution did not succeed")
    if not isinstance(review["final"], str) or not review["final"].strip():
        raise ValueError("final must be nonempty text")
    if type(review["approved"]) is not bool:
        raise ValueError("approved must be a JSON boolean")
    if not isinstance(review["reason"], str) or not review["reason"].strip():
        raise ValueError("reason must be nonempty text")
    result = review
    code = 0 if review["approved"] else 17
except (KeyError, TypeError, ValueError) as error:
    reason = f"Invalid review result: {error}"
    result = {"final": reason, "approved": False, "reason": reason, "exception": reason}
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
