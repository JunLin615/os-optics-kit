---
name: candidates
description: Bounded table of public catalog products that pass explicit filters (category, form, wavelength, readiness, numeric ranges with units), sorted by closeness to a value or by a field. Use it to pick catalog parts for a requirement.
version: 0.1.0
command: os-optics-kit candidates
requires: os-optics>=0.12,<0.13
kind: tool
---

# candidates

## When to use

- Shortlist lenses, mirrors, waveplates, polarizers or filters by specification.
- Find the product closest to a target value (`--near`), e.g. a focal length.

## When not to use

- Full product details: use `os-optics catalog show SKU`.
- Checking a design with a chosen product: put it in the Project and run it (`edit`, then `summary`).

## Inputs

- `--category` (e.g. LENS, MIRROR, WAVEPLATE, POLARIZER), `--form` (e.g. PLANO_CONVEX_LENS),
  `--wavelength-nm` (a published simulation model is valid there), `--readiness` ORDERABLE |
  SIMULATION_ONLY, `--mounting` AVAILABLE | NOT_AVAILABLE.
- `--range FIELD=MIN..MAX[UNIT]` (repeatable; either side may be empty), on scalar summary fields such as
  `focal_length_mm`, `outer_diameter_mm`, `center_wavelength_nm`, `reflectance`, `extinction_ratio`.
- `--near FIELD=VALUE[UNIT]`, or `--sort FIELD [--descending]`; `--limit N` (1..100, default 10);
  `--columns a,b`; `--out FILE.csv` (every matched row).

## Outputs

Stdout: `catalog_version`, the filters, `engine_matches`, `matched`, `excluded_missing_field`, `shown`,
`omitted`, units per column and the rows (catalog decimal strings, compared exactly).
Exit 0 rows match, 1 no product matches, 4 invalid input or no catalog installed
(`CATALOG_NOT_AVAILABLE`).

## Minimal example

```
os-optics-kit candidates --category LENS --form PLANO_CONVEX_LENS --wavelength-nm 780 --range focal_length_mm=90..110 --near focal_length_mm=100 --limit 5 --columns optical_sku,focal_length_mm,outer_diameter_mm
```

## Limits

- Reads the installed catalog release only (`os-optics catalog sync` or `os-optics catalog import FILE`).
- Products without a filtered or sorted field are excluded and counted per field.

## Version

0.1.0 (os-optics-kit 0.1.0), verified with os-optics 0.12.0 and catalog release 2026-10-28.1.
