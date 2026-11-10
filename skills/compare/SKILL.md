---
name: compare
description: Compare saved runs, or the samples of a sweep study, by target and observation-plane ID without rerunning; lists differing inputs, deltas to a reference run and, with a current Project, each run's freshness. Use it to explain what an edit changed.
version: 0.2.0
command: os-optics-kit compare
requires: os-optics>=0.13,<0.14
kind: tool
---

# compare

## When to use

- Before/after an edit: which targets, plane powers and inputs differ.
- Check whether saved runs are still CURRENT for the Project you now have.

## When not to use

- One run only: use `summary`. Charts: use `plot`. A written report: use `report`.

## Inputs

- `PATH ...`: run directories (each holds `manifest.json`) or study directories written by `sweep`.
- `--labels a,b`, `--reference LABEL` (default: first run), `--targets a,b` (ids deciding outcomes).
- `--project CURRENT.json`: report freshness CURRENT / HISTORICAL / UNDETERMINED with engine reasons.
- `--out DIR`: also write `compare.json` and `compare.csv`.

## Outputs

Stdout: runs with outcome, status, freshness and identity; targets by ID with values, units, statuses
and deltas to the reference; observation planes (power, arriving beams); input differences keyed by
object ID (at most 8 runs and 10 differences shown; the files hold everything).
Exit 0 compared, 4 invalid input.

## Minimal example

`edits.json`:

```
[{"op": "transform", "selector": {"ids": ["L1"]}, "frame": {"component": "L1"},
  "translation_mm": [0, 0, {"value": 500, "unit": "um"}]}]
```

```
os-optics project example --kind focus > project.json
os-optics project run project.json --out-dir run
os-optics-kit edit project.json --edits edits.json --out moved.json --run run-moved
os-optics-kit compare run run-moved --labels base,moved --project moved.json --out cmp
```

## Limits

- Deltas are given only between exact applicable values in the same unit.
- A historical run keeps its result; its outcome is labelled, e.g. `HISTORICAL PASS`.

## Version

0.2.0 (os-optics-kit 0.2.0), verified with os-optics 0.13.0 and catalog release 2026-10-28.1.
