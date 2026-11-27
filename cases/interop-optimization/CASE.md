---
name: interop-optimization
title: External optimization and Sobol tolerance of a beam expander
summary: SciPy and Optuna collimate a catalog-free Keplerian expander (ideal thin lenses f = 50 mm and 150 mm, 532 nm, 0.5 mm waist) by moving L2, with every design run by the engine; SALib Sobol indices then rank the mounting errors. An ABCD reference checks every value.
class: free-space
domain: gaussian-beam
provenance: self-built
publication: own-work
engine: 0.14.0
catalog: none
project: project.json
tools: optimize
tags: beam-expander, optimization, sensitivity, interop, scipy, optuna, salib
license: MIT
---

## What it shows

`project.json` is the baseline: source, L1 at x = 100 mm, L2 at a 200 mm separation, plane `output` at
700 mm, target `collimated` (divergence half-angle ≤ 1 mrad). `optimize_expander.py` sets L2's position per
evaluation, runs the engine in memory and reads `divergence_half_angle_x_rad`; `evaluations.jsonl` logs every
evaluation. Verified run: SciPy reaches the ABCD optimum (200.0573 mm) to 9e-7 mm in 10 evaluations;
Optuna's best design is within 0.14 % of the optimal divergence; engine values match the reference to 2e-14; the
engine and reference Sobol indices agree to 2e-14. Outcome WITHIN_TOLERANCE (710 evaluations, 1.9 min).

## Run

```bash
os-optics project run project.json --out-dir run
os-optics results targets run
python -m pip install -r requirements-optimization.txt
python optimize_expander.py --out opt
python optimize_expander.py --out opt-strict --max-divergence-rad 1e-9
os-optics-kit optimize studies/optimize-expander.json --out kit-opt
os-optics-kit optimize studies/optimize-expander-infeasible.json --out kit-strict
```

The strict requirement leaves no feasible design: the script exits 2 (NO_FEASIBLE_RESULT), the kit 1
(`NO_FEASIBLE`, every margin in `history.jsonl`).

## Targets

| Target | Metric | Plane | Bound | Verified |
| --- | --- | --- | --- | --- |
| `collimated` | `divergence_half_angle_x_rad` | `output` | ≤ 1 mrad | 0.1129 mrad, MET |

## Baseline

os-optics 0.14.0, no catalog, vacuum (`environment.pressure_pa` 0, as on 0.13.0): run status PASS (`expected.json`). The optimization ran with os-optics 0.13.0 on Python 3.14.7 with
the pinned versions in `requirements-optimization.txt`.

## Files

- `project.json`, `expected.json`: the baseline design and its verified outcome.
- `optimize_expander.py`: optimization, Sobol analysis and the ABCD reference.
- `studies/optimize-expander*.json`: the same search as `os-optics-kit optimize` specs.
- `requirements-optimization.txt`: pinned packages.
- `README.md`, `README.zh-CN.md`: the illustrated pages; `images.json` records `images/iso.png` and
  `images/section.png`.
