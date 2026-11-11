---
name: present
description: Prepare slide-ready material from saved runs and studies - system views, plots, comparisons, animations, an offline gallery and a bundle - and restyle it (language, background, sizes) from a saved configuration without simulating.
version: 0.2.0
command: os-optics-kit present
requires: os-optics>=0.13,<0.14; os-optics-kit[plot]; os-optics-kit[render] for views and animations; os-optics-kit[media] for MP4
kind: tool
---

# present

## When to use

- "Prepare presentation material from these results": system view, result figures, captions, gallery.
- "White background, Chinese labels, larger text": restyle a built set; nothing reruns.
- Compare saved alternatives in one consistent layout: `skills show present compare.md`.
- Hand results over as an offline gallery or bundle: `skills show present handoff.md`.
- Orbit, move to a detail view, playback of recorded study samples: `skills show present animate.md`.

## When not to use

- New results or design changes: `edit`, `sweep`, `os-optics project run` first.
- Numbers for a decision: `summary`, `compare`; a detailed record: `report`.
- One quick plot: `plot --theme ...`; a system view or case image: `os-optics view RUN --no-browser --snapshot
  DIR` (no browser, no `render` extra).

## Inputs

```text
os-optics-kit present init RUN_OR_STUDY... --out DIR [--labels A,B] [--title T] [--animate]
os-optics-kit present build DIR/presentation.json [--set KEY=VALUE]... [--only ID,...] [--out DIR2]
os-optics-kit present list DIR | settings | gallery DIR | bundle DIR --out X.zip [--only ID,...]
```

`init` writes `presentation.json`: theme, sources and default assets (views, power
and target charts, study curves, comparisons). Edit it or use `--set`; `present settings` lists
every key. Common: `theme.language` `en|zh-CN`, `theme.background` `light|dark|transparent`,
`theme.font_scale`, `theme.size` `slide|4k|WxH`, `theme.units` `{"mm": "um"}`, `theme.labels`,
`assets.<id>.caption`, `assets.<id>.camera`.
Put the user's known language, background and size in `theme` from the start.

## Outputs

`DIR/assets/<id>/`: PNG, SVG/PDF for plots, `data.json`, `caption.<lang>.txt`, `provenance.json`;
views add an editable text overlay and an annotated PNG. Also `previews/<id>.png`, `assets.json`
(asset index), `index.html` (offline gallery), `contact-sheet.png` (previews labelled by id).
Stdout: each asset's id, status and paths. `--set` changes are saved in `presentation.json`;
unchanged assets are reused. Exit 0 done; 2 `INCOMPLETE`: `missing[]` names each asset not built
(missing source, target or panel; `FONT_MISSING`, `BROWSER_NOT_FOUND`, `MEDIA_EXTRA_MISSING`) with a
recovery step, and the rest is built; 4 refused specification or setting (JSON on stderr).

## Minimal example

```sh
python -m pip install "os-optics-kit[plot,render]"
os-optics project example --kind focus > project.json
os-optics project run project.json --out-dir run
os-optics-kit present init run --out talk --title "Focusing lens"
os-optics-kit present build talk/presentation.json
os-optics-kit present build talk/presentation.json --set theme.language=zh-CN
```

## Limits

- Pick assets from `contact-sheet.png` by id; write the narrative from `data.json` values.
- 3D views are Chrome/Chromium captures of the engine viewer (raster); plots are also vector.
- Chinese needs a CJK font (Noto Sans CJK SC, Microsoft YaHei or PingFang SC).

## Version

0.2.0 (os-optics-kit 0.3.0): routes plain system views to the engine.
0.1.0: first version, verified with os-optics 0.13.0.
