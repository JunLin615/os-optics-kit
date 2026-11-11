---
name: mirror-alignment
title: Beam position and pointing after a fold mirror
summary: A 633 nm beam folds 90 deg at a flat mirror onto planes 100 mm and 400 mm away; positions must stay within 0.1 mm and pointing within 0.5 mrad. The workflow studies mirror tilt and source offset against exact reflection geometry and the 2-theta law.
class: free-space
domain: beam-steering
provenance: self-built
publication: own-work
engine: 0.13.0
catalog: none
project: project.json
tools: measurements, edit, sweep, compare, plot, report, sensitivity, tolerance
tags: fold-mirror, alignment, parameter-sweep, measurements, independent-reference
license: MIT
---

## What it shows

`project.json` is the nominal design of `workflow.py` (`os_optics_kit.optical_designs.alignment`): source 100 mm
before mirror `M1` (45° incidence), planes `near` and `far` on the reflected axis. Plane axes: u = design +Z ×
reflected direction, v = design +Z. A mirror tilt θ about local Y moves the beam by 2θ along u; about local X by
2θ·cos 45° along v. The workflow runs 12 samples and compares 122 engine values with exact vector reflection and the
small-angle law; all agree. At 0.25 mrad only the far position misses; at 60 mrad the beam misses the far plane
(`OBSERVATION_NOT_REACHED`). `--synthetic-measurements` fits SYNTHETIC-labelled positions on five planes with
`os-optics-kit measurements` (1 mrad tilt: 1.997 ± 0.018 mrad pointing vs 2 mrad).

## Run

```bash
os-optics project run project.json --out-dir run
os-optics results targets run
python workflow.py --out wf
python workflow.py --out wf-synthetic --no-studies --mirror-tilt-local-y-mrad 1 --synthetic-measurements
os-optics-kit sensitivity studies/sensitivity_alignment.json --out sensitivity
os-optics-kit tolerance studies/tolerance_alignment.json --out tolerance
```

The tolerance study passes 64 of 100 seeded samples.

## Targets

| Target | Metric | Plane | Bound | Verified |
| --- | --- | --- | --- | --- |
| `near_position_u`, `near_position_v` | `position_u_mm`, `position_v_mm` | `near` | ± 0.1 mm | 0 mm, MET |
| `far_position_u`, `far_position_v` | `position_u_mm`, `position_v_mm` | `far` | ± 0.1 mm | 0 mm, MET |
| `far_pointing_u`, `far_pointing_v` | `direction_cosine_u`, `direction_cosine_v` | `far` | ± sin 0.5 mrad | 0, MET |

## Baseline

os-optics 0.13.0, no catalog: run status PASS (`expected.json`).
`os-optics-kit cases verify mirror-alignment` repeats the run and compares it.

## Files

- `project.json`, `expected.json`: the nominal design and its verified outcome.
- `workflow.py`: the parameterized workflow with the synthetic-measurement variant.
- `studies/`: sensitivity and tolerance specs.
- `README.md`, `README.zh-CN.md`: the illustrated pages; `images.json` records `images/iso.png` and `images/top.png`.
