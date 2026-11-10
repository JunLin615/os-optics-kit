# Case format

A case is a design that anyone can rerun with the released engine and compare with its recorded outcome. Cases live
in `cases/<name>/` (kebab-case) and ship inside the `os-optics-kit` wheel, so `os-optics-kit cases list`, `show`,
`copy` and `verify` work without a clone.

## Files

| File | Content |
| --- | --- |
| `project.json` | The Project: a ProjectDocumentV2 (`schema: opensci.project`, `project_schema_version: 2.0`) with its `targets`, in the engine's normalized form: `json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False)` plus a final newline, LF line ends. The `project.json` that `os-optics project run --out-dir RUN` writes into `RUN` has this form. |
| `CASE.md` | The agent-facing description: flat front matter and five sections, at most 700 tokens (ceil(characters / 4)). |
| `README.md` | The illustrated page for people; `README.zh-CN.md` is its Simplified Chinese version. |
| `images/*.png` | At most 4 images, each at most 400 KB, rendered by the engine from the case's own run and shown in the README. |
| `expected.json` | The verified outcome: run status and every target assessment. |
| optional | Case scripts (`workflow.py` or other `*.py`, importing only `os_optics`, `os_optics_kit` and their own pinned requirements), `studies/*.json`, `data/`, `requirements-*.txt`. |

No file is larger than 1 MB. Run directories, GLB scenes, CSV dumps and the catalog are never committed, and no case
file holds a machine-local absolute path.

## CASE.md

```text
---
name: ideal-lens-focus                 # the directory name
title: Gaussian focus of an ideal thin lens
summary: one or two sentences, at most 300 characters
engine: 0.13.0                         # engine version of the verified run
catalog: none                          # catalog release the Project pins, or none
project: project.json
tools: summary                         # os-optics-kit commands the case uses, comma-separated, or none
tags: catalog-free, focusing           # kebab-case, comma-separated
license: MIT
---
```

The front matter is `key: value` lines between two `---` lines: no nesting, no lists. The body has these `## `
sections, in this order: `What it shows`, `Run`, `Targets`, `Baseline`, `Files`. `templates/CASE.md` and
`templates/README.case.md` are the starting points; their `{{placeholders}}` name the values to fill in.

## Images

Render the images from a run of the case's `project.json`:

```bash
os-optics project run project.json --out-dir run
os-optics view run --no-browser --snapshot shots --views iso,top --size 960x540
os-optics view run --no-browser --snapshot shots --section ALL --frame scene --size 1200x420
```

Copy the chosen PNG files to `images/` and show each one in the README.

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

## Verification

```bash
os-optics-kit cases verify ideal-lens-focus
os-optics-kit cases verify --all --out runs
os-optics-kit cases verify beam-expander --catalog public_optical_catalog.v2.json
```

`verify` runs `os-optics project check`, `os-optics project run` and `os-optics results targets` with the installed
engine and compares the run status, every target assessment and the catalog release with `expected.json`. It prints
compact JSON and exits 0 when every case matches, 1 on a mismatch and 4 when the request is refused. `--catalog FILE`
imports a catalog release file into a private store under the output directory before running, leaving the default
store untouched.

`os-optics-kit cases index --check` validates the format rules above for every case and checks that `cases.json` is
current; `os-optics-kit cases index --write` regenerates `cases.json` (name, title, summary, path, engine, catalog,
tools, tags, files and the SHA-256 of `project.json`). Neither needs the engine.

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
   engine-written `project.json`, `expected.json` and the `engine` / `catalog` lines of `CASE.md`.
5. Update the values quoted in `CASE.md` and the READMEs, and rerender the images.
6. Regenerate and check the index: `os-optics-kit cases index --write`, then `os-optics-kit cases index --check`.
7. Update `baseline.json` (verified engine version, catalog release and SHA-256).
