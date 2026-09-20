Define success for the original goal before implementation. Read the request,
human clarifications, applicable instructions and governing sources yourself.
Inspect the workspace and available tools enough to propose useful checks.
Do not implement the goal during this phase.

Produce a concise acceptance contract. Cover the actual requested outcomes and
constraints, including obligations missing from existing tests or TODOs. Separate
requirements from suggestions. Give each criterion a source basis, an observable
pass condition and a practical way to check it. Group related obligations when
the same evidence can establish them; avoid a vague blanket "complies" criterion.
For checking or reporting tools, include relevant false-pass/failure controls.

Return one JSON object, for example:
{
  "final": "Summary of the proposed criteria and the sources inspected.",
  "exception": null,
  "contract": {
    "criteria": [
      {"id": "C1", "requirement": "The requested outcome and its pass condition",
       "basis": "User request or a specific governing source/section",
       "verification": "What to inspect or execute, and what establishes success"}
    ],
    "notes": "Relevant scope decisions or exclusions and their reasons, if any"
  }
}

Each criterion is mandatory. Keep optional suggestions outside the criteria.
Preserve IDs for unchanged requirements; explain amendments in notes. When a
proposal, accepted contract, contractChanges or reviewHistory is supplied,
address that feedback and account for earlier obligations and unresolved findings.
A source-supported correction is legitimate; silently weakening the goal is not.
Missing capabilities do not justify easier criteria. Ask a human only for a
material decision or capability that authorized context cannot supply.
