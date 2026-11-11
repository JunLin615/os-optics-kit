---
name: contrib-case
description: Turn a finished os-optics design into a PR-ready kit case: verified project.json, classified CASE.md, English and Chinese READMEs with engine system views, expected outcome and a git patch. Use to share a design as a case.
version: 0.3.0
command: os-optics-kit contrib case
requires: os-optics>=0.13,<0.14
kind: contribution
---

# contrib-case

## When to use

- A Project (ProjectDocumentV2 with targets) runs and the user wants it in the kit's case collection.
- After editing a case bundle's prose: `--refresh BUNDLE` re-verifies it and rebuilds the patch.

## When not to use

- The Project has no targets or its run does not complete: fix the design first (`os-optics project check`,
  `os-optics results targets RUN`).
- A reusable helper script: `contrib-tool`.
- Changing an existing case: edit `cases/NAME` in a kit checkout, `os-optics-kit cases images NAME`,
  `cases index --write`, `python scripts/case_tables.py --write`, then a pull request.

## Inputs

- `PROJECT.json`, `--name` (kebab-case, becomes `cases/NAME`), `--class` (`lens`, `free-space`,
  `integrated-path`), `--domain a,b` (1-3, primary first), `--tags x,y` (1-8).
- Optional: `--title`, `--summary` (at most 300 characters), `--tools a,b` (kit commands used),
  `--provenance` (default `self-built`), `--publication` (default `own-work`), `--source` (required for
  third-party material), `--base REF`, `--out BUNDLE`. Values: `os-optics-kit cases vocabulary`.
- `--repo`: a kit clone (default: the one around the current directory; none: the case files only).
- `--catalog FILE`: a release file when the pinned catalog is not installed.
- `--dry-run`: nothing leaves the device. `--open-pr`: opens the pull request with the user's `gh` login.

## Outputs

One JSON object: `status` (`READY`, `PR_OPENED`, `INCOMPLETE`, `ERROR`), `bundle`, `patch`, `baseline`,
`verification`, `warnings`, `next_step`. The bundle directory:

- `case/`: `project.json` (engine-normalized), `CASE.md`, both READMEs, `expected.json`, `images.json`,
  `images/` (iso, plus section for a lens)
- `0001-feat-cases-add-NAME.patch`: one signed-off commit (case, `cases.json`, README case tables)
- `PR_BODY.md`, `bundle.json`

Exit 0 ready, 1 run not completed or a view not rendered (`INCOMPLETE`), 4 refusal (`error`).

## Minimal example

```sh
os-optics-kit contrib case design.json --name beam-expander-2x --class free-space --domain gaussian-beam \
  --tags beam-expander --dry-run
# edit case/CASE.md (summary, What it shows) and both READMEs
os-optics-kit contrib case --refresh contrib-case-beam-expander-2x --open-pr
```

`--open-pr` publishes under the user's GitHub account: confirm with the user first.

## Limits

- Case images need no browser and no `render` extra (kit `render`/`present` need Chrome or Chromium): the
  engine renders the system views (`os-optics view RUN --no-browser --snapshot DIR --views iso`,
  `--section ALL --frame=scene` for a lens). On `IMAGE_NOT_RENDERED`, that command shows the error; fix it, then
  `--refresh` renders the missing views.
- At most 5 PNG images of at most 400 KiB each, a system view first; result tables stay Markdown. No run
  directories or GLB files.
- Both READMEs keep their language links under the title.
- CASE.md: at most 700 tokens; sections What it shows, Run, Targets, Baseline, Files.
- One catalog release per Project (`os-optics project update-catalog PROJECT.json`).

## Version

0.3.0 (os-optics-kit 0.3.0): classification, Chinese README, browser-free system views.
0.2.0 (os-optics-kit 0.2.0): for os-optics 0.13.
