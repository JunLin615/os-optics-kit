# study: recipe for repeated evaluations

Read this page before a study of more than a few evaluations. The Engine computes each design; you choose
the designs, the batch size, the fidelity and the worker budget. Your own search logic stays yours.

## 1. Discover what is installed

```
os-optics capabilities
os-optics-kit --help
os-optics-kit skills list
```

Select the targets the task needs (`targets` in the spec, `--targets` for candidates). Every run computes
the full result; selecting targets decides the outcome class, not the work done.

## 2. Time a pilot

Run the nominal design and one or two variants, and read the phase timings:

```
os-optics-kit study candidates nominal.json variant.json --out pilot
os-optics-kit study status pilot
```

`samples/<id>/sample.json` `timing.run_s` is the seconds of one evaluation; `timing.phase_seconds`
splits it (`trace`, `result`, `scene`, `write`, ...); `peak_memory_bytes` is the process memory.
Fidelity is the Project's own `compute_budget`; `--ray-data summary` keeps smaller run directories
with the same results.

## 3. Choose the worker budget

A worker process pays about 4 s and 150 MiB once (Engine import), then runs samples at up to the
pilot's speed (measured on a 12-CPU desktop: about 90 % with 2 workers, about two thirds with 4).
Parallel runs pay when samples x seconds per sample is well above that start-up: e.g. 20 samples of
0.2 s stay serial; 400 samples or samples of several seconds gain from 4 workers. `--workers auto`
assumes 0.2 s per sample of a new plan (serial up to 31 samples, at most 4 workers) and uses the
measured `timing.run_s` on `--resume`; when the pilot shows heavier samples, give N yourself.
`os-optics-kit study bench` measures this machine. Keep workers x numerical threads within the CPUs
(workers use one thread each) and leave room for other jobs. Do not start one agent per sample: one
study runs the whole batch.

## 4. Run a batch, inspect, refine

```
os-optics-kit sweep coarse.json --out coarse --workers 4
os-optics-kit study status coarse
```

Read `summary.json` counts and every non-PASS sample (`ERROR`, `UNKNOWN`, `OUT_OF_MODEL` stay in the
table). Plan a finer study around the promising samples (narrower `range`, more samples, a higher
`compute_budget`) in a new directory. A failed or cancelled sample is explicit (`state`); the others
stay usable. After an interruption, `study run DIR --resume` runs only what is missing; a larger
tolerance `--samples` with the same seed keeps the samples already run.

For an external optimiser, hand each independent batch (finite differences, a Sobol or multistart set)
to `os_optics_kit.study`: `make_request` per design, `plan_study`, `execute_study(dir, workers=N)`,
`collect_records(dir)` in plan order, one new directory per batch; keep the dependent steps in your
own loop. Workers import the calling script: in a script, call these under
`if __name__ == "__main__":`.

## 5. Reuse saved results

Presentation never reruns the Engine: `plot sweep DIR --y target:ID`, `report DIR`, `compare RUN DIR`,
`present init RUN DIR --out PRESENT`. `sensitivity` and `tolerance` take `--no-plots --no-report`
to present later (`sweep` and `study candidates` write no plots or report; `study run` follows the
choice made when planning); re-aggregate with `study aggregate DIR [--no-plots] [--no-report]`.
