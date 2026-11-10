---
name: phase-only-flat-top
title: Circular flat-top shaping and input-width sensitivity
summary: Design a phase-only mask for a circular flat top, then change the Gaussian input width while keeping the mask fixed. Measure core intensity variation and target-region power.
engine: 0.13.0
catalog: none
project: project.json
tools: none
tags: catalog-free, beam-shaping, phase-mask, fourier-optics, interop
license: MIT
---

## What it shows

A 532 nm, 10 mW beam feeds a phase-only Fourier design. A circular super-Gaussian target gives core intensity CV 0.01197 and target-region power fraction 0.99485. Reducing the source waist by 10% with the same mask raises CV to 0.07450. The engine supplies the Gaussian illumination; the case computes the modulated field externally with NumPy/SciPy.

## Run

Use Python 3.12+ with os-optics 0.13.0 and os-optics-kit 0.2.0. In a copied case directory:

```bash
python -m pip install -r requirements-flat-top.txt
python workflow.py --out out/flat-top
python workflow.py --out out/flat-top --redraw
```

`--width-scale 1.1` tests a wider input; `--radius-mm 0.45` redesigns the target. Use a fresh output directory for each computation. Default compute time is about 7 seconds on the recorded Windows/Python 3.14 environment.

## Targets

Native target: illumination 1/e² radius 0.749–0.752 mm. External observables: CV = standard deviation / mean for r < 0.315 mm; power fraction where the target exceeds 10% of its peak. Both are dimensionless. Default external checks require CV < 0.02 and fraction > 0.99.

## Baseline

Catalog-free. Scalar, coherent, paraxial model; continuous lossless phase and ideal f = 100 mm Fourier lens. Smooth energy transport initializes 251 Gerchberg–Saxton updates minimizing target-intensity error. Independent checks use complex-q propagation, direct Fourier quadrature, power conservation and a half-pitch evaluation of the fixed mask.

## Files

`project.json` and `expected.json` describe native illumination. `workflow.py` saves native runs, handoffs, `science.npz`, `configuration.json`, `results.json` and `preview.png` under the chosen output directory. The READMEs define the data and optical boundary. `images/layout.png` shows the native reference system.
