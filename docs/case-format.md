# Case format

A case is a design that anyone can rerun with the released engine and compare with its recorded outcome. Cases live
in `cases/<name>/` (kebab-case) and ship inside the `os-optics-kit` wheel, so `os-optics-kit cases list`,
`vocabulary`, `show`, `copy` and `verify` work without a clone. The vocabulary is explained for readers in
[the case library](en/case-library.md) ([简体中文](zh-CN/case-library.md)).

## Files

| File | Content |
| --- | --- |
| `project.json` | The Project: a ProjectDocumentV2 (`schema: opensci.project`, `project_schema_version: 2.0`) with its `targets`, in the engine's normalized form: `json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False)` plus a final newline, LF line ends. The `project.json` that `os-optics project run --out-dir RUN` writes into `RUN` has this form. |
| `CASE.md` | The agent-facing description: flat front matter and five sections, at most 700 tokens (ceil(characters / 4)). |
| `README.md`, `README.zh-CN.md` | The illustrated pages for people, English and Simplified Chinese; both are required and link each other. |
| `images/*.png` | The system views and figures, each at most 400 KiB, recorded in `images.json`. |
| `images.json` | The record of the images: options, SHA-256 and the run they were drawn from. |
| `expected.json` | The verified outcome: run status and every target assessment. |
| optional | Case scripts (`workflow.py` or other `*.py`, importing only `os_optics`, `os_optics_kit` and their own pinned requirements), `studies/*.json`, `data/`, `requirements-*.txt`. |

No other file is larger than 1 MiB. Run directories, GLB scenes, CSV dumps and the catalog are never committed, and
no case file holds a machine-local absolute path.

## CASE.md

```text
---
name: ideal-lens-focus                 # the directory name
title: Gaussian focus of an ideal thin lens
summary: one or two sentences, at most 300 characters
class: lens                            # exactly one class
domain: gaussian-beam                  # 1-3 domains, comma-separated, primary first
provenance: self-built                 # where the design comes from
publication: own-work                  # why it may be published
engine: 0.13.0                         # engine version of the verified run
catalog: none                          # catalog release the Project pins, or none
project: project.json
tools: summary                         # os-optics-kit commands the case uses, comma-separated, or none
tags: ideal-lens, focusing, python-api # 1-8 tags from the tag vocabulary
license: MIT
---
```

The front matter is `key: value` lines between two `---` lines: no nesting, no lists, the keys in the order above.
`source` is the one optional key; it follows `publication`. The body has these `## ` sections, in this order:
`What it shows`, `Run`, `Targets`, `Baseline`, `Files`. `templates/CASE.md` and `templates/README.case.md` are the
starting points; their `{{placeholders}}` name the values to fill in.

### Vocabularies

`os-optics-kit cases vocabulary` prints every value below as compact JSON, with its English and Chinese label (and,
for tags, its group), the scale bands with their thresholds and the image budget. Values outside the vocabulary are
refused by `cases index --check`.

| Key | Values |
| --- | --- |
| `class` | `lens` (a coaxial lens or lens group whose conclusion is the lens's own behaviour: focusing, prescription, image quality), `free-space` (a beam path laid out element by element in open space whose conclusion is that the assembled path meets a concrete requirement), `integrated-path` (a path held by custom mechanics whose conclusion is the joint validity of the optical path and its mechanical constraints). Classify by the primary conclusion; a mechanical carrier alone does not change the class. |
| `domain` | `imaging`, `gaussian-beam`, `diffraction`, `dispersion`, `polarization`, `interference`, `quantum-optics`, `nanophotonics`, `beam-shaping`, `wavefront-control`, `beam-steering`, `radiometry` |
| `provenance` | `literature`, `patent`, `manufacturer-file`, `customer-task`, `self-built` |
| `publication` | `own-work` (written for the kit, MIT), `open-license` (third-party material under an open license, attributed), `permission` (the rights holder's written permission), `public-domain` |
| `source` | Citation of the third-party origin, at most 200 characters. |
| `tags` | technique: `sensitivity`, `tolerance`, `parameter-sweep`, `optimization`, `edit-by-id`, `lens-swap`, `design-comparison`, `requirement-check`, `placement-tolerance`, `alignment`, `measurements`, `calibration`, `handoff`, `interop`, `model-validation`, `independent-reference`, `mtf`, `python-api`, `report`; component: `achromat`, `aspheric`, `beam-expander`, `ideal-lens`, `fold-mirror`, `waveplate`, `polarizer`, `beam-splitter`, `detector`, `aperture-stop`, `metasurface`, `tabulated-thin-lens`, `cylindrical-lens`, `phase-mask`; application: `focusing`, `wide-field`, `mid-infrared`, `manufacturability`, `light-sheet`, `flat-top`, `hong-ou-mandel`, `linear-optical-network`, `rcwa`, `full-aperture`; external tool: `scipy`, `optuna`, `salib`, `lightpipes`, `prysm`, `poppy`, `diffractio`, `grcwa`, `perceval`, `piquasso` |

Cross-field rules: `provenance: self-built` goes with `publication: own-work` and no `source`; any other provenance
needs `source` and a publication basis other than `own-work`. Domains and tags are distinct. A tag never repeats a
field: no class, domain, provenance or publication value, no `catalog` or `catalog-free` (the `catalog:` line says
it) and no `none`.

### Scale

Scale is derived from `project.json` by the indexer and never written by hand. `cases.json` stores it as
`{"band", "elements", "sources", "branches"}`:

- elements E: every component whose `optical_role` is not `source`. A catalog `product` counts 1; a component with
  `optical_model_id` counts the leaf models of that model (a `COMPOUND` model counts its members recursively,
  following the `model_id` references inside `optical_models[].model`); `APERTURE_STOP` models count 0; anything else
  counts 1.
- sources S: components with `optical_role: source` (reported, not banded).
- branches B: components with `optical_role: splitter`.

| Band | Rule |
| --- | --- |
| `small` | E ≤ 3 and B = 0 |
| `medium` | E ≤ 12 and B ≤ 2 |
| `large` | E ≤ 40 and B ≤ 8 |
| `very-large` | anything larger |

## Images

A case shows at most 5 images: 1 to 3 system views and at most 3 figures. Each is a PNG of at most 400 KiB.

- A system view is a PNG written by the engine's browser-free snapshot, `os-optics view RUN --no-browser --snapshot
  DIR`, from `RUN = os-optics project run project.json --out-dir RUN` of the case's own `project.json` with default
  options. `--views V` or `--camera A,E` give a snapshot view, `--section ELEMENT|ALL` a section view. Every case has
  at least one snapshot view, and the first image of each README is a snapshot view (3D `iso` preferred; another
  snapshot view or camera only where iso does not read). A `lens` case also has a section view.
- A figure is a plot drawn by a case script (a `.py` file of the case) from the case's own run. Tables are never
  images: a result table is a Markdown table in the README.

No browser and no `render` extra are needed. The kit's `render` and `present` (presentation-grade figures, camera
sets, galleries, animations) need `os-optics-kit[render]` and Chrome or Chromium; they are never needed for case
images.

In a kit checkout, `os-optics-kit cases images` renders and records the system views:

```bash
os-optics-kit cases images ideal-lens-focus
os-optics-kit cases images ideal-lens-focus --view iso --section ALL --frame=scene
os-optics-kit cases images NAME --view iso --view top --size 960x540
os-optics-kit cases images NAME --view iso --section ALL --frame=-6,-6,-4,6,6,168 --section-size 1360x440
os-optics-kit cases images NAME --camera 60,25 --catalog public_optical_catalog.v2.json
os-optics-kit cases images NAME --figure images/mtf.png --script figures.py
```

`cases images NAME` runs the case's `project.json` into a temporary directory (or takes `--run RUN`, which must be a
run of this case: `RUN/project.json` equal to the case's, else `RUN_NOT_FROM_CASE`) and writes
`images/<view>.png` (`images/camera.png`, `images/section.png` or `images/section-<ELEMENT>.png`) and `images.json`.
Without any view option (no `--view`, `--camera`, `--section`, `--size`, `--section-size` or `--frame`) it renders
again exactly the system views recorded in `images.json`, each with its recorded view, size and frame, in recorded
order: this is how to redraw a case's images. Only when nothing is recorded does it use the defaults: `iso`, plus
`--section ALL --frame=scene` for a lens case. A record it cannot render again exactly (an unknown option, or a file
other than the one its view writes) is refused with `IMAGES_RECORD`. With view options it renders the views in the
order given (snapshot views first, the section last; no view given: `iso`, plus `section ALL` for a lens case).
Snapshots default to 960x540 and sections to 1200x420; `--frame` applies to the section and is always passed and
recorded as one token, `--frame=VALUE` (a negative box frame given as a separate argument is refused by argparse on
Python 3.11-3.13). A PNG over 400 KiB is redrawn at 75 % and 50 % of the size, else refused (`IMAGE_TOO_LARGE`).
Every previous system view and its PNG are replaced; figures are kept after the new views. Only recorded files of the
form `images/NAME.png` inside the case are ever overwritten, deleted or hashed, never through a
symbolic link; any other recorded path is left alone, dropped from the record and reported under `skipped`. A
symbolic link at `images/` or at a view file, and an `images.json` that is not a UTF-8 JSON object, are refused before
anything is rendered. A view whose file is recorded as a figure is refused
(`BAD_ARGUMENT`) before anything is rendered. `--catalog FILE` imports a catalog release into a private store first
(catalog cases). `--figure images/X.png --script SCRIPT.py` records or refreshes one figure, appended after the system
views unless it is already recorded; a figure cannot use a file name a view writes (`images/<view>.png`,
`images/camera.png`, `images/section*.png`). Both print one compact JSON document. Other refusals: `IMAGE_BUDGET`,
`IMAGES_RECORD_MISSING` (a figure before any system view: `images.json` missing or recording no system view),
`NOT_A_CHECKOUT`, `ENGINE_FAILED`.

### images.json

```json
{
  "images": [
    {"file": "images/iso.png", "kind": "system-view", "options": ["--views", "iso", "--size", "960x540"],
     "sha256": "...", "view": "iso"},
    {"file": "images/section.png", "kind": "system-view",
     "options": ["--section", "ALL", "--frame=scene", "--size", "1200x420"], "sha256": "...", "view": "section"},
    {"file": "images/mtf.png", "kind": "figure", "script": "figures.py", "sha256": "..."}
  ],
  "project_sha256": "<sha256 of project.json bytes>",
  "run": {"catalog": null, "engine": "0.13.0", "engine_input_hash": "...", "project_hash": "...",
          "scene_content_hash": "..."},
  "schema": "os-optics-kit.case-images",
  "schema_version": 1
}
```

The file is in the engine's normalized form (indent 2, sorted keys, LF, final newline; shown compacted here). `view`
is a snapshot view name (`iso`, `path_horizontal`, `top`, `front`, `side`, `beam_axis`, `plane_xy`, `plane_zx`,
`plane_yz`), `camera` or `section`. `options` are the arguments given to `os-optics view` after
`RUN --no-browser --snapshot DIR`: `--views V`, `--camera A,E` or `--section ELEMENT|ALL`, then `--frame=VALUE`
(sections only, always one token) and `--size WxH`; a value starting with `-` is joined to its option
(`--camera=-30,20`). `file` is the file that view writes, so `cases images NAME` can render the record again. `run`
comes from the run's `manifest.json`: `project.project_hash.digest`,
`engine_run.input_hash.digest`, `engine_run.engine_version`, `optical_view.scene_content_hash_osc1` and
`catalog_version`. The list order is the order in which the READMEs show the images.

### README pair

`README.md` and `README.zh-CN.md` each show exactly the recorded images, each once, in `images.json` order, with
paired captions. An image is shown by image syntax, `![caption](images/X.png)` (a title is allowed) or
`<img src="images/X.png">`; a plain link `[text](images/X.png)`, an HTML comment and a fenced code block show nothing.
Other `README*.md` files may show images only if they exist.

The pair links each other in the first lines after the title: `README.md` carries
`English · [简体中文](README.zh-CN.md)` and `README.zh-CN.md` carries `[English](README.md) · 简体中文` (other links,
such as the agent case sheet `CASE.md`, may follow).

### Checks

`cases index --check` reports, without the engine: `IMAGES_RECORD` (`images.json` missing, not UTF-8 JSON, not
normalized (CRLF line ends included), wrong schema or keys, a record whose file is missing or whose SHA-256 differs,
options that do not match the view or are not in the replayable form above (a frame not given as `--frame=VALUE`, an
unknown or repeated option), a view file other than the one the view writes, a figure script that is not a `.py`
file of the case, `project_sha256` that is not the SHA-256 of `project.json`, `run.engine` or `run.catalog` that
differs from `CASE.md`), `IMAGES` (a file that is not a PNG or is over 400 KiB, a file under `images/` that is not
recorded, the budget, a first image that is not a snapshot view, a lens case without a section view, a README that
does not show exactly the recorded images in order, an image shown that does not exist) and `README_PAIR`
(`README.md` does not link `README.zh-CN.md`, or `README.zh-CN.md` does not link `README.md`; a link inside an HTML
comment or a fenced code block does not count).

## expected.json and the baseline

```json
{
  "catalog": null,
  "engine": "0.13.0",
  "schema": "os-optics-kit.case-expected",
  "schema_version": 1,
  "status": "PASS",
  "targets": {"spot_radius": "MET"}
}
```

`status` is the run status and `targets` maps every target of `project.json` to its assessment (`MET`, `UNMET`,
`INDETERMINATE` or `NOT_APPLICABLE`), both from `os-optics results targets RUN`. `engine` and `catalog` (`null` for a
catalog-free case) equal the `engine` and `catalog` lines of `CASE.md`; a catalog case's components pin exactly that
release. `baseline.json` at the repository root records the engine requirement, the verified engine version and the
catalog release with its SHA-256 for the whole collection.

## Finding cases

```bash
os-optics-kit cases list --class lens
os-optics-kit cases list --domain gaussian-beam --catalog none
os-optics-kit cases list --tag interop --scale medium
os-optics-kit cases vocabulary
```

`cases list` prints `cases.json`, filtered by `--class`, `--domain`, `--provenance`, `--publication`, `--scale`,
`--tag` and `--catalog RELEASE|none`. Each filter takes one value and the filters combine with AND. A value outside
the vocabulary is refused with exit 4 (`BAD_ARGUMENT`, naming the allowed values).

## Verification

```bash
os-optics-kit cases verify ideal-lens-focus
os-optics-kit cases verify --all --out runs --jobs 4
os-optics-kit cases verify beam-expander --catalog public_optical_catalog.v2.json
```

`verify` runs `os-optics project check`, `os-optics project run` and `os-optics results targets` with the installed
engine and compares the run status, every target assessment and the catalog release with `expected.json`. It then
reads the run's `manifest.json`: its `project_hash` and `engine_input_hash` must equal the `run` recorded in
`images.json`, so the images come from the design that verifies. A difference is reported as
`{"field": "images.run.project_hash" | "images.run.engine_input_hash", "expected", "actual"}` and makes the case a
mismatch. `scene_content_hash` is recorded for traceability and not compared (it may differ across operating
systems). `--jobs N` verifies up to N cases at once; the output keeps the given order. `verify` prints compact JSON
and exits 0 when every case matches, 1 on a mismatch and 4 when the request is refused. `--catalog FILE` imports a
catalog release file into a private store under the output directory before running, leaving the default store
untouched.

## Index

`os-optics-kit cases index --check` validates the rules above for every case and checks that `cases.json` is current;
`os-optics-kit cases index --write` regenerates it and refuses while a case has problems. Neither needs the engine.
`cases.json` (`schema: os-optics-kit.cases`, `schema_version: 2`) holds one entry per case with the keys `name`,
`title`, `summary`, `path`, `class`, `domain` (list), `provenance`, `publication`, `source` (only when given),
`scale`, `engine`, `catalog`, `tools`, `tags`, `images` (`{"file", "kind", "view"}` or `{"file", "kind", "script"}`
in `images.json` order), `files` and `project_sha256`.

The case tables of `README.md` and `README.zh-CN.md` sit between `<!-- cases:start -->` and `<!-- cases:end -->` and
are generated from `cases.json`: `python scripts/case_tables.py --write` rewrites them (one table per class, rows by
name, with domain, scale and catalog), `--check` exits 1 when they are stale.

## Catalog snapshot in CI

Catalog cases run on the release named in `baseline.json`. CI fetches that release from the public catalog service
and checks its digest before verifying:

```bash
os-optics catalog sync --release 2026-10-28.1
os-optics catalog status        # active.sha256 must equal catalog.sha256 in baseline.json
os-optics-kit cases verify --all
```

## Updating cases

After a catalog regeneration or an engine minor release:

1. Install the new engine (and `os-optics catalog sync --release VERSION` for a new catalog release).
2. Re-pin catalog cases: `os-optics project update-catalog cases/NAME/project.json --out cases/NAME/project.json`.
   Products that changed keep their old pin; review them, then use `--include-changed`.
3. Rerun and compare: `os-optics-kit cases verify --all --out runs`. Read every difference before accepting it.
4. Record the new baseline: `os-optics-kit cases verify NAME --out runs-new --update-expected` writes the run's
   engine-written `project.json`, `expected.json` and the `engine` / `catalog` lines of `CASE.md` (not the images).
5. Rerender the images with `os-optics-kit cases images NAME` (with no view option it renders the views recorded in
   `images.json` again, with their recorded sizes and frames), redraw the figures with their scripts and re-record
   them, and update the values quoted in `CASE.md` and the READMEs.
6. Regenerate and check: `os-optics-kit cases index --write`, `os-optics-kit cases index --check`,
   `python scripts/case_tables.py --write`.
7. Update `baseline.json` (verified engine version, catalog release and SHA-256).
