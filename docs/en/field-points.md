# Field points from a Project

[os-optics-kit](../../README.md) · English · [简体中文](../zh-CN/field-points.md)

`os-optics-kit field-points` evaluates stated field points of a native Project at a
fixed image plane: field angles, object heights or real image heights. For each
point it reports the chief ray's hit point, image height and angle on the plane,
a first-order comparison and, on request, the tangential and sagittal optical MTF
of each spectral line from the Engine's imaging analysis, with sampled field minima.
The input Project is never modified.

## Run

```bash
os-optics-kit field-points project.json --plane image_plane --out fp \
    --angles-deg 0,5,10 --mtf-frequencies 20,40 --launch-grid 128
```

- **Field kind**, one per request: `--angles-deg` (ANGULAR, degrees from the
  reference axis), `--object-heights-mm` (OBJECT_HEIGHT, finite object only) or
  `--image-heights-mm` (IMAGE_HEIGHT, the real chief-ray height on the plane,
  solved). Giving two refuses with `FIELD_KIND_MIXED`. `--expect-field-kind KIND`
  refuses a request of another kind (`FIELD_KIND_MISMATCH`): pass the kind the
  Project's documentation states, so an angle is never read as a height.
- **Direction**: `--azimuth-deg` (default 0) is the field direction in the plane's
  (u, v) axes, measured from +u toward +v. An ANGULAR beam is turned toward it, so
  a lens of positive power images it on that side; an OBJECT_HEIGHT object point
  moves along it, and its real, inverted image lies on the opposite side.
- **Reference source**: `--source SRC` names the source on the optical axis (the
  Project's only source by default). Its emission axis is the request's optical
  axis; its spectral lines are the field points' lines unless `--wavelengths-nm`
  states others (equal shares of its power, or its own line powers).
- **Configurations**: a Project that declares configurations needs
  `--configuration ID` (`CONFIGURATION_REQUIRED` lists them); that configuration
  is flattened with the Engine's configured Project.
- **MTF**: `--mtf-frequencies F,..` (cycles/mm) runs one imaging analysis on the
  evaluated Project (32 field points per call). `--launch-grid`, `--no-convergence`
  and `--imaging-request FILE` (any other key of `os-optics schema imaging-request`)
  set the pupil sampling; the command sets the fields, lines, frequencies and the
  chief ray's crossing itself.
- **Files**: `--out DIR` must be new or empty (`--overwrite` replaces this
  command's files). `--write-run` also writes the evaluated run `DIR/run`, which
  `os-optics-kit field`, `fans` and `mtf` read; it is a full run (minutes on a large
  lens).

## Method

A field point is a copy of the reference source **turned** about a design-frame
axis across the beam (`reference axis x field direction`) and placed so that its
chief ray, the beam-centre ray the Engine traces, crosses the centre of the
aperture stop. All points are extra source components of one evaluated Project,
which is the input (flattened) with its other sources and its targets removed;
one evaluation and one imaging call cover them. Field points are not
configurations: in a lens Project configurations are design states.

- **Object conjugate** from the reference source's waist: within one Rayleigh range
  of the source origin the rays leave nearly parallel, so the object is at
  infinity; a waist at least ten Rayleigh ranges upstream is a point object at the
  waist. Anything between is refused (`CONJUGATE_UNDETERMINED`). The numbers are in
  `conditions.conjugate`. An object height at infinity is refused
  (`OBJECT_AT_INFINITY`).
- **Ray aiming**: three probe chief rays give a paraxial entrance-pupil estimate;
  each point then starts with its chief ray through that estimate and its lateral
  offset (ANGULAR) or aim point (OBJECT_HEIGHT) is corrected until the chief ray
  crosses the stop plane within `--aim-tolerance-mm` (default 0.001 mm) of the stop
  centre. The crossing is read from the Engine's chief-ray segments, so nothing is
  added to the evaluated Project. Aiming evaluations trace the chief-ray line of
  each point alone, linearly polarized: a chief ray's path does not depend on the
  polarization or on the other lines. Without an aperture stop, `--pivot-mm X,Y,Z`
  turns the points about that point instead (`aim: NOT_AIMED`); without either the
  command refuses (`NO_APERTURE_STOP`).
- **Image heights** are measured on the plane from the on-axis chief ray's hit
  point. An IMAGE_HEIGHT request solves the angle (object at infinity) or object
  height (finite object) from the first-order guess until the height is within
  `--height-tolerance-mm` (default 1e-4 mm). An on-axis point is always traced
  (a requested zero, or `FP_AXIS`).
- **Plane aperture**: while aiming, the plane's active area is widened so every hit
  point is read; the evaluated Project keeps the declared aperture unless a point
  would fall outside it, and then widens it (pose unchanged, `plane.widened`).
  `within_declared_aperture` says, per point, whether the declared plane holds it.
- **First-order comparison** (`FIRST_ORDER_COMPARISON`, not a distortion figure):
  object at infinity, `f tan(theta)` with the EFL of the Engine's paraxial analysis
  of the elements on the reference axis at the chief-ray wavelength, and the
  paraxial focus's distance from the plane; finite object, `|m| h` with `m` the A
  element of the paraxial ABCD from the object plane to the plane. When the
  paraxial analysis is refused the reference is null with its reason.
- **MTF**: per point, line, direction and frequency, read through the kit's
  imaging reader, never interpolated: `value`, `status`, `value_kind`, `reason`,
  `converged`, `model_basis`. Only `EXACT` values (state `KNOWN`, converged,
  in-model) count as confirmed.

## Read the results

`field-points.json` (schema `os-optics-kit.field-points` v1):

- `input`: `path`, `sha256` of the bytes as read, `sha256_lf` (after CRLF -> LF),
  the Engine's `project_hash`, `engine_input_hash`, `catalog_version`, and
  `configuration_id`.
- `evaluated`: `evaluated-project.json` with its hashes and `derivation` (removed
  sources and targets, added sources with their poses, the plane's declared and
  evaluated aperture); `engine` version and build.
- `conditions`: field kind and unit, azimuth and field direction, `requested` (in
  request order), conjugate, reference source facts (its axis, the plane normal,
  its chief ray's hit and stop offset), chief-ray wavelength, lines and their
  powers, frequencies, the imaging request, the aim (`pupil_magnification`,
  `entrance_pupil_from_stop_mm`) and the first-order reference.
- `points[]`: `status` (`EVALUATED` or `NOT_EVALUATED` with `reason`: `NO_ARRIVAL`,
  `OUTSIDE_PLANE`, `STOP_NOT_REACHED`, `CHIEF_RAY_PATH_DIFFERS`, `AIM_NOT_CONVERGED`,
  `HEIGHT_NOT_REACHED`), `field` (angle, object height), `pose`, `chief_ray`
  (`image_point_uv_mm`, `image_height_mm`, `image_azimuth_deg`, `angle_deg` to the
  plane normal, `stop_offset_mm`, `aim`, `iterations`, `within_declared_aperture`),
  `first_order`, `spot` (pupil rays launched and arrived), `lines` (the same per
  spectral line) and `mtf[]`.
- `minima[]` per frequency: `per_line` (each wavelength) and `all_lines`, with
  `field_grid`, `directions`, `value`, `status` and `limiting_sample`. `CONFIRMED`
  needs every sample to be `EXACT`; otherwise `UNCONFIRMED`, `value` is the minimum
  of the exact samples with `bound: UPPER`, and `missing[]` names each other sample
  and its reason. `metric: per_line_minimum`: a per-line worst case is not a
  spectrally weighted (multispectral) MTF.
- `imaging[]`: each imaging file, its identity and its request. `scope` states what
  is supported.

Exit codes: 0 `COMPLETED` (every point evaluated, every MTF sample exact), 2
`INCOMPLETE` (everything is still written), 4 refused, with a JSON error on stderr.

A CHIEF_RAY_PATH_DIFFERS point meets other surfaces than the on-axis chief ray (it
misses an element, or is reflected): it lies beyond the system's field.

## Four traps of hand-made field points

The skill page `skills/field-points/traps.md` states each with its symptom; the
Engine-side issue is [os-optics#87](https://github.com/JunLin615/os-optics/issues/87).

1. **Shifted, not turned**: the run completes, the image stays near the axis while
   the incidence angle changes, or a model-scope finding appears; nothing says the
   field was not applied.
2. **Turned about the beam's own axis**: the quaternion changes, the result equals
   the 0° field.
3. **The plane's aperture clips the beam**: the point has no arrival (it is under
   `outside_active_area`) and a default imaging request leaves it out silently.
4. **`readings` leaves `quantity_state` at `NOT_COMPUTED`**: the arrivals are there;
   only the plane's power bounds (`plane_bounds`) were not computed.

## Lens records

For an os-optics-lens record, bind facts to the committed Project only:

| Lens | Report |
|---|---|
| `native_project.sha256` | `input.sha256_lf` |
| `configuration_id` | `input.configuration_id` |
| image plane (fixed) | `plane.observation_id`; the pose never changes |
| `field.kind` | `conditions.field_kind` (pass it as `--expect-field-kind`) |
| `field_basis` | the request, e.g. image-height fractions of the image-circle radius along `--azimuth-deg` |
| `sampled_field_fractions` | `conditions.requested` divided by that radius, same order |
| `frequency_lp_mm` | `minima[].frequency_cyc_per_mm` (numerically the same) |
| directions | `minima[].directions` |
| `limiting_sample` | `minima[].all_lines.limiting_sample` |
| spectrum | `conditions.wavelengths_nm` and `line_powers_w` |

The minimum is per line (`per_line_minimum`): a record whose metric is a spectrally
weighted MTF states other conditions. Per-point numbers are Engine-computed, a
minimum over the sampled points is derived from them. `evaluated.derivation` lists
every change from the committed Project.

## Verification

Verified with os-optics 0.14.0:

- **aspheric-lens case** (stop in front of five elements, EFL 49.9967 mm at
  587.5618 nm): the 12.5° chief ray lands 11.0997738 mm from the axis, the height of
  the case's own stop-aimed 12.5° source within 1e-6 mm; f tan(theta) is
  11.0840111 mm (+0.14 %). An 80° request misses the elements
  (`CHIEF_RAY_PATH_DIFFERS`) and leaves the minimum `UNCONFIRMED`. The tangential and
  sagittal MTF equal a direct imaging call with the same request
  (`tests/test_field_points_engine.py`).
- **lenslibrary-528155** (Dagor, f/8, EFL 239.8934 mm): aimed chief rays at 5°,
  17.7° and 26.6° land at 20.98841, 76.58583 and 120.25078 mm, within 0.11 % of
  f tan(theta), on a plane widened from its declared 19.6045 mm radius. The same 5°
  source turned about its own origin and not aimed (`--pivot-mm -100,0,0`) crosses
  the stop 8.91 mm from its centre and lands at 20.8662 mm (-0.58 %): an unaimed
  chief ray reads as distortion.
- **Yutong P2b** (`--source F0W1`, eleven real image heights 0 to 0.8 of 21.635 mm
  along the sensor diagonal at 587 nm): the solved field angles reproduce the
  record's stop-aimed sources within 2e-4° (6.58848° at 0.1, 42.78214° at 0.8), with
  a chief-ray angle of 14.4153° at 0.8.

## Reuse from Python

```python
from os_optics_kit import field_points

request = field_points.Request(project="project.json", plane="image_plane", kind="ANGULAR",
                               values=[0.0, 5.0, 10.0], mtf_frequencies=[20.0, 40.0])
report = field_points.run(request, "fp")
report["minima"][0]["all_lines"]
```

`field_points.minima(samples, frequencies, wavelengths, field_grid)` and the
geometry in `os_optics_kit.field_geometry` (turned poses, the conjugate rule, aiming)
are usable on their own.

Related: [os-optics#87](https://github.com/JunLin615/os-optics/issues/87),
`os-optics-kit field`, `os-optics-kit mtf`.
