# Polarization control with a half-wave plate and a polarizer

[简体中文](README.zh-CN.md)

A linearly polarized 1030 nm laser (1 mW, polarization along design +Y) passes a half-wave plate and a linear
polarizer. With the plate's fast axis at 22.5° the polarization turns to 45°, the polarizer's transmission axis, and
0.891 mW reaches the detector (plate transmittance 0.99, polarizer 0.9). The design is catalog-free and fully
parameterized: `workflow.py` rebuilds it from your inputs.

![3D view: the source SRC, the half-wave plate HWP, the plane after_hwp, the polarizer POL and the plane detector](images/iso.png)

The view is the engine's browser-free snapshot of a run of `project.json`
(`os-optics view run --no-browser --snapshot DIR --views iso --size 960x540`). In a kit checkout,
`os-optics-kit cases images polarization-control` re-renders it exactly as recorded in `images.json`.

## Run it

```bash
os-optics-kit cases copy polarization-control --dest .
cd polarization-control
os-optics project run project.json --out-dir run
os-optics results targets run
python workflow.py --out wf
```

## Results

| Target | Value | Bound | Assessment |
| --- | --- | --- | --- |
| `transmitted_power` | 0.891 mW | ≥ 0.8 mW | MET |
| `output_azimuth` | 45° | 45° ± 0.5° | MET |

The default workflow runs the nominal design and 13 study samples; all 94 compared values (power, azimuth, Stokes
parameters) agree with Jones calculus and Malus' law.

| Study | Values | PASS | FAIL | Out of model |
| --- | --- | --- | --- | --- |
| `hwp_angle_deg` | 0 … 67.5° | 1 (22.5°) | 7 | 0 |
| `wavelength_nm` | 1000 … 1100 | 3 | 0 | 2 (1064 and 1100 nm, outside the plate's band) |

At 1064 and 1100 nm the targets read PASS, but the plate is characterized for 1000–1060 nm only, so the run is out
of model (`MODEL_WAVELENGTH_OUTSIDE_BAND`).

Verified with os-optics 0.13.0; run status PASS.
