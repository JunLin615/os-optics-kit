---
name: sfr
description: Measure the slanted-edge SFR (ISO 12233 e-SFR) of an image region, with edge angle, fit residuals, SFR50 and SFR at Nyquist; optionally put a saved os-optics imaging MTF on the same axes with the input differences stated. No engine run needed.
version: 0.1.0
command: os-optics-kit sfr
requires: os-optics-kit[plot] for PNG/TIFF and plots
kind: tool
---

# sfr

## When to use

- A camera or module image of a slanted edge: system SFR curve, `sfr50`, `sfr_at_nyquist`, edge angle.
- Measured SFR beside the design: `--design` with saved `os-optics results imaging RUN --monitor PLANE` JSON,
  or a `frequency,value` CSV.

## When not to use

- Design-side SFR (optics x pixel x sampling): the engine's optical MTF has no pixel term; this tool never adds one.
- Several edges, a curved target or an ROI without one straight edge.

## Inputs

- `IMAGE`: PNG/TIFF (8/16-bit grey or RGB), `.npy` or `.csv` 2-D array; `--out DIR` (new or empty).
- `--roi X,Y,W,H` around one edge, about 5 deg from vertical or horizontal (orientation detected).
- Linearization `--oecf linear|gamma|srgb|table` (`--gamma G`, `--oecf-table CSV`, `--full-scale V`);
  `--channel luminance|R|G|B`; `--fit-order 2` for a curved edge.
- `--pixel-pitch-um P` adds cycles/mm and is required for a cy/mm design; `--at`, `--at-mm` frequencies.
- Design: `--design FILE`, `--design-line SRC#0`, `--design-direction`, `--design-curve`, `--design-unit` (CSV).

## Outputs

`sfr.csv`, `esf.csv` (bin counts, filled bins), `lsf.csv`, `edge.csv` (per-row centroid, fit, residual),
`summary.json` (provenance: input sha256, ROI, settings, method), `sfr.png`; with `--design` also
`comparison.csv` (common cy/px grid), `comparison.json` (input differences, scalars per curve), `comparison.png`.
Stdout: status, edge, sfr scalars, ESF bins, clipping, issues. Exit 0 PASS / WARNING, 2 UNKNOWN (too many empty
ESF bins), 4 refused input (`ROI_INVALID`, `EDGE_NOT_FOUND`, `PIXEL_PITCH_REQUIRED`, `OUTPUT_EXISTS`, ...).

## Minimal example

```
os-optics-kit sfr edge.png --roi 400,300,80,120 --pixel-pitch-um 3.45 --out sfr-run
os-optics results imaging run --monitor image_plane > imaging.json
os-optics-kit sfr edge.png --roi 400,300,80,120 --pixel-pitch-um 3.45 --design imaging.json --design-line SRC0#0 --out sfr-cmp
```

## Limits

- SFR is a magnitude in cycles/pixel along the edge normal; the design MTF is signed optics only, compared as
  |MTF| with negative frequencies listed. Read the differences in `comparison.json`, never one as the other.
- Image processing in the image (demosaic, sharpening) stays in the measured SFR and is not declared.
- Read `issues`: shallow or steep angle, clipping, few rows, low contrast, empty bins, large fit residual.
- PNG/TIFF and plots need Pillow and matplotlib; `.npy`/`.csv` with `--no-plot` need neither.

## Version

0.1.0 (os-optics-kit 0.4.0): slanted-edge e-SFR and one design comparison, verified with Python 3.11 and numpy 2.4.
