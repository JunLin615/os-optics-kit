# Optimization

[os-optics-kit](../../README.md) · English · [简体中文](../zh-CN/optimize.md)

`os-optics-kit optimize` refines the continuous parameters of one Project within bounds. A JSON spec names the
variables (edit ops with a `"$value"`), the quantities to drive toward goals and the limits a candidate must hold.
A bounded local solver from SciPy (trust-region reflective least squares, or SLSQP) chooses the candidates; the
engine evaluates each one on a single `os_optics.local.EvaluationSession` through the
[evaluation adapter](evaluation-adapter.md). The command writes every evaluation to a history, the best feasible
Project, and a result that states why the search stopped, which limits hold the design back and which field is worst.

## Run

The worked example is the beam expander of [interop-optimization](../../cases/interop-optimization): move L2 to
collimate the 532 nm beam.

```bash
os-optics-kit cases copy interop-optimization --dest .
cd interop-optimization
os-optics-kit optimize studies/optimize-expander.json --out opt
os-optics-kit optimize studies/optimize-expander.json --out opt-slsqp --method slsqp
os-optics-kit optimize studies/optimize-expander-infeasible.json --out strict
```

Options: `--method trf|slsqp` and `--max-evaluations N` override the spec's `solver`; `--overwrite` replaces the
files of an earlier run in `--out` (a new or empty directory otherwise). SciPy and numpy come with os-optics.

## The spec

```json
{
  "schema": "os-optics-kit.optimize-spec", "schema_version": 1,
  "description": "Collimate the expander: move L2 along the axis",
  "project": "../project.json",
  "variables": [
    {"name": "separation", "unit": "mm", "bounds": [150, 250], "scale": 1.0,
     "ops": [{"op": "set_component_pose", "instance_id": "L2",
              "position_mm": [{"$value": {"offset": 100.0}}, 0.0, 50.0]}]}
  ],
  "objectives": [{"name": "divergence", "source": {"target": "collimated"}, "goal": 0.0, "scale": 1e-4}],
  "constraints": [{"name": "collimated", "source": {"target": "collimated"}, "maximum": 1e-3, "scale": 1e-4}],
  "solver": {"method": "trf", "max_evaluations": 120,
             "tolerances": {"ftol": 1e-12, "xtol": 1e-12, "gtol": 1e-12}, "diff_step": 1e-3}
}
```

| Field | Meaning |
| --- | --- |
| `project` | the base Project: a path relative to the spec, or the Project object; `configuration` names the state of a Project with configurations (flattened first) |
| `variables[]` | `name`, `unit`, `ops` (edit ops of `os-optics-kit edit`, `"$value"` = the value; `{"$value": {"offset", "scale"}}` writes `offset + scale * value`), `bounds`, `nominal` (omitted: read from the field the ops write in the base Project, 0 for `transform`), `scale` (the variable's step unit, default 1) |
| `objectives[]` | `name`, `source`, `goal`, `scale`, `weight`: residual `sqrt(weight) * (value - goal) / scale` |
| `constraints[]` | `name`, `source`, `minimum` and/or `maximum`, `scale`: `margin` is the scaled distance to the nearest limit, positive inside |
| `aggregation`, `value_use`, `imaging`, `invalid_residual` | as in the evaluation adapter |
| `ray_loss` | `infeasible` (default) or `report`: what lost pupil rays mean |
| `active_margin` | a constraint is active when its \|margin\| is at most this (default 1e-3) |
| `solver` | `method` (`trf`, `slsqp`), `max_evaluations` (default 200), `max_iterations`, `tolerances` (`ftol`, `xtol`, `gtol`; default 1e-8), `diff_step` (default 1e-3 variable scales), `difference` (`central`, `forward`), `x_scale` (`variables`, `jac`) |
| `verify` | `ops` and `imaging` applied to the best Project for a second check (for example denser pupil sampling), `tolerances` (`relative` 1e-9, `absolute` 1e-12) of the reproduction |

Quantity sources: `{"target": ID}` (a declared target), `{"rays": {"target": ID}}` (the share of a spot target's
pupil rays that reach its plane), `{"paraxial": {"components", "wavelength_nm", "quantity"}}` (EFL, BFL, ... of the
engine's paraxial analysis), `{"solids": {"quantity", "component", "member"}}` (the thinnest glass gap of the
optical-solid check) and `{"mtf": {...}}` (an MTF floor; a constraint only). The
[evaluation adapter](evaluation-adapter.md#quantity-sources) describes each.

The edit op `set_optical_model_value {model_id, path, value}` reaches one number of a declared model by JSON pointer,
for example `/surfaces/1/radius_mm`, `/surfaces/0/conic` or `/surfaces/0/aspheric_coefficients/0`; a decimal string
stays a string. It works in `os-optics-kit edit` and in variable templates, and an omitted `nominal` is read from it.

The whole spec is checked before any evaluation. An unknown key, a malformed source, MTF as an objective, `trf`
with an aggregation other than `sum_squares`, a nominal outside its bounds, or an op that cannot apply is refused
with `SPEC_INVALID` and the `path` of the offending field (exit 4); a nominal whose fields disagree is
`NOMINAL_AMBIGUOUS`, a Project with configurations and no `configuration` is `CONFIGURATION_REQUIRED`.

## Method

- The solvers work in scaled variables `z = (x - start) / scale`, starting from the nominal values.
- `trf` is `scipy.optimize.least_squares(method="trf")` on the adapter's residual vector: the objective errors, then
  each constraint's violation `max(0, -margin)` as a quadratic penalty. It needs `aggregation: sum_squares`.
- `slsqp` is `scipy.optimize.minimize(method="SLSQP")` on the adapter's objective, with every constraint margin
  `>= 0` as an inequality constraint. It uses `ftol` only.
- Derivatives are central differences with the step `diff_step` in variable scales, one-sided at a bound. Every
  probe is an engine evaluation: counted, cached by the exact candidate vector (a repeat costs nothing), written to
  the history, and never outside the bounds.
- `max_evaluations` is a hard cap on engine evaluations, the start and every probe included: the evaluation that
  would exceed it is not run.
- Feasibility is the adapter's: every constraint holds and, under `ray_loss: infeasible`, no objective or constraint
  read a target that lost pupil rays. The best candidate is the feasible evaluation with the smallest objective.
- The search is deterministic: the same spec and engine give the same evaluations in the same order.

Lost rays. A spot RMS (`spot_rms_mm`) counts only the pupil rays that reach the plane, so a search can shrink a spot
by vignetting it. Under the default `ray_loss: infeasible` a candidate that loses pupil rays is not feasible. When
the start already loses rays, set `ray_loss: report` and bound the loss with `rays` constraints, as the aspheric
example below does. A `rays` value changes in steps of one ray: its finite-difference derivative is zero, or a spike
where a probe crosses a step, so neither solver can see the limit coming. Keep the variable bounds local around a
start that holds it; `trf` then steps across, sees the quadratic penalty and steps back, while the best candidate is
always taken from the feasible evaluations.

## Read the result

| Status | Exit | Meaning |
| --- | --- | --- |
| `CONVERGED` | 0 | the solver met its tolerance; a feasible best exists |
| `NO_FEASIBLE` | 1 | no candidate was feasible: no `best-project.json`; `closest` and every margin in `history.jsonl` |
| `STOPPED` | 2 | budget, iteration cap or a solver stop, with a feasible best |
| `INTERRUPTED` | 130 | Ctrl-C; the files written so far are kept |

`--out` holds `spec.json` (the spec as run, defaults filled in and the base Project inlined: it runs again by
itself), `history.jsonl` (one line per engine evaluation: `index`, `role` (`start`, `step`, `jacobian`),
`candidate`, objective, values, margins, `infeasible`, `rays_lost`, `reason`, `seconds`, identity hashes),
`result.json` and `best-project.json`. `result.json` (schema `os-optics-kit.optimize` v1):

- `termination`: `reason` (`CONVERGED`, `BUDGET_EXHAUSTED`, `ITERATION_LIMIT`, `SOLVER_STOPPED`, `INTERRUPTED`), the
  solver's message and status, its last point;
- `counts`: `evaluations` (by role), `cache_hits`, `iterations` (null when the budget stopped the solver before it
  returned), `seconds`, `verification_evaluations`;
- `variables`: each nominal and where it was read (`given`, `base`, `default`);
- `best` and `start`: candidate, objective, each objective and constraint with value, margin, `active` and its
  `conditions` (target, plane, source, wavelength, quantity, binding gap), `active_constraints`, `worst_residual`,
  `ray_loss`, identity; `closest` replaces `best` when nothing is feasible;
- `feasible_count`, `infeasible_reasons` (`CONSTRAINT_VIOLATED`, `RAYS_LOST`, an invalid candidate's reason code);
- `verification`: `reproduction` evaluates `best-project.json` as written in a fresh session and compares every
  value, margin and residual (`REPRODUCED` or `MISMATCH`); `dense` evaluates it again with the `verify` ops and
  imaging and lists each value before and after and whether feasibility changed;
- `identity`: engine version and build, catalog, base Project hash, `start_reproduces_base`, spec digests.

## Verification

Beam expander (catalog-free, os-optics 0.14.0). The independent ABCD optimum of the case script is
200.0572873 mm.

| Spec | Method | Status | Evaluations | Separation (mm) | From ABCD |
| --- | --- | --- | --- | --- | --- |
| `optimize-expander.json` | `trf` | `CONVERGED` | 42 | 200.0572870 | 3e-7 mm |
| `optimize-expander.json` | `slsqp` | `CONVERGED` | 9 | 200.0572869 | 3e-7 mm |
| `optimize-expander-infeasible.json` | `trf` | `NO_FEASIBLE` | 42 | closest: 200.0572870 | closest margin −1.128 |

Aspheric lens ([aspheric-lens](../../cases/aspheric-lens), `studies/optimize-aspheric.json`): five variables (L1
front radius, L5 back radius, L5 front fourth-order term, L2 front conic, image-plane position) in local ranges, the
nine spot RMS targets as objectives, the paraxial EFL held at 50 ± 0.25 mm, glass at least 3 mm thick, and the
vignetting bounded with `rays` constraints (every pupil ray on axis and at 12.5°, at least 75 % at 25°). Same start,
bounds, requirements and budget (150 evaluations) for both methods; os-optics 0.14.0, wall times on a shared
machine.

| Method | Status | Evaluations (start / steps / probes) | Feasible | Objective (start 162.0) | 25° F spot (start 86.2 µm) | Wall time |
| --- | --- | --- | --- | --- | --- | --- |
| `trf` | `STOPPED` (budget) | 150 (1 / 20 / 129) | 55 | 94.09 | 66.4 µm | 280 s |
| `slsqp` | `STOPPED` (budget) | 150 (1 / 29 / 120), 218 cache hits | 11 | 161.92 | 86.2 µm | 213 s |

The `trf` best keeps the EFL at 50.137 mm and every limit; with twice the pupil rings it stays feasible (objective
93.80, the 25° C share 0.750, at its floor). SLSQP linearizes the `rays` limits with a zero derivative, steps deep
into vignetting at once and cycles there: its best is a finite-difference probe beside the start. Without the `rays`
constraints `trf` reached an objective of 26.0, but only 44, 27 and 19 of the 96 pupil rays of the 25° field arrived:
the spots shrank by vignetting, which is what the constraints prevent.

## Reuse from Python

```python
from os_optics_kit import optimize

result = optimize.run("studies/optimize-expander.json", "opt", method="slsqp")
print(result["status"], result["best"]["candidate"], result["counts"]["evaluations"])
```

`optimize.run(spec, out_dir, *, session=None, session_factory=None, adapter_options=None)` writes the directory and
returns `result.json`; `optimize.load_spec` validates a spec without evaluating it. `os_optics_kit.study.solvers`
holds the solvers: `solve(adapter, Settings(...), recorder)` drives any evaluation-adapter `Adapter` and returns
every evaluation with the stopping reason; `best` and `closest` pick from them.

Related: [Evaluation adapter](evaluation-adapter.md), [Studies](studies.md), `os-optics-kit sensitivity` to rank
variables and check an optimum.
