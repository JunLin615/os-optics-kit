---
name: plot
description: Draw standard PNG and SVG plots from saved results without simulating - sweep curves or two-parameter heatmaps with target bounds, the power distribution of a run, signed bar charts and histograms. Use it to show study or run results.
version: 0.2.0
command: os-optics-kit plot
requires: os-optics>=0.13,<0.14; os-optics-kit[plot]
kind: tool
---

# plot

## When to use

- Show a target against a swept parameter (`sweep`), every sample marked by its outcome.
- Show where a run's power went (`power`).
- Plot your own labelled values (`bars`) or a value distribution with bounds (`histogram`).

## When not to use

- 3D views or ray pictures: use `os-optics view RUN`.
- Numbers for a decision: read `summary`, `compare` or the study's `summary.json`.

## Inputs

- `sweep STUDY_DIR --y COLUMN`: COLUMN is `target:<id>`, `derived:<name>` or `param:<name>`.
- `power RUN_DIR`.
- `bars DATA.json`: `{"labels", "values", "unit", "title", "ylabel"?}`.
- `histogram DATA.json`: `{"values", "unit", "title", "xlabel"?, "bounds"?: {"minimum", "maximum"}, "bins"?}`.
- `--out DIR` (default: `plots/` in the study, or next to the run), `--name STEM`.

## Outputs

`<name>.png` and `<name>.svg`, each captioned with the data file (relative path and sha256 prefix), the
engine build and the catalog release. Stdout: one JSON document with the written paths and point counts.
Exit 0 written, 4 invalid input or missing matplotlib (`PLOT_EXTRA_MISSING`).

## Minimal example

```
python -m pip install "os-optics-kit[plot]"
os-optics project example --kind focus > project.json
os-optics project run project.json --out-dir run
os-optics-kit plot power run
```

After a `sweep` into `study`: `os-optics-kit plot sweep study --y target:spot_radius`.

## Limits

- Samples without an exact value are drawn as hollow markers below the lowest value, never as values.
- `bars` and `histogram` plot the given numbers only; units come from the data file.

## Version

0.2.0 (os-optics-kit 0.2.0), verified with os-optics 0.13.0, matplotlib 3.11 and catalog release
2026-10-28.1.
