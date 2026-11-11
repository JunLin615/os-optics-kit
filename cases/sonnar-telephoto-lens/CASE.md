---
name: sonnar-telephoto-lens
title: Six-element Sonnar at f/2.4, measured across the full entrance pupil
summary: A six-element Sonnar (EFL 85 mm, f/2.4, half field 14 deg) run at its stated aperture rather than in its paraxial core. 4.05 um RMS on axis, 9.29 um at 14 deg, power ledger conserved.
engine: 0.13.0
catalog: none
project: project.json
tools: none
tags: lens-design, imaging, mtf, sonnar, catalog-free
license: MIT
---

## What it shows

A six-element Sonnar, EFL 85 mm at f/2.4 over a half field of 14 deg.
`prescription.csv` is the surface table; the Project imports it as one COMPOUND unit and puts the
image plane at the paraxial rear focus, x = 129.23 mm.

The case measures the lens **at its stated aperture**. The entrance pupil radius is 17.7 mm
(85 / (2 x 2.4)); a 2 mm waist beam fills it when the launch disc is stated as 8.85 beam radii.
Without that statement no aperture clips the beam, the engine reports `PUPIL_NOT_BOUNDED`, and only
the paraxial core (NA 0.03) is analysable. Filled, the measured image-space NA is **0.2085** against
0.208 for f/2.4.

`results imaging` returns 38 of 42 quantities. The four outside their model are the geometric MTF of
the 0° and 14° fields, refused because those wavefronts (0.33 and 0.46 wave) are already
diffraction-limited (`GEOMETRIC_REGIME_NOT_MET`); `README.md` explains why that is a property of the
design, not a run gap.

## Run

```bash
os-optics project check project.json
os-optics project run project.json --out-dir run
os-optics results targets run
os-optics results imaging run --monitor image_plane --request imaging-request.json
```

`imaging-request.json` holds `launch_grid 256`, `psf_half_width_um 250`, `launch_radius_beam_radii
8.8542`.

## Targets

| Target | Metric | Plane | Bound | Verified |
| --- | --- | --- | --- | --- |
| `on_axis_spot` | `spot_rms_mm` | `image_plane` | 0.006 mm, SRC0 | 0.000032 mm, MET |
| `edge_field_spot` | `spot_rms_mm` | `image_plane` | 0.020 mm, SRC2 | 0.001341 mm, MET |

Both bound the arriving beam's ray-footprint RMS; read `results imaging` for the imaging figures.

## Baseline

os-optics 0.13.0, catalog-free: run status `WARNING`, execution outcome `PARTIAL`. The untraced-light
probe stopped on its path budget; it is a separate model from the imaging quantities, which are all
KNOWN, and the power ledger balances (`conserved true`, residual 2.1e-16 W).
`os-optics-kit cases verify sonnar-telephoto-lens` repeats the run and compares it.

## Files

- `project.json`, `expected.json`: the design and its verified outcome.
- `prescription.csv`, `input_prescription.csv`: the surface table and its starting point.
- `imaging-request.json`: settings that make the diffraction quantities reportable.
- `README.md`, `README.zh-CN.md`, `images/`: the illustrated page.
