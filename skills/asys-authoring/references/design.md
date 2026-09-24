# Dashboard design packages

Presentation is separate from execution and graph layout. The default package
contains the shared stylesheet and logo; an alternative package overrides its
CSS and metadata. No dashboard source edits or build are required.

The complete default CSS is in `designs/default/styles.css` in a checkout, or
`PREFIX/share/asys/designs/default/styles.css` after installation. Inspect it for
the shared component rules. Custom CSS loads after that stylesheet, so a package
can override only the tokens and rules it needs.

For a complete contrasting example, inspect `designs/slate` in a checkout or
`PREFIX/share/asys/designs/slate` after installation. It bundles serif fonts,
rounded task marks, circular gateways, a new logo and a dark palette. Start it
on a separate port to compare designs against the same runs:

```sh
asys dashboard --root ./state --port 8776 --design ./designs/slate
```

```text
my-design/
  design.json
  styles.css
  logo.svg
  fonts/display.woff2
```

```json
{
  "version": 1,
  "name": "Research console",
  "stylesheet": "styles.css",
  "logo": "logo.svg",
  "wordmark": "asys",
  "title": "Research console"
}
```

`version`, `name` and `stylesheet` are required. Logo, wordmark and title are
optional. All asset paths stay inside the package. Stylesheets can use local
relative font/image URLs. The host serves CSS, image and font assets only.

```sh
asys dashboard --root ./state --design ./my-design
```

## Translate a design document

Read the supplied document, inspect any referenced visual assets, and extract
its palette, type hierarchy, spacing, borders, task marks and identity. Create
the package above. Convert that specification to CSS and assets; the dashboard
does not interpret a PDF or ask a model to generate styles at runtime.

Preserve execution semantics while changing presentation. Types need distinct
marks or colors and visible labels. Status remains text. Keep selected/focused
items identifiable, controls usable, transcript JSON legible, and the workflow's
real connections intact. A visual redesign must not change which task an edge
connects to or which human answer a button submits.

Use the existing default layout unless the document requires a different one.
The shared stylesheet covers overview, workflow, inspector, console, Senate and
built-in worlds. Override component classes for deeper changes. Historical
third-party world modules may carry their own presentation; they must consume
theme context to follow every token.

## Shared tokens

```css
:root {
  --carta: #f4f4f2;
  --inchiostro: #20252a;
  --campo: #e4e8eb;
  --rail: #7c8790;
  --quiet: #59636b;
  --verde: #35744b; /* agent */
  --goal: #167d9a;
  --senate: #8954a6;
  --swarm: #c26926;
  --blu: #2a5caa; /* program */
  --rosso: #c43e39; /* human */
  --giallo: #daa51a; /* gateway */
  --font: "Research Sans", Arial, sans-serif;
  --mono: "Research Mono", monospace;
  --task-shape: "M0 0L1 0L1 1L0 1Z"; /* square instead of triangle */
  --gateway-shape: "M.5 0L1 .5L.5 1L0 .5Z";
  --task-clip: polygon(0 0, 100% 0, 100% 100%, 0 100%);
}
```

The color variable names retain the default design's vocabulary. `--task-shape`
and `--gateway-shape` are SVG path data in a 0–1 coordinate box. They drive actual
workflow and Senate marks, legends and built-in world marks. `--task-clip` can
override small CSS-only world legend marks when needed. For fonts:

```css
@font-face {
  font-family: "Research Sans";
  src: url("fonts/display.woff2") format("woff2");
}
```

Useful stable selectors include `.masthead`, `.identity`, `.brand-logo`,
`.wordmark`, `.run-table`, `.sequence`, `.work-mark[data-kind]`, `.symbol-legend`,
`.selection-inspector`, `.console`, `.message-markdown`, `.message-fields`,
`.senate-viewport`, `.leader`, `.torus`, and `.habitat-view`.

Verify an alternative package in a browser with a workflow, program result,
Senate and swarm. Check narrow and wide screens, keyboard focus, label contrast,
font loading, task selection and console scrolling. Compare screenshots to the
supplied design document. Deliver the package path and exact launch command.
