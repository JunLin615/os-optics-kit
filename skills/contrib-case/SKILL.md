---
name: contrib-case
description: Turn a finished os-optics design into a PR-ready os-optics-kit case: engine-verified project.json, CASE.md, README with rendered images, expected outcome and a git patch. Use when the user wants to share a design as a case.
version: 0.1.0
command: os-optics-kit contrib case
requires: os-optics>=0.12,<0.13
kind: contribution
---

# contrib-case

## When to use

- The user has a Project (ProjectDocumentV2 JSON with targets) that runs and wants it in the kit's case collection.
- After editing a case bundle's prose: `--refresh BUNDLE` re-verifies it and rebuilds the patch.

## When not to use

- The Project has no targets or its run does not complete: fix the design first (`os-optics project check`,
  `os-optics results targets RUN`).
- A reusable helper script: `contrib-tool`.
- Changing an existing case: edit `cases/NAME` in a kit checkout and open a pull request.

## Inputs

- `PROJECT.json`, `--name` (kebab-case, becomes `cases/NAME`).
- Optional: `--title`, `--summary` (at most 300 characters), `--tools a,b` (kit commands the case uses),
  `--tags x,y`, `--base REF`, `--out BUNDLE`.
- `--repo`: a kit clone (`git clone https://github.com/JunLin615/os-optics-kit`); default: the clone around the
  current directory. Without one, the bundle holds the case files only.
- `--catalog FILE`: a catalog release file when the pinned release is not in the local store.
- `--dry-run`: nothing leaves the device. `--open-pr`: fork, push and open the pull request with the user's `gh` login.

## Outputs

One JSON object on stdout: `status` (`READY`, `PR_OPENED` or `ERROR`), `bundle`, `patch`, `baseline` (engine,
catalog), `verification` (check, run status, execution, target assessments), `warnings`, `next_step`.

The bundle directory:

- `case/`: `project.json` (engine-normalized), `CASE.md`, `README.md`, `expected.json`, `images/iso.png`, `images/top.png`
- `0001-feat-cases-add-NAME.patch`: one signed-off commit (case + `cases.json`); `git am` applies it
- `PR_BODY.md`, `bundle.json`

Exit codes: 0 ready, 1 the run did not complete, 4 refusal (`error.code`, `error.problems`, `error.hint`).

## Minimal example

```sh
os-optics-kit contrib case design.json --name beam-expander-2x --tags beam-expander --dry-run
# edit contrib-case-beam-expander-2x/case/CASE.md (summary, What it shows) and README.md
os-optics-kit contrib case --refresh contrib-case-beam-expander-2x --open-pr
```

`--open-pr` publishes under the user's GitHub account: confirm with the user first.

## Limits

- CASE.md: at most 700 tokens (ceil(characters/4)); sections What it shows, Run, Targets, Baseline, Files.
- At most 4 PNG images of at most 400 KB; run directories, GLB files and bulk outputs are never packaged.
- One catalog release per Project (`os-optics project update-catalog PROJECT.json`).
- `--dry-run` needs the pinned catalog release in the local store, or `--catalog FILE`.

## Version

0.1.0: first version (os-optics-kit 0.1.0).
