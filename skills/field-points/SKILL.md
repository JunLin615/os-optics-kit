---
name: field-points
description: Field points of a native Project at stated angles, object heights or real image heights: turned, stop-aimed sources; chief-ray hit point, height and angle; first-order comparison; optical MTF per line at a fixed plane with sampled field minima.
version: 0.1.0
command: os-optics-kit field-points
requires: os-optics>=0.14,<0.15
kind: tool
---

# field-points

## When to use

- A Project has one on-axis source and you need its field points: image height, chief-ray angle (CRA), the
  first-order comparison (f tan(theta), m h) or edge MTF at a fixed image plane.
- To verify a lens record's stated field, or to build a run that `field`, `fans` and `mtf` read.

## When not to use

- A run that already holds field sources: use `field` (distortion, curvature) or `mtf`.
- Moving the plane or the design per field: use configurations (`zoom`), not field points.

## Inputs

- `PROJECT` (never modified), `--plane PLANE`, `--out DIR` (new or empty; `--overwrite`).
- One kind: `--angles-deg`, `--object-heights-mm` (finite object only) or `--image-heights-mm` (solved).
- `--azimuth-deg`: field direction in the plane's (u, v), from +u toward +v.
- `--source SRC` (several sources), `--configuration ID` (a Project with configurations).
- `--wavelengths-nm` (default: the source's lines), `--chief-ray-wavelength-nm`.
- `--expect-field-kind KIND`: refuse another kind (state the record's `field.kind`).
- `--mtf-frequencies F,..` with `--launch-grid`, `--imaging-request FILE`, `--no-convergence`.
- `--pivot-mm X,Y,Z`: no aperture stop: turn about this point (`NOT_AIMED`).
- `--write-run`: also `DIR/run` (a full run: minutes on a large lens).

## Outputs

- `field-points.json` (`os-optics-kit.field-points` v1): `input` (`sha256`, `sha256_lf`, `project_hash`,
  `configuration_id`), `evaluated` (file, hashes, `derivation`), `plane` (declared and evaluated aperture,
  `widened`), `conditions` (kind, requested order, conjugate, aim, lines, imaging request, first-order reference).
- `points[]`: `status` `EVALUATED` | `NOT_EVALUATED` + `reason`; `field.angle_deg`, `field.object_height_mm`;
  `chief_ray` (`image_point_uv_mm`, `image_height_mm` from the on-axis hit, `angle_deg` to the plane normal,
  `stop_offset_mm`, `aim`, `within_declared_aperture`); `first_order` (`FIRST_ORDER_COMPARISON`); `spot`; `lines`;
  `mtf[]` per line x direction x frequency: `value`, `status`, `value_kind`, `reason`, `converged`.
- `minima[]` per frequency: `per_line` and `all_lines`, `CONFIRMED` only when every sample is `EXACT`, else
  `UNCONFIRMED` with `bound: UPPER` and `missing[]`; `limiting_sample`.
- `evaluated-project.json`, `imaging.json` (`mtf --imaging` draws it), `run/` with `--write-run`.
- Exit 0 `COMPLETED`, 2 `INCOMPLETE`, 4 refused: `FIELD_KIND_MIXED`, `FIELD_KIND_MISMATCH`, `OBJECT_AT_INFINITY`,
  `CONJUGATE_UNDETERMINED`, `NO_APERTURE_STOP`, `SOURCE_REQUIRED`, `CONFIGURATION_REQUIRED`, `PLANE_NOT_FOUND`.

## Minimal example

```
os-optics-kit field-points project.json --plane image_plane --out fp --angles-deg 0,5,10 --mtf-frequencies 20,40
```

## Limits

- Centred imaging systems, one plane crossing; the chief ray is aimed at the stop centre (`aim_tolerance_mm`).
- MTF is per spectral line; `per_line_minimum` is not a weighted multispectral MTF.
- Fast or wide lenses need a larger `--launch-grid` (else `PUPIL_UNDERSAMPLED`).
- The four ways a hand-made field point goes wrong: `traps.md`. Lens records: `lens.md`.

## Version

0.1.0 (unreleased, after os-optics-kit 0.5.0): first version, verified with os-optics 0.14.0.
