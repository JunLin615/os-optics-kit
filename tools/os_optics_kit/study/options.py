"""The execution options every study tool shares, and the documents a tool prints when it plans or is cancelled.

CLI (``add_execution_options``): ``--workers N|auto`` (default 1: serial in this process; ``auto`` picks a count from
the plan size, see ``resources.suggest_workers``), ``--resume`` (continue a planned or interrupted study in --out),
``--plan-only`` (write the plan and stop; ``os-optics-kit study run`` executes it), ``--time-limit-s S`` and
``--max-memory-mib M`` (per evaluation, enforced at Engine checkpoints), ``--ray-data full|summary`` (requested
outputs: ``summary`` keeps chief rays only).
"""

from __future__ import annotations

from pathlib import Path

from .. import workflow_common as wc
from . import store

#: Exit code of a study that was cancelled (Ctrl-C or the API): the study stays resumable.
CANCELLED_EXIT = 130
EXIT_CODES = {"PLANNED": 0, "CANCELLED": CANCELLED_EXIT}


def workers_value(text):
    """``--workers`` value: a positive integer or ``auto``."""
    if str(text).strip().lower() == "auto":
        return "auto"
    try:
        value = int(text)
    except ValueError:
        raise ValueError(f"--workers: give a positive integer or auto, not {text!r}") from None
    if value < 1:
        raise ValueError("--workers must be at least 1")
    return value


def add_execution_options(parser, *, plan_only=True, outputs=True):
    group = parser.add_argument_group("execution (the samples are independent; results do not depend on these)")
    group.add_argument("--workers", type=workers_value, default=1, metavar="N|auto",
                       help="worker processes (default 1: serial in this process; auto: from the plan size)")
    group.add_argument("--resume", action="store_true",
                       help="continue the planned or interrupted study in --out: samples whose request is unchanged "
                            "and that completed are kept, the rest run")
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
            "next": f"os-optics-kit study run {study_dir.as_posix()} --workers N",
            "paths": {"study_dir": study_dir.as_posix(), "study": store.STUDY_FILE, "plan": store.PLAN_FILE,
                      "requests": store.REQUESTS_DIR}}


def cancelled_summary(study_dir, kind, execution):
    """The document of a cancelled execution (exit 130): what ran, and how to continue."""
    study_dir = Path(study_dir)
    return {"status": "CANCELLED", "kind": f"{kind}-execution", "execution": execution.as_dict(),
            "next": f"os-optics-kit study run {study_dir.as_posix()} --resume",
            "paths": {"study_dir": study_dir.as_posix(), "study": store.STUDY_FILE,
                      "journal": store.JOURNAL_FILE, "samples": store.SAMPLES_DIR}}


def execution_brief(study_dir):
    """``study.json`` ``execution`` in short (for a tool's stdout), or None."""
    try:
        execution = wc.read_json(Path(study_dir) / store.STUDY_FILE).get("execution") or {}
    except (OSError, ValueError):
        return None
    counts = execution.get("counts") or {}
    return {key: execution.get(key) for key in ("state", "scheduler", "workers", "blas_threads", "seconds")} | {
        key: counts.get(key) for key in ("ran", "reused", "recovered", "refused")}
