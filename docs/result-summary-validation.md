# Result-summary validation

Issue [#4](https://github.com/JunLin615/opensci-engine/issues/4), following standing
rules [#1](https://github.com/JunLin615/opensci-engine/issues/1) and
[#2](https://github.com/JunLin615/opensci-engine/issues/2).

## Reproduce

From the repository root in an isolated Python environment:

```bash
python -m pip install "opensci-engine[protected]==0.6.1"
python -m unittest discover -s tests -v
python -m tests.check_beam_summary
python -m pip check
```

The unittest suite runs offline. The separate beam check needs the public catalog
or its cached release; it does not import the private development repository.
It writes native run artifacts and `summary-check.json` to `run-summary-check/`,
and an arrivals CSV to `run-summary-check-arrivals.csv`.

## Observed baseline

Validated on 2026-10-13 with Windows, Python 3.13.5, released PyPI package
`opensci-engine==0.6.1` (build `0.6.1+e5785dea69453e1e`), and catalog
`2026-10-06.3`. This check does not advance the repository's verified baseline.
The installation's dependency consistency check passed.

All 15 offline tests passed. They cover the six scientific statuses, null values,
zero and two-sided bounds, complete actionable issue context, model-scope status,
integrity failures, unsupported statuses, and stdout/stderr separation. Subprocess
checks verified ideal-lens `PASS`/exit `0`, strict-target `FAIL`/exit `1`, explicit
detail output, and invalid numeric or usage input/exit `4` with empty stdout.
Passing and failing simulations both retained their full request, result, ray data,
and arrivals CSV. `UNKNOWN` and `OUT_OF_MODEL_SCOPE` exit mappings were checked
using public result-contract fixtures, rather than invented optical scenarios.

The catalog beam expander returned `PASS`, with an expanded waist of
`1.9960708624295032 mm`. The ideal-lens example returned `PASS` for its default
`0.05 mm` maximum, with actual radius `0.016934085944977664 mm`.

The beam check constructs both the original example's output and the new summary
from the **same saved result**, with result hash
`6e3cda81604f7be4095a4e0458e6b7c440843d6ab91f79460883ea26f841e547`.
Sizes count UTF-8 bytes with one LF terminator, independent of shell newline
translation, using the relative output path `run-summary-check`:

| Output | Bytes |
| --- | ---: |
| Original example JSON, original indentation | 6,905 |
| Original content, minified for a like-format comparison | 4,667 |
| New default JSON, including additional scientific context | 2,594 |

These figures describe this example and catalog release; they are not a general
token-savings estimate. Target bounds, units, issues, model assumptions, identities,
and artifact references remain in the new output. All targets and diagnostics are
retained, so output size still scales with their count.

Saved-run summarization and explicit detail inspection succeeded with public
simulation entry points patched to raise. Every native artifact's SHA-256 and
modification time remained unchanged. The detail ledger matched
`load_run(...).power_budget()` exactly, and all emitted artifact/export paths existed.

The human walkthroughs document exit codes, strict and invalid target invocations,
and the distinction between native local runs and the ideal example's low-level
artifact bundle: [English](en/results.md) / [简体中文](zh-CN/results.md).
