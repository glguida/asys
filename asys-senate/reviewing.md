# Review with a Senate

Give the Senate the specification, actual submitted artifacts and acceptance
criteria. Ask it to inspect evidence independently and produce a typed verdict:

```json
{"final":"The uncertainty calculation fails the reference case","exception":null,"approved":false,"reason":"Correct the propagated uncertainty and repeat the supplied checks."}
```

Use the [review configuration](examples/review-senate.json) as the `config` of a
named Senate definition. Its professional participants examine implementation
and numerical correctness. Select role prompts appropriate to the project.

Execution success, consensus and approval are distinct. `exception: null` means
the discussion executed; `consensus` describes agreement; `approved` is the
requested verdict. A unanimous rejection is valid execution with a negative
verdict. Validate required fields before taking consequential downstream actions.

For a normal revision workflow, bind the Senate result as `review`, then route
`review.approved = true` to the next step and the default branch back to
implementation. Pass `review.reason` into the next assignment. Re-run the
independent program checks and Senate after revision. Do not repeatedly ask the
same question without giving the author the findings.

If a project instead requires rejection to fail execution, an ordinary program
can validate the verdict, save it through `ASYS_RESULT`, and exit nonzero on
rejection. BPMN boundary errors can distinguish rejection from malformed output.
This policy belongs to the workflow, not to the Senate controller.

Review instructions do not make the workspace read-only. When unchanged
submissions are an acceptance criterion, record and compare hashes in a program
check. Keep scientific acceptance criteria in deterministic checks where possible.

The [workflow reference](../skills/asys-authoring/references/workflows.md)
contains gateway and boundary-error examples. The
[mixed review](../asys-bpmn/examples/mixed-review/README.md) includes shared
artifacts, an independent program and a Human revision branch. The dashboard
and `asys top RUN` preserve the actual discussion and dissent.
