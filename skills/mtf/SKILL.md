---
name: mtf
description: Tangential and sagittal optical MTF of an os-optics imaging analysis per spectral line, with the diffraction limit and geometric MTF, as a CSV, a JSON document and one figure per line; declined curves keep their reason and are never drawn.
version: 0.1.0
command: os-optics-kit mtf
requires: os-optics>=0.14,<0.15; os-optics-kit[plot] for figures
kind: tool
---

# mtf

## When to use

- A saved run of an imaging Project: the MTF curves per field and wavelength at an image plane.
- A saved `os-optics results imaging` JSON (or a previous `imaging.json`): table and figures, no Engine analysis.

## When not to use

- Optical MTF only: no pixel aperture, sampling or SFR. Measured SFR: `sfr`; design-side SFR is
  JunLin615/os-optics#42.
- A pass/fail verdict or an MTF target: none here; MTF constraints belong to `optimize`.
- Turned field points from a Project: `field-points`. PSF maps, encircled energy: `os-optics results imaging`.

## Inputs

- `RUN --monitor PLANE --out DIR`: runs one imaging analysis in-process and keeps it as `DIR/imaging.json`.
  `--request FILE`: an imaging request (`os-optics schema imaging-request`); set `fields` and
  `frequencies_cyc_per_mm`, else each line gets its own frequency axis and a field the plane's aperture clips
  is left out (listed as `OUTSIDE_ACTIVE_AREA`). A configurations run needs `--configuration ID`.
- `--imaging FILE --out DIR [--run RUN]`: no analysis; `--run` checks the document belongs to that run.
- `--no-figures` (no matplotlib needed), `--theme presentation|report|FILE`, `--set language=zh-CN`,
  `--overwrite`.

## Outputs

- `mtf.csv`: `field` (`SRC#0`), `source_id`, `wavelength_nm`, `direction`, `frequency_cyc_per_mm`, then `mtf`,
  `diffraction_limited_mtf`, `geometric_mtf` each with a `*_status`, `unit` (`1`). Empty value: its status says
  why (`NOT_CONVERGED` keeps the value; `BEYOND_NYQUIST`, `OUT_OF_MODEL_SCOPE`, `UNKNOWN`, `ABSENT`, ...).
  Python: `os_optics_kit.mtf.read_csv`.
- `mtf.json` (`os-optics-kit.mtf` v1): run and analysis identity, request, conventions, per line the curves
  (state, reason, model basis, convergence), image point, PSF Nyquist; `not_analysed` with reasons.
- `mtf-<source>-<index>.png` per line: T and S MTF, dashed diffraction limits, missing curves named.
- Stdout: status and per line `tangential_status`, `sagittal_status`. Exit 0 `COMPLETED`; 2 `INCOMPLETE`
  (a curve unconverged or declined, or a field not analysed; files written); 4 refused (`MONITOR_NOT_FOUND`,
  `CONFIGURATION_REQUIRED`, `IMAGING_RUN_MISMATCH`, `ANALYSIS_REFUSED`, `IMAGING_INVALID`, `OUTPUT_EXISTS`).

## Minimal example

```
os-optics project run project.json --out-dir run
os-optics-kit mtf run --monitor image_plane --request imaging-request.json --out mtf
os-optics-kit mtf --imaging mtf/imaging.json --run run --out mtf-zh --set language=zh-CN
```

## Limits

- One curve per spectral line; no polychromatic or weighted MTF. MTF(0) is the PSF window's light share, not 1.
- The MTF lies on the plane normal to the line's image-space axis through its reference point (`CENTROID` by
  default); on axis "tangential" is the plane's u axis.
- Fast lenses need a dense `launch_grid` (else `PUPIL_UNDERSAMPLED`); read `converged` before trusting a curve.

## Version

0.1.0 (unreleased, after os-optics-kit 0.5.0): first version, verified with os-optics 0.14.0.
