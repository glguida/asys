# Human decisions

## Write the briefing at the source

The workflow/worker producing a Human request owns its explanation. The terminal
renders the supplied facts and controls; it cannot infer a useful decision from
an opaque state dump. Write for someone who knows the project but has not
followed the execution.

| Field | What the producer supplies |
| --- | --- |
| `title` | Concrete decision/problem name |
| `prompt` | Why attention is needed now, exact question/action, consequences of each choice |
| `summary` | Markdown: completed work, changes since last review, checks/results, unresolved or unverified work |
| `files` | Existing workspace artifacts as `{path, label, description?}` |
| `context` | Additional readable background |
| `details` | Technical records: revisions, diagnostic objects, counters, internal paths |
| `form` | JSON Schema draft-07 for the answer |
| `uischema` | Optional JSON Forms layout for those answer fields |
| `candidates` | Optional eligible reviewer names |

Only `prompt` is required. Essential facts belong in `prompt` and `summary`.
Use real paths, command outcomes and observed causes. Explain what Retry reruns
and what the human should change first. A dirty checkout is not a merge conflict
unless Git actually reported one. Distinguish accepting a proposal from accepting
completed changes.

For an upstream agent, request `review_summary` and `review_files` in its final
JSON, with the meanings above. Then pass those fields to the human task. These
are task-defined result fields, not automatically populated runtime metadata.
Keep its full execution report in job storage; make the briefing understandable
without needing that private report.

## A complete input example

This is an example contract. Populate the prose and file paths from the actual
work before submitting it:

```json
{
  "title": "Review the parser change",
  "prompt": "Accept the parser change for this request, or request revisions. Accepting ends this review workflow; requesting revisions sends your comments to the implementer and reruns the checks. Neither choice publishes a release.",
  "summary": "The parser now rejects empty identifiers. The targeted parser tests passed; the full suite has not been run. The diff and test log are linked below. Please check whether the error message meets the requested wording.",
  "files": [
    {"path": "src/parser.py", "label": "Parser implementation"},
    {"path": "review/changes.diff", "label": "Changes for this review"},
    {"path": "review/parser-tests.txt", "label": "Targeted test results"}
  ],
  "details": {"check": "python3 -m unittest tests.test_parser"},
  "form": {
    "type": "object",
    "properties": {
      "approved": {
        "type": "boolean",
        "title": "Accept these changes?",
        "oneOf": [
          {"const": true, "title": "Accept changes"},
          {"const": false, "title": "Request revisions"}
        ]
      },
      "comments": {"type": "string", "title": "What should change?"}
    },
    "required": ["approved", "comments"],
    "additionalProperties": false
  }
}
```

Keeping comments required permits an empty string but guarantees the next
prompt has a string field. Alternatively make it optional and handle missing
values explicitly in FEEL. Use meaningful labels with `oneOf`/`const`/`title`
while preserving the values your workflow consumes. Do not use approval-shaped
booleans for a choice that is really retry/stop; use a string enum or `oneOf`
with those values.

## Bind and route the answer

Declare `human` in `workers.json` with command
`/opt/asys/asys-workers/tools/asys-human`. A task can pass a precomputed request
object or construct it in FEEL:

```xml
<bpmn:userTask id="approval" name="Review the deliverable">
  <bpmn:extensionElements>
    <asys:job type="human" input='= {
      title: "Deliverable review",
      prompt: "Accept this deliverable to finish the workflow, or request revisions to rerun implementation and checks with your comments.",
      summary: draft.review_summary,
      files: draft.review_files,
      form: {type: "object", properties: {
        approved: {type: "boolean", oneOf: [
          {const: true, title: "Accept deliverable"},
          {const: false, title: "Request revisions"}]},
        comments: {type: "string", title: "What should change?"}
      }, required: ["approved", "comments"], additionalProperties: false}
    }'/>
  </bpmn:extensionElements>
</bpmn:userTask>
```

Add an exclusive gateway afterward: `approval.approved = true` proceeds;
the revision path returns to implementation with `approval.comments` included.
Disapproval completes the Human job successfully. The workflow owns the branch;
changing labels does not create routing behavior. Check that every consequence
described in the question is implemented by that flow.

Without a custom form, the terminal's default answer schema is `{"type":"string"}`.
The result variable is the string itself: use `help`, not `help.text`, for a
plain question whose task ID is `help`. Use an explicit object form when you
need named answer fields. Verify the contract if replacing the Human worker or
terminal.

## Evidence paths

Use workspace-relative paths or absolute worker paths **inside the workspace**.
The host maps worker paths through actual dcomp bind metadata. Files outside
the workspace and paths containing `..` are not offered as file shortcuts.
Job scratch, result JSON, and queue state are not review artifacts by default.
For work in a separate worktree, point at the actual artifacts in the mounted
workspace and explain which revision/worktree is under review.

Save an inspectable diff and relevant check reports in the workspace when
needed, and record compared revisions in `details`. Do not fabricate evidence
files to satisfy the schema. The briefing should remain useful even before the
human opens the links.

## Service and terminal

```sh
asys-human-prompt --system asys
asys-bpmn run workflow.bpmn env/development --input request.md --human
asys-bpmn resume RUN --human
```

The first attaches to the shared `@human_endpoint`; requests can arrive before
the terminal. Closing the shared terminal releases its claim and leaves requests
waiting. One terminal may attach at a time. The BPMN `--human` option instead
owns a private service/terminal for that run. Workflow progress then goes to
the run log while the terminal handles questions.

`asys-human-prompt --private --name review-desk` exports `review-desk.human`;
select it with `-L human=review-desk.human`. `--claimant NAME` selects the identity
used for candidate restrictions; it is not an authentication mechanism.
`--plain` uses line prompts, `--tui` forces the terminal UI, and `--once` detaches
after one completed answer. `--root`, `--dcomp-state-root`, and `--runtime-root`
select the corresponding state locations.

The Human service validates the submitted answer against the schema. Defaults
do not silently choose an approval. Skip leaves a request pending; quitting
releases the claim. Cancelling a worker withdraws its pending question. Stopping
a private service interrupts unanswered requests.

Before relying on a new decision, inspect it in the terminal at a normal size:
can a reviewer understand what happened, what is being asked, the consequences,
and which files establish the facts without opening the technical record?
Verify both positive and negative answers follow the intended path.
