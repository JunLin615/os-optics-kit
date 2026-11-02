# Workflow validation

Issues [#5](https://github.com/JunLin615/opensci-engine/issues/5) (reusable editing, sweep, comparison, plotting
and report scripts) and [#6](https://github.com/JunLin615/opensci-engine/issues/6) (three parameterized optical
workflows), following standing rules [#1](https://github.com/JunLin615/opensci-engine/issues/1) and
[#2](https://github.com/JunLin615/opensci-engine/issues/2).

This record covers:

- `examples/beam_expansion_focus.py`, `examples/polarization_control.py` and `examples/alignment_analysis.py`
  (shared steps in `examples/design_workflow.py`, designs and closed-form references in
  `examples/optical_designs.py`);
- the #5 demonstration on the catalog `beam_expander` example: bounded candidate query, validated edit, L2 spacing
  sweep with unmet samples, ID-aligned comparison, labelled plots and a report linking the artifacts
  (`examples/specs/beam_expander_*.json`);
- the same helpers reused on the polarization design (`examples/specs/polarization_*.json`).

## Environment

Validated on 2026-10-18 with Windows 10 (AMD64), CPython 3.11.16, released PyPI package
`opensci-engine[protected]==0.8.0` (build `0.8.0+94f6dc70bb40a2ed`), numpy 2.4.6, matplotlib 3.10.7, and catalog
release `2026-10-15.1` (sha256 `ce974b614b15b857edb6053709eb57136c339e592ef5116402047a5d8cdf5e87`) installed in the
local store, with `OPENSCI_CATALOG_CHECK_INTERVAL=0`. Only the published wheel was installed; no engine source tree was on the import path. This record
does not change `engine-baseline.json`.

The three workflows and the polarization demonstration use native Engine models only (`catalog_version` null in their
runs). The beam-expander demonstration needs the catalog release above; its test class is skipped when no catalog
release is installed.

## Reproduce

From the repository root in an isolated environment:

```bash
python -m pip install "opensci-engine[protected]==0.8.0" -r requirements-examples.txt
python -m unittest discover -s tests -v

# three workflows, default studies
python examples/beam_expansion_focus.py --out wf-evidence/expansion
python examples/polarization_control.py --out wf-evidence/polarization
python examples/alignment_analysis.py --out wf-evidence/alignment
python examples/alignment_analysis.py --out wf-evidence/alignment-synthetic --no-studies \
    --mirror-tilt-local-y-mrad 1 --synthetic-measurements

# #5 demonstration: catalog beam expander
python examples/beam_expander.py --out wf-evidence/be/base-run
python examples/catalog_candidates.py --category LENS --form PLANO_CONVEX_LENS --wavelength-nm 780 \
    --range focal_length_mm=95..105 --range outer_diameter_mm=12.7..12.7 --near focal_length_mm=100 --limit 8 \
    --columns optical_sku,focal_length_mm,outer_diameter_mm,working_band_nm --out wf-evidence/be/l2-candidates.csv
python examples/edit_project.py wf-evidence/be/base-run/project.json --edits examples/specs/beam_expander_swap_l2.json \
    --out wf-evidence/be/swapped.json --run wf-evidence/be/swapped-run
python examples/sweep_parameters.py examples/specs/beam_expander_l2_sweep.json --base wf-evidence/be/swapped.json \
    --out wf-evidence/be/l2-sweep
python examples/plot_results.py sweep wf-evidence/be/l2-sweep --y target:expanded_waist
python examples/plot_results.py sweep wf-evidence/be/l2-sweep --y target:output_beam_radius
python examples/edit_project.py wf-evidence/be/swapped.json --edits examples/specs/beam_expander_move_l2.json \
    --out wf-evidence/be/final.json --run wf-evidence/be/final-run
python examples/compare_runs.py wf-evidence/be/base-run wf-evidence/be/swapped-run wf-evidence/be/final-run \
    --project wf-evidence/be/final.json --out wf-evidence/be/compare
python examples/build_report.py wf-evidence/be/l2-sweep --project wf-evidence/be/swapped.json \
    --plots wf-evidence/be/l2-sweep/plots --changes wf-evidence/be/swapped.changes.json
python examples/build_report.py wf-evidence/be/base-run wf-evidence/be/swapped-run wf-evidence/be/final-run \
    --project wf-evidence/be/final.json --changes wf-evidence/be/final.changes.json \
    --plots wf-evidence/be/l2-sweep/plots/sweep_target_expanded_waist.png \
            wf-evidence/be/l2-sweep/plots/sweep_target_output_beam_radius.png --out wf-evidence/be/report

# the same helpers on the polarization design
python examples/polarization_control.py --no-studies --out wf-evidence/pol/design
python examples/edit_project.py wf-evidence/pol/design/nominal/project.json \
    --edits examples/specs/polarization_threshold.json --out wf-evidence/pol/strict.json --run wf-evidence/pol/strict-run
python examples/sweep_parameters.py examples/specs/polarization_hwp_wavelength_sweep.json \
    --base wf-evidence/pol/strict.json --out wf-evidence/pol/sweep
python examples/plot_results.py sweep wf-evidence/pol/sweep --y target:transmitted_power
python examples/compare_runs.py wf-evidence/pol/design/nominal/run wf-evidence/pol/strict-run --out wf-evidence/pol/compare
python examples/build_report.py wf-evidence/pol/sweep --project wf-evidence/pol/strict.json \
    --plots wf-evidence/pol/sweep/plots --changes wf-evidence/pol/strict.changes.json
```

Every command was run as a separate process from the repository root. Runs are deterministic: a rerun reproduces the
target values and result hashes.

## Tests

`tests/test_workflow_examples.py` (25 tests) runs each workflow end to end into a temporary directory with reduced
studies and checks exit codes, summary fields, the unmet and diagnostic samples, that design inputs change the built
Project and its results, closed-form agreement within the stated tolerances, report links and artifact hashes, the
synthetic-measurement variant, the spec files, and the polarization and catalog beam-expander demonstrations. With the
catalog installed: 25 tests OK in 42 s; with an empty catalog store: 25 run, 22 OK, 3 skipped (the catalog demonstration).
The full suite (`python -m unittest discover -s tests`) on 2026-10-19: 282 tests OK in
121 s with the catalog; 282 run, 7 skipped, all others OK in 135 s with an empty catalog store.

## Observed outcomes: workflows

### Beam expansion and focusing (`beam_expansion_focus.py`, defaults)

Design: f1 50 mm, f2 150 mm, f3 100 mm, source waist 0.5 mm at 780 nm, ideal thin lenses with 25 mm clear aperture and
declared band 400-1100 nm. Nominal outcome PASS (exit 0), result hash prefix `a20f2128b776c202`:

| target | Engine value | reference (complex q) | status |
| --- | ---: | ---: | --- |
| expanded_radius (mm) | 1.5011183040943072 | 1.5011183040943072 | PASS (1.5 mm ± 2 %) |
| spot_radius (mm) | 0.01655211408155712 | 0.01655211408155711 | PASS (≤ 0.02 mm) |
| waist_location (mm) | -0.036489675408304834 | -0.036489675408304834 | PASS (± 0.5 mm) |

Default studies (21 samples):

| parameter | values | outcome per value | key values |
| --- | --- | --- | --- |
| gap_error_mm | -5, -2, -1, 0, 1, 2, 5 | F, F, P, P, P, F, F | spot_radius 0.03798180896955085 / 0.021773984769928593 / 0.018151733064043964 / 0.01655211408155712 / 0.017537605731193704 / 0.020742970275977126 / 0.0365106672134584 mm; expanded_radius fails at ±5 mm (1.4677459701823363 / 1.5344907280460458) |
| f3_mm | 50, 100, 125, 150, 200 | P, P, F, F, F | spot_radius 0.008276057040778564, 0.01655211408155712, 0.020690142601946394, 0.024828171122335665, 0.03310422816311424 mm |
| wavelength_nm | 532, 780, 1064, 1300 | P, P, F, OUT_OF_MODEL | 1064 nm spot 0.02257878125997023 mm; 1300 nm: run status FAIL (spot 0.027586856802595174 mm) with `MODEL_WAVELENGTH_OUTSIDE_BAND` on all three lenses, classified OUT_OF_MODEL |
| waist_radius_mm | 0.25, 0.5, 1, 3, 4 | F, P, P, P (WARNING), OUT_OF_MODEL | 0.25 mm: spot 0.03310422816311424 mm; 3 mm: status WARNING, `GAUSSIAN_TRUNCATION`, spot 0.00275868568025952 mm; 4 mm: OUT_OF_MODEL_SCOPE, `GAUSSIAN_SEVERE_TRUNCATION`, no target values |

Closed-form check: 63 values compared (21 samples + nominal, 3 targets), 63 within tolerance (relative 1e-9 for the
radii, absolute 1e-9 mm for the waist location), largest absolute difference 5.684341886080802e-14 mm, largest
relative difference 6.523098154505645e-15; 3 values not compared (4 mm waist: no Engine value).

### Polarization control (`polarization_control.py`, defaults)

Design: source 1030 nm linear at 0°, 1 mW; native half-wave retarder (ZERO_ORDER, band 1000-1060 nm, T 0.99) at
22.5°; linear polarizer (T 0.9, extinction ratio 1000) at 45°; targets transmitted_power ≥ 0.8 mW and output_azimuth
45° ± 0.5°. Nominal outcome PASS (exit 0), transmitted_power 0.0008909999999999998 W (Jones 0.0008910000000000001 W,
Malus 0.000891 W), output_azimuth 44.99999999999999°, result hash prefix `fb9ad89ecb23ea05`.

| parameter | values | outcome per value | transmitted_power (W) |
| --- | --- | --- | --- |
| hwp_angle_deg | 0, 7.5, 15, 22.5, 30, 45, 60, 67.5 | F, F, F, P, F, F, F, F | 0.0004459454999999999, 0.0006684727499999995, 0.0008313740030685818, 0.0008909999999999998, 0.0008313740030685813, 0.0004459455, 6.051699693141874e-05, 8.909999999999999e-07 (extinction floor) |
| wavelength_nm | 1000, 1030, 1060, 1064, 1100 | P, P, P, OUT_OF_MODEL, OUT_OF_MODEL | 0.0008900124161877219, 0.0008909999999999998, 0.0008901209823400975, 0.0008898796266670364, 0.0008865678270928473 |

The power target fails at 0, 7.5, 45, 60 and 67.5°; at 15° and 30° the power passes (0.000831 W) and the azimuth
target fails (30.000000000000018° and 59.99999999999999°). At 1064 and 1100 nm both targets read PASS while the run
status is OUT_OF_MODEL_SCOPE with `MODEL_WAVELENGTH_OUTSIDE_BAND` at the retarder; the workflow lists them as
OUT_OF_MODEL with "targets not PASS: all PASS".

Closed-form check: 94 values compared, 94 within tolerance: Jones calculus 84 values (detector power relative 1e-12,
azimuth absolute 1e-9°, after_hwp power relative 1e-12, after_hwp Stokes s1/s2/s3 absolute 1e-12; largest absolute
difference 2.842170943040401e-14, in degrees), Malus' law 10 values where the effective retardance is exactly half a
wave (largest relative difference 3.3591869664552265e-15).

### Alignment analysis (`alignment_analysis.py`, defaults)

Design: 633 nm source 100 mm before a flat fold mirror at 45° AOI; planes `near` and `far` 100 and 400 mm after the
mirror (12.5 mm radius); targets |position_u/v| ≤ 0.1 mm on both planes and |direction_cosine_u/v| ≤ sin(0.5 mrad) on
`far`. Nominal outcome PASS (exit 0), all positions ≤ 5.1e-14 mm, result hash prefix `3e1c842c3842e93f`.

| parameter | values | outcome per value | key values |
| --- | --- | --- | --- |
| mirror_tilt_local_y_mrad | -1, 0, 0.1, 0.25, 1, 15, 60 | F, P, P, F, F, F, UNKNOWN | 0.25 mrad: near u 0.05000000416665952 mm PASS, far u 0.20000001666659545 mm FAIL (position tolerance miss), two-plane pointing 0.5 mrad; 1 mrad: far u 0.8000010666682484 mm, pointing 2 mrad; 15 mrad: `INCOMPLETE_ENVELOPE`, far u 12.003601296472219 mm, pointing 30 mrad; 60 mrad: `OBSERVATION_NOT_REACHED` on `far`, outcome UNKNOWN, far values none, near u 12.057933721130537 mm |
| source_offset_z_mm | 0.05, 0.15 | P, F | v = 0.05000000000000002 / 0.15000000000000002 mm on both planes, pointing 0 |
| mirror_tilt_local_x_mrad | -0.25, 0.1, 0.5 | F, P, F | far v 0.14142135918321094 / -0.05656854268342915 / -0.28284273604494076 mm (2θ·cos 45° geometry) |

Closed-form check: 122 values compared, 122 within tolerance; 8 not compared (60 mrad: far plane not reached). Exact
vector geometry: 98 values, largest absolute difference 9.423017921506016e-13 (mm or mrad). Small-angle 2θ
expectation (tolerance 1000·D² mrad, D the summed deviation angles in rad): 24 values, largest absolute difference
2.500000419345863e-04 mrad (second-order term at 0.5 mrad out-of-plane tilt).

Synthetic-measurement variant (`--mirror-tilt-local-y-mrad 1 --synthetic-measurements`; exit 1 because the 1 mrad
nominal tilt misses the position tolerance): Engine chief-ray u on planes at 100, 175, 250, 325 and 400 mm, invented
residuals 0.004, -0.006, 0.002, 0, 0 mm and an invented camera zero of 0.05 mm; all five CSV rows labelled
`SYNTHETIC` in `data_origin`. `analyze_measurements.py`: status PASS, slope 0.001997336000003954 ± 1.8151624761477544e-05
mm/mm, RMSE 0.0033346664001066297 mm. Fitted pointing 1.9973333439854573 ± 0.018151552348562267 mrad against the
2θ expectation 2.0 mrad and the exact-geometry two-plane pointing 1.999999999999686 mrad; difference
-0.0026666560145427454 mrad, within three standard errors.

## Observed outcomes: #5 demonstrations

### Catalog beam expander (catalog 2026-10-15.1)

1. `beam_expander.py`: base run PASS, expanded_waist (`waist_radius_x_mm`) 1.9960708624295032 mm.
2. `catalog_candidates.py`: 471 plano-convex lenses with a model valid at 780 nm, 8 within 95-105 mm focal length
   and 12.7 mm diameter, all 8 shown: five f = 100 mm (N-BK7 and H-K9L), two fused-silica f = 100.3 mm, one
   fused-silica f = 100.4 mm.
3. `edit_project.py` with `beam_expander_swap_l2.json`: adds target `output_beam_radius` (`beam_radius_mm`, same
   value and 1 % tolerance) and replaces L2 by `OS-OPT-LENS-PCX-D12P7-F100P3-T2P2-R46-UVFS-AR400-1100-W587P6`; the
   Engine accepts the edit; the run (exit 1) gives expanded_waist 1.4406961517845205 mm FAIL and output_beam_radius
   2.0101867326606095 mm PASS.
4. `sweep_parameters.py` with `beam_expander_l2_sweep.json` (12 samples, L2 moved along design +X): 5 PASS, 7 FAIL;
   feasible 5; best `l2_shift` 0.54 mm with expanded_waist 1.9964496422827251 mm (|error| 0.00032561127497299225 mm).

   | l2_shift (mm) | -1 | -0.5 | 0 | 0.25 | 0.4 | 0.5 | 0.54 | 0.55 | 0.6 | 0.7 | 1 | 2 |
   | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
   | expanded_waist (mm) | 0.7323 | 0.9911 | 1.4407 | 1.7410 | 1.9034 | 1.9780 | 1.9964 | 1.9999 | 2.0098 | 1.9923 | 1.7162 | 0.8340 |
   | output_beam_radius (mm) | 2.01023 | 2.01016 | 2.01019 | 2.01024 | 2.01028 | 2.01031 | 2.01033 | 2.01033 | 2.01035 | 2.01039 | 2.01053 | 2.01127 |

   `waist_radius_x_mm` is the radius of the waist the output beam has or had. The output is nearly collimated (Rayleigh
   range of metres), so a sub-millimetre spacing change moves that waist by metres and changes its radius by a factor
   of 2.7 over this range. `beam_radius_mm` is the radius at the plane 100 mm after L2; it changes by 0.055 % over
   the same range. The first measures collimation, the second beam size at the plane.
5. `edit_project.py` with `beam_expander_move_l2.json` (540 um given in um): run PASS, expanded_waist
   1.9964496422827251 mm, identical to sweep sample s0007.
6. `compare_runs.py` (base, swapped, final) against `final.json`: targets aligned by ID, `output_beam_radius` listed as
   missing in the base run; freshness HISTORICAL (PROJECT_CHANGED) for the base and swapped runs, CURRENT for the
   final run; inputs: 8 of the 54 compared fields of the runs' `project.json` differ (L2 position, L2 SKU and six
   fields of the added target); the runs report lists them with the target issue code `TARGET_NOT_MET` of the
   swapped run under Diagnostics.
7. Plots `sweep_target_expanded_waist` and `sweep_target_output_beam_radius` (PNG + SVG, status markers, target band,
   data-file caption), the study report and the runs report (`report/report.md`, 16 artifacts with sha256, 4 of them
   plot files) link only existing files.

`opensci-engine project paraxial wf-evidence/be/swapped.json --components L1 L2 --wavelength-nm 780 --solve afocal`
gives the geometric afocal spacing for the swapped pair at an L2 move of 0.591223702475645 mm; the Gaussian
expanded-waist target is closest at 0.54 mm in this grid, because the source waist does not lie in the front focal
plane of L1.

### Polarization reuse

`polarization_threshold.json` sets the transmitted_power minimum to 0.85 mW (stored as 0.00085 W). The two-parameter
sweep (`hwp_turn` -22.5…22.5°, a rigid rotation of HWP about its own optical axis, × wavelength 1030 / 1064 nm, 18
samples, outcome decided by transmitted_power) gives 3 PASS (turns -5, 0, 5° at 1030 nm; 0.0008641599294975623,
0.0008909999999999998, 0.0008641599294975623 W), 6 FAIL and 9 OUT_OF_MODEL (every 1064 nm sample, including three with
transmitted_power PASS). Best: turn 0° at 1030 nm. Every sample's power matches the Jones reference at
`hwp_angle_deg = 22.5 + turn` within relative 1e-12 (test). The heatmap marks the 9 OUT_OF_MODEL cells; the report
lists the edit (`minimum` 0.0008 → 0.00085).

## Output volume and time

UTF-8 bytes of each command's stdout JSON with one LF terminator, and wall time of the separate process on this host
(about 2.5 s of each is Python start-up and `import opensci_engine`), measured on 2026-10-19 with the scripts of this revision
(edit runs list `out_of_model_issues`, sweeps `samples_with_warnings`, comparisons `inputs_compared`, plots the
target bounds and samples without a value):

| command | exit | status | stdout bytes | wall (s) |
| --- | ---: | --- | ---: | ---: |
| beam_expansion_focus.py (defaults) | 0 | PASS | 3,876 | 11.13 |
| polarization_control.py (defaults) | 0 | PASS | 2,942 | 7.85 |
| alignment_analysis.py (defaults) | 0 | PASS | 4,809 | 8.49 |
| alignment_analysis.py synthetic variant | 1 | FAIL | 2,491 | 3.96 |
| beam_expander.py (base run) | 0 | PASS | 2,769 | 3.53 |
| catalog_candidates.py | 0 | COMPLETED | 2,044 | 3.41 |
| edit_project.py swap + run | 1 | FAIL | 1,697 | 3.81 |
| sweep_parameters.py L2 sweep | 0 | COMPLETED | 2,477 | 6.54 |
| plot_results.py expanded_waist / output_beam_radius | 0 / 0 | COMPLETED | 453 / 461 | 1.38 / 1.32 |
| edit_project.py move + run | 0 | PASS | 1,459 | 3.77 |
| compare_runs.py (3 runs) | 0 | COMPLETED | 2,716 | 4.10 |
| build_report.py study / runs | 0 / 0 | COMPLETED | 935 / 315 | 5.56 / 3.84 |
| polarization_control.py --no-studies | 0 | PASS | 1,375 | 3.32 |
| edit_project.py threshold + run | 0 | PASS | 1,373 | 1.97 |
| sweep_parameters.py HWP × wavelength | 0 | COMPLETED | 2,429 | 5.42 |
| plot_results.py heatmap | 0 | COMPLETED | 389 | 1.36 |
| compare_runs.py (2 runs) | 0 | COMPLETED | 1,174 | 1.75 |
| build_report.py study | 0 | COMPLETED | 969 | 4.38 |

The `--help` texts are 6,843 (expansion), 7,604 (polarization) and 9,738 (alignment) bytes.

Engine evaluations and time per study inside the workflow processes (in-process runs; "Engine" is the sum of the
per-sample run times, "study" includes saving, comparison, plots, closed-form check and report):

| workflow | parameter | samples | outcomes | Engine (s) | study (s) | plotted targets |
| --- | --- | ---: | --- | ---: | ---: | --- |
| expansion | gap_error_mm | 7 | FAIL 4, PASS 3 | 1.25 | 2.43 | waist_location, expanded_radius, spot_radius |
| expansion | f3_mm | 5 | FAIL 3, PASS 2 | 0.88 | 1.74 | spot_radius, waist_location |
| expansion | wavelength_nm | 4 | OUT_OF_MODEL 1, FAIL 1, PASS 2 | 0.73 | 1.68 | spot_radius, waist_location, expanded_radius |
| expansion | waist_radius_mm | 5 | OUT_OF_MODEL 1, FAIL 1, PASS 3 | 0.94 | 1.89 | expanded_radius, spot_radius, waist_location |
| polarization | hwp_angle_deg | 8 | FAIL 7, PASS 1 | 1.47 | 2.50 | output_azimuth, transmitted_power |
| polarization | wavelength_nm | 5 | OUT_OF_MODEL 2, PASS 3 | 0.94 | 1.74 | output_azimuth, transmitted_power |
| alignment | mirror_tilt_local_y_mrad | 7 | UNKNOWN 1, FAIL 4, PASS 2 | 1.15 | 2.46 | far_position_u, far_position_v, far_pointing_u |
| alignment | source_offset_z_mm | 2 | FAIL 1, PASS 1 | 0.33 | 1.01 | near_position_v, far_position_v |
| alignment | mirror_tilt_local_x_mrad | 3 | FAIL 2, PASS 1 | 0.50 | 1.33 | far_position_v, far_pointing_v, near_position_v |

Evaluations per command: expansion 22 (workflow time 9.49 s), polarization 14 (5.94 s), alignment 13 (6.50 s),
synthetic variant 2 (2.21 s); beam-expander demonstration 15 (base, swap, 12 samples, move); polarization reuse 20
(design, threshold edit, 18 samples).

These figures were measured for these examples on this host. Terminal output
is bounded (at most eight listed samples per study, values to ten significant digits); full-precision values are in
the files.

## Artifacts

Each workflow directory holds `workflow.json` (full summary), `nominal/` (project, run, sample record, closed-form
CSV/JSON, power plot), `studies/<parameter>/` (study.json, summary.json, table.csv, one project and run per sample,
`compare/compare.json` and `.csv` against the nominal run, plots, closed-form CSV/JSON, `report/report.md` and `.json`)
and `report/report.md` linking all of them. File counts and sizes in this run: expansion 285 files (10.5 MB),
polarization 173 (7.0 MB), alignment 180 (6.1 MB), synthetic variant 33 (1.2 MB, plus `synthetic/` with the labelled
CSV and the analysis bundle), beam-expander demonstration 163 (9.1 MB), polarization reuse 216 (9.4 MB). Native run
directories dominate the size.

## Benchmark provenance

Task families from the [Free-Space Optical Experiment Benchmark](https://github.com/JunLin615/free-space-optical-experiment-benchmark)
(JunLin615 and contributors, commit `e72ad3a9b6be12c7f0dbb00121fda23289c4e908`, CC BY 4.0 content) informed the designs.
Only the task family is used: the designs, numbers and checks were written for this repository; no case text, gold
answer or scoring material is copied. `optical_designs.DESIGN_INFO[...]["provenance"]` and every `workflow.json` carry
the same records with an attribution string.

| workflow | case | revision / release | recorded hash | what informed the workflow |
| --- | --- | --- | --- | --- |
| beam expansion and focusing | SEED-2-2 Designing a fivefold afocal beam expander | 0.2.2 / 0.1.0-rc1 | case_content_sha256 `dfe041664ee0fc501e683ad4ecadfedb5ab7294d9f4b0bb137fd76b40494cbc6` | two-lens Keplerian afocal expansion, magnification f2/f1 (×3 here) |
| beam expansion and focusing | SEED-7-4 Independently verify a fivefold beam expander | 0.1.0 / unreleased | file sha256 `36d8132bc1f860bb36b57a48f746b1ad9d3a0c937f4fa535e562484ba705db87` | verification by beam size at a plane and by waist location |
| polarization control | SEED-1-7 Half-wave plate and linear polarization | 0.2.1 / 0.1.0-rc1 | case_content_sha256 `cf0f6a8b3db99ad00d9ecce099cc92d0dacac129305a0a33ea2758dfd374c3db` | a half-wave plate turns linear polarization by twice its angle |
| polarization control | SEED-1-8 Continuously adjustable free-space attenuation | 0.1.0 / unreleased | file sha256 `6154639f8afa52299907ef9779466c16da20b273665f2533ed73951dff728dec` | rotatable half-wave plate before a fixed polarizer as an attenuator (linear polarizer here) |
| alignment analysis | SEED-1-2 Establishing an axis with two steering mirrors and two apertures | 0.1.0 / unreleased | file sha256 `e8b23207326f24f1961b020cca8fca81651151367ee69735ed2431c3e74fac29` | position and direction checked at two separated planes |
| alignment analysis | EX-ALIGN-01 Two-iris alignment diagnosis | 1.0.0 / optics-examples-v1 | content_sha256 `5d92ff16059730f7c20a226cad6fe481b2fd1b7995daf942befb631ce0d74738` | separating position error from angle error with two distances |

The synthetic-measurement variant reuses `examples/analyze_measurements.py`, whose own provenance record names its
case-library sources.

## Limits

- The workflows use ideal native models: thin lenses without aberration or dispersion, ideal thin Jones elements, a
  flat mirror with declared reflectance. The closed-form checks therefore confirm the set-up (geometry, conventions,
  units) to rounding level; they do not test real-component physics.
- The beam-expander demonstration uses catalog products of release 2026-10-15.1; the swap spec names one SKU and the
  move spec one shift (0.54 mm), both valid for that release.
- Workflow studies change one parameter at a time; two-parameter grids go through `sweep_parameters.py` specs (as in
  the polarization reuse). At most `--max-plots` (default 3) targets are plotted per study, chosen by their change
  relative to the target's bound width; every target value is in `table.csv`.
- The small-angle 2θ comparison is an expectation with a second-order tolerance; the exact-geometry comparison is the
  strict check.
- Synthetic rows are Engine positions plus invented numbers; they demonstrate the measurement-analysis route and its
  labelling, not an experiment.
- `--help` of the workflow scripts takes about 2.5 s because the parameter table is read from `optical_designs.py`,
  which imports the Engine.
