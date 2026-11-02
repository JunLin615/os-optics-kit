# Workflow release preview checks

## Published 0.9.0 recheck (2026-10-28)

The release previewed here was published as PyPI `opensci-engine` **0.9.0** on
2026-10-27 12:47Z (build `0.9.0+63613606da77d301`, wheel SHA-256
`049d367b3b4f5cc26490b0da7bc6baf189ad4e51efff2d68eb7060d46259f1c4`, source package
`bc9de85fa552f2b0f5b69ab1ffb58a8ff491de99d2e43803f0d1bee49bc68393`). It is now the
verified baseline in [engine-baseline.json](../engine-baseline.json); the 0.9.0
alignment record is in [alignment validation](engine-alignment-validation.md).

Rechecked on the published wheel in a clean environment (Windows 11 AMD64, CPython
3.14.8): the installed wheel hash equals the digest above, `catalog sync` reaches the
live service and receives release `2026-10-15.1` with the published SHA-256, the
default alignment run passes every row, and 305 offline tests pass. Two candidate
findings changed status:

- **Catalog transport:** `catalog sync` now completes from an ordinary client; the
  `CATALOG_INTEGRITY_FAILED` and truncated download recorded below were a service
  transport defect and no longer reproduce.
- **Tabulated-lens target assessments:** the four out-of-domain counterexamples now
  assess their target `INDETERMINATE` with the scope code in
  `applicability_issue_codes`, so step 4 reports `WITHIN_TOLERANCE` instead of
  `OUT_OF_TOLERANCE`.

All four external-tool topics were re-run against the published wheel:
`check_alignment.py --optional all` passed **71 of 71 rows** including every
external-tool check, and the metasurface chain was rebuilt from scratch for its
recorded numbers. Every check reports `WITHIN_TOLERANCE` or `COMPLETED` with the same
independent references as before; the only numerical change is the optimizer path
(10 evaluations, minimum 8.97e-7 mm from the reference, still far inside the 1e-4 mm
tolerance). The results are in
[external-tool validation](interop-validation.md#record-2026-10-28-published-pypi-wheel-opensci-engine-090).

The workflow-discovery and measurement observations below were made on the candidate
build identified in the next section; they are kept as that record.

## 2026-10-27 candidate record

Recorded 2026-10-27. These checks use a locally built **0.9.0 candidate**, build
`0.9.0+06608b92cc9c5dca`. Its wheel SHA-256 is
`4ad4f4692b8c66ae9f3cc510dfa5f7e2b58fd214556b21b9f418a7826c05cf98`. The candidate
build is not the published artifact and is superseded by the release above.

Environment: Windows 11 AMD64, CPython 3.12.6, numpy 2.5.4, scipy 1.18.1.
External tools: Optuna 5.0.0, SALib 1.6.0, LightPipes 2.1.5, prysm 0.21.1,
POPPY 1.2.0, Perceval 1.3.1, exqalibur 1.4.2, Piquasso 8.0.1, grcwa 0.1.2,
matplotlib 3.11.2. Diffractio 1.0.0 runs in a separate environment with
numpy 1.26.4 and scipy 1.17.1. The external packages are example dependencies.

`python -m unittest discover -s tests`: **305 tests passed**, with a locally stored
catalog matching the published manifest hash. Coverage includes atomic invalid-edit
refusal, expected-hash mismatch, repeated edits, physical/target/budget freshness,
target-free and partial runs, failed/cancelled execution, exact versus bounded
objectives, explicit no-feasible studies, seeded tolerance, measurement analysis,
artifact preservation, plotting and alignment reporting. This stored-catalog test
result is separate from the failed direct download below.

## Package and public access

A clean PyPI install resolved `opensci-engine==0.8.0`, wheel SHA-256
`fe9a3bfbae9b200b638a71c103f5abddae40d2520c13d3cab6fd9c99065c3828`.
It does not supply the preview's 0.9 workflow APIs. Candidate checks record
`engine_source.mode: local-wheel` and `publication_verified: false`.

The English and Chinese Portal, API discovery, Designer and Account returned HTTP
200. The catalog manifest named release `2026-10-15.1`, SHA-256
`ce974b614b15b857edb6053709eb57136c339e592ef5116402047a5d8cdf5e87`.
Fresh `catalog sync` attempts failed with `CATALOG_INTEGRITY_FAILED`. A separate
download ended at 598,334 bytes of the advertised 13,452,502 bytes with an unexpected
EOF. An incomplete download is not an installed catalog. Catalog-dependent checks
are reported separately from package/API checks; a skipped check is not a pass.

## External comparisons

All four workflows use catalog-free Projects and keep external outputs separate
from engine run artifacts. The commands are in the
[external tools guide](en/interop.md). Values below are candidate observations,
with the independent references unchanged from the reproducible 0.8.0 record.

| Workflow | Check | Candidate result |
| --- | --- | --- |
| Optimization | SciPy minimum vs analytical ABCD separation | absolute difference 8.97e-7 mm, tolerance 1e-4 mm |
| Optimization | 710 retained evaluations vs ABCD divergence | maximum relative difference 1.96e-14, tolerance 1e-9 |
| Optimization | Optuna, seed 1, 60 trials | divergence 0.1371% above the analytical minimum, tolerance 1% |
| Sensitivity | SALib total Sobol indices, all 640 samples | maximum absolute difference from independent-reference indices 1.78e-14, tolerance 1e-6 |
| No feasible design | maximum divergence 0 rad, 3 Optuna trials | 37 search evaluations retained; `NO_FEASIBLE_RESULT`, `best: null` |
| Diffraction | three stop radii, LightPipes finest grid vs Hankel integral | relative peak errors 3.22e-8, -1.05e-4 and 8.58e-4 |
| Diffraction | prysm/POPPY normalized profiles | maximum discrepancy 3.87e-7 |
| Diffraction | separate Diffractio environment, absolute peak | relative errors -2.63e-5, -2.72e-5 and -5.98e-6 |
| Quantum | 19 exported networks, independent two-photon reference | Perceval maximum error 4.04e-8; Piquasso 1.81e-9 |
| Quantum | exported vs independently computed group delay | maximum difference 1.32e-9 ps |
| Metasurface | 101/201 RCWA orders, 1024/2048 propagation samples | all seven documented steps executed; fitted bounds 1.4 µm focus position, 8.1% spot radius, 19% delivered power |
| Metasurface | tabulated model, independent held-out cases | all inside stated errors; engine/model relative difference 4.45e-16 |
| Metasurface | tabulated model's out-of-domain target assessments | **failed**: four affected targets report MET despite the corresponding scope error |
| Metasurface | per-wavelength ideal model | numerical comparison within tolerance; engine/model relative difference 3.82e-7 |

The optimization log distinguishes `objective_valid`, `requirement_feasible`,
`assessment` and `execution`. Its sensitivity sample includes valid quantities
whose targets are unmet. Invalid quantities keep their evidence and prevent Sobol
indices for an incomplete sample set. An optimizer's penalty is never returned as
the best feasible physical result. Existing study output is preserved; use a new
`--out` directory for another run.

The diffraction example retains the 1.2 mm stop's approximately 62% Gaussian-peak
overestimate and the 0.5 mm stop's unavailable Gaussian peak. The external field is
constructed from the stated TEM00 convention. The quantum input file supplies photon
states independently of the classical layout; the unsupported ideal-lens network and
short-photon unbalanced interferometer are refused as documented.

The candidate tabulated-lens runs at 450 nm and at 5°, 8° and 20° report
`MODEL_WAVELENGTH_OUTSIDE_BAND` or `MODEL_FIELD_ANGLE_EXCEEDED`, but their compact
target rows report `MET` with no applicability issue. These targets depend on the
out-of-domain lens. Step 4 preserves both fields and returns `OUT_OF_TOLERANCE`
until the affected assessments become indeterminate. The unsupported-metric
counterexample already returns `INDETERMINATE`. The failed cases do not extend the
model's validated domain: 490–580 nm, angles up to 2°, and the stated input beam
and polarization conditions.

External API references consulted for the optimization changes:
[SciPy scalar optimization](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.minimize_scalar.html),
[Optuna Study](https://optuna.readthedocs.io/en/stable/reference/generated/optuna.study.Study.html),
and [SALib sampling and analysis](https://salib.readthedocs.io/en/latest/user_guide/basics_with_interface.html).

## Workflow discovery and controls

Two fresh customer-Agent trials used only the public guides, example code and the
installed preview package. They used different parameters from the worked examples.

| Task | Result |
| --- | --- |
| 780 nm, 0.7 mm source waist, 4× expansion, 125 mm focusing lens; maximum spot radius 0.012 mm | Found and adapted the existing expansion workflow. Nominal waist 0.01108217 mm; fixed-plane radius 0.01108400 mm. Five separation samples completed, with fixed-plane targets met in 2/5. |
| Same design: invalid edit, retry, freshness | Invalid second operation left the original unchanged and output absent. A repeated relative edit returned ALREADY_APPLIED without a second movement. Target edit reported TARGETS_CHANGED; budget edit reported PHYSICS_CHANGED. |
| 808 nm alignment, near/far planes 125/650 mm, changed source and mirror tolerances | Seed 20261027 produced 7/12 met and 5/12 unmet samples. All 12 were retained; repeat parameters, outcomes and hashes matched. |
| Two-step sensitivity and explicit no-feasible scan | All 13 sensitivity evaluations completed. Mirror-y far-u derivative 1.300000002 mm/mrad, source-z far-v derivative 1 mm/mm. The constrained two-point scan had 0/2 feasible samples, best null and exit 1. |

The trials used 11 and 10 tool calls, respectively. They found that exploratory
sweeps with no constraints do not apply Project targets as selection rules; add an
explicit assessment constraint when it should determine feasibility. Waist radius
and radius at a fixed plane can rank the same samples differently. A catalog-free
run's `catalog_version` is null even when an environment has an installed catalog; the helpers
now report the installed catalog separately. These observations establish task use
on this preview, not an efficiency improvement or validation of a published release.

## Recheck with a published package

Use a new output directory and an exact published version:

```bash
python scripts/check_alignment.py --candidate VERSION --out run-published-check
python scripts/check_alignment.py --candidate VERSION --optional all --out run-published-external
```

Both commands install from PyPI into clean environments, record the installed
artifact identity and fetch the catalog directly. A `--catalog-file` run tests a
stored release separately. Update the baseline only after the direct checks and
the affected examples pass on the published artifact.
