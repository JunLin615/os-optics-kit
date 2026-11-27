# Optical MTF of an imaging run

[os-optics-kit](../../README.md) · English · [简体中文](../zh-CN/mtf.md)

`os-optics-kit mtf` turns the engine's imaging analysis into an MTF table, a JSON
document and one figure per spectral line. The numbers are the engine's
(`os-optics results imaging`, `RunResults.imaging`): the tool reads them, states
their status and draws them; it computes no MTF of its own.

It is the optical MTF only: no pixel aperture, no sampling and no slanted-edge
SFR. A measured SFR is [`os-optics-kit sfr`](sfr.md); a design-side SFR (optical
MTF × pixel aperture) is JunLin615/os-optics#42. There is no PASS/FAIL and no
target here; an MTF floor in a design loop is a constraint of `optimize` and the
[evaluation adapter](evaluation-adapter.md).

## Run

From a saved run (one imaging analysis, in-process):

```bash
os-optics project run project.json --out-dir run
os-optics-kit mtf run --monitor image_plane --request imaging-request.json --out mtf
```

The analysis is kept as `mtf/imaging.json`. From a saved analysis (no engine
analysis at all; `--run` checks that the document is of that run by its
`engine_input_hash` and `run_result_hash`):

```bash
os-optics results imaging run --monitor image_plane --request imaging-request.json > imaging.json
os-optics-kit mtf --imaging imaging.json --run run --out mtf
```

A run of a Project with configurations holds one run per configuration: name it
with `--configuration ID` (without it the command lists the ids,
`CONFIGURATION_REQUIRED`). `--no-figures` writes the table and the document only
and needs no matplotlib. `--theme presentation|report|FILE` and
`--set KEY=VALUE` (for example `--set language=zh-CN`) style the figures as in
[presentation](presentation.md). The output directory must be new or empty;
`--overwrite` replaces this command's own files in it.

The request is the engine's (`os-optics schema imaging-request`). Two settings
decide whether the table is what you expect:

- `frequencies_cyc_per_mm`: without it every line gets its own axis, 33 points
  from 0 to its cut-off 2 NA/λ, so lines do not share rows.
- `fields`: without it the engine analyses only the sources that reach the
  plane inside its aperture. The tool lists every line the plane clips as
  `OUTSIDE_ACTIVE_AREA` (RUN mode, or `--imaging` with `--run`); name the field
  in `fields` or widen the plane to analyse it.

A fast lens needs a dense `launch_grid`: below it the engine declines the
diffraction MTF (`PUPIL_UNDERSAMPLED`) and the table says so.

## Read the results

`mtf.csv` has one row per line × direction × frequency of that line's axis:

| Column | Meaning |
| --- | --- |
| `field` | the line, `<source>#<spectral index>` (`SRC#0`) |
| `source_id`, `wavelength_nm` | the source and the line's vacuum wavelength |
| `direction` | `tangential` or `sagittal` |
| `frequency_cyc_per_mm` | the engine's frequency, cycles/mm |
| `mtf`, `diffraction_limited_mtf`, `geometric_mtf` | the engine's values, unchanged |
| `*_status` | why a value is there or not (below) |
| `unit` | `1`: MTF is a modulation ratio |

| Status | Value | Meaning |
| --- | --- | --- |
| `KNOWN` | yes | the engine's value |
| `NOT_CONVERGED` | yes | the convergence pass (launch grid doubled) differs by more than 0.03 |
| `BEYOND_NYQUIST` | empty | above the PSF map's Nyquist frequency |
| `OUT_OF_MODEL_SCOPE`, `UNKNOWN`, `NOT_COMPUTED` | empty | the engine declines the curve; `mtf.json` has its reason |
| `ABSENT` | empty | the analysis states no such curve |

A declined curve is never written, even when the engine's JSON keeps an array
for it (a geometric MTF outside its regime, the curves of a stand-in pupil).
`os_optics_kit.mtf.read_csv(path)` reads the table back as the same records.

`mtf.json` (schema `os-optics-kit.mtf`, version 1) holds:

- `input`: mode (`run` or `imaging`), run, monitor, configuration, the
  analysis file and its sha256, the request file;
- `identity`: the run's (`RunResults.identity()`: hashes, engine build,
  catalog) and the analysis's (`identity.physical`, request, analysis
  version);
- `conventions`: the rules on this page and the engine's own plane and axes
  text;
- `lines`: per line the wavelength, the image point (`reference` `CENTROID` or
  `CHIEF`, `point_mm`, `image_axis`, `tangential_axis`), the PSF window's light
  share and Nyquist frequency, and every curve with state, reason, model
  basis, convergence and value kind (values only for a usable curve);
- `not_analysed`: fields that are no beam source (`FIELD_NOT_A_BEAM_SOURCE`),
  lines without an arrival (`NO_ARRIVAL`), requested fields or wavelengths that
  gave no line (`NO_LINE`, `WAVELENGTH_NOT_ANALYSED`) and clipped lines
  (`OUTSIDE_ACTIVE_AREA`);
- `counts` and `files`.

Each figure `mtf-<source>-<index>.png` shows tangential and sagittal MTF (solid)
and their diffraction limits (dashed). A curve without values is not drawn and
is named in the figure; an unconverged curve is labelled.

The status is `COMPLETED` (exit 0) when every line's tangential and sagittal
MTF is `KNOWN` and nothing was left out, else `INCOMPLETE` (exit 2, all files
written). Refusals exit 4 with a JSON error on stderr: `BAD_ARGUMENT`,
`OUTPUT_EXISTS`, `IMAGING_INVALID`, `MONITOR_NOT_FOUND`,
`CONFIGURATION_REQUIRED`, `IMAGING_RUN_MISMATCH`, `RUN_UNREADABLE`,
`ANALYSIS_REFUSED` (with the engine's `engine_code`), `PLOT_EXTRA_MISSING`.

## Conventions

- One curve per spectral line. The engine has no polychromatic or weighted
  MTF, and a weighted sum of these magnitudes would not be one (it drops the
  OTF phase and each line's own centring).
- MTF(0) is the share of the light inside the PSF window, not 1; the curves are
  not renormalized.
- The PSF and MTF lie on the plane normal to the line's image-space axis through
  its reference point, not on a tilted detector. Tangential is the image
  plane's normal projected on that plane; on axis it is the plane's u axis.

## Verification

Recorded with os-optics 0.14.0. The sonnar-telephoto-lens case (85 mm, f/2.4,
0°, 7° and 14° fields at 587.56 nm, the case's request):

| Line | Direction | MTF 20 | MTF 40 | MTF 100 | Diffraction limit 40 | Geometric MTF |
| --- | --- | --- | --- | --- | --- | --- |
| SRC0#0 (0°) | T | 0.9015 | 0.7039 | 0.0995 | 0.9267 | `OUT_OF_MODEL_SCOPE` |
| SRC1#0 (7°) | T | 0.6699 | 0.3491 | 0.1069 | 0.9266 | 0.3188 at 40 |
| SRC1#0 (7°) | S | 0.3762 | 0.0597 | 0.0240 | 0.9260 | 0.0624 at 40 |
| SRC2#0 (14°) | T | 0.7158 | 0.5533 | 0.3352 | 0.8680 | `OUT_OF_MODEL_SCOPE` |

The tables equal the engine's arrays value for value (tests on the saved
analysis `tests/data/mtf-sonnar-imaging.json`). On the achromatic-imaging case (its
request, three lines on axis) the command's 24 MTF values equal the case's
recorded `data/snapshot.json` exactly, for example 0.6891 / 0.6323 / 0.5889 at
40 cycles/mm for 486 / 588 / 656 nm. The case figure scripts of
sonnar-telephoto-lens, aspheric-lens and midwave-infrared-imager now run the
analysis in-process and read it through the same reader, and redraw their
recorded figures byte for byte. An engine test runs the command once on the
achromatic-imaging Project and checks that exactly one analysis runs, that the
table equals a direct `RunResults.imaging` call, that a rerun from the saved
`imaging.json` gives the same table and that a tampered document is refused.

## Reuse from Python

```python
from os_optics_kit import imaging_results, mtf

document = mtf.imaging_of("run", "image_plane", {"frequencies_cyc_per_mm": [10, 20, 40]})
for row in mtf.rows(document):          # the mtf.csv records
    print(row["field"], row["direction"], row["frequency_cyc_per_mm"], row["mtf"], row["mtf_status"])
curve = imaging_results.curve(document, "SRC#0", "mtf", "tangential")
imaging_results.value_at(curve, 20.0)   # (value, status): a frequency of the line's own axis
```

`imaging_of(run, monitor, request, saved=FILE)` reads a saved analysis and
checks it against the run instead of running one; `mtf.analyse(out_dir, ...)`
is the whole command.

Related: JunLin615/os-optics-kit#31, JunLin615/os-optics#42.
