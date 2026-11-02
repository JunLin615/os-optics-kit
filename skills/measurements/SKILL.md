---
name: measurements
description: Subtract backgrounds from detector CSV data, optionally normalize by a reference channel, and fit a straight line with residuals, a plot and a provenance record; the input file is never modified. Use it to analyze a measured response curve.
version: 0.1.0
command: os-optics-kit measurements
requires: os-optics-kit[plot]
kind: tool
---

# measurements

## When to use

- Detector signal against power, time or another variable, with a dark/background column.
- Drift correction with a reference detector (`--reference`, `--reference-background`).
- A recorded pass/fail on fit quality (`--maximum-rmse`).

## When not to use

- Simulated results: use `summary`, `compare` or `plot`.
- Non-linear models, weighted fits or unit conversion: this command fits one unweighted line.

## Inputs

- `INPUT.csv`: UTF-8 CSV with a unique header row.
- Required: `--out NEW_DIR` (must not exist), `--x COL`, `--signal COL`, `--background COL`,
  `--x-unit U`, `--signal-unit U`.
- Optional: `--reference COL --reference-background COL --reference-unit U`, `--minimum-reference V`,
  `--maximum-rmse V` (response units).

## Outputs

`NEW_DIR/original-input.csv`, `processed.csv` (every row with its exclusion reasons, fit and residual),
`measurements.png`, `provenance.json` (input sha256, method, software), `summary.json`.
Stdout: status, row counts with exclusion reasons, fit (slope, intercept, standard errors, RMSE, R
squared), targets, issues. Exit 0 PASS or WARNING (rows excluded, constant response), 1 FAIL (RMSE
above `--maximum-rmse`), 2 UNKNOWN (fewer than three usable rows), 4 invalid input or missing matplotlib.

## Minimal example

`response.csv`:

```
power_mW,signal_V,dark_V
0,0.010,0.010
1,0.112,0.011
2,0.209,0.010
3,0.311,0.010
4,0.412,0.012
```

```
python -m pip install "os-optics-kit[plot]"
os-optics-kit measurements response.csv --out analysis --x power_mW --signal signal_V --background dark_V --x-unit mW --signal-unit V --maximum-rmse 0.01
```

## Limits

- No imputation or outlier rejection: invalid rows are excluded and listed.
- No x uncertainty or correlated-error model; units are labels, never converted.
- Needs no engine run.

## Version

0.1.0 (os-optics-kit 0.1.0), verified with Python 3.14 and matplotlib 3.11.
