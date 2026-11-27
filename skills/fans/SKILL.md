---
name: fans
description: Ray fans of a saved os-optics run: transverse and longitudinal aberration and per-ray OPD across the pupil, per field point and wavelength, from the engine's published ray data, with the engine's spot and OPD numbers beside them.
version: 0.1.0
command: os-optics-kit fans
requires: os-optics>=0.14,<0.15
kind: tool
---

# fans

## When to use

- You need ray fans of a run at an image plane: transverse and longitudinal aberration or OPD versus
  pupil coordinate, per field and wavelength.
- To check them against the engine's spot radius and wavefront RMS.

## When not to use

- For MTF: `mtf`; PSF, encircled energy: `os-optics results imaging`.
- For the wavefront of one path only: use `os-optics results wavefront`.
- To change the design: use `edit`.

## Inputs

- `RUN`: a run saved with `--ray-data full` (the default); never modified, nothing traced.
- `--monitor PLANE`: the image plane.
- `--path ID` (repeatable): only these paths (default: all reaching the plane).
- `--zones N` (N >= 1): keep N rings per fan, the outermost always.
- `--imaging FILE`: `results imaging` of this run, its request with `"crossing_index": 0`.
- `--out FILE`: write the JSON there.

## Outputs

JSON. Per path: `path_id`, `source_id`, `wavelength_nm`, `chief_uv_mm`, `tangential_axis_uv`,
`sagittal_axis_uv`, `pupil_frame`, `rho_max`, `fan_rho_max`, `rings`, `vignetted_rays`, `zones`;
`fans.tangential` / `fans.sagittal` rows (`rho`, `zone`, `sampled`, `fit_residual_um`, `transverse_um`,
`longitudinal_mm`, `opd_waves`); `spot_rms_um`, `opd_rms_waves`; `engine` (OPD RMS/PV, wavefront
status and reason; spot only for the same run, plane, line, route and crossing: `spot_rms_comparison`
says `COMPARED` or why not). `imaging`: the run check.
`unreached`: paths with no counted rays at the plane, with termination; `refused`: paths without
fans, with code and reason. Exit 0, or 4 with JSON on stderr
(`BAD_ARGUMENT`, `PATH_NOT_FOUND`, `NO_FANS`, `FAN_AXIS_NOT_SAMPLED`, `PUPIL_FRAME_UNRESOLVED`,
`IMAGING_INVALID`, `ENGINE_RESULTS_FAILED`, `OUTPUT_NOT_WRITABLE`, ...).

## Conventions

- rho: launch offset in units of the source's 1/e^2 radii; vignetting does not change it.
- Pupil frame: the source's x, y carried to the plane's (u, v), oriented by the engine's first-order map.
  Tangential: from the plane origin towards the chief ray's image point (+v there); sagittal: tangential
  turned by -90 deg.
- Rays on a fan azimuth are used as they are; elsewhere each complete ring is Fourier-fitted in azimuth.
- Transverse: displacement from the chief ray along the fan, um. Longitudinal: where a fan ray crosses
  the chief ray, along the normal in the light's direction (positive = behind the plane); null when parallel.
- OPD: the engine's per-ray OPD (reference plane through the chief ray, piston removed), in waves.

## Minimal example

```
os-optics project run project.json --out-dir run
os-optics results imaging run --monitor image_plane --request imaging-request.json > imaging.json
os-optics-kit fans run --monitor image_plane --imaging imaging.json --out fans.json
```

## Limits

- Geometric optics only; no diffraction.
- `spot_rms_um` uses the saved pupil rays; the engine's spot re-traces a denser grid.
- The first arrival at the plane only (crossing index 0).
- Paths reflected before the plane are refused (`PUPIL_FRAME_UNRESOLVED`).
- A ring with missing rays gives only its rays on a fan azimuth (`fan_rho_max` per fan); a fan without
  points refuses the path (`FAN_AXIS_NOT_SAMPLED`).

## Version

0.1.0 (os-optics-kit 0.5.0), for os-optics 0.14.
