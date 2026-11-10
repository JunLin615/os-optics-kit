# present: gallery and handoff

Read this page when the user wants to browse results or hand them to someone.

## Gallery

`present build` writes `index.html` and `contact-sheet.png` next to `assets.json`. The gallery opens
offline from the folder: an overview of previews, a full-size view per asset with its caption, every file
of the asset (PNG, SVG, PDF, MP4, GIF, data, captions), its sources (labels, engine build, catalog release,
result hashes) and its redraw settings. Captions switch between English and Chinese when both exist.

`os-optics-kit present gallery DIR` rewrites both pages after you edit captions in `presentation.json`
and rebuild, or after copying the folder.

## Select assets

1. `os-optics-kit present list DIR`: id, kind, status, title, caption, preview and image per asset.
2. Look at `contact-sheet.png`: every preview is labelled with its id.
3. `assets.json` maps an id to its files: `assets[].files.image.path` (full size), `preview`,
   `caption_files`, `provenance`, `files.data.path` (the plotted values).

## Bundle

```sh
os-optics-kit present bundle DIR --out results.zip --only view-final,targets-compare,before-after
os-optics-kit present bundle DIR --out results-folder
```

The bundle holds the selected assets with their captions, data and provenance (a composition
carries its own panel images), a filtered `assets.json`, `presentation.json` with their redraw
settings and those of the assets they are composed from, `index.html` and `contact-sheet.png`.
Links are relative: the recipient extracts it anywhere and opens `index.html` without a network
connection. Saved runs and studies stay out of the bundle; `presentation.json` keeps their relative
paths, so a recipient who also has the runs can rebuild from the same settings.

## Concise and technical handoff

- Presentation: the default `presentation` preset: short titles, captions as separate files.
- Technical: build a second variant with `--out DIR-report --set theme.preset=report`: captions and the
  data file, engine build and catalog release inside each figure, PDF too. Add a full record with
  `os-optics-kit report RUN... --plots DIR-report/assets/<id>`.

## Missing material

A missing source, target or panel gives status `INCOMPLETE` with `missing[]`: asset id, code and the
recovery step. The other assets are built and bundled; tell the user which material is missing and
how to supply it.
