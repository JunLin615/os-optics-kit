# Engine alignment

How os-optics-kit stays aligned with the os-optics engine and the optical catalog, and the checks that show it.

## Versions

| Item | Where | Rule |
| --- | --- | --- |
| Kit version | `tools/os_optics_kit/_version.py`, `baseline.json` `kit_version` | Independent semantic version of os-optics-kit; a release tag `vX.Y.Z` must name it |
| Supported engine | `pyproject.toml` dependency, `baseline.json` `engine.requirement` | One engine minor series, e.g. `>=0.13,<0.14`; both files state the same range |
| Verified engine | `baseline.json` `engine.verified_version` | The os-optics release the tests, tools and cases were verified with; inside the supported range |
| Catalog snapshot | `baseline.json` `catalog` | The public catalog release the cases run on, with its schema version and SHA-256 |
| Case baseline | each case's `CASE.md` (`engine`, `catalog`) and `expected.json` | The engine and catalog the case was verified with, and its verified execution outcome and target assessments |
| CI Python versions | `baseline.json` `python` | Oldest supported and newest; the CI matrices read this list |

`python scripts/check_repo.py` fails when `baseline.json`, `pyproject.toml` and `_version.py` disagree, when a
document pins `os-optics==` / `os-optics-kit==` to anything other than the baseline, when an engine range written
out in a file (outside `tests/`, `scripts/` and `CHANGELOG.md`) differs from `engine.requirement`, or when
`CHANGELOG.md` has no section for `kit_version` as its newest release.

A kit release supports one engine minor series. When a new engine minor is released, a reviewed pull request moves
the range, the verified version and, if the cases need it, the catalog snapshot; then the kit gets a new release.

## Deterministic catalog snapshot

Cases and catalog tests never use "whatever release is current". The runner prepares the snapshot in
`baseline.json` explicitly, in an isolated store (`OPENSCI_CATALOG_DIR` inside the output directory), with
automatic checks off (`OPENSCI_CATALOG_CHECK_INTERVAL=0`):

- CI: `os-optics catalog sync --release VERSION` from the public catalog service, then `catalog status` must
  report that release as active with the SHA-256 from `baseline.json`.
- Offline or local: `--catalog-file RELEASE.json` imports a release file after checking its SHA-256.

A different release can be checked with `--catalog-release VERSION --catalog-sha256 DIGEST` (or a file plus
`--catalog-sha256`) without changing `baseline.json`.

## Run the checks

The runner needs only the standard library. It creates a fresh environment in the output directory, installs the
engine and the kit there, prepares the catalog and runs the checks in `alignment-checks.json`:

```bash
python scripts/check_alignment.py --out run-alignment                     # verified engine from PyPI
python scripts/check_alignment.py --engine latest --out run-latest        # newest engine release
python scripts/check_alignment.py --engine 0.13.0 --out run-0130          # an exact release
python scripts/check_alignment.py --engine-wheel os_optics-X.Y.Z-py3-none-any.whl \
  --catalog-file public_optical_catalog.json --out run-local              # before a release is on PyPI
python scripts/check_alignment.py --kit-wheel dist/os_optics_kit-0.4.0-py3-none-any.whl --out run-kit-wheel
python scripts/check_alignment.py --optional all --out run-full           # include the optional checks
python scripts/check_alignment.py --only spec-be-report --out run-flow    # one check and what it needs
python scripts/check_alignment.py --skip cases-verify --out run-no-cases  # leave a check out
```

Every output directory must be new. The engine comes from PyPI unless `--engine-wheel` is given; the kit comes
from this checkout (installed, not editable) unless `--kit-wheel` is given. The kit is installed with `--no-deps`,
so the engine under test stays installed even outside the supported range; `dependency-consistency` (`pip check`)
then reports the mismatch. The runner removes `PYTHONPATH`/`PYTHONHOME`, disables user site-packages and checks
that `os_optics` and `os_optics_kit` are the requested versions and load from the new environment.

A local engine wheel is recorded as `engine_source.mode = local-wheel`, `publication_verified = false`: it shows
the kit works with that build, not that the release on PyPI does.

### Default checks

- `achromatic-imaging-compute`, `achromatic-imaging-study`: the native three-colour imaging run of the
  achromatic-imaging case, then its sensitivity and tolerance studies; `focus-requirement-compute`,
  `focus-requirement-study`: the two lens placements of the focus-requirement case, then its placement sweeps.
- `kit-cli`, `summary-missing-run`: the installed command and its JSON error contract.
- `offline-tests`: the whole test suite before the catalog is prepared (catalog tests skip themselves).
- `beam-summary`, `saved-run-summary`: a catalog beam expander run and its saved-result summary.
- `catalog-tests`: the catalog-dependent test classes with `OS_OPTICS_KIT_REQUIRE_CATALOG=1`, so a missing
  catalog fails them instead of skipping.
- `cases-verify`: `os-optics-kit cases verify --all` (the CI engine job skips it and verifies the cases in its own
  step, see [In CI](#in-ci)).
- The design workflows of the expansion-focus, polarization-control and mirror-alignment cases, one parameter
  sweep, one sensitivity and one tolerance study, and the catalog tool chain (candidates, edit, sweep, move,
  compare, plot, report) of the beam-expander studies.
- `tolerance-polarization-workers`: the tolerance study in two worker processes; `study-resume`: `study run
  --resume` of the completed tolerance study. Both checks assert that the command completes; equality with the
  serial study, sample reuse, design workflows and the benchmark with two workers are asserted by
  `tests/test_study.py`, `tests/test_study_tools.py` and `tests/test_study_workers.py` in `offline-tests`.

Optional checks (full workflow studies, seed verification, external-tool cases with their own pinned
requirements) run with `--optional NAME` or `--optional all`. A check with a `requirements` file runs in its own
environment with the engine, the kit and that file. pip resolves an `os-optics-kit` requirement of that file from
PyPI before the kit under test replaces it, so a case that pins the kit version being prepared runs once that
version is published.

### Add a check

Add an object to `checks` in `alignment-checks.json`; checks run in list order.

| Field | Default | Meaning |
| --- | --- | --- |
| `name` | required | Unique lower-case name (`[a-z0-9-]`); also the log file name |
| `argv` | required | Command starting with `{python}`; paths relative to the repository root |
| `description` | none | One line |
| `expected_exit` | `0` | Exit code that counts as success (an unmet target is `1`, a refusal `4`) |
| `expected_status` | none | String or list; stdout must be one JSON document whose `status_field` has one of these values |
| `status_field` | `"status"` | Key read for `expected_status` |
| `json_error` | `false` | Success requires empty stdout and a JSON `error` object on stderr |
| `needs_catalog` | `false` | Prepare the catalog snapshot first; skipped when that fails |
| `needs` | `[]` | Earlier checks that must pass first |
| `requirements` | none | Requirements file for a separate environment |
| `requires_python` | none | Minimum Python, e.g. `"3.12"`; skipped with that reason on an older interpreter |
| `optional` | `false` | Run only when selected with `--optional` |
| `timeout` | `180` | Seconds |
| `env` | `{}` | Extra environment variables |

Placeholders: `{python}`, `{runs}` (`OUTPUT/runs`) and `{out}` (`OUTPUT`). Step names of the runner itself
(`install-engine`, `install-kit`, `catalog-sync`, ...) are reserved. `tests/test_alignment_check.py` validates the
list.

## Read the result

The command prints a compact JSON status and the report path; exit `0` means every executed check passed.

| Evidence | Contents |
| --- | --- |
| `report.json` | Requested, resolved and installed engine versions, engine and kit sources with SHA-256, Python, catalog source, check selection, and every check with category, status, command, exit codes, error and log paths |
| `environment.json` | Installed versions and locations, Python and platform |
| `logs/` | stdout and stderr of every installation and command |
| `runs/` | Outputs of the checks |

`python scripts/report_alignment.py run-alignment/report.json` renders the Markdown summary, including a command
that reproduces the run. Failures carry a category:

- `package_api`: installation, import, changed APIs or outputs, failed tests, unexpected outcomes.
- `catalog_network`: the catalog service could not deliver the release (`CATALOG_SERVICE_UNREACHABLE`,
  `CATALOG_RELEASE_NOT_FOUND`, transport errors, ...). This is not an engine regression.
- `catalog_data`: the release cannot be used or is not the expected one (`CATALOG_INTEGRITY_FAILED`, a different
  SHA-256 or active release, product or model codes such as `EXAMPLE_UNAVAILABLE`). A refused query
  (`CATALOG_QUERY_INVALID`) or an unsupported model kind is `package_api`.
- `setup`: the baseline, the check list, version discovery or environment creation failed.

A check that could not run is `SKIPPED` with the category of the check that blocked it and `blocked_by`.

## In CI

- **CI** (`.github/workflows/ci.yml`, every pull request, push to `main` and merge-queue run): the engine job first
  asks PyPI for `engine.verified_version`. When it is not published yet, the job says so in its summary and passes
  without running engine checks. When it is published, the job runs this runner on it with the catalog snapshot and
  `--skip cases-verify`, then verifies the cases in one step, `os-optics-kit cases verify ... --jobs 4`, with the
  runner's environment and catalog store. The step writes `cases-verify.json` and one summary line (cases and
  MATCH/MISMATCH/ERROR counts, or why nothing was verified); any case that is not MATCH fails the job. The step
  fails on a non-zero `cases verify` exit code and also when the exit code is 0 but `cases-verify.json` is missing
  or its status is not MATCH.
  - Selection (`scripts/changed_cases.py`): a pull request verifies the case directories its diff touches (a deleted
    case is not verified), or every case when it changes a file that `cases verify` or the selection depends on:
    `baseline.json`, `pyproject.toml`, `alignment-checks.json`, `.github/workflows/ci.yml`,
    `scripts/changed_cases.py`, `scripts/verified_tree.py`, `scripts/check_alignment.py` and
    `scripts/requirements-alignment.txt` (they build the step's environment and catalog store), and `__init__.py`,
    `__main__.py`, `_version.py`, `cli.py` (the command's entry point and exit code), `cases.py`,
    `case_taxonomy.py`, `_engine.py`, `_frontmatter.py`, `_resources.py` under `tools/os_optics_kit/`. A push to
    `main` and the merge queue verify every case.
  - Once per tree (`scripts/verified_tree.py`): a run that verified every case with every case MATCH uploads the
    artifact `cases-verified-TREE` (TREE is `git rev-parse HEAD^{tree}` of the checked-out commit; for a pull
    request that is the merge commit, the tree `main` gets when the up-to-date pull request merges). Before
    verifying, a push to `main` or a merge-queue run looks for that artifact with the job's read-only token
    (`actions: read`) and skips verification, naming the verifying run in its summary, only when the artifact is
    not expired, comes from a run of this repository (not a fork), and its record names the same tree, every case
    MATCH, and the engine version and catalog SHA-256 of `baseline.json`. Anything else, an API or download error
    included, means the cases are verified.
- **Scheduled checks** (`.github/workflows/scheduled.yml`, weekly and on demand): the verified engine on Linux,
  Windows and macOS with every Python version in `baseline.json`, all cases through the default check
  `cases-verify` (never skipped by an earlier verification); and the newest engine release on PyPI
  as a candidate when it is newer than the verified one. On demand, a candidate version and optional checks can be
  given.

- **Release** (`.github/workflows/release.yml`, a published GitHub release; a manual run only builds and checks):
  refuses a tag whose commit is not on `main`, runs `check_repo.py` (including the changelog section of the
  version), fails when `engine.verified_version` is not on PyPI (`python scripts/engine_release.py
  --require-published`), then builds and checks the wheel and the source archive before publishing them.

Only the scheduled reporter job may write issues. It reads each report with the reporter from the scheduled
commit and opens one issue per new observation: a new unverified engine release or a new failure pattern.
Identical observations (same version, status and failed checks) share one issue, open or closed.

## Update after a new engine or catalog release

1. Run the runner against the new release (`--engine VERSION`, or `--catalog-release VERSION --catalog-sha256
   DIGEST` for a new catalog). Resolve `package_api` and `catalog_data` failures; a `SKIPPED` check has not passed.
2. Re-verify every case on the new baseline. A case whose outcome changes gets a new `expected.json`, its
   `CASE.md` `engine`/`catalog` fields and, if they changed, new images.
3. In one reviewed pull request, update `baseline.json` (and the `pyproject.toml` range for a new engine minor),
   the cases, the indexes (`os-optics-kit cases index --write`) and every document `check_repo.py` reports.
4. Release the kit with a new version once the pull request is merged.

A new engine minor moves the supported range; the next section is that procedure in full.

## Move the kit to a new engine minor

A kit release supports one engine minor, so moving to a new minor is one reviewed pull request that ends in a new
kit minor release. The pull request can be prepared and verified before the engine release is on PyPI; it is
merged and released only after.

### Files that move together

| Group | Files | Kept equal by |
| --- | --- | --- |
| Engine requirement | `pyproject.toml` (the `os-optics` dependency), `tools/os_optics_kit/_engine.py` (`ENGINE_REQUIREMENT`), `baseline.json` (`engine.requirement`, `engine.verified_version`) | `check_repo.py` (`baseline`) |
| Kit version | `tools/os_optics_kit/_version.py`, `baseline.json` `kit_version`, a new `CHANGELOG.md` section, `cases.json` and `skills.json` (`cases index --write`, `skills index --write`) | `check_repo.py` (`baseline`, `changelog`), `cases index --check`, `skills index --check` |
| Skill pages | the `requires:` line of every skill that needs the engine, and `templates/SKILL.md` | `skills index --check`, `check_repo.py` (`ranges`) |
| Ranges written out | recovery messages, docstrings, guides, the engine line of the case requirement files | `check_repo.py` (`ranges`) |
| Exact pins | documents and case requirement files that pin `os-optics==` or `os-optics-kit==` | `check_repo.py` (`pins`) |
| Cases | each case's `CASE.md` (`engine`, the baseline sentence), `expected.json`, the verified-with lines of its READMEs, recorded data that names the engine build, images whose outcome changed | `cases verify --all` |
| Guides | the version statements of `README.md`, `README.zh-CN.md`, `AGENT_GUIDE.md` and the guides under `docs/` | review (`check_repo.py` reports the ranges and pins among them) |
| Tests | tests that state the requirement or skip with it | the test suite |

A new engine minor can change every engine input hash: runs made with the previous minor then read as not current,
and the changelog section says so ("run the Project again").

### Verify against a local engine wheel

Before the engine release is on PyPI, verify the branch against the engine's wheel:

```bash
python scripts/check_alignment.py --engine-wheel os_optics-X.Y.Z-py3-none-any.whl \
  --catalog-file RELEASE.json --out run-local
python scripts/check_alignment.py --engine-wheel os_optics-X.Y.Z-py3-none-any.whl \
  --catalog-file RELEASE.json --only cases-verify --out run-local-cases
```

The report records `engine_source.mode = local-wheel` and `publication_verified = false`: evidence that the kit
works with that build, not with the published release. Re-verify every case on it and commit the outcomes that
changed with the pull request.

### The engine job must have run

While `engine.verified_version` is not on PyPI, the CI job "Engine checks (published os-optics)" passes without
running anything and says so in its summary. For this pull request that is not a result. Merge it only after the
engine release is on PyPI and a CI run of the final head shows the job ran: `baseline.json` changed, so every case
is verified against the published release. The release workflow enforces the same rule: a kit release whose
verified engine is not on PyPI fails to build.

### Order of publication

1. Release the current kit minor, if it has unreleased changes, while it still requires the previous engine minor.
2. Prepare the move on a branch: the files above, the new kit minor version and its changelog section, verified
   against the local engine wheel.
3. The engine release is published on PyPI.
4. Re-run CI on the pull request; the engine job runs against the published release. Merge.
5. Tag the merge commit and publish the kit release.

Never publish the kit release before the engine release: `pip install os-optics-kit` would keep resolving the
previous kit, and the new kit version could not be installed at all. Between steps 3 and 5 the latest kit holds the
engine at the previous minor; finishing step 2 before step 3 keeps that window short. The weekly candidate check
reports the new engine release as unverified until step 5.

### Study evaluation

The study executor evaluates every sample through a replaceable evaluator; today's evaluator runs each sample as
one complete engine run. The kit's study evaluator adopts the engine's session-based evaluation (one evaluation
session per worker that keeps the loaded catalog and compiled elements between samples, and can evaluate only the
selected targets and what they read) in the kit release that moves the supported range to the engine minor that
offers it. The kit uses an engine interface only in a release whose required engine range offers it.
