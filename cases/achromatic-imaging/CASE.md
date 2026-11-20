---
name: achromatic-imaging
title: Three-colour imaging with a declared achromatic objective
summary: A 75 mm cemented N-BK7/N-F2 objective images an on-axis distant point through a 6 mm pupil at three wavelengths. Native PSF/MTF and encircled energy at one common image plane accompany chromatic focus, sensitivity and seeded alignment studies.
class: lens
domain: imaging, dispersion, diffraction
provenance: self-built
publication: own-work
engine: 0.13.0
catalog: none
project: project.json
tools: summary, compare, sensitivity, tolerance, report, present
tags: achromat, mtf, sensitivity, tolerance
license: MIT
---

## What it shows

Native `lens.achromat` and published Sellmeier data give a reproducible three-surface prescription.
Native imaging analyses a uniformly illuminated finite pupil; all three wavelengths share x=76.478991 mm.
Best-focus diagnostics keep their own plane coordinates. Study perturbations keep the prescription and
fixed detector except the labelled image-plane sensitivity.

## Run

```bash
os-optics-kit cases verify achromatic-imaging
python workflow.py all --out output
python workflow.py present --out output --dest figures-zh --language zh-CN --background dark
```

Install `os-optics-kit[plot,render]`: `all` ends with `present`, an optional presentation gallery that needs
Chrome/Chromium. Stages: `compute`, `study`, `report`, `present`; the last two read saved results.
Variation: `compute --out shifted --image-shift-mm 0.05`.

## Targets

Native `pupil_spot_F/d/C`: `spot_rms_mm` ≤0.005 mm, on Gaussian equal-power pupil rings.
Native `centroid_u/v`: d-line pupil centroid within ±0.05 mm.
Separate derived uniform-pupil checks: RMS ≤2 µm, 50% energy radius ≤5 µm, MTF at 10/20/40 cycles/mm
≥0.85/0.75/0.55, best-focus span ≤0.06 mm, d-line EFL 75 mm ±1%.

## Baseline

Engine 0.13.0, no catalog; PASS, five native targets MET. Uniform-pupil RMS radii 0.869/0.421/0.614 µm;
geometric best-focus span 0.04462 mm. The +0.15 mm defocused comparison keeps unmet derived checks.
Sensitivity uses four stated perturbation scales; tolerance uses 16 draws, seed 20261111.

## Files

`project.json`, `expected.json`: baseline. `data/`: prescription and numerical snapshot.
`analysis.py`: native calculations and derived checks; `workflow.py`: bounded studies; `figures.py`: gallery
imaging panels from saved data. `studies/`: declared perturbations. `README.md`, `README.zh-CN.md`: the
illustrated pages; `images.json` records the system views in `images/`.
