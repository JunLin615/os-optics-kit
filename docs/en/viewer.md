# Local live viewer

[os-optics-kit](../../README.md) · English · [简体中文](../zh-CN/viewer.md)

`os-optics-kit view` opens a lightweight, read-only local web page in your default browser so you can watch an optical
design change in real time while you or a coding agent edit a native Project JSON or write new saved runs.

It runs strictly on loopback (`127.0.0.1`), needs only the core `os-optics-kit` install (`os-optics>=0.14,<0.15`), and
requires **no** `[render]` or `[plot]` extras, **no** Playwright/Chromium download, and **no** cloud account.

## Quick start

```bash
python -m pip install os-optics os-optics-kit

# Watch a saved run directory (3D rays, power ledger, execution status and targets)
os-optics-kit view run

# Watch a native Project file (geometry preview only until a run is attached)
os-optics-kit view project.json

# Watch a Project together with its saved run (tracks freshness automatically)
os-optics-kit view project.json --run run

# Evaluate a Project once into a temporary directory without modifying the input file
os-optics-kit view project.json --evaluate
```

Useful options:

- `--port PORT` / `--host 127.0.0.1` — bind to a specific loopback port (default: OS-assigned free port; non-loopback hosts are refused).
- `--project PROJECT.json` — when opening a run directory, also track freshness against a Project file.
- `--configuration ID` — select one configuration inside a multi-configuration run directory.
- `--poll-interval SECONDS` — file-watch polling interval (default `0.4`).
- `--no-open` — start the server and print its compact JSON ready record without launching a browser.
- `--once` — print one snapshot envelope JSON to stdout and exit.

Press `Ctrl-C` to stop the server cleanly.

## What the page shows

- **Interactive 3D optical path & traced rays** — reuses the engine's local 3D viewer directly (offline, zero CDN), distinguishes traced branches, drawable rays and recorded plane-crossing rows (working cleanly on `--ray-data summary` runs and runs without observation planes), and pairs the view with branch color chips showing power and termination.
- **Honest unevaluated geometry** — Project-only views resolve catalog and nominal dimensions through the engine's layout API without running ray tracing; parts lacking resolved dimensions are explicitly tagged as schematic placeholders.
- **Power ledger** — total input power, power residual, conservation check, stacked channel proportion bar, exact channel values/units (`W`, `mW`, `µW`) and per-element breakdown.
- **Execution, integrity & freshness** — separates execution completion (`COMPLETED` vs `PARTIAL` with exhausted budgets), model scope, manifest hash integrity and freshness (`CURRENT` vs `HISTORICAL`).
- **Target assessments & observation planes** — displays `MET`, `UNMET`, `INDETERMINATE` and `NOT_APPLICABLE` assessments alongside measured values, declared bounds and bounded/untraced power intervals on partial runs.
- **Bilingual UI (`EN` / `中文`)** — switch display language directly in the top bar without restarting.

## Live-follow and safety guarantees

- **Read-only by design**: the viewer never edits your Project JSON or run directory, and refuses mutating HTTP methods (`POST`/`PUT`/`DELETE`/`PATCH` return `405`).
- **One atomic snapshot**: geometry, traced rays, power ledger, execution status and targets are bound to the same `snapshot_id` both on disk and in the browser (via `/engine-view/<snapshot_id>/` and atomic scene-panel swap). The server never returns bytes from snapshot B for a request targeting A (`409 SNAPSHOT_SUPERSEDED` if evicted), and the viewer never publishes a run that fails manifest integrity verification. The engine's viewer code under `/engine-assets/` is the same for every snapshot and is served independently of the snapshot cache; a scene that was evicted, or a frame that failed to start, is abandoned and the page moves on to the newest valid snapshot without a refresh, keeping the previous verified frame meanwhile.
- **One frame for plane coordinates**: `quantities[].plane` reports `origin_mm`, `u_axis`, `v_axis` and `normal` in the design frame after the selected configuration, where the 3D scene draws the plane, in Project-only, matching-run and historical views alike. A plane attached to a device keeps `pose_frame: DEVICE_LOCAL` and `device_id`; the pose as the Project writes it is in `declared_pose`.
- **Geometry preview is never a computed result**: opening a Project without a run shows nominal component/plane geometry only, with no rays and an explicit uncomputed state. `--evaluate` runs once into a temporary directory and never auto-reruns on subsequent edits.
- **No stale misattribution**: when an attached Project changes relative to its run, freshness flips to `HISTORICAL`, old numbers and rays are withheld, and the updated geometry is shown until a matching run is written.
- **Graceful recovery from partial writes**: if an agent is mid-write (truncated JSON or half-copied run), the viewer enters a waiting state while holding the last valid snapshot, then updates automatically as soon as valid files settle.
- **The camera stays where you put it**: a new revision (a Project edit, a new run, traced rays giving way to geometry only and back) is drawn through the camera you were looking through, whether you moved it or left the first framing. The view buttons, Fit and Reset reframe on request. A geometry-only view of a historical run starts on that run's view directions.

## What an agent can read

The page states what it shows twice, and both agree with `GET /api/snapshot` (the `snapshot` of its envelope):

- **Attributes of `#app`** for the snapshot on screen: `data-revision`, `data-snapshot-id`, `data-status` (`ok`, `waiting`, `error`), `data-result-mode` (`computed_run`, `stale_run`, `geometry_preview`), `data-computed`, `data-freshness` (`CURRENT`, `HISTORICAL`, `UNDETERMINED`, `RUN_ONLY`, or empty when not checked), `data-project-hash`, `data-execution-outcome`, `data-requirement-outcome`, `data-power-available`, `data-power-conserved` (`true`, `false`, or empty when the ledger is withheld), `data-branch-count`, `data-geometry-only` and `data-drawable-ray-count`. Each is the value of the same field of the snapshot, empty where that field is null.
- **The visible text**: a badge that does not apply is hidden **and empty** (`aria-hidden="true"`), so the page's text never holds the verdict of an earlier snapshot. On a stale run, for example, there is no "Power conserved" anywhere in the DOM.

The active 3D frame (`iframe.engine-frame.active`) carries the same `data-snapshot-id`, and `data-camera` says whether it took over the previous frame's camera (`carried`) or starts on the engine's framing (`default`).

## Checking a release in a browser

`python scripts/released_viewer_check.py OUT_DIR --engine-version 0.14.0 --kit-version 0.5.0` (in a checkout of this repository) installs the published wheels into a fresh environment outside the checkout, checks their SHA-256 against PyPI, and follows a saved run, a Project edit and a re-run in one page of a local Chrome, comparing the page, `/api/snapshot` and the engine's `results` commands at each step. `OUT_DIR` receives a JSON record of every check and a screenshot per revision; `--kit-source .` checks a build of the checkout instead. The `Released viewer check` workflow runs it on Linux and Windows (`workflow_dispatch`).

## Relation to other commands

- Use `os-optics-kit view` for **live local inspection** in your ordinary browser.
- Use `os-optics view RUN --no-browser --snapshot DIR --views iso` for browser-free static system views.
- Use `os-optics-kit render` or `os-optics-kit present` for presentation-grade figures, galleries and slide bundles.
