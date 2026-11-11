---
name: aspheric-lens
title: Even aspheres carry a five-element f/3.5 lens to 25 degree half field
summary: Five elements at f/3.5 reach 25 deg half field with even aspheres on four surfaces; a spherical control reaches only 20 deg. The engine reports a scalar diffraction MTF at every field and wavelength.
engine: 0.13.0
catalog: none
project: project.json
tools: summary
tags: aspheric, refractive-imaging, wide-field, comparison
license: MIT
---

## What it shows

This five-element f/3.5 lens reaches 25° half field with an on-axis Strehl ratio of 0.77–0.999.
The engine reports `in_model_scope: false` (`GAUSSIAN_ABERRATION_DOMINATED`): the pupil rays spread
4.65 times wider than an aberration-free Gaussian beam, which is what an imaging system does — the
engine's Gaussian model is the wrong yardstick here, and every diffraction quantity it reports is
KNOWN (110 of 126). The 16 it declines are the `geometric_mtf_*` pairs of eight fields
(`GEOMETRIC_REGIME_NOT_MET`: below 1 wave of RMS, diffraction sets the MTF).

The case's value is the comparison. A control with identical elements but all radii spherical was
optimised over the field it can carry: past 20° its rays stop reaching the image plane at all. At
10° the control's Strehl ratio is 0.023–0.139; the aspheric version at 12.5° holds 0.254–0.483.
The surfaces that carry the difference are S1, S3, S9 and S10, each with a conic and one even term.

## Run

```bash
os-optics project run project.json --out-dir run
os-optics results targets run
os-optics results imaging run --monitor image_plane --request imaging-request.json
```

## Targets

Nine `spot_rms_mm` targets on `image_plane`, bounded at 0.05 mm, one per field and wavelength
(486 / 588 / 656 nm). Six are MET (on axis 1.00–5.42 µm, 12.5° 9.80–11.08 µm); the three 25° ones are UNMET at 61.93–86.22 µm. The run status FAIL comes from the model scope above, not from these
targets: all nine beams reach the plane and no beam is partly intercepted.

## Baseline

The spherical control, held to the same stop, source, budget and imaging request, reports 114/126
KNOWN and a maximum Strehl ratio of 0.139 at its off-axis field; the aspheric version reports 110/126
KNOWN and 0.483 at a larger field. To get there the control pushes S4 to R=443 mm and S10 to
R=−794 mm, nearly plane; the aspheric version stays at ordinary curvatures. Both land 9/9 sources on
the image plane with 14 quantities each.

## Files

- `prescription.csv` - the optimised aspheric surface table
- `input_prescription.csv` - the input prescription
- `project.json` - the Project the results came from
- `expected.json` - status and every target assessment
- `studies/` - both surface tables, the request, every engine number
- `images/` - section, MTF, Strehl and spot comparisons
