# present: comparisons and multi-panel layouts

Read this page when the user wants saved alternatives side by side. Nothing is simulated: values come
from the saved runs through `compare`, and views from the engine viewer.

## Start

`present init RUN_A RUN_B [RUN_C] --labels "Before,After" --out DIR` adds a view per run with one
shared framing (`framing_sources`), a `targets` plot of all runs and a composition of the views
(`before-after` for two runs, `row` for more). With a study as well, `system-and-result` pairs a
view with a study curve.

## Target comparison plot

```json
{"id": "targets-compare", "kind": "plot", "type": "targets", "sources": ["before", "after"],
 "targets": ["expanded_waist"], "reference": "before"}
```

One panel per target in its own unit (a target reported in different units gets separate panels),
one point per run with its value and assessment, the target bounds as a band, and with `reference`
the difference of each run to it. A target a run does not have is marked "missing", a value that
is not exact "no value"; neither is drawn as zero.

## Composition

```json
{"id": "before-after", "kind": "compose", "layout": "before-after",
 "panels": [{"asset": "view-before"}, {"asset": "view-after"}], "shared": {"framing": true}}
{"id": "fix-and-result", "kind": "compose", "layout": "row", "panel_letters": true,
 "panels": [{"asset": "view-before"}, {"asset": "view-after"}, {"asset": "targets-compare"}]}
```

- `layout`: `row`, `column`, `grid` (`columns`, per-panel `span`, `heights`), `before-after` (exactly
  two panels with an arrow, "Before"/"After" unless `label` is given), `system-plot` (a view and a
  plot, the view wider).
- `panels`: assets of the same build in display order; `label` text or `{"en": ..., "zh-CN": ...}`
  (default: the panel's source label). Reorder or relabel here and rebuild.
- `shared.y` / `shared.color`: `auto` shares one value axis or colour scale only for the same quantity
  in the same unit; `true` for the same unit; `false`; or `[min, max]` in display units. Sweep, target
  and power panels are redrawn, so they stay vector in SVG/PDF.
- Views keep their aspect ratio; `trim` crops uniform margins. Views share one framing only when their
  view assets list the same `framing_sources`.

## Check the result

`details` of the compose entry in `assets.json` states what was applied: `scales` (shared range, or a
reason such as `UNITS_DIFFER`), `framing` (`shared` or `independent` with a recovery step) and
`text_fits`. Each panel is also written as `panel-<n>.png` (plots also `.svg`), and `layout.json`
records order, labels, spacing, applied scales and pixel boxes for slide editing.
