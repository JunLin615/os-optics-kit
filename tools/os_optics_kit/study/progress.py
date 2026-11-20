"""How far a saved study got, for every command that reads it: planned only, executed in part, executed, aggregated.

The readers of a saved study (``study aggregate``, ``plot sweep``, ``report``, ``compare``, ``present``) ask
``reading_error`` (or ``require_results``) before they read its tables, so a study that was planned and never
executed, or executed in part, is refused with one error that names the command which continues it:

* ``STUDY_NOT_RUN``: no planned sample has a result (``--plan-only``, or an execution cancelled before its first
  sample); next ``os-optics-kit study run DIR`` (with ``--resume`` once a sample directory holds a record);
* ``STUDY_INCOMPLETE``: some planned samples have no result (an execution was killed or cancelled, or the plan
  changed since) and no table was aggregated since; next ``os-optics-kit study run DIR --resume``, or
  ``os-optics-kit study aggregate DIR`` for a table of what ran (the other samples are ``NOT_RUN`` rows, and the
  readers accept that table until the plan changes or another execution starts);
* ``STUDY_NOT_AGGREGATED``: every planned sample has a result but there is no ``summary.json``; next
  ``os-optics-kit study aggregate DIR`` (no Engine run);
* ``STUDY_RUNNING`` instead of the above while an execution of the study runs.

``study aggregate`` refuses only ``STUDY_NOT_RUN``: it aggregates a study executed in part, and its stdout then has
status ``INCOMPLETE`` (exit 2) with the samples without a result and the ``next`` command.

A sample has a result when its ``sample.json`` carries the plan's request hash and was not left by a cancellation of
the study (state ``NOT_RUN`` with error code ``CANCELLED``, or ``RUN_STOPPED`` with problem ``STUDY_CANCELLED`` /
``INTERRUPTED``); a ``FAILED`` evaluation, a time or memory limit, a planner refusal and a ``stop_on`` stop are
results. A directory without a planned study (no ``plan.jsonl`` and ``study.json`` identity, e.g. a study written by
an earlier kit) is left to the reader's own checks.
"""

from __future__ import annotations

from pathlib import Path

from .. import workflow_common as wc
from . import evaluators, store
from .execute import cancelled_by_study

NOT_RUN, PARTIAL, EXECUTED = "NOT_RUN", "PARTIAL", "EXECUTED"
SUMMARY_FILE = "summary.json"
#: Sample ids listed in an error or a status document.
LISTED = 10


def run_command(study_dir, *, resume, evaluator=None):
    """The ``study run`` command that executes (``resume``: continues) the study in ``study_dir``; a named evaluator
    other than the default is passed on."""
    command = f"os-optics-kit study run {Path(study_dir).as_posix()}"
    if resume:
        command += " --resume"
    if evaluator is not None and evaluator != evaluators.DEFAULT_EVALUATOR:
        command += f" --evaluator {evaluator}"
    return command


def aggregate_command(study_dir):
    return f"os-optics-kit study aggregate {Path(study_dir).as_posix()}"


def left_by_cancellation(record):
    """Whether a sample record is that of a sample the study's own cancellation stopped or never started."""
    error = record.get("error") or {}
    return (record.get("state") == "NOT_RUN" and error.get("code") == "CANCELLED") or cancelled_by_study(record)


def aggregated_since_change(study_dir):
    """Whether ``summary.json`` was written after the last plan and the last execution start (journal order)."""
    if not (Path(study_dir) / SUMMARY_FILE).is_file():
        return False
    for event in reversed(store.read_journal(study_dir)):
        name = event.get("event")
        if name == store.AGGREGATED_EVENT:
            return True
        if name in ("planned", "execution_started"):
            return False
    return False


def study_progress(study_dir):
    """``{state, samples, with_result, without_result, summary, aggregated, next}`` of the planned study in
    ``study_dir`` (files only), or None when it holds no planned study. ``state``: ``NOT_RUN`` (no sample has a
    result), ``PARTIAL`` or ``EXECUTED``; ``summary``: ``summary.json`` exists; ``aggregated``: it was written after
    the last plan and execution start; ``next``: the command that moves the study on (None when it is executed and
    aggregated)."""
    study_dir = Path(study_dir)
    if not store.has_study(study_dir):
        return None
    study = store.read_study(study_dir)
    entries = store.read_plan(study_dir)
    without = []
    for entry in entries:
        record = store.read_record(study_dir, entry["sample_id"])
        if record is None or record.get("request_hash") != entry["request_hash"] or left_by_cancellation(record):
            without.append(entry["sample_id"])
    state = NOT_RUN if len(without) == len(entries) else PARTIAL if without else EXECUTED
    summary = (study_dir / SUMMARY_FILE).is_file()
    if state == EXECUTED:
        command = None if summary else aggregate_command(study_dir)
    else:
        command = run_command(study_dir, resume=store.has_results(study_dir),
                              evaluator=(study.get("execution") or {}).get("evaluator"))
    return {"state": state, "samples": len(entries), "with_result": len(entries) - len(without),
            "without_result": without, "summary": summary, "aggregated": aggregated_since_change(study_dir),
            "next": command}


def reading_error(study_dir, *, aggregating=False):
    """The error a reader of ``study_dir`` raises (module docstring), or None when the study can be read (or the
    directory holds no planned study). ``aggregating``: for ``study aggregate``, which reads a study executed in part
    and needs no ``summary.json``."""
    return _error(study_dir, study_progress(study_dir), aggregating)


def require_results(study_dir, *, aggregating=False):
    """Raise ``reading_error`` when there is one; returns ``study_progress`` (None without a planned study)."""
    progress = study_progress(study_dir)
    error = _error(study_dir, progress, aggregating)
    if error is not None:
        raise error
    return progress


def _error(study_dir, progress, aggregating):
    if progress is None:
        return None
    state = progress["state"]
    if state == EXECUTED and (aggregating or progress["summary"]):
        return None
    if state == PARTIAL and (aggregating or progress["aggregated"]):
        return None
    try:
        store.require_not_running(study_dir)
    except wc.WorkflowError as running:  # its records are being written: STUDY_RUNNING, not "never executed"
        return running
    folder = Path(study_dir).as_posix()
    listed = wc.bounded(progress["without_result"], LISTED)
    details = {"study_dir": folder, "samples": progress["samples"], "with_result": progress["with_result"],
               "next": progress["next"]}
    if state == NOT_RUN:
        return wc.WorkflowError(
            "STUDY_NOT_RUN", f"{folder} holds a planned study that was not executed: none of its {progress['samples']} "
            f"planned samples has a result; execute it with `{progress['next']}`, then read it again", **details)
    if state == PARTIAL:
        missing = len(progress["without_result"])
        return wc.WorkflowError(
            "STUDY_INCOMPLETE", f"{folder} was executed in part: {missing} of its {progress['samples']} planned "
            "samples have no result (an execution was interrupted or cancelled, or the plan changed since) and no "
            f"table was aggregated since; continue it with `{progress['next']}`, or tabulate what ran (the other "
            f"samples as NOT_RUN rows) with `{aggregate_command(study_dir)}`",
            **details, without_result=listed, aggregate=aggregate_command(study_dir))
    return wc.WorkflowError(
        "STUDY_NOT_AGGREGATED", f"{folder}: every planned sample has a result, but no summary.json was written; "
        f"write it with `{progress['next']}` (no Engine run)", **details)
