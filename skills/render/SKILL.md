---
name: render
description: Render the Optical Simulation View at an arbitrary camera pose (angle to the axis, elevation, roll), framed on the optics or one element, with a manifest. The frames are the engine's shading; only the camera is this tool's.
version: 0.1.0
command: os-optics-kit render
requires: os-optics>=0.12,<0.13
kind: tool
---

# render

## When to use

- An angle the nine `view` presets do not give: side-on (86°) to show element thickness, three-quarter
  (55°/22°) to read the stack, near-axial to show aperture and stop.
- A reproducible angle: the same `--camera 86,8` gives the same pose on any machine, and `shots.json`
  records the direction, up, framing box and pixel size used.
- A close-up on one element (`--focus element:<id>`), a transparent background for compositing, or a
  batch of named poses from `--shots shots.json`.

## When not to use

- A standard view is enough: `os-optics view RUN --snapshot DIR --views iso,top,front,side` is faster,
  needs no browser, and is the engine's own output.
- A 2D drawing: `os-optics view RUN --snapshot DIR --section ALL` gives the true-scale section.
- Captions, contact sheets, page layout: compose afterwards; this command writes frames, not layouts.

## Inputs

```text
os-optics-kit render RUN --out DIR [--camera AZ,EL[,ROLL]]... [--shots FILE] [--focus optics|scene|element:ID]
                                   [--size WxH] [--background studio|transparent|#rrggbb]
                                   [--edges] [--labels] [--beams] [--browser PATH] [--html-only]
```

- `AZ` is the angle between the eye and the optical axis (0 down the axis, 90 exactly side-on); `EL`
  lifts the eye towards the world up axis; `ROLL` turns the image about the view axis.
- `--focus optics` (default) frames every element but the sources, detectors and observation planes.
- `--html-only` writes the camera-placed pages and no image (no browser needed).

## Outputs

One JSON document on stdout: `status`, `shots[]` (with `direction`, `up`, `box_min`, `box_max`,
`image_px`, `png`), `failed`; in `--out`: `<name>.png`, `<name>.html`, `shots.json`.

Exit codes: 0 written, 1 a capture failed, 2 camera or framing unresolved (CAMERA_UNDEFINED,
FOCUS_UNKNOWN, FRAMING_EMPTY, SIZE_INVALID), 4 usage error / engine missing / no browser.

## Minimal example

```sh
os-optics-kit render RUN --out shots --camera 86,8 --camera 55,22 --background transparent
os-optics-kit render RUN --out shots --focus element:M1 --shots poses.json
```

## Limits

- Capture needs a Chrome or Chromium; without one, `--html-only` still writes the pages.
- The camera is applied through the viewer page's global `window.__opensciViewer.frame`; geometry,
  materials and shading stay the engine's. If a future engine renames the global the command reports
  PAGE_UNEXPECTED instead of guessing.

## Version

0.1.0 (os-optics-kit 0.1.0): first version.
