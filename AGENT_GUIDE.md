# Agent guide

Select one task below and read its linked recipe. Human tutorials share the same
scripts: [English](docs/en/README.md) · [简体中文](docs/zh-CN/README.md).

## Version and environment

These examples and workflow helpers use the published **0.10.0** release of
os-optics, the version recorded in [engine-baseline.json](engine-baseline.json)
with its wheel and source hashes and the tested environment. Install the exact version
in a fresh environment and run the alignment checks to reproduce the verified result.
See [installation](docs/en/installation.md) and [alignment](docs/engine-alignment.md).

Run scripts from the repository root in a Python 3.11+ virtual environment. Install
`requirements-examples.txt` for plotting. External tools use separate environments
and pinned dependencies in [the interop guide](docs/en/interop.md).

## Select a task

| Task | Command | Detail |
| --- | --- | --- |
| Installation check without catalog | `python examples/ideal_lens_focus.py --out run-focus` | [Results](docs/en/results.md) |
| Editable catalog design | `python examples/beam_expander.py --out run-example` | Saves `project.json` and native run |
| Discover object IDs, units and axes | `python examples/edit_project.py run-example/project.json --describe --ids L2` | [Find objects](docs/workflow-recipes.md#find-objects) |
| Edit, check, run, read | `python examples/edit_project.py BASE.json --edits OPS.json --out NEW.json --run NEW-run` | [Edit and retry](docs/workflow-recipes.md#edit-a-project-by-id) |
| Check a Project | `os-optics project check NEW.json` | [Native controls](docs/workflow-recipes.md#native-project-controls) |
| Compact targets | `os-optics results targets NEW-run` | [Interpretation](docs/en/results.md#interpret-the-result) |
| Check saved-run freshness | `os-optics results freshness NEW-run --project NEW.json` | [Freshness](docs/workflow-recipes.md#compare-and-freshness) |
| Parameter sweep | `python examples/sweep_parameters.py SPEC.json --base BASE.json --out STUDY` | [Sweep](docs/workflow-recipes.md#sweep) |
| Compare saved results | `python examples/compare_runs.py RUN_A RUN_B --project CURRENT.json --out CMP` | No simulation |
| Plot and report | `python examples/plot_results.py sweep STUDY --y target:ID` then `python examples/build_report.py STUDY --project CURRENT.json --plots STUDY/plots` | [Recipes](docs/workflow-recipes.md) |
| Sensitivity | `python examples/sensitivity_analysis.py examples/specs/sensitivity_expander.json --out run-sensitivity` | [Sensitivity](docs/workflow-recipes.md#sensitivity) |
| Seeded tolerances | `python examples/tolerance_study.py examples/specs/tolerance_expander.json --out run-tolerance` | [Tolerance](docs/workflow-recipes.md#tolerance) |
| Beam, polarization or alignment design | `python examples/beam_expansion_focus.py --out run-expansion` | [Three workflows](docs/en/workflows.md#parameterized-workflows) |
| Catalog selection or detector CSV | `python examples/catalog_candidates.py --help` / `python examples/analyze_measurements.py --help` | [Catalog recipe](docs/workflow-recipes.md#catalog-candidates) · [Measurements](docs/en/measurements.md) |
| Optimization, diffraction, quantum or metasurface | Select one topic's environment and example | [Interop](docs/en/interop.md) |

Workflow inputs are discoverable with `--list-parameters`; use unit-bearing values,
for example `--gap-error-mm 250um`, and bounded studies such as
`--study gap_error_mm=-1..1:5`. `--no-studies` runs only the nominal design.

## Interpret before deciding

Read computation completion, quantity validity, optional target assessment and
freshness separately. Engine target assessments are `MET`, `UNMET`,
`INDETERMINATE` or `NOT_APPLICABLE`. Retain bounds and scope diagnostics: a bounded
quantity is an interval, and an unrelated unavailable quantity does not invalidate
every result. No configured target means no requirement was assessed.

Study `PASS`/`FAIL` classes summarize selected targets, not universal scientific
approval. Keep every sample, distinguish objective validity from constraint
feasibility, and report `NO_FEASIBLE` when appropriate. Fractions state their
denominator; tolerance fractions describe simulated samples under the declared
distributions. See [result meanings and exits](docs/en/results.md).

Saved results retain their identities after edits. Use engine freshness reasons to
identify physical, target or computation-budget changes. Inspect older results as
historical evidence and rerun when the task needs the edited design. Hash checks
establish artifact consistency; analytical or experimental checks establish other
claims. Purchasing eligibility is reported by the commercial operation separately.

## Inputs, limits and recovery

Establish wavelength (nm), power (W), Gaussian 1/e² intensity **radius** and position
(mm), object IDs, poses and the customer's actual target bounds. Coordinates are
right-handed; quaternions are `[w, x, y, z]`. Discover local axes before translating
or rotating. Example geometry is a starting point to adapt.

Set both the study's `max_evaluations` and the Project's computation budget.
[Budget recipes](docs/workflow-recipes.md#computation-budgets) explain their separate
effects. Inspect limited or partial results before increasing a limit.

Scripts print compact JSON and preserve details in files. Invalid edits are refused
atomically. The helper preserves BASE and records the new output; repeating its
identical command returns `already_applied`. Native update uses `--ops` and
`--expect-hash`; the helper uses `--edits` and `--expect-base-hash`. After an interrupted
native response, inspect the saved project/hash before retrying a relative transform.
See [recovery](docs/workflow-recipes.md#recovery). Report the command, versions, units,
selected assessments, freshness and artifact paths.
