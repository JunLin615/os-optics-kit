---
name: interop-diffraction
title: Diffraction at a declared stop, handed off to external codes
summary: A 532 nm TEM00 beam (1 mm waist, 1 mW) passes a 1.2 mm radius stop and an ideal f = 200 mm lens. The engine's transverse-plane handoff feeds LightPipes, prysm, POPPY and Diffractio, which compute the diffracted focus the paraxial Gaussian result cannot give after truncation.
engine: 0.12.0
catalog: none
project: project.json
tools: none
tags: catalog-free, interop, diffraction, aperture-stop, gaussian-beam, lightpipes, handoff
license: MIT
---

## What it shows

`project.json` is the 1.2 mm stop design: source, `APERTURE_STOP` at 100 mm, `IDEAL_THIN_LENS` 1 mm behind it,
planes `pupil`, `stop_plane` and `focus` (back focal plane). The stop clips 5.6 % of the power: run status WARNING
with `GAUSSIAN_TRUNCATION`. `opensci_handoff.py` runs stop radii 3, 1.2 and 0.5 mm and writes `handoff.json` per
case from `os-optics results handoff` (Gaussian q per axis, power, Jones vector, stop outline, TEM00 field
convention, run identity). `external_focus.py` reads only these files and propagates the field.

| Stop | Engine status | Engine focal peak vs Hankel reference | LightPipes finest grid |
| --- | --- | --- | --- |
| 3 mm | PASS | +0.024 % | 3e-8 |
| 1.2 mm | WARNING | +62 % | −1.0e-4 |
| 0.5 mm | OUT_OF_MODEL_SCOPE | Gaussian withheld | 8.6e-4 |

prysm and POPPY match the reference profile to 4e-7, Diffractio the peak to 3e-5. Outcome WITHIN_TOLERANCE.

## Run

```bash
os-optics project run project.json --out-dir run
os-optics results targets run
python -m pip install -r requirements-diffraction.txt
python opensci_handoff.py --out diff
python external_focus.py diff
```

Diffractio needs numpy < 2: install `requirements-diffractio.txt` in a second environment (Python 3.12) and run
`external_focus.py diff` there.

## Targets

| Target | Metric | Plane | Bound | Verified |
| --- | --- | --- | --- | --- |
| `spot` | `beam_radius_mm` | `focus` | ≤ 0.1 mm | 0.03387 mm, MET |

## Baseline

os-optics 0.12.0, no catalog: run status WARNING (`expected.json`). External side verified on Python 3.14.7
(LightPipes, prysm, POPPY) and 3.12.14 (Diffractio) with the pinned requirements.

## Files

- `project.json`, `expected.json`: the 1.2 mm stop design and its verified outcome.
- `opensci_handoff.py`, `external_focus.py`, `tem00_field.py`, `hankel_reference.py`: both sides and the reference.
- `requirements-diffraction.txt`, `requirements-diffractio.txt`: pinned third-party packages.
- `README.md`, `README.zh-CN.md`, `images/`: the illustrated page.
