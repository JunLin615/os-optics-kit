---
name: study
description: Run independent Engine evaluations as a resumable study, serially or in worker processes; plan, execute, aggregate and present as separate steps. Use it for sweeps, tolerance samples, candidate Projects and batches handed over by your own search code.
version: 0.3.0
command: os-optics-kit study
requires: os-optics>=0.14,<0.15
kind: tool
---

# study

## When to use

- Many independent evaluations: sweep points, tolerance samples, candidate Projects, a multistart or an
  optimiser's independent batch; `--workers N` when a pilot shows it pays.
- Resume a study (`--resume`); aggregate and present saved samples without rerunning the Engine.
- Agent recipe (pilot, worker budget, refinement): `skills show study recipe.md`.

## When not to use

- One run: `os-optics project run`. A sequential optimiser: `optimize`, or your own loop on
  `os_optics_kit.study.evaluation_adapter` (variables to residuals, `docs/en/evaluation-adapter.md`).

## Inputs

- A planned study directory: `sweep`, `sensitivity` or `tolerance` with `--plan-only`, or
  `study candidates P1.json P2.json ... --out DIR [--targets ID ...]`. `zoom --study` writes and runs one
  (`DIR/study`). A compensated `tolerance` plan: run its `next`.
- Python: `os_optics_kit.study` `make_request`, `plan_study`, `study_header`, `execute_study`,
  `collect_records` (replaceable evaluator, scheduler); `evaluation_adapter` (above).

Commands: `study run DIR [--workers N|auto] [--resume] [--time-limit-s S] [--max-memory-mib M]
[--scheduler serial|process] [--evaluator module:function]`; `study status DIR`;
`study aggregate DIR [--no-plots] [--no-report]`; `study bench`.
Study tools share `--workers`, `--resume`, `--plan-only`, `--ray-data full|summary`.

## Outputs

`study.json` (identity, execution), `plan.jsonl`, `requests/`, `samples/<id>/{project.json, run/,
sample.json}`, `journal.jsonl`, then `table.csv` and `summary.json` in plan order. Each record has
`state` (`COMPLETED`, `REFUSED`, `FAILED`, `CANCELLED`, `NOT_RUN`) next to its outcome, `timing`,
`peak_memory_bytes`, `worker`, `evaluator`. Exit codes of the planning tool; 130 cancelled (running
samples `CANCELLED`, the rest `NOT_RUN`).

## Minimal example

`sweep.json`: see `skills show sweep`.

```
os-optics project example --kind focus > project.json
os-optics-kit sweep sweep.json --out study --plan-only
os-optics-kit study run study --workers 4
os-optics-kit study status study
os-optics-kit study run study --resume
os-optics-kit plot sweep study --y target:spot_radius
```

## Limits

- Results equal the serial run (hashes, values, plan order); only timing fields differ.
- A worker costs about 4 s and 150 MiB to start; `--workers auto` (at most 4): see recipe.md.
- Time and memory limits act at Engine checkpoints; a worker busy 5 s past its time limit is
  terminated. Workers use one BLAS thread each.
- `study run` refuses a changed Engine build or catalog release (plan again with `--overwrite`).
  A resume keeps `COMPLETED` / `REFUSED` samples (same request, named evaluator).
- While an execution runs, another one, planning and `study aggregate` are refused (`STUDY_RUNNING`).
- Readers refuse a study with nothing to read (`STUDY_NOT_RUN`, ...; `next` names the command);
  `study aggregate` of a partly executed study exits 2 `INCOMPLETE`.

## Version

0.3.0 (os-optics-kit 0.5.0): evaluation adapter, zoom, compensated tolerance.
0.2.0 (os-optics-kit 0.4.0): readers name the next step for an unexecuted study.
0.1.0 (os-optics-kit 0.4.0), verified with os-optics 0.13.0 and catalog release 2026-10-28.1.
