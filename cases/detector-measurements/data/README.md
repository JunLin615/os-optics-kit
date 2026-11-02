# Synthetic measurement fixtures and attribution

All readings in these two CSVs are invented for checking the helper. They are
not measurements, calibration certificates, or a claim about actual hardware.
See the walkthrough in [English](../../../docs/en/measurements.md) or
[Simplified Chinese](../../../docs/zh-CN/measurements.md).

## Detector response

`detector_response.csv` has `input_power_mW` (mW), `signal_V` (V), `dark_V` (V),
and the textual `data_origin` label. For the seven valid rows, indexed `i = 0..6`:

```text
input_power_mW = i / 10
dark_V = 0.020
signal_V = dark_V + 2 * input_power_mW + 0.005 + residual_V[i]
residual_V = [0.002, -0.003, 0.001, 0, 0.001, -0.003, 0.002]
```

The residuals sum to zero and have zero covariance with x. Thus the OLS slope is
2 V/mW, intercept is 0.005 V, and RMSE is exactly 0.002 V in real arithmetic.
Tests accept absolute error of 1e-12 for these quantities. Three appended rows
exercise missing signal, invalid incident power, and missing dark background.

## Reference drift

`reference_drift.csv` adds `time_s` (s), `reference_V` (V), and
`reference_dark_V` (V). For eight valid rows, `time_s = i = 0..7`:

```text
reference_dark_V = 0.010
dark_V = 0.020
reference_V = reference_dark_V + 1 + 0.01 * i
signal_V = dark_V + 0.4 * (1 + 0.01 * i)
```

The dark-subtracted channels grow by 7%; their quotient stays 0.4. The ratio's
fitted slope is zero, intercept is 0.4, and RMSE is zero in real arithmetic.
Tests use absolute tolerance 1e-12. R² is undefined for this constant response.
Five appended rows exercise zero corrected reference, missing reference,
invalid signal, negative corrected reference, and missing reference background.

## Sources and changes

The workflow ideas adapt **JunLin615 and contributors**, *Free-space Optical
Experiment Benchmark*, at commit
`e72ad3a9b6be12c7f0dbb00121fda23289c4e908`:

- [EX-DETECT-01: Detector linearity calibration](https://github.com/JunLin615/free-space-optical-experiment-benchmark/blob/e72ad3a9b6be12c7f0dbb00121fda23289c4e908/benchmark/case_library/EX-DETECT-01.md), blob `81fc82e61aec5e3929b51f45eea4eb8c6b869817`.
- [EX-DRIFT-01: Reference channel for slow drift](https://github.com/JunLin615/free-space-optical-experiment-benchmark/blob/e72ad3a9b6be12c7f0dbb00121fda23289c4e908/benchmark/case_library/EX-DRIFT-01.md), blob `55374f93e53046837423e909b2e55696bbe12854`.

Benchmark case content is licensed under [Creative Commons Attribution 4.0
International](https://creativecommons.org/licenses/by/4.0/), as recorded in its
[LICENSE-CONTENT](https://github.com/JunLin615/free-space-optical-experiment-benchmark/blob/e72ad3a9b6be12c7f0dbb00121fda23289c4e908/LICENSE-CONTENT).
These adaptations replace the detector case's illustrative 12% filter test with
a calibrated-power scan, add explicit offsets, deterministic residuals and
invalid records, and turn the reference case's 7% shared-drift idea into concrete
CSV values. The Python implementation, equations for these fixtures, and actual
data values were created for this repository. No benchmark dataset, gold answer,
or scorer is copied. The bilingual walkthroughs explain the adapted workflows;
generated provenance records these source links and the exact revision.
