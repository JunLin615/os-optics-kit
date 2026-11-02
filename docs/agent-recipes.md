# Project-editing recipes for agents

[Agent guide](../AGENT_GUIDE.md). These instructions are English-only; customer
walkthroughs remain available in [English](en/getting-started.md) and
[Simplified Chinese](zh-CN/getting-started.md). These recipes use os-optics
**0.10.0**, the version recorded in [engine-baseline.json](../engine-baseline.json); use
the matching [installation](en/installation.md).
The same edits by ID, with unit conversion, a change record and retry safety, are
available without writing Python through `examples/edit_project.py`
([workflow recipes](workflow-recipes.md#edit-a-project-by-id)).

## Adapt a native beam-expander project

First create the native run from the repository root:

```bash
python examples/beam_expander.py --out run-example
```

Keep `run-example/project.json` as the baseline. The following complete recipe
edits four supported fields by ID, validates the resulting document through the
engine, and writes a separate run. Save it as `run-recipe.py` in the repository
root. It uses the same compact summary and exit behavior as the existing examples.

```python
from pathlib import Path

from os_optics.local import load_project, run_optical_project
from examples.result_summary import emit_cli, summarize_run


def by_id(rows, field, identifier):
    matches = [row for row in rows if row[field] == identifier]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one {field}={identifier}")
    return matches[0]


def workflow():
    document = load_project(Path("run-example/project.json")).payload()

    # Wavelength in nm; changing it may exceed a catalog model's supported band.
    source = by_id(document["components"], "instance_id", "SRC")
    source["source_model"]["wavelength_nm"] = 785.0

    # Element position in design-frame mm; preserve its orientation.
    lens = by_id(document["components"], "instance_id", "L2")
    lens["nominal_pose"]["position_mm"][0] += 1.0

    # Observation position in the same frame, independent of element motion.
    plane = by_id(document["observations"], "observation_id", "output")
    plane["pose"]["position_mm"][0] += 1.0

    # Replace the original value/tolerance constraint with a radius interval.
    target = by_id(document["targets"], "target_id", "expanded_waist")
    for key in ("target_value", "tolerance_abs", "tolerance_rel", "minimum", "maximum"):
        target.pop(key, None)
    target.update(minimum=1.8, maximum=2.2, unit="mm")

    project = load_project(document)
    run_optical_project(project, output_dir="run-adapted")
    return summarize_run("run-adapted")


if __name__ == "__main__":
    raise SystemExit(emit_cli(workflow))
```

Run and inspect the saved result:

```bash
python run-recipe.py
python examples/result_summary.py run-adapted
```

For a controlled single-parameter change, keep only the relevant edit and use a
fresh output directory. These example IDs belong to the generated beam expander;
inspect your own project and select its IDs rather than indexing arrays by position.
The target metric remains `waist_radius_x_mm`: an interval on the propagated
beam's waist radius, not its radius at the observation plane. Changing the metric
requires selecting a compatible unit and bound.

Schema validation by `load_project` checks the document; it is not a physics or
model-validity pass. The run evaluates those and its summary preserves all outcomes.
Check `run-adapted/project.json` to confirm the saved inputs, `targets` to assess
the interval, and `issues` / `model_scope_issues` before using the result.
The original pinned catalog references and original run remain unchanged.

On catalog `2026-10-28.1`, the combined edits above produce a waist radius near
`1.1149 mm`: the `1.8–2.2 mm` interval is **not met**, so the recipe and saved
summary exit with code `1`. This is a completed simulation with a failing design,
not a command error. Inspect the result before deciding which edit to revise;
different catalog releases may change the numerical outcome.

To exercise a non-passing target, change the interval to `minimum=0.0,
maximum=0.001` and write to `run-adapted-strict`. Do not relax a customer's target
merely to obtain `PASS`. Changing orientation requires a valid quaternion; adding
or replacing catalog optics requires checking their supported model and band in
the [catalog guide](en/catalog.md).

## Adjust the catalog-free ideal focus

For just the acceptance bound, no source edit is needed:

```bash
python examples/ideal_lens_focus.py --out run-ideal-bound --maximum-radius-mm 0.01
```

The default layout focuses to a beam radius of `0.016934085944977664 mm`, so the
0.01 mm bound is not met: `FAIL`, exit `1`.

For a different ideal optical layout, read only
[`build_project`](../examples/ideal_lens_focus.py) and adapt its explicit
`B.laser`, `B.thin_lens`, `B.observation_plane`, and `Target` parameters in a copy.
Its builder positions are mm; the wavelength is nm; directions and normals are
unit vectors. This low-level request differs from the native catalog project
schema above. Preserve the full request/result/rays, and summarize the public
`results_from_engine` result as the example does.

## Process measured CSV data

Use the [measurement workflow](en/measurements.md) for background subtraction,
reference normalization, and response fitting without a simulation. Supply
explicit column names and units; use a fresh output directory. It preserves input
bytes, every row's exclusion reasons, and fit residuals. Compare the fitted values
and your configured RMSE bound alongside the diagnostics before drawing a
scientific conclusion. The two synthetic datasets are demonstrations, not
independent agent-performance evaluation tasks.
