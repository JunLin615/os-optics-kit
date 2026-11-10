---
name: summary
description: Read a saved os-optics run directory into one compact JSON document (targets, assessments, power budget, issues, identity) with an exit code per requirement outcome. Use it after every run before deciding anything.
version: 0.2.0
command: os-optics-kit summary
requires: os-optics>=0.13,<0.14
kind: tool
---

# summary

## When to use

- A run directory exists (`os-optics project run P.json --out-dir RUN`) and you need its outcome in one read.
- You need a scripted pass/fail decision: the exit code follows the selected requirements.

## When not to use

- To change or rerun a design: use `edit` (with `--run`) or `os-optics project run`.
- To compare several runs: use `compare`.
- For rays, wavefronts or exports: use `os-optics results ...` / `os-optics export ...`.

## Inputs

- `RUN_DIR`: a saved run directory (holds `manifest.json`). Never modified; nothing is simulated.
- `--details`: also print the complete power ledger.

## Outputs

One JSON document on stdout: `status` (run status), `requirement_outcome`
(PASS / FAIL / UNKNOWN / OUT_OF_MODEL / NOT_APPLICABLE / ERROR), `outcome.execution`, `targets` (value,
unit, bounds, `assessment` MET / UNMET / INDETERMINATE / NOT_APPLICABLE, quantity state), `observations`,
`power_budget`, `issues`, `model_scope_issues`, `identity` (engine build, catalog release, hashes),
`integrity`, `artifacts`, `view_command`.

Exit: 0 met or no targets declared, 1 an unmet target, 2 indeterminate or a calculation that did not
complete (`PARTIAL`, e.g. `RETURN_ANALYSIS_BUDGET`), 3 a selected target outside its model, 4 invalid input,
broken artifact or failed run (JSON error on stderr).

## Minimal example

```
os-optics project example --kind focus > project.json
os-optics project run project.json --out-dir run
os-optics-kit summary run
```

## Limits

- Reads one run; a run whose files fail the integrity check is refused (`RUN_INTEGRITY_FAILED`).
- A bounded quantity is an interval, not an exact value; read `quantity_state` before ranking values.
- The example Project uses catalog products: it needs an installed catalog release (`os-optics catalog sync`).

## Version

0.2.0 (os-optics-kit 0.2.0), verified with os-optics 0.13.0 and catalog release 2026-10-28.1.
