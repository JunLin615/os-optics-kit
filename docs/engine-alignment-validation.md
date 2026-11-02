# Alignment-check validation

Dated records of alignment checks, newest first. Each record keeps the versions,
hosts and results observed at the time; later records do not change earlier ones.
The maintainer procedure is in [engine alignment](engine-alignment.md).

## 2026-10-29: baseline os-optics 0.10.0

From 0.10.0 the engine is published as `os-optics` (command `os-optics`,
`import os_optics`); 0.9.0 and earlier were published as `opensci-engine`. This record
moves the verified baseline to the published **0.10.0** (build
`0.10.0+502a4e1b62417916`), published 2026-10-29 13:37Z, with catalog release
`2026-10-28.1` (catalog schema 3.2). The checks ran first on the release-candidate wheel
and again on the published PyPI release; the PyPI artifacts are byte-identical to the
candidate builds:

| Artifact | SHA-256 |
| --- | --- |
| `os_optics-0.10.0-py3-none-any.whl` (1,834,687 B) | `52dc37494df0e0e83e0a233ff750f987b788456431349eb931bdcd56f3ea871b` |
| `os_optics-0.10.0.tar.gz` (1,668,941 B) | `2e44767fb0df8263b95293b4e88184fdffe88bc1390d1c35b90ff1b8644d141f` |

Host: Windows 10 (`Windows-10-10.0.19045-SP0`, AMD64), CPython 3.11.16 and 3.14.7.

### Catalog release used

The catalog service was not reachable from this host, so every run imported the
release file of `2026-10-28.1` (13,463,067 B, SHA-256
`7d7f5f0a6fa1db7ce32503a6a1d86c5bdcd167035782b47b73c2ac00fc1acf30`, recorded as
`verified_environment.catalog_sha256`) with `--catalog-file`. These runs establish the
examples on that release; they do not establish delivery by `catalog sync`, which is
checked separately.

### Published release: complete pass

```bash
python scripts/check_alignment.py --catalog-file 2026-10-28.1.json --out run-alignment
python scripts/check_alignment.py --catalog-file 2026-10-28.1.json --optional all --out run-alignment-full
```

| Python | Selection | Status |
| --- | --- | --- |
| 3.11.16 | default checks | `PASS`, 34 of 34 rows |
| 3.14.7 | default and all 22 optional checks | `PASS`, 72 of 72 rows |

Each run installed `os-optics[protected]==0.10.0` from `https://pypi.org/simple`; pip's
installation report names the `files.pythonhosted.org` wheel and SHA-256 `52dc3749…`, and
the report records `engine_source.publication_verified: true`. Nothing was skipped;
`offline-tests` ran 306 tests (7 catalog classes skipped there) and `catalog-tests` their
9 with none skipped. Every external-tool outcome and comparison value equals the
candidate run below. Separately, `python -m unittest discover -s tests` with the catalog
imported and `OPENSCI_EXAMPLES_REQUIRE_CATALOG=1` passed 306 tests, none skipped, in fresh
environments holding the PyPI release on CPython 3.11.16 and 3.14.7.

### Release-candidate wheel: complete pass

Before publication (`engine_source.mode` `local-wheel`, `publication_verified` `false`):

```bash
python scripts/check_alignment.py --candidate 0.10.0 \
  --candidate-wheel os_optics-0.10.0-py3-none-any.whl --candidate-sha256 52dc3749… \
  --catalog-file 2026-10-28.1.json --out run-alignment
python scripts/check_alignment.py --candidate 0.10.0 \
  --candidate-wheel os_optics-0.10.0-py3-none-any.whl --candidate-sha256 52dc3749… \
  --catalog-file 2026-10-28.1.json --optional all --out run-alignment-full
```

| Python | Selection | Status |
| --- | --- | --- |
| 3.11.16 | default checks | `PASS`, 35 of 35 rows |
| 3.14.7 | default and all 22 optional checks | `PASS`, 73 of 73 rows |

Nothing was skipped. `offline-tests` ran 306 tests (7 catalog classes skipped there);
`catalog-tests` ran their 9 tests with none skipped. The expected non-zero exits were
observed: `ideal-unmet-target` 1, `ideal-invalid-input` 4,
`workflow-polarization-out-of-model` 3, `workflow-alignment-synthetic` 1 and
`spec-be-swap` 1. The representative values equal those of the 0.9.0 record below:
`ideal-pass` `spot_radius` 0.016934085944977664 mm; `workflow-expansion`
`expanded_radius` 1.501118304 mm, `spot_radius` 0.01655211408 mm, `waist_location`
−0.03648967541 mm; `tolerance-polarization` 14 `PASS` and 6 `OUT_OF_MODEL` of 20;
`spec-be-swap` `expanded_waist` 1.4406961517845205 mm; `spec-be-sweep` best shift
0.54 mm with 1.99645 mm, 5 of 12 `PASS`; `catalog-candidates` 471 modeled plano-convex
lenses, 40 within 95–105 mm. External-tool outcomes: optimization `WITHIN_TOLERANCE`
(SciPy 9.0e-7 mm from the reference in 10 evaluations, Engine vs ABCD 2.0e-14);
diffraction `COMPLETED` and `WITHIN_TOLERANCE`; quantum `COMPLETED` (19 networks, the
ideal-lens network refused as `PHASE_UNDEFINED_IDEAL_LENS`) and `WITHIN_TOLERANCE`
(Perceval 4.0e-8, Piquasso 1.8e-9, HOM visibility 0.9491); metasurface `COMPLETED`
through step 3 and step 4 `WITHIN_TOLERANCE` (Engine vs the model 4.4e-16, the four
counterexamples `INDETERMINATE` as expected).

### Documented tutorial commands

With the imported release, the tutorial sections 1–3 of the workflow guide and the
edit examples of both recipes reproduced their quoted values exactly (base
`expanded_waist` 1.99607 mm; eight of 471 candidates; swap 1.4407 / 2.0102 mm; the
twelve sweep values; final 1.99645 mm; recipe edit 1.1148985411200965 mm, exit 1), so
those documents now name `2026-10-28.1`. On CPython 3.14.7 the README quick start and
Python snippet, `doctor`, `catalog import/status/list/show`, `project
example/check/run/verify`, `results describe/targets/freshness/monitor/power-budget`,
`export arrivals`, `schema project`, `view --write-html` and `view --snapshot` exited 0.
`catalog check` could not reach the service from this host.

### Two-beam interference with catalog schema 3.2

`project example --kind two_beam_interference` on `2026-10-28.1` completed with all four
targets `MET` (fringe period 551.543 nm, intensity balance 0.99501, polarization
overlap 1.0, coherence length 322.46 mm). The interference result reports fringe
visibility 0.99991 with `group_delay_bound_mm` 0.6745 from the coatings' stated
group-delay bound; `phase_difference_rad` is `null` and the run lists
`PHASE_UNDEFINED`, because the coating passes do not state a phase. The getting-started
guides describe this.

### Baseline update

`engine-baseline.json` records distribution `os-optics`, version 0.10.0, the published
artifact digests above and the verified environment (CPython 3.14.7, Windows 10 AMD64, build
`0.10.0+502a4e1b62417916`, catalog `2026-10-28.1`). The documentation check now also
reports pins and prose under the earlier name; it reported no problems. Earlier
records keep their versions and package names.

## 2026-10-28: baseline opensci-engine 0.9.0

This record moves the verified baseline from 0.8.0 to the published **0.9.0**
(build `0.9.0+63613606da77d301`), published 2026-10-27 12:47Z, with catalog release
`2026-10-15.1`.

PyPI artifacts of that release, recorded under `artifacts` in
[engine-baseline.json](../engine-baseline.json):

| Artifact | SHA-256 |
| --- | --- |
| `opensci_engine-0.9.0-py3-none-any.whl` (1,825,987 B) | `049d367b3b4f5cc26490b0da7bc6baf189ad4e51efff2d68eb7060d46259f1c4` |
| `opensci_engine-0.9.0.tar.gz` (1,655,620 B) | `bc9de85fa552f2b0f5b69ab1ffb58a8ff491de99d2e43803f0d1bee49bc68393` |

Host: Windows 11 (`Windows-11-10.0.26200-SP0`, AMD64), CPython 3.14.8. A fresh
virtual environment installed `opensci-engine[protected]==0.9.0` from
`https://pypi.org/simple`; pip's own installation report names the
`files.pythonhosted.org` wheel URL and the SHA-256 above, and the imported engine lies
in that environment's `site-packages`. `pip check` reported no broken requirements.
The alignment runner below created its own environments the same way and recorded
`engine_source.publication_verified: true` with the same wheel digest.

### Catalog release used

`catalog sync` from that environment now completes against the live service: the
manifest at `https://opensci-optics.com/api/v1/catalog` named release `2026-10-15.1`
(13,452,502 B) and the downloaded release matched SHA-256
`ce974b614b15b857edb6053709eb57136c339e592ef5116402047a5d8cdf5e87`, which is recorded
as `verified_environment.catalog_sha256`. The earlier `CATALOG_INTEGRITY_FAILED` and
truncated-download failures were a transport defect on the service side and no longer
reproduce. The alignment run used the default fresh sync, not `--catalog-file`, so it
also establishes that an ordinary client receives the release.

### Offline checks

`python -m unittest discover -s tests` passed in the clean installation: 305 tests,
7 skipped (the catalog-dependent classes), 97 s. With the catalog prepared,
`catalog-tests` reruns those classes with `OPENSCI_EXAMPLES_REQUIRE_CATALOG=1` and
passed with none skipped.

### Default alignment run: complete pass

```bash
python scripts/check_alignment.py --out run-alignment
```

| Python | Verification | Status | Catalog |
| --- | --- | --- | --- |
| 3.14.8 | clean PyPI install, wheel `049d367b…` | `PASS`, 33 of 33 rows (22 checks plus setup) | fresh sync, active `2026-10-15.1` |

Every row passed; nothing was skipped. The expected non-zero exits were observed:
`ideal-unmet-target` 1, `ideal-invalid-input` 4, `workflow-polarization-out-of-model`
3, `workflow-alignment-synthetic` 1 and `spec-be-swap` 1. Representative results:

| Check | Result |
| --- | --- |
| `ideal-pass` | `spot_radius` 0.016934085944977664 mm against 0.05 mm |
| `workflow-expansion` | `expanded_radius` 1.501118304 mm, `spot_radius` 0.016552114 mm, `waist_location` −0.036489675 mm; all closed-form comparisons agreed (max 1.6e-14) |
| `workflow-polarization-out-of-model` | 1064 nm outside the retarder band: both targets `INDETERMINATE` with `MODEL_WAVELENGTH_OUTSIDE_BAND`, workflow `OUT_OF_MODEL`, exit 3 |
| `tolerance-polarization` | 20 seeded samples: 14 `PASS`, 6 `OUT_OF_MODEL`; the six retain the scope code and are not counted as target passes |
| `spec-be-swap` | 100.3 mm L2 candidate: `expanded_waist` 1.44 mm, `FAIL`, exit 1 |
| `spec-be-sweep` | best L2 shift 0.54 mm, `expanded_waist` 1.99645 mm, 5 of 12 samples `PASS`; matches the published reference numbers |
| `catalog-candidates` | 471 modeled plano-convex lenses, 40 rows within 95–105 mm, 5 shown |

### Documented tutorial commands

Every command in the README quick start, the installation, catalog, results,
measurement and workflow guides ran against the installed wheel: `doctor`,
`capabilities`, `catalog status/list/show/check/sync`, `project example/check/run/verify/
update-catalog`, `results describe/targets/freshness/monitor/power-budget/rays/metrics/
columns`, `export arrivals/rays`, `schema project`, `view --write-html/--snapshot`, the
README Python snippet, the example scripts (`result_summary.py`, `ideal_lens_focus.py`,
`catalog_candidates.py`, `analyze_measurements.py` with both documented CSVs,
`edit_project.py`, the three workflow scripts) and the documented expected outcomes:
`results freshness` returns 0 for `CURRENT` and 1 for `HISTORICAL`, the strict spot
bound returns `FAIL` with exit 1, an invalid bound exits 4, and both measurement
examples return `WARNING` with exit 0. The commercial commands refused as documented
without `OPENSCI_API_URL` (exit 78, `API_URL_MISSING`).

### Behaviour change from the tabulated-lens fix

The published engine now reports a target that depends on light meeting an
out-of-domain `TABULATED_THIN_LENS` as `INDETERMINATE` with the scope code in
`applicability_issue_codes`; `--status-contract requirements` exits 2 for such a run.
The four metasurface step-4 counterexamples are re-recorded below as `INDETERMINATE`
rather than `MET`. The workflow helpers, sweeps and tolerance studies classify those
samples `OUT_OF_MODEL` instead of a numeric `FAIL`; the public tests now assert that
contract (the 530 nm in-domain case is unchanged).

### Optional external-tool checks

```bash
python scripts/check_alignment.py --optional all --out run-alignment-full
```

On CPython 3.12.6 (the topic requirements files need 3.12 or newer) the run passed
**71 of 71 rows**: the default checks above plus all 22 optional checks, including every
external-tool check. Each topic environment installed the published wheel and the
pinned packages of its requirements file from PyPI. Outcomes: `interop-optimization`
`WITHIN_TOLERANCE`; `interop-diffraction-handoff` `COMPLETED` and
`interop-diffraction-external` `WITHIN_TOLERANCE`; `interop-quantum-layouts`
`COMPLETED` (19 networks exported) and `interop-quantum-simulation`
`WITHIN_TOLERANCE`; the metasurface chain `COMPLETED` with
`interop-metasurface-engine-check` `WITHIN_TOLERANCE` (engine vs the model 4.4e-16,
bounds ±1.4 µm, ±8.1 % and ±19 %, no unexpected counterexample); and the optional
workflow and study checks (full studies, the unmet and refused cases, edit retry, seed
verification, zero tolerance and band-edge sensitivity) all passing. The per-case
metasurface numbers are in the [external-tool record](interop-validation.md).

### Baseline update

`engine-baseline.json` now records version 0.9.0, the published wheel and source
SHA-256 values, CI Python 3.11 and 3.14, the website `https://opensci-optics.com` and
the retired host, and the verified environment above. The documentation check covers
README, README.zh-CN, AGENT_GUIDE, both installation guides, both guide indexes, the
agent recipes, the workflow recipes, both workflow guides and the bug-report template,
and scans the remaining documents, examples, requirements files and issue templates;
it reported no problems. The three dated 0.8.0 records (workflow, sensitivity and
tolerance, release preview) are listed as historical documents and keep their original
versions.

## 2026-10-18: baseline opensci-engine 0.8.0

This record moves the verified baseline from 0.6.1 to the published **0.8.0**
(build `0.8.0+94f6dc70bb40a2ed`) with catalog release `2026-10-15.1`.

Host: Windows 10 (`Windows-10-10.0.19045-SP0`, AMD64). The runner and every
environment it created installed `opensci-engine[protected]==0.8.0` from
`https://pypi.org/simple`; the recorded engine module path lay inside the run's own
`.venv`. Generated `run-*` evidence is ignored by Git.

### Catalog release used

From this host the catalog service at `https://opensci-optics.com` is not reachable:
the TLS connection is reset (`WinError 10054`). The published release
`2026-10-15.1` was obtained as a file whose SHA-256,
`ce974b614b15b857edb6053709eb57136c339e592ef5116402047a5d8cdf5e87`, equals the
digest in the service's catalog manifest; that digest is recorded as
`verified_environment.catalog_sha256`. The catalog checks below therefore used
`--catalog-file`.

### Offline checks

`python -m unittest discover -s tests -v` passed in every runner-created environment:
106 tests in the pinned-file runs on Python 3.11.16 and 3.14.7, and 107, including the
`requires_python` test, in the later fresh-sync and external-tool runs. The alignment
tests (34 runner, 22 reporting) use fake subprocess and HTTP transports; they cover
catalog error classification (`CATALOG_SERVICE_ERROR`, `CATALOG_STORE_BUSY` and the
other service codes as `catalog_network`, release/store/model codes as `catalog_data`,
explicit `CatalogError` and transport tracebacks in non-JSON output), blocking
categories on skipped rows, the declarative check list and its validation, optional
environments, `requires_python`, catalog-file digest verification and import,
documentation drift (version pins, retired host including the badge form, stale
catalog release), the repository's own baseline/documentation consistency, and
unchanged deduplication.

### Pinned catalog file: complete pass

```bash
python scripts/check_alignment.py --candidate 0.8.0 \
  --catalog-file 2026-10-15.1.json --out run-candidate-080-file
```

| Python | Commit | Status | Installed numpy / scipy / matplotlib |
| --- | --- | --- | --- |
| 3.11.16 | `49b9932` | `PASS`, 17 of 17 checks | 2.4.6 / 1.17.1 / 3.10.7 |
| 3.14.7 | `49b9932` | `PASS`, 17 of 17 checks | 2.5.4 / 1.18.1 / 3.10.7 |

`pip check` reported no conflicts. Results, identical on both interpreters:

| Check | Result |
| --- | --- |
| `ideal-pass` | `PASS`, exit 0, `spot_radius` 0.016934085944977664 mm (maximum 0.05 mm) |
| `ideal-unmet-target` | `FAIL`, exit 1, as expected |
| `ideal-invalid-input` | exit 4, JSON error on stderr, empty stdout |
| `catalog-file`, `catalog-import`, `catalog-identity` | SHA-256 matched; active release `2026-10-15.1` with the same digest; service URL `https://opensci-optics.com/api/v1/catalog` |
| `beam-summary` | `PASS`, `expanded_waist` 1.9960708624295032 mm; result hash `20647306808bc04fa737b0fcd3aff981ae6d75f06c5f766069ede819ac89fac3`; saved inspection unchanged; summary 4,188 bytes versus 7,322 for the original layout (5,084 minified) |
| `saved-run-summary` | `PASS`, exit 0 |

Byte counts depend on output-path length. The `expanded_waist` value equals the
0.6.1 record below.

The [editing recipe](agent-recipes.md), extracted verbatim and run in the 3.11
environment with the imported release, completed with `FAIL` and exit 1:
`expanded_waist` 1.1148985411200965 mm against the 1.8–2.2 mm interval, as the
recipe states for catalog `2026-10-15.1`.

### Fresh catalog sync: catalog_network

```bash
python scripts/check_alignment.py --candidate 0.8.0 --out run-candidate-080-sync
```

On Python 3.11.16 every setup, offline and ideal-lens check passed. `catalog-sync`
exited 1 with `CATALOG_SERVICE_UNREACHABLE` for
`https://opensci-optics.com/api/v1/catalog`
(`URLError: [WinError 10054] An existing connection was forcibly closed by the remote
host`) and was classified `catalog_network`. `beam-summary` was `SKIPPED` as
`catalog_network`, blocked by `catalog-sync`, and `saved-run-summary` was `SKIPPED`
as `catalog_network`, blocked by `beam-summary`. The report status was `FAIL`, which
is the expected outcome on this host. Whether GitHub-hosted runners reach the service
is established by the workflow's own runs.

### Optional external-tool checks

```bash
python scripts/check_alignment.py --candidate 0.8.0   --catalog-file 2026-10-15.1.json --optional all --out run-candidate-080-optional
```

On Python 3.11.16 the topic environments could not be built: every requirements
file pins `numpy==2.5.4`, which requires Python 3.12 or newer, so pip found no
matching distribution (`install-env-*` failed as `package_api`; the checks were
skipped with that category). The optional checks declare `requires_python`
`"3.12"` and are skipped on 3.11 with that reason.

On Python 3.12.14, each topic environment installed the engine
wheel and its pinned tools (numpy 2.5.4, scipy 1.18.1; Optuna 5.0.0, SALib 1.6.0;
LightPipes 2.1.5, prysm 0.21.1, POPPY 1.2.0; perceval-quandela 1.3.1, piquasso 8.0.1;
grcwa 0.1.2). The default checks passed as above.

| Check | Outcome |
| --- | --- |
| `interop-optimization` | `WITHIN_TOLERANCE`, exit 0: 709 evaluations (686 `PASS`, 23 `FAIL`); ABCD optimum 200.05728727493047 mm; SciPy error 5.7e-14 mm; Engine vs ABCD 2.3e-14 relative; Sobol ST difference 7.5e-15 |
| `interop-diffraction-handoff`, `interop-diffraction-external` | `COMPLETED`, `WITHIN_TOLERANCE` |
| `interop-quantum-layouts`, `interop-quantum-simulation` | `COMPLETED`, `WITHIN_TOLERANCE`; Perceval vs reference 4.0e-8, Piquasso 1.8e-9 |
| `interop-metasurface-unit-cells`, `-focusing`, `-model` | `COMPLETED` |
| `interop-metasurface-engine-check` | **`OUT_OF_TOLERANCE`, exit 1** (`package_api`) |

The metasurface check followed the four documented commands, which produce the
101-order library and the 1024-sample focus tables only. Without the 201-order and
2048-sample convergence tables, step 3 adds no external numerical spread, so the
stated focus-position bound was 0.3 µm (spot radius 7.4 %, power 16 %). The 575 nm
test case deviates by +0.3155 µm and lies outside it; the Engine reproduced its model
to 4.4e-16 and every counterexample behaved as expected. The 2026-10-15 interop record
reports `WITHIN_TOLERANCE` with bounds that include that spread (±1.4 µm) and the same
+0.315 µm deviation at 575 nm. The documented route lacked the convergence commands; the
[external tools guide](en/interop.md) now lists them, and step 3 stops with
`CONVERGENCE_DATA_MISSING` when their tables are missing.

### Workflow checks

The check list gained the workflow, study and demonstration checks: seven default
checks (`workflow-expansion`, `workflow-polarization-out-of-model`,
`workflow-alignment-synthetic`, `spec-pol-design`, `spec-pol-sweep`,
`sensitivity-polarization`, `tolerance-polarization`) and eighteen optional ones that use
the main environment (full workflow studies, refusal and unmet cases, edit retry, seed
verification, zero tolerance, band-edge sensitivity, catalog candidates and the catalog
beam-expander chain from base run to report).

```bash
python scripts/check_alignment.py --catalog-file 2026-10-15.1.json \
  --optional tolerance-verify-seed,tolerance-zero,sensitivity-band-edge,workflow-expansion-full,workflow-expansion-unmet,workflow-budget-refused,workflow-polarization-full,workflow-alignment-full,spec-pol-edit,spec-pol-edit-retry,catalog-candidates,spec-be-base,spec-be-swap,spec-be-sweep,spec-be-move,spec-be-compare,spec-be-plot,spec-be-report \
  --out run-alignment-workflows
```

Python 3.11.16, baseline 0.8.0 from PyPI, catalog file `2026-10-15.1`: **PASS**, all 42
rows. The offline tests ran 244 tests (7 skipped) in the runner's environment. The
expected non-zero exits were observed: `workflow-polarization-out-of-model` 3,
`workflow-alignment-synthetic`, `workflow-expansion-unmet` and `spec-be-swap` 1,
`workflow-budget-refused` 4 with a JSON error. The external-tool checks were not
selected in this run.

The default run on 2026-10-19 included the catalog helper flow (`catalog-candidates`, then
`spec-be-base` to `spec-be-report`) and `catalog-tests`, and classified `CATALOG_QUERY_INVALID` /
`SIMULATION_MODEL_UNSUPPORTED` as `package_api`. `catalog-tests` reruns the
catalog-dependent test classes after the catalog is prepared and fails instead of skipping
without a catalog. Python 3.11.16, baseline 0.8.0 from PyPI and catalog file `2026-10-15.1`
(`python scripts/check_alignment.py --catalog-file 2026-10-15.1.json --out run-alignment`):
**PASS**, all 33 rows, 306 s including the installation. `offline-tests` ran 282 tests
(7 skipped: the catalog classes); `catalog-tests` ran their 9 tests with none skipped;
`spec-be-swap` exited 1 as expected.

### Baseline update

`engine-baseline.json` now records version 0.8.0, CI Python 3.11 and 3.14, the
website `https://opensci-optics.com` and the retired host, and the verified
environment above. The documentation check covers README, README.zh-CN,
AGENT_GUIDE, both installation guides, both guide indexes, the agent recipes and
the bug-report template, and scans the remaining documents, examples, requirements
files and issue templates; it reported no problems. The recipe's catalog release was
the only stale statement among the current documents.

## 2026-10-13: initial check with baseline 0.6.1 (historical)

This record is kept as observed on 2026-10-13. The baseline then was 0.6.1; the
catalog host named below has since been retired.

Validated on 2026-10-13 using Windows AMD64 and Python 3.13.5. The verified
engine baseline remained `0.6.1`; this work did not advance it.

### Offline checks

The complete suite passed **45 tests** in an isolated environment installed by
the runner from PyPI:

```powershell
.\run-alignment-initial\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Coverage included the existing 15 result-summary tests, 11 release-runner tests,
and 19 reporting tests. The new tests exercised public-release selection,
installation isolation, unchanged baseline files, expected nonzero scientific
exits, retained timeout evidence, documentation drift, catalog/API error
classification, blocked checks, and duplicate reporting across open and closed
issues. Reporter HTTP operations used a fake transport; tests did not publish
issues. The workflow also passed `actionlint` 1.7.12 and `git diff --check`.

### Isolated released-package checks

Two checks created separate virtual environments and catalog stores:

```bash
python scripts/check_alignment.py --out run-alignment-initial
python scripts/check_alignment.py --candidate latest --out run-alignment-candidate
```

Both installed `opensci-engine[protected]==0.6.1` from PyPI successfully. The
candidate check resolved `latest` to `0.6.1`; its recorded engine module path was
inside its own `.venv/Lib/site-packages/opensci_engine/`. Dependency consistency,
offline tests, ideal-lens passing/unmet-target cases, and invalid-input handling
passed. The candidate run additionally recorded the complete installed-package
list. These runs preceded the last beam-error classification regression test;
the final 45-test suite above includes that test.

**Both complete alignment runs reported `FAIL`**, because fresh catalog sync
returned `CATALOG_SERVICE_UNREACHABLE` for
`https://opensci-optic.com/api/v1/catalog`, with `WinError 10054` (connection reset
by the remote host). The reports classified this as `catalog_network` and marked
the beam and saved-summary checks `SKIPPED`. Complete diagnostics remained in
each output directory's `report.json` and `logs/`; failed scientific cases also
retained their generated artifacts. The Markdown reporter rendered the failed
report successfully. No baseline or documentation pins changed during either
check.

This established installation, offline/API checks and failure reporting on this
host, but did not establish fresh catalog availability or a complete passing
alignment run.

### Separate cached-catalog integration

Using the released package in the first runner-created environment, the beam
integration check passed with the previously cached catalog `2026-10-06.3`:

```powershell
.\run-alignment-initial\.venv\Scripts\python.exe -m tests.check_beam_summary --out run-alignment-cached-beam
```

The check confirmed that compact and detailed summaries preserve all expected
results, saved inspection does not rerun simulation, and saved artifact hashes
and modification times remain unchanged. For this run, compact output was
2,682 bytes versus 6,929 bytes for the original summary (4,691 bytes minified).
The result hash was
`6e3cda81604f7be4095a4e0458e6b7c440843d6ab91f79460883ea26f841e547`.
Byte counts depend on output-path length.
