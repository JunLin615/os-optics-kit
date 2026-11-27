---
name: field
description: Field curvature and distortion of a saved os-optics run per field point and wavelength, from the engine's published ray data, source poses and aperture stop: image height, distortion against EFL tan(theta) on one plane, tangential and sagittal focus, astigmatism, and each field without a row.
version: 0.1.0
command: os-optics-kit field
requires: os-optics>=0.14,<0.15
kind: tool
---

# field

## When to use

- A run with several field points exists and you need distortion and field curvature at an image plane.

## When not to use

- For ray-fan curves of one field: use `fans`. For MTF: `mtf`; PSF: `os-optics results imaging`.

## Inputs

- `RUN`: a saved run (`project.json`, `--ray-data full`); never modified, nothing is traced. It needs an on-axis
  field (chief ray on the plane origin, within 1e-3 mm), sources aimed at the stop opening's centre, no reflection
  before the plane.
- `--monitor PLANE`: the image plane.
- `--efl MM`: the EFL for the distortion reference (> 0); the plane is then taken as its focal plane. Default: the
  engine's paraxial EFL and rear focal plane (`os-optics project paraxial`) of the elements on the on-axis field's
  line, per wavelength.
- `--stop-tolerance-mm MM`: chief-ray distance allowed from the stop opening's centre (default 0.01).
- `--out FILE`: write the JSON there.

## Outputs

- `references` per wavelength: `efl_mm`, `basis`, `distortion_plane`, `rear_focal_point_mm`,
  `paraxial_focus_from_plane_mm` (positive behind the plane; null with `--efl`).
- `fields` rows: `path_id`, `wavelength_nm`, `field_angle_deg`, `image_height_mm` (at the plane), `chief_ray`
  (`stop.md`), `distortion_height_mm`, `reference_height_mm`, `distortion_pct`,
  `tangential_focus_mm`, `sagittal_focus_mm`, `astigmatism_mm`; a `*_reason` beside each null.
- `unreached_fields`: each declared source or path without a row (`source_id`, `path_id`, `termination`, `reason`,
  `message`): `NO_COUNTED_RAYS_AT_MONITOR`, `NO_RAYS_AT_MONITOR`, a `fans` refusal code, or `NO_PATH_IN_RUN`.

Exit 0, or 4 with a JSON error (`BAD_ARGUMENT`, `EFL_NOT_STATED`, `AXIS_FIELD_MISSING`, `SOURCE_POSE_MISSING`,
`PROJECT_UNREADABLE`, `RUN_FILE_UNREADABLE`, `REFLECTED_PATH_UNSUPPORTED`, `NO_FANS`, `ENGINE_RESULTS_FAILED`).

## Conventions

- Chief ray: a source's beam-centre ray, `WITHIN_STOP_TOLERANCE` of the stop opening's centre, else null
  distortion and focus with the reason (`stop.md`). `NO_APERTURE_STOP`: aim sources at the entrance-pupil centre.
- Field angle: between a source's emission axis and that of the on-axis source.
- Distortion: (h - EFL tan(theta)) / (EFL tan(theta)), in percent, both on one plane. Default: h where the chief
  ray, continued in a straight line, meets the paraxial rear focal plane, from the axis; the plane's defocus does not
  enter. With `--efl`: h on the plane.
- Focus: along the plane normal from the plane, positive behind it, from the innermost pupil zone of each fan.

## Minimal example

```
os-optics project run project.json --out-dir run
os-optics-kit field run --monitor image_plane
```

## Limits

- Geometric optics; one plane crossing. Focus comes from the innermost sampled zone, not a paraxial trace.
- Assumes no image rotation and a rotationally symmetric reference (h = EFL tan(theta)).
- Reflected paths (any mirror, internal reflection) are unsupported, even with `--efl`:
  refused with `REFLECTED_PATH_UNSUPPORTED` (`stop.md`).
- The default EFL needs the elements on one straight axis; else state `--efl`.

## Version

0.1.0 (os-optics-kit 0.5.0), for os-optics 0.14.
