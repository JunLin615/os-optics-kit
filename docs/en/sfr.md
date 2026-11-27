# Measure slanted-edge SFR

[os-optics-kit](../../README.md) · English · [简体中文](../zh-CN/sfr.md)

`os-optics-kit sfr` measures the spatial frequency response (SFR) of a camera or
imaging module from one image of a slanted edge, by the ISO 12233 e-SFR method.
It needs no simulation. With `--design` it puts a design MTF saved from
`os-optics results imaging` on the same axes and states how the two differ.

## Run

```bash
python -m pip install "os-optics-kit[plot]"
os-optics-kit sfr edge.png --roi 400,300,80,120 --pixel-pitch-um 3.45 --out sfr-run
```

- **Input**: PNG or TIFF (8- or 16-bit grey, RGB or RGBA), a `.npy` array or a
  comma-separated `.csv` array without a header. RGB is reduced to luminance
  `Y = 0.2126 R + 0.7152 G + 0.0722 B` of the linearized channels (ITU-R BT.709),
  or one channel with `--channel R|G|B`.
- **ROI**: `--roi X,Y,W,H` in pixels, origin top-left, X along columns. It holds
  one straight edge tilted about 5° from vertical or horizontal, with at least
  8 px of flat field on each side. The tool detects the orientation.
- **Linearization**: values are used as given by default (`--oecf linear`, for raw
  or linear data). `--oecf gamma --gamma 2.2` applies `(v / full_scale) ** 2.2`,
  `--oecf srgb` the sRGB inverse transfer and `--oecf table --oecf-table oecf.csv`
  a measured OECF (`code,linear` rows, piecewise linear). Full scale is 255 or
  65535 for 8- or 16-bit data; set `--full-scale` for float data.
- **Units**: frequencies are in cycles/pixel along the edge normal.
  `--pixel-pitch-um` (or `--pixel-pitch-mm`, square pixels) adds cycles/mm (lp/mm).
- `--at 0.125,0.25` and `--at-mm 50` report the SFR at chosen frequencies.

Stdout is one compact JSON document. An existing, non-empty `--out` directory is
refused (`OUTPUT_EXISTS`) unless `--overwrite`. `--no-plot` writes no PNG and
needs no matplotlib; `.npy` and `.csv` inputs then need neither matplotlib nor Pillow.

## Method

| Step | Implementation |
| --- | --- |
| Orientation | Mean horizontal against mean vertical gradient of the ROI |
| Edge per row | Centroid of the central-difference derivative under a Hamming window, first at the ROI centre, then twice at the fitted edge |
| Edge fit | Least-squares line (`--fit-order 2`: quadratic) of position against row; angle and residual RMS |
| ESF | Every pixel projected onto its distance along the edge normal; 4 bins per pixel over the span every row covers; empty bins counted and filled by linear interpolation |
| LSF | Central difference of the ESF |
| Window | Hamming window centred on the LSF peak (0.08 at the farther end) |
| SFR | Magnitude of the FFT, normalized to 1 at zero frequency, divided by the derivative's transfer function `sinc(2 f / 4)` |

SFR is reported as a non-negative magnitude: an edge measurement loses phase.
`sfr50` is the first frequency where the SFR falls below 0.5 (linear
interpolation); `sfr_at_nyquist` is the SFR at 0.5 cycles/pixel. The curve is
written up to 1 cycle/pixel. The average over each 1/4-pixel bin stays in the
estimate, as in ISO 12233 (factor 0.974 at 0.5 cycles/pixel).

## Read the results

| File | Contents |
| --- | --- |
| `sfr.csv` | Frequency (cycles/pixel, cycles/mm with a pitch), SFR, derivative correction |
| `esf.csv` | Bin distance, ESF, samples per bin, `filled` for interpolated bins |
| `lsf.csv` | LSF, window and windowed LSF |
| `edge.csv` | Per-row centroid, fitted position, residual and whether the row was used |
| `sfr.png` | ROI with the fitted edge, ESF, LSF with window, SFR with Nyquist and SFR50 |
| `summary.json` | The stdout summary plus provenance: input path and SHA-256, ROI, settings, method, software |

`edge.angle_deg` is the angle from the image column (vertical edge) or row
(horizontal edge). Status and issues:

| Status / exit | Meaning |
| --- | --- |
| `PASS` / 0 | No diagnostic |
| `WARNING` / 0 | SFR measured; see `issues` |
| `UNKNOWN` / 2 | More than 25 % of the ESF bins were empty: the edge angle and row count give too little sub-pixel phase coverage |
| Error / 4 | Refused input, JSON error on stderr |

| Issue | Trigger |
| --- | --- |
| `EDGE_ANGLE_SHALLOW`, `EDGE_ANGLE_STEEP` | Angle below 2° or above 30° |
| `INCOMPLETE_PHASE_COVERAGE`, `EMPTY_BINS` | Edge moves less than 1 px over the rows; bins without samples |
| `CLIPPED_PIXELS` | ROI pixels at 0 or at full scale |
| `FEW_ROWS`, `ROWS_WITHOUT_EDGE` | Fewer than 20 rows; rows without a clear step |
| `LARGE_FIT_RESIDUAL` | Edge-fit residual RMS above 0.25 px (noise, a curved edge, a second edge) |
| `LOW_EDGE_CONTRAST`, `SHORT_ESF_SPAN` | Michelson contrast below 0.2; less than ±8 px of ESF |
| `SFR50_NOT_REACHED` | SFR stays at or above 0.5 up to 1 cycle/pixel |

Refusal codes include `INPUT_NOT_FOUND`, `UNSUPPORTED_INPUT`, `UNSUPPORTED_IMAGE_MODE`,
`ROI_INVALID`, `ROI_TOO_SMALL`, `EDGE_NOT_FOUND`, `EDGE_OUTSIDE_ROI`,
`NONFINITE_VALUES`, `OECF_RANGE` and `IMAGE_READER_MISSING` (PNG/TIFF without Pillow).

## Compare with the design

```bash
os-optics results imaging run --monitor image_plane --request imaging-request.json > imaging.json
os-optics-kit sfr edge.png --roi 400,300,80,120 --pixel-pitch-um 3.45 --design imaging.json --design-line SRC0#0 --out sfr-cmp
```

`--design` reads the JSON of `os-optics results imaging` (UTF-8 or the UTF-16 of
a Windows redirect) or a `frequency,value` CSV with `--design-unit cy/mm|cy/px`.
For engine JSON, `--design-line SOURCE#INDEX` picks the field and wavelength when
there are several; `--design-direction` (`both`, `tangential`, `sagittal`) and
`--design-curve` (`mtf`, `diffraction_limited_mtf`, `geometric_mtf`) pick the
curve. A cycles/mm design curve needs the pixel pitch (`PIXEL_PITCH_REQUIRED`):
`f[cy/px] = f[cy/mm] × pitch[mm]`.

Only a curve the engine states as `KNOWN` (or `KNOWN_ZERO`) is taken: a declined curve (for example a
geometric MTF outside its regime, or the curves of a stand-in pupil) is refused with
`DESIGN_CURVE_UNAVAILABLE` and the engine's state and reason, even when the JSON
holds an array for it. `os-optics-kit mtf` writes the design's optical MTF on its
own, as a table, a JSON document and one figure per line ([MTF](mtf.md)); the
`imaging.json` it keeps is a valid `--design`.

The two curves are different quantities:

| Aspect | Measured SFR | Design curve |
| --- | --- | --- |
| Quantity | Optics × pixel aperture × sampling × any processing in the image | Optical MTF only |
| Pixel aperture, sampling, image processing | Included | Not included |
| Sign | Magnitude | Signed values kept; compared as \|MTF\|, negative frequencies listed |
| Direction | Edge normal along image x or y | Tangential or sagittal of the design field |

The measured SFR therefore normally lies below the optical MTF. The tool adds no
pixel-aperture or sampling term to the design curve. `comparison.csv` holds the
measured frequencies inside each design curve's range, the design curve
interpolated linearly (never extrapolated), `|design|` and the difference
measured − |design|. `comparison.json` states the input differences, the design
identity (line, wavelength, engine input hash) and, per curve, the values at
Nyquist, the 0.5 crossings, the largest and RMS difference and every native
design frequency with the measured SFR there. `comparison.png` shows both.

## Verification

The tests render slanted edges with a known answer: a step blurred by a Gaussian
PSF of SD σ px and integrated over square pixels (100 % fill), exactly across the
edge and with 32 sub-rows along it. The true SFR along the edge normal is
`exp(-2π²σ²f²) |sinc(f cos θ) sinc(f sin θ)|`, which is `exp(-2π²σ²f²) |sinc f|`
at θ = 0. Over 0–0.5 cycles/pixel, for 3°, 5°, 8° and −6° edges, vertical and
horizontal, and σ = 0.3, 0.5 and 1.0 px:

| Check | Declared tolerance | Typical |
| --- | --- | --- |
| SFR, noise-free | ±0.025 | ≤ 0.019 (σ = 0.3 px), ≤ 0.01 (σ ≥ 0.5 px) |
| Edge angle, noise-free | 0.01° | < 10⁻⁵° |
| SFR50, noise-free | 0.012 cycles/pixel | ≤ 0.01 |
| Edge angle, noise SD 0.02 on a 0.6 step | 0.1° | 0.03° |
| SFR, noise SD 0.005 / 0.01 / 0.02 | ±0.035 / 0.06 / 0.12 | 0.02 / 0.036 / 0.072 (worst of five seeds) |

## Reuse from Python

```python
from os_optics_kit.sfr import measure_sfr, synthetic_edge, true_sfr

image = synthetic_edge((100, 80), angle_deg=5.0, sigma_px=0.5)
result = measure_sfr(image)
f, sfr = result["frequency"], result["sfr"]
print(result["angle_deg"], abs(sfr - true_sfr(f, 0.5, 5.0))[f <= 0.5].max())
```

`measure_sfr(array)` takes values already linear in exposure and writes no files;
`analyze_sfr(path, out_dir, SfrConfig(...))` runs the whole command.
