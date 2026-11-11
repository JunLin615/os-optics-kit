---
name: expansion-focus
title: Beam expansion and focusing workflow
summary: A Keplerian expander (ideal thin lenses f = 50 mm and 150 mm) and a 100 mm focusing lens take a 780 nm, 0.5 mm waist beam to a 1.5 mm expanded radius and a focus below 20 um. The workflow studies gap error, focal length, wavelength and waist one at a time against a complex-q reference.
class: free-space
domain: gaussian-beam
provenance: self-built
publication: own-work
engine: 0.13.0
catalog: none
project: project.json
tools: sweep, compare, plot, report, sensitivity, tolerance
tags: beam-expander, focusing, parameter-sweep, sensitivity, tolerance, independent-reference
license: MIT
---

## What it shows

`project.json` is the nominal design of `workflow.py` (`os_optics_kit.optical_designs.expander_focus`, default
inputs): source, L1 at 0 mm, L2 at f1 + f2, L3 100 mm after L2, planes `collimated` and `focus`. `workflow.py`
rebuilds the design from any inputs, runs it and 21 one-parameter samples, checks every engine value against
complex-q Gaussian propagation (63 of 63 agree), plots the targets and writes a report. Unmet and out-of-model
samples stay: 1300 nm is outside the lens band (`MODEL_WAVELENGTH_OUTSIDE_BAND`), a 3 mm waist warns
`GAUSSIAN_TRUNCATION`, 4 mm is out of model.

## Run

```bash
os-optics project run project.json --out-dir run
os-optics results targets run
python workflow.py --out wf
python workflow.py --out wf-strict --no-studies --max-spot-radius-mm 10um
os-optics-kit sensitivity studies/sensitivity_expander.json --out sensitivity
os-optics-kit tolerance studies/tolerance_expander.json --out tolerance
os-optics project update project.json --ops studies/compute_budget_limited.json --out limited.json
```

`wf/report/report.md` links every study. The tolerance study passes 92 of 100 seeded samples; the one-step
compute budget gives a PARTIAL run with every target INDETERMINATE.

## Targets

| Target | Metric | Plane | Bound | Verified |
| --- | --- | --- | --- | --- |
| `expanded_radius` | `beam_radius_mm` | `collimated` | 1.5 mm ± 2 % | 1.50112 mm, MET |
| `spot_radius` | `beam_radius_mm` | `focus` | ≤ 0.02 mm | 0.016552 mm, MET |
| `waist_location` | `waist_distance_x_mm` | `focus` | −0.5 … 0.5 mm | −0.03649 mm, MET |

## Baseline

os-optics 0.13.0, no catalog: run status PASS (`expected.json`).
`os-optics-kit cases verify expansion-focus` repeats the run and compares it.

## Files

- `project.json`, `expected.json`: the nominal design and its outcome.
- `workflow.py`: the parameterized workflow (`--list-parameters`, `--study NAME=V1,V2`, `--no-studies`).
- `studies/`: sensitivity, band-edge sensitivity, tolerance and compute-budget specs.
- `README.md`, `README.zh-CN.md`: the illustrated pages; `images.json` records the system views in `images/`.
