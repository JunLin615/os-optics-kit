---
name: run-case
description: Find an engine-reproducible OS Optics case, copy it, run it, check it against its recorded outcome and adapt it. Use when a worked design close to the user's question exists or a known-good starting Project is needed.
version: 0.2.0
command: os-optics-kit cases
requires: os-optics>=0.13,<0.14
kind: guide
---

# run-case

## When to use

- The user's design resembles a listed case (focusing, beam expansion, polarization, alignment, detector
  calibration, external optimization, diffraction, quantum-optics export, metasurface model).
- A known-good Project is needed as a starting point, or the installed engine must be checked against recorded runs.

## When not to use

- Building a design from scratch with no similar case: start from `os-optics project example` instead.
- Changing a case's recorded outcome to match a different engine result without a rerun.

## Inputs

- A case name from `os-optics-kit cases list` (compact JSON: name, title, summary, engine, catalog, tools, tags).
- A destination directory for the copy.
- For catalog cases, the catalog release named in the case (`os-optics catalog status`).

## Outputs

- `DEST/NAME/`: project.json, CASE.md, README.md, expected.json, images and any case scripts or data.
- `cases verify`: compact JSON per case with `result` MATCH, MISMATCH or ERROR and the differing fields;
  exit 0 when all match, 1 on a mismatch, 4 on refusal.

## Minimal example

```bash
os-optics-kit cases list --tag catalog-free
os-optics-kit cases show ideal-lens-focus          # read What it shows, Run, Targets first
os-optics-kit cases copy ideal-lens-focus --dest .
cd ideal-lens-focus
os-optics project check project.json
os-optics project run project.json --out-dir run
os-optics results targets run                      # compare status and assessments with expected.json
os-optics-kit cases verify ideal-lens-focus        # the same check, automated
```

Adapt: edit the copy by ID (`os-optics-kit edit project.json --edits ops.json --out mine.json --run mine-run`),
rerun, and read the new assessments; the case's expected.json no longer applies to the edited design.

## Limits

- Cases record one engine version and one catalog release (`engine`, `catalog` in CASE.md). Another catalog
  release may change catalog cases; `os-optics project update-catalog` re-pins a Project to the active release.
- Interop cases need the pinned third-party packages in their `requirements-*.txt`, in a separate environment.
- Case scripts write into the directory given by `--out`; choose a new one per run.

## Version

0.2.0 (os-optics-kit 0.2.0): cases verified with os-optics 0.13.0, catalog 2026-10-28.1.
