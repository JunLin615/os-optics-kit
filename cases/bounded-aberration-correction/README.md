# Intensity-only aberration correction

How much focal intensity can five phase controls recover when spherical aberration
remains uncontrolled? This case optimizes a scalar on-axis intensity sensor and
compares the ideal, disturbed and corrected focal patterns at equal transmitted power.

![Engine relay: source, two lenses and the pupil plane](images/layout.png)

## Run

```bash
os-optics-kit cases copy bounded-aberration-correction --dest .
cd bounded-aberration-correction
python -m pip install -r requirements-aberration.txt
python workflow.py --out out/default
python workflow.py --out out/stronger --spherical-waves 0.1
python workflow.py --out out/default --redraw-only
```

The external workflow requires Python 3.12+ with the pinned dependencies. From a
checkout, run in this case directory. Each calculation uses a new output directory. The default took about 3 s on Python 3.14.8, os-optics 0.13.0;
no catalog download is needed. Open the generated `preview.png`.

## Optical model and handoff

The engine traces a 532 nm, 20 mW TEM00 source (0.65 mm waist radius) through
ideal −40 mm and +80 mm lenses at z = 20 and 60 mm. Its public `handoff` at
z = 119 mm supplies wavelength, two-axis q, power and field applicability.
The engine target `relay_radius` is MET: the 1/e² intensity radius is
1.300312968 mm, within 1.29–1.31 mm.

The external calculation uses this radius and power at a circular pupil of radius
1.5 mm. A fixed phase plate cancels the nominal curvature; the disturbance and
correction then act at the pupil, followed by an ideal 120 mm Fourier lens.
The pupil transmits 18.603049 mW. Its sampled amplitude is normalized to that
analytic aperture power. The sensor is noiseless and the phase controls continuous.
The PSF and correction are external scalar paraxial results; the saved engine run
describes the relay feeding them.

For normalized pupil radius r and azimuth θ, the six phase modes are
√3(2r²−1), √6r²cos2θ, √6r²sin2θ, √8(3r³−2r)cosθ,
√8(3r³−2r)sinθ and √5(6r⁴−6r²+1). Their default coefficients in waves
are [0.24, 0.17, −0.11, 0.12, −0.09, 0.055]. Powell search adjusts only the
first five, each within ±0.6 waves, using scalar sensor readings. The last
coefficient remains fixed. This basis has unit RMS over a uniformly illuminated
unit disk; the actual illumination is Gaussian.

## Results and useful changes

The principal observable is intensity **at the optical axis**, divided by the
ideal on-axis intensity with the same aperture and power. A displaced disturbed
PSF can have a larger peak elsewhere; the reported number is not peak-search Strehl.

| Uncontrolled spherical coefficient | Before correction | After correction | Sensor evaluations |
| --- | --- | --- | --- |
| 0.055 waves | 0.092393 | 0.895105 | 221 |
| 0.100 waves | 0.106409 | 0.692747 | 149 |

Increasing the uncontrolled mode reduces the recovered intensity. The corrected
default defocus is −0.219892 waves, rather than exactly −0.24: a small residual
defocus compensates part of the spherical phase under the Gaussian weighting.
Try `--bound-waves 0.1` to explore actuator saturation. All evaluations, including
poor trial settings, remain in `science.npz`; convergence checks remain active.

The 160² optimization grid is evaluated on a 512² pupil. A 768² pupil changes
the corrected ratio by 0.000185 (default) and 0.000470 (stronger spherical).
Independent polar Gaussian quadrature gives 0.894818 and 0.692023, respectively;
the absolute differences from the 512² calculation are below 0.00073.
The relay q matches an independent ABCD calculation to 6×10⁻¹⁵ relative error.
Parseval power conservation and direct coherent sum versus FFT center agree to
7×10⁻¹⁶. The default correction is a local bounded optimum.

## Saved results

- `engine/` keeps the normalized engine input, result and ray artifacts.
- `handoff.json` is the unmodified public engine handoff, with run identity.
- `config.json` holds optical parameters, grids and the input project digest.
- `metrics.json` holds results, checks, versions, runtime and engine target identity.
- `science.npz` holds the complex pupil fields (√W/mm), pupil coordinates (mm),
  phase (waves), a ±80 µm focal intensity crop (W/mm²), its axis (mm), the ideal
  peak, disturbance/correction coefficients and every sensor trial. State order
  is ideal, disturbed, corrected. History columns are the five controls then the
  dimensionless on-axis ratio. The unwrapped pupil phase outside the aperture
  carries no field; use the zero-amplitude mask when plotting it.
- `preview.png` uses one shared linear intensity scale. Redraw reads only saved
  arrays; it invokes neither the engine nor the optimizer.

The Fourier calculation uses a 6 mm pupil window and zero pads 512² samples to
2048² for focal sampling. Reconstruct the full focal field from the saved complex
pupil if another crop is needed. Keep generated results outside version control.

## Presentation from saved results (kit 0.3.0)

```bash
python present_case.py out/default out/stronger --out talk/en --language en --background light --size 1920x1080 --bundle talk/aberration-en.zip
python present_case.py out/default out/stronger --out talk/zh --language zh-CN --background light
python present_case.py out/default out/stronger --out talk/dark --language en --background dark
os-optics-kit present gallery talk/en
os-optics-kit present bundle talk/en --out talk/share.zip --only psf,response
```

Open `talk/en/index.html` offline. The helper reads `science.npz`, `metrics.json`
and `config.json`; it checks ratios against the complex pupil fields and verifies
their hashes after export. Neither the engine nor the optimizer runs. Use a new
`--out` for every style. Chrome/Chromium is needed for the native relay view;
Chinese text needs a CJK font such as Microsoft YaHei or Noto Sans CJK SC.

The valid native `presentation.json` builds the saved relay radius target with
public `present.build`. For this Z-directed relay, kit 0.3.0 does not expose the
camera up-axis in `present`; its direct view build fails with `CAMERA_UNDEFINED`.
The helper uses public `render_shots` with optical axis Z and up-axis Y, adding
the native view card to the gallery. The radius target measures the relay, not
corrected PSF performance. Comparison inputs must differ only in spherical
coefficient; differing bounds or optics are rejected to avoid confounded comparisons.

Kit 0.3.0 has native sweep, power and target plots, but no external-array PSF
plot type. This case therefore uses the published theme/font helpers for its two
external plots, then appends plot cards through the documented `assets.json`
protocol before calling official gallery and bundle functions. No private plot
handler or synthetic engine result is used. **Redraw the full set with
`present_case.py`**; standalone `present build` rebuilds only the native radius
target and removes the case-added view and external cards from its index.

`psf` shows the first run's three states with a shared linear 0–1 color scale;
`response` compares saved runs' on-axis ratios at their own equal-power ideal.
Each external card contains PNG, editable SVG, bilingual captions, selected
`plot-data.npz`, `provenance.json`, `redraw.json` (theme, units, normalization,
origin and source selection), and a copy of the case helper. Relative saved-run
paths and input hashes preserve traceability without embedding local absolute
paths. As with native bundles, restore the saved runs or pass their new paths to
the helper to rebuild the full gallery. An extracted external card can also be
redrawn independently from its bundled snapshot, without the original runs:

```bash
python present_case.py --redraw-external talk/en/assets/psf/redraw.json --out psf-zh --language zh-CN --background dark
```

The same command accepts the `response` card's `redraw.json`; each result includes
PNG, SVG and the portable snapshot/config/helper for another redraw.
