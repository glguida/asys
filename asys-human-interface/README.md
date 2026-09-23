# asys-human-interface

The Human service receives questions from workers and presents them in a host
terminal. It owns the request queue, claims, candidate checks, form validation,
and answers. Workers call its typed `asys.human.v1.Human` interface; the terminal
uses the service's runtime host channel. No worker job directories are shared
with the service.

```text
workers.human -> @human_endpoint -> Human service <-> host channel <-> terminal
```

## Terminal handler

```sh
make -C asys-human-interface install
asys-human-prompt
```

Workflows start the shared Human component automatically when they need
`@human_endpoint`. Requests wait in that service until a terminal attaches;
the prompt can start before or after the workflow. Starting the prompt also
creates the service if needed. One terminal may attach at a time.

For a handler dedicated to one workflow, use the workflow launcher:

```sh
asys-bpmn run workflow.bpmn env/design --input request.md --human
asys-bpmn resume RUN --human
```

The launcher starts a private Human component and a host terminal handler for
this run. It configures the workers' Human port as an input and links it directly
to the private component. The environment's source files and `@human_endpoint`
are unchanged. The host handler owns the terminal while workflow progress goes
to the run log, available through `asys logs RUN` and `asys top`. When the run
ends, the handler removes its private component and restores the terminal
before the workflow command prints its result.

`asys-human-prompt --private --name review-desk` exports `review-desk.human`
without assigning a global. Other launchers can select it explicitly with
`-L human=review-desk.human`. `--claimant alice` changes the reviewer identity.

Interactive terminals open a full-screen [Textual](https://textual.textualize.io/)
application. Review opens with the question, work summary, and file links. Additional
supporting information has a section selector and scrollable Markdown; the
response pane contains choices, multiline comments, and other schema fields.
The Files tab browses the project workspace, previews text and Markdown, and
offers Back, Copy path and Open externally. File links open in this preview;
links inside a document resolve relative to that document. Copy path copies
only when explicitly selected. The Technical tab holds job identifiers,
metadata, and the full supplied request. Request counts
update in place while you edit. At widths below 100 columns, Review, Files, Technical and
Response occupy separate tabs, preserving the draft when the terminal resizes.

| Key | Action |
| --- | --- |
| F2 / F3 / F4 / F5 | Review / Files / Response / Technical |
| Tab / Shift-Tab | Next / previous control |
| Arrows / Space | Navigate and select choices |
| Ctrl-S | Submit the displayed response |
| Ctrl-K | Skip this request |
| Ctrl-Q / Ctrl-C | Release the request and stop |

Clicking Submit or pressing Ctrl-S is the explicit submission step. Choices start
unselected, even when a schema declares a default. Validation errors stay
beside the form and retain all entered values for correction. Scroll with the
mouse, or focus the review pane and use arrows, Page Up/Down, Home/End.

`--plain` selects the line-based interface; redirected input/output also selects
it automatically. Use `/details` at any input prompt to inspect the technical
record without changing your answer. `--tui` forces the full-screen interface. Python 3.10 or later
and pip are required to install the host. `make install-host` installs just the
host and its pinned private dependencies, without rebuilding container images
or changing the user's Python environment.

The [human prompt demo](../asys-bpmn/examples/human-prompt/README.md) provides a
small workflow with parallel approval and text questions, its own environment,
and two commands to try the queue without an inference provider.

The shared component keeps running when the terminal exits. Ctrl-C, Ctrl-Q,
SIGTERM, `/quit`, and end of input release the current claim and detach; waiting
jobs remain pending. Reconnecting restores pending questions and recovers an
abandoned claim after a terminal crash. Skip leaves a question pending for a
later terminal session. `--once` detaches after one completed answer.

Private handlers (`--private`, including workflow `--human`) own their service
for their foreground session and remove it on exit. Stopping a private service
interrupts its unanswered requests.

The default claimant is the host login name; `--claimant` supplies the name
checked against task candidates. These names are trusted identities, not an
authentication mechanism.

Questions enter a FIFO queue in notification order. Duplicate notifications
retain their position, and job IDs are qualified by their originating component, so different workers
remain separate. Only the question being presented is claimed. Tasks already
claimed elsewhere, tasks for other candidates, and tasks completed or cancelled
while queued are skipped. The handler shows the question and the supplied work
summary before asking for input. Origin identifiers are in Technical.

The terminal renders the task's JSON Schema `form` as individual fields. Objects
with approval and comment fields work directly; the user does not type JSON.
In plain mode, it shows a response summary and asks `Submit response? [y/n]` before sending the
answer. Choosing `n` reopens the fields, retaining the draft. Defaults in the
schema never choose a decision automatically.

Invalid field input is prompted again. The human service validates the answer against
the complete schema before recording it; rejection reopens the draft for
correction. Disapproval is a completed decision with a false value; the workflow
decides what that means for subsequent work.

`--root DIRECTORY` selects the asys system root, overriding `ASYS_STATE_ROOT`.
The default root is `$XDG_STATE_HOME/asys` or `$HOME/.local/state/asys`.
Human service state and handler sessions are saved in `ROOT/human`. A shared
service reuses its saved directory; a private handler creates a session
directory. The terminal
prints that directory, containing command logs and a
`session.json` with its component identity and any unresolved claim or answer.
Its `request.json` contains the complete JSON presentation of the current or
most recently displayed request, including context, file locations and controls.
`--dcomp-state-root` and `--runtime-root` select nondefault dcomp directories.
`ASYS_HUMAN_INTERFACE_IMAGE` overrides the default `asys-human-interface:dev` image.

Workers need no discovery or rewiring when a default handler changes. Their
link remains `@human_endpoint`, which dcomp resolves. Each human job sends one
`Ask` RPC and waits for its answer. Cancelling the job disconnects that request
and withdraws the unanswered question. A completed request can be retrieved
again with the same ID and input without asking twice.

After installation, `asys update` refreshes the shared service with the current
image whether a terminal is attached or not. It also creates the service for a
running inference installation that has none. An unchanged image is left
running. Private handlers are excluded. Replacing the service interrupts
unanswered requests, so finish human decisions before changing its image.

Upgrading from 0.1.0 requires a one-time handler restart and a change to the
worker environment's Human declaration; follow the
[upgrade instructions](../INSTALL.md#upgrade-from-010).

## Forms and presentation

Human-task input uses `form` for the answer's
[JSON Schema](https://json-schema.org/understanding-json-schema/) and an optional
sibling `uischema` for the [JSON Forms UI schema](https://jsonforms.io/docs/uischema/).
These are separate descriptions of data and presentation. The component carries
them unchanged through the Human interface. A graphical host can feed
them to JSON Forms as its `schema` and `uischema` and submit the same result.
JSON Forms is a framework with a documented UI schema; it is not a universal
terminal UI standard. This host implements the terminal mapping below.

The workflow or environment writes the request. The host presents its content
and collects an answer; it does not generate explanations from state or logs.
The short [Reporting to humans guide](../asys-workers/src/reporting-to-humans.md)
explains how to compose the request and is included in built-in agent prompts.
The [environment authoring guide](../asys-bpmn/AUTHORING.md#write-a-decision-the-human-can-understand)
describes how to prepare the question and evidence.

| Input field | Content and presentation |
| --- | --- |
| `title` | Short name for this decision |
| `prompt` | Required question, concrete issue, and requested action; appears first |
| `summary` | Optional Markdown describing work completed, changes, checks, and remaining work; appears on the first page under Work so far |
| `files` | Optional list of `{path, label, description?}` entries for changed files, diffs, and evidence inside the workspace; first-page links open in Files |
| `context` | Optional supporting explanation, rendered as additional Review sections |
| `details` | Optional technical data such as full diagnostics, state paths, revisions, and counters; available in Technical |
| `form`, `uischema` | Answer schema and optional control layout |
| `candidates` | Optional eligible reviewer names |

The host combines the task's title, prompt, summary, context, file references
and answer controls into one `uischema`. Display information
uses standard `Label` elements; answer fields use `Control` elements. The saved
`request.json` has `version`, `worker`, `task`, `title`, `prompt`, `summary`, `files`,
`technical`, `fileMounts`, `form` and `uischema`. `fileMounts` records the relevant
worker mounts so links inside documents can be resolved on the host.
It can be rendered by another frontend with `form` as the answer schema and the
complete `uischema` as its presentation. File locations stay outside the answer
data. Submission, claims and queue navigation remain host responsibilities.
`technical` retains the complete supplied request and metadata, including
`details`, and the originating worker and task IDs. No domain evidence is
truncated or summarized by the presenter. Structured context becomes labeled
sections. Worker result prose and domain
fields are retained; enclosing assistant transcripts, signatures, token usage
and provider metadata are excluded from the presentation.

### Workspace files

The approval view shows the actual project workspace. The Files tab starts at
that directory, where you can browse files, preview text and Markdown, and open
a selected file in its external viewer. Explicit `files` entries add shortcuts
to relevant files and directories without replacing the workspace browser.
Use workspace-relative paths, or absolute worker paths inside the workspace.
Paths outside the workspace and paths containing `..` are not offered as
shortcuts. Job records and the answer result file are not automatically offered
as review locations. References are retained in the technical record even when
they cannot be offered as file shortcuts.

Clicking a local Markdown link opens the file inside the Files tab, including
workspace-relative links, absolute worker paths, and host `file:` URLs. Links
inside a preview resolve relative to that file. Back returns to the previous
file or directory; F2 returns to the question without losing the response.
Missing files, unmounted worker paths, and paths outside the workspace show an
explanation in the preview. Web links open in the browser. Binary and specialized
design files use Open externally; text and Markdown can be read in the terminal.

Workers supply the workspace in `Task.metadataJson.files.workspace` through the
Human interface. The host translates that worker path using the component's
actual dcomp binds, including nested mounts. The saved document's `files` entry
has role `workspace` or `file`, a label, `workerPath`, and, when mounted on the host,
`path` and a `file:` URI. Named volumes or unmounted paths are explicitly marked
as worker paths. Files are never automatically opened, copied, or modified.
The service does not need access to worker job directories.

### Authoring a form

For example, a workflow can submit this human-task input:

```json
{
  "title": "Review the PCB",
  "prompt": "Approve this PCB revision for fabrication, or describe the corrections you need before approval.",
  "summary": "Moved connector J1 to the board edge and rerouted its power traces. The design-rule check passed. Connector clearance still needs your review.",
  "files": [
    {"path": "pcb/board.kicad_pcb", "label": "Changed PCB", "description": "Inspect J1 and its power traces."},
    {"path": "pcb/review/changes.diff", "label": "Changes since the previous review"},
    {"path": "pcb/review/drc.txt", "label": "Design-rule check report"}
  ],
  "details": {"revision": 5},
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
constraints across fields, stays at the human service. In the TUI, optional fields have
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
an omitted comment while rejection requires a nonblank comment. The human service
validates the complete answer; a rejected answer reopens the form with its draft
preserved. Branches that introduce additional controls are not supported.

Controls must cover required fields and must not overlap. Conditional UI rules,
read-only/secret fields, other control options, tuple arrays, and remote or recursive references are not
supported by this terminal renderer. It reports unsupported forms and releases
them for another handler while continuing the queue. It does not silently
ignore behavioral UI instructions or fall back to asking the user for raw JSON.

## Component and host protocol

The supplied manifest exports one Human interface:

```text
docker asys-human-interface:dev
output asys.human.v1.Human human
```

A worker environment declares `input asys.human.v1.Human human`. Host launchers
supply this input when an environment directly runs the built-in `asys-human`
command, or when BPMN is run or resumed with `--human`. They correct an omitted
input or an output declaration in the run's workers definition, leaving the
source environment unchanged. Custom programs that call Human declare the
input explicitly. Its default connection is `@human_endpoint`;
`-L human=TARGET` chooses another service, and `--human` selects the run's private
component.
`Ask` carries a unique request ID, the input JSON, and metadata including the
originating component and its workspace. It returns the answer JSON. The other
Human methods support task presentation, claims, and validated completion.

Bind service state at `/var/lib/asys-human`, or use `--root DIRECTORY`.
The default host channel is `human`; `--host-channel NAME` changes it. No network
port is published and the component requires no egress. Run one component and
one logical host handler per channel. Questions and decisions are persisted in
`tasks.sqlite` in this private service directory.

Use the existing Python or JavaScript runtime `Reader` and `Writer`. Directions
are relative to the component: the host writes `in` and reads `out`.

| Direction | Event | Data |
| --- | --- | --- |
| Out | `ready` | Empty object |
| Out | `attention` | `worker`, `task`: Human Task in protobuf JSON |
| In | `request` | `worker`, `method`, `body`: Human request in protobuf JSON |
| Out | `result` | `request`: inbound sequence, `result`: Human response |
| Out | `error` | `request`: inbound sequence, `code`: Connect code name, `message` |

Supported methods are `ListTasks`, `GetTask`, `ClaimTask`, `ReleaseTask`, and
`CompleteTask`. The component subscribes to `WatchAttention` automatically.
For example, a host writes:

```json
{"worker":"workers-a","method":"ClaimTask","body":{"id":"workers-a.review","claimant":"alice","claimId":"claim-1"}}
```

The correlated result contains a task and claim token. Complete with that token,
a stable `completionId`, and `resultJson`, exactly as specified by the
[existing Human interface](../asys-workers/proto/asys/human/v1/human.proto).
Task `inputJson`, `metadataJson`, and `resultJson` remain JSON strings in this
representation. A task ID includes its originating component and job ID.

Replies are published before advancing the inbound cursor. Interrupted host
operations can replay: preserve `claimId`, `completionId`, tokens, and answer
content on retries. Attention notifications include outstanding questions on
subscription and new or released questions afterward. Fetch the current task
before acting on a notification. A channel write failure stops the component.

Completed answers survive service restarts. In-flight RPCs do not: the worker
fails and a workflow can handle the error or retry the job. SIGKILL or host
failure can leave a component behind; `session.json` records its identity.

The foreground handler holds `handler.lock` for its lifetime. `asys update`
selects live shared handlers under the asys state's `human/` directory, then
sends `update` with an immutable image ID through `channels/control/in` in the
handler session directory. The handler performs the dcomp lifecycle changes
and replies on `out` with `updated` or `error`, correlated by request sequence.
This control channel is between host processes and is not mounted in a component.
