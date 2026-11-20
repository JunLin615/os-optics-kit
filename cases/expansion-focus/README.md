# Beam expansion and focusing

[简体中文](README.zh-CN.md) · [Agent case sheet](CASE.md)

A 780 nm Gaussian beam with a 0.5 mm waist radius is expanded three times by two ideal thin lenses (f = 50 mm and
150 mm) and focused by a third lens (f = 100 mm). The design is catalog-free and fully parameterized: `workflow.py`
rebuilds it from your inputs and studies one parameter at a time.

![3D view: the source SRC, the expander lenses L1 and L2, the plane `collimated`, the focusing lens L3 and the plane `focus` with the traced beam](images/iso.png)

![Section along the beam: L1 and L2 expand the beam, L3 focuses it onto the plane `focus`](images/section.png)

Both images are the engine's browser-free snapshots of a run of `project.json`:
`os-optics view run --no-browser --snapshot DIR` with `--views iso --size 960x540`, and again with
`--section ALL --frame=scene --size 1200x420`. In a kit checkout, `os-optics-kit cases images expansion-focus`
re-renders exactly these views as recorded in `images.json`.

## Run it

```bash
os-optics-kit cases copy expansion-focus --dest .
cd expansion-focus
os-optics project run project.json --out-dir run
os-optics results targets run
python workflow.py --out wf
```

## Results

| Target | Value | Bound | Assessment |
| --- | --- | --- | --- |
| `expanded_radius` | 1.50112 mm | 1.5 mm ± 2 % | MET |
| `spot_radius` | 0.016552 mm | ≤ 0.02 mm | MET |
| `waist_location` | −0.03649 mm | −0.5 … 0.5 mm | MET |

The default workflow runs 22 designs. Every engine value agrees with an independent complex-q Gaussian calculation.

| Study | Values | PASS | FAIL | Out of model |
| --- | --- | --- | --- | --- |
| `gap_error_mm` | −5 … 5 | 3 | 4 | 0 |
| `f3_mm` | 50 … 200 | 2 | 3 | 0 |
| `wavelength_nm` | 532, 780, 1064, 1300 | 2 | 1 | 1 (outside the lens band) |
| `waist_radius_mm` | 0.25 … 4 | 3 | 1 | 1 (severe truncation) |

`studies/` adds a sensitivity study, a band-edge sensitivity study, a seeded tolerance study (92 of 100 samples
pass) and two compute-budget edits. Verified with os-optics 0.13.0; run status PASS.
