# Design studies and parameterized workflows

[User guide](README.md) · English · [简体中文](../zh-CN/workflows.md)

The workflow scripts in `examples/` take a design from a first run to a documented
study: edit components and targets by ID, sweep parameters, compare saved runs, plot
and report the results, and estimate sensitivities and tolerances. Every step saves its
projects and runs, prints a compact JSON summary and keeps the complete data in files.
Three parameterized workflows apply the same steps to beam expansion and focusing,
polarization control and alignment.

## Before you start

The examples and helpers use os-optics **0.10.0**, recorded in
[engine-baseline.json](../../engine-baseline.json). Install it as described in
[version matching](installation.md#match-the-examples-to-the-installed-version).
Run commands from the repository root in an isolated environment, plus
`requirements-examples.txt` for plots.

Sections 1–3 use catalog components and need `os-optics catalog sync`; sections
4 and 5 use catalog-free ideal elements. The quoted numerical examples are the
**reference results** of the runs in the linked validation documents, using catalog
release `2026-10-28.1` where applicable; the alignment checks reproduce them on this
release. Read each new run's own identities and assessments: catalog contents and
result identities can differ when the engine or catalog changes.

### Edit, check, run and read compact results

```bash
python examples/beam_expansion_focus.py --out run-controls --no-studies
os-optics project check run-controls/nominal/project.json
os-optics results targets run-controls/nominal/run
os-optics results freshness run-controls/nominal/run --project run-controls/nominal/project.json
```

`project check` validates and lowers a Project without running it. `results targets`
returns execution, optional requirements, target assessments and applicability issues
without repeating computation. `results freshness` compares saved identities with the
current Project. The [native editing recipe](../workflow-recipes.md#native-project-controls)
covers atomic refusal, expected hashes and interrupted-response retries.

### Choose computation limits

A study's `max_evaluations` limits the number of designs; each Project's
`compute_budget` limits work inside one design. The
[working budget example](../workflow-recipes.md#computation-budgets) limits the three-lens
workflow to one interaction per path, then raises the limit to eight. The limited run
retains its artifacts and reports partial execution and indeterminate affected targets.
Changing the budget makes the old result historical. Inspect those outcomes before
choosing a larger limit, and preserve both runs for comparison.

## 1. Adapt a design

Start from the catalog beam expander: two plano-convex lenses (f = 50 mm and 100 mm)
expand a 780 nm Gaussian beam with a 1 mm waist radius. Its target `expanded_waist`
asks for a waist radius of 1.9961 mm within ±1 %.

```bash
python examples/beam_expander.py --out run-tutorial/base
python examples/edit_project.py run-tutorial/base/project.json --describe --ids L2
```

The run passes with 1.99607 mm. `--describe` lists every component, observation plane
and target by ID; with `--ids` it shows the current pose of L2, its local axes in the
design frame, its catalog product with the valid wavelength band, and the operations that
can change it.

Look for an alternative second lens of the same diameter near 100 mm focal length:

```bash
python examples/catalog_candidates.py --category LENS --form PLANO_CONVEX_LENS --wavelength-nm 780 \
    --range focal_length_mm=95..105 --range outer_diameter_mm=12.7..12.7 --near focal_length_mm=100 \
    --limit 8 --columns optical_sku,focal_length_mm,outer_diameter_mm,working_band_nm
```

Of the 471 plano-convex lenses with a model valid at 780 nm, eight match: five with
f = 100 mm, two fused-silica lenses with f = 100.3 mm and one with f = 100.4 mm.

[`beam_expander_swap_l2.json`](../../examples/specs/beam_expander_swap_l2.json) replaces L2
by the 100.3 mm fused-silica lens and adds a second target, `output_beam_radius`: the
1/e² beam radius at the output plane, with the same value and tolerance.

```bash
python examples/edit_project.py run-tutorial/base/project.json --edits examples/specs/beam_expander_swap_l2.json \
    --out run-tutorial/swapped.json --run run-tutorial/swapped-run
```

The edit is validated by the engine before anything is written. The new project goes to
`run-tutorial/swapped.json`, the change record with every field's previous and new value
to `run-tutorial/swapped.changes.json`, and the original project stays untouched. The run
completes with `FAIL` (exit code 1): `expanded_waist` drops to 1.4407 mm while
`output_beam_radius` passes with 2.0102 mm. The two targets measure different things. The
waist radius describes collimation: the output beam is nearly collimated, with a Rayleigh
range of metres, so a small focal-length change moves its waist far away and changes the
waist size strongly. The beam radius at the plane barely changes.

Edits are written as a list of operations by ID. Numbers are in the field's unit (`_mm`,
`_nm`, `_w`, `_deg`) or carry their own, for example `{"value": 540, "unit": "um"}`. A
`transform` operation moves or rotates selected objects in the design frame or in a
component's own frame. Repeating an identical command reports `already_applied` and
changes nothing.

## 2. Sweep the lens spacing

[`beam_expander_l2_sweep.json`](../../examples/specs/beam_expander_l2_sweep.json) moves L2
along the beam in twelve steps from −1 mm to +2 mm, keeps samples whose `expanded_waist`
passes, and selects the one closest to the target value. The budget of 20 evaluations is
checked before the first run.

```bash
python examples/sweep_parameters.py examples/specs/beam_expander_l2_sweep.json --base run-tutorial/swapped.json \
    --out run-tutorial/l2-sweep
python examples/plot_results.py sweep run-tutorial/l2-sweep --y target:expanded_waist
```

| L2 shift (mm) | −1 | −0.5 | 0 | 0.25 | 0.4 | 0.5 | 0.54 | 0.55 | 0.6 | 0.7 | 1 | 2 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| expanded_waist (mm) | 0.7323 | 0.9911 | 1.4407 | 1.7410 | 1.9034 | 1.9780 | 1.9964 | 1.9999 | 2.0098 | 1.9923 | 1.7162 | 0.8340 |
| outcome | FAIL | FAIL | FAIL | FAIL | FAIL | PASS | PASS | PASS | PASS | PASS | FAIL | FAIL |

Five of the twelve samples pass (pass fraction 5/12); the best is a shift of +0.54 mm
with 1.99645 mm. Each sample keeps its own project and run under
`run-tutorial/l2-sweep/samples/`, and `table.csv` lists parameter values with units,
outcome, every target's value and status, identities (hashes, engine build and catalog
release) and run paths. The plot
`run-tutorial/l2-sweep/plots/sweep_target_expanded_waist.png` (and `.svg`) shows every
sample with its outcome marker and the target band.

Apply the best shift, given in micrometres:

```bash
python examples/edit_project.py run-tutorial/swapped.json --edits examples/specs/beam_expander_move_l2.json \
    --out run-tutorial/final.json --run run-tutorial/final-run
```

The run passes with `expanded_waist` 1.99645 mm, identical to the sweep sample.

## 3. Compare and report

Compare the three runs against the final project, without running anything again:

```bash
python examples/compare_runs.py run-tutorial/base run-tutorial/swapped-run run-tutorial/final-run \
    --project run-tutorial/final.json --out run-tutorial/compare
```

Targets and planes are aligned by ID: `output_beam_radius` appears as missing in the base
run. The input differences are the L2 position (152.841 mm → 153.381 mm), the L2 product
and the added target. Each run's freshness is stated against the final project: the final
run is `CURRENT`, the other two are `HISTORICAL` (the engine supplies the change reasons), so the base run's
pass reads `HISTORICAL PASS`: it belongs to the earlier design.

Assemble a report of the sweep, with its plots and the lens-swap edit:

```bash
python examples/build_report.py run-tutorial/l2-sweep --project run-tutorial/final.json \
    --plots run-tutorial/l2-sweep/plots --changes run-tutorial/swapped.changes.json
```

`run-tutorial/l2-sweep/report/report.md` (with `report.json`) lists the inputs and the
edit, the outcome counts with their denominators, the best sample, the diagnostics, the
freshness of every sample, plot links and all artifacts with SHA-256 checksums. Pass
several run directories instead of a study to report on individual runs.

## 4. Sensitivity and tolerance

These studies use the catalog-free expander-and-focus design of section 5.1, built
directly from the specification file.

```bash
python examples/sensitivity_analysis.py examples/specs/sensitivity_expander.json --out run-tutorial/sensitivity
```

Each parameter is perturbed by ±h at two step sizes; the script reports central
differences, the agreement between the two steps, the second difference, and a ranking
scaled by a stated tolerance for each parameter (for example 50 µm for an axial lens
position). The expanded radius depends almost only on the L2 axial position:
0.006668 mm/mm, with a relative difference below 1e-10 between the two steps. The spot
radius responds most to the focus-plane position, but the plane sits 36.5 µm before the
focused waist, close to the minimum of the curve. The first derivative (−0.000497 mm/mm) is therefore flagged
`NEAR_EXTREMUM`, and the second difference (0.01357 mm/mm²) describes the response. The
tilt and decenter of the ideal focusing lens change the spot radius only at second order.
Results are in `summary.json` and `sensitivity.csv`, with bar and tornado plots under
`plots/`.

[`sensitivity_expander_band_edge.json`](../../examples/specs/sensitivity_expander_band_edge.json)
starts at 1099.8 nm, 0.2 nm below the lenses' 1100 nm band limit. The +0.5 nm sample lies
outside the band and is classified out of model, so that step uses a backward difference
and is labelled as such; the nominal spot radius (0.02334 mm) misses its 0.02 mm maximum
and stays in the output as `FAIL`.

```bash
python examples/tolerance_study.py examples/specs/tolerance_expander.json --out run-tutorial/tolerance
python examples/tolerance_study.py --verify-seed run-tutorial/tolerance
python examples/tolerance_study.py examples/specs/tolerance_expander.json --out run-tutorial/tolerance-zero \
    --zero-tolerance --samples 5
```

The tolerance study draws 100 designs with seed 20261018: L2 axial position uniform within
±0.5 mm, focus-plane position uniform within ±0.3 mm and source waist radius normal with
σ = 5 µm truncated at 3σ, all independent. The sampled offsets are saved in `samples.csv`
before the first run. 92 of the 100 samples meet all three targets; the 8 others miss
the expanded-radius tolerance of 1.5 mm ± 2 % (sampled values 1.462–1.540 mm). The pass
fraction 92/100 is the fraction of simulated samples under these distributions.
`--verify-seed` redraws the samples and confirms the same `samples.csv` bytes. In the
third command every offset is zero, and all five samples reproduce the nominal project
and result hashes exactly.

## Parameterized workflows

Each workflow builds its design from your inputs, runs it, runs one-parameter studies,
compares every study with the nominal run, plots the targets that change, checks the
engine values against an independent closed-form calculation and writes a report. Show
all inputs with units and defaults with `--list-parameters`. Inputs accept a unit suffix
(`--gap-error-mm 250um`); `--study NAME=V1,V2` or `--study NAME=START..STOP:COUNT`
replaces the default studies, and `--no-studies` runs the nominal design only.

Output layout of `--out DIR`: `DIR/workflow.json` (full summary), `DIR/nominal/` (project,
run, closed-form comparison, power plot), `DIR/studies/<parameter>/` (one project and run
per sample, `table.csv`, comparison, plots, report) and `DIR/report/report.md`, which links
everything. The exit code reflects the nominal design (0 PASS, 1 FAIL, 2 UNKNOWN,
3 OUT_OF_MODEL); study samples that miss are counted in the summary.

### 5.1 Beam expansion and focusing

```bash
python examples/beam_expansion_focus.py --out run-tutorial/expansion
python examples/beam_expansion_focus.py --out run-tutorial/expansion-strict --no-studies --max-spot-radius-mm 10um
```

- **Design:** a Gaussian source (780 nm, 0.5 mm waist radius) passes a Keplerian expander
  of ideal thin lenses L1 (f1 = 50 mm) and L2 (f2 = 150 mm) and a focusing lens L3
  (f3 = 100 mm). `gap_error_mm` detunes the L1–L2 spacing; `focus_plane_shift_mm` moves the
  focus plane. Lengths are mm, wavelength nm.
- **Model:** paraxial Gaussian beam through ideal thin lenses (no aberration or
  dispersion), 25 mm clear aperture, lenses declared valid for 400–1100 nm.
- **Checks:** `expanded_radius` at the plane 50 mm after L2 within ±2 % of
  source waist radius × f2/f1 = 1.5 mm; `spot_radius` at the focus plane at most 0.02 mm;
  `waist_location` within ±0.5 mm of that plane. Nominal: 1.50112 mm, 0.016552 mm,
  −0.0365 mm, all `PASS`. The complex-q reference agrees within 6e-14 mm.
- **Studies:** the spot passes only for |gap error| ≤ 1 mm and for f3 ≤ 100 mm; at 1064 nm
  it grows to 0.02258 mm (`FAIL`); at 1300 nm the lenses are outside their declared band
  and the sample is `OUT_OF_MODEL`; a 3 mm source waist passes with the warning
  `GAUSSIAN_TRUNCATION`, a 4 mm waist is `OUT_OF_MODEL` (`GAUSSIAN_SEVERE_TRUNCATION`).
- **Unmet example:** the second command tightens the spot bound to 10 µm; the spot radius
  of 0.016552 mm fails it and the command exits 1.

### 5.2 Polarization control

```bash
python examples/polarization_control.py --out run-tutorial/polarization
python examples/polarization_control.py --out run-tutorial/polarization-1064 --no-studies --wavelength-nm 1064
```

- **Design:** a linearly polarized 1030 nm source (1 mW, polarization at 0°) passes a
  half-wave plate at 22.5° and a linear polarizer at 45°. Angles are degrees, measured
  from design +Y toward +Z about the beam.
- **Model:** ideal thin Jones elements: a zero-order half-wave retarder (transmittance
  0.99) characterized for 1000–1060 nm, its retardance scaling as 1030 nm / λ; a linear
  polarizer with transmittance 0.9 and extinction ratio 1000.
- **Checks:** `transmitted_power` at the detector at least 0.8 mW (nominal 0.891 mW);
  `output_azimuth` after the plate 45° ± 0.5° (nominal 45°). Independent Jones-calculus and
  Malus' law calculations reproduce every compared value within their tolerances; the
  largest power difference is 3.4e-15 relative.
- **Studies:** turning the plate from 0° to 67.5° keeps both targets only at 22.5°; at 15°
  and 30° the power (0.831 mW) still passes while the azimuth (30°, 60°) does not; at 67.5°
  the polarizer passes only the extinction floor, 0.89 µW. Wavelengths of 1064 and 1100 nm
  lie outside the plate's characterized band.
- **Diagnostic example:** at 1064 nm both targets read `PASS` (0.88988 mW, 44.93°), yet the
  run reports `MODEL_WAVELENGTH_OUTSIDE_BAND` at the plate. The workflow classifies the
  design `OUT_OF_MODEL` and exits 3, because the plate's retardance is characterized only
  for 1000–1060 nm.

### 5.3 Alignment analysis

```bash
python examples/alignment_analysis.py --out run-tutorial/alignment
python examples/alignment_analysis.py --out run-tutorial/alignment-synthetic --no-studies \
    --mirror-tilt-local-y-mrad 1 --synthetic-measurements
```

- **Design:** a 633 nm source 100 mm before a flat fold mirror at 45° angle of incidence;
  observation planes `near` and `far` 100 mm and 400 mm after the mirror (12.5 mm radius).
  Mirror tilts (mrad) turn the mirror about its own face axes; source offsets (mm) and tilts
  (mrad) move the input beam.
- **Model:** exact 3D chief-ray trace with a flat mirror of declared reflectance. The
  reference is exact vector reflection and plane intersection, plus the small-angle rule
  that a mirror tilt θ deflects the beam by 2θ (2θ·cos 45° out of the plane of
  incidence).
- **Checks:** beam position within ±0.1 mm on both planes and direction within ±0.5 mrad
  on `far`; the analysis reports positions and the two-plane pointing angle
  1000·atan(Δ / 300 mm) in mrad.
- **Studies:** a 0.25 mrad mirror tilt moves the beam 0.05 mm on `near` (`PASS`) and
  0.20 mm on `far` (`FAIL`), with 0.5 mrad pointing; at 15 mrad the beam reaches the edge
  of the far plane (`INCOMPLETE_ENVELOPE`); at 60 mrad it misses the far plane, which
  reports `OBSERVATION_NOT_REACHED` and no values, so that sample is `UNKNOWN`. A pure
  0.15 mm source offset shifts both planes equally and leaves the pointing at 0.
- **Synthetic-measurement example:** the second command adds planes at 100–400 mm, takes
  the engine's beam positions, adds invented residuals and an invented 0.05 mm camera zero,
  and labels every row `SYNTHETIC` in
  `run-tutorial/alignment-synthetic/synthetic/synthetic_measurements.csv`. The
  [measurement analysis](measurements.md) script fits a pointing of 1.9973 ± 0.0182 mrad
  against the expected 2 mrad. The 1 mrad tilt misses the position tolerance, so the
  command exits 1.

## Read the results

Separate execution, quantity validity, selected target assessments and freshness.
`MET`, `UNMET`, `INDETERMINATE` and `NOT_APPLICABLE` are engine assessments; the
workflow's `PASS`, `FAIL`, `UNKNOWN`, `OUT_OF_MODEL` and `NOT_APPLICABLE` classes route
the selected requirements. Scope issues affect the quantities that depend on them.
An unrelated out-of-model quantity does not invalidate every useful result.

Every requested sample remains visible, including invalid, partial and indeterminate
samples. Objective validity and requirement feasibility are separate fields. Exact
objectives and finite differences use valid point values; bounded quantities retain
their intervals. A sweep reports no feasible sample explicitly instead of selecting
an invalid optimum. Pass fractions divide by all requested samples; the fraction among
evaluable samples has a separate denominator. A tolerance pass fraction is a simulated
sample result under the specified distributions.

Comparison delegates freshness to the engine. Physical and budget changes produce
`PHYSICS_CHANGED`; target edits produce `TARGETS_CHANGED`. Historical results retain
their identities and original assessments. See [result interpretation](results.md)
and the [workflow recipes](../workflow-recipes.md).

## Benchmark provenance

Task families of the
[Free-Space Optical Experiment Benchmark](https://github.com/JunLin615/free-space-optical-experiment-benchmark)
(JunLin615 and contributors, commit `e72ad3a9b6be12c7f0dbb00121fda23289c4e908`) informed
the three workflows. Its case content is licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Only the task families are used:
the designs, numbers and checks were written for these examples, and no case text, gold
answer or scoring material is copied.

| Workflow | Benchmark case | Case revision | Informed |
| --- | --- | --- | --- |
| Beam expansion and focusing | SEED-2-2 Designing a fivefold afocal beam expander | 0.2.2 | two-lens Keplerian expansion with magnification f2/f1 |
| Beam expansion and focusing | SEED-7-4 Independently verify a fivefold beam expander | 0.1.0 | verification by beam size at a plane and by waist location |
| Polarization control | SEED-1-7 Half-wave plate and linear polarization | 0.2.1 | a half-wave plate turns linear polarization by twice its angle |
| Polarization control | SEED-1-8 Continuously adjustable free-space attenuation | 0.1.0 | a rotatable half-wave plate before a fixed polarizer as an attenuator |
| Alignment analysis | SEED-1-2 Establishing an axis with two steering mirrors and two apertures | 0.1.0 | position and direction checked at two separated planes |
| Alignment analysis | EX-ALIGN-01 Two-iris alignment diagnosis | 1.0.0 (optics-examples-v1) | separating position error from angle error at two distances |

Each `workflow.json` carries the same records with an attribution string. Content hashes,
run times and the full set of verified values are in the
[workflow validation](../workflow-validation.md) and
[sensitivity and tolerance validation](../sensitivity-tolerance-validation.md) records.

Next: [Read and export results](results.md) or [use external scientific tools](interop.md).
