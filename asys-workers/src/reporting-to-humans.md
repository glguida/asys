# Reporting to humans

Write for someone who knows the project but has not followed your execution.
Give them enough verified context to understand the outcome or make the
requested decision. Use plain language and concrete names for files, changes,
and operations.

Start a report with the result: what was requested, what you accomplished, and
what remains. Explain changed behavior, checks performed and their results,
and anything unverified. Separate completed work from plans and expectations.
Keep routine details brief; give consequential failures enough explanation.
An outcome report may need several paragraphs. "Done" or "Stage failed" is
insufficient.

When preparing a Human request, put that explanation into its JSON fields:

| Field | What to supply |
| --- | --- |
| `title` | A concrete name for the decision or problem. |
| `prompt` | Why the person is needed now, the exact question or requested action, and what each choice will cause. |
| `summary` | Markdown explaining work completed, changes since the previous review, evidence from checks, and remaining work. |
| `files` | Existing review artifacts as `{path, label, description?}`. Use workspace-relative paths or absolute paths inside the workspace, including the actual worktree when relevant. Label what each file helps the person inspect. |
| `context` | Additional readable explanation supporting the decision. |
| `details` | Technical records such as revisions, counters, state paths, and full diagnostic objects. |
| `form` | The required answer schema. Give choices and comment fields meaningful labels while preserving the values the caller expects. |

Essential facts belong in `prompt` and `summary`; file links and technical
records support that explanation. A raw state object or a short completion
message alone is not a briefing. Include relevant diffs and check reports when
available; never invent files, results, or evidence to fill a field.

For a blocker, explain the observed cause, affected operation, preserved work,
and intervention needed. For Retry, say what must change first and which work
will repeat. Verify consequences from the supplied task or workflow; identify
unknowns explicitly. Distinguish accepting a proposal from accepting completed
work. Preserve an existing question's decision scope and answer contract.

When a task requests `review_summary` and `review_files` in your final result,
prepare them as the `summary` and `files` above. They must contain the actual
briefing and evidence references. Keep the full execution report in `report.md`;
the human briefing must make sense without access to that job record.

Before handing off, check that the person can tell what happened, why their
attention is needed, what they can do next, and where to inspect the evidence.
