"""Composable study execution: plan requests, execute them (serially or in worker processes), aggregate the records.

A study is plan + execute + aggregate (+ present). Each step reads and writes files of one study directory, so a caller
can run any of them alone: execute a saved plan, aggregate saved samples, present a saved study. The ``sweep``,
``sensitivity``, ``tolerance`` tools and ``os-optics-kit study`` are compositions of these functions.

Python (an external optimiser handing over a batch of independent evaluations)::

    from os_optics_kit.study import make_request, plan_study, study_header, execute_study, collect_records

    requests = [make_request(f"s{i:04d}", document, labels={"parameters": {"x": {"value": x, "unit": "mm"}}})
                for i, (x, document) in enumerate(batch, start=1)]
    plan_study("runs/batch-01", study_header("my-optimiser.batch", parameters=[{"name": "x", "unit": "mm"}]),
               requests)
    execute_study("runs/batch-01", workers=4)
    for entry, record in collect_records("runs/batch-01"):
        ...                                   # record["outcome"], record["targets"], record["state"]

The evaluator (default ``evaluators.run_project``: one ``os_optics.local.run_optical_project`` call per request) and
the scheduler (``SerialScheduler``, ``ProcessScheduler`` or any object with ``run`` and ``cancel``) are replaceable.
"""

from __future__ import annotations

from .aggregate import (aggregate_generic, collect_records, columns, counts, finish_study, row_from_record,
                        rows_from_records, state_counts, target_units, write_summary, write_table)
from .evaluators import DEFAULT_EVALUATOR, empty_record, load_evaluator, run_project
from .execute import ExecutionSummary, execute_study, run_job
from .requests import make_request, request_hash, sample_ids, sample_seed, validate_request
from .resources import ResourceBudget, available_memory_bytes, peak_memory_bytes, suggest_workers
from .schedulers import ProcessScheduler, SerialScheduler, make_scheduler
from .store import (append_journal, clear_plan, has_study, plan_study, read_journal, read_plan, read_record,
                    read_request, read_study, study_header, write_plan)

__all__ = [
    "DEFAULT_EVALUATOR", "ExecutionSummary", "ProcessScheduler", "ResourceBudget", "SerialScheduler",
    "aggregate_generic", "append_journal", "available_memory_bytes", "clear_plan", "collect_records", "columns",
    "counts", "empty_record", "execute_study", "finish_study", "has_study", "load_evaluator", "make_request",
    "make_scheduler", "peak_memory_bytes", "plan_study", "read_journal", "read_plan", "read_record", "read_request",
    "read_study", "request_hash", "row_from_record", "rows_from_records", "run_job", "run_project", "sample_ids",
    "sample_seed", "state_counts", "study_header", "suggest_workers", "target_units", "validate_request",
    "write_plan", "write_summary", "write_table",
]
