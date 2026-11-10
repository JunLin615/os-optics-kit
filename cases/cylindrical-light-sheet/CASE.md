---
name: cylindrical-light-sheet
title: Cylindrical light-sheet thickness and range
summary: A two-surface cylindrical lens focuses a 488 nm Gaussian beam along y. Compare 50 and 40 mm focal lengths to measure the tradeoff between waist FWHM and the propagation range within sqrt(2) of that thickness.
engine: 0.13.0
catalog: none
project: project.json
tools: summary
tags: catalog-free, light-sheet, cylindrical-lens, gaussian-beam
license: MIT
---

## What it shows

A constant-index n=1.52, 2 mm thick cylindrical lens creates a thin y waist while x stays wide. At f=50 mm the waist FWHM is 9.14456 um and the full sqrt(2)-thickness range is 0.776659 mm. At f=40 mm these become 7.31569 um and 0.497068 mm.

## Run

Run from this copied case directory:

```bash
python -m pip install -r requirements-light-sheet.txt
os-optics project run project.json --out-dir ../sheet-native
python workflow.py --out ../sheet-default
python workflow.py --focal-length-mm 40 --out ../sheet-f40
```

## Targets

Native `thin-waist`: waist_radius_y_mm <= 0.008493218, equivalent to FWHM <=10 um. Native `sheet-range`: rayleigh_range_y_mm >=0.225, equivalent to full range >=0.45 mm. Both apply at `after` and are MET by both examples. External profiles use native q and power, with independent thick-lens ABCD and power checks.

## Baseline

Engine 0.13.0; no catalog. Source 488 nm, 10 mW, 1 mm waist. Cylinder entrance radius 26 mm, plane exit, 10 mm clear diameter, 0.995 power transmission per surface. Paraxial separable Gaussian model, M²=1.

## Files

`project.json` is the normalized default. `workflow.py` builds the prescribed lens through the public native interface and saves its configuration, handoff, targets, scientific profiles and preview under `--out`. `--plot-only` redraws saved profiles. See both READMEs for units, equations and parameter limits.
