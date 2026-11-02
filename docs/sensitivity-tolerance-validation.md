# Sensitivity and tolerance validation

Issues [#8](https://github.com/JunLin615/opensci-engine/issues/8) (sensitivity analysis) and
[#9](https://github.com/JunLin615/opensci-engine/issues/9) (tolerance sampling), following standing
rules [#1](https://github.com/JunLin615/opensci-engine/issues/1) and
[#2](https://github.com/JunLin615/opensci-engine/issues/2).

Scripts: [examples/sensitivity_analysis.py](../examples/sensitivity_analysis.py) and
[examples/tolerance_study.py](../examples/tolerance_study.py); runnable specs in
[examples/specs](../examples/specs/) (`sensitivity_*.json`, `tolerance_*.json`); tests in
[tests/test_sensitivity_tolerance.py](../tests/test_sensitivity_tolerance.py). Every spec uses a
catalog-free design from [examples/optical_designs.py](../examples/optical_designs.py) (native ideal thin
lenses, retarder, linear polarizer, flat mirror), so no catalog product is involved.

## Reproduce

From the repository root in an isolated Python environment:

```bash
python -m pip install "opensci-engine[protected]==0.8.0" -r requirements-examples.txt
python -m unittest discover -s tests -v
python examples/sensitivity_analysis.py examples/specs/sensitivity_expander.json --out run-sensitivity_expander
python examples/sensitivity_analysis.py examples/specs/sensitivity_expander_band_edge.json --out run-sensitivity_expander_band_edge
python examples/sensitivity_analysis.py examples/specs/sensitivity_polarization.json --out run-sensitivity_polarization
python examples/sensitivity_analysis.py examples/specs/sensitivity_alignment.json --out run-sensitivity_alignment
python examples/tolerance_study.py examples/specs/tolerance_expander.json --out run-tolerance_expander
python examples/tolerance_study.py examples/specs/tolerance_polarization.json --out run-tolerance_polarization
python examples/tolerance_study.py examples/specs/tolerance_alignment.json --out run-tolerance_alignment
python examples/tolerance_study.py examples/specs/tolerance_expander.json --out run-tolerance_expander_zero --zero-tolerance --samples 5
python examples/tolerance_study.py examples/specs/tolerance_expander.json --out run-tolerance_expander_draw --draw-only
python examples/tolerance_study.py --verify-seed run-tolerance_expander
```

Each study directory holds `study.json`, `summary.json`, `table.csv`, `samples/<id>/` (project, native run
directory, sample record), `plots/` (PNG + SVG) and `report/` (`report.md`, `report.json`); sensitivity
studies add `sensitivity.csv`, tolerance studies add `samples.csv` and `sampling.json`. Run outputs are not
committed.

## Environment

Recorded on 2026-10-18 with Windows 10 (AMD64), CPython 3.11.16, released PyPI package
`opensci-engine==0.8.0` (build `0.8.0+94f6dc70bb40a2ed`), numpy 2.4.6, matplotlib 3.10.7. The active catalog
release was `2026-10-15.1`; it is recorded in every study's identity and not used by these designs.

All 187 offline tests passed in 81 s; the 23 sensitivity, tolerance and plot-layout tests
(`test_sensitivity_tolerance.py`, `test_plot_bars_layout.py`) take 17 s.

## Sensitivity: derivatives against closed forms

Central differences of Engine target values compared with independent closed forms (Malus law with
transmittance and extinction ratio; mirror deviation 2θ in the plane of incidence and 2θ·cos 45° out of it,
planes at 100 mm and 400 mm):

| Case | Engine derivative | Closed form | Relative difference |
| --- | ---: | ---: | ---: |
| dP/dθ HWP at 10°, h = 0.001° | 2.3801510633237027e-05 W/deg | 2.380151065278729e-05 W/deg | −8.2e-10 |
| same, h = 0.01° | 2.38015087193383e-05 W/deg | | −8.1e-08 |
| same, h = 0.1° | 2.380131731046821e-05 W/deg | | −8.1e-06 |
| same, h = 1° | 2.3782181084521588e-05 W/deg | | −8.1e-04 |
| far u / mirror tilt about local Y (h = 0.01 mrad) | 0.8000000001304248 mm/mrad | 0.8 | 1.6e-10 |
| near u / mirror tilt about local Y | 0.2000000000331391 mm/mrad | 0.2 | 1.7e-10 |
| far v / mirror tilt about local X | −0.5656854249937889 mm/mrad | −0.5656854249492381 | 7.9e-11 |
| near v / mirror tilt about local X | −0.14142135624844726 mm/mrad | −0.14142135623730953 | 7.9e-11 |
| far v / source offset along Z | 1.0000000000000002 mm/mm | 1 | 2.2e-16 |
| d²P/dθ² HWP at 22.5° (maximum), h = 0.1° | −2.169132845195563e-06 W/deg² | −2.1691416552714187e-06 | −4.1e-06 |
| same, h = 1° (reported second difference) | −2.1682607872785826e-06 W/deg² | | −4.1e-04 |
| d²P/dα² polarizer at 45°, h = 1° | −5.422303528594772e-07 W/deg² | −5.422854138178547e-07 | −1.0e-04 |

The HWP slope error falls by a factor 100 per factor 10 in step (O(h²)); the step-consistency ratio of the
0.001° and 0.01° estimates is 1.0000000804108156. The tests require the 0.01° slope and the mirror slopes
within 1e-6 and the 0.1° curvature within 1e-4 of the closed form.

## Sensitivity: example specs

Stdout is one compact JSON document; bytes count UTF-8 with the LF terminator, with the `--out` names of the
commands above. Wall time is one CLI process (about 2 s of it is the Engine import). Stdout bytes and wall times in
this record were measured again on 2026-10-19 with the scripts of this revision (tolerance stdout lists
`samples_with_warnings`); sample outcomes and values are unchanged.

| Spec | Exit | Samples (outcomes) | Stdout bytes | Wall |
| --- | ---: | --- | ---: | ---: |
| sensitivity_expander | 0 | 17 (17 PASS) | 3,399 | 7.16 s |
| sensitivity_expander_band_edge | 0 | 9 (8 FAIL, 1 OUT_OF_MODEL) | 2,525 | 5.28 s |
| sensitivity_polarization | 0 | 13 (13 PASS) | 1,917 | 5.80 s |
| sensitivity_alignment | 0 | 15 (11 PASS, 2 FAIL, 2 UNKNOWN) | 5,204 | 6.82 s |

**Expander and focus** (nominal PASS: expanded radius 1.5011183040943072 mm, spot radius
0.01655211408155712 mm). Primary estimates (central, smallest step), scale = declared normalization scale:

| Target | Parameter (scale) | Derivative | Step ratio | Second difference | Flags | Rank |
| --- | --- | ---: | ---: | ---: | --- | ---: |
| spot_radius | focus plane along X (100 µm) | −0.0004965430335554522 mm/mm | 1.0040566638727793 | 0.013565755594994313 mm/mm² | NEAR_EXTREMUM | 1 |
| spot_radius | L2 along its axis (50 µm) | −0.0003310395965407456 mm/mm | 1.0008026684293576 | 0.002684044556370765 mm/mm² | – | 2 |
| spot_radius | L3 tilt about local X (5 mrad) | 0 mm/mrad | – | 6.96055084390551e-08 mm/mrad² | NEAR_EXTREMUM | 3 |
| spot_radius | L3 decenter along local X (50 µm) | 0 mm/mm | – | −4.962234623900884e-06 mm/mm² | NEAR_EXTREMUM | 4 |
| expanded_radius | L2 along its axis (50 µm) | 0.006668088057748545 mm/mm | 1.0000000000844145 | 0.00013349514442761998 mm/mm² | – | 1 |
| expanded_radius | L3 tilt, L3 decenter, focus plane | 0 | – | 0 | NO_CHANGE | 2 |

The focus plane lies 36.5 µm before the focused waist, which the NEAR_EXTREMUM flag of the first row reflects
(|D|·h ≤ |f″|·h²/2 at h = 0.1 mm). The tilt and decenter of the ideal thin lens L3 change the spot radius only
at second order. Ranking uses taylor_change_at_scale = |D|·s + |f″|·s²/2 (spot_radius: 1.1748308133051678e-04,
1.9907035522500737e-05, 8.700688554881887e-07, 6.202793279876106e-09 mm).

**Band edge** (`wavelength_nm` 1099.8 on lenses modelled for 400–1100 nm). The nominal spot radius
0.02333848085499552 mm FAILs its 0.02 mm maximum and stays in the output (`nominal.outcome: FAIL`). Sample
s0004 (+0.5 nm, 1100.3 nm) completes with `MODEL_WAVELENGTH_OUTSIDE_BAND`, class OUT_OF_MODEL; the 0.5 nm
estimate is therefore labelled `backward` (2.1220659078939608e-05 mm/nm) with the reason
`+h: OUT_OF_MODEL (MODEL_WAVELENGTH_OUTSIDE_BAND)`, the 0.05 nm central estimate 2.1220659078611962e-05 mm/nm
is primary, and the pair carries METHOD_DIFFERS. A unit test adds a 199.5 mm L2 shift: its −h edit is refused
(`WORKSPACE_OPERATION_REFUSED`, `OPTICAL_BODIES_OVERLAP`), the sample is an ERROR row with its record, and that
step is labelled `forward`.

**Polarization** (nominal 22.5° HWP before a 45° polarizer, P = 0.0008909999999999998 W, the maximum). HWP
angle: derivative −4.336808689942018e-18 W/deg, NEAR_EXTREMUM, second difference
−2.1682607872785826e-06 W/deg²; polarizer angle: 0 W/deg, NEAR_EXTREMUM, −5.422303528594772e-07 W/deg²;
wavelength (design 1030 nm, zero-order retarder): 2.0098856389969e-12 W/nm at 1 nm and a ratio of
0.009999667442249402 to the 10 nm estimate, flagged STEP_DEPENDENT and NEAR_EXTREMUM (the central estimate is
dominated by the third-order term at the half-wave wavelength).

**Alignment** (resolution 1 nm on the four position targets). Mirror tilt about local Y with steps 0.01, 0.1
and 20 mrad: at ±20 mrad the beam misses the 12.5 mm far plane (`OBSERVATION_NOT_REACHED`, value null, class
UNKNOWN, samples s0005/s0006 listed in `unusable_samples`); that step has no far-plane estimate while the near
plane keeps one, and the 0.01/0.1 mrad pair gives the slopes above with ratio 0.9999999868250341. The
couplings far v / mirror tilt Y (−3.1401535159893977e-12 mm/mrad) and far u / source offset Z (0) are within
the 1 nm resolution and flagged NO_CHANGE; far u / mirror tilt X is 0 mm/mrad with second difference
−0.0007999999851968192 mm/mrad², flagged NEAR_EXTREMUM.

## Tolerance sampling

Offsets are drawn with `numpy.random.Generator(numpy.random.PCG64(seed))`, one uniform number per sample and
parameter in spec order, uniform or truncated normal (inverse CDF), all parameters independent; seed
20261018, 100 samples per spec plus the nominal run s0000. Rates use all requested samples as the denominator;
target statistics use samples that completed in model scope with a value.

| Spec | Exit | ERROR | OUT_OF_MODEL | UNKNOWN | FAIL | PASS | PASS / all | PASS / (PASS + FAIL) | Stdout bytes | Wall |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| tolerance_expander | 0 | 0 | 0 | 0 | 8 | 92 | 92/100 | 92/100 | 3,293 | 28.82 s |
| tolerance_polarization | 0 | 0 | 33 | 0 | 1 | 66 | 66/100 | 66/67 | 3,614 | 29.19 s |
| tolerance_alignment | 0 | 0 | 0 | 0 | 36 | 64 | 64/100 | 64/100 | 4,920 | 31.94 s |
| tolerance_expander --zero-tolerance --samples 5 | 0 | 0 | 0 | 0 | 0 | 5 | 5/5 | 5/5 | 3,332 | 4.71 s |

Distributions (offsets from nominal) and sampled ranges:

| Spec | Parameter | Distribution | Sampled min | Sampled max |
| --- | --- | --- | ---: | ---: |
| expander | L2 along its axis | uniform(−0.5, 0.5) mm | −0.4921545684601508 | 0.4726022087822529 |
| expander | focus plane along X | uniform(−0.3, 0.3) mm | −0.29627229351227574 | 0.29738323450415943 |
| expander | source waist radius (nominal 500 µm) | normal(0, 5) µm, ±3σ | −13.405442334805972 | 13.58285630600273 |
| polarization | HWP angle | normal(0, 4)°, ±3σ | −9.434053626494428 | 7.602613126779821 |
| polarization | polarizer angle | normal(0, 2)°, ±3σ | −4.860330622841626 | 5.060458718888366 |
| polarization | wavelength (nominal 1030 nm) | uniform(−40, 40) nm | −39.81395711260517 | 39.84372194975836 |
| alignment | mirror tilt local Y / local X | normal(0, 0.1) mrad, ±3σ | −0.26810884669611945 / −0.2384007176757423 | 0.24535997227563008 / 0.2716571261200546 |
| alignment | source offset Y | uniform(−0.05, 0.05) mm | −0.048375168669823554 | 0.04935263465661549 |
| alignment | source offset Z | uniform(−50, 50) µm | −49.21545684601508 | 48.53295955527473 |

Target statistics (count over 100 requested samples):

| Spec | Target | Count | Min | Median | p95 | Max | Per-target status |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| expander | expanded_radius (mm) | 100 | 1.4620780120581691 | 1.5019598088580386 | 1.5354941254024765 | 1.5403996504693351 | 92 PASS, 8 FAIL |
| expander | spot_radius (mm) | 100 | 0.016122384273525765 | 0.016829266313018332 | 0.017535103944114987 | 0.018279895447710783 | 100 PASS |
| expander | waist_location (mm) | 100 | −0.49972638617019527 | 0.010386191785862309 | 0.32881630879502943 | 0.3949836779517426 | 100 PASS |
| polarization | transmitted_power (W) | 67 | 0.0007924926406150894 | 0.0008812054561223266 | 0.0008907393302853988 | 0.0008909977496438483 | 66 PASS, 1 FAIL, 33 OUT_OF_MODEL |
| alignment | far_position_u (mm) | 100 | −0.25134429010211085 | 0.004413084354694811 | 0.13840683101185244 | 0.1755658975057022 | 77 PASS, 23 FAIL |
| alignment | far_position_v (mm) | 100 | −0.17267011728946066 | 0.007972959398944289 | 0.10348981709728149 | 0.12976479223457751 | 87 PASS, 13 FAIL |
| alignment | far_pointing_u (1) | 100 | −0.0005362519304416004 | −6.876060079208378e-06 | 0.0003127068146105861 | 0.0004907192156653475 | 99 PASS, 1 FAIL |

The near-plane positions and far_pointing_v of the alignment study pass in all 100 samples. In the
polarization study the 33 samples whose wavelength lies outside the retarder's 1000–1060 nm band complete with
`MODEL_WAVELENGTH_OUTSIDE_BAND`; their transmitted-power target reads PASS in all 33, and they are counted as
OUT_OF_MODEL, not PASS, and excluded from the power statistics (count 67).

Reproducibility: the full expander study and the `--draw-only` run wrote byte-identical `samples.csv`
(sha256 `61c22e95edd0c9017b56bdca0e4068bf9b2e28a832a0a0d9ae921fc2363c50d2`), and `--verify-seed` on both
returned `REPRODUCED` (exit 0, 0.46 s). Tests check that the same seed gives the same sample set, that the first
20 of 50 samples equal a 20-sample draw, that another seed differs, that a modified `samples.csv` gives
`MISMATCH` (exit 1), and that 4000 truncated-normal draws stay within their bounds and reach both tails.

Zero tolerance: with `--zero-tolerance` the five samples have the nominal project hash and result hash, and the
maximum absolute target difference from the nominal run is 0.0 for all three targets (tolerance 0; the Engine
run is deterministic).
