---
name: beam-expander
title: Catalog beam expander: swap a lens, sweep its spacing, restore collimation
summary: A Keplerian beam expander of two catalog plano-convex lenses (f = 50 mm and 100 mm, 780 nm, 1 mm waist) reaches its 1.9961 mm output waist. The studies swap L2 for a 100.3 mm fused-silica lens, sweep L2 along the beam and apply the best shift.
engine: 0.12.0
catalog: 2026-10-28.1
project: project.json
tools: candidates, edit, sweep, compare, plot, report
tags: catalog, beam-expander, lens-swap, parameter-sweep, edit-by-id
license: MIT
---

## What it shows

`project.json` is the engine's reference design `os-optics project example --kind beam_expander`; its components
pin catalog release 2026-10-28.1. The target `expanded_waist` (waist radius of the output beam, a collimation
measure) asks for 1.9961 mm ± 1 %. The study files edit the design by ID: swapping L2 for the 100.3 mm lens breaks
collimation (`expanded_waist` 1.4407 mm, UNMET, while the radius at the plane, `output_beam_radius`, stays MET);
a twelve-sample sweep of the L2 position finds +0.54 mm (1.99645 mm, 5 of 12 samples PASS); applying it passes.

## Run

```bash
os-optics project run project.json --out-dir run
os-optics results targets run
os-optics-kit candidates --category LENS --form PLANO_CONVEX_LENS --wavelength-nm 780 \
  --range focal_length_mm=95..105 --range outer_diameter_mm=12.7..12.7 --near focal_length_mm=100 --limit 8
os-optics-kit edit project.json --edits studies/beam_expander_swap_l2.json --out swapped.json --run swapped-run
os-optics-kit sweep studies/beam_expander_l2_sweep.json --base swapped.json --out l2-sweep
os-optics-kit plot sweep l2-sweep --y target:expanded_waist
os-optics-kit edit swapped.json --edits studies/beam_expander_move_l2.json --out final.json --run final-run
os-optics-kit compare run swapped-run final-run --project final.json --out compare
os-optics-kit report l2-sweep --project final.json --plots l2-sweep/plots --changes swapped.changes.json
```

## Targets

| Target | Metric | Plane | Bound | Verified |
| --- | --- | --- | --- | --- |
| `expanded_waist` | `waist_radius_x_mm` | `output` | 1.99612 mm ± 1 % | 1.99607 mm, MET |

## Baseline

os-optics 0.12.0, catalog 2026-10-28.1: run status PASS (`expected.json`).
`os-optics-kit cases verify beam-expander` repeats the run and compares it.

## Files

- `project.json`, `expected.json`: the design and its verified outcome.
- `studies/beam_expander_swap_l2.json`, `studies/beam_expander_l2_sweep.json`, `studies/beam_expander_move_l2.json`:
  the lens swap, the L2 sweep and the final move.
- `README.md`, `README.zh-CN.md`, `images/`: the illustrated page.
