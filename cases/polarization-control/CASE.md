---
name: polarization-control
title: Polarization control with a half-wave plate and a polarizer
summary: A linearly polarized 1030 nm beam passes a rotatable half-wave plate and a linear polarizer. At 22.5 deg the plate turns the polarization to 45 deg and 0.891 mW of 1 mW reaches the detector. The workflow studies plate angle and wavelength against Jones calculus and Malus' law.
class: free-space
domain: polarization
provenance: self-built
publication: own-work
engine: 0.13.0
catalog: none
project: project.json
tools: edit, sweep, compare, plot, report, sensitivity, tolerance
tags: waveplate, polarizer, parameter-sweep, independent-reference
license: MIT
---

## What it shows

`project.json` is the nominal design of `workflow.py` (`os_optics_kit.optical_designs.polarization_control`,
azimuth target 45°): source, native retarder `HWP` (zero-order, characterized for 1000–1060 nm), plane
`after_hwp`, linear polarizer `POL`, plane `detector`. Angles are measured from design +Y toward +Z. The workflow
runs 13 samples and compares 94 engine values (power, azimuth, Stokes parameters) with Jones calculus and
Malus' law; all agree. At 1064 and 1100 nm the targets read PASS but the run is out of model
(`MODEL_WAVELENGTH_OUTSIDE_BAND`).

## Run

```bash
os-optics project run project.json --out-dir run
os-optics results targets run
python workflow.py --out wf
os-optics-kit edit project.json --edits studies/polarization_threshold.json --out strict.json --run strict-run
os-optics-kit sweep studies/polarization_hwp_wavelength_sweep.json --base strict.json --out sweep
os-optics-kit sensitivity studies/sensitivity_polarization.json --out sensitivity
os-optics-kit tolerance studies/tolerance_polarization.json --out tolerance
```

The threshold edit raises the power minimum to 0.85 mW (still PASS); the 18-sample sweep of plate turn and
wavelength gives 3 PASS, 6 FAIL and 9 out of model (every 1064 nm sample).

## Targets

| Target | Metric | Plane | Bound | Verified |
| --- | --- | --- | --- | --- |
| `transmitted_power` | `power_W` | `detector` | ≥ 0.8 mW | 0.891 mW, MET |
| `output_azimuth` | `polarization_azimuth_deg` | `after_hwp` | 45° ± 0.5° | 45°, MET |

## Baseline

os-optics 0.13.0, no catalog: run status PASS (`expected.json`).
`os-optics-kit cases verify polarization-control` repeats the run and compares it.

## Files

- `project.json`, `expected.json`: the nominal design and its verified outcome.
- `workflow.py`: the parameterized workflow (`--list-parameters`, `--study NAME=V1,V2`, `--no-studies`).
- `studies/`: threshold edit, two-parameter sweep, sensitivity and tolerance specs.
- `README.md`, `README.zh-CN.md`: the illustrated pages; `images.json` records `images/iso.png`.
