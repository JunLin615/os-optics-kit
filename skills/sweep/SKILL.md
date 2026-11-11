---
name: sweep
description: Run a bounded one- or two-parameter sweep of a Project from a JSON spec; every sample's Project and run are saved, counted by outcome, and ranked by an optional objective under constraints. Use it to explore a design parameter.
version: 0.3.0
command: os-optics-kit sweep
requires: os-optics>=0.13,<0.14
kind: tool
---

# sweep

## When to use

- See how targets change over explicit values of one parameter, or a grid of two.
- Find the best feasible sample for a stated objective and constraints.

## When not to use

- Local derivatives: use `sensitivity`. Random manufacturing offsets: use `tolerance`.
- A single change: use `edit`.

## Inputs

`SPEC.json`:

- `base_project` (relative to the spec; `--base` overrides) and `max_evaluations` (required budget,
  checked before any run).
- `parameters`: 1 or 2 items `{name, unit, values | range {start, stop, count}, ops}`; `ops` use the
  `edit` vocabulary with the placeholder `"$value"` (or `{"$value": {"scale", "offset"}}`).
- Optional: `targets` (ids deciding outcomes), `constraints`, `objective` `{column, sense}`, `derived`,
  `stop_on`. Columns: `param:<name>`, `target:<id>`, `derived:<name>`.

Options: `--out STUDY_DIR` (new or empty), `--overwrite`.
Execution (`study` skill): `--workers N|auto` (worker processes, same results as one), `--resume`
(continue a planned or interrupted study), `--plan-only` (then `os-optics-kit study run DIR`),
`--time-limit-s`, `--max-memory-mib` (per evaluation), `--ray-data summary`.

## Outputs

`STUDY_DIR/study.json`, `plan.jsonl`, `requests/`, `samples/<id>/{project.json, run/, sample.json}`,
`journal.jsonl`, `table.csv`, `summary.json` (rows in sample order).
Stdout: counts per outcome with denominators, feasible count, best sample or `no_feasible`, samples with
warnings, top rows, execution, paths. Exit 0 completed or planned, 1 no feasible sample, 4 refused spec or
invalid input, 130 cancelled (resumable).

## Minimal example

`sweep.json`:

```
{"base_project": "project.json", "max_evaluations": 5,
 "parameters": [{"name": "l1_shift", "unit": "mm", "values": [-1, 0, 1],
   "ops": [{"op": "transform", "selector": {"ids": ["L1"]}, "frame": {"component": "L1"},
            "translation_mm": [0, 0, "$value"]}]}],
 "objective": {"column": "target:spot_radius", "sense": "minimize"}}
```

```
os-optics project example --kind focus > project.json
os-optics-kit sweep sweep.json --out study
```

## Limits

- At most two parameters; every sample is one full engine run.
- Unknown target ids or columns refuse the spec before any run.
- No sample is dropped: ERROR, UNKNOWN and OUT_OF_MODEL samples stay in the table.
- `stop_on` acts in sample order: every sample after the first listed outcome is `STOPPED`.

## Version

0.3.0 (os-optics-kit 0.4.0), verified with os-optics 0.13.0 and catalog release 2026-10-28.1.
