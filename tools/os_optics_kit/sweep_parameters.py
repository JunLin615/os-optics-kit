"""Run a bounded one- or two-parameter sweep of a Project; save every sample's Project and run.

Usage:
  os-optics-kit sweep SPEC.json --out STUDY_DIR [--base BASE.json] [--overwrite]
                      [--workers N|auto] [--resume] [--plan-only] [--time-limit-s S] [--max-memory-mib M]
                      [--ray-data full|summary]

SPEC.json (keys; units as in os-optics-kit edit):
  {
    "base_project": "base.json",                  # relative to SPEC.json; --base overrides
    "parameters": [                               # 1 or 2 parameters; 2 give the full grid (first one outermost)
      {"name": "l2_shift", "unit": "mm",          # unit of the values (nm/um/mm/m, deg/rad/mrad/urad, W/mW/uW,
                                                  # or omitted for a plain number)
       "values": [-2, 0, 2],                      # or "range": {"start": -2, "stop": 2, "count": 5} (inclusive)
       "ops": [{"op": "transform", "selector": {"ids": ["L2"]},
                "translation_mm": ["$value", 0, 0]}]}
    ],
    "max_evaluations": 50,                        # required; the sweep is refused before any run when exceeded
    "targets": ["spot_radius"],                   # optional: target ids deciding each sample's outcome (default
                                                  # all); an id not in the Project is refused (TARGET_UNKNOWN)
    "constraints": [                              # optional, all must hold for a sample to be feasible
      {"outcome": ["PASS"]},
      {"target": "spot_radius", "assessment": ["MET"]},
      {"column": "target:spot_radius", "max": {"value": 18, "unit": "um"}}   # min / max, in the column's unit
    ],
    "objective": {"column": "target:spot_radius", "sense": "minimize"},     # or "maximize"
    "derived": [{"name": "spot_um", "op": "mul", "a": "target:spot_radius", "b": 1000, "unit": "um"}],
    "stop_on": []                                 # outcome classes that stop the sweep (default: never stop)
  }

Placeholder syntax in "ops": the string "$value" is replaced by the sample's value of that parameter, passed as
{"value": v, "unit": <parameter unit>} so the edit converts it to the field's unit; {"$value": {"scale": s,
"offset": o}} is replaced by o + s * v (o in the parameter's unit). Each sample applies its ops to the unchanged base.
Columns: "param:<name>", "target:<id>" (target value in the target's unit), "derived:<name>"; derived ops are add,
sub, mul, div (a, b: columns or numbers), abs (a) and abs_diff (|a - b|); a derived column may declare its "unit"
(nm/um/mm/m, deg/rad/mrad/urad, W/mW/uW or 1), which then applies to its constraints, objective, table and plots, and
may use the derived columns listed before it. A target id or column that the spec cannot produce (a misspelt
objective, constraint or derived operand) refuses the spec before any run (TARGET_UNKNOWN / COLUMN_UNKNOWN, with the
known ids and columns).

Each sample retains execution (including PARTIAL), quantity evidence, and target assessments separately.
The outcome shorthand describes selected requirements: ERROR, OUT_OF_MODEL (a selected target's applicability),
UNKNOWN (indeterminate), FAIL (unmet), PASS (all met), or NOT_APPLICABLE (no selected requirements).
No sample is dropped. Feasibility follows the explicit constraints and objective validity. An exact scalar
objective must have a finite value with applicable dependencies; bounded values remain recorded but are not
ranked as exact. Unrelated diagnostics do not invalidate an otherwise usable objective. The best feasible
sample optimizes the stated objective (ties: lowest sample id).

Steps (each one reads and writes files of STUDY_DIR): plan (study.json with the spec as given and normalized, base
project hash, Engine / catalog identity, environment, budget; plan.jsonl and requests/<id>.json, one request per
sample, before any run), execute (samples/<id>/project.json, run/ and sample.json; journal.jsonl), aggregate
(table.csv and summary.json, rows in sample order). The samples are independent: --workers N runs them in N worker
processes with the same results as one; --plan-only stops after planning (os-optics-kit study run executes the
plan); --resume continues a planned or interrupted study (samples whose request is unchanged and completed are kept).
stop_on acts in sample order: every sample after the first one with a listed outcome is recorded STOPPED. The study
directory must be new or empty (--overwrite replaces the files this command writes there and removes the study's
plots/ and report/ directories, which describe the previous samples).

Stdout: one compact JSON document (counts per outcome with denominators, best sample or no-feasible outcome, the
samples whose runs carry warnings, at most five rows with their warnings, execution, paths). Exit 0 completed (or
planned), 1 no feasible sample, 4 refused spec or invalid input, 130 cancelled (the study can be resumed).
"""

from __future__ import annotations

import argparse
import copy
import itertools
import math
from pathlib import Path

from . import workflow_common as wc
from .edit_project import EditError, apply_edits, normalize_ops
from .study import aggregate as agg, execute as ex, options as opts, requests as rq, store

PROG = "os-optics-kit sweep"
KIND = "opensci-example.parameter-sweep"
PLACEHOLDER = "$value"
KNOWN_UNITS = ("nm", "um", "µm", "mm", "m", "deg", "rad", "mrad", "urad", "W", "mW", "uW")
DERIVED_OPS = ("add", "sub", "mul", "div", "abs", "abs_diff")
TOP_ROWS = 5
#: Removed by --overwrite: the study's files and its samples, plot and report output.
STUDY_FILES = ("study.json", "table.csv", "summary.json")
STUDY_DIRS = ("samples", "plots", "report")
EXIT_CODES = {**wc.WORKFLOW_EXIT_CODES, **opts.EXIT_CODES}


# --------------------------------------------------------------------------------------------- spec

def parameter_values(parameter):
    """The value list of a parameter: ``values`` or ``range {start, stop, count}`` (inclusive, evenly spaced)."""
    name = parameter.get("name")
    if "values" in parameter:
        values = parameter["values"]
        if not isinstance(values, list) or not values:
            raise wc.WorkflowError("SPEC_INVALID", f"parameter {name!r}: values must be a non-empty list")
    elif "range" in parameter:
        spec = parameter["range"]
        try:
            start, stop, count = float(spec["start"]), float(spec["stop"]), int(spec["count"])
        except (KeyError, TypeError, ValueError):
            raise wc.WorkflowError("SPEC_INVALID", f"parameter {name!r}: range needs start, stop and count") from None
        if count < 1:
            raise wc.WorkflowError("SPEC_INVALID", f"parameter {name!r}: range count must be >= 1")
        values = [start] if count == 1 else [start + (stop - start) * i / (count - 1) for i in range(count)]
    else:
        raise wc.WorkflowError("SPEC_INVALID", f"parameter {name!r}: give values or range")
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise wc.WorkflowError("SPEC_INVALID", f"parameter {name!r}: values must be finite numbers")
    return list(values)


def substitute(template, value, unit):
    """Replace the "$value" placeholders of an op template (module docstring)."""
    if template == PLACEHOLDER:
        return value if unit is None else {"value": value, "unit": unit}
    if isinstance(template, dict):
        if set(template) == {PLACEHOLDER}:
            rule = template[PLACEHOLDER] or {}
            scaled = float(rule.get("offset", 0.0)) + float(rule.get("scale", 1.0)) * value
            return scaled if unit is None else {"value": scaled, "unit": unit}
        return {key: substitute(item, value, unit) for key, item in template.items()}
    if isinstance(template, list):
        return [substitute(item, value, unit) for item in template]
    return template


def normalize_spec(spec, *, build=None):
    """Validated, normalized parameters and options; raises WorkflowError(SPEC_INVALID / BUDGET_EXCEEDED)."""
    parameters = spec.get("parameters")
    if not isinstance(parameters, list) or not 1 <= len(parameters) <= 2:
        raise wc.WorkflowError("SPEC_INVALID", "parameters must list one or two parameters")
    names = []
    out = []
    for parameter in parameters:
        name = parameter.get("name")
        if not isinstance(name, str) or not name or name in names:
            raise wc.WorkflowError("SPEC_INVALID", "each parameter needs a unique name")
        names.append(name)
        unit = parameter.get("unit")
        if unit is not None:
            wc.convert(1.0, unit, unit, path=f"parameter {name} unit")
            if unit not in wc.UNITLESS and unit not in KNOWN_UNITS:
                raise wc.WorkflowError("UNIT_UNKNOWN", f"parameter {name!r}: unknown unit {unit!r}", unit=unit)
        ops = parameter.get("ops")
        if build is None and (not isinstance(ops, list) or not ops):
            raise wc.WorkflowError("SPEC_INVALID", f"parameter {name!r}: ops must be a non-empty list of op templates")
        out.append({"name": name, "unit": unit, "values": parameter_values(parameter), "ops": ops or []})
    total = math.prod(len(p["values"]) for p in out)
    budget = spec.get("max_evaluations")
    if isinstance(budget, bool) or not isinstance(budget, int) or budget < 1:
        raise wc.WorkflowError("SPEC_INVALID", "max_evaluations must be a positive integer")
    if total > budget:
        raise wc.WorkflowError("BUDGET_EXCEEDED", f"the sweep has {total} samples, more than max_evaluations "
                               f"{budget}; nothing was run", samples=total, max_evaluations=budget)
    derived = spec.get("derived") or []
    for item in derived:
        if item.get("op") not in DERIVED_OPS or not item.get("name"):
            raise wc.WorkflowError("SPEC_INVALID", f"derived columns need a name and op in {DERIVED_OPS}")
        unit = item.get("unit")
        if unit is not None and unit not in KNOWN_UNITS and unit not in wc.UNITLESS:
            raise wc.WorkflowError("UNIT_UNKNOWN", f"derived column {item['name']!r}: unknown unit {unit!r}; known: "
                                   f"{', '.join(KNOWN_UNITS)} or 1", unit=unit)
    objective = spec.get("objective")
    if objective is not None and (objective.get("sense") not in ("minimize", "maximize") or
                                  not isinstance(objective.get("column"), str)):
        raise wc.WorkflowError("SPEC_INVALID", "objective is {column, sense: minimize|maximize}")
    for rule in spec.get("constraints") or []:
        if not ({"outcome"} <= set(rule) or {"target", "assessment"} <= set(rule) or "column" in rule):
            raise wc.WorkflowError("SPEC_INVALID", f"unsupported constraint {rule!r}")
    stop_on = list(spec.get("stop_on") or [])
    if any(item not in wc.OUTCOMES for item in stop_on):
        raise wc.WorkflowError("SPEC_INVALID", f"stop_on lists outcome classes {wc.OUTCOMES}")
    return {"parameters": out, "samples": total, "max_evaluations": budget, "targets": spec.get("targets"),
            "constraints": spec.get("constraints") or [], "objective": objective, "derived": derived,
            "stop_on": stop_on}


def known_columns(normalized, target_ids):
    """Columns a sample row can hold for this spec: ``param:``, ``target:`` and ``derived:`` names."""
    return ([f"param:{p['name']}" for p in normalized["parameters"]] + [f"target:{t}" for t in target_ids] +
            [f"derived:{d['name']}" for d in normalized["derived"]])


def validate_columns(normalized, target_ids):
    """Refuse (before any run) a target id or column that no sample row can hold.

    Raises WorkflowError TARGET_UNKNOWN (selected targets, ``{"target": id}`` constraints) or COLUMN_UNKNOWN
    (objective, ``{"column": ...}`` constraints and derived operands; a derived operand may use the parameters, the
    targets and the derived columns listed before it), each with the known ids / columns."""
    target_ids = sorted(target_ids)
    columns = known_columns(normalized, target_ids)

    def target(ident, where):
        if ident not in target_ids:
            raise wc.WorkflowError("TARGET_UNKNOWN", f"{where}: target {ident!r} is not in the Project; known: "
                                   f"{target_ids}", unknown=[ident], known=target_ids)

    def column(name, where, available):
        if name not in available:
            raise wc.WorkflowError("COLUMN_UNKNOWN", f"{where}: column {name!r} is not produced by this sweep; "
                                   f"known: {available}", column=name, known=list(available))

    for ident in normalized["targets"] or []:
        target(ident, "targets")
    available = [c for c in columns if not c.startswith("derived:")]
    for item in normalized["derived"]:
        for key in ("a", "b"):
            operand = item.get(key)
            if isinstance(operand, str):
                column(operand, f"derived {item['name']}.{key}", available)
        available = [*available, f"derived:{item['name']}"]
    for index, rule in enumerate(normalized["constraints"]):
        if "outcome" in rule:
            continue
        if "target" in rule:
            target(rule["target"], f"constraint {index}")
        else:
            column(rule["column"], f"constraint {index}", columns)
    if normalized["objective"] is not None:
        column(normalized["objective"]["column"], "objective", columns)
    return columns


def sample_points(parameters):
    """Grid points in sample order: ``[(sample_id, {name: value}), ...]`` (ids ``s0001``..., first parameter
    outermost)."""
    grid = list(itertools.product(*[p["values"] for p in parameters]))
    names = [p["name"] for p in parameters]
    return [(sample_id, dict(zip(names, values))) for sample_id, values in zip(rq.sample_ids(len(grid)), grid)]


def sample_ops(parameters, values):
    ops = []
    for parameter in parameters:
        ops.extend(substitute(copy.deepcopy(parameter["ops"]), values[parameter["name"]], parameter["unit"]))
    return ops


# --------------------------------------------------------------------------------------------- columns

row_from_record = agg.row_from_record
counts = agg.counts


def _operand(row, item):
    if isinstance(item, (int, float)) and not isinstance(item, bool):
        return float(item) if math.isfinite(item) else None
    value = row.get(item)
    if isinstance(item, str) and item.startswith("target:") and row.get(f"{item}:exact") is False:
        return None
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def add_derived(row, derived):
    for item in derived:
        a = _operand(row, item.get("a"))
        b = _operand(row, item.get("b")) if item["op"] not in ("abs",) else 0.0
        value = None
        if a is not None and b is not None:
            op = item["op"]
            if op == "add":
                value = a + b
            elif op == "sub":
                value = a - b
            elif op == "mul":
                value = a * b
            elif op == "div":
                value = a / b if b != 0 else None
            elif op == "abs":
                value = abs(a)
            elif op == "abs_diff":
                value = abs(a - b)
        row[f"derived:{item['name']}"] = value if value is not None and math.isfinite(value) else None
        if item.get("unit") is not None:
            row[f"derived:{item['name']}:unit"] = item["unit"]
    return row


def column_unit(row, column):
    if column.startswith(("param:", "target:", "derived:")):
        return row.get(f"{column}:unit")
    return None


def _bound(row, column, bound):
    if wc.is_quantity(bound):
        unit = column_unit(row, column)
        if unit is None:
            raise wc.WorkflowError("UNIT_INCOMPATIBLE", f"constraint on {column}: the column has no unit; give a "
                                   "bare number")
        return wc.convert(bound["value"], bound["unit"], unit, path=f"constraint {column}")
    return bound


def constraint_failures(row, constraints):
    """Names of the explicit constraints a row does not meet; refused samples are always excluded."""
    failed = []
    if row["outcome"] == "ERROR":
        failed.append(f"outcome {row['outcome']}")
    for rule in constraints:
        if "outcome" in rule:
            if row["outcome"] not in rule["outcome"]:
                failed.append(f"outcome in {rule['outcome']}")
        elif "target" in rule:
            if row.get(f"target:{rule['target']}:assessment") not in rule["assessment"] or \
                    row.get(f"target:{rule['target']}:applicability_issue_codes"):
                failed.append(f"target {rule['target']} assessment in {rule['assessment']}")
        else:
            column = rule["column"]
            value = _operand(row, column)
            low = _bound(row, column, rule.get("min")) if rule.get("min") is not None else None
            high = _bound(row, column, rule.get("max")) if rule.get("max") is not None else None
            if not isinstance(value, (int, float)) or (low is not None and value < low) or \
                    (high is not None and value > high):
                failed.append(f"{column} within [{low}, {high}]")
    return failed


def select_best(rows, constraints, objective):
    """Mark ``feasible`` / ``constraint_failures`` on every row; return (best row or None, feasible count)."""
    feasible = []
    for row in rows:
        failures = constraint_failures(row, constraints)
        row["objective_valid"] = None if objective is None else _operand(row, objective["column"]) is not None
        if objective is not None and not row["objective_valid"]:
            failures.append(f"{objective['column']} has no exact usable value")
        row["feasible"] = not failures
        row["constraint_failures"] = failures
        if not failures:
            feasible.append(row)
    if objective is None or not feasible:
        return None, len(feasible)
    sign = 1.0 if objective["sense"] == "minimize" else -1.0
    best = min(feasible, key=lambda r: (sign * r[objective["column"]], r["sample_id"]))
    return best, len(feasible)


# --------------------------------------------------------------------------------------------- sweep

def plan_sweep(spec, study_dir, *, base=None, build=None, documents=None, base_path=None, overwrite=False,
               resume=False, outputs=None):
    """Validate ``spec`` and write the planned study (``study.json``, ``plan.jsonl``, ``requests/``); nothing runs.

    ``documents``: the Project of every grid point in sample order, built by the caller (an alternative to ops and
    ``build``). A sample whose ops the Engine refuses becomes a refused request (recorded ERROR, never run).
    Returns ``(study_dir, normalized spec)``."""
    normalized = normalize_spec(spec, build=build if build is not None else documents)
    if build is None and documents is None and base is None:
        raise wc.WorkflowError("SPEC_INVALID", "a base Project is required unless a build function is given")
    parameters = normalized["parameters"]
    points = sample_points(parameters)
    if documents is not None and len(documents) != len(points):
        raise wc.WorkflowError("SPEC_INVALID", f"{len(documents)} documents for {len(points)} grid points")
    if build is None and documents is None:  # unit and op errors are spec errors: refuse before running anything
        for _sample_id, values in points:
            normalize_ops(base, sample_ops(parameters, values))
    requests = []
    for index, (sample_id, values) in enumerate(points):
        labels = {"parameters": {p["name"]: {"value": values[p["name"]], "unit": p["unit"]} for p in parameters}}
        try:
            if documents is not None:
                document = documents[index]
            elif build is not None:
                document = build(dict(values))
            else:
                document = apply_edits(base, sample_ops(parameters, values), validate=False)["project"]
        except EditError as exc:
            requests.append(rq.make_request(sample_id, None, labels=labels, selected_targets=normalized["targets"],
                                            outputs=outputs, refusal={"stage": "edit", "code": exc.code,
                                                                      "problems": [exc.as_dict()]}))
            continue
        requests.append(rq.make_request(sample_id, document, labels=labels, selected_targets=normalized["targets"],
                                        outputs=outputs))
    # target ids and columns must exist: a misspelt id would make every sample UNKNOWN or infeasible
    reference = requests[0]["project"] or base
    validate_columns(normalized, {t["target_id"] for doc in (base, reference) if doc for t in doc.get("targets", [])})
    study_dir, resuming = store.prepare_study_dir(study_dir, overwrite=overwrite, resume=resume, files=STUDY_FILES,
                                                  dirs=STUDY_DIRS)
    base_check = wc.check(base) if base is not None else None
    header = store.study_header(
        KIND, spec=spec, normalized={**normalized, "parameters": parameters},
        base_project=None if base is None else {
            "file": None if base_path is None else Path(base_path).as_posix(),
            "project_hash": base_check.get("project_hash"), "engine_input_hash": base_check.get("engine_input_hash"),
            "document_sha256": wc.canonical_digest(base)},
        build_function=getattr(build, "__qualname__", repr(build)) if build is not None
        else ("documents supplied by the caller" if documents is not None else None),
        seed=None, sampling="deterministic grid",
        budget={"max_evaluations": normalized["max_evaluations"], "samples": normalized["samples"]},
        stop_on=normalized["stop_on"])
    store.plan_study(study_dir, header, requests, resume=resuming)
    return study_dir, normalized


def aggregate_sweep(study_dir):
    """``table.csv`` and ``summary.json`` of a sweep from its saved samples (no Engine run); returns the summary."""
    study_dir = Path(study_dir)
    study = store.read_study(study_dir)
    normalized = study["normalized"]
    parameters = normalized["parameters"]
    pairs = agg.collect_records(study_dir)
    rows = [add_derived(row, normalized["derived"]) for row in agg.rows_from_records(study_dir, pairs, parameters)]
    stopped_by = next((row["sample_id"] for row in rows if row["outcome"] in normalized["stop_on"]), None)
    best, feasible = select_best(rows, normalized["constraints"], normalized["objective"])
    restricted = bool(normalized["constraints"]) or normalized["objective"] is not None
    status = "NO_FEASIBLE" if restricted and feasible == 0 else "COMPLETED"
    columns = agg.write_table(study_dir, rows)
    study = agg.finish_study(study_dir, rows)
    summary = {
        "status": status, "kind": f"{KIND}-summary", "counts": counts(rows),
        "feasible": feasible, "stopped_by": stopped_by,
        "objective": normalized["objective"], "constraints": normalized["constraints"],
        "best": None if best is None else _best(best, parameters, normalized["objective"]),
        "no_feasible": None if status != "NO_FEASIBLE" else _no_feasible(rows),
        "parameters": [{"name": p["name"], "unit": p["unit"], "count": len(p["values"])} for p in parameters],
        "targets": agg.target_units(rows), "columns": columns, "rows": rows,
        "identity": agg.identity(study, rows, (study.get("base_project") or {}).get("project_hash")),
        "paths": {"study_dir": study_dir.as_posix(), "study": "study.json", "table": "table.csv",
                  "summary": "summary.json", "samples": "samples"},
    }
    agg.write_summary(study_dir, summary)
    return summary


def run_sweep(spec, study_dir, *, base=None, build=None, documents=None, base_path=None, overwrite=False,
              workers=1, scheduler=None, evaluator=None, limits=None, resume=False, plan_only=False, outputs=None,
              on_progress=None):
    """Plan, execute and aggregate the sweep described by ``spec`` (module docstring); returns the summary.

    ``base``: the base Project document (needed unless ``build`` or ``documents`` is given).
    ``build(params) -> ProjectDocumentV2``: Python alternative to op templates; ``params`` maps each parameter name
    to its value in the parameter's unit. ``documents``: prebuilt Projects in sample order. ``workers``,
    ``scheduler``, ``evaluator``, ``limits``, ``resume``, ``on_progress``: see ``study.execute_study``;
    ``plan_only``: stop after planning (status ``PLANNED``); a cancelled execution returns status ``CANCELLED``."""
    study_dir, normalized = plan_sweep(spec, study_dir, base=base, build=build, documents=documents,
                                       base_path=base_path, overwrite=overwrite, resume=resume, outputs=outputs)
    if plan_only:
        return opts.planned_summary(study_dir, KIND)
    execution = ex.execute_study(study_dir, workers=workers, scheduler=scheduler, evaluator=evaluator, limits=limits,
                                 resume=resume, stop_on=normalized["stop_on"], on_progress=on_progress)
    if execution.state == "CANCELLED":
        return opts.cancelled_summary(study_dir, KIND, execution)
    return aggregate_sweep(study_dir)


_columns = agg.columns
_target_units = agg.target_units


def _best(row, parameters, objective):
    return {"sample_id": row["sample_id"], "outcome": row["outcome"],
            "parameters": {p["name"]: {"value": row[f"param:{p['name']}"], "unit": p["unit"]} for p in parameters},
            "objective": None if objective is None else {
                "column": objective["column"], "sense": objective["sense"], "value": row.get(objective["column"]),
                "unit": column_unit(row, objective["column"])},
            "run_dir": row["run_dir"]}


def _no_feasible(rows):
    reasons = {}
    for row in rows:
        for failure in row["constraint_failures"]:
            reasons[failure] = reasons.get(failure, 0) + 1
    return {"message": "no sample meets the constraints", "failed_constraints": reasons}


def compact(summary, limit=TOP_ROWS):
    """The bounded stdout document of a sweep summary."""
    rows = summary["rows"]
    objective = summary["objective"]
    if objective is not None:
        sign = 1.0 if objective["sense"] == "minimize" else -1.0
        ordered = sorted(rows, key=lambda r: (not r["feasible"], sign * r[objective["column"]]
                                              if isinstance(r.get(objective["column"]), (int, float)) else math.inf,
                                              r["sample_id"]))
    else:
        ordered = rows
    keep = ["sample_id", "outcome", "feasible"] + [c for c in summary["columns"] if c.startswith(
        ("param:", "target:", "derived:")) and not c.endswith(":unit")] + ["error_code", "warnings"]
    top = [{key: row.get(key) for key in keep if row.get(key) is not None and row.get(key) != []}
           for row in ordered[:limit]]
    return {"status": summary["status"], "counts": summary["counts"], "feasible": summary["feasible"],
            "samples_with_warnings": wc.warning_summary(rows),
            "best": summary["best"], "no_feasible": summary["no_feasible"], "stopped_by": summary["stopped_by"],
            "units": {"parameters": {p["name"]: p["unit"] for p in summary["parameters"]},
                      "targets": summary["targets"]},
            "top_rows": top, "rows_omitted": max(0, len(rows) - limit),
            "execution": opts.execution_brief(summary["paths"]["study_dir"]), "paths": summary["paths"]}


def main(argv=None):
    def workflow():
        parser = wc.ArgumentParser(prog=PROG, description=__doc__,
                                    formatter_class=argparse.RawDescriptionHelpFormatter)
        parser.add_argument("spec", type=Path, help="Sweep spec JSON")
        parser.add_argument("--out", type=Path, required=True, help="Study directory to create")
        parser.add_argument("--base", type=Path, help="Base Project JSON (overrides spec base_project)")
        parser.add_argument("--overwrite", action="store_true", help="Replace the files this command writes in --out")
        opts.add_execution_options(parser)
        args = parser.parse_args(argv)
        spec = wc.read_json(args.spec)
        base_path = args.base or (args.spec.parent / spec["base_project"] if spec.get("base_project") else None)
        if base_path is None:
            raise wc.WorkflowError("SPEC_INVALID", "give base_project in the spec or --base")
        summary = run_sweep(spec, args.out, base=wc.read_json(base_path), base_path=base_path,
                            overwrite=args.overwrite, **opts.execution_arguments(args))
        return summary if summary["status"] in opts.EXIT_CODES else compact(summary)

    return wc.emit(workflow, EXIT_CODES)


if __name__ == "__main__":
    raise SystemExit(main())
