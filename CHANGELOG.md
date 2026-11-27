# Changelog

Releases of os-optics-kit, newest first. Each release states the os-optics range it requires and the os-optics
version and catalog release it was verified with (`baseline.json`). A release heading is the version on PyPI and
the tag `vX.Y.Z`; `python scripts/check_repo.py` requires a section for the version in `baseline.json`. Changes
that are not in a release yet come first, under "Unreleased".

## Unreleased

### Added

- `os-optics-kit mtf RUN --monitor PLANE` (or `--imaging FILE [--run RUN]`): the engine's optical MTF per spectral
  line as `mtf.csv` (a status per value: `KNOWN`, `NOT_CONVERGED`, `BEYOND_NYQUIST`, the engine's state of a
  declined curve, `ABSENT`), `mtf.json` (run and analysis identity, request, conventions, curves with model basis
  and convergence, image point, fields not analysed with their reason) and one figure per line in the kit themes;
  one in-process imaging analysis, or none with a saved analysis. Optical MTF only: no pixel aperture, sampling or
  SFR, no polychromatic weighting, no PASS/FAIL. Skill `mtf`, `docs/en/mtf.md` (#31).
- `os-optics-kit field-points PROJECT --plane PLANE --out DIR (--angles-deg | --object-heights-mm |
  --image-heights-mm)`: field points of a native Project as copies of its on-axis source turned across the beam
  and aimed at the aperture-stop centre, all in one evaluated Project (the input is never modified; every change is
  listed in `derivation`). Per point: the chief ray's hit point, real image height and angle, a first-order
  comparison (f tan(theta) or m h, not a distortion figure) and, with `--mtf-frequencies`, the tangential and
  sagittal optical MTF of each spectral line, with sampled field minima per line and over all lines (`CONFIRMED`
  only when every sample is exact, else an `UNCONFIRMED` upper bound with what is missing). The report binds the
  input by its sha256, its LF-normalized sha256 (os-optics-lens records) and the Engine's hashes; `--write-run`
  writes a run that `field`, `fans` and `mtf` read. Skill `field-points` with the four field-point traps and the
  Lens record mapping, `docs/en/field-points.md` (#48, #60).
- `os-optics-kit optimize SPEC.json --out DIR`: bounded local optimization of a Project (trf least squares on the
  evaluation adapter's residuals, or SLSQP with every constraint margin >= 0). Finite-difference probes are
  counted, cached and kept in bounds; `max_evaluations` is a hard cap. Writes `spec.json`, `history.jsonl`,
  `result.json` (termination, counts, best/start/closest with active constraints, infeasible reasons, reproduction
  of `best-project.json` in a fresh session, a dense re-check) and `best-project.json`. Exit 0 `CONVERGED`, 1
  `NO_FEASIBLE`, 2 `STOPPED` (also when trf converges just outside an active constraint: `CONVERGED_INFEASIBLE`), 130
  interrupted. Skill `optimize`, `docs/en/optimize.md`, case specs in
  `interop-optimization/studies` and `aspheric-lens/studies` (#33).
- `os_optics_kit.imaging_results`: one reader of the imaging analysis (lines, curves with their state, convergence
  and a usable flag, values with a status, scalars, fields not analysed) used by `mtf`, `field-points`, `sfr`, the
  evaluation adapter and the case figure scripts.
- Evaluation adapter: `paraxial`, `solids` and `rays` quantity sources; pupil-ray counts on spot readings with
  `Problem.ray_loss` (`infeasible` / `report`); MTF requests narrowed to the fields and lines every constraint
  names (#33).
- `edit`: `set_optical_model_value {model_id, path, value}` sets one number of a declared optical model by JSON
  pointer; `sensitivity`, `tolerance` and `optimize` read its nominal from the base Project (#33).

### Changed

- The sonnar-telephoto-lens, aspheric-lens and midwave-infrared-imager figure scripts run the imaging analysis
  in-process and read it through `imaging_results`; achromatic-imaging's `analysis.py` reads its MTF curves the
  same way. Recorded figures are unchanged (#31).
- The `field`, `fans` and `sfr` skills point to `mtf` for MTF curves; `field` points to `field-points` for
  creating field points. `field_aberrations` exposes `stop_crossings` and `paraxial_efl`;
  `workflow_common.prepare_output` holds the output-directory rule of `mtf`, `field-points` and `optimize`
  (`--overwrite` removes only what the run writes; an input it would replace or remove is `OUTPUT_IS_INPUT`);
  `geometry3d` holds the quaternion helpers.

### Fixed

- `sfr --design` no longer takes a curve the engine declines (a geometric MTF outside its regime, the curves of a
  stand-in pupil) although os-optics 0.14 keeps its array: `DESIGN_CURVE_UNAVAILABLE` with the engine's state and
  reason (#31).

## 0.5.0

Requires `os-optics>=0.14,<0.15`; verified with os-optics 0.14.0 and catalog release 2026-10-28.1. Every case
is re-recorded on os-optics 0.14.0.

### Added

- `zoom`: positions of two moving groups per requested focal length with the focus held on a fixed image plane,
  solved by continuation from known anchors with the engine's paraxial analysis; writes a Project with one named
  configuration per sample, a trajectory with achieved EFL, focus error, travel and clearance per sample, and
  optionally a sweep study of the configurations (`docs/en/zoom.md`, case `three-group-zoom`).
- `os-optics-kit fans RUN --monitor PLANE`: ray fans of a saved run at an image plane, per field point and
  wavelength: transverse and longitudinal aberration and per-ray OPD across the pupil, read from the run's published
  ray data (nothing is traced again), with the engine's spot and OPD numbers beside them. Skill `fans`.
- `os-optics-kit field RUN --monitor PLANE`: field curvature and distortion of a saved run per field point and
  wavelength: image height, distortion against `EFL tan(theta)` with both heights on one plane (by default the
  engine's paraxial EFL and rear focal plane per wavelength, each chief ray carried to that plane; with `--efl`, the
  stated EFL on the measurement plane), tangential and sagittal focus and astigmatism, the chief ray's crossing of the
  aperture stop, and every declared field without a row in `unreached_fields` with its reason. Skill `field`.
- Tolerance compensation: an optional `compensation` object in a `tolerance` spec names compensators (edit ops with
  `"$value"`, a unit, a nominal and bounds) and an objective over the study's targets. Each sample is then reported
  as built and compensated (`result_stage`), the compensators are solved within their bounds, and a solve that does
  not succeed keeps the as-built result with a reason code (`NOT_CONVERGED`, `BOUNDS_HIT`, `WORSE_THAN_AS_BUILT`,
  ...). `--no-compensation` ignores the object. Examples: `cases/expansion-focus/studies/tolerance_expander_refocus.json`
  and `cases/mirror-alignment/studies/tolerance_alignment_compensated.json`.
- Evaluation adapter (`os_optics_kit.study.evaluation_adapter`): a Python interface from named, bounded variables to
  scaled objective and constraint residuals of a design, on the engine's evaluation session, for an external solver.
  Every candidate returns a result; one the engine cannot evaluate carries a reason code. Example:
  `cases/expansion-focus/adapter_example.py`.

### Changed

- Cases: every case is re-recorded on os-optics 0.14.0 with catalog release 2026-10-28.1. Cases first recorded on
  os-optics 0.13 state the assumptions those records made: vacuum (`environment.pressure_pa` 0) and, where the Project
  declares its glass, lossless glass (`absorption_coefficient_per_mm` 0); their target and imaging values are
  unchanged. mirror-alignment and polarization-control run in standard air with identical values. beam-expander's run
  status is `UNKNOWN` on os-optics 0.14 (the catalog's N-BK7 states no bulk loss) with its target and studies
  unchanged; aspheric-lens, midwave-infrared-imager and sonnar-telephoto-lens now complete (`COMPLETED`, was
  `PARTIAL`); cylindrical-light-sheet is rebuilt by the 0.14 lens builder in standard air (entrance radius
  25.979142 mm) and its table re-recorded.
- `study run DIR` uses the evaluator the study records (a compensated tolerance study records its own); a different
  `--evaluator` is refused with `EVALUATOR_MISMATCH`, and printed `next` commands name the recorded evaluator.
- The kit requires `os-optics>=0.14,<0.15`.
- The four interop cases' requirement files ask for `os-optics-kit>=0.5,<0.6`, which installs with os-optics 0.14.

### Fixed

- `zoom`: a sample no anchor agreement confirms is `UNVERIFIED` (reason `CONTINUITY_UNVERIFIED`): after a march fails
  the agreement check at the next anchor, the samples between the two anchors, and those marched outward from an end
  anchor whose only check failed, no longer pass; `continuity` in the trajectory, summary and report names each break.
- `zoom`: the solved model is the exported Project. An optical element before the image plane that is in no group and
  whose aperture reaches the axis is refused with `UNGROUPED_PATH_ELEMENT` (with the groups' and the full path's EFL),
  and an `image_plane.position_mm` that misses the Project's observation behind the last group is refused with
  `IMAGE_PLANE_MISMATCH`, both before anything is written. A cylindrical, biconic or toroidal element the paraxial
  analysis takes only with a section axis counts as an element on the path (it was skipped with the detectors and
  apertures, #50). The summary, `trajectory.json` and `report.md` name the observation the focus is held on and the
  observations the Project's targets measure on, and say so when they differ (#58).
- CI: the engine job installs exactly the engine the gate selects from `baseline.json` (pinned with `==`) and shows
  the installed os-optics version, wheel and SHA-256 in its summary; a change to `scripts/engine_gate.py` verifies
  every case.
- Tolerance compensation: `--resume` runs the compensated samples again when the objective, a compensator (ops,
  nominal, bounds, tolerance, unit, name) or a solver setting changed, and keeps them when only a description changed;
  a sample interrupted during its solve runs again instead of being recovered without its compensation; with
  `--workers`, `--time-limit-s` applies to each Engine run of a compensated sample (the worker stop allows
  `compensation.max_evaluations + 2` runs).
- `field`: the chief ray's stop offset is measured from the centre of the stop's opening (`opening_center_mm`) in the
  design frame, and the crossed stop is identified by its Engine surface id, so a stop holding a sample is recognised;
  an unresolved crossing or stop geometry reads `CHIEF_RAY_STOP_UNRESOLVED`. The result lists `aperture_stops`. A plane
  that only reflected paths reach is refused with `REFLECTED_PATH_UNSUPPORTED`.
- `fans`: the engine's imaging spot is set beside a path's fans only when the imaging document is of the same run,
  plane, source, wavelength, route and crossing; otherwise the fans are kept and `engine.spot_rms_comparison` gives the
  reason (`IMAGING_CROSSING_MISMATCH`, `IMAGING_PATH_MISMATCH`, `IMAGING_RUN_MISMATCH`, ...).
- `zoom`: an anchor outside the requested focal-length range no longer widens the pass intervals or coverage or changes
  the counts, step statistics, worst values or status; it still seeds the solve and the branch check and is reported
  under `anchors_outside_range`.
- Evaluation adapter: finite residuals whose `objective` or `residual_objective` exceeds the float range give an invalid
  result with the reason `AGGREGATE_NOT_FINITE` instead of a valid one with an infinite objective; the `OUT_OF_BOUNDS`
  residual growth keeps the sum of squares within half the float range; every result serializes with
  `json.dumps(..., allow_nan=False)`.
- `summary`, `compare`, `report`, `edit --run` and `study run --resume` check a saved run's files against their
  recorded hashes again: with os-optics 0.14 the engine reads a run as stored unless asked to verify it. A run whose
  files do not verify is `HISTORICAL` (`RUN_UNVERIFIED`) and is never adopted as a study sample.
- `edit` refuses an edited Project that does not pass the engine's check (`PROJECT_REJECTED` with the check's
  problems) instead of failing with `KeyError` on os-optics 0.14.
- The ideal expander and focus design (`optical_designs.expander_focus`, used by the `expansion-focus` workflow)
  states a vacuum environment (`optical_designs.EXPANDER_FOCUS_ENVIRONMENT`), the medium of its closed-form
  Gaussian reference: os-optics 0.14 propagates a Project that states no environment in standard air. The
  `expansion-focus` case Project is that design again and the case is recorded on os-optics 0.14.0.

## 0.4.0

Requires `os-optics>=0.13,<0.14`; verified with os-optics 0.13.0 and catalog release 2026-10-28.1.

### Added

- Study execution (`os_optics_kit.study`): `sweep`, `sensitivity`, `tolerance`, `study candidates` and the design
  workflows of the cases plan their samples as requests, execute them through one executor and aggregate the saved
  records. A study run with several workers equals the serial run: sample ids, values and result hashes come from
  the plan, not from scheduling.
- `--workers N|auto`, `--resume`, `--plan-only`, `--time-limit-s` and `--max-memory-mib` on `sweep`,
  `sensitivity`, `tolerance` and `study candidates`; `--workers N|auto` on the design workflows of the cases.
- `os-optics-kit study run | status | aggregate | candidates | bench`: execute a saved plan, inspect a study,
  rebuild its tables without running the Engine, compare a set of Project files, and measure how worker processes
  scale on your machine.
- Study directory: `study.json` (plan hash, Engine version and build, catalog release, kit version), `plan.jsonl`,
  `requests/`, `journal.jsonl`, `samples/<id>/sample.json`. `--resume` reuses a sample only when its request, the
  Engine build and the catalog release are unchanged.
- `sweep` takes any number of parameters. `plot sweep --fix NAME=VALUE` (asset key `fix` in `present`) plots a
  slice; a selection that leaves more than two parameters free is refused with `PLOT_NEEDS_SLICE`, which names
  them and their value counts.
- Value kinds of target values: study tables add `target:<id>:value_kind` (`EXACT`, `BOUNDED`,
  `QUANTITY_UNKNOWN`, `OUTSIDE_MODEL`, `UNDECIDED`, `NO_VALUE`). `value_use: "reported"` in sweep, sensitivity and
  tolerance specs and `compare --value-use reported` use every finite value and state the kinds used; the default
  `exact` uses `EXACT` values only, as before.
- Study rows `execution_outcome` and `budgets_exhausted`, counts `by_execution`, and the sweep constraint
  `{"execution": ["COMPLETED"]}`, which makes only runs that reached no compute budget feasible.
- `sensitivity` and `tolerance`: an omitted `nominal` of a parameter with `set_*` ops is read from the base Project
  (`nominal_source` in `study.json` and `summary.json`); ops whose fields hold different base values refuse the
  parameter (`NOMINAL_AMBIGUOUS`).
- `sensitivity` accepts one step size per parameter: 1 + 2N runs, no step-consistency check (flag
  `STEP_CHECK_UNAVAILABLE`).
- `--overwrite` on `cases verify --out` (replaces the case's run only) and `measurements --out` (replaces the five
  files it writes; other files stay).
- Skill `study`; guide "Studies: plan, execute, aggregate, present" (`docs/en/studies.md`, `docs/zh-CN/studies.md`).
- Cases `achromatic-imaging`, `aspheric-lens`, `bounded-aberration-correction`, `cylindrical-light-sheet`,
  `focus-requirement`, `midwave-infrared-imager`, `phase-only-flat-top`, `sonnar-telephoto-lens` (18 in total).
- `os-optics-kit sfr IMAGE --roi X,Y,W,H --out DIR`: slanted-edge SFR (ISO 12233 e-SFR) of one image region, with
  the edge angle, fit residuals, `sfr50`, SFR at Nyquist and diagnostics; `--design` puts a saved
  `os-optics results imaging` MTF on the same axes and states the input differences. No Engine run is needed;
  PNG and TIFF input and the plots need `os-optics-kit[plot]`, and without numpy the command refuses with
  `NUMPY_MISSING`. Skill `sfr` (17 skills) and the guide "Measure slanted-edge SFR" (`docs/en/sfr.md`,
  `docs/zh-CN/sfr.md`).
- Case taxonomy: every case states a `class` (`lens`, `free-space`, `integrated-path`), one to three `domain`
  values, `provenance`, `publication`, an optional `source` and tags from a closed vocabulary; the scale band is
  derived from `project.json`. `cases list` filters by `--class`, `--domain`, `--provenance`, `--publication`,
  `--scale`, `--tag` and `--catalog`; `cases vocabulary` prints the values. Guide "Case library"
  (`docs/en/case-library.md`, `docs/zh-CN/case-library.md`).
- Case images: `images.json` records every image of a case with its SHA-256, the Engine options of each system
  view and the run it was drawn from. `cases images NAME` renders the system views with the Engine's browser-free
  snapshot (`os-optics view RUN --no-browser --snapshot`) and records them; `cases images NAME --figure
  images/X.png --script SCRIPT.py` records a figure drawn by a case script. A case has at most five images: one to
  three system views (a 3D view first, the section of a lens) and at most three figures, PNG of at most 400 KiB.
- `cases verify` compares the run identity recorded in `images.json` (`project_hash`, `engine_input_hash`) with
  the fresh run, so a case's images come from the design that verifies; `--jobs N` verifies up to N cases at once.
- `contrib case` takes `--class`, `--domain`, `--tags`, `--provenance`, `--publication` and `--source`, renders
  the system views, writes `images.json`, `README.md` and `README.zh-CN.md`, and adds `cases.json` and the README
  case tables to the patch. `python scripts/case_tables.py --write | --check` generates the case tables of both
  READMEs from `cases.json`.
- A planned study with nothing to read is refused by `study aggregate`, `plot sweep`, `report`, `compare` and
  `present` with the command to run next: `STUDY_NOT_RUN` (no sample has a result), `STUDY_INCOMPLETE` (executed in
  part and not aggregated since), `STUDY_NOT_AGGREGATED` (every sample has a result, no `summary.json`).
  `study aggregate` of a study executed in part tabulates what ran and exits 2 with status `INCOMPLETE`, the
  samples without a result and the `next` command. `study status` reports `progress` (`NOT_RUN`, `PARTIAL`,
  `EXECUTED`) and the `next` command.

### Changed

- The requirement class of a run (`summary`, `edit --run`, study outcomes) is the class of its selected targets'
  assessments; a target a reached budget left undecided keeps the run `UNKNOWN`. An unmet target makes the run
  `FAIL` also when another target is outside its model. A run whose selected targets are all assessed
  `NOT_APPLICABLE` is `UNKNOWN` (exit 2; it was `NOT_APPLICABLE`, exit 0), as under the os-optics `requirements`
  exit contract. A `PARTIAL` run is never `PASS` or `NOT_APPLICABLE` (`UNKNOWN` when its targets are all met or
  none is selected, as before) and exits 3 whatever its class (it exited by its class), the os-optics
  `requirements` code for a calculation that did not complete. `outcome.execution` and the budgets stay beside
  the class; a `FAILED` or `CANCELLED` run is still an error (exit 4).
- `sweep`, `sensitivity` and `tolerance` write a study directory with the files above next to the tables and
  reports they wrote before. The tables list the samples in plan order.
- `max_evaluations` is optional in sweep, sensitivity and tolerance specs (absent: no cap); `--max-evaluations` of
  the design workflows defaults to no bound. A stated cap is checked before any run, as before.
- A study value whose Project cannot be built is one refused sample (stage `build`); the other values run.
- A study interrupted with Ctrl-C exits 130 and prints the command that resumes it. A refused design, a time or
  memory limit or a lost worker fails its own sample; the other samples complete.
- A study directory is owned by one execution; a second run, aggregation or re-plan is refused (`STUDY_RUNNING`),
  and a file system without file locks is refused (`STUDY_LOCK_UNAVAILABLE`).
- `summary`, `compare` and `report` read a run whose files do not match their recorded hashes as stored, with
  `integrity.consistent: false` and the problems (`summary` also writes a `RUN_INTEGRITY_FAILED` warning to
  stderr); they refused it before. `--resume` never reuses such a run.
- `compare STUDY --project` and `report` give a study one freshness record against the current Project
  (`study_freshness`); samples are labelled `HISTORICAL` only when the study is.
- `cases verify` and `contrib case` run each case once with `os-optics project run`. `expected.json` schema 2
  records the execution outcome and every target assessment in place of the aggregate status; a schema 1 file is
  refused with `EXPECTED_SCHEMA` (`cases verify NAME --update-expected` writes the new one). `contrib case`
  packages a `PARTIAL` run with the warning `RUN_EXECUTION_PARTIAL`.
- `cases.json` is index schema 2: each entry adds `class`, `domain`, `provenance`, `publication`, `source`,
  `scale` and `images`. Every case has `README.md` and `README.zh-CN.md`, which show exactly the images recorded in
  `images.json` and link each other; `cases index --check` checks the record, the files and both READMEs. The
  lens model of `focus-requirement` is named after its 50 mm focal length (`ideal-f50`); its targets and outcome
  are unchanged.

### Fixed

- Chinese figures on macOS and Windows: `present` finds the Chinese fonts these systems ship (PingFang SC,
  Hiragino Sans GB, Heiti SC, Songti SC, Microsoft YaHei, SimHei, DengXian, SimSun and others), uses a family's
  light face when it has no regular one, reads the indexed face of a `.ttc` collection, and otherwise uses
  installed fonts that draw Chinese; `FONT_MISSING` remains for a system without one. The contact sheet keeps
  families installed only in a light face.
- A targets plot in a short panel keeps its value axis in proportion to the values; panels whose labels still do
  not fit are listed in `details.labels_cramped`. Shared-axis captions print display units (`µm`, not `um`).
- The gallery shows animation controls below the video instead of over its last line.
- The Chinese playback title of a parameter animation is a complete phrase.

Studies saved by 0.3.0 are still read by `plot`, `report`, `compare` and `present`; they hold no plan, so run them
again to use `--resume`.

## 0.3.0

Requires `os-optics>=0.13,<0.14`; verified with os-optics 0.13.0 and catalog release 2026-10-28.1.

### Added

- `os-optics-kit present`: presentation material from saved results, with themes, languages, figure sizes and
  display units from a saved configuration; an offline gallery and a portable bundle; comparison figures and
  multi-panel layouts; orbit, camera-transition and parameter-sample animations.
- Skill `present`: prepare, restyle and hand over presentation assets.

### Fixed

- Scheduled compatibility checks no longer open duplicate observation issues.

## 0.2.0

Requires `os-optics>=0.13,<0.14`; verified with os-optics 0.13.0 and catalog release 2026-10-28.1.

### Changed

- Moved to os-optics 0.13. `summary`, `edit --run` and the studies read 0.13.0 results: a partial run is
  `UNKNOWN` (exit 2) even when its targets are met; a failed or cancelled run is an error (exit 4).
- Cases verified against os-optics 0.13.0; their results equal those of 0.12.0.
- Runs made with os-optics 0.12.0 read as not current: run the Project again.

### Added

- `render`: the Optical Simulation View at a chosen camera pose (`os-optics-kit[render]`), with the skill `render`.

## 0.1.0

Requires `os-optics>=0.12,<0.13`; verified with os-optics 0.12.0 and catalog release 2026-10-28.1.

First release: tools (Project edits, parameter sweeps, run comparison, plots, reports, sensitivity and tolerance
studies, catalog candidates, measurement processing), 10 engine-reproducible cases, 13 agent skills with a
machine-readable index, and `os-optics-kit contrib case` / `contrib tool`.
