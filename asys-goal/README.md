# Verified goals

A goal iterates implementation and independent verification inside one ordinary
worker job. Start with a named definition or the bundled `goal`:

```sh
asys-workers add ./env/development goal repair
asys-run ./env/development repair "Implement the specification and demonstrate that its acceptance criteria hold" --workspace ./project
```

The objective and governing constraints come from the request. The controller
establishes and reviews acceptance criteria, preserves an implementation
conversation, and asks independent verification to inspect the workspace.
Verification receives the objective, contract, unresolved findings and Human
guidance without the implementer's completion narrative. Only successful
verification completes the goal.

Implementation reports `goal_status: "continue"` to keep working or `"review"`
to request verification. Any phase can surface a blocker for Human guidance.
Retry/correction retain applicable context and reopen criteria when needed.
A genuine unanswered Human question remains pending until answered or cancelled.

`config.maxAttempts` or `--parameters limits.json` with `{"maxAttempts":4}` in
that file supplies a positive limit on successful implementation turns, including continued work. The default
is unlimited. A process timeout is separate. Model selection follows the named
definition, run override and configured system default.

BPMN binds the same name with `{request: request}`. The
[portable template](../skills/asys-authoring/assets/goal.bpmn) shows the binding.
Inspect criteria, implementation, verification and Human exchanges in saved job
sessions through `asys top` or the dashboard. See
[worker definitions](../skills/asys-authoring/references/workers.md) and
[Human help](../skills/asys-authoring/references/human.md).
