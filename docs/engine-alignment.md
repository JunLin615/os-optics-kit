# Engine alignment

How os-optics-kit stays aligned with the os-optics engine and the optical catalog, and the checks that show it.

## Versions

| Item | Where | Rule |
| --- | --- | --- |
| Kit version | `tools/os_optics_kit/_version.py`, `baseline.json` `kit_version` | Independent semantic version of os-optics-kit; a release tag `vX.Y.Z` must name it |
| Supported engine | `pyproject.toml` dependency, `baseline.json` `engine.requirement` | One engine minor series, e.g. `>=0.13,<0.14`; both files state the same range |
| Verified engine | `baseline.json` `engine.verified_version` | The os-optics release the tests, tools and cases were verified with; inside the supported range |
| Catalog snapshot | `baseline.json` `catalog` | The public catalog release the cases run on, with its schema version and SHA-256 |
| Case baseline | each case's `CASE.md` (`engine`, `catalog`) and `expected.json` | The engine and catalog the case was verified with, and its verified run status and target assessments |
| CI Python versions | `baseline.json` `python` | Oldest supported and newest; the CI matrices read this list |

`python scripts/check_repo.py` fails when `baseline.json`, `pyproject.toml` and `_version.py` disagree, or when a
document pins `os-optics==` / `os-optics-kit==` to anything other than the baseline.

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

- `kit-cli`, `summary-missing-run`: the installed command and its JSON error contract.
- `offline-tests`: the whole test suite before the catalog is prepared (catalog tests skip themselves).
- `beam-summary`, `saved-run-summary`: a catalog beam expander run and its saved-result summary.
- `catalog-tests`: the catalog-dependent test classes with `OS_OPTICS_KIT_REQUIRE_CATALOG=1`, so a missing
  catalog fails them instead of skipping.
- `cases-verify`: `os-optics-kit cases verify --all`.
- The design workflows of the expansion-focus, polarization-control and mirror-alignment cases, one parameter
  sweep, one sensitivity and one tolerance study, and the catalog tool chain (candidates, edit, sweep, move,
  compare, plot, report) of the beam-expander studies.
- `tolerance-polarization-workers`: the tolerance study in two worker processes; `study-resume`: `study run
  --resume` of the completed tolerance study (every sample reused).

Optional checks (full workflow studies, seed verification, external-tool cases with their own pinned
requirements) run with `--optional NAME` or `--optional all`. A check with a `requirements` file runs in its own
environment with the engine, the kit and that file.

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

- **CI** (`.github/workflows/ci.yml`, every pull request and push to `main`): the engine job first asks PyPI for
  `engine.verified_version`. When it is published, the job runs this runner on it with the catalog snapshot; a pull
  request verifies the cases it changes (all cases when `baseline.json` or `pyproject.toml` changes), `main` and
  the merge queue verify every case. When it is not published yet, the job says so in its summary and passes
  without running engine checks.
- **Scheduled checks** (`.github/workflows/scheduled.yml`, weekly and on demand): the verified engine on Linux,
  Windows and macOS with every Python version in `baseline.json`, all cases; and the newest engine release on PyPI
  as a candidate when it is newer than the verified one. On demand, a candidate version and optional checks can be
  given.

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
