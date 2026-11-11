---
name: ideal-lens-focus
title: Gaussian focus of an ideal thin lens
summary: A 532 nm Gaussian beam with a 1 mm waist radius is focused by an ideal thin lens (f = 100 mm); the 1/e^2 radius in the back focal plane must stay below 50 um. Catalog-free: the lens is the engine's native ideal thin lens.
class: lens
domain: gaussian-beam
provenance: self-built
publication: own-work
engine: 0.13.0
catalog: none
project: project.json
tools: summary
tags: ideal-lens, focusing, python-api
license: MIT
---

## What it shows

The smallest complete Project: one Gaussian source, one native ideal thin lens (`lens.ideal_thin`, 25.4 mm clear
aperture), an observation plane in the back focal plane and one target. The focused radius equals the Gaussian
law w' = λf / (πw) = 16.93 µm. `ideal_lens_focus.py` runs the same design through the engine's low-level request API
(`os_optics.ValidationRequest`) and writes the request, result, rays and an arrivals CSV.

## Run

```bash
os-optics project check project.json
os-optics project run project.json --out-dir run
os-optics results targets run
os-optics-kit summary run
python ideal_lens_focus.py --out run-api
python ideal_lens_focus.py --out run-strict --maximum-radius-mm 0.001
```

The last command tightens the bound to 1 µm: status FAIL, exit code 1.

## Targets

| Target | Metric | Plane | Bound | Verified |
| --- | --- | --- | --- | --- |
| `spot_radius` | `beam_radius_mm` | `focus` | ≤ 0.05 mm | 0.01693 mm, MET |

## Baseline

os-optics 0.13.0, no catalog: run status PASS, every target MET (`expected.json`).
`os-optics-kit cases verify ideal-lens-focus` repeats the run and compares it.

## Files

- `project.json`: the Project with its target.
- `expected.json`: the verified status and target assessments.
- `ideal_lens_focus.py`: the same design through the low-level request API.
- `README.md`, `README.zh-CN.md`: the illustrated pages; `images.json` records `images/iso.png` and
  `images/section.png` (`os-optics-kit cases images ideal-lens-focus`).
