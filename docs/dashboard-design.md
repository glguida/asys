# Dashboard architecture and design

The dashboard separates execution records, interaction and presentation.
`python/asys/dashboard.py` serves saved run/job data and scoped controls.
`dashboard_workflow.py` projects saved BPMN connections and activity metadata;
it does not decide which paths execute. Boundary attachments are separate from
sequence flows, and nested activities retain their scope IDs. Task selection
uses recorded activity IDs before historical label fallbacks. Worker kinds are
saved during environment preparation so pending named tasks keep their colors.
`dashboard_assets/dashboard.mjs` coordinates selection, workflow layout,
transcripts, replay and the shared inspector. `designs/default` owns the default
stylesheet, identity and symbol settings. Alternative packages load with
`asys dashboard --design DIRECTORY`.

## Shared visual contract

The design covers the overview, workflow, console, inspector, Senate and built-in
world views. Agent, goal, Senate, swarm, program and human each have a distinct
color; status is explicit text. Default tasks are upright triangles, routing
uses inverted marks and events use ticks. Diagram connections follow actual
sequence flows, with separate lanes for feedback and separate end events.

`design.mjs` reads CSS tokens and supplies colors, fonts and normalized SVG paths
to diagrams. World views receive that theme through their mount context. The
[package guide](../skills/asys-authoring/references/design.md) specifies manifests,
assets, tokens and conversion from a supplied design document. The earlier
[graphic study](styleguide.pdf) is historical design input; the shipped package
is authoritative for current colors and customizable symbols.

## Interaction contract

Select a task, participant, artifact or message without losing the surrounding
page. The inspector stays beside the page on wide screens and is an explicit
panel on narrow screens. Selecting a swarm member changes evidence without
scrolling away; Open transcript is the navigation action.

Polling preserves focus, page position, console scroll, selections and expanded
fields. Identical saved frames do not rebuild world DOM. Freeze display pauses
polling only. Execution pause/resume/cancel use separately labeled controls.
Dragging a text selection beyond the console scrolls that box, not the document.

## Text and evidence

Transcript, output and selected data share rendering. Markdown becomes controlled
DOM; JSON becomes inspectable structure; Plain text preserves the original.
The browser does not invent summaries or remove arbitrary model fields. Tool
results, arguments, thinking text and compaction records remain available.
Raw HTML stays literal and links use allowed protocols.

The frontend bundles Marked 18.0.5 and Dagre 3.1.1 with their licenses. It requires
no CDN or browser build step. Dagre supplies node/edge layout; the host renderer
routes feedback into explicit return lanes and keeps task labels readable.

## Senate and worlds

Senators occupy several semicircular tiers; more members add seating capacity.
The princeps senatus is centered as the application's coordinating role and
retains the configured name. This is a schematic: the historical Curia Julia
used stepped banks to the left and right, as linked in the layout's explanation.

World components own domain rules. Their saved views own domain explanations,
while the host supplies common selection, history and evidence surfaces.
Historical runs retain saved renderer versions. Custom viewers must consume
shared theme values if they are to follow alternative designs fully.

## Verification

Use actual browser interaction and screenshots: trace loops to their targets,
select every worker kind, read program evidence, select Senate participants,
replay a frame, inspect member transcripts and preserve scroll across polls.
Check the default and an alternative design at narrow and wide sizes. A screenshot
establishes appearance; interaction tests establish the behavior shown.
