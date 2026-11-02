# External scientific tools

[User guide](README.md) · English · [简体中文](../zh-CN/interop.md)

OpenSci computes ray paths, Gaussian beams, polarization, power and a linear optical
network of your design. Optimizers, diffraction codes, quantum-optics simulators and
electromagnetic solvers run in your own Python environment and read what OpenSci
exports. The four worked examples below were validated against independent references;
the [validation record](../interop-validation.md) lists versions, inputs, outputs and
deviations.

The engine-running scripts use os-optics **0.10.0**; use
[version matching](installation.md#match-the-examples-to-the-installed-version) for the
matching installation. The numerical comparisons below are the recorded validation runs
unless identified as a new result. They use the linear optical
network with per-path group delays, native components and the `TABULATED_THIN_LENS`
model kind. The scripts that run the Engine check the installed version and stop with a
clear message on an older engine; the external-side scripts read only exported files and
do not import the Engine.

| Topic | OpenSci provides | External tool computes | Example |
| --- | --- | --- | --- |
| [Optimization and tolerance](#optimization-and-tolerance) | Each design's run, target metrics, run identity | Next design values (SciPy, Optuna), sensitivity indices (SALib) | `examples/interop/optimization/` |
| [Diffraction at a declared stop](#diffraction-at-a-declared-stop) | Gaussian beam state, power, stop and lens parameters, plane frames | Diffracted focal field (LightPipes; prysm, POPPY, Diffractio as cross-checks) | `examples/interop/diffraction/` |
| [Quantum photonics](#quantum-photonics-from-the-network-export) | Linear optical network: modes, lossy transfer matrix, path delays, conventions | Photon statistics (Perceval; Piquasso as cross-check) | `examples/interop/quantum/` |
| [Metasurface lens as a virtual component](#metasurface-lens-as-a-virtual-focusing-component) | Runs with a tabulated thin focusing element inside its validated domain | Unit-cell responses (grcwa) and focusing (angular spectrum) | `examples/interop/metasurface/` |

External results stay in their own files next to the OpenSci run they used, with that
run's identity. They do not change the run, its hashes or its Local Validation, and a
virtual component made from them is a simulation model, not an orderable product.

Prompts for your own agent: [English](interop-prompts.md) · [简体中文](../zh-CN/interop-prompts.md).

## Before you start

The external-tool environments need Python 3.12 or newer, because the pinned numpy
2.5.4 and scipy 1.18.1 require it: 3.12 to 3.14 for the quantum topic, 3.12 for the
separate Diffractio environment. The examples were verified with Python 3.12.14; the
core examples of this repository run on Python 3.11 or newer. Use one fresh virtual
environment per topic and run the commands from the repository root:

```bash
python -m venv .venv-optimization
.venv-optimization/bin/python -m pip install -r examples/interop/optimization/requirements-optimization.txt
```

On Windows use `.venv-optimization\Scripts\python`. The requirements files request
os-optics 0.10.0 or newer. They pin the tested
versions of the third-party packages, which are never dependencies of `os-optics`
itself.

Every script prints one JSON summary on stdout. Its `outcome` compares OpenSci with an
external tool or an independent reference, and is not an OpenSci run status:
`WITHIN_TOLERANCE` (exit 0) when every comparison is inside its stated tolerance,
`OUT_OF_TOLERANCE` (exit 1) when one is not, `COMPLETED` (exit 0) for a step that only
produces data, and exit 4 with a JSON error on stderr for an error. OpenSci's own
statuses (`PASS`, `WARNING`, `OUT_OF_MODEL_SCOPE`, ...) appear separately as
`engine_status`.

## Optimization and tolerance

`optimize_expander.py` collimates a catalog-free Keplerian expander (two declared ideal
thin lenses, f = 50 mm and 150 mm, 532 nm, 0.5 mm waist) by moving lens `L2`:

```bash
python examples/interop/optimization/optimize_expander.py --out run-interop-optimization
```

Each evaluation copies the Project, sets `L2`'s position by `instance_id`, runs the
Engine in memory and reads the target metric `divergence_half_angle_x_rad` from the
run's results. SciPy (`minimize_scalar`, bounded) and Optuna (TPE, seed 1, 60 trials)
search the separation; SALib then estimates first-order and total Sobol indices of
the divergence for uniform mounting errors of ±0.5 mm on both lenses and ±5 % on the
source waist. An independent ABCD calculation (numpy only) supplies the reference
optimum and the reference value of every evaluated design.

Outputs in the `--out` directory: `evaluations.jsonl` (one line per Engine run with its
inputs, metric value, status and `project_hash`, `input_hash`, `result_hash`,
`engine_build_id`), `project-baseline.json` and `report.json`. On the published 0.10.0
wheel SciPy reached the reference optimum (200.0573 mm separation) to 9e-7 mm in 10
evaluations, every Engine value matched the ABCD reference to 2e-14 relative, and the
Sobol indices of Engine and reference agreed to 2e-14. The optimizer chooses values and OpenSci computes each design. The updated workflow
keeps objective validity separate from feasibility: the selected divergence must be
an exact usable quantity; the `collimated` requirement must have assessment `MET`
for a feasible optimum. Unrelated scope findings do not invalidate that objective.
Every evaluation remains in `evaluations.jsonl`, including invalid, partial, unmet and
indeterminate cases. An absent feasible optimum is `NO_FEASIBLE_RESULT` (exit 2).

Use `--max-divergence-rad` for the customer requirement, `--max-evaluations` for total
work, and `--max-trace-steps` / `--max-interactions-per-path` for per-design engine work.
For example, tighten the divergence requirement while retaining the same search:

```bash
python examples/interop/optimization/optimize_expander.py --out run-optimization-strict --max-divergence-rad 1e-9
```

SALib analysis uses every valid objective, including unmet targets; an incomplete
sample set is refused instead of dropping evaluations and reporting altered Sobol
indices. Preserve the sample log to interpret the denominator and missing results.

## Diffraction at a declared stop

A 532 nm TEM00 beam (1 mm waist radius, 1 mW) passes a declared circular
`APERTURE_STOP` and an `IDEAL_THIN_LENS` (f = 200 mm); the question is the irradiance
in the back focal plane. Run both sides, possibly in two environments:

```bash
python examples/interop/diffraction/opensci_handoff.py --out run-interop-diffraction
python examples/interop/diffraction/external_focus.py run-interop-diffraction
```

`opensci_handoff.py` runs the Project for stop radii 3, 1.2 and 0.5 mm and writes a
`handoff.json` per case: the Engine's transverse-plane handoff on an observation plane
in the stop's plane (`RunResults.handoff`, also `os-optics results handoff RUN
--monitor ID --element ID`): Gaussian q per axis, radii, wavefront radii, M², power,
wavelength, medium, beam axes, Jones vector, the stop outline in the plane's frame and
the field-construction convention with its applicability; plus the lens parameters
from the Project, the Engine's own focal-plane result and the run identity. The script
checks the handoff against the pupil-plane state 1 mm upstream propagated by ABCD.
`external_focus.py` reads only these files. OpenSci does not export a field: intensity
or power alone does not determine a complex field, and the sparse pupil rays of a run
are not a sampled pupil field. The script therefore constructs the sampled field from
the Gaussian state under a stated convention (`tem00_field.py`):

* field `E ~ exp[i(k·r − ωt)]`; `U = sqrt(2P/(π w_x w_y)) exp(−x²/w_x² − y²/w_y²) exp(+ik(x²/2R_x + y²/2R_y))`
  in the beam axes, `R > 0` for a diverging beam; Gouy phase and piston omitted;
  `|U|²` integrates to the beam power;
* only valid for M² = 1 on both axes (the script refuses otherwise); M² > 1 alone
  does not determine a mode content or coherence;
* hard, infinitely thin stop with area-weighted edge pixels; ideal lens as a
  quadratic phase (the Engine does not model the ideal lens's phase).

LightPipes propagates the field with absolute units on six grids and windows (the
convergence study); prysm and POPPY recompute the normalized radial profile, Diffractio
the absolute peak; a Hankel integral (`hankel_reference.py`) is the independent
reference. Results of the validated run:

| Stop radius | Engine status | Engine focal peak vs reference | LightPipes finest grid: relative peak error (convergence spread) |
| --- | --- | --- | --- |
| 3 mm | `PASS` | +0.024 % | 3e-8 (1e-7) |
| 1.2 mm (5.6 % of the power blocked) | `WARNING` `GAUSSIAN_TRUNCATION` | **+62 %** | −1.0e-4 (1.7e-4) |
| 0.5 mm | `OUT_OF_MODEL_SCOPE` `GAUSSIAN_SEVERE_TRUNCATION` | Gaussian withheld | 8.6e-4 (1.7e-3) |

The Engine's transmitted power equals `1 − exp(−2a²/w²)` in every case. Its focal
irradiance is a paraxial Gaussian quantity: exact for the open stop, but after a
`GAUSSIAN_TRUNCATION` warning only the power is reliable and the focal irradiance needs
a diffraction calculation like this one. prysm and POPPY agree with the reference
profile to 4e-7; both compute the same discrete Fourier transform of the same sampled
field, so together they are one cross-check of the sampling, not two independent
references. Diffractio's absolute peak agrees to 3e-5.

Diffractio 1.0.0 requires numpy < 2 and cannot share an environment with
os-optics. Install `requirements-diffractio.txt` in a second environment and run
the same `external_focus.py` there: it reads only the handoff files. The script also
writes `focus_profiles.png` in the primary environment (Engine Gaussian and diffraction reference per case).

The separate Diffractio environment writes `external_report_diffractio.json`,
`external_summary_diffractio.json` and `focus_profiles_diffractio.png`, preserving
the primary environment's reports and figure.

## Quantum photonics from the network export

`build_layouts.py` builds small catalog-free Projects from native components (a thin
50:50 plate splitter, flat mirrors, uncoated windows of a declared N-BK7 Sellmeier
glass) and exports each with the public command:

```bash
python examples/interop/quantum/build_layouts.py --out run-interop-quantum
python examples/interop/quantum/run_quantum.py run-interop-quantum --inputs examples/interop/quantum/quantum_inputs.json
```

Cases: Hong-Ou-Mandel interference at 780 nm; a delay scan from 13 exports with source
B moved by −0.3 … +0.3 mm; a 3 mm window in one arm with the other source moved back by
the window's group-delay length or by its phase-delay length only; a two-input
Mach-Zehnder at 532 nm with 0, 1 and 1.0003 mm windows; an ideal thin lens on the path,
which the export refuses (`PHASE_UNDEFINED_IDEAL_LENS`).

The Project's lasers only define the network's modes, wavelength and polarization.
Every quantum quantity comes from `quantum_inputs.json`: one photon per source in the
V mode; Gaussian wavepackets (rms intensity duration 0.25 ps for the HOM cases, 1000 ps
for the MZI) with zero-delay indistinguishability 0.95; one threshold detector per output
plane with efficiency 0.8 and a dark-count probability per gate (0, and 0.01 in the
lossless HOM case and the lossy 1 mm MZI case).
`run_quantum.py` never imports os-optics:

* it reads `T[output, input]` (Perceval `Unitary(Matrix(U))` and Piquasso
  `Interferometer(U)` read matrices the same way) and multiplies by `sqrt(η)` for the
  detector efficiency;
* a lossy T is embedded in a unitary twice its size (unitary dilation), and the extra
  modes are traced out;
* each path's group delay is taken from the export's `group_delay_ps` and checked
  against the value computed from the Project's declared glass; the photons'
  mutual indistinguishability is `p0 exp(−Δτ²/4σ²)` with Δτ their relative group delay;
* a single-wavelength T is used only when one photon's paths to one output differ in
  group delay by at most 5 % of its duration; otherwise the case is refused
  (`SINGLE_FREQUENCY_MODEL_INVALID`, shown with 0.25 ps photons in the 1 mm MZI).

Perceval (SLOS) simulates the partially distinguishable photons with noise tags;
Piquasso recomputes the same distribution as the p-weighted mixture of
indistinguishable and distinguishable photons. The closed-form reference
(`reference.py`) uses no exported data: 2 × 2 splitter algebra, the SCHOTT N-BK7
Sellmeier formula, Fresnel loss of the uncoated faces and the Gaussian wavepacket
overlap. For lossy layouts it takes the loss amplitudes of both photons from
`L = I − M†M`, so loss through a shared element interferes as it does physically,
and threshold detection with dark counts acts on the complete photon-number
distribution (one, two or no photons at each detector). In the validated run Perceval matched the reference to 4e-8 and Piquasso to
2e-9 over all cases; the HOM dip visibility over the scan was 0.9491 (reference
0.9491; analytic 0.95 at infinite delay range). Compensating the 3 mm window by its
phase delay leaves a 0.163 ps group-delay mismatch and lowers the photons'
indistinguishability from 0.95 to 0.854; the export's group delay makes this visible.

Licenses as reported upstream: Perceval (`perceval-quandela`) MIT; its required
dependency `exqalibur` declares CC BY-NC-ND 4.0 in its package metadata; Piquasso
Apache-2.0. Piquasso 8.0.1 with numba 0.68 can fail to reload its own compiled cache;
the script gives each run a fresh `NUMBA_CACHE_DIR`.

## Metasurface lens as a virtual focusing component

Step 4 passes on 0.10.0: the in-domain comparisons and convergence checks are inside
their tolerances, and the four out-of-domain counterexamples (a 450 nm wavelength and
the 5°, 8° and 20° field angles) report their scope codes with `INDETERMINATE` target
assessments. See the [validation record](../interop-validation.md) for the exact cases;
the numerical tables below record the full run on the published 0.9.0 wheel.

A TiO2 nanopillar metalens (f = 200 µm at 532 nm, 100 µm diameter) is characterized
externally and turned into an OpenSci `TABULATED_THIN_LENS` model:

```bash
python examples/interop/metasurface/step1_unit_cells.py --out run-interop-metasurface
python examples/interop/metasurface/step1_unit_cells.py --out run-interop-metasurface --nG 201 --wavelengths-nm 490 515 532 545 580
python examples/interop/metasurface/step2_focusing.py run-interop-metasurface
python examples/interop/metasurface/step2_focusing.py run-interop-metasurface --nG 201
python examples/interop/metasurface/step2_focusing.py run-interop-metasurface --samples 2048 --wavelengths-nm 490 515 532 545 580
python examples/interop/metasurface/step3_fit_model.py run-interop-metasurface
python examples/interop/metasurface/step4_engine_check.py run-interop-metasurface
```

The second step-1 command and the last two step-2 commands compute the convergence
tables: 201 instead of 101 RCWA orders, and a 2048-sample (666 µm) instead of a
1024-sample (333 µm) propagation grid, at five wavelengths across the band plus the
field-angle and beam-size cases. Step 3 takes the external numerical spread from these
tables and stops with `CONVERGENCE_DATA_MISSING` (exit 4) when either kind is missing,
because bounds without the spread would understate the reference's own uncertainty.

The external results are split into three sets that play different roles:

| Set | Content | Used for |
| --- | --- | --- |
| Table | 10 wavelengths, 490–580 nm, 30 µm beam, normal incidence | the model rows |
| Calibration | 5 wavelengths between the rows (495, 515, 525, 545, 565 nm), a 20 µm beam, field angles 2, 5, 8 and 20° at 532 nm | the error bounds and the domain |
| Test | 4 other wavelengths (505, 535, 555, 575 nm), a 25 µm beam at 545 nm, 1.5° at 555 nm | only the final check |

1. **Unit cells** (grcwa): zeroth-order complex transmission of pillars 40–150 nm in
   radius (period 325 nm, height 600 nm, constant indices TiO2 2.43 / SiO2 1.46, normal
   incidence, x polarization) at all 19 wavelengths. The flux-normalized amplitude
   `t·sqrt(T/|t|²)` is used, because `|t|²` alone is not a power fraction across media of
   different index.
2. **Focusing** (numpy): local periodic approximation and scalar angular-spectrum
   propagation; focus position, second-moment radius and focusing efficiency for every
   case. Tilted inputs reuse the normal-incidence cell responses, an approximation that
   worsens with angle.
3. **Model**: one row per table wavelength (equivalent thin-lens focal length from the
   Gaussian focusing law; transmittance = focusing efficiency / Gaussian power inside the
   clear aperture), optical power and transmittance interpolated linearly in wavelength.
   Each supported metric's error bound is the largest deviation over the calibration set
   plus the external numerical spread of the calibration cases (RCWA orders 101/201,
   propagation grids of 1024/2048 samples, from the convergence commands). For the
   field-angle domain, step 3 runs the model in OpenSci at each calibration angle and
   compares the Engine's focus with the external focus; the largest angle reached without leaving the bounds becomes `max_field_angle_deg`. The
   largest validated input beam radius (30 µm) becomes `max_beam_radius_mm`; `LINEAR_X`
   is the validated polarization;
   tool versions and the SHA-256 of the unit-cell library are the evidence. The result
   is `metalens_model.json`.
4. **OpenSci check**: the model is declared in a catalog-free Project. The Engine must
   reproduce the model's own thin-lens prediction, stay inside the stated errors on the
   test set, and refuse the counterexamples. The calibration set's deviations are
   reported as an envelope only: they set the bounds, so they cannot test them.

Results of the validated run on the published PyPI 0.9.0 wheel (external spread: 0.50 % in focus
position, 0.71 % in spot radius, 0.015 in efficiency):

| Quantity | Calibration set (sets the bound) | Test set (checks it) | Stated model error |
| --- | --- | --- | --- |
| Focus position | ≤ 0.30 µm | ≤ 0.32 µm | `waist_distance_*_mm` ± 0.0014 mm |
| Spot radius (Engine Gaussian vs external second moment) | −0.6 … −7.3 % | −1.6 … −5.8 % | `waist_radius_*_mm` ± 8.1 % |
| Delivered power | −5.4 … +15.9 % | −3.6 … +15.7 % | `power_W` ± 19 % |

All test cases stay inside the stated errors. The focal position follows the external
result closely across the band; the ideal Gaussian focus is 2–7 % smaller than the
external spot (phase discretization of the cells), which the stated radius error carries.
The focusing efficiency changes from 0.64 to 0.95 within 10 nm, so linear interpolation
between rows predicts the delivered power only to about 16 %; denser rows or
`transmittance_interpolation: POINTS_ONLY` give a tighter power model. At 2° the
Engine's focus stays inside the bounds (axial −0.23 µm, spot −5.2 %); at 5° the external
focus moves 1.56 µm toward the lens while the Engine's does not, more than the 1.4 µm
bound, so `max_field_angle_deg` is 2. The Engine reproduced the model's thin-lens
prediction to 4.4e-16, also with all 19 wavelengths as spectral lines of one source; the
power not delivered into the focus appears as the element's `UNMODELLED_RESPONSE` sink in
the power budget.

Counterexamples, all `OUT_OF_MODEL_SCOPE` with the affected target assessed
`INDETERMINATE`: a 450 nm source, outside the 490–580 nm rows
(`MODEL_WAVELENGTH_OUTSIDE_BAND`); 5°, 8° and 20° (`MODEL_FIELD_ANGLE_EXCEEDED`; at 20°
the external focus lies 15.6 µm closer to the lens and 5.6 µm closer to the axis than
the Engine's, with a 68 % larger spot and 13 % less power); and a target asking for a
metric the model was not validated for (`peak_irradiance_W_per_mm2`) through the element
(`METRIC_NOT_VALIDATED_FOR_ELEMENT`, no value). The scope code appears in the target's
`applicability_issue_codes`, so the value stays readable while its assessment states
that it does not establish validity outside the model. `--model per-wavelength-ideal` runs the
same comparison with one `IDEAL_THIN_LENS` per wavelength; that kind cannot state a
field-angle domain and returns `WARNING` at 5°, 8° and 20°. All runs with the 30 µm beam
report `GAUSSIAN_TRUNCATION` because the 100 µm aperture clips 0.4 % of it; the Engine
counts that power loss itself. The 20 µm and 25 µm beam cases are `WARNING` from the waist
target's Gaussian state.

Other unit-cell solvers fit the same pipeline as long as step 1 writes the same library
fields: meent (MIT) and torcwa (LGPL) install with pip; DFlat (`dflat-opt`) and metabox
depend on gdspy, which needs a C compiler on Windows, and metabox runs on Python 3.8 with
TensorFlow 2.11, so it belongs in its own environment exchanging files. Only grcwa
(GPLv3, as declared in its package metadata) was executed for this example.

A single external focus does not define a general component. The model is valid only
for its wavelength rows, field angles up to `max_field_angle_deg`, input beam radii up to
`max_beam_radius_mm` (calibrated at 20 and 30 µm, tested at 25 µm), x
polarization and the listed metrics; a target asking for another metric through the
element is outside the model's scope. It states no phase, so the network export and
wavefront results refuse paths through it. Its accuracy is bounded by the external
reference itself: the local periodic approximation and scalar propagation neglect cell
coupling and vector effects.
