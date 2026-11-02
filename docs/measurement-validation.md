# Measurement workflow validation

Checked on 2026-10-13 with Python 3.13.5 on Windows, Matplotlib 3.10.7, and the
published opensci-engine 0.6.1 package for the existing simulation regressions.
Measurement analysis itself does not import or require the engine. Its only
third-party dependency is plotting, pinned in `requirements-examples.txt`.

## Reproduce

```bash
python -m pip install -r requirements-examples.txt
python -m unittest discover -s tests -v
```

The full suite passed 27 tests: 12 measurement tests and 15 existing result/CLI
tests. Measurement coverage includes known coefficients/residuals, independent
negative-slope and large-offset variants, 1e-200/1e200 response scaling, missing
and nonfinite fields, malformed/blank/ragged records, BOM and multiline CSV,
normalization overflow, zero/negative/near-zero reference handling, overlapping
exclusion reasons, insufficient data and degenerate x, JSON/exit behavior,
artifact hashes, byte preservation, refusal to overwrite output, and a complete
CLI run with engine imports actively blocked.

Independent code review found that subtracting a rounded absolute x mean biased
a fit for `x = [1e16, 1e16+2, 1e16+4, 1e16+6]`. The implementation now anchors
both channels to their first observation before taking means. Regression checks
require slope 1 and exactly zero residuals when either or both coordinates have
a 1e16 offset. Fit diagnostics use norms rather than squaring unscaled values,
and the summary includes an offset form for stable evaluation.

## Scientific and artifact checks

Both commands in the [English](en/measurements.md) and
[Chinese](zh-CN/measurements.md) walkthroughs were executed. They use identical
CSV fixtures and helper code. The detector fit recovered slope
`2.0000000000000004 V/mW`, intercept `0.0049999999999998865 V`, and RMSE
`0.0020000000000000196 V`; all meet the stated 1e-12 absolute calculation
tolerance. Seven of ten records were usable. The reference fit recovered slope
`0`, intercept `0.39999999999999997`, and RMSE `0`, removing the defined 7%
shared drift. Eight of thirteen records were usable. Both RMSE targets passed;
both summaries explicitly retained `WARNING` for excluded rows. Constant-response
R² is explicitly unavailable rather than spuriously reported as a good score.

Original source bytes remained unchanged and archived bytes matched. Tests
recomputed hashes of the original-copy, processed CSV and PNG against provenance.
Every input record remained represented in the audit CSV, including invalid text
and exclusion reasons. Both plots were visually checked for labeled axes, units,
visible fit/residual data, row counts and input-hash identification. Help and
configuration errors follow the shared English/JSON CLI conventions.

At the tested Windows checkout, compact JSON serialized to 1,686 bytes for the
detector and 1,683 bytes for the reference, using `run-detector-final` and
`run-reference-final` output paths. These lengths exclude the trailing newline
and vary with artifact paths. The summary contains coefficients, diagnostics,
targets, counts and artifact links; full record arrays stay in CSV files.

The two public benchmark cases were exposed during implementation. Exact source
revision, blob IDs, license attribution and adaptation details are in the
[dataset notes](../examples/data/measurements/README.md) and generated provenance.
Synthetic calculation tests demonstrate arithmetic and workflow behavior, not
performance on unseen agent tasks or real experimental validity. No agent
token-efficiency or independent benchmark-transfer claim is made.
