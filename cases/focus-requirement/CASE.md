---
name: focus-requirement
title: A focusing requirement verified at a fixed working plane
summary: Focus a 780 nm Gaussian beam to a 1/e² intensity radius at most 20 µm at a fixed plane 50 mm beyond the nominal lens position. A before/after comparison and refined placement sweep turn the requirement into a bounded tolerance answer and report.
engine: 0.13.0
catalog: none
project: project.json
tools: summary, edit, sweep, compare, report, present
tags: catalog-free, gaussian-beam, focusing, requirements, placement-tolerance
license: MIT
---

## What it shows

Requirement sheet: 780 nm, M²=1, source waist radius 1 mm at x=−10 mm, ideal f=50 mm lens nominally at x=0,
fixed working plane at x=50 mm. The requested radius is at this physical plane. The source and detector stay
fixed during lens-placement variations. A +1 mm placement error supplies the unmet starting example.

## Run

```bash
os-optics-kit cases verify focus-requirement
python workflow.py all --out output
python workflow.py present --out output --dest figures-zh --language zh-CN --background dark
```

Install `os-optics-kit[plot]`. Separate commands: `compute`, `study`, `report`, `present`.
`report` and `present` read saved results. A smaller placement-refinement step is `study --out output --resolution-mm 0.005`
after `compute` in a new output directory.

## Targets

`spot_radius`: native `beam_radius_mm` ≤0.020 mm at `focus`; Gaussian 1/e² intensity radius.
The placement interval is derived from this unchanged target. Coarse sweep ±1 mm in 0.2 mm steps;
both transitions refined to brackets at most 0.01 mm wide by default. Each boundary includes a failing sample.

## Baseline

Engine 0.13.0, no catalog: PASS, `spot_radius` MET, radius 12.414 µm.
Every sampled radius is checked against an independent complex-q calculation.
`expected.json` verifies the nominal native result; `verification.json` in the output records the actual interval,
boundary brackets, resolution, reference error and identities.

## Files

`project.json`, `expected.json`: requirement and native baseline. `workflow.py`: shared compute/study/redraw steps.
`data/snapshot.json`: numerical baseline. Bilingual READMEs and `images/` explain the workflow.
Generated outputs include saved runs, coarse/refined studies, comparison, reports, offline gallery and ZIP bundle.
