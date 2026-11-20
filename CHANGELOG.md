# Changelog

Releases of os-optics-kit, newest first. Each release states the os-optics range it requires and the os-optics
version and catalog release it was verified with (`baseline.json`). A release heading is the version on PyPI and
the tag `vX.Y.Z`; `python scripts/check_repo.py` requires a section for the version in `baseline.json`.

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
