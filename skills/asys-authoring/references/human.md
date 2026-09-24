# Human help and review

Bind a `userTask` to the ordinary `human` command type. Provide an intelligible
briefing from the producing task, not a generic question followed by raw state.

| Field | Content |
| --- | --- |
| `prompt` | Required question and consequences of each answer |
| `title` | Concrete decision name |
| `summary` | Work completed, changes, observed checks and unresolved issues |
| `files` | Existing workspace artifacts: `{path, label, description?}` |
| `context` | Readable background |
| `details` | Optional technical records |
| `form` | JSON Schema draft-07 describing the answer |
| `uischema` | Optional JSON Forms layout |
| `candidates` | Optional eligible human identities |

Example assignment object:

```json
{
  "title": "Review the route result",
  "prompt": "Accept writes a handover. Revise sends your comments to implementation, repeats checks and asks again. Stop ends without acceptance. Which next step do you want?",
  "summary": "The checker measured both candidates. Review evidence.md for the actual scores and remaining limitations.",
  "files": [{"path":"evidence.md","label":"Measurements and checks"}],
  "form": {
    "type":"object",
    "properties": {
      "action":{"type":"string","enum":["accept","revise","stop"]},
      "comments":{"type":"string","title":"Requested revision or decision comments"}
    },
    "required":["action"],
    "additionalProperties":false
  }
}
```

Construct this as a FEEL context in the task's `asys:job input`, or let a program
produce the object and pass it through. Without a custom form the handler uses
its default answer contract; choose an explicit form when routing depends on
typed fields. The parsed answer becomes the task's result, for example
`{"action":"revise","comments":"Add fractional-coordinate checks"}`.

Route with conditions such as `human_review.action = "revise"`. Pass
`human_review.comments` into the revision request, handling null when optional.
Re-run deterministic validation and review before asking for acceptance again.
Do not interpret “stop” as acceptance or treat a negative answer as a failed
human process.

Artifact links refer to files in the actual workspace. Ensure they exist before
asking. Explain measurements in the summary; do not make the person reconstruct
the decision from internal job directories or UUIDs. Keep secrets out of briefs.

```sh
asys-human-prompt --root ./state
asys-run ./env/development workflow.bpmn --input request.md --human
```

The first attaches to the shared service. The second creates a private service
and terminal for a workflow. The service can wait before a terminal attaches.
The dashboard displays the task; answers go through the Human service. A live
revision needs a real person's answer. Fixture answers may test routing but must
be identified as fixture data.
