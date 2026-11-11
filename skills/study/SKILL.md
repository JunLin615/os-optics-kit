---
name: study
description: Run independent Engine evaluations as a resumable study, serially or in worker processes; plan, execute, aggregate and present as separate steps. Use it for sweeps, tolerance samples, candidate Projects and batches handed over by your own search code.
version: 0.1.0
command: os-optics-kit study
requires: os-optics>=0.13,<0.14
kind: tool
---

# study

## When to use

- Many independent evaluations: sweep points, tolerance samples, candidate Projects, a multistart or an
  optimiser's independent batch. Run them with `--workers N` when a pilot shows it pays.
- Continue an interrupted or extended study (`--resume`), or aggregate and present saved samples
  without running the Engine again.
- Step-by-step agent recipe (pilot, worker budget, refinement): `skills show study recipe.md`.

## When not to use

- One run: `os-optics project run`. Steps that depend on the previous result (a sequential optimiser)
  stay in your own loop; hand over only the independent batches.

## Inputs

- A planned study directory: `sweep`, `sensitivity` or `tolerance` with `--plan-only`, or
  `study candidates P1.json P2.json ... --out DIR [--targets ID ...]`.
- Python: `os_optics_kit.study` `make_request`, `plan_study`, `study_header`, `execute_study`,
  `collect_records` (evaluator and scheduler are replaceable).

Commands: `study run DIR [--workers N|auto] [--resume] [--time-limit-s S] [--max-memory-mib M]
[--scheduler serial|process] [--evaluator module:function]`; `study status DIR`;
`study aggregate DIR [--no-plots] [--no-report]`; `study bench [--workers 1,2,4] [--samples N]
[--repeat N]` (default counts stop at the CPUs).
The study tools take the same `--workers`, `--resume`, `--plan-only`, `--ray-data full|summary`.

## Outputs

`study.json` (identity: plan hash, Engine build, catalog; execution: scheduler, workers, threads,
limits, state), `plan.jsonl`, `requests/`, `samples/<id>/{project.json, run/, sample.json}`,
`journal.jsonl`, then `table.csv` and `summary.json` in plan order. Each record has `state`
(`COMPLETED`, `REFUSED`, `FAILED`, `CANCELLED`, `NOT_RUN`) next to its outcome, `timing` with the
Engine's phase seconds, `peak_memory_bytes`, `worker`, `evaluator`. Exit codes of the planning tool;
130 cancelled (Ctrl-C or the API; running samples `CANCELLED`, the rest `NOT_RUN`).

## Minimal example

`sweep.json`: see `skills show sweep`.

```
os-optics project example --kind focus > project.json
os-optics-kit sweep sweep.json --out study --plan-only
os-optics-kit study run study --workers 4
os-optics-kit study status study
os-optics-kit study run study --resume          # after Ctrl-C: runs only what is missing
os-optics-kit plot sweep study --y target:spot_radius
```

## Limits

- Results equal the serial run (same hashes and values, plan order); only timing fields differ.
- A worker costs about 4 s and 150 MiB to start. `--workers auto` assumes 0.2 s per sample of a new
  plan (31 samples or fewer stay serial) and the measured median on `--resume`; at most 4.
- Time and memory limits act at Engine checkpoints; a worker busy 5 s past its time limit is
  terminated. Workers use one BLAS thread each.
- `study run` refuses a changed Engine build or catalog release (plan again with `--overwrite`);
  resume reruns a changed request, or one another evaluator produced, and nothing else.
- One execution per study directory: a second one is refused (`STUDY_RUNNING`) while the first runs.

## Version

0.1.0 (os-optics-kit 0.4.0), verified with os-optics 0.13.0 and catalog release 2026-10-28.1.
