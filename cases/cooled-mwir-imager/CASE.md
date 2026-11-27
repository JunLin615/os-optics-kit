---
name: cooled-mwir-imager
title: A cooled 3-5 um imager with the cold stop as its aperture stop
summary: A four-element F/2 imager for 3-5 um, EFL 100 mm, half field 1.5 deg, whose aperture stop is the cold stop of a cooled detector in image space (CSH 13 mm).
class: lens
domain: imaging
provenance: self-built
publication: own-work
engine: 0.14.0
catalog: none
project: project.json
tools: summary
tags: mid-infrared, aperture-stop, detector
license: MIT
---

## What it shows

A four-element imager for the 3-5 um band (Si, ZnSe, CaF2, Sapphire), F/2,
EFL 100 mm, half field 1.5 deg, at 3/4/5 um.

The point is **where the aperture stop sits**. In a cooled infrared camera the
stop is the cold shield opening of the detector: a 77 K plate in *image* space,
`CSH` in front of the focal plane. Everything the detector sees through it is
scene; everything outside it is cold metal.

That placement has two consequences a visible-band layout never meets.

**The focal ratio follows from the stop and its distance.** With `CSH = 13 mm`,
the on-axis cone subtends `atan(D / (2 CSH))`, so the smallest stop that passes
it is `D = CSH / F# = 6.5 mm` at F/2. The entrance pupil, `D = EFL / F# = 50 mm`,
is the image of that stop through the front group, so the front elements carry a
25 mm beam while the stop itself stays small.

**A stop at that minimum vignettes every off-axis field.** The beam walks across
the stop as the field angle grows; at 1.5 deg the marginal ray lands outside the
axial footprint. This design uses 9.0 mm, measured from the traced pupil rays
rather than assumed. The focal ratio stays 2.0 because the entrance pupil sets
it, not the stop's own diameter.

A 1 mm silicon detector window sits between the stop and the focal plane.

## Run

```bash
os-optics project run project.json --out-dir run
os-optics results targets run
```

## Targets

Every target is `spot_rms_mm` on `focal_plane`, bounded at 0.08 mm, one per
field and wavelength. All nine are MET, 0.0034-0.0261 mm.

## Baseline

EFL 99.07 mm (target 100), F/2 by the 50 mm entrance pupil, half field 1.5 deg,
cold stop 9.0 mm at CSH 13 mm.

The status is `OUT_OF_MODEL_SCOPE`: a stop necessarily clips the pupil of an
off-axis field, and the engine follows each beam along its chief ray, so it can
report that part of a beam is cut but not the power of the surviving part. The
spot values are geometric and remain valid.

`BULK_LOSS_NOT_ESTABLISHED` for all four materials: the catalog's 3-5 um entries
carry dispersion and a validity band but no measured bulk absorption there, so
transmitted power is an upper bound.

## Files

- `prescription.csv`: the surface table
- `project.json`, `expected.json`: the Project and the verified outcome
- `README.md`, `README.zh-CN.md`: the illustrated pages
