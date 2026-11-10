# Presentation material from saved results

[os-optics-kit](../../README.md) · English · [简体中文](../zh-CN/presentation.md)

`os-optics-kit present` turns saved runs and sweep studies into slide-ready material: system views, result
plots, comparison figures, animations, an offline gallery and a portable bundle. Everything is drawn from one
saved configuration, `presentation.json`. Changing the language, background, text size or figure size redraws
the figures from the saved results; no simulation runs and the runs and studies are never modified.

## Install

```bash
python -m pip install "os-optics[protected]" "os-optics-kit[plot,render,media]"
os-optics catalog sync
```

| Extra | Needed for |
| --- | --- |
| `plot` | every figure (matplotlib) |
| `render` | system views and animations, together with an installed Chrome or Chromium |
| `media` | MP4 animations; GIF needs only `plot` |

The example below uses a catalog lens, hence `os-optics catalog sync`.

## Prepare a presentation set

Start from saved results: a run of the focusing example and a five-sample sweep of the lens position.

```bash
os-optics project example --kind focus > project.json
os-optics project run project.json --out-dir nominal
```

`sweep.json`:

```json
{"base_project": "project.json", "max_evaluations": 5,
 "parameters": [{"name": "l1_shift", "unit": "mm", "values": [-1, -0.5, 0, 0.5, 1],
   "ops": [{"op": "transform", "selector": {"ids": ["L1"]}, "frame": {"component": "L1"},
            "translation_mm": [0, 0, "$value"]}]}]}
```

```bash
os-optics-kit sweep sweep.json --out study
os-optics-kit present init nominal study --out talk --title "Focusing lens"
os-optics-kit present build talk/presentation.json
```

`init` writes `talk/presentation.json` with the sources (paths relative to the file) and a default set of
assets. Each source is named after its directory; a directory named `run` takes the name of the directory
above it, so `study/samples/s0003/run` becomes `s0003`.

| Asset | Kind | Content |
| --- | --- | --- |
| `view-nominal` | view | 3D system view of the run, with an interactive viewer page |
| `power-nominal` | plot `power` | received power per observation plane and the power-ledger sinks |
| `targets-nominal` | plot `targets` | each target's value, bounds and assessment |
| `sweep-study-focus_position`, `sweep-study-spot_radius` | plot `sweep` | one curve per study target, outcome of every sample marked |
| `system-and-result` | compose `system-plot` | the system view beside the first study curve |

`build` draws every asset and writes:

```text
talk/
  presentation.json        redraw configuration
  assets.json              asset index: files, sizes, captions, sources, status per asset
  index.html               offline gallery
  contact-sheet.png        all previews, labelled by asset id
  previews/<id>.png        previews, at most 640 px wide
  assets/<id>/             full-size image, SVG/PDF, data.json, caption.en.txt,
                           caption.zh-CN.txt, provenance.json
```

A view adds `<id>.overlay.svg` (title and caption as editable text, the size of the image),
`<id>.annotated.png` (the view with its title) and `camera.json`. Stdout lists each asset's id, status,
preview and image; `os-optics-kit present list talk` prints the index again. Progress lines go to stderr
(`--quiet` turns them off).

Pick assets from `contact-sheet.png` by id; `assets.json` maps an id to its full-size image, captions,
data and provenance. Write the narrative from the values in `data.json` and the captions.

## Restyle without rerunning

```bash
os-optics-kit present build talk/presentation.json --out talk-zh \
    --set theme.language=zh-CN --set theme.background=light --set theme.font_scale=1.3
```

`--out` writes the variant into a new directory with its own `presentation.json`; the source paths are
rewritten relative to it. Without `--out`, the settings are saved in `talk/presentation.json` and the set is
redrawn in place. Building again with unchanged settings reuses every asset; an asset is redrawn only when
its definition, theme, sources or files changed.

| Setting | Values |
| --- | --- |
| `theme.preset` | `presentation` (default), `report` |
| `theme.language` | `en`, `zh-CN` |
| `theme.background` | `light`, `dark`, `transparent`, `#rrggbb` |
| `theme.font_scale` | 0.5 to 3 |
| `theme.size` | `slide`, `4k`, `half`, `square`, `report`, `thumbnail`, `WIDTHxHEIGHT` |
| `theme.title` | `true`, `false` |
| `theme.legend` | `auto`, `outside`, `none` |
| `theme.units` | display units, e.g. `{"mm": "um"}` |
| `theme.labels` | readable names for identifiers, in both languages |
| `assets.<id>.camera` | `[angle to the optical axis, elevation]` in degrees, e.g. `[30,15]` |
| `assets.<id>.caption`, `assets.<id>.title` | text or `{"en": "...", "zh-CN": "..."}` |

`os-optics-kit present settings` prints every key, size, asset kind and field. Identifiers such as
`target:spot_radius` appear as they are until you give them a label:

```bash
os-optics-kit present build talk-zh/presentation.json \
    --set 'theme.labels.target:spot_radius={"en": "Spot radius", "zh-CN": "光斑半径"}' \
    --set 'theme.labels.param:l1_shift={"en": "Lens shift", "zh-CN": "透镜位移"}'
```

Values that contain JSON can also be written directly into `presentation.json`, which avoids shell quoting.
`--only ID,...` draws the listed assets and the assets they use; the others keep their previous build.

## Sizes, formats and fonts

| Size | Pixels |
| --- | --- |
| `slide` | 1920 × 1080 |
| `4k` | 3840 × 2160 |
| `half` | 960 × 1080 |
| `square` | 1080 × 1080 |
| `report` | 1600 × 1000 |
| `WIDTHxHEIGHT` | as given |

Text keeps its proportion to the figure at every size: a `4k` figure is a sharper version of the `slide`
figure. A single asset can differ from the set with `"theme": {"size": "half"}` in its definition.

Plots are written as PNG and SVG (text kept as text), and also as PDF with the `report` preset. 3D views are
raster images captured from the engine's viewer; their title and caption are available as editable SVG text
in the overlay file.

Chinese text needs a CJK font: Noto Sans CJK SC, Microsoft YaHei or PingFang SC. When the installed fonts
cannot draw a label, the asset reports `FONT_MISSING` with the characters concerned instead of drawing
empty boxes; install one of these fonts or set `theme.font_family` to an installed font.

## Compare alternatives

Give `init` several runs and a label for each:

```bash
os-optics-kit present init study/samples/s0001/run study/samples/s0003/run study/samples/s0005/run \
    --labels "Shift -1 mm,Nominal,Shift +1 mm" --out compare --title "Lens position"
os-optics-kit present build compare/presentation.json
```

With several runs, `init` adds:

- one view per run, all framed with the same box (`framing_sources`), so the systems appear at one scale;
- `targets-compare` (plot type `targets`): each target's value per run, with its bounds and assessment;
- `views-compare` (kind `compose`): the views side by side, layout `before-after` for two runs, `row` for more.

A composite figure lists its panels in order, each with an optional label:

```json
{"id": "views-compare", "kind": "compose", "layout": "row",
 "panels": [{"asset": "view-s0001"}, {"asset": "view-s0003", "label": {"en": "Nominal", "zh-CN": "标称"}},
            {"asset": "view-s0005"}]}
```

Layouts: `row`, `column`, `grid`, `before-after` and `system-plot` (a system view beside a result plot, as in
`system-and-result` above). Reorder, relabel or change the layout in `presentation.json` and build again;
for example `--set assets.views-compare.layout=column`. The panels come from the built assets, not from
new runs.

## Animations

`init --animate` adds an orbit of the first run (`orbit-nominal`) and a playback of the first study
(`playback-study`):

```bash
os-optics-kit present init nominal study --out motion --title "Focusing lens" --animate
os-optics-kit present build motion/presentation.json
```

Add other animations to `assets` in `presentation.json`:

| Mode | Source | Motion |
| --- | --- | --- |
| `orbit` | saved run | the camera turns around the system at constant scale; a full turn loops seamlessly |
| `transition` | saved run | the camera moves from one pose to another, e.g. from the whole system to one element |
| `playback` | sweep study | the recorded samples one after another, ordered by the swept parameter |

```json
{"id": "zoom-lens", "kind": "animation", "mode": "transition", "source": "nominal",
 "to": {"camera": [80, 10], "focus": "element:L1"}, "duration_s": 3}
```

A playback shows the saved run and recorded values of each sample: every frame names the parameter value,
the plotted value with its unit and the sample's outcome, and carries the label "Recorded parameter samples".
Each segment shows one saved sample with its recorded values; only camera motion is interpolated.

Each animation writes `<id>.mp4`, `<id>.gif`, a representative still `<id>.png` and `sequence.json` (camera
poses, sample order and timing); a playback also writes `data.json` with the values shown. The defaults are
24 frames per second and both formats; `fps`, `duration_s`, `hold_s` and `formats` change them. MP4 needs the
`media` extra and an even width and height; set `"formats": ["gif"]` to work without it. Captured views are
cached, so a rebuild that changes only text, language or labels re-encodes the frames without capturing
them again.

To replay a sequence with a new theme, copy it next to `presentation.json` and point the asset at it:

```bash
cp motion/assets/orbit-nominal/sequence.json motion/orbit-sequence.json
os-optics-kit present build motion/presentation.json \
    --set assets.orbit-nominal.sequence=orbit-sequence.json --set theme.background=dark
```

## Gallery and bundle

`index.html` opens from the folder without a network connection: an overview of the previews, a full-size
view of each asset with its caption, its files (PNG, SVG, PDF, MP4, GIF, data, captions), its sources and its
settings. Captions switch between English and Chinese when both exist. `os-optics-kit present gallery talk`
rewrites `index.html` and `contact-sheet.png`.

A bundle is a portable copy to hand to a colleague:

```bash
os-optics-kit present bundle talk --out share/focusing-lens.zip --only view-nominal,targets-nominal,system-and-result
os-optics-kit present bundle talk-zh --out share/focusing-lens-zh
```

A `.zip` destination holds one folder named after the archive; any other destination is a directory. The
bundle contains the selected assets (all built assets without `--only`) with their images, vector files,
media, data, captions and provenance, their previews, a filtered `assets.json`, `presentation.json`,
`index.html` and `contact-sheet.png`. Every link is relative, so the recipient extracts it anywhere and
opens `index.html`. The saved runs and studies, other assets and the render cache stay out of the bundle,
and a file that contains an absolute local path is refused (`BUNDLE_PATH_LEAK`). `presentation.json` keeps
the relative source paths: a recipient who places the bundle next to the same runs can rebuild it.

## Concise and technical handoff

The same results give a concise set for a talk and a detailed set for a technical record:

```bash
os-optics-kit present build talk/presentation.json --out talk-report --set theme.preset=report
os-optics-kit report nominal --project project.json --plots talk-report/assets/targets-nominal --out report-nominal
```

| | `presentation` | `report` |
| --- | --- | --- |
| Size | `slide` (1920 × 1080) | `report` (1600 × 1000) |
| Base font | 14 pt | 10 pt |
| Formats | PNG, SVG | PNG, SVG, PDF |
| Caption | separate `caption.<lang>.txt` | inside the figure, also as a file |
| Data file, engine build, catalog release | in `provenance.json` | also printed in the figure |

`provenance.json` records, per asset, the source identities (project, input and result hashes, engine build,
catalog release), the data files with their sha256 and the settings used. `os-optics-kit report` adds
counts, diagnostics and artifact hashes around the selected plots.

## Exit codes and recovery

Exit 0: every asset built. Exit 2 (`INCOMPLETE`): some assets could not be drawn; `missing[]` names each with
its code and a recovery step, and the other assets are built and in the gallery. Exit 4: the command was
refused, with a JSON error on stderr.

| Code | Next step |
| --- | --- |
| `SOURCE_MISSING` | point `sources.<id>.path` at a run directory (holds `manifest.json`) or a study directory (holds `summary.json`), relative to `presentation.json` |
| `FONT_MISSING` | install Noto Sans CJK SC, Microsoft YaHei or PingFang SC, or set `theme.font_family` |
| `BROWSER_NOT_FOUND` | install Chrome or Chromium, or pass `--browser PATH` |
| `MEDIA_EXTRA_MISSING` | `python -m pip install "os-optics-kit[media]"`, or set `"formats": ["gif"]` |
| `OUTPUT_EXISTS` | choose a new `--out`; `bundle --overwrite` replaces an earlier bundle |
| `OUTPUT_INVALID` | build outside the saved runs and studies, on the same drive |

The [workflow recipes](../workflow-recipes.md#presentation-material) list every key and code in one place.
