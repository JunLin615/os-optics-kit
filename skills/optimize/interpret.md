# optimize: reading the result

`result.json` answers what to do next. Read it in this order:

1. `status` and `termination.reason`:
   - `CONVERGED` (exit 0): the solver met its tolerance at a feasible point. Check
     `verification.reproduction.status` (`REPRODUCED`: best-project.json gives the same numbers in a
     fresh session) and `verification.dense` (values and feasibility with denser sampling). A
     feasibility change there means the search sampling was too coarse.
   - `STOPPED` (exit 2): a feasible best exists but the budget (`BUDGET_EXHAUSTED`), the iteration
     cap or the solver (`SOLVER_STOPPED`, its `solver_message`) ended the search, or `trf` converged
     just outside an active constraint (`CONVERGED_INFEASIBLE`: rerun with `slsqp`). Rerun from the best:
     set each variable's `nominal` to `best.candidate`, or raise `max_evaluations`.
   - `NO_FEASIBLE` (exit 1): no candidate met every constraint. `infeasible_reasons` counts why
     (`CONSTRAINT_VIOLATED`, `RAYS_LOST`, an invalid candidate's reason code such as
     `QUANTITY_UNAVAILABLE`); `closest` is the candidate nearest to feasible with its margins. A
     margin that stays negative everywhere means the requirement is out of reach for this layout:
     relax it, free another variable, or change the layout. Every candidate's margins are in
     `history.jsonl`.
2. `best`: `candidate`, `objective`, `constraints` (value, margin, `active` when |margin| <=
   `active_margin`), `active_constraints` (the limits that hold the design back), `worst_residual`
   with its `conditions` (target, plane, source, wavelength: the worst field), `ray_loss`.
3. `counts`: physical `evaluations` (by role: `start`, `step`, `jacobian`), `cache_hits`,
   `iterations`, `seconds`. A run that spends most evaluations on `jacobian` with little progress
   has a poorly scaled variable: revisit `scale`.

Composing:

- Before: `os-optics-kit sensitivity` on the same ops ranks which variables matter and suggests
  their scales; `os-optics-kit edit --describe` finds ids and model paths.
- After: `os-optics-kit edit best-project.json ...` for a manual change, `os-optics project run
  best-project.json --out-dir run` for a full run, `os-optics-kit compare` between runs of the
  start and the best, and `sensitivity` at the optimum (`NEAR_EXTREMUM` confirms a minimum).
- A fixed assembly: give only the parameters the brief allows to change as variables; every other
  element stays as in the base Project.
- Alternative assemblies (another element, another glass): build each as its own base Project,
  run the same spec on each (same requirements and budget), and compare their `best` and
  `counts`.
