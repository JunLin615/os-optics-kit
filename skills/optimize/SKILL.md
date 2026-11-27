---
name: optimize
description: Bounded local optimization of a Project from a JSON spec (trf least squares or SLSQP): named variables, objectives and constraints with margins, a hard evaluation budget, a full history and the best feasible Project, re-checked in a fresh session. Use it to refine a fixed layout.
version: 0.1.0
command: os-optics-kit optimize
requires: os-optics>=0.14,<0.15
kind: tool
---

# optimize

## When to use

- Refine continuous parameters of one layout (positions, radii, conics, aspheric terms, plane
  position) to drive targets toward goals while limits hold: spot size, divergence, paraxial EFL,
  glass thickness, an MTF floor.
- Find out whether any candidate meets a requirement (`NO_FEASIBLE` says none did, with margins).

## When not to use

- Rank parameters or check a converged optimum: `sensitivity`. Independent batches: `study`.
- Compare alternative assemblies: one spec per assembly, then `compare` (see `interpret.md`).

## Inputs

`SPEC.json` (`os-optics-kit.optimize-spec` v1; full example: `spec.md`):

- `project` (path relative to the spec, or inline), `configuration` for a configurations Project.
- `variables`: `{name, unit?, ops, bounds, nominal?, scale?}`; `ops` are `edit` ops with
  `"$value"` = the value (`set_optical_model_value` reaches a model member by JSON pointer). Omitted
  nominal: read from the base Project.
- `objectives` `{name, source, goal, scale, weight}`, `constraints` `{name, source, minimum?,
  maximum?, scale}`. Sources: `{"target": ID}`, `{"rays": {"target": ID}}` (share of a spot target's
  pupil rays that arrive), `{"paraxial": {components, wavelength_nm,
  quantity}}`, `{"solids": {quantity, component?, member?}}`, `{"mtf": {...}}` (constraint only).
- `ray_loss`: `infeasible` (default) or `report`; `value_use`, `imaging`, `aggregation`.
- `solver`: `method` `trf` | `slsqp`, `max_evaluations`, `tolerances`, `diff_step` (variable scales).
- `verify`: `ops` (e.g. denser pupil sampling), `imaging`, reproduction `tolerances`.

CLI: `--out DIR`, `--method`, `--max-evaluations`, `--overwrite`. Python: `os_optics_kit.optimize.run`.

## Outputs

`DIR/spec.json` (as run, Project inlined: reruns as is), `history.jsonl` (every evaluation,
finite-difference probes included), `result.json`, `best-project.json` (only when feasible).
Stdout: status, termination, counts, best candidate with active constraints and worst residual.
Exit 0 `CONVERGED`, 1 `NO_FEASIBLE`, 2 `STOPPED` (budget or iteration cap), 4 refused spec, 130
interrupted. Reading `result.json`: `interpret.md`.

## Minimal example

```
os-optics-kit cases copy interop-optimization --dest .
cd interop-optimization
os-optics-kit optimize studies/optimize-expander.json --out opt
```

L2 moves to a 200.0573 mm separation (the ABCD optimum): `CONVERGED`, exit 0.

## Limits

- Local search from the nominal; fixed layout. Spot RMS counts arriving rays only: lost rays are
  reported, and infeasible by default.
- `trf` needs `aggregation: sum_squares`; MTF is a constraint, not an objective.

## Version

0.1.0 (os-optics-kit 0.5.0): first version.
