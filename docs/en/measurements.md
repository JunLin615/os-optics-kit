# Analyze experimental measurements

[User guide](README.md) · English · [简体中文](../zh-CN/measurements.md)

Use [analyze_measurements.py](../../examples/analyze_measurements.py) to subtract
measured backgrounds, optionally divide by a simultaneous reference channel, and
fit a linear response. It runs independently of OS Optics or a simulation.
The supplied CSVs are **synthetic**, with known answers and deliberate bad rows.

## Install and run

Use Python 3.11+ in a virtual environment. From the repository root:

```bash
python -m pip install -r requirements-examples.txt
python examples/analyze_measurements.py --help
python examples/analyze_measurements.py examples/data/measurements/detector_response.csv --x input_power_mW --signal signal_V --background dark_V --x-unit mW --signal-unit V --maximum-rmse 0.003 --out run-detector
python examples/analyze_measurements.py examples/data/measurements/reference_drift.csv --x time_s --signal signal_V --background dark_V --x-unit s --signal-unit V --reference reference_V --reference-background reference_dark_V --reference-unit V --maximum-rmse 0.000000000001 --out run-reference
```

Each command writes one compact JSON document to stdout. Choose a **new output
directory** for each run; existing directories are rejected to preserve results.
Both examples return `WARNING` and exit 0 because deliberate bad rows are excluded;
both RMSE targets pass. Their full results are saved even if a target fails.

| Example | Total / usable / excluded | Expected fitted response | Check tolerance |
| --- | --- | --- | --- |
| Detector | 10 / 7 / 3 | `response_V = 2 * input_power_mW + 0.005`; RMSE 0.002 V | Absolute error ≤ 1e-12 in slope, intercept and RMSE |
| Reference | 13 / 8 / 5 | `ratio = 0 * time_s + 0.4`; RMSE < 1e-12 | Absolute slope and intercept error ≤ 1e-12 |

The detector example subtracts a 0.020 V dark offset and recovers a 2 V/mW
response with a remaining 0.005 V offset and small deterministic residuals. A
nonzero fitted intercept is retained; the helper never forces a fit through zero.
For real calibration, record calibrated incident power or filter transmission and
the dark readings, keep bandwidth/gain fixed, and inspect residuals for saturation
or other nonlinearity. A small RMSE alone does not establish a linear operating
range or validate a detector.

The reference example subtracts **both** dark offsets before division:

```text
corrected_signal = signal_V - dark_V
corrected_reference = reference_V - reference_dark_V
ratio = corrected_signal / corrected_reference
```

Corrected signal and reference rise by 7% over 0–7 s; their ratio remains 0.4.
The ratio is dimensionless because both channels are in V. It represents sample
transmission only after channel gains and split ratios are calibrated. Reference
normalization removes shared source fluctuations; it cannot remove drift arising
after the beam split or uncorrelated detector noise.

## Supply your own CSV

Use UTF-8 CSV (an optional BOM is accepted), comma separators, decimal points,
and a unique, nonempty header. Map columns explicitly with `--x`, `--signal`, and
`--background`; their names have no built-in meaning. Signal and background must
share the unit named by `--signal-unit`; the helper does no unit conversion.

For normalization, supply all three of `--reference`, `--reference-background`,
and `--reference-unit`. The two reference columns must share that unit. Select
distinct columns for every role. Units are labels: identical signal/reference
labels produce response unit `1`; otherwise the response unit is their quotient.
The independent variable uses `--x-unit`; fitted slope uses response units per x
unit. Choose compatible physical quantities before running.

No rows are imputed or automatically removed as outliers. Negative corrected
signals remain valid. These rows are excluded from the fit:

- A required field is blank/absent, nonnumeric, NaN or infinite.
- The record has more or fewer cells than the header, including a blank line.
- Subtraction or division produces a nonfinite value.
- The **dark-subtracted** reference is zero, negative, or at/below
  `--minimum-reference` (default 0, in reference units). Set a positive floor
  appropriate to your noise level to avoid unstable division by a small reference.

`rows.total` counts logical CSV records after the header, including blank records;
a quoted field spanning several physical lines is one record. `rows.used` counts
usable processed rows and `rows.excluded` counts the remainder. `fit.n` records
the actual fit size when a fit is available. A row can have several reasons, so
`exclusion_counts` can sum to more than the excluded-row count. Malformed CSV
syntax, missing columns, or invalid configuration stops the command with an error.

## Read the results

| Artifact linked from JSON | Contents |
| --- | --- |
| `original-input.csv` | Byte-for-byte input snapshot; the source file is never written |
| `processed.csv` | Original columns and every record, plus `_analysis_` fields for record/line numbers, eligibility, exclusion reasons, corrected values, response, fitted response, signed residual and input SHA-256; excess cells are retained in `_analysis_extra_cells` |
| `measurements.png` | Corrected channels, processed response/fit, and residuals; labeled axes and units, row counts and input hash |
| `provenance.json` | Input path/hash/size, exact column mapping and units, numerical method, UTC time, helper hash, Python/Matplotlib versions, source attribution and artifact hashes |
| `summary.json` | The compact summary also emitted on stdout |

The fit uses unweighted ordinary least squares with an intercept, at least three
usable observations, and at least two distinct x values. Residual means
**observed − fitted**. RMSE divides the squared residual sum by `n`; residual
standard deviation and coefficient standard errors use `n - 2` degrees of
freedom. These standard errors assume exact x values and independent response
errors with equal variance; they do not account for calibration uncertainty,
correlated drift, or changing noise after division. Supply a more suitable model
when those assumptions fail. `r_squared` is `null` for a constant response, such
as the reference example; inspect slope and residuals instead.

The fit centers offsets from the first observation to retain small variations
on large absolute timestamps or DC levels. For reuse, the stable form is
`response_origin + (response_offset_at_x_origin + slope * (x - x_origin))`;
the conventional `slope` and `intercept` are also reported. Residuals are computed
in offset coordinates; at very large DC levels, a stored fitted response can
round away a small residual. Numeric ranges that cannot be represented produce
an error; rescale those inputs and their unit labels.

`--maximum-rmse` adds a target in response units. Inspect `targets`, `issues`,
`rows`, and `fit` together. Status and exit conventions match other examples:

| Status / exit | Meaning |
| --- | --- |
| `PASS` / 0 | Fit completed without diagnostics; any configured RMSE target passed |
| `WARNING` / 0 | Fit completed with excluded rows or undefined constant-response R² |
| `FAIL` / 1 | Configured RMSE maximum was exceeded |
| `UNKNOWN` / 2 | Too few usable observations or no x variation; audit artifacts retained |
| Error / 4 | Input, configuration, dependency or runtime error; JSON error on stderr |

A successful fit is not automatic scientific acceptance. Without
`--maximum-rmse`, no acceptance bound is checked. Residual structure may expose a
poor model even when a scalar error bound passes. Never silently discard excluded
rows or interpret a constant-reference trace's undefined R² as a failed fit.

## Reuse from Python

```python
from examples.analyze_measurements import MeasurementConfig, analyze_measurements

summary = analyze_measurements(
    "examples/data/measurements/detector_response.csv",
    "run-detector-python",
    MeasurementConfig(
        x="input_power_mW", signal="signal_V", background="dark_V",
        x_unit="mW", signal_unit="V", maximum_rmse=0.003,
    ),
)
print(summary["fit"]["slope"])
```

`preprocess_csv(bytes, config)` and `fit_linear(x, y)` are also importable and do
not write files or require plotting. See [dataset definitions and
attribution](../../examples/data/measurements/README.md) and [validation
evidence](../measurement-validation.md). These helpers complement optical
simulation; use the [agent editing recipes](../agent-recipes.md) to revise a
simulated project separately.
