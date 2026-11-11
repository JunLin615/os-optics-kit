# Studies: plan, execute, aggregate, present

[os-optics-kit](../../README.md) · English · [简体中文](../zh-CN/studies.md)

A sweep, a sensitivity or tolerance study and a set of candidate designs are many independent Engine evaluations.
The kit runs them in four steps that each read and write one study directory:

| Step | Writes | Command |
| --- | --- | --- |
| Plan | `study.json`, `plan.jsonl`, `requests/<id>.json` (every sample's Project, before any run) | `sweep` / `sensitivity` / `tolerance` with `--plan-only`, `study candidates ... --plan-only` |
| Execute | `samples/<id>/{project.json, run/, sample.json}`, `journal.jsonl` | `study run STUDY [--workers N]` |
| Aggregate | `table.csv`, `summary.json` (and the tool's own analysis, plots, report) | `study aggregate STUDY` |
| Present | figures, reports, comparisons, presentation material | `plot`, `report`, `compare`, `present` |

Without `--plan-only` a tool runs all steps in one call, through the same functions. Any step can start from the
files of the previous one: execute a plan written earlier, aggregate saved samples, present a saved study. Nothing
after the execution step runs the Engine.

## Run a study with several workers

```bash
os-optics project example --kind focus > project.json
os-optics-kit sweep sweep.json --base project.json --out study --workers 4
```

`--workers 4` starts four worker processes once; each imports the Engine once and then evaluates samples until the
study is done. The results equal the serial run: `table.csv` and `summary.json` list the samples in plan order with
the same values and result hashes; only `seconds`, the `timing` of each record, the worker that ran it and the
execution block of `study.json` differ. `--workers auto` stays serial for small plans and uses workers when the
plan is large enough to repay their start-up.

Each worker uses one numerical-library thread (`OPENBLAS_NUM_THREADS`, `OMP_NUM_THREADS`, `MKL_NUM_THREADS`), so
workers x threads stay within the logical CPUs; more workers than CPUs are refused. `--time-limit-s` and
`--max-memory-mib` bound every evaluation: the Engine's run monitor reads them at its checkpoints (each phase
change and inside long phases) and stops the run there; a worker still busy five seconds after its time limit is
terminated. The serial scheduler has no other preemption.

## Interrupt, inspect and resume

```bash
os-optics-kit sweep sweep.json --base project.json --out study --plan-only
os-optics-kit study run study --workers 2      # Ctrl-C midway: exit 130, unfinished samples NOT_RUN
os-optics-kit study status study               # states, execution, last journal events
os-optics-kit study run study --resume         # runs only the samples without a completed record
```

Every sample record carries a job `state` next to its requirement outcome: `COMPLETED` (the Engine returned a
result or refused the design), `REFUSED` (the planner could not build the sample), `FAILED`, `CANCELLED` (a limit
or a cancellation stopped it) or `NOT_RUN`. A resume keeps every record whose request is unchanged and completed,
reads back a run directory that finished without its record, and runs the rest; `journal.jsonl` records which
samples were reused and which ran. A request is identified by the SHA-256 of its Project, selected targets,
requested outputs and seed: rerunning a tool with `--resume` and a changed spec runs exactly the samples whose
request changed, and a larger tolerance `--samples` with the same seed keeps the samples already run. A study
planned with another Engine build or catalog release is not resumed (`STUDY_IDENTITY_CHANGED` names the field).

## Candidate designs and external search code

```bash
os-optics-kit study candidates a.json b.json c.json --out cand --targets spot_radius --workers 2
os-optics-kit compare cand
```

Each Project file is one sample (`param:candidate` = its position, label `candidate` = its file name). An
optimiser keeps its own sequential steps and hands each independent batch to the executor:

```python
from os_optics_kit.study import collect_records, execute_study, make_request, plan_study, study_header

requests = [make_request(f"s{i:04d}", document, selected_targets=["spot_radius"])
            for i, document in enumerate(batch, start=1)]
plan_study("batch-01", study_header("my-search.batch"), requests)
execute_study("batch-01", workers=4)
values = [record["targets"] for _entry, record in collect_records("batch-01")]
```

The evaluator (one Engine run per request) and the scheduler are parameters of `execute_study`; a caller may
pass its own function `f(request, sample_dir, limits) -> record`. The
[interop-optimization case](../../cases/interop-optimization) runs its 640 Sobol samples this way.

## When parallel runs pay

A worker costs its start-up once (spawn and Engine import) and then runs samples at serial speed. The table is
`python scripts/bench_study.py --workers 1,2,4 --samples 48` on a 12-CPU Windows desktop with os-optics 0.13.0:

| Design (48 samples) | Workers | Command s | Execution s | Samples/s | Speed-up | Worker start-up s | Peak memory MiB | Same results |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| expander, 3 thin lenses (0.21 s/sample) | 1 | 14.5 | 10.2 | 4.7 | 1.00 | - | 158 | yes |
| | 2 | 13.6 | 9.1 | 5.3 | 1.12 | 3.5 | 446 | yes |
| | 4 | 12.1 | 7.9 | 6.1 | 1.30 | 3.8 | 733 | yes |
| relay, 12 thin lenses, 13 planes (0.43 s/sample) | 1 | 25.3 | 20.7 | 2.3 | 1.00 | - | 167 | yes |
| | 2 | 20.1 | 15.6 | 3.1 | 1.33 | 3.4 | 465 | yes |
| | 4 | 15.9 | 11.4 | 4.2 | 1.81 | 3.8 | 769 | yes |

"Command" includes starting Python and importing the Engine in the command's own process (about 4 s); the
speed-up compares execution times. Each worker adds about 3.5 s of start-up and 150 MiB. Studies of a few dozen
sub-second samples gain little; heavier samples and larger studies gain more. Results were identical for every
worker count.

Time a small pilot first (`study candidates` on two designs; `timing.run_s` and `timing.phase_seconds` of each
record), then choose the worker count; `os-optics-kit study bench` measures the machine at hand.
