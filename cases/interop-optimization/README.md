# External optimization and Sobol tolerance

[简体中文](README.zh-CN.md)

A Keplerian beam expander of two ideal thin lenses (f = 50 mm and 150 mm) expands a 532 nm beam with a 0.5 mm waist
radius. The optimizers SciPy and Optuna choose the position of L2; the engine computes each design and reports the
output divergence. SALib then estimates which mounting error matters most.

![3D view: the laser source, lenses L1 and L2 and the output plane with the traced beam](images/iso.png)

![Section along the beam: source, L1, L2 and the output plane with the traced rays](images/section.png)

Both images are the engine's browser-free snapshots of a run of `project.json`:
`os-optics view run --no-browser --snapshot DIR` with `--views iso --size 1280x400`, and again with
`--section ALL --frame=scene --size 1200x420`. In a kit checkout,
`os-optics-kit cases images interop-optimization` re-renders exactly these views as recorded in `images.json`.

## Run it

```bash
os-optics-kit cases copy interop-optimization --dest .
cd interop-optimization
os-optics project run project.json --out-dir run
os-optics results targets run
python -m pip install -r requirements-optimization.txt
python optimize_expander.py --out opt
python optimize_expander.py --out opt-strict --max-divergence-rad 1e-9
os-optics-kit optimize studies/optimize-expander.json --out kit-opt
os-optics-kit optimize studies/optimize-expander-infeasible.json --out kit-strict
```

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
meet (`--max-divergence-rad 1e-9`) ends with NO_FEASIBLE_RESULT, exit code 2. The optimization ran with
os-optics 0.13.0 on Python 3.14.7 (SciPy 1.18.1, Optuna 5.0.0, SALib 1.6.0). The engine run is verified with
os-optics 0.14.0, in vacuum (`environment.pressure_pa` 0), and gives the same values.

## With the kit's optimizer

`studies/optimize-expander.json` states the same search for `os-optics-kit optimize`, with no extra packages: the
separation is the one variable (150-250 mm; the op writes L2 at x = 100 mm + separation), the divergence of
`collimated` is the objective and its 1 mrad limit a constraint. On os-optics 0.14.0 the trust-region solver (`trf`)
converges to 200.057287 mm in 42 engine evaluations (finite-difference probes included), SLSQP (`--method slsqp`)
to 200.057287 mm in 9; both lie within 4e-7 mm of the ABCD optimum, and `best-project.json` reproduces its divergence
exactly in a fresh session. `studies/optimize-expander-infeasible.json` asks for at most 1e-9 rad: status
`NO_FEASIBLE`, exit 1, no best Project, and `history.jsonl` keeps every candidate's negative margin; `closest`
names the separation nearest to the limit (the same optimum).
