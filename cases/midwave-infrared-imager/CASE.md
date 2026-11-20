---
name: midwave-infrared-imager
title: A five-element 3-5 um imager with manufacturable element thicknesses
summary: Five-element F/2 imager for 3-5 um, EFL 100 mm, half field 3 deg, every element held to a 2-12 mm centre and 1.5-3 mm edge. The engine reports a scalar diffraction MTF at every field and wavelength.
class: lens
domain: imaging, diffraction
provenance: self-built
publication: own-work
engine: 0.13.0
catalog: none
project: project.json
tools: summary
tags: mid-infrared, manufacturability, mtf
license: MIT
---

## What it shows

A five-element imager for the 3-5 µm band (Si, ZnSe, CaF2, Si, ZnSe), F/2, EFL 100 mm, half field
3°, at 3/4/5 µm. Every element keeps a 2-12 mm centre and a 1.5-3 mm edge thickness.

All nine beams reach the image plane under the scalar diffraction model. The request launches a
disc one beam radius wide, so the evaluated cone is the stop's near-axis core:
`image_space_na` is 0.060, not the 0.25 of the full F/2 cone. Inside the model's scalar-sine limit
of 0.2, the diffraction MTF, Strehl ratio and encircled energy are reported. The engine marks the
geometric MTF of every field out of scope (`GEOMETRIC_REGIME_NOT_MET`): below 1 wave RMS,
diffraction sets the MTF.

The 4 and 5 µm images are near diffraction-limited; 3 µm is the residual the thickness constraint
left.

## Run

```bash
os-optics project run project.json --out-dir run
os-optics results targets run
os-optics results imaging run --monitor image_plane --request imaging-request.json
```

## Targets

Every target is `spot_rms_mm` on `image_plane`, bounded at 0.05 mm, one per field and wavelength
(the metric is single-beam, so the source is named). All nine are MET, 0.0085-0.0436 mm. The status
is WARNING because the engine's stray-light search runs out of paths; the main beams are traced in
full and `in_model_scope` is true.

## Baseline

EFL 99.9878 mm (target 100), F/2 by the 25 mm stop semi-diameter, half field 3°.
Strehl 0.908-0.982 at 4 and 5 µm, 0.315-0.514 at 3 µm. On axis at 4 µm the MTF is 0.561 at
10.3 cycles/mm against a limit of 0.571.
The run leaves 89.5 % of the input in `UNTRACED_REFLECTION`: each uncoated face loses its Fresnel
reflection (Si at n=3.43 reflects 0.30 per face) instead of that branch being followed, while the
imaging quantities are weighted by the amplitude those faces transmit.

## Files

- `prescription.csv`, `input_prescription.csv`: optimised and input surface tables
- `project.json`, `expected.json`, `imaging-request.json`: Project, verdict, imaging request
- `data/engine-midwave.json`: every number above, from the engine
- `README.md`, `README.zh-CN.md`; `images/` recorded in `images.json` (iso, section, MTF, spot,
  wavefront with Strehl)
- `figures.py`: `python figures.py RUN --out images` draws the figures
