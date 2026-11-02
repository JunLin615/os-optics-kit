# Results, visualization, and export

[User guide](README.md) · English · [简体中文](../zh-CN/results.md)

One run produces a complete record of the design and its simulation. The results API reads that record directly, so inspecting or exporting data does not rerun the simulation.

## Read the run directory

| File | Contents |
| --- | --- |
| `manifest.json` | Overall status, target outcomes, identities, and file hashes |
| `project.json` | The optical project that was run |
| `engine_request.json` | The input passed to the physics engine |
| `simulation_result.json` | Target values, beams, observations, paths, power budget, and issues |
| `ray_data.json` | Traced chief and pupil rays, including surface crossings and optical path lengths |
| `local_validation.json` | Local Validation record with project, catalog, engine, and result identities |
| `optical_scene.json` | Structured optical scene |
| `optical_scene.glb` | 3D scene in glTF binary format |

```bash
os-optics results describe run
os-optics results monitor run output
os-optics project verify run
```

`output` is the observation-plane identifier in the beam-expander example. Use `results describe` to discover the planes available in your own run.

## Interpret the result

These compact controls are available in 0.10.0; install the matching package as
described in [version matching](installation.md#match-the-examples-to-the-installed-version).

When a quantity lies outside a component model's declared wavelength, angle or beam
domain, the targets that depend on it are assessed `INDETERMINATE` and carry the
model-scope issue code in `applicability_issue_codes`. The quantity keeps its value,
unit and bounds, so you can read the number while the assessment states that it does
not establish validity outside the declared domain. Keep the calculation inside the
domain when you need a `MET` or `UNMET` assessment; see the
[metasurface validation record](../interop-validation.md) for worked cases.

```bash
os-optics results targets run
os-optics results freshness run --project project.json
```

Read these dimensions separately:

| Dimension | What to inspect | Meaning |
| --- | --- | --- |
| Computation | `outcome.execution`: completion, termination and budgets | Whether the requested computation completed, and which limits were exhausted |
| Quantity | The metric's value, unit, validity, bounds and applicability issues | A usable point value, an interval, an unavailable quantity or a quantity outside its model |
| Customer requirement | Each target's `assessment` and `outcome.requirements` | `MET`, `UNMET`, `INDETERMINATE` or `NOT_APPLICABLE`; no targets means no requirement was assessed |
| Freshness | `CURRENT`, `HISTORICAL` or `UNDETERMINED`, with reason codes | Whether the saved run corresponds to the supplied Project |
| Commercial result | The response to configuration or order commands | What can be purchased or ordered under that operation's conditions |

An aggregate `PASS` does not answer all five questions. A completed run can have an
unmet optional target and still contain useful quantities. Read applicability on the
quantity or target you use: an unrelated scope issue does not invalidate every
quantity. Keep a bounded value as an interval, and preserve an indeterminate target
when its interval cannot determine the requested comparison.

A physical or computation-budget edit is reported as `PHYSICS_CHANGED`; a target-only
edit as `TARGETS_CHANGED`. Preserve the original run and its hashes. Changing targets
does not turn an old target assessment into a new one. Another engine build can leave a
run `CURRENT` when both Project and lowered input match; inspect `engine_build_matches`
when reproducing the calculation. See [comparison recipes](../workflow-recipes.md#compare-and-freshness).

`project verify` checks recorded artifact hashes and consistency. It does not compare
the calculation with an independent optical solution or an experiment. The analytical
references in the worked examples provide separate checks with stated assumptions.

The read command `results targets` exits 0 when it answers, including an unmet target.
`results freshness` uses 0 for `CURRENT`, 1 for `HISTORICAL` and 2 for `UNDETERMINED`.
Read structured assessments to decide a scientific task's result; command completion
alone is insufficient.

## Compact summaries from the examples

Run the examples from the repository root:

```bash
python examples/beam_expander.py --out run-example
python examples/ideal_lens_focus.py --out run-ideal-focus
```

Each example writes one JSON summary to standard output. Structured keys remain in English; diagnostics go to standard error. The default output includes the overall `status`, `engine_status`, nullable `validation_status`, and target identifiers, metrics, actual values, units, applicable bounds, outcomes, and available messages or issue codes. It also preserves actionable `issues`, `model_scope_issues`, and `element_models`, along with `identity` and `integrity` information. The compact `power_budget`, when available, contains scalar totals and `total_sinks`; the full ledger is available with `--details`.

The `artifacts` and `exports` fields point to complete data on disk. The beam-expander example saves a native local run and an arrivals CSV, and provides a `view_command` for its 3D scene. The ideal-lens example runs without a catalog download and saves its request, simulation result, rays, and arrivals under `--out`. Its low-level result does not include a native local-run manifest or a 3D viewer command. The default output directories are `run-example` and `run-ideal-focus`, respectively.

Read a previously saved native local run without repeating the simulation:

```bash
python examples/result_summary.py run-example
python examples/result_summary.py run-example --details
```

The helper checks saved artifact integrity before summarizing the run through the public results API. It preserves execution, requirement and quantity information alongside the legacy status fields. This command expects the native local-run artifacts produced by the beam-expander example or `os-optics project run`; the ideal-lens output is a low-level result bundle. The reusable Python functions in [result_summary.py](../../examples/result_summary.py) are `summarize_results(results, *, artifacts=None, details=False)` and `summarize_run(directory, *, details=False)`. Both return a dictionary suitable for JSON output.

The example scripts and saved-run summary command use `requirement_outcome` for exit
behavior. The raw aggregate `status` remains in the record alongside
`outcome.execution` and `outcome.requirements`. These are the helper exit codes:

Exit `0` describes the selected requirements, not execution completeness. For example,
a budget-limited run with no targets can retain `execution.outcome: PARTIAL`, aggregate
`status: UNKNOWN` and `requirement_outcome: NOT_APPLICABLE` while exiting `0`.
Inspect execution and quantity validity before using its values.

| Exit code | Reported result |
| --- | --- |
| `0` | selected requirements met or not applicable |
| `1` | Selected requirement unmet |
| `2` | Selected requirement indeterminate |
| `3` | Selected quantity outside its model |
| `4` | Input, artifact-integrity, runtime, or command-usage error |

Design outcomes, including a non-passing result, still produce a JSON summary and preserve complete data. Input or runtime exceptions produce a JSON error on standard error, leave standard output empty, and exit `4`. A saved run with failed or cancelled execution instead retains its JSON result summary on standard output and also exits `4`. These example error codes are separate from the engine CLI's file and usage error codes.

The ideal-lens target can be changed with `--maximum-radius-mm` (default `0.05`). These commands exercise the default target, a stricter target intended to fail, and invalid input:

```bash
python examples/ideal_lens_focus.py --out run-focus-default
python examples/ideal_lens_focus.py --out run-focus-strict --maximum-radius-mm 0.001
python examples/ideal_lens_focus.py --maximum-radius-mm -1
```

The radius bound must be finite and nonnegative; `nan` is also invalid. Inspect `$LASTEXITCODE` in PowerShell or `$?` in a POSIX shell immediately after each command. Use `--details` with either example to include the full power ledger in its summary; full ray data remains available through the saved artifact paths.

## View and share

```bash
os-optics view run
os-optics view run --write-html view.html
os-optics view run --snapshot shots
```

The browser viewer includes camera presets, selectable components, beam visibility controls, and English/Chinese labels. Snapshots include orthographic PNG views, `scene_summary.json`, and a legend.

## Export tables

```bash
os-optics export arrivals run --format csv --out arrivals.csv
os-optics export rays run --format jsonl --out rays.jsonl
os-optics results columns arrivals
os-optics results metrics --name waist_distance_x_mm
```

Exports include monitors, arrivals, rays, paths, segments, power budgets, superpositions, wavefronts, irradiance, and metric definitions. Use `export --help` for formats and selectors. Wavefront exports use traced optical-path differences; irradiance maps use the paraxial Gaussian superposition model.

From Python:

```python
from pathlib import Path
from os_optics.results import load_run

results = load_run("run")
print(results.describe())
print(results.power_budget())
Path("arrivals.csv").write_bytes(results.export("arrivals", "csv"))
```

## Units and conventions

The API uses millimetres for length, nanometres for vacuum wavelength, degrees for angles, and watts for power. Gaussian beam size `w` is the **1/e² intensity radius**. Coordinates are right-handed; a transmissive element's local `+z` is its optical axis, while a reflector's local `+z` is the coated-face outward normal. Export column names and metric descriptors identify the relevant units.

Next: [Continue from design to hardware](hardware.md).
