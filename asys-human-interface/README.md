# asys-human-interface

Connect many workers' existing `asys.human.v1.Human` outputs to one host handler.
The component subscribes to `WatchAttention`, retrieves each question with
`GetTask`, and transports the existing Human operations over a runtime host
channel. Workers continue owning claims, candidate checks, form validation, and
decisions. The component needs no access to workers' job directories.

```text
worker Human outputs <-> asys-human-interface <-> one host channel <-> host handler
```

## Terminal handler

```sh
make -C asys-human-interface install
asys-human-prompt
```

Run it alongside `asys-bpmn run`, or another producer of human jobs. The dcomp
system must already exist. The command discovers all outputs with the Human
service in that system and follows new workers as they appear. To restrict it:

```sh
asys-human-prompt workers-a.human workers-b.human --claimant alice
```

Interactive terminals open a full-screen [Textual](https://textual.textualize.io/)
application. The review has a section selector and scrollable Markdown; the
response pane contains choices, multiline comments, and other schema fields.
The Files tab browses the project workspace, previews text and Markdown, and
offers Copy path and Open externally. Request counts and reconnect notices
update in place while you edit. At widths below 100 columns, Review, Files and
Response occupy separate tabs, preserving the draft when the terminal resizes.

| Key | Action |
| --- | --- |
| F2 / F3 / F4 | Review / Files / Response |
| Tab / Shift-Tab | Next / previous control |
| Arrows / Space | Navigate and select choices |
| Ctrl-S | Submit the displayed response |
| Ctrl-K | Skip this request |
| Ctrl-Q / Ctrl-C | Release the request and stop |

Clicking Submit or pressing Ctrl-S is the explicit submission step. Choices start
unselected, even when a schema declares a default. Worker validation errors stay
beside the form and retain all entered values for correction. Scroll with the
mouse, or focus the review pane and use arrows, Page Up/Down, Home/End.

`--plain` selects the line-based interface; redirected input/output also selects
it automatically. `--tui` forces the full-screen interface. Python 3.10 or later
and pip are required to install the host. `make install-host` installs just the
host and its pinned private dependencies, without rebuilding container images
or changing the user's Python environment.

The [human prompt demo](../asys-bpmn/examples/human-prompt/README.md) provides a
small workflow with parallel approval and text questions, its own environment,
and two commands to try the queue without an inference provider.

The handler owns one bridge component for its foreground session. Ctrl-C,
Ctrl-Q, SIGTERM, or `/quit` and end of input in plain mode release its current
claim and remove that component. Skip releases the current question for the rest of
this session. `--once` exits after one completed answer. Other containers keep
running. The default claimant is the host login name; `--claimant` supplies the
name checked against task candidates. These names are trusted identities, not
an authentication mechanism.

Questions enter a FIFO queue in notification order. Duplicate notifications
retain their position, and identical task IDs from different worker outputs
remain separate. Only the question being presented is claimed. Tasks already
claimed elsewhere, tasks for other candidates, and tasks completed or cancelled
while queued are skipped. The handler shows the title, prompt, context, and
origin before asking for input.

The terminal renders the task's JSON Schema `form` as individual fields. Objects
with approval and comment fields work directly; the user does not type JSON.
In plain mode, it shows a response summary and asks `Submit response? [y/n]` before sending the
answer. Choosing `n` reopens the fields, retaining the draft. Defaults in the
schema never choose a decision automatically.

Invalid field input is prompted again. The worker validates the answer against
the complete schema before recording it; rejection reopens the draft for
correction. Disapproval is a completed decision with a false value; the workflow
decides what that means for subsequent work.

`--root DIRECTORY` selects the parent for saved handler sessions, defaulting to
`$XDG_STATE_HOME/asys/human` or `$HOME/.local/state/asys/human`, or
`$ASYS_STATE_ROOT/human` when the asys base is set. Each invocation
prints its private directory, containing command logs, component logs, and a
`session.json` with its component identity and any unresolved claim or answer.
Its `request.json` contains the complete JSON presentation of the current or
most recently displayed request, including context, file locations and controls.
`--dcomp-state-root` and `--runtime-root` select nondefault dcomp directories.
`ASYS_HUMAN_INTERFACE_IMAGE` overrides the default `asys-human-interface:dev` image.

Dcomp fixes inputs when creating a component. When the set of Human outputs
changes, the launcher recreates only its bridge with the updated declarations,
retaining the host channel, queued questions, and current worker claim. Each
input is derived from its full worker endpoint, so changing discovery order
cannot redirect a saved command. Task discovery itself uses worker subscriptions;
the host only polls dcomp topology, every two seconds.
Subscription failures are checked against that topology before the terminal
reports them. A worker removed after its workflow completes is ordinary cleanup;
a worker still present gets a reconnect notice.

## Forms and presentation

Human-task input uses `form` for the answer's
[JSON Schema](https://json-schema.org/understanding-json-schema/) and an optional
sibling `uischema` for the [JSON Forms UI schema](https://jsonforms.io/docs/uischema/).
These are separate descriptions of data and presentation. The component carries
them unchanged through the existing Human interface. A graphical host can feed
them to JSON Forms as its `schema` and `uischema` and submit the same result.
JSON Forms is a framework with a documented UI schema; it is not a universal
terminal UI standard. This host implements the terminal mapping below.

The complete question screen is a JSON Forms document, not a separately printed
header followed by a form. The host combines the task's title, prompt, context,
file references and answer controls into one `uischema`. Display information
uses standard `Label` elements; answer fields use `Control` elements. The saved
`request.json` has `version`, `worker`, `task`, `title`, `prompt`, `files`, `form` and `uischema`.
It can be rendered by another frontend with `form` as the answer schema and the
complete `uischema` as its presentation. File locations stay outside the answer
data. Submission, claims and queue navigation remain host responsibilities.
Structured context becomes labeled sections. Worker result prose and domain
fields are retained; enclosing assistant transcripts, signatures, token usage
and provider metadata are excluded from the presentation.

### Workspace files

The approval view shows the actual project workspace. The Files tab starts at
that directory, where you can browse files, preview text and Markdown, and open
a selected file in its external viewer. Job records and the answer result file
are not offered as review locations. File references in the task input do not
change the browser's root.

Workers supply the workspace in `Task.metadataJson.files.workspace` through the
Human interface. The host translates that worker path using the component's
actual dcomp binds, including nested mounts. The saved document's `files` entry
has role `workspace`, a label, `workerPath`, and, when mounted on the host,
`path` and a `file:` URI. Named volumes or unmounted paths are explicitly marked
as worker paths. Files are never automatically opened, copied, or modified.
The bridge does not need access to worker job directories.

### Authoring a form

For example, a workflow can submit this human-task input:

```json
{
  "title": "Review the PCB",
  "prompt": "Review the design and verification results.",
  "form": {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
      "approved": {
        "type": "boolean",
        "oneOf": [
          {"const": true, "title": "Approve"},
          {"const": false, "title": "Disapprove"}
        ]
      },
      "comments": {"type": "string"}
    },
    "required": ["approved"],
    "additionalProperties": false
  },
  "uischema": {
    "type": "Group",
    "label": "Design review",
    "elements": [
      {"type": "Control", "scope": "#/properties/approved", "label": "Decision", "options": {"format": "radio"}},
      {"type": "Control", "scope": "#/properties/comments", "label": "Comments", "options": {"multi": true}}
    ]
  }
}
```

The TUI offers choices and a multiline comment editor. Plain mode offers
numbered decisions, then accepts comment lines until `/done`.
It constructs `{"approved":false,"comments":"Move the connector."}` internally
after the user reviews and submits. `approved` remains a boolean, and comments
remain text. Without `uischema`, fields follow their order in `properties` and
use their schema `title` or property name as a label. Existing workflows need no
changes to use the form renderer.

| JSON Schema field | Terminal presentation |
| --- | --- |
| No form, or `string` | Plain text; no JSON quoting |
| `boolean` | Yes/No choices; root booleans and fields named `approved` use Approve/Disapprove |
| `enum`, or `oneOf` with `const` and optional `title` | Choices retaining their original JSON values |
| `integer`, `number` | Numeric input with immediate type and range feedback |
| `object` | Individual fields, recursively; optional objects can be omitted |
| `array` with one `items` schema | Add/remove items with individual fields |
| `const`, `null` | A fixed value included in the response review |
| One type combined with `null` | The field's usual input plus a null option |

The renderer follows local references such as `#/definitions/address`. Schema
titles and descriptions are shown; full validation, including patterns and
constraints across fields, stays at the worker. In the TUI, optional fields have
a Value options control to distinguish omission, explicit empty text, and null.
In plain mode, blank input omits an optional field on first entry and keeps an
existing answer when editing. `/omit` removes
an optional answer, `/empty` enters an empty string, and `/null` enters an
explicit null where allowed. Omission, empty text, zero, false and null remain
distinct. JSON Schema `required` means the field must be present; use `minLength`
to require nonempty text.

The supported JSON Forms UI elements are `Control`, `Label`, `VerticalLayout`,
`HorizontalLayout`, `Group`, `Categorization` and `Category`. Layouts are visited
in declared order; horizontal layouts and tabs become sequential terminal
sections. Controls use schema pointers, including nested properties and escaped
property names. They support `label`, `options.format: "radio"` and
`options.multi: true`. TUI text editors accept newlines and literal slash commands.
In plain mode, multiline fields end with `/done`; `/keep` retains the
previous multiline answer when editing. Prefix a literal slash command with an
extra slash, for example `//quit` to enter `/quit` as text.

Schema combinations (`anyOf`, `oneOf`, `allOf`) and conditional constraints can
validate fields declared outside their branches. For example, approval can allow
an omitted comment while rejection requires a nonblank comment. The worker
validates the complete answer; a rejected answer reopens the form with its draft
preserved. Branches that introduce additional controls are not supported.

Controls must cover required fields and must not overlap. Conditional UI rules,
read-only/secret fields, other control options, tuple arrays, and remote or recursive references are not
supported by this terminal renderer. It reports unsupported forms and releases
them for another handler while continuing the queue. It does not silently
ignore behavioral UI instructions or fall back to asking the user for raw JSON.

## Component and host protocol

The supplied `component.dcomp` declares a single `human` input. For a custom
composition, declare one Human input per worker and pass `--config FILE`:

```json
{"workers":[{"id":"workers-a.human","input":"alpha"},{"id":"workers-b.human","input":"beta"}]}
```

The corresponding component declaration is:

```text
docker asys-human-interface:dev
input asys.human.v1.Human alpha
input asys.human.v1.Human beta
```

Link those inputs to the corresponding worker outputs. Bind a host directory
at `/var/lib/asys-human`, or use `--root DIRECTORY`. The default channel is
`human`; `--host-channel NAME` changes it. Only this directory crosses the host
boundary. No network port is published and the component requires no egress.
The enclosing directory grants access to the host handler; do not share it with
untrusted processes. Run one component and one logical handler per channel.

Use the existing Python or JavaScript runtime `Reader` and `Writer`. Directions
are relative to the component: the host writes `in` and reads `out`.

| Direction | Event | Data |
| --- | --- | --- |
| Out | `ready` | `workers`: configured worker IDs |
| Out | `attention` | `worker`, `task`: existing Human Task in protobuf JSON |
| In | `request` | `worker`, `method`, `body`: existing Human request in protobuf JSON |
| Out | `result` | `request`: inbound sequence, `result`: existing Human response |
| Out | `error` | `request`: inbound sequence, `code`: Connect code name, `message` |
| Out | `worker.unavailable` / `worker.available` | `worker`, with `message` on failure |

Supported methods are `ListTasks`, `GetTask`, `ClaimTask`, `ReleaseTask`, and
`CompleteTask`. The component subscribes to `WatchAttention` automatically.
For example, a host writes:

```json
{"worker":"workers-a.human","method":"ClaimTask","body":{"id":"review","claimant":"alice","claimId":"claim-1"}}
```

The correlated result contains a task and claim token. Complete with that token,
a stable `completionId`, and `resultJson`, exactly as specified by the
[existing Human interface](../asys-workers/proto/asys/human/v1/human.proto).
Task `inputJson`, `metadataJson`, and `resultJson` remain JSON strings in this
representation. IDs for tasks, claims, and completions retain their existing
worker scope; route every operation using the same `worker`.

Replies are published before the component advances its inbound cursor.
Interrupted operations can replay: preserve `claimId`, `completionId`, tokens,
and answer content on retries. Worker subscriptions reconnect independently and
recover outstanding tasks, including claimed tasks. Attention events can repeat.
They are hints, so fetch the latest task before acting; the current worker stream
does not report every cancellation or completion. A channel write failure stops
the component rather than silently discarding a question.

The terminal command cleans up on ordinary exits and signals. SIGKILL or host
failure can leave a component and claim behind; `session.json` preserves their
identities for recovery. Claims have the existing Human API's explicit-release
semantics and do not expire automatically.

## Development

```sh
make test-deps                    # repository root
make -C asys-human-interface test
make -C asys-human-interface build
python3 asys-human-interface/tools/integration-test
```

The integration test uses isolated dcomp state and requires `asys-workers:dev`
and the human interface image. It tests real worker RPC, dynamic attachment,
queued approval/comment forms, text answers, explicit submission, JSON Forms
controls and multiline input, worker validation and correction, signal cleanup,
and retention of existing worker containers. The ordinary tests cover schema
field rendering, edits, unsupported UI behavior, reconnects, command
replay, validation, channel ownership, failed publication, and host installation.

The generated Human definitions are reused through the small
`@asys/human-protocol` package in `asys-workers/gen`; the bridge image does not
install agent dependencies. The workers' `npm run generate` also restores this
package's manifest after regenerating its definitions. Further host handlers can reuse this component and
channel contract with their own presentation and delivery mechanisms.
