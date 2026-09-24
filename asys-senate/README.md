# Senate deliberation

A Senate is one named worker containing separate participant conversations.
Its princeps senatus coordinates the discussion and delivers the decision.
Senators represent professional roles, such as Seasoned engineer, Numerical
analyst and Verification engineer. The princeps keeps the configured name.

```sh
asys-workers add ./env/research senate review
asys-workers edit ./env/research review
asys-run ./env/research review "Review the supplied specification and evidence" --workspace ./project
```

The [definition reference](../skills/asys-authoring/references/workers.md#senate)
contains the complete JSON shape. Names must be nonempty and unique. Each
participant may specify additional `prompt`, selected environment `agent`
assets and `model`. The optional `prompt` can be a personality paragraph
steering temperament, reasoning and discussion style; it stays in that
participant's instructions throughout deliberation. Omitted agent assets use the bundled simple agent. A
participant's model takes precedence over the run fallback and system default.

## Discussion and results

The princeps introduces the topic. Senators intervene in configuration order,
receiving the discussion so far. The princeps assesses consensus after each
round. Consensus ends deliberation; after three negative assessments the
princeps makes a separate final decision. Agreement is assessed, not counted
as a deterministic vote.

The terminating princeps report supplies `final`, `exception` and task-defined
fields. The controller adds `consensus`, `rounds` and `decision`. A decision
without consensus can still be successful. Ask explicitly for fields such as
boolean `approved` and textual `reason` when a workflow depends on them.
Individual senators' reports are preserved but not merged into a synthetic vote.

All participants share the selected workspace and installed tools. Review-only
instructions do not create filesystem isolation. Research needs the environment's
actual network access and tools; Senate adds no separate search service.

## Inspect and reuse

The dashboard shows three or more stepped semicircles, placing senators across
the tiers as their number grows. Select a participant for their role and saved
conversation. The princeps sits at the center as an asys coordination schematic;
the history note explains the difference from the Curia Julia's side banks.
Transcript and result use the same rendering as other jobs.

BPMN uses `type="review"` and `input="= {request: request}"`. The complete
[mixed review example](../asys-bpmn/examples/mixed-review/README.md) combines it
with agent work, independent validation and Human decisions. Read
[reviewing](reviewing.md) for acceptance and revision contracts.
