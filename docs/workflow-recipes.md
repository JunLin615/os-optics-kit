# Workflow recipes for agents

[Agent guide](../AGENT_GUIDE.md). English-only reference for the workflow scripts in
`examples/`; read only the section the task needs. Each script's `--help` holds its
complete specification. These workflows use os-optics **0.10.0**, the version
recorded in [engine-baseline.json](../engine-baseline.json) with the matching
[installation guide](en/installation.md). Human walkthrough: [English](en/workflows.md) ·
[简体中文](zh-CN/workflows.md).

## Conventions

- Run from the repository root. Stdout is one compact JSON document; diagnostics go to
  stderr. A refusal prints `{"error": {"code", "message", ...}}` on stderr, nothing on
  stdout, and exits 4. Invalid edit requests are rejected before changing the Project;
  study sample failures remain recorded alongside successful samples. One exception: when `edit_project.py
  --run` writes a valid edit and the Engine refuses the run, stdout reports
  `run.status` `RUN_REFUSED` with its `problems` (exit 4).
- Inputs are never modified. Edit, study and workflow scripts refuse an existing output
  (`OUTPUT_EXISTS`) unless `--overwrite`; on a study directory `--overwrite` also removes
  its `plots/` and `report/`, which describe the previous samples.
- Numbers keep their schema unit (`_mm`, `_nm`, `_w`/`_W`, `_deg`, `_rad`; target bounds
  in the target's `unit`) or are written `{"value": 250, "unit": "um"}` with `nm`, `um`,
  `mm`, `m`, `deg`, `rad`, `mrad`, `urad`, `W`, `mW`, `uW`. Quaternions `[w, x, y, z]`
  are unitless; rotation angles are degrees unless given as a quantity.
- Paths in stdout are as given on the command line; paths recorded inside a study are
  relative to its study directory.
- `installed_catalog` describes the local cache only. Run and study catalog identity
  comes from saved results; a catalog-free run retains a null `catalog_version` even
  with a catalog installed. A study's `catalog_versions` preserves distinct run
  versions, including null, when its samples differ.

| Script | Exit 0 | 1 | 2 | 3 | 4 |
| --- | --- | --- | --- | --- | --- |
| `edit_project.py` | `EDITED`, `ALREADY_APPLIED`, `DESCRIBED`; with `--run` outcome `PASS` or `NOT_APPLICABLE` | outcome `FAIL` | outcome `UNKNOWN` | outcome `OUT_OF_MODEL` | refused, `RUN_REFUSED` |
| `sweep_parameters.py` | `COMPLETED` | `NO_FEASIBLE` (constraints given, none met) | | | refused |
| `sensitivity_analysis.py` | `COMPLETED` | | `INCOMPLETE` (a derivative unavailable) | | refused |
| `tolerance_study.py` | `COMPLETED` (at least one PASS), `DRAWN`, `REPRODUCED` | `NO_FEASIBLE` (no PASS), `MISMATCH` | | | refused |
| `compare_runs.py`, `plot_results.py`, `build_report.py` | `COMPLETED` | | | | invalid input |
| `catalog_candidates.py` | `COMPLETED` | `NO_MATCH` | | | invalid input, no catalog |
| three design workflows | nominal `PASS` | nominal `FAIL` | `UNKNOWN` | `OUT_OF_MODEL` | `ERROR`, refused |

`edit_project.py --run` uses the outcome of its selected targets. It retains the
engine's execution, requirements, quantity validity and issue fields. Outcome classes
are workflow routing summaries; inspect those fields for the scientific meaning.

## Outcome classes

Selected requirements determine a sample's class:

1. `ERROR`: an edit, load or run was refused, or the sample was not run.
2. `OUT_OF_MODEL`: a selected target depends on a quantity outside its model. A missing selected target ID is `UNKNOWN`.
3. `FAIL`: at least one selected requirement is unmet; other indeterminate requirements remain listed.
4. `UNKNOWN`: no selected requirement is unmet, but one is indeterminate or its quantity is unavailable.
5. `PASS`: all selected requirements are met.
6. `NOT_APPLICABLE`: no selected requirement applies, including a target-free run. A mixture of applicable and not-applicable targets stays explicit as `UNKNOWN`.

Unrelated scope findings remain in the record without invalidating an otherwise
usable selected quantity. A bounded quantity retains its interval and is not an exact
objective or derivative input. `objective_valid` is separate from `feasible`: an exact
objective may be usable even when a requirement is unmet. Every requested sample stays
in the table, including invalid edits, partial runs and indeterminate results.

`pass_fraction` divides by **all requested samples**;
`pass_fraction_among_evaluable` divides by PASS + FAIL and is labelled accordingly.
A tolerance fraction describes simulated samples under the specified distributions.

## Native Project controls

The engine supplies semantic editing, atomic checks and freshness. For an existing
`BASE.json`, write the operations to `OPS.json` and use:

```bash
os-optics project check BASE.json
os-optics project update BASE.json --ops OPS.json --expect-hash BASE_HASH --out NEW.json
os-optics project check NEW.json
os-optics project run NEW.json --out-dir NEW-run
os-optics results targets NEW-run
os-optics results freshness NEW-run --project NEW.json
```

Replace `BASE_HASH` with the `project_hash` from `project check`. The update checks all
operations and the resulting Project before writing anything. Invalid edits return
exit 65 and leave the output untouched. A hash mismatch returns `PROJECT_CHANGED` and
the current hash. Without `--out`, update returns the new Project in its report; it does
not write it. With the input file as `--out`, update changes that file atomically.

After an interrupted response to an in-place relative transform, repeat only with the
**original** expected hash. A mismatch means inspect the current Project before making
a new decision; do not substitute the new hash and blindly repeat the transform.
Absolute assignments are unchanged when reapplied, but relative moves accumulate.
The helper below instead preserves a base/output pair and an edit record for retries.

## Computation budgets

Study `max_evaluations` caps the number of designs. A Project `compute_budget` caps work
inside each design. Inspect effective limits in `project check` or `capabilities`.
To see a limited run with the catalog-free expansion workflow, first generate
`run-expansion/nominal/project.json` using `beam_expansion_focus.py --no-studies`, then
save the following as `budget.json`:

```json
[{"op": "set_compute_budget", "compute_budget": {
  "max_interactions_per_path": 1,
  "max_paths": 16,
  "max_trace_steps": 32,
  "max_interference_pairs": 16
}}]
```

```bash
os-optics project update run-expansion/nominal/project.json --ops budget.json --out limited.json
os-optics project check limited.json
os-optics project run limited.json --out-dir limited-run
os-optics results targets limited-run
os-optics results freshness run-expansion/nominal/run --project limited.json
```

The one-interaction limit stops this three-lens design early. Inspect
`outcome.execution.budgets.exhausted`, affected paths and selected target assessments;
retain the limited run. Raise `max_interactions_per_path` to 8 in a second budget file,
write another Project and run directory, then compare which quantities become available.
Choose limits for the requested topology and accuracy: higher caps permit more work
and larger artifacts. Changing a cap makes the previous result historical with
`PHYSICS_CHANGED`. `set_compute_budget` with `compute_budget: null` restores engine
defaults; it does not remove all limits. The reusable operation files
[`compute_budget_limited.json`](../examples/specs/compute_budget_limited.json) (one trace
step) and [`compute_budget_default.json`](../examples/specs/compute_budget_default.json)
(default restoration) offer the same limited-versus-default comparison. Pass either
file to native `--ops` or the helper's `--edits`.

## Find objects

```bash
python examples/edit_project.py run-example/project.json --describe
python examples/edit_project.py run-example/project.json --describe --ids SRC,L2,expanded_waist
```

The first prints every component, observation and target with `id`, `kind`, `role`,
`binding` and position (a short index). `--ids` adds, per
object, the current values with units, pose with `local_axes` in design axes, retained
DOFs, the allowed ops, target bounds with `bounds_unit`, and the model and catalog
wavelength bands, plus one `conventions` block (transform frames and pivots, pose
formula, component origin, plane axes). Read `local_axes` before moving along a local
axis: in the catalog beam expander L2's local +Z is design −X.

## Edit a project by ID

```bash
python examples/edit_project.py BASE.json --edits EDITS.json --out NEW.json [--run RUN_DIR] \
    [--changes FILE] [--expect-base-hash HASH] [--overwrite]
```

`EDITS.json` is a list of operations (or `{"ops": [...]}`) in the engine semantic-edit
vocabulary, applied by the engine in order to a copy of BASE. The helper adds unit
conversion, original-file preservation and a durable retry record. `changes` objects merge;
`null` removes a field. An argument left out is refused (`BAD_ARGUMENT`), as in the
Designer: `set_retained_dof` needs `control` (`null` releases the DOF) and
`set_design_intent` needs `design_intent` (`null` removes it).

| Op | Arguments |
| --- | --- |
| `set_component_pose` | `instance_id`, `position_mm?`, `quaternion_wxyz?` (absolute, design frame) |
| `set_source_model` | `instance_id`, `changes` (e.g. `wavelength_nm`, `power_w`, `waist_radius_mm`) |
| `set_observation_pose`, `set_observation` | `observation_id`, pose fields or `changes` |
| `set_target` | `target_id`, `changes` (bounds in the target's unit) |
| `set_component_product` | `instance_id`, `optical_sku`, `catalog_version?` |
| `set_component_model` | `instance_id`, `optical_model_id`, `custom_part_key?`, `description?` |
| `set_retained_dof(s)`, `set_design_intent` | as in `--help` |
| `set_compute_budget` | `compute_budget` object with supported limits, or `null` for defaults |
| `add_/remove_component`, `add_/remove_observation`, `add_/remove_target` | object or id |
| model ops | `add_/update_/remove_optical_model`, `add_/update_/remove_model_material`, `add_/remove_model_coating` |
| `transform` | `selector {ids \| groups \| instances \| kinds}`, `translation_mm?`, `rotation? {axis, angle_deg} \| {quaternion_wxyz}`, `frame "design" \| {"component": ID}`, `pivot "frame_origin" \| "selection_centroid" \| {"point_mm": [...]} \| {"component": ID}` |

`transform` is a rigid move computed by the Engine; translation and rotation axis are in
the frame's axes, so `"frame": {"component": "L2"}` with `"translation_mm": [0, 0, 0.25]`
moves L2 0.25 mm along its own optical axis. Observation ids in the selector move too.
The workspace frames `{"group": ID}` and `{"instance": ID}` exist only in a Designer
workspace; on a Project file the Engine refuses them (`FRAME_INVALID`).

Example on the catalog beam expander (`python examples/beam_expander.py --out run-example`):
change the wavelength, move L2 and the output plane 1 mm along design +X, and replace the
waist target by an interval. Save as `edits.json`:

```json
[
  {"op": "set_source_model", "instance_id": "SRC", "changes": {"wavelength_nm": 785}},
  {"op": "transform", "selector": {"ids": ["L2", "output"]}, "frame": "design", "translation_mm": [1, 0, 0]},
  {"op": "set_target", "target_id": "expanded_waist",
   "changes": {"target_value": null, "tolerance_rel": null, "minimum": 1.8, "maximum": 2.2}}
]
```

```bash
python examples/edit_project.py run-example/project.json --edits edits.json --out run-edit/project.json --run run-edit/run
```

Result on catalog `2026-10-28.1`: exit 1, `run.outcome` `FAIL`, `expanded_waist`
1.1148985411200965 mm outside 1.8–2.2 mm (a design that misses its target). The output
is `run-edit/project.json` (the Engine-normalized Project),
`run-edit/project.changes.json` (every op as given and normalized, before/after of each
affected field, project hashes before and after, output sha256) and the run directory.

Retry and concurrency:

- Running the same command again prints `"already_applied": true`, writes nothing and
  reuses the run when it is still current (`run.reused: true`). The operations digest is
  taken over the normalized operations with numbers as floats, so the same edit written
  in other units (`1` mm or `{"value": 1000, "unit": "um"}`) counts as the same edit.
- The same `--out` with different ops or a changed base is refused: `OUTPUT_EXISTS` with
  `mismatch` `OPS_CHANGED`, `BASE_CHANGED`, `RECORD_MISSING` or `OUTPUT_MODIFIED`. Choose a
  new `--out`, or `--overwrite` deliberately.
- `--expect-base-hash HASH` guards the preserved input snapshot (unlike the native
  in-place `--expect-hash` example above). It refuses with
  `BASE_CHANGED` and the `current_hash` when BASE has changed since that result.

## Sweep

```bash
python examples/sweep_parameters.py SPEC.json [--base BASE.json] --out STUDY_DIR
```

| Key | Meaning |
| --- | --- |
| `base_project` | Project file relative to the spec; `--base` overrides |
| `parameters` | 1 or 2 entries `{name, unit?, values \| range {start, stop, count}, ops}`; two give the full grid, first outermost |
| `max_evaluations` | required; refused with `BUDGET_EXCEEDED` before any run when the sample count exceeds it |
| `targets` | ids deciding each sample's outcome (default all) |
| `constraints` | all must hold: `{"outcome": [...]}`, `{"target": ID, "assessment": ["MET"]}`, `{"column": C, "min"?, "max"?}` (quantities convert to the column unit) |
| `objective` | `{"column": C, "sense": "minimize" \| "maximize"}` over feasible samples (finite exact objective with applicable dependencies, every explicit constraint met) |
| `derived` | `{"name", "op": add \| sub \| mul \| div \| abs \| abs_diff, "a", "b", "unit"?}`; `unit` applies to its constraints, objective, table and plots |
| `stop_on` | outcome classes that stop the sweep; remaining samples become `ERROR` (default none) |

Placeholders in `ops`: `"$value"` becomes `{"value": v, "unit": <parameter unit>}`;
`{"$value": {"scale": s, "offset": o}}` becomes `o + s·v`. Each sample applies its ops to
the unchanged base. Columns: `param:<name>`, `target:<id>` (target unit),
`derived:<name>` (a derived operand may use the derived columns listed before it). Before
any run, a target id that is in neither the base nor the first sample is refused with
`TARGET_UNKNOWN`, and an objective, constraint or derived column that the spec cannot
produce with `COLUMN_UNKNOWN`; both list the known ids or columns. Examples:
`examples/specs/beam_expander_l2_sweep.json` (one parameter, constraint, objective and a
derived column in mm) and `examples/specs/polarization_hwp_wavelength_sweep.json`
(2-parameter grid with out-of-model samples).

Writes `study.json` (spec as given and normalized, base hash, identities, budget),
`samples/<id>/{project.json, run/, sample.json}`, `table.csv` (every sample: parameters
with units, outcome, status, `model_scope_issues`, `out_of_model_issues`, per-target
value/unit/status/assessment, quantity validity, objective validity, error codes, hashes, run path, seconds) and `summary.json`. Stdout
lists counts with denominators, the best sample or `no_feasible` with the failed
constraints,
`samples_with_warnings` (samples whose runs carry WARNING issues, how many of them PASS,
codes) and at most five rows, each with its `warnings`.

## Compare and freshness

```bash
python examples/compare_runs.py RUN_A RUN_B --project CURRENT.json --out CMP
os-optics results freshness RUN_A --project CURRENT.json
```

Targets and observation planes align by ID; missing quantities remain explicit.
Input differences include geometry, sources, targets, budgets and catalog identity.
The helper uses engine freshness: `CURRENT`, `HISTORICAL` or `UNDETERMINED`, preserving
reasons such as `PHYSICS_CHANGED`, `TARGETS_CHANGED`, `OTHER_PROJECT_CHANGED`,
`CATALOG_CHANGED` and `RUN_UNVERIFIED`. A budget change is `PHYSICS_CHANGED`.

An unchanged Project and lowered input can remain current across engine builds;
`engine_build_matches` reports that distinction. A historical result keeps its
original assessment, displayed as e.g. `HISTORICAL PASS`, and is not an assessment of
the current design. Nothing is rerun by comparison. Files: `CMP/compare.json` and
`CMP/compare.csv` (long format).

## Plots and reports

```bash
python examples/plot_results.py sweep STUDY_DIR --y target:expanded_waist
python examples/plot_results.py power RUN_DIR
python examples/build_report.py STUDY_DIR --project CURRENT.json --plots STUDY_DIR/plots --changes NEW.changes.json
python examples/build_report.py RUN_A RUN_B --project CURRENT.json --out REPORT_DIR
```

`sweep` draws a curve (one parameter) or heatmap (two) with axis units, the target
bounds (a band on the curve, dashed lines on the heatmap colour bar) and every sample's
outcome marker; samples without an exact value are hollow markers on a dotted line labelled
"samples without an exact value" below the lowest value (the position carries no value), with
their own legend entry, or a marked empty cell on the heatmap. `power` plots received
power per plane and the power-ledger sinks. Solid bars are known powers; hatched
bars are traced lower bounds, with an upper-bound whisker when available. An
unknown quantity is labelled without substituting zero, and the JSON retains its
null value and quantity state.
`bars` and `histogram` take a small JSON file (`--help`). PNG + SVG go to
`STUDY_DIR/plots/` or, for a run, `plots/` next to the run directory; the caption names
the data file (sha256 prefix), Engine build and catalog release. Reports write
`report.md` + `report.json` (default `STUDY_DIR/report/` or `./report/`) with inputs,
the edit record, outcomes with units, diagnostics (model-scope issues, target issue
codes, warnings and refusals of every run or sample), freshness (the best sample of a
study is labelled `HISTORICAL` like its row), plot links and every artifact's relative
path and sha256.

## Catalog candidates

```bash
python examples/catalog_candidates.py --category LENS --form PLANO_CONVEX_LENS --wavelength-nm 780 \
    --range focal_length_mm=95..105 --range outer_diameter_mm=12.7..12.7 --near focal_length_mm=100 \
    --limit 8 --columns optical_sku,focal_length_mm,outer_diameter_mm,working_band_nm --out candidates.csv
```

`--category`, `--form`, `--readiness`, `--mounting` and `--wavelength-nm` (a published
model is valid there) are Engine filters; `--range FIELD=MIN..MAX[UNIT]` compares the
catalog's decimal strings exactly (either side may be empty); `--near FIELD=VALUE` or
`--sort FIELD [--descending]` order the rows; `--limit` ≤ 100. The output states
`catalog_version`, `engine_matches`, `matched`, `excluded_missing_field` (products
without a `--range`, `--near` or `--sort` field, per field), `shown` and `omitted`;
`--out` writes every matched row. The command above returns 8 of
471 plano-convex lenses valid at 780 nm. Use a returned `optical_sku` in
`set_component_product`.

## Sensitivity

```bash
python examples/sensitivity_analysis.py SPEC.json --out STUDY_DIR [--base BASE.json] [--no-plots] [--no-report]
```

| Key | Meaning |
| --- | --- |
| `base_project` or `design` | Project file, or `{"builder": NAME, "arguments": {...}}` from `examples/optical_designs.py` (catalog-free) |
| `targets` | target ids to differentiate (unknown ids refused: `TARGET_UNKNOWN`) |
| `parameters` | `{name, unit, ops, nominal = 0, steps (≥ 2; numbers, quantities or {"relative": r}), scale?}` |
| `consistency_threshold` | relative difference of two step estimates that is flagged (default 0.01) |
| `resolution` | per-target change treated as none, e.g. `{"far_position_u": {"value": 1, "unit": "nm"}}` |
| `max_evaluations` | required; 1 + 2·(total steps) must fit |

`"$value"` is nominal + perturbation. For a `transform` translation or rotation keep
`nominal` 0 (the ops move by the step); for a `set_*` op give the field's base value as
`nominal`, otherwise `NOMINAL_MISMATCH`. Default steps: 0.01 and 0.1 mm, 0.01° and 0.1°,
relative 1e-3 and 1e-2. Method: central difference at each step, forward or backward
(labelled, with the reason) when one side is ERROR, out of model or without an exact valid selected quantity;
second difference reported; flags `STEP_DEPENDENT`, `NEAR_EXTREMUM` (use the second
difference there), `NO_CHANGE`, `ONE_SIDED`, `METHOD_DIFFERS`, `UNAVAILABLE`. Units are
target unit per parameter unit. With `scale`, `scaled` = D·s and the ranking uses
|D|·s + |f″|·s²/2. Files add `sensitivity.csv` and `plots/sensitivity_<target>`,
`plots/tornado_<target>`. Specs: `examples/specs/sensitivity_*.json`;
`sensitivity_expander_band_edge.json` shows a one-sided estimate next to the band edge.

## Tolerance

```bash
python examples/tolerance_study.py SPEC.json --out STUDY_DIR [--seed N] [--samples N] [--zero-tolerance] [--draw-only]
python examples/tolerance_study.py --verify-seed STUDY_DIR
```

| Key | Meaning |
| --- | --- |
| `base_project` or `design` | as for sensitivity |
| `targets` | all selected target assessments must be `MET` for a sample to PASS |
| `samples`, `seed`, `max_evaluations` | sample count (nominal run extra), integer seed, budget ≥ samples + 1 |
| `parameters` | `{name, unit, ops, nominal = 0, distribution}`, sampled independently in this order |
| `distribution` | `{"kind": "uniform", "low", "high"}` or `{"kind": "normal", "sigma", "mean_offset" = 0, "truncate_sigma" \| "bounds": [low, high]}` (offsets in the parameter unit; normal bounds holding less than 1e-9 of the distribution are refused with `SPEC_INVALID`) |

Offsets come from `numpy.random.Generator(PCG64(seed))`, one uniform number per sample
and parameter, row by row; `samples.csv` and `sampling.json` are written before any run.
The same seed and spec give byte-identical `samples.csv`, and the first n samples of a
larger run are the same; `--verify-seed` redraws and compares. `--zero-tolerance`
reproduces the nominal project and result hashes exactly. Stdout: counts per outcome
class with denominators, target-value statistics over exact valid selected quantities
(count stated), `samples_with_warnings`, sampling assumptions. Specs:
`examples/specs/tolerance_*.json`.

## Design workflows

```bash
python examples/beam_expansion_focus.py --out run-expansion-gap --gap-error-mm 250um --study f3_mm=75..125:5
python examples/polarization_control.py --out run-pol-1064 --wavelength-nm 1064 --no-studies
python examples/alignment_analysis.py --out run-alignment-tilt --study mirror_tilt_local_y_mrad=-1..1:5
```

Each builds a catalog-free design from `examples/optical_designs.py`, runs it, runs its
studies, compares each study with the nominal run, plots changing targets, compares the
Engine values with an independent closed-form reference and writes reports.
`--list-parameters` prints every input with unit, default, meaning and role. Inputs take
a bare number in the option's unit or a unit suffix (`--gap-error-mm 250um`,
`--min-power-w 0.85mW`). `--study NAME=V1,V2` or `NAME=START..STOP:COUNT` (repeatable)
replaces the default studies; `--max-evaluations` (default 40) bounds nominal plus all
samples before anything runs. Output: `DIR/workflow.json`, `DIR/nominal/`,
`DIR/studies/<NAME>/` (sweep study, `compare/`, `plots/`, `closed_form.json`, `report/`),
`DIR/report/report.md`. Exit follows the nominal outcome; study samples are counted in
`studies[].counts` and `pass_fraction` ("n/N"). `alignment_analysis.py
--synthetic-measurements` adds a fit of rows labelled `SYNTHETIC` (not measurements).

## Recovery

| Error code | Cause | Next step |
| --- | --- | --- |
| `OUTPUT_EXISTS` | output file or directory already holds other results | new `--out`; `--overwrite` only to replace it |
| `BASE_CHANGED` | `--expect-base-hash` differs from the base | re-read the base (`--describe`) and rebuild the edit |
| `PROJECT_OP_INVALID` | `op_code` `UNKNOWN_OP`, `NOT_FOUND`, `DUPLICATE_ID`, `BAD_ARGUMENT`, `IN_USE`, `UNIT_UNKNOWN`, `UNIT_INCOMPATIBLE` at `op_index` | fix that op; check ids with `--describe` |
| `PROJECT_OP_INVALID` with `TRANSFORM_REFUSED` | transform selector, frame or pivot cannot be applied | inspect the named operation and its problems |
| `PROJECT_REJECTED` | the Engine check refuses the edited Project; `problems` list codes and locations | change the offending values |
| `RUN_REFUSED` (`run.status`) | the edit was written but the Engine refused the run | read `run.problems`; adjust geometry or models |
| `BUDGET_EXCEEDED` | more evaluations than `max_evaluations` | fewer values or a deliberate larger budget |
| `SPEC_INVALID`, `TARGET_UNKNOWN`, `COLUMN_UNKNOWN`, `NOMINAL_MISMATCH`, `STEP_REQUIRED` | spec structure, unknown target, objective / constraint / derived column the spec cannot produce, `set_*` parameter without its base `nominal`, relative step at nominal 0 | fix the spec as the message states; `known` lists the valid ids or columns |
| `INPUT_INVALID` | a workflow input cannot build a design | change the named input |
| `CATALOG_NOT_AVAILABLE` | no catalog release installed | `os-optics catalog sync` (or `catalog import FILE`) |

An `ERROR` sample inside a study is recorded with its stage and code in `table.csv`; the
other samples still run.
