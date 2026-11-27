# optimize: the spec

A minimal runnable spec (catalog-free; `os-optics project example --kind focus > project.json`): move
the lens L1 along its axis for the smallest spot while the waist stays within 0.1 mm of the plane.

```
{"schema": "os-optics-kit.optimize-spec", "schema_version": 1,
 "description": "Refocus L1 for the smallest spot with the waist on the plane",
 "project": "project.json",
 "variables": [{"name": "L1_axial", "unit": "mm", "bounds": [-2, 2], "scale": 0.1,
   "ops": [{"op": "transform", "selector": {"ids": ["L1"]}, "frame": {"component": "L1"},
            "translation_mm": [0, 0, "$value"]}]}],
 "objectives": [{"name": "spot", "source": {"target": "spot_radius"}, "scale": 0.01}],
 "constraints": [{"name": "waist", "source": {"target": "focus_position"},
                  "minimum": -0.1, "maximum": 0.1, "scale": 0.1}],
 "solver": {"method": "trf", "max_evaluations": 60}}
```

Rules worth knowing before writing one:

- `"$value"` is the variable's value in `unit` (absolute for `set_*` ops, a move for `transform`).
  `{"$value": {"offset": o, "scale": s}}` writes `o + s * value`: one variable can drive a field
  with an offset (a separation from a fixed lens) or several linked fields.
- Omitted `nominal`: the base value of the field the ops write (`NOMINAL_AMBIGUOUS` when two fields
  disagree), 0 for `transform`. The search starts there; it must lie inside `bounds`.
- `scale` is the variable's step unit: the solver works in scaled variables, and the finite-difference
  step is `diff_step` (default 1e-3) scales. Choose scales near the change that matters.
- Objectives: residual `sqrt(weight) * (value - goal) / scale`. Constraints: `margin` = scaled
  distance to the nearest limit, positive inside. Objective and constraint names are unique together.
- `set_optical_model_value {model_id, path, value}`: `path` is a JSON pointer into the model, e.g.
  `/surfaces/1/radius_mm`, `/surfaces/0/conic`, `/surfaces/0/aspheric_coefficients/0`; list objects
  with `os-optics-kit edit P.json --describe --ids ID`.
- `paraxial` quantities: `efl_mm`, `bfl_mm`, `ffd_mm`, `front_principal_plane_mm`,
  `rear_principal_plane_mm`. `solids` quantities: `min_thickness_mm`, `edge_thickness_mm`,
  `centre_thickness_mm`, `clear_aperture_min_thickness_mm` (smallest over the selected gaps).
- Spot RMS counts arriving pupil rays only: a search can shrink a spot by vignetting it. Bound the
  loss with a `{"rays": {"target": ID}}` constraint (arrived / launched, e.g. `minimum` at the start's
  share), and set `ray_loss: "report"` when the start already loses rays (the default `infeasible`
  rejects any loss).
- MTF constraints use the spec's `imaging` request (default `launch_grid` 24 undersamples fast
  lenses: give the case's request). When every MTF constraint names `source_id` / `wavelength_nm`,
  only those fields and lines are analysed (same values, less work).
- `solver`: `trf` minimizes the residuals' sum of squares (needs `aggregation: sum_squares`);
  `slsqp` minimizes the objective with every margin >= 0. `max_evaluations` counts every Engine
  evaluation, the start and finite-difference probes included; repeats are cached.
- `verify.ops` apply to the best Project before a second evaluation, e.g. a full
  `set_compute_budget` block with `pupil_ring_count` doubled; `verify.imaging` overrides `imaging`.

Every unknown key, wrong type, MTF objective or `trf` with `sum_abs` / `max_abs` is refused before
any evaluation: `SPEC_INVALID` with `path`, exit 4.
