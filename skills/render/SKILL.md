---
name: render
description: Render presentation-grade 3D figures with camera angles, roll, element framing and a manifest. Needs Chrome or Chromium; case images and plain system views come from the engine without a browser.
version: 0.2.0
command: os-optics-kit render
requires: os-optics>=0.13,<0.14; os-optics-kit[render]
kind: tool
---

# render

## When to use

- Presentation-grade 3D figures: a set of camera poses with roll, element close-ups and a `shots.json` manifest.

## When not to use

- Case images and system views: the engine needs no browser and no `render` extra.
  `os-optics view RUN --no-browser --snapshot DIR --views iso` (also `--camera AZ,EL`, `--focus element:ID`,
  `--background transparent`); `--section ALL` for a lens section; in a kit checkout `os-optics-kit cases images NAME`.

## Inputs

```text
os-optics-kit render RUN --out DIR [--camera AZ,EL[,ROLL]]...
  [--shots FILE] [--name NAME] [--focus optics|scene|element:ID]
  [--size WxH] [--background studio|transparent|#rrggbb]
  [--axis X,Y,Z] [--up-axis X,Y,Z] [--labels] [--edges] [--beams]
  [--browser PATH] [--timeout-s 20] [--html-only] [--overwrite]
```

PNG capture needs Chrome or Chromium and `python -m pip install "os-optics-kit[render]"`.
`--html-only` writes pages without browser verification or PNGs.

`AZ` is the angle from the optical axis to the target-to-eye direction: 0 axial, 90 side-on.
`EL` is elevation towards world up, projected perpendicular to the axis; positive is up cross axis.
Use `0 <= AZ <= 180`, `|EL| <= min(AZ,180-AZ)`, `|EL| < 90`, finite degrees. At zero roll, screen up points
towards projected up; `ROLL` turns it about the target-to-eye direction (right-hand rule). If the axis
is parallel to +Z, set `--up-axis`.

`--focus optics` is the default; `element:ID` frames one element. `--shots` is a JSON list of
`{name, azimuth_deg, elevation_deg, roll_deg}` (elevation, roll default 0). `--name` needs one `--camera`
and no `--shots`. Names: a letter or digit, then letters, digits, `_`, `-`, at most 64 characters, unique ignoring case; reserved
filenames are refused. Existing outputs require `--overwrite`; all shots are checked first.

## Outputs

Compact JSON on stdout and `shots.json` in `--out`; each shot has HTML and, on capture, a PNG.
Shot status is `captured`, `html_only` or `failed`. `requested_camera`: intended direction, unrolled up,
roll. `applied_camera`: measured position, target, direction, screen up and matrix (null unless captured).
`box_min`, `box_max` and `image_px` record framing and size.

Exit 0: written. 1: capture failed (`FAIL`, per-shot errors on stdout). 2: unresolved axis or focus
(`CAMERA_UNDEFINED`, `FOCUS_UNKNOWN`, `FRAMING_EMPTY`). 4: refused input or missing dependency. Exit 2/4
errors are JSON on stderr. `OUTPUT_EXISTS`: another directory or `--overwrite`. `FOCUS_UNKNOWN`: an ID from
`available`. `CAPTURE_TIMEOUT`: open the HTML or raise `--timeout-s`.

## Minimal example

```sh
os-optics-kit render RUN --out figures --camera 55,22 --camera 86,8 --background transparent
os-optics-kit render RUN --out closeup --camera 55,22,45 --focus element:LENS --labels
```

## Limits

Uses the os-optics viewer's camera hook: a missing hook reports `PAGE_UNEXPECTED`, a failed camera
`CAMERA_APPLY_FAILED`. Failed captures record no PNG; with `--overwrite` the old PNG stays.

## Version

0.2.0 (os-optics-kit 0.3.0): routes case images and system views to the engine.
0.1.0 (os-optics-kit 0.2.0): first version, verified with os-optics 0.13.0.
