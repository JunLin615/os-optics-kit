---
name: aspheric-lens
title: Even aspheres carry a five-element f/3.5 lens to 25 degree half field
summary: Five elements at f/3.5 reach 25 deg half field with even aspheres on four surfaces; a spherical control reaches only 20 deg. The engine reports a scalar diffraction MTF at every field and wavelength.
class: lens
domain: imaging, diffraction
provenance: self-built
publication: own-work
engine: 0.13.0
catalog: none
project: project.json
tools: summary
tags: aspheric, wide-field, design-comparison, mtf
license: MIT
---

## What it shows

The five-element f/3.5 lens reaches 25° half field with an on-axis Strehl ratio of 0.77–0.999.
The engine reports `in_model_scope: false` (`GAUSSIAN_ABERRATION_DOMINATED`): pupil rays spread
4.65 times wider than an aberration-free Gaussian beam, so the Gaussian model is the wrong yardstick;
every diffraction quantity is KNOWN (110 of 126). The 16 declined are the `geometric_mtf_*` pairs of
eight fields (`GEOMETRIC_REGIME_NOT_MET`: below 1 wave RMS, diffraction sets the MTF).

The value is the comparison. A control with identical elements but all radii spherical, optimised
over the field it can carry, loses the image plane past 20°. At 10° its Strehl ratio is
0.023–0.139; the aspheric version at 12.5° holds 0.254–0.483. S1, S3, S9 and S10 carry the
difference, each with a conic and one even term.

## Run

```bash
os-optics project run project.json --out-dir run
os-optics results targets run
os-optics results imaging run --monitor image_plane --request imaging-request.json
```

## Targets

Nine `spot_rms_mm` targets on `image_plane`, bounded at 0.05 mm, one per field and wavelength
(486 / 588 / 656 nm). Six are MET (on axis 1.00–5.42 µm, 12.5° 9.80–11.08 µm); the three 25° ones
are UNMET at 61.93–86.22 µm. Status FAIL comes from the model scope, not the targets: all nine
beams reach the plane, none partly intercepted.

## Baseline

The spherical control, held to the same stop, source, budget and imaging request, reports 114/126
KNOWN and a maximum Strehl ratio of 0.139 off axis; the aspheric version reports 110/126 KNOWN and
0.483 at a larger field. The control pushes S4 to R=443 mm and S10 to R=−794 mm, nearly plane.
Both land 9/9 sources on the image plane with 14 quantities each.

## Files

- `prescription.csv`, `input_prescription.csv`: optimised aspheric and input surface tables
- `project.json`, `expected.json`, `imaging-request.json`: Project, verdict, imaging request
- `studies/`: aspheric and spherical-control tables; `data/engine-aspheric.json`: every engine number
- `README.md`, `README.zh-CN.md`; `images/` recorded in `images.json` (iso, section, MTF, Strehl, spot)
- `figures.py`: `python figures.py RUN --out images` draws the figures
