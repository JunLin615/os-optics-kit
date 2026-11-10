---
name: render
description: Render reproducible 3D figures with camera angles, roll, element framing and a manifest.
version: 0.1.0
command: os-optics-kit render
requires: os-optics>=0.13,<0.14
kind: tool
---

# render

## When to use

- Near-side views, three-quarter views, element close-ups or transparent figures.

## When not to use

- Standard views: use `os-optics view RUN --snapshot DIR --views iso,top,front,side`.
- True-scale sections: use `os-optics view RUN --snapshot DIR --section ALL`.

## Inputs

```text
os-optics-kit render RUN --out DIR [--camera AZ,EL[,ROLL]]...
  [--shots FILE] [--name NAME] [--focus optics|scene|element:ID]
  [--size WxH] [--background studio|transparent|#rrggbb]
  [--axis X,Y,Z] [--up-axis X,Y,Z] [--labels] [--edges] [--beams]
  [--browser PATH] [--timeout-s 20] [--html-only] [--overwrite]
```

PNG capture needs Chrome/Chromium and `python -m pip install "os-optics-kit[render]"`.
`--html-only` writes pages without browser verification or PNGs.

`AZ` is the angle from the optical axis to the target-to-eye direction: 0 axial, 90 side-on.
`EL` is elevation towards world up, projected perpendicular to the optical axis. The positive
side is up cross axis. Use `0 <= AZ <= 180`, `|EL| <= min(AZ,180-AZ)`, and `|EL| < 90`.
At zero roll, screen up points towards projected up. `ROLL` rotates it about the target-to-eye
direction by the right-hand rule. Use finite degrees. If the optical axis is parallel to
the default +Z up, choose a nonparallel `--up-axis`.

`--focus optics` is the default. Use `element:ID` to frame one element.
`--shots` accepts a JSON list of `{name, azimuth_deg, elevation_deg, roll_deg}`; elevation and roll
default to zero. `--name` requires one `--camera` and no `--shots`. Names start with a letter or digit,
use only letters, digits, `_` or `-`, and have at most 64 characters; reserved filenames are refused.
Names are unique ignoring case. Existing outputs require `--overwrite`; all shots are checked first.

## Outputs

Compact JSON on stdout and `shots.json` in `--out`; each shot has HTML and, on successful capture,
a PNG. Shot status is `captured`, `html_only` or `failed`. `requested_camera` holds the intended
direction, unrolled up and roll. `applied_camera` holds the measured position, target, direction,
screen up and matrix; it is null unless capture succeeds. `direction` and `up` likewise describe
the applied camera. `box_min`, `box_max` and `image_px` record framing and size.

Exit 0: written. Exit 1: capture failed (`FAIL`, with per-shot errors on stdout).
Exit 2: unresolved axis/focus (`CAMERA_UNDEFINED`, `FOCUS_UNKNOWN`, `FRAMING_EMPTY`).
Exit 4: refused input or missing dependency. Exit 2/4 errors are JSON on stderr, with empty stdout.
For `OUTPUT_EXISTS`, choose another directory or explicitly use `--overwrite`. For `FOCUS_UNKNOWN`,
choose an ID from `available`. For `CAPTURE_TIMEOUT`, open the HTML to inspect it or raise `--timeout-s`.

## Minimal example

```sh
os-optics-kit render RUN --out figures --camera 55,22 --camera 86,8 --background transparent
os-optics-kit render RUN --out closeup --camera 55,22,45 --focus element:LENS --labels
```

## Limits

Uses the os-optics viewer's camera hook. An unavailable hook reports `PAGE_UNEXPECTED`;
a failed camera application reports `CAMERA_APPLY_FAILED`. Failed captures have no PNG recorded.
With `--overwrite`, an old PNG remains untouched if its replacement capture fails; check `shots.json`.

## Version

0.1.0 (os-optics-kit 0.2.0): first version, verified with os-optics 0.13.0.
