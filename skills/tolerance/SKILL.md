---
name: tolerance
description: Seeded tolerance study - draw independent offsets from declared uniform or truncated-normal distributions, save the sample set before any run, run and count every sample, and report target distributions. Use it to estimate how often a design meets its targets.
version: 0.3.0
command: os-optics-kit tolerance
requires: os-optics>=0.13,<0.14
kind: tool
---

# tolerance

## When to use

- Simulated PASS fraction of a design under stated position, angle or value tolerances.
- Reproduce or audit a sample set from its seed (`--verify-seed`).

## When not to use

- Which parameter matters most: use `sensitivity`. Systematic scans: use `sweep`.

## Inputs

`SPEC.json`:

- `base_project` or `design` (as in `sensitivity`); `--base` overrides.
- `targets` (all must PASS for a sample to PASS), `samples`, `seed` (integer >= 0),
  `max_evaluations` (required; samples + 1).
- `parameters`: `{name, unit, ops, nominal?, distribution}` with distribution
  `{"kind": "uniform", "low", "high"}` or `{"kind": "normal", "sigma", "mean_offset"?, "truncate_sigma" | "bounds"}`
  (offsets from nominal, parameter unit).

Options: `--out STUDY_DIR`, `--seed N`, `--samples N`, `--zero-tolerance` (all offsets 0),
`--draw-only` (write the sample set and stop), `--verify-seed STUDY_DIR`, `--overwrite`, `--no-plots`,
`--no-report`.
Execution (`study` skill): `--workers N|auto` (worker processes, same results as one), `--resume`
(continue a planned or interrupted study), `--plan-only` (then `os-optics-kit study run DIR`),
`--time-limit-s`, `--max-memory-mib` (per evaluation), `--ray-data summary`. A larger `--samples` with `--resume` keeps
the samples already run.

## Outputs

`sampling.json` and `samples.csv` (written before any run), `samples/`, `table.csv`, `summary.json`,
`plots/`, `report/`. Stdout: counts per outcome with denominators, PASS fraction, per-target percentiles,
unsuccessful samples. Exit 0 at least one PASS sample (or drawn, planned), 1 no PASS sample or a
`--verify-seed` mismatch, 4 refused spec or invalid input, 130 cancelled (resumable).

## Minimal example

`tolerance.json`:

```
{"base_project": "project.json", "targets": ["spot_radius"], "samples": 20, "seed": 7, "max_evaluations": 21,
 "parameters": [{"name": "L1_axial", "unit": "mm", "nominal": 0,
   "distribution": {"kind": "normal", "sigma": 0.1, "truncate_sigma": 3},
   "ops": [{"op": "transform", "selector": {"ids": ["L1"]}, "frame": {"component": "L1"},
            "translation_mm": [0, 0, "$value"]}]}]}
```

```
os-optics project example --kind focus > project.json
os-optics-kit tolerance tolerance.json --out tol
os-optics-kit tolerance --verify-seed tol
```

## Limits

- Parameters are independent; fractions describe simulated samples under the declared distributions.
- Sampling uses numpy `Generator(PCG64(seed))`: same seed and spec give the same `samples.csv`.

## Version

0.3.0 (os-optics-kit 0.4.0), verified with os-optics 0.13.0 and catalog release 2026-10-28.1.
