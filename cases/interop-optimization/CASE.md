---
name: interop-optimization
title: External optimization and Sobol tolerance of a beam expander
summary: SciPy and Optuna collimate a catalog-free Keplerian expander (ideal thin lenses f = 50 mm and 150 mm, 532 nm, 0.5 mm waist) by moving L2, with every design run by the engine; SALib Sobol indices then rank the mounting errors. An ABCD reference checks every value.
engine: 0.13.0
catalog: none
project: project.json
tools: none
tags: catalog-free, interop, optimization, scipy, optuna, salib, sensitivity
license: MIT
---

## What it shows

`project.json` is the baseline the optimizer edits: source, L1 at x = 100 mm, L2 at a 200 mm separation, plane
`output` at 700 mm, target `collimated` (divergence half-angle ≤ 1 mrad). `optimize_expander.py` copies it per
evaluation, sets L2's position by `instance_id`, runs the engine in memory and reads
`divergence_half_angle_x_rad`. Each evaluation is logged with its run identity in `evaluations.jsonl`, including
unmet and refused designs. Verified run: SciPy reaches the ABCD optimum (200.0573 mm) to 9e-7 mm in 10 evaluations;
Optuna's best design is within 0.14 % of the optimal divergence; engine values match the reference to 2e-14; the
engine and reference Sobol indices agree to 2e-14. Outcome WITHIN_TOLERANCE (710 evaluations, 1.9 min).
The 640 Sobol samples are independent: they run as one os-optics-kit study batch (`opt/tolerance-batch/`),
serially or with `--workers N` in N worker processes; both give the same values and report.

## Run

```bash
os-optics project run project.json --out-dir run
os-optics results targets run
python -m pip install -r requirements-optimization.txt
python optimize_expander.py --out opt
python optimize_expander.py --out opt-strict --max-divergence-rad 1e-9
python optimize_expander.py --out opt-w4 --workers 4
```

The strict requirement leaves no feasible design: outcome NO_FEASIBLE_RESULT, exit code 2.

## Targets

| Target | Metric | Plane | Bound | Verified |
| --- | --- | --- | --- | --- |
| `collimated` | `divergence_half_angle_x_rad` | `output` | ≤ 1 mrad | 0.1129 mrad, MET |

## Baseline

os-optics 0.13.0, no catalog: run status PASS (`expected.json`). The optimization was verified on Python 3.14.7 with
the pinned versions in `requirements-optimization.txt`. `os-optics-kit cases verify interop-optimization` repeats
the engine run.

## Files

- `project.json`, `expected.json`: the baseline design and its verified outcome.
- `optimize_expander.py`: optimization, Sobol analysis and the ABCD reference.
- `requirements-optimization.txt`: pinned third-party packages.
- `README.md`, `README.zh-CN.md`, `images/section.png`: the illustrated page.
