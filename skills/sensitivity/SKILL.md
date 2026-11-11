---
name: sensitivity
description: Finite-difference sensitivity of selected targets to explicit design parameters, with two step sizes checked against each other and every sample saved; ranks parameters per target. Use it to find which positions, angles or values a target depends on most.
version: 0.3.0
command: os-optics-kit sensitivity
requires: os-optics>=0.13,<0.14
kind: tool
---

# sensitivity

## When to use

- Rank parameters by their effect on a target around the nominal design.
- Check whether a target sits at an extremum (`NEAR_EXTREMUM`: read the second difference).

## When not to use

- Large ranges or non-linear behaviour: use `sweep`. Random offsets and yield: use `tolerance`.

## Inputs

`SPEC.json` (quantities: a number in the parameter's unit or `{"value", "unit"}`):

- `base_project` (relative to the spec; `--base` overrides) or `design` `{builder, arguments}` (a builder
  of `os_optics_kit.optical_designs`).
- `targets` (default: all), `max_evaluations` (required; 1 + 2 x steps per parameter).
- `parameters`: `{name, unit, ops, nominal?, steps? (>= 2 sizes), scale?}`; `ops` use the `edit`
  vocabulary with `"$value"` = nominal + perturbation; at zero perturbation they must reproduce the base
  Project hash (`NOMINAL_MISMATCH` otherwise).
- Optional `consistency_threshold`, `resolution` per target.

Options: `--out STUDY_DIR`, `--overwrite`, `--no-plots`, `--no-report`.
Execution (`study` skill): `--workers N|auto` (worker processes, same results as one), `--resume`
(continue a planned or interrupted study), `--plan-only` (then `os-optics-kit study run DIR`),
`--time-limit-s`, `--max-memory-mib` (per evaluation), `--ray-data summary`.

## Outputs

`study.json`, `samples/`, `table.csv`, `sensitivity.csv`, `summary.json`, `plots/` (bars and tornado
per target; skipped with a recorded reason without matplotlib), `report/`. Stdout: per target the ranked
parameters with derivative, unit, method (central / forward / backward), step ratio, second difference
and flags (STEP_DEPENDENT, NEAR_EXTREMUM, ONE_SIDED, NO_CHANGE, ...).
Derivatives, plots and report are computed from the saved samples. Exit 0 every derivative has a value
(or planned), 2 some unavailable, 4 refused spec or invalid input, 130 cancelled (resumable).

## Minimal example

`sensitivity.json`:

```
{"base_project": "project.json", "targets": ["focus_position", "spot_radius"], "max_evaluations": 5,
 "parameters": [{"name": "L1_axial", "unit": "mm", "nominal": 0, "steps": [0.01, 0.1],
   "ops": [{"op": "transform", "selector": {"ids": ["L1"]}, "frame": {"component": "L1"},
            "translation_mm": [0, 0, "$value"]}]}]}
```

```
os-optics project example --kind focus > project.json
os-optics-kit sensitivity sensitivity.json --out sens
```

## Limits

- One parameter at a time around the nominal; no interaction terms.
- Units: target unit per parameter unit; a `scale` normalizes parameters with different units.

## Version

0.3.0 (os-optics-kit 0.4.0), verified with os-optics 0.13.0 and catalog release 2026-10-28.1.
