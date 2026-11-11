# External optimization and Sobol tolerance

A Keplerian beam expander of two ideal thin lenses (f = 50 mm and 150 mm) expands a 532 nm beam with a 0.5 mm waist
radius. The optimizers SciPy and Optuna choose the position of L2; the engine computes each design and reports the
output divergence. SALib then estimates which mounting error matters most.

![Section along the beam: source, L1, L2 and the output plane](images/section.png)

## Run it

```bash
os-optics-kit cases copy interop-optimization --dest .
cd interop-optimization
python -m pip install -r requirements-optimization.txt
python optimize_expander.py --out opt
```

The Sobol samples run as one batch of independent evaluations; `--workers 4` runs them in four worker
processes with the same results.

## Results

| Target | Value | Bound | Assessment |
| --- | --- | --- | --- |
| `collimated` | 0.1129 mrad | ≤ 1 mrad | MET |

| Check (verified run, 710 engine evaluations) | Result | Tolerance |
| --- | --- | --- |
| SciPy optimum vs ABCD optimum (200.0573 mm separation) | 9e-7 mm | 1e-4 mm |
| Optuna best divergence above the optimum | 0.14 % | 1 % |
| Engine divergence vs ABCD reference, every evaluation | 2e-14 relative | 1e-9 |
| Engine vs reference total Sobol indices (L1, L2, waist) | 2e-14 | 1e-6 |

`evaluations.jsonl` keeps every evaluation with its inputs, status and run identity. A requirement no design can
meet (`--max-divergence-rad 1e-9`) ends with NO_FEASIBLE_RESULT. Verified with os-optics 0.13.0 on Python 3.14.7
(SciPy 1.18.1, Optuna 5.0.0, SALib 1.6.0).
