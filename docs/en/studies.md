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

`sweep.json` is a sweep spec over that Project: `os-optics-kit skills show sweep` prints one, and
[Sweep the lens spacing](workflows.md#2-sweep-the-lens-spacing) explains its keys.

`--workers 4` starts four worker processes once; each imports the Engine once and then evaluates samples until the
study is done. The results equal the serial run: `table.csv` and `summary.json` list the samples in plan order with
the same values and result hashes; only `seconds`, the `timing` of each record, the worker that ran it and the
execution block of `study.json` differ. `--workers auto` estimates the elapsed time from the number of samples to
run and their run time: with `--resume`, the median `timing.run_s` of the study's completed samples; otherwise
0.2 s per sample. It stays serial unless worker processes (about 4 s of start-up each) save at least 10 %, and
uses at most 4 workers; a new plan of 31 or fewer samples therefore runs serially. With `--scheduler serial`,
`--workers auto` runs serially. When a pilot shows heavier samples, give the worker count yourself.

Each worker uses one numerical-library thread (`OPENBLAS_NUM_THREADS`, `OMP_NUM_THREADS`, `MKL_NUM_THREADS`), so
workers x threads stay within the logical CPUs; more workers than CPUs are refused. Before the workers start, the
command obtains the catalog release each Project pins, so a study that runs serially also runs with workers.
`--time-limit-s` and `--max-memory-mib` bound every evaluation: the Engine's run monitor reads them at its
checkpoints (each phase change and inside long phases) and stops the run there; a worker still busy five seconds
after its time limit is terminated. Either way the sample is recorded `CANCELLED` with error code `RUN_STOPPED` and
the cause (`TIME_LIMIT`, `MEMORY_LIMIT`) as problem code. The serial scheduler has no other preemption.

## Interrupt, inspect and resume

```bash
os-optics-kit sweep sweep.json --base project.json --out study --plan-only
os-optics-kit study run study --workers 2      # Ctrl-C midway: exit 130, running sample CANCELLED, rest NOT_RUN
os-optics-kit study status study               # states, execution, last journal events
os-optics-kit study run study --resume         # runs only the samples without a completed record
```

Ctrl-C stops the whole study with any number of workers: the running samples are cancelled, no other sample
starts, `study.json` `execution.state` becomes `CANCELLED` and the command exits 130 with a `next` command that
resumes it. One execution owns a study directory at a time (`owner.json`, removed when it ends; `owner.lock`,
which stays, lets one process at a time take ownership): while the first one's process runs, a second `study run`,
a planning command and `study aggregate` on that directory are refused with `STUDY_RUNNING`, and `study status`
reports `RUNNING` only while that process runs (`INTERRUPTED` once it ended without finishing; a resume takes the
study over).

Every sample record carries a job `state` next to its requirement outcome: `COMPLETED` (the Engine returned a
result or refused the design), `REFUSED` (the planner could not build the sample), `FAILED`, `CANCELLED` (a limit
or a cancellation stopped it) or `NOT_RUN`, and the `evaluator` that produced it. A resume keeps every record whose
request is unchanged, that completed and that the same evaluator produced; it reads back a run directory that
finished without its record, and runs the rest. `journal.jsonl` records which samples were reused and which ran.
An evaluator is identified by its `module:function` name. A function passed from Python that has no such name (a
lambda, a nested function) has no identity: a resume runs its samples again, and `study status` says so in
`resume_note`.
A request is identified by the SHA-256 of its Project, selected targets, requested outputs and seed: rerunning a
tool with `--resume` and a changed spec runs exactly the samples whose request changed (a kept record takes the
labels of the new plan), and a larger tolerance `--samples` with the same seed keeps the samples already run.
`study run`, with or without `--resume`, refuses a study planned with another Engine build or catalog release
(`STUDY_IDENTITY_CHANGED` names the field): plan it again with its planning command and `--overwrite`.

## Candidate designs and external search code

```bash
os-optics-kit study candidates a.json b.json c.json --out cand --targets spot_radius --workers 2
os-optics-kit compare cand
```

Each Project file is one sample (`param:candidate` = its position, label `candidate` = its file name without
`.json`). An optimiser keeps its own sequential steps and hands each independent batch to the executor. This
script evaluates the Projects `a.json` and `b.json` in two worker processes and prints each sample's outcome
and target values:

```python
import json
from pathlib import Path

from os_optics_kit.study import collect_records, execute_study, make_request, plan_study, study_header


def evaluate(documents, study_dir, workers=2):
    requests = [make_request(f"s{i:04d}", document, selected_targets=["spot_radius"])
                for i, document in enumerate(documents, start=1)]
    plan_study(study_dir, study_header("my-search.batch"), requests)
    execute_study(study_dir, workers=workers)
    return collect_records(study_dir)  # [(plan entry, record)] in plan order


if __name__ == "__main__":
    batch = [json.loads(Path(name).read_text(encoding="utf-8")) for name in ("a.json", "b.json")]
    for entry, record in evaluate(batch, "batch-01"):
        print(entry["sample_id"], record["outcome"], {t["id"]: t["value"] for t in record["targets"]})
```

With more than one worker the executor starts worker processes that import the calling script: keep the calls
under `if __name__ == "__main__":`. Give each batch a new study directory: `execute_study` refuses a directory
whose samples already ran (`STUDY_STARTED`) unless it is called with `resume=True`. The evaluator (one Engine
run per request) and the scheduler are parameters of `execute_study`; a caller may pass its own function
`f(request, sample_dir, limits) -> record`. A scheduler object that was cancelled (`cancel()`, or Ctrl-C during
its run) runs nothing until its `reset()`: resume with a new scheduler, or with the same one after `reset()`.

## When parallel runs pay

A worker costs its start-up once (spawn and Engine import). The table is one run per worker count of
`python scripts/bench_study.py --workers 1,2,4 --samples 48` on a Windows desktop with 12 logical CPUs and
os-optics 0.13.0:

| Design (48 samples) | Workers | Command s | Execution s | Samples/s | Speed-up | Worker start-up s | Peak memory MiB | Same results |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| expander, 3 thin lenses (0.20 s/sample) | 1 | 13.63 | 9.56 | 5.02 | 1.0 | - | 157 | yes |
| | 2 | 12.58 | 8.59 | 5.59 | 1.11 | 3.19-3.21 | 443 | yes |
| | 4 | 11.12 | 7.16 | 6.71 | 1.34 | 3.41-3.52 | 731 | yes |
| relay, 12 thin lenses, 13 planes (0.43 s/sample) | 1 | 24.83 | 20.45 | 2.35 | 1.0 | - | 168 | yes |
| | 2 | 19.43 | 15.05 | 3.19 | 1.36 | 3.31-3.40 | 465 | yes |
| | 4 | 15.15 | 10.89 | 4.41 | 1.88 | 3.46-3.57 | 768 | yes |

"Command" includes starting Python and importing the Engine in the command's own process (about 4 s); the
speed-up compares execution times. Each worker adds 3.2-3.6 s of start-up and 143-150 MiB. After start-up,
each of two workers ran samples at about 90 % of the serial per-sample speed, and each of four at about two
thirds. Studies of a few dozen sub-second samples gain little; heavier samples and larger studies gain more.
Results were identical for every worker count.

Time a small pilot first (`study candidates` on two designs; `timing.run_s` and `timing.phase_seconds` of each
record), then choose the worker count; `os-optics-kit study bench` measures the machine at hand (worker counts
1, 2 and 4, up to its logical CPUs).
