---
name: interop-metasurface
title: Metasurface lens as a tabulated virtual component
summary: A TiO2 nanopillar metalens (f = 200 um at 532 nm, 100 um diameter) is characterized with RCWA (grcwa) and angular-spectrum focusing, fitted as a TABULATED_THIN_LENS with stated errors and domain, and checked in the engine on held-out test cases and out-of-domain counterexamples.
engine: 0.13.0
catalog: none
project: project.json
tools: none
tags: catalog-free, interop, metasurface, rcwa, tabulated-thin-lens, model-validation
license: MIT
---

## What it shows

`project.json` is the step-4 in-domain design: a 532 nm, 30 µm waist source, the fitted `TABULATED_THIN_LENS`
`META` (rows 490–580 nm, field angles ≤ 2°, beam radius ≤ 30 µm, x polarization, error bounds: focus position
± 1.4 µm, spot radius ± 8.1 %, power ± 19 %, with tool versions and the unit-cell library SHA-256 as evidence) and
the plane `after` 0.3 mm behind it. The 100 µm aperture clips 0.4 % of the beam: status WARNING with
`GAUSSIAN_TRUNCATION`. Steps: 1 unit cells (grcwa), 2 focusing, 3 fit with convergence spread, 4 engine check.
Verified run: the engine reproduces the model's thin-lens focus to 4e-16; all test cases stay inside the stated
errors (largest: 0.32 µm, 4.3 %, 15.7 %); 450 nm, 5°, 8°, 20° and an unvalidated metric are OUT_OF_MODEL_SCOPE with
the target INDETERMINATE. Outcome WITHIN_TOLERANCE.

## Run

```bash
os-optics project run project.json --out-dir run
os-optics results targets run
python -m pip install -r requirements-metasurface.txt
python step1_unit_cells.py --out ms
python step1_unit_cells.py --out ms --nG 201 --wavelengths-nm 490 515 532 545 580
python step2_focusing.py ms
python step2_focusing.py ms --nG 201
python step2_focusing.py ms --samples 2048 --wavelengths-nm 490 515 532 545 580
python step3_fit_model.py ms
python step4_engine_check.py ms
```

Steps 1–4 took 24 minutes in the verified run; steps 1 and 2 never import the engine.

## Targets

| Target | Metric | Plane | Bound | Verified |
| --- | --- | --- | --- | --- |
| `spot` | `waist_radius_x_mm` | `after` | ≤ 0.01 mm | 0.001126 mm, MET |

## Baseline

os-optics 0.13.0, no catalog: run status WARNING (`expected.json`). Steps 1–4 verified on Python 3.14.7 with the
pinned versions in `requirements-metasurface.txt`.

## Files

- `project.json`, `expected.json`: the design with the fitted model and its verified outcome.
- `step1_unit_cells.py` … `step4_engine_check.py`: the four steps.
- `requirements-metasurface.txt`: pinned third-party packages (grcwa: GPLv3).
- `README.md`, `README.zh-CN.md`, `images/section.png`: the illustrated page.
