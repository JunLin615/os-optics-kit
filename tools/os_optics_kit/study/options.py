"""The execution options every study tool shares, and the documents a tool prints when it plans or is cancelled.

CLI (``add_execution_options``): ``--workers N|auto`` (default 1: serial in this process; ``auto`` picks a count from
the samples to run and their measured or assumed run time, see ``resources.suggest_workers``), ``--resume``
(continue a planned or interrupted study in --out),
``--plan-only`` (write the plan and stop; ``os-optics-kit study run`` executes it), ``--time-limit-s S`` and
``--max-memory-mib M`` (per evaluation, enforced at Engine checkpoints), ``--ray-data full|summary`` (requested
outputs: ``summary`` keeps chief rays only).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .. import workflow_common as wc
from . import evaluators, store

#: Exit code of a study that was cancelled (Ctrl-C or the API): the study stays resumable.
CANCELLED_EXIT = 130
EXIT_CODES = {"PLANNED": 0, "CANCELLED": CANCELLED_EXIT}


#: What ``--workers auto`` does, for help texts.
AUTO_HELP = ("auto: from the number of samples to run and their run time (the median of the study's completed "
             "samples on --resume, else 0.2 s assumed), serial unless workers save time, at most 4")


def workers_value(text):
    """``--workers`` value: a positive integer or ``auto``."""
    if str(text).strip().lower() == "auto":
        return "auto"
    try:
        value = int(text)
    except ValueError:
        raise ValueError(f"give a positive integer or auto, not {text!r}") from None
    if value < 1:
        raise ValueError(f"give at least 1 worker, not {value}")
    return value


def workers_argument(text):
    """``workers_value`` for argparse (its message reaches the user as ``argument --workers: ...``)."""
    try:
        return workers_value(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def add_execution_options(parser, *, plan_only=True, outputs=True):
    group = parser.add_argument_group("execution (the samples are independent; results do not depend on these)")
    group.add_argument("--workers", type=workers_argument, default=1, metavar="N|auto",
                       help=f"worker processes (default 1: serial in this process; {AUTO_HELP})")
    group.add_argument("--resume", action="store_true",
                       help="continue a planned or interrupted study: samples whose request is unchanged and that "
                            "completed with the same evaluator are kept, the rest run")
    if plan_only:
        group.add_argument("--plan-only", action="store_true",
                           help="write the plan (study.json, plan.jsonl, requests/) and stop; run it with "
                                "os-optics-kit study run")
    group.add_argument("--time-limit-s", type=float, metavar="S",
                       help="per evaluation: stop a run still going after S seconds (at its next Engine checkpoint)")
    group.add_argument("--max-memory-mib", type=int, metavar="M",
                       help="per evaluation: stop a run whose process memory reaches M MiB")
    if outputs:
        group.add_argument("--ray-data", choices=("full", "summary"), default="full",
                           help="ray data kept in each run directory (summary: chief rays only; results and hashes "
                                "are the same)")
    return group


def execution_arguments(args):
    """Keyword arguments of a study tool's run function from the parsed execution options."""
    limits = {"time_limit_s": args.time_limit_s, "max_memory_mib": args.max_memory_mib}
    out = {"workers": args.workers, "resume": args.resume, "plan_only": getattr(args, "plan_only", False),
           "limits": limits if any(v is not None for v in limits.values()) else None}
    if hasattr(args, "ray_data"):
        out["outputs"] = {"ray_data": args.ray_data}
    return out


def planned_summary(study_dir, kind, extra=None):
    """The document of a study that was planned and not executed (``--plan-only``)."""
    study_dir = Path(study_dir)
    entries = store.read_plan(study_dir)
    study = store.read_study(study_dir)
    return {"status": "PLANNED", "kind": f"{kind}-plan", "samples": len(entries),
            "identity": study["identity"], **(extra or {}),
            "next": f"os-optics-kit study run {study_dir.as_posix()}",
            "paths": {"study_dir": study_dir.as_posix(), "study": store.STUDY_FILE, "plan": store.PLAN_FILE,
                      "requests": store.REQUESTS_DIR}}


def cancelled_summary(study_dir, kind, execution):
    """The document of a cancelled execution (exit 130): what ran, and how to continue."""
    study_dir = Path(study_dir)
    command = f"os-optics-kit study run {study_dir.as_posix()} --resume"
    if execution.evaluator is not None and execution.evaluator != evaluators.DEFAULT_EVALUATOR:
        command += f" --evaluator {execution.evaluator}"
    return {"status": "CANCELLED", "kind": f"{kind}-execution", "execution": execution.as_dict(),
            "next": command,
            "paths": {"study_dir": study_dir.as_posix(), "study": store.STUDY_FILE,
                      "journal": store.JOURNAL_FILE, "samples": store.SAMPLES_DIR}}


def execution_brief(study_dir):
    """``study.json`` ``execution`` in short (for a tool's stdout), or None."""
    if study_dir is None:
        return None
    try:
        execution = wc.read_json(Path(study_dir) / store.STUDY_FILE).get("execution") or {}
    except (OSError, ValueError):
        return None
    counts = execution.get("counts") or {}
    return {key: execution.get(key) for key in ("state", "scheduler", "workers", "blas_threads", "seconds")} | {
        key: counts.get(key) for key in ("ran", "reused", "recovered", "refused")}
