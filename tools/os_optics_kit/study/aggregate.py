"""Aggregation: rows, ``table.csv`` and ``summary.json`` from the saved records of a study, in plan order.

Aggregation reads only files (``study.json``, ``plan.jsonl``, ``samples/<id>/sample.json``) and never runs the
Engine, so it is the same whether the samples ran serially, in parallel, in several resumed executions or were
written by another evaluator. The sweep, sensitivity, tolerance and candidate studies build their summaries from
these helpers and add their own analysis (best sample, finite differences, distributions).

Row columns (``row_from_record``): ``sample_id, outcome, status, in_model_scope, execution, requirements,
model_scope_issues, out_of_model_issues, warnings, error_stage, error_code, error_problems``, ``param:<name>`` and
``param:<name>:unit`` per parameter, per target ``target:<id>`` (value in the target unit) with ``:status``, ``:unit``,
``:assessment``, ``:quantity_state``, ``:exact``, ``:applicability_issue_codes``, then ``project_hash, input_hash,
result_hash, engine_build_id, catalog_version, run_dir`` (study-relative) and ``seconds``.
"""

from __future__ import annotations

from pathlib import Path

from .. import workflow_common as wc
from . import store


# --------------------------------------------------------------------------------------------- records

def collect_records(study_dir, entries=None):
    """``[(plan entry, record)]`` in plan order. A sample without a record gets a ``NOT_RUN`` record (outcome
    ``ERROR``, code ``NOT_RUN``), so a partly executed study still aggregates."""
    study_dir = Path(study_dir)
    entries = store.read_plan(study_dir) if entries is None else entries
    out = []
    for entry in entries:
        record = store.read_record(study_dir, entry["sample_id"])
        if record is None or record.get("request_hash") != entry["request_hash"]:
            record = wc.error_record("not_run", "NOT_RUN", [{"code": "NOT_RUN", "message": "no record for the "
                                                                     "planned request of this sample"}])
            record.update({"sample_id": entry["sample_id"], **(entry.get("labels") or {}),
                           "request_hash": entry["request_hash"], "state": "NOT_RUN"})
        out.append((entry, record))
    return out


def parameter_values(record, parameters, field="value"):
    """``{name: number}`` of a record's ``parameters`` label (``field``: ``value`` or ``offset``)."""
    saved = record.get("parameters") or {}
    return {p["name"]: (saved.get(p["name"]) or {}).get(field) for p in parameters}


def row_from_record(sample_id, values, parameters, record, study_dir):
    """One table row of a sample record (module docstring); ``values``: ``param:`` column values by name."""
    error = record.get("error") or {}
    row = {"sample_id": sample_id, "outcome": record["outcome"], "status": record.get("status"),
           "in_model_scope": record.get("in_model_scope"),
           "execution": record.get("execution"), "requirements": record.get("requirements"),
           "model_scope_issues": sorted({i.get("code") for i in record.get("model_scope_issues") or []}),
           "out_of_model_issues": sorted({i.get("code") for i in record.get("out_of_model_issues") or []}),
           "warnings": record.get("warnings") or [],
           "error_stage": error.get("stage"), "error_code": error.get("code"),
           "error_problems": sorted({p.get("code") for p in error.get("problems", [])})}
    for parameter in parameters:
        row[f"param:{parameter['name']}"] = values[parameter["name"]]
        row[f"param:{parameter['name']}:unit"] = parameter["unit"]
    for target in record.get("targets") or []:
        row[f"target:{target['id']}"] = target["value"]
        row[f"target:{target['id']}:status"] = target["status"]
        row[f"target:{target['id']}:unit"] = target["unit"]
        row[f"target:{target['id']}:assessment"] = target.get("assessment")
        row[f"target:{target['id']}:quantity_state"] = target.get("quantity_state")
        row[f"target:{target['id']}:exact"] = wc.target_value_is_exact(target)
        row[f"target:{target['id']}:applicability_issue_codes"] = target.get("applicability_issue_codes")
    identity = record.get("identity") or {}
    row.update({key: identity.get(key) for key in (
        "project_hash", "input_hash", "result_hash", "engine_build_id", "catalog_version")})
    row["run_dir"] = run_dir_of(record, study_dir)
    row["seconds"] = round(record.get("seconds") or 0.0, 4)
    return row


def run_dir_of(record, study_dir):
    """The record's run directory relative to the study directory (None without one)."""
    run_dir = record.get("run_dir")
    if run_dir is None:
        return None
    path = Path(run_dir)
    if not path.is_absolute():
        return path.as_posix()  # study-relative already
    return wc.relative(path, study_dir)


def resolve_paths(record, study_dir):
    """A copy of a record whose ``sample_dir``, ``project_file`` and ``run_dir`` are paths a reader can open (the saved
    record keeps them study-relative)."""
    out = dict(record)
    for key in ("sample_dir", "project_file", "run_dir"):
        value = out.get(key)
        if value is not None and not Path(value).is_absolute():
            out[key] = (Path(study_dir) / value).as_posix()
    return out


def rows_from_records(study_dir, pairs, parameters, *, field="value"):
    """Rows of ``collect_records`` pairs; ``param:`` columns from the record's ``parameters`` label ``field``."""
    return [row_from_record(entry["sample_id"], parameter_values(record, parameters, field), parameters, record,
                            study_dir) for entry, record in pairs]


# --------------------------------------------------------------------------------------------- columns / counts

def columns(rows):
    """Every key of the rows, in first-seen order."""
    out = []
    for row in rows:
        for key in row:
            if key not in out:
                out.append(key)
    return out


def target_units(rows):
    units = {}
    for row in rows:
        for key, value in row.items():
            if key.startswith("target:") and key.endswith(":unit") and value is not None:
                units.setdefault(key[len("target:"):-len(":unit")], value)
    return units


def counts(rows):
    """Outcome counts with explicit denominators."""
    by = {outcome: 0 for outcome in wc.OUTCOMES}
    for row in rows:
        by[row["outcome"]] += 1
    total = len(rows)
    evaluable = by["PASS"] + by["FAIL"]
    return {"requested": total, "by_outcome": by,
            "pass_fraction": {"numerator": by["PASS"], "denominator": total,
                              "value": by["PASS"] / total if total else None,
                              "definition": "PASS samples / all requested samples"},
            "pass_fraction_among_evaluable": {"numerator": by["PASS"], "denominator": evaluable,
                                              "value": by["PASS"] / evaluable if evaluable else None,
                                              "definition": "PASS / (PASS + FAIL)"}}


def state_counts(pairs):
    """Job states of the records (``COMPLETED``, ``REFUSED``, ``FAILED``, ``CANCELLED``, ``NOT_RUN``)."""
    out = {}
    for _entry, record in pairs:
        state = record.get("state") or "COMPLETED"
        out[state] = out.get(state, 0) + 1
    return out


# --------------------------------------------------------------------------------------------- writers

def write_table(study_dir, rows, table_columns=None, name="table.csv"):
    """``table.csv`` (LF) of ``rows``; returns the columns."""
    table_columns = columns(rows) if table_columns is None else table_columns
    wc.write_csv(Path(study_dir) / name, rows, table_columns)
    return table_columns


def write_summary(study_dir, summary):
    return store.write_json_atomic(Path(study_dir) / "summary.json", summary)


def finish_study(study_dir, rows):
    """Record the run catalog provenance and the aggregation time in ``study.json``; returns the study. It holds
    the study's ownership while it does, so it is refused (``STUDY_RUNNING``) while an execution runs: an
    aggregation calls it before it writes any other file."""
    with store.owned(study_dir):
        study = wc.read_json(Path(study_dir) / store.STUDY_FILE)
        study["catalog"] = wc.run_catalog_identity(rows)
        study["finished"] = wc.utc_now()
        store.write_study(study_dir, study)
    return study


def identity(study, rows, base_project_hash=None):
    """The ``identity`` block of a summary: Engine of the study, catalog of the saved runs, base Project hash."""
    return {"engine": study["engine"], **wc.run_catalog_identity(rows), "base_project_hash": base_project_hash}


def aggregate_generic(study_dir, *, plots=False, report=False):
    """Summary of a study without a tool-specific analysis (candidate studies, studies planned through Python):
    rows of every planned sample with the study's ``parameters`` (``[{name, unit}]``) and its ``row_labels``
    (label keys copied to each row), outcome counts, ``table.csv`` and ``summary.json``."""
    study_dir = Path(study_dir)
    study = wc.read_json(study_dir / store.STUDY_FILE)
    parameters = list(study.get("parameters") or [])
    pairs = collect_records(study_dir)
    rows = rows_from_records(study_dir, pairs, parameters)
    for row, (_entry, record) in zip(rows, pairs):
        for key in study.get("row_labels") or []:
            row[key] = record.get(key)
    study = finish_study(study_dir, rows)  # first: refused while an execution owns the study
    table_columns = write_table(study_dir, rows)
    by_state = state_counts(pairs)
    summary = {
        "status": "COMPLETED", "kind": f"{study.get('kind')}-summary", "counts": counts(rows),
        "states": by_state, "feasible": None, "stopped_by": (study.get("execution") or {}).get("stopped_by"),
        "objective": None, "constraints": [], "best": None, "no_feasible": None,
        "parameters": [{"name": p["name"], "unit": p.get("unit"), "count": len(rows)} for p in parameters],
        "targets": target_units(rows), "columns": table_columns, "rows": rows,
        "identity": identity(study, rows, (study.get("base_project") or {}).get("project_hash")),
        "paths": {"study_dir": study_dir.as_posix(), "study": "study.json", "table": "table.csv",
                  "summary": "summary.json", "samples": "samples"},
    }
    write_summary(study_dir, summary)
    return summary
