"""Evaluators: the function that turns one request into one sample record.

An evaluator is ``evaluator(request, sample_dir, limits) -> record``. ``request`` is a study request
(``requests.py``); ``sample_dir`` is the sample's own directory (the evaluator is its only writer); ``limits`` is
``{"time_limit_s", "max_memory_mib"}`` (either may be None). The record holds at least ``outcome`` (``ERROR``,
``OUT_OF_MODEL``, ``UNKNOWN``, ``FAIL``, ``PASS`` or ``NOT_APPLICABLE``); ``empty_record`` gives every other key its
default. An evaluator never raises for a refused design: it returns a record with ``error``.

``run_project`` is the default: one in-process ``os_optics.local.run_optical_project`` call through
``workflow_common.run_sample``, monitored by an ``os_optics.run_monitor.RunMonitor``. The monitor enforces the memory
limit, stops the run when the time limit has passed or the study was cancelled, and supplies the phase timings and
the peak memory of the record. Limits act at the Engine's checkpoints: the time limit and a cancellation are read
when the Engine reports progress (every phase change, and at most every 0.1 s inside a phase) and stop the run at the
next checkpoint.

A caller may pass another evaluator to ``execute_study`` (a function, or ``"module:function"`` for worker
processes), for example one that evaluates the request with a different tool.
"""

from __future__ import annotations

import importlib
from pathlib import Path
import shutil
import time

from .. import workflow_common as wc

DEFAULT_EVALUATOR = "os_optics_kit.study.evaluators:run_project"
#: Seconds between two progress reports of a monitored run (the granularity of the time limit inside a phase).
PROGRESS_INTERVAL_S = 0.1
MIB = 1024 * 1024

_CANCEL = None  # an Event-like object set when the study is cancelled (installed by the scheduler)


def set_cancel_event(event):
    """Install the object whose ``is_set()`` tells evaluators of this process that the study was cancelled."""
    global _CANCEL
    _CANCEL = event


def cancel_requested():
    """Whether the study this process works for was cancelled."""
    return _CANCEL is not None and _CANCEL.is_set()


def empty_record(sample_dir=None, selected_targets=None):
    """A sample record with every key at its default (outcome ``ERROR``); an evaluator fills in what it computed."""
    return wc._empty_record(sample_dir, None, selected_targets)


def normalize_limits(limits):
    """``{"time_limit_s": float | None, "max_memory_mib": int | None}``; refuses non-positive values."""
    limits = dict(limits or {})
    unknown = sorted(set(limits) - {"time_limit_s", "max_memory_mib"})
    if unknown:
        raise wc.WorkflowError("BAD_ARGUMENT", f"limits: unknown keys {unknown}; known: time_limit_s, max_memory_mib")
    out = {}
    for key in ("time_limit_s", "max_memory_mib"):
        value = limits.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not value > 0):
            raise wc.WorkflowError("BAD_ARGUMENT", f"limits.{key} must be a positive number")
        out[key] = value
    if out["max_memory_mib"] is not None:
        out["max_memory_mib"] = int(out["max_memory_mib"])
    return out


def run_project(request, sample_dir, limits=None):
    """Run the request's Project once with the installed Engine and return the sample record."""
    from os_optics.run_monitor import RunMonitor

    limits = normalize_limits(limits)
    outputs = request["outputs"]
    sample_dir = Path(sample_dir)
    run_dir = sample_dir / "run"
    if run_dir.exists():  # files of an interrupted or superseded evaluation of this sample
        shutil.rmtree(run_dir, ignore_errors=True)
    started = time.monotonic()
    stop = {"code": None}

    def on_progress(_report):
        if monitor.cancelled:
            return
        if cancel_requested():
            stop["code"] = "STUDY_CANCELLED"
            monitor.cancel("the study was cancelled")
        elif limits["time_limit_s"] is not None and time.monotonic() - started > limits["time_limit_s"]:
            stop["code"] = "TIME_LIMIT"
            monitor.cancel(f"the evaluation passed its time limit of {limits['time_limit_s']:g} s")

    monitor = RunMonitor(on_progress, interval_s=PROGRESS_INTERVAL_S,
                         max_memory_bytes=None if limits["max_memory_mib"] is None
                         else limits["max_memory_mib"] * MIB)
    record = wc.run_sample(request["project"], sample_dir, targets=request["selected_targets"],
                           write_run=outputs["run_directory"], monitor=monitor, ray_data=outputs["ray_data"])
    if (record.get("error") or {}).get("code") == "RUN_STOPPED":
        for problem in record["error"]["problems"]:
            if problem.get("code") == "CALLER":  # cancelled here: by the study, the time limit or Ctrl-C
                problem["code"] = stop["code"] or "INTERRUPTED"
    progress = monitor.summary()
    record["timing"] = {"run_s": record["seconds"], "phase_seconds": progress["phase_seconds"]}
    record["peak_memory_bytes"] = progress["peak_memory_bytes"]
    return record


def evaluator_name(evaluator):
    """``"module:function"`` of an evaluator given as that text or as an importable function, else None."""
    if evaluator is None:
        return DEFAULT_EVALUATOR
    if isinstance(evaluator, str):
        return evaluator
    module, name = getattr(evaluator, "__module__", None), getattr(evaluator, "__qualname__", "")
    if not module or "<" in name or "." in name:
        return None
    try:
        found = getattr(importlib.import_module(module), name, None)
    except ImportError:
        return None
    return f"{module}:{name}" if found is evaluator else None


def load_evaluator(spec):
    """The function named ``"module:function"`` (``EVALUATOR_INVALID`` when it cannot be imported or called)."""
    module_name, sep, function_name = str(spec).partition(":")
    if not sep or not module_name or not function_name:
        raise wc.WorkflowError("EVALUATOR_INVALID", f"evaluator {spec!r}: use module:function", evaluator=str(spec))
    try:
        function = getattr(importlib.import_module(module_name), function_name)
    except (ImportError, AttributeError) as exc:
        raise wc.WorkflowError("EVALUATOR_INVALID", f"evaluator {spec!r} cannot be imported: {exc}",
                               evaluator=str(spec)) from None
    if not callable(function):
        raise wc.WorkflowError("EVALUATOR_INVALID", f"evaluator {spec!r} is not a function", evaluator=str(spec))
    return function
