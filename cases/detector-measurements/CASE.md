---
name: detector-measurements
title: Detector linearity and reference-channel normalization
summary: A 1 mW, 532 nm beam is split 50:50 onto a signal and a reference detector, each with a 2.5 mm radius active area. Two synthetic data sets with known answers calibrate the signal detector's linearity and normalize slow source drift by the reference channel.
class: free-space
domain: radiometry
provenance: self-built
publication: own-work
engine: 0.13.0
catalog: none
project: project.json
tools: measurements
tags: beam-splitter, detector, measurements, calibration
license: MIT
---

## What it shows

`project.json` is the measurement setup: Gaussian source `SRC` (0.5 mm waist), native plate splitter `BS`
(R = T = 0.5) at 45°, absorbing planes `signal` (transmitted) and `reference` (reflected) 100 mm away. The run confirms
that each detector receives 0.5 mW and that the beam (0.5046 mm radius) fits the active area, the conditions the
analysis assumes. `os-optics-kit measurements` subtracts dark readings, optionally divides by the reference
channel, fits a line and checks the RMSE. The CSV readings are synthetic, with deliberate bad rows:

| Data | Rows used | Fit |
| --- | --- | --- |
| `data/detector_response.csv` | 7 of 10 | 2 V/mW · P + 0.005 V, RMSE 0.002 V |
| `data/reference_drift.csv` | 8 of 13 | ratio 0.4, slope 0, RMSE 0 |

Both analyses return WARNING (rows excluded) with the RMSE target PASS.

## Run

```bash
os-optics project run project.json --out-dir run
os-optics results targets run
os-optics-kit measurements data/detector_response.csv --x input_power_mW --signal signal_V \
  --background dark_V --x-unit mW --signal-unit V --maximum-rmse 0.003 --out detector
os-optics-kit measurements data/reference_drift.csv --x time_s --signal signal_V --background dark_V \
  --x-unit s --signal-unit V --reference reference_V --reference-background reference_dark_V \
  --reference-unit V --maximum-rmse 0.000000000001 --out reference
```

## Targets

| Target | Metric | Plane | Bound | Verified |
| --- | --- | --- | --- | --- |
| `signal_power`, `reference_power` | `power_W` | `signal`, `reference` | 0.5 mW ± 1 % | 0.5 mW, MET |
| `signal_beam_radius`, `reference_beam_radius` | `beam_radius_mm` | `signal`, `reference` | ≤ 1 mm | 0.5046 mm, MET |

## Baseline

os-optics 0.13.0, no catalog: run status PASS (`expected.json`).
`os-optics-kit cases verify detector-measurements` repeats the run and compares it.

## Files

- `project.json`, `expected.json`: the setup and its verified outcome.
- `data/`: the two CSV files and their construction and attribution (`data/README.md`, `data/README.zh-CN.md`).
- `README.md`, `README.zh-CN.md`: the illustrated pages; `images.json` records `images/iso.png` and `images/top.png`.
