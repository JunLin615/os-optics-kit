---
name: edit
description: List a Project's objects with editable values, units and frames, then apply edit operations by object ID with explicit units, checked by the engine and written atomically to a new file (optionally run). Use it to change a design reproducibly.
version: 0.1.0
command: os-optics-kit edit
requires: os-optics>=0.12,<0.13
kind: tool
---

# edit

## When to use

- Find object IDs, current values, units, frames and supported operations: `--describe [--ids A,B]`.
- Move, rotate or re-parameterize components, sources, observation planes or targets by ID.
- Retry safely: a repeated identical edit reports `ALREADY_APPLIED` and writes nothing.

## When not to use

- Many values of one parameter: use `sweep`.
- Reading results only: use `summary`.

## Inputs

- `BASE.json`: a Project (never modified).
- `--edits EDITS.json`: a list of operations (or `{"ops": [...]}`): `set_component_pose`,
  `set_source_model`, `set_observation_pose`, `set_target`, `set_component_product`, add/remove ops,
  and `transform` (`selector`, `translation_mm`, `rotation`, `frame` `"design"` or `{"component": ID}`,
  `pivot`). Numbers are in the field's unit (name suffix `_mm`, `_nm`, `_w`, `_deg`) or
  `{"value": x, "unit": "um"}`; unknown or incompatible units are refused.
- `--out NEW.json`; optional `--run RUN_DIR`, `--changes FILE`, `--overwrite`, `--expect-base-hash H`.

## Outputs

`NEW.json` (normalized, engine-checked) and `NEW.changes.json` (operations as given and normalized,
before/after values, base and output hashes). Stdout: one JSON document; status `EDITED`,
`ALREADY_APPLIED` or `DESCRIBED`, with `--run` the run's outcome.
Exit 0 edited / described; with `--run` 1 FAIL, 2 UNKNOWN, 3 out of model scope; 4 refused edit or
invalid input (JSON error on stderr with the operation index and code).

## Minimal example

`edits.json`:

```
[{"op": "transform", "selector": {"ids": ["L1"]}, "frame": {"component": "L1"},
  "translation_mm": [0, 0, {"value": 500, "unit": "um"}]}]
```

```
os-optics project example --kind focus > project.json
os-optics-kit edit project.json --describe --ids L1
os-optics-kit edit project.json --edits edits.json --out moved.json --run run-moved
```

The moved lens misses the `focus_position` target: status FAIL, exit 1.

## Limits

- Every operation applies to the unchanged base; an edit the engine refuses writes nothing.
- Workspace frames (`{"group": ID}`) need a workspace; Project files have none (`FRAME_INVALID`).
- `--describe` bands are advisory; the engine run decides validity.

## Version

0.1.0 (os-optics-kit 0.1.0), verified with os-optics 0.12.0 and catalog release 2026-10-28.1.
