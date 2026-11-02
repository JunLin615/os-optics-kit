---
name: interop-quantum
title: Hong-Ou-Mandel interference from the linear optical network export
summary: Two 780 nm sources meet on a native 50:50 plate splitter. The engine exports the layout as a linear optical network (modes, transfer matrix, path group delays); Perceval and Piquasso compute two-photon statistics from it and match a closed-form reference.
engine: 0.12.0
catalog: none
project: project.json
tools: none
tags: catalog-free, interop, quantum-optics, hong-ou-mandel, linear-optical-network, perceval, piquasso
license: MIT
---

## What it shows

`project.json` is the Hong-Ou-Mandel layout: sources `A` (+X) and `B` (+Y), splitter `BS` at the origin, planes `D1`
and `D2`. Its targets check that each output receives 1 mW, the sum of the two independent lasers' halves
(`POWER_INCOHERENT_SUM`). `build_layouts.py` builds 20 layouts (HOM, a 13-point delay scan, a 3 mm N-BK7 window
compensated by group or phase delay, Mach-Zehnder interferometers with 0, 1 and 1.0003 mm windows) and exports each
with `os-optics project network`; a layout with an ideal thin lens is refused (`PHASE_UNDEFINED_IDEAL_LENS`).
`run_quantum.py` reads only the exports and `quantum_inputs.json` (one photon per source, wavepackets, detector
efficiency 0.8, dark counts). Verified run: Perceval matches the reference to 4e-8, Piquasso to 2e-9; HOM visibility
0.9491; phase-delay compensation lowers the indistinguishability from 0.95 to 0.854. Outcome WITHIN_TOLERANCE.

## Run

```bash
os-optics project run project.json --out-dir run
os-optics project network project.json --out hom.network.json
python -m pip install -r requirements-quantum.txt
python build_layouts.py --out q
python run_quantum.py q --inputs quantum_inputs.json
```

## Targets

| Target | Metric | Plane | Bound | Verified |
| --- | --- | --- | --- | --- |
| `d1_power` | `power_W` | `D1` | 1 mW ± 1 % | 1 mW, MET |
| `d2_power` | `power_W` | `D2` | 1 mW ± 1 % | 1 mW, MET |

## Baseline

os-optics 0.12.0, no catalog: run status PASS (`expected.json`). The quantum side was verified on Python 3.14.7 with
the pinned versions in `requirements-quantum.txt`.

## Files

- `project.json`, `expected.json`: the HOM layout and its verified outcome.
- `build_layouts.py`, `run_quantum.py`, `network_io.py`, `reference.py`: export, simulation and reference.
- `quantum_inputs.json`: photons, wavepackets and detectors.
- `requirements-quantum.txt`: pinned third-party packages.
- `README.md`, `README.zh-CN.md`, `images/top.png`: the illustrated page.
