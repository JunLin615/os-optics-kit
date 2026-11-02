# Maintaining alignment with released engines

Two files in the repository root drive the alignment checks:

| File | Contents |
| --- | --- |
| [engine-baseline.json](../engine-baseline.json) | Verified PyPI release, its wheel and source SHA-256 values, CI Python versions, website and retired hosts, verified environment (date, Python, platform, engine build, catalog release and its SHA-256, evidence), current documents and the documentation scan |
| [alignment-checks.json](../alignment-checks.json) | The checks run against an installed release: command, expected exit code and result status, catalog and environment requirements |

Current entry and installation documents state the baseline version and reference
`engine-baseline.json`; historical validation records keep their original versions.

The checks run in this repository against published releases; when a release changes
an interface, update the affected example here. Examples and helpers follow released
engine contracts.

## Run a check

Use Python 3.11+ from the repository root. The runner needs only the standard
library; it creates its own virtual environment and installs the exact released
distribution from PyPI, even if another engine is already installed:

```bash
python scripts/check_alignment.py --out run-alignment
python scripts/check_alignment.py --candidate 0.10.1 --out run-candidate-0101
python scripts/check_alignment.py --candidate latest --out run-candidate-latest
```

The first command checks the verified baseline. `latest` resolves the current
release from PyPI metadata and records the exact selected version. Every output
directory must be new, so repeated checks cannot overwrite earlier evidence.
Candidate checks never write `engine-baseline.json`, `alignment-checks.json` or
documentation.

### Prepare against a local wheel

Before a release reaches PyPI, use a supplied wheel with its exact version and
SHA-256. For example, substitute the wheel filename and digest you received:

```bash
python scripts/check_alignment.py --candidate 0.10.1 \
  --candidate-wheel os_optics-0.10.1-py3-none-any.whl \
  --candidate-sha256 WHEEL_SHA256 --out run-wheel-preparation
```

This installs the wheel into fresh environments and checks its hash before each
installation. `engine_source.mode` is `local-wheel` and
`engine_source.publication_verified` stays `false`. Its report is preparation
evidence; the reporting command does not publish it as a release observation.
Keep wheel files and their generated environments outside tracked files.

When that version is published, rerun with `--candidate VERSION` and a new output
directory, without `--candidate-wheel`. The default runner and Actions always use
PyPI. Each PyPI run keeps pip's installation report, including the artifact URL and
SHA-256, in `logs/install-engine.json`; `engine_source` repeats the engine artifact
identity. A local-wheel success does not advance the verified baseline.
The runner reads the supported [pip installation-report format](https://pip.pypa.io/en/stable/reference/installation-report/).

The runner installs `os-optics[protected]` and the `default_requirements` of
`alignment-checks.json` into `OUTPUT/.venv`, strips `PYTHONPATH`/`PYTHONHOME`,
disables user-site imports, and verifies the imported engine version and that its
module lies inside that environment. Every check runs with that interpreter.
Besides the offline tests and the ideal-lens and beam-expander examples, the default
checks run each design workflow once (beam expansion with a three-value study, the
polarization workflow at 1064 nm, which must report `OUT_OF_MODEL` with exit 3, and the
alignment workflow with its synthetic-measurement fit), one parameter sweep, one
sensitivity study and a 20-sample tolerance study; together they add about 40 s.
Once the catalog is prepared, the default checks also run the catalog helper flow
(candidate query, then the beam-expander L2 swap, sweep, move, comparison, plot and
report) and `catalog-tests`, a second unittest pass over the catalog-dependent test
classes; together they add about 70 s. `offline-tests` runs before the catalog step, so
those classes skip there;
`catalog-tests` sets `OPENSCI_EXAMPLES_REQUIRE_CATALOG=1`, which makes them fail
instead of skip when no catalog release is installed. To run them locally, install a
release (`os-optics catalog sync` or `catalog import FILE`) and run
`python -m unittest discover -s tests`, or set the variable to make a missing
catalog an error.

### Catalog source

By default the runner downloads the catalog's current release into an isolated
store (`catalog sync`), which exercises the catalog service. On a network that
cannot reach the service, import a published release file instead:

```bash
python scripts/check_alignment.py --candidate 0.10.1 \
  --catalog-file CATALOG_RELEASE.json --out run-candidate-file
```

The runner verifies the file's SHA-256 before importing it: against
`verified_environment.catalog_sha256` when the file holds the baseline's catalog
release, otherwise against `--catalog-sha256 DIGEST` taken from the catalog
manifest (`sha256` of the release). A missing or different digest fails as
`catalog_data` and blocks the catalog checks. With an imported file every command
runs with `OPENSCI_CATALOG_CHECK_INTERVAL=0`, so no run contacts the catalog
service. The report's `catalog_source` records `mode` (`sync` or `file`), the file
and its digests, the active release and the service URL; the Markdown summary
names the source. A passing file-mode check establishes the examples on that
release; it does not establish that the service delivers it.

### Optional checks

Checks marked `optional` run only on request. A check with a `requirements` file runs
in its own environment (`OUTPUT/.venv-<topic>`) holding the candidate engine and that
file; the other optional checks (full workflow studies, edit retry, seed verification,
zero tolerance and the band-edge sensitivity) use the main environment:

```bash
python scripts/check_alignment.py --optional interop-optimization --out run-optimization
python scripts/check_alignment.py --optional all --out run-alignment-full
```

`--optional` is repeatable and accepts comma-separated names. Selecting a check
also selects the checks it `needs`. The report lists optional checks that were not
selected. The seeded optional checks cover the external-tool examples
(optimization, diffraction, quantum, metasurface). Their requirements pin
`numpy==2.5.4`, which needs Python 3.12 or newer, so run them with such an
interpreter; on Python 3.11 they are skipped with that reason. The Diffractio
cross-check uses an environment without the engine and is not part of the list.

## Add a check

Add an object to `checks` in `alignment-checks.json`; no runner change is needed.
Checks run in list order.

```json
{
  "name": "sensitivity-expander",
  "description": "Sensitivity of the catalog-free expander",
  "argv": ["{python}", "examples/sensitivity_analysis.py", "examples/specs/sensitivity_expander.json",
           "--out", "{runs}/sensitivity-expander", "--no-plots", "--no-report"],
  "expected_exit": 0,
  "expected_status": "COMPLETED",
  "timeout": 600
}
```

| Field | Default | Meaning |
| --- | --- | --- |
| `name` | required | Unique lower-case name (`[a-z0-9-]`); also the log file name |
| `argv` | required | Command; the first item must be `{python}`. Paths are relative to the repository root |
| `description` | none | One line for maintainers |
| `expected_exit` | `0` | Exit code that counts as success; a helper reporting an unmet requirement expects `1` |
| `expected_status` | none | String or list; stdout must be one JSON document whose `status_field` has one of these values |
| `status_field` | `"status"` | Key read for `expected_status` (`"outcome"` for the external-tool examples) |
| `json_error` | `false` | Success requires empty stdout and a JSON `error` object on stderr |
| `needs_catalog` | `false` | Prepare the catalog (sync or file import) first; skipped when that fails |
| `needs` | `[]` | Earlier checks that must pass; otherwise this check is skipped |
| `requirements` | none | Requirements file installed with the engine into a separate environment |
| `requires_python` | none | Minimum Python (`"3.12"`); on an older runner the check is `SKIPPED` as `setup` with that reason |
| `optional` | `false` | Run only when selected with `--optional` |
| `timeout` | `180` | Seconds |
| `env` | `{}` | Extra environment variables (upper-case names) |

Placeholders: `{python}` (the check's interpreter), `{runs}` (`OUTPUT/runs`) and
`{out}` (`OUTPUT`). Output paths built from `{runs}` or `{out}` use native separators.
Built-in step names (`install-engine`, `catalog-sync`, `catalog-identity`, ...) are
reserved. Add offline unit tests for new helpers under `tests/`; offline-tests runs
them before any catalog step. Run `python -m unittest tests.test_alignment_check`
after editing the list: it validates the repository's own check list and
documentation against the baseline.

## Read the result

The command prints a compact JSON status, the catalog source and the report path;
exit `0` means every executed check passed and exit `1` means at least one failed.
An expected non-passing design is a successful **check**: the strict ideal-lens case
must report `FAIL` and exit `1`, while invalid input must exit `4` with an error on
stderr.

| Evidence | Contents |
| --- | --- |
| `report.json` | Requested/resolved/installed versions, engine source and artifact SHA-256, Python version, catalog source, optional selection; each check's category, status, command, actual/expected exit code, diagnostic, log paths and, when skipped, `blocked_by` |
| `environment.json` | Installed engine version/location, Python executable/version and platform |
| `logs/` | Complete stdout/stderr for installation and every executed command, including installed package lists |
| `runs/` | Complete successful or non-passing example outputs and the beam comparison report |
| `.venv*/`, `.catalog/` | Local execution environments and the isolated catalog store; omitted from uploaded artifacts |

Produce a Markdown report without contacting GitHub:

```bash
python scripts/report_alignment.py run-alignment/report.json --summary run-alignment/summary.md
```

Failures are separated by cause:

- `package_api`: installation/import/dependency failures, changed APIs or output
  contracts, unit-test failures, or unexpected example outcomes. Inspect the
  command and stderr before changing the helper or expected result.
- `catalog_network`: the catalog service or its transport could not deliver a
  release: `CATALOG_SERVICE_UNREACHABLE`, `CATALOG_SERVICE_ERROR` (the service
  answered with something that is not a manifest or release), `CATALOG_NOT_AVAILABLE`,
  `CATALOG_RELEASE_NOT_FOUND`, `CATALOG_SYNC_UNAVAILABLE`, `CATALOG_ORIGIN_INVALID`,
  `CATALOG_STORE_BUSY`, an uncaught connection/URL/TLS exception in a catalog
  command, or a catalog-sync timeout. This does not establish an engine regression.
- `catalog_data`: a delivered or stored release cannot be used (`CATALOG_INVALID`,
  `CATALOG_INTEGRITY_FAILED`, `CATALOG_RELEASE_CONFLICT`, `CATALOG_STORE_ERROR`,
  `CATALOG_ACTIVE_UNKNOWN`, `ENGINE_UPGRADE_REQUIRED`, other `CATALOG_*` codes),
  a product/model code such as `EXAMPLE_UNAVAILABLE`, a catalog file whose SHA-256
  differs, or an active release other than the imported file. A refused catalog
  query (`CATALOG_QUERY_INVALID`) or a model kind the engine does not support
  (`SIMULATION_MODEL_UNSUPPORTED`) is a `package_api` failure: the engine's query
  vocabulary or supported models changed.
- `documentation`: a current document disagrees with the baseline. `problems` in
  the row lists each file and line: a version pin other than the baseline, a
  listed document without a pin or without its `engine-baseline.json` reference,
  a catalog release other than the verified one, or a retired host.
- `setup`: version discovery, the check list, environment creation or runner setup
  could not complete.

Codes are read from the JSON error documents the engine prints (whole output or
single JSON lines) and from `CatalogError: CODE` traceback lines; other text is not
interpreted, so an unrecognized failure stays `package_api` until diagnosed. A
check that cannot run is `SKIPPED` with the category of the check that blocked it
and `blocked_by` naming that check: after a `catalog_network` sync failure, the
catalog checks are `SKIPPED` as `catalog_network` while catalog-free checks still
run. Retry after connectivity changes with a new output directory, and keep the
failed report.

### Documentation scan

`documentation` lists the current documents: each must state the baseline version
(exact install pins such as `os-optics[protected]==X.Y.Z`, prose such as
**X.Y.Z** or `os-optics X.Y.Z`, issue-template placeholders), and entries
written as plain paths must also link `engine-baseline.json`. Pins and prose under
the earlier distribution name `opensci-engine` count as well, so a leftover one is
reported. Minimum statements
(`0.10.1 or newer`, `>=0.10.1`) are not pins. `documentation_scan.include` lists
further files that are checked for exact install pins and retired hosts;
`documentation_scan.historical` lists dated validation records, which are exempt.
A new document that states the version belongs in `documentation`; a new
validation record belongs in `historical`.

## CI and periodic reporting

The [Engine alignment workflow](../.github/workflows/engine-alignment.yml) checks
the verified baseline on pull requests and on pushes to `main`, once per Python
version in `ci_python` (the oldest supported and the newest). In Actions, choose
**Run workflow** to supply a candidate version or `latest` and optional check names
or `all`; blank inputs check the baseline with the default checks. A weekly Monday
check at 05:23 UTC checks `latest` with fresh catalog sync.

Each run writes a job summary and uploads `engine-alignment-py<version>` artifacts
for 30 days, including logs and generated example files even on failure. It excludes
virtual environments and catalog stores. Actions logs retain setup failures that
occur before the runner can create a report. Download evidence needed beyond the
retention window before it expires.

Only the scheduled reporting job receives issue-write permission. It reads each
interpreter's report with code from the scheduled default-branch commit, and creates
an issue for a new unverified release or a new failure pattern. The fingerprint
combines the resolved version, overall status and failed check names/categories.
It ignores Python version, catalog source, temporary paths, timestamps, error wording
and skipped rows, and searches open **and closed** issues; unchanged observations
create no comments or duplicate issues, and two interpreters with the same result
share one issue. A new failed check or category on the same version creates a new
report. Recovery to a passing but still unverified version is a distinct
observation. Manual and pull-request checks produce summaries and artifacts without
posting issues.

## Complete an alignment update

1. Select an exact candidate and keep its report. Resolve `package_api`,
   `catalog_data` and `documentation` failures; investigate `catalog_network`
   separately. A `SKIPPED` check has not passed. When the catalog service is
   unreachable, rerun with `--catalog-file` and the published release to exercise
   the catalog workflows, and record both reports.
2. Update examples, helpers, documentation and expected results for the published
   API. Rerun the candidate check, including the affected optional checks, until
   the relevant workflows pass.
3. In one reviewed pull request, update `engine-baseline.json` (`version`, the
   published `artifacts` wheel and source SHA-256 values, `verified_environment` with
   date, Python, platform, engine build, catalog release
   and SHA-256, evidence), every document the documentation check reports, and the
   affected walkthroughs or expected outputs. Add a dated section to the validation
   record and leave earlier sections unchanged.
4. Run the baseline check from that revision and inspect the final PR-head CI
   artifacts against the current base. Hand the PR to the maintainer for acceptance
   and merge. Close version-specific observation issues only after acceptance of
   their complete scope. Keep the standing issues open.

### Snapshots

Before moving the default branch to a new baseline, tag the last commit verified on
the previous one, for example `engine-0.6.1`, with an annotated message naming the
engine build, catalog release and the validation record that holds its evidence:

```bash
git tag -a engine-0.6.1 COMMIT -m "Examples verified on opensci-engine 0.6.1 (build 0.6.1+e5785dea69453e1e), catalog 2026-10-06.3"
```

A tag reproduces the examples together with the documentation of that release. Keep
one supported baseline on the default branch; do not add compatibility branches
solely to avoid updating examples.

Validation evidence is recorded in [alignment validation](engine-alignment-validation.md).
