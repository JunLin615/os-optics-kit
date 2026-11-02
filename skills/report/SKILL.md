---
name: report
description: Assemble a factual Markdown and JSON report from a sweep study or saved runs without rerunning - identity, inputs, edits, target outcomes, counts with denominators, diagnostics, freshness, plots and an artifact list with sha256. Use it to hand results to a person.
version: 0.1.0
command: os-optics-kit report
requires: os-optics>=0.12,<0.13
kind: tool
---

# report

## When to use

- A study or a set of runs is finished and needs a reviewable record with links and hashes.
- An edit's change record should appear next to its results (`--changes`).

## When not to use

- Deciding from numbers in a script: read `summary` / `compare` JSON directly.
- Making plots: run `plot` first, then link them with `--plots`.

## Inputs

- `PATH ...`: one study directory (from `sweep`, `sensitivity` or `tolerance`), or run directories.
- `--project CURRENT.json`: freshness of every run (a historical PASS is labelled `HISTORICAL PASS`).
- `--plots PNG_OR_DIR ...`: plot files or directories to link.
- `--changes NEW.changes.json`: the change record written by `edit`.
- `--out DIR`: default `STUDY_DIR/report`, or `./report` for run directories.

## Outputs

`DIR/report.md` and `DIR/report.json` (links relative to DIR). Stdout: paths, kind, counts, best sample
or no-feasible outcome, diagnostics, numbers of artifacts and plots. Exit 0 written, 4 invalid input.

## Minimal example

```
os-optics project example --kind focus > project.json
os-optics project run project.json --out-dir run
os-optics-kit report run --project project.json --out report
```

For a study: `os-optics-kit report study --project project.json --plots study/plots`.

## Limits

- Numbers are copied from the saved files; nothing is interpreted beyond the computed comparisons.
- From Python, `os_optics_kit.build_report.build_report(..., sections=[...], artifacts=[...])` adds your
  own sections and files.

## Version

0.1.0 (os-optics-kit 0.1.0), verified with os-optics 0.12.0 and catalog release 2026-10-28.1.
