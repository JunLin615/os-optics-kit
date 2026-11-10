---
name: bounded-aberration-correction
title: Intensity-only correction with five bounded phase modes
summary: Feed a Gaussian relay handoff into a scalar pupil model. Five bounded phase controls improve on-axis focal intensity while an uncontrolled spherical mode sets the residual.
engine: 0.13.0
catalog: none
project: project.json
tools: present
tags: catalog-free, aberration, optimization, handoff
license: MIT
---

## What it shows

A 532 nm relay supplies a 1.300313 mm Gaussian radius. An external 1.5 mm pupil,
curvature compensation and ideal 120 mm Fourier lens produce the focal field.
Powell search sees only normalized on-axis intensity; five controls cannot remove
the sixth, spherical mode. Every trial is saved.

## Run

```bash
python -m pip install -r requirements-aberration.txt
python workflow.py --out out/default
python workflow.py --out out/stronger --spherical-waves 0.1
python workflow.py --out out/default --redraw-only
python present_case.py out/default out/stronger --out talk/en --bundle talk/aberration.zip
```

Output directories must be new. `--bound-waves` sets the symmetric control limit
(default 0.6 waves); `--spherical-waves` sets the uncontrolled coefficient (default
0.055). Read `metrics.json` and `preview.png`.

## Targets

Engine target `relay_radius`: 1.29–1.31 mm at `modulator`, MET at 1.300313 mm.
The external principal observable is on-axis intensity divided by the ideal
on-axis intensity at equal transmitted power, not a peak-search Strehl ratio.
Default: 0.092393 → 0.895105. With 0.1 waves spherical: 0.106409 → 0.692747.
Transmitted power is 18.60305 mW in every phase state.

## Baseline

os-optics 0.13.0, catalog-free; external workflow Python 3.12+ (verified 3.14.8), NumPy 2.5.4, SciPy 1.18.1,
Matplotlib 3.11.2. Default runtime about 3 s. ABCD q error <6e-15;
Parseval error <7e-16. Independent polar quadrature differs by <0.00073
for the two documented runs. Sensor is noiseless; scalar paraxial optics,
continuous phase controls, fixed compensation of nominal curvature.

Presentation uses kit 0.3.0. Add `--language zh-CN`, `--background dark`, or
`--size 1920x1080` with a new `--out`. The case helper redraws saved external PSFs
and ratios; native `present build` handles the relay radius target. No solver runs.
Open `talk/en/index.html`; see bilingual README for fonts, camera and handoff.

## Files

- `project.json`, `expected.json`: relay and engine target.
- `workflow.py`, `requirements-aberration.txt`: external calculation and checks.
- `README.md`, `README.zh-CN.md`: parameters, interpretation and saved data.
- Generated `engine/`, `handoff.json`, `config.json`, `science.npz`, `metrics.json`,
  `preview.png`: engine evidence, reusable science and preview.
