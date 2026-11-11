"""Run, inspect and aggregate planned studies; plan a study of independent candidate Projects; benchmark workers.

Usage:
  os-optics-kit study run STUDY_DIR [--workers N|auto] [--resume] [--time-limit-s S] [--max-memory-mib M]
                                    [--scheduler serial|process] [--evaluator module:function]
  os-optics-kit study status STUDY_DIR
  os-optics-kit study aggregate STUDY_DIR [--no-plots] [--no-report]
  os-optics-kit study candidates PROJECT.json [PROJECT.json ...] --out STUDY_DIR [--targets ID ...]
                                 [--overwrite] [--workers N|auto] [--resume] [--plan-only] [--time-limit-s S]
                                 [--max-memory-mib M] [--ray-data full|summary]
  os-optics-kit study bench [--out DIR] [--workers 1,2,4] [--samples N] [--design expander|relay|both]
                            [--repeat N]

A study directory holds a plan (study.json, plan.jsonl, requests/<id>.json: one request per independent Engine
evaluation), the sample records (samples/<id>/sample.json, with project.json and the run directory) and a journal.
"sweep", "sensitivity" and "tolerance" with --plan-only, and "study candidates" with --plan-only, write such a plan.

run        executes the samples that still need a result and aggregates the study as the planning tool would
           (sweep: best sample; sensitivity: finite differences; tolerance: distributions; plots and report as
           chosen when planning). --workers N runs N worker processes (spawned once, each imports the Engine once);
           1 runs serially in this process; auto chooses from the number of samples to run and their run time
           (the median of the study's completed samples with --resume, else 0.2 s assumed): serial unless worker
           processes, which start in about 4 s, save time; at most 4 (with --scheduler serial: one). --resume keeps
           every sample whose record has the plan's request hash, state COMPLETED or REFUSED and was produced by
           the same evaluator, reads back a complete run directory without a record, and runs the rest. With or
           without --resume, the study's Engine build and catalog release must be the installed ones
           (STUDY_IDENTITY_CHANGED names the field; plan it again with its planning command and --overwrite). One
           execution at a time: while the first one's process runs, a second one, a planning command and
           "aggregate" on the same directory are refused (STUDY_RUNNING). --time-limit-s and
           --max-memory-mib apply per evaluation and stop it at the Engine's next checkpoint (state CANCELLED,
           error code RUN_STOPPED, problem code TIME_LIMIT / MEMORY_LIMIT; a worker process still busy 5 s after
           its time limit is terminated and recorded the same way); there is no other preemption. --evaluator
           replaces the Engine call by a function f(request, sample_dir, limits) -> sample record. Ctrl-C (or a
           cancellation through the Python API) cancels the running sample, starts no other, records the rest
           NOT_RUN and exits 130; the printed "next" command (run --resume) continues.
status     plan size, record states, execution block (state RUNNING only while the executing process runs;
           INTERRUPTED when it ended without finishing), the samples a resume would run, last journal events;
           resume_note when the last execution's evaluator had no importable module:function name (a resume
           runs its samples again); never runs the Engine.
aggregate  rewrites table.csv and summary.json (and the planning tool's analysis, plots and report) from the
           saved records; never runs the Engine; refused (STUDY_RUNNING) while an execution of the study runs.
candidates plans (and unless --plan-only runs) one sample per Project file, in the order given: parameter
           "candidate" = 1-based index, label "candidate" = file stem; compare, report and present read the
           result like any study.
bench      times a catalog-free study with each worker count (fresh study directory each): elapsed, throughput,
           worker start-up, first-result latency, peak memory, and whether the results equal the one-worker run.
           The default counts 1,2,4 stop at the logical CPUs; a larger count given with --workers is refused.

Every sample record carries state (COMPLETED, REFUSED, FAILED, CANCELLED, NOT_RUN; distinct from the requirement
outcome), attempts, timing (queued_s, run_s, phase_seconds from the Engine's run monitor), peak_memory_bytes and
worker. Results are written in plan order, so 1 and N workers give the same table.csv and summary.json except
seconds, timing, worker, peak memory and the execution block.

Stdout: one compact JSON document. Exit codes: those of the planning tool (sweep 0/1, sensitivity 0/2, tolerance
0/1), 0 for status, candidates and bench, 4 refused input, 130 cancelled.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from . import workflow_common as wc
from .study import aggregate as agg, evaluators, execute as ex, options as opts, requests as rq, store

PROG = "os-optics-kit study"
CANDIDATE_KIND = "os-optics-kit.candidate-study"
EXIT_CODES = {**wc.WORKFLOW_EXIT_CODES, "DESCRIBED": 0, "BENCHMARKED": 0, "INCOMPLETE": 2, "MISMATCH": 1,
              **opts.EXIT_CODES}
TOP_ROWS = 10


# --------------------------------------------------------------------------------------------- kinds

def _tool(kind):
    """``(aggregate(study_dir, plots, report), compact(summary))`` of a study kind; generic for other kinds."""
    from . import sensitivity_analysis as sa
    from . import sweep_parameters as sp
    from . import tolerance_study as ts

    if kind == sp.KIND:
        return (lambda d, plots=None, report=None: sp.aggregate_sweep(d)), sp.compact
    if kind == sa.KIND:
        return sa.aggregate_sensitivity, sa.compact
    if kind == ts.KIND:
        return ts.aggregate_tolerance, ts.compact
    return (lambda d, plots=None, report=None: agg.aggregate_generic(d)), compact_generic


def aggregate_study(study_dir, *, plots=None, report=None):
    """Aggregate a planned study with its tool's analysis; returns ``(summary, compact)``."""
    study = store.read_study(study_dir)
    aggregate, compact = _tool(study.get("kind"))
    summary = aggregate(study_dir, plots=plots, report=report)
    return summary, compact(summary)


def compact_generic(summary, limit=TOP_ROWS):
    """Bounded stdout document of a study without a tool-specific analysis."""
    labels = [c for c in summary["columns"] if not c.startswith(("param:", "target:")) and c in ("candidate",)]
    keep = ["sample_id", *labels, "outcome", "error_code"] + [
        c for c in summary["columns"] if c.startswith(("param:", "target:")) and c.count(":") == 1]
    rows = [{k: row.get(k) for k in keep if row.get(k) is not None} for row in summary["rows"][:limit]]
    return {"status": summary["status"], "kind": summary["kind"], "counts": summary["counts"],
            "states": summary["states"], "samples_with_warnings": wc.warning_summary(summary["rows"]),
            "targets": summary["targets"], "rows": rows, "rows_omitted": max(0, len(summary["rows"]) - limit),
            "execution": opts.execution_brief((summary.get("paths") or {}).get("study_dir")), "paths": summary["paths"]}


# --------------------------------------------------------------------------------------------- candidates

def plan_candidates(projects, study_dir, *, targets=None, overwrite=False, resume=False, outputs=None):
    """Plan one sample per Project (``[(label, document, file)]`` or paths), in the given order."""
    items = []
    for item in projects:
        if isinstance(item, (str, Path)):
            path = Path(item)
            items.append((path.stem, wc.read_json(path), path.as_posix()))
        else:
            items.append(tuple(item))
    if not items:
        raise wc.WorkflowError("BAD_ARGUMENT", "give at least one Project")
    if targets is not None:
        for label, document, _file in items:
            known = [t.get("target_id") for t in document.get("targets") or []]
            unknown = [t for t in targets if t not in known]
            if unknown:
                raise wc.WorkflowError("TARGET_UNKNOWN", f"candidate {label}: targets {unknown} are not in the "
                                       f"Project; known: {known}", unknown=unknown, known=known, candidate=label)
    ids = rq.sample_ids(len(items))
    requests = [rq.make_request(sample_id, document, selected_targets=targets, outputs=outputs,
                                labels={"role": "candidate", "candidate": label,
                                        "parameters": {"candidate": {"value": index, "unit": ""}}})
                for index, (sample_id, (label, document, _file)) in enumerate(zip(ids, items), start=1)]
    study_dir, resuming = store.prepare_study_dir(study_dir, overwrite=overwrite, resume=resume,
                                                  files=("table.csv", "summary.json"), dirs=("plots", "report"))
    candidates = [{"sample_id": sample_id, "index": index, "label": label, "file": file,
                   "document_sha256": wc.canonical_digest(document)}
                  for index, (sample_id, (label, document, file)) in enumerate(zip(ids, items), start=1)]
    header = store.study_header(
        CANDIDATE_KIND, spec={"projects": [c["file"] for c in candidates], "targets": targets},
        normalized={"parameters": [{"name": "candidate", "unit": "", "values": list(range(1, len(items) + 1))}],
                    "targets": targets},
        parameters=[{"name": "candidate", "unit": ""}], row_labels=["candidate"], candidates=candidates,
        base_project=None, seed=None, sampling="candidate list",
        budget={"max_evaluations": len(items), "samples": len(items)}, stop_on=[])
    store.plan_study(study_dir, header, requests, resume=resuming)
    return study_dir


def run_candidates(projects, study_dir, *, targets=None, overwrite=False, workers=1, scheduler=None,
                   evaluator=None, limits=None, resume=False, plan_only=False, outputs=None, on_progress=None):
    """Plan, execute and aggregate a candidate study; returns the summary (or the PLANNED / CANCELLED document)."""
    study_dir = plan_candidates(projects, study_dir, targets=targets, overwrite=overwrite, resume=resume,
                                outputs=outputs)
    if plan_only:
        return opts.planned_summary(study_dir, CANDIDATE_KIND)
    execution = ex.execute_study(study_dir, workers=workers, scheduler=scheduler, evaluator=evaluator, limits=limits,
                                 resume=resume, on_progress=on_progress)
    if execution.state == "CANCELLED":
        return opts.cancelled_summary(study_dir, CANDIDATE_KIND, execution)
    return agg.aggregate_generic(study_dir)


# --------------------------------------------------------------------------------------------- status

def status(study_dir, events=8):
    """Plan size, record states, execution block, last journal events (files only)."""
    study_dir = Path(study_dir)
    study = store.read_study(study_dir)
    entries = store.read_plan(study_dir)
    states, outcomes, missing = {}, {}, []
    for entry in entries:
        record = store.read_record(study_dir, entry["sample_id"])
        if record is None or record.get("request_hash") != entry["request_hash"]:
            missing.append(entry["sample_id"])
            continue
        states[record.get("state")] = states.get(record.get("state"), 0) + 1
        outcomes[record.get("outcome")] = outcomes.get(record.get("outcome"), 0) + 1
    last = study.get("execution") or {}
    # the evaluator a resume is predicted for: the last execution's (None when it had no importable name)
    evaluator = last.get("evaluator") if "evaluator" in last else evaluators.DEFAULT_EVALUATOR
    rerun = [e["sample_id"] for e in entries if e["sample_id"] not in missing
             and store.reusable_record(study_dir, e, evaluator) is None]
    journal = store.read_journal(study_dir)
    summary_path = study_dir / "summary.json"
    execution = dict(study.get("execution") or {})
    owner = store.read_owner(study_dir)
    running = owner is not None and store.owner_alive(owner)
    if execution.get("state") == "RUNNING" and not running:
        execution["state"] = "INTERRUPTED"  # its process ended without finishing: resume it with run --resume
    described = {"status": "DESCRIBED", "kind": study.get("kind"), "samples": len(entries),
                 "execution": execution, "owner": owner if running else None, "states": states, "outcomes": outcomes,
                 "without_record": wc.bounded(missing, TOP_ROWS),
                 "to_run_on_resume": wc.bounded(missing + rerun, TOP_ROWS),
                 "identity": study["identity"], "aggregated": summary_path.is_file(),
                 "journal": journal[-events:], "paths": {"study_dir": study_dir.as_posix(), "study": store.STUDY_FILE,
                                                         "plan": store.PLAN_FILE, "journal": store.JOURNAL_FILE}}
    if "evaluator" in last and evaluator is None:
        described["resume_note"] = ("the last execution's evaluator has no importable module:function name: a "
                                    "resume keeps none of its records and runs those samples again (a module-level "
                                    "function of an importable module has a name, and a resume keeps its records)")
    return described


# --------------------------------------------------------------------------------------------- CLI

def _parser():
    parser = wc.ArgumentParser(prog=PROG, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True, metavar="{run,status,aggregate,candidates,bench}",
                                     parser_class=wc.ArgumentParser)
    run = commands.add_parser("run", help="execute the samples of a planned study that still need a result, then "
                                          "aggregate it")
    run.add_argument("study_dir", type=Path, metavar="STUDY_DIR")
    opts.add_execution_options(run, plan_only=False, outputs=False)
    run.add_argument("--scheduler", choices=("serial", "process"),
                     help="serial (this process) or process (worker pool); default: serial for one worker; serial "
                          "with --workers auto runs serially")
    run.add_argument("--evaluator", metavar="module:function",
                     help="evaluate each request with this function instead of one Engine run (--resume keeps only "
                          "the samples this evaluator produced)")
    show = commands.add_parser("status", help="plan, record states, execution and journal of a study (no Engine run)")
    show.add_argument("study_dir", type=Path, metavar="STUDY_DIR")
    again = commands.add_parser("aggregate", help="table, summary and the tool's analysis from saved records "
                                                  "(no Engine run)")
    again.add_argument("study_dir", type=Path, metavar="STUDY_DIR")
    again.add_argument("--no-plots", action="store_true", help="do not write the planning tool's plots")
    again.add_argument("--no-report", action="store_true", help="do not write the planning tool's report")
    cands = commands.add_parser("candidates", help="plan and run one sample per Project file")
    cands.add_argument("projects", type=Path, nargs="+", metavar="PROJECT.json")
    cands.add_argument("--out", type=Path, required=True, help="study directory to create")
    cands.add_argument("--targets", nargs="+", metavar="ID", help="target ids deciding each outcome (default all)")
    cands.add_argument("--overwrite", action="store_true", help="replace the files this command writes in --out")
    opts.add_execution_options(cands)
    bench = commands.add_parser("bench", help="time a catalog-free study with several worker counts")
    bench.add_argument("--out", type=Path, help="directory for the benchmark studies and results (default: a "
                                                "temporary directory, removed afterwards)")
    bench.add_argument("--workers", help="comma-separated worker counts (default 1,2,4 up to the logical CPUs)")
    bench.add_argument("--samples", type=int, default=24, help="samples per study (default 24)")
    bench.add_argument("--design", choices=("expander", "relay", "both"), default="both",
                       help="expander: three thin lenses; relay: a 12-lens relay with 13 planes (heavier)")
    bench.add_argument("--repeat", type=int, default=1, help="runs per worker count (median reported)")
    return parser


def main(argv=None):
    def workflow():
        args = _parser().parse_args(argv)
        if args.command == "status":
            return status(args.study_dir)
        if args.command == "aggregate":
            _summary, compact = aggregate_study(args.study_dir, plots=False if args.no_plots else None,
                                                report=False if args.no_report else None)
            return compact
        if args.command == "bench":
            from .study import bench

            counts = None
            if args.workers is not None:
                counts = [bench.count_value(item) for item in str(args.workers).split(",") if item.strip()]
            return bench.run_benchmark(args.out, workers=counts, samples=args.samples, designs=args.design,
                                       repeat=args.repeat)
        if args.command == "candidates":
            execution = opts.execution_arguments(args)
            summary = run_candidates(args.projects, args.out, targets=args.targets, overwrite=args.overwrite,
                                     **execution)
            return summary if summary["status"] in opts.EXIT_CODES else compact_generic(summary)
        execution = opts.execution_arguments(args)
        result = ex.execute_study(args.study_dir, workers=execution["workers"], scheduler=args.scheduler,
                                  evaluator=args.evaluator, limits=execution["limits"], resume=execution["resume"])
        kind = store.read_study(args.study_dir).get("kind")
        if result.state == "CANCELLED":
            return opts.cancelled_summary(args.study_dir, kind, result)
        _summary, compact = aggregate_study(args.study_dir)
        return compact

    return wc.emit(workflow, EXIT_CODES)


if __name__ == "__main__":
    raise SystemExit(main())
