"""Run a bounded one- or two-parameter sweep of a Project; save every sample's Project and run.

Usage:
  python examples/sweep_parameters.py SPEC.json --out STUDY_DIR [--base BASE.json] [--overwrite]

SPEC.json (keys; units as in edit_project.py):
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

Writes STUDY_DIR/study.json (spec as given and normalized, base project hash, engine / catalog identity,
environment, budget, timestamps), samples/<id>/project.json, samples/<id>/run/ (run directory),
samples/<id>/sample.json (full record), table.csv and summary.json. The study directory must be new or empty
(--overwrite replaces the files this script writes there and removes the study's plots/ and report/ directories,
which describe the previous samples).

Stdout: one compact JSON document (counts per outcome with denominators, best sample or no-feasible outcome, the
samples whose runs carry warnings, at most five rows with their warnings, paths). Exit 0 completed, 1 no feasible
sample, 4 refused spec or invalid input.
"""

from __future__ import annotations

import argparse
import copy
import itertools
import math
from pathlib import Path
import shutil
import time

if __package__:
    from . import workflow_common as wc
    from .edit_project import EditError, apply_edits, normalize_ops
else:
    import workflow_common as wc
    from edit_project import EditError, apply_edits, normalize_ops

PLACEHOLDER = "$value"
KNOWN_UNITS = ("nm", "um", "µm", "mm", "m", "deg", "rad", "mrad", "urad", "W", "mW", "uW")
DERIVED_OPS = ("add", "sub", "mul", "div", "abs", "abs_diff")
TOP_ROWS = 5
#: Directories removed by --overwrite: the samples and the default plot and report output of the study.
STUDY_DIRS = ("samples", "plots", "report")


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
    """Grid points in sample order: ``[(sample_id, {name: value}), ...]``."""
    grid = itertools.product(*[p["values"] for p in parameters])
    return [(f"s{index:04d}", dict(zip([p["name"] for p in parameters], values)))
            for index, values in enumerate(grid, start=1)]


def sample_ops(parameters, values):
    ops = []
    for parameter in parameters:
        ops.extend(substitute(copy.deepcopy(parameter["ops"]), values[parameter["name"]], parameter["unit"]))
    return ops


# --------------------------------------------------------------------------------------------- columns

def row_from_record(sample_id, values, parameters, record, study_dir):
    row = {"sample_id": sample_id, "outcome": record["outcome"], "status": record["status"],
           "in_model_scope": record["in_model_scope"],
           "execution": record.get("execution"), "requirements": record.get("requirements"),
           "model_scope_issues": sorted({i.get("code") for i in record["model_scope_issues"]}),
           "out_of_model_issues": sorted({i.get("code") for i in record.get("out_of_model_issues") or []}),
           "warnings": record["warnings"],
           "error_stage": (record["error"] or {}).get("stage"), "error_code": (record["error"] or {}).get("code"),
           "error_problems": sorted({p.get("code") for p in (record["error"] or {}).get("problems", [])})}
    for parameter in parameters:
        row[f"param:{parameter['name']}"] = values[parameter["name"]]
        row[f"param:{parameter['name']}:unit"] = parameter["unit"]
    for target in record["targets"]:
        row[f"target:{target['id']}"] = target["value"]
        row[f"target:{target['id']}:status"] = target["status"]
        row[f"target:{target['id']}:unit"] = target["unit"]
        row[f"target:{target['id']}:assessment"] = target.get("assessment")
        row[f"target:{target['id']}:quantity_state"] = target.get("quantity_state")
        row[f"target:{target['id']}:exact"] = wc.target_value_is_exact(target)
        row[f"target:{target['id']}:applicability_issue_codes"] = target.get("applicability_issue_codes")
    row.update({key: record["identity"].get(key) for key in (
        "project_hash", "input_hash", "result_hash", "engine_build_id", "catalog_version")})
    row["run_dir"] = None if record["run_dir"] is None else wc.relative(record["run_dir"], study_dir)
    row["seconds"] = round(record["seconds"], 4)
    return row


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


def counts(rows):
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

def _prepare_dir(study_dir, overwrite):
    study_dir = Path(study_dir)
    if study_dir.exists() and any(study_dir.iterdir()):
        if not overwrite:
            raise wc.WorkflowError("OUTPUT_EXISTS", f"{study_dir} is not empty; choose a new directory or use "
                                   "--overwrite", out=study_dir.as_posix())
        for name in STUDY_DIRS:
            shutil.rmtree(study_dir / name, ignore_errors=True)
        for name in ("study.json", "table.csv", "summary.json"):
            (study_dir / name).unlink(missing_ok=True)
    study_dir.mkdir(parents=True, exist_ok=True)
    return study_dir


def run_sweep(spec, study_dir, *, base=None, build=None, base_path=None, overwrite=False):
    """Run the sweep described by ``spec`` (module docstring) and write the study directory.

    ``base``: the base Project document (needed unless ``build`` is given). ``build(params) -> ProjectDocumentV2``:
    Python alternative to op templates; ``params`` maps each parameter name to its value in the parameter's unit.
    Returns the summary dict (also written to ``summary.json``)."""
    normalized = normalize_spec(spec, build=build)
    if build is None and base is None:
        raise wc.WorkflowError("SPEC_INVALID", "a base Project is required unless a build function is given")
    parameters = normalized["parameters"]
    points = sample_points(parameters)
    if build is None:  # unit and op errors are spec errors: refuse before running anything
        for _sample_id, values in points:
            normalize_ops(base, sample_ops(parameters, values))
    # target ids and columns must exist: a misspelt id would make every sample UNKNOWN or infeasible
    first = points[0][1]
    try:
        reference = build(dict(first)) if build is not None else \
            apply_edits(base, sample_ops(parameters, first), validate=False)["project"]
    except EditError:
        reference = base
    validate_columns(normalized, {t["target_id"] for doc in (base, reference) if doc for t in doc.get("targets", [])})
    started = wc.utc_now()
    study_dir = _prepare_dir(study_dir, overwrite)
    base_check = wc.check(base) if base is not None else None
    study = {
        "kind": "opensci-example.parameter-sweep", "spec": spec,
        "normalized": {**normalized, "parameters": parameters},
        "base_project": None if base is None else {
            "file": None if base_path is None else Path(base_path).as_posix(),
            "project_hash": base_check.get("project_hash"), "engine_input_hash": base_check.get("engine_input_hash"),
            "document_sha256": wc.canonical_digest(base)},
        "build_function": None if build is None else getattr(build, "__qualname__", repr(build)),
        "engine": wc.engine_identity(), "installed_catalog": wc.catalog_identity(),
        "catalog": wc.run_catalog_identity([]), "environment": wc.environment(),
        "seed": None, "sampling": "deterministic grid", "budget": {"max_evaluations": normalized["max_evaluations"],
                                                                    "samples": normalized["samples"]},
        "stop_on": normalized["stop_on"], "started": started,
    }
    wc.write_json(study_dir / "study.json", study)
    rows = []
    stopped_by = None
    for sample_id, values in points:
        sample_dir = study_dir / "samples" / sample_id
        t0 = time.perf_counter()
        if stopped_by is not None:
            record = wc.error_record("not_run", "STOPPED", [{"code": "STOPPED", "message": f"stop_on rule after "
                                     f"sample {stopped_by}"}], sample_dir=sample_dir, selected=normalized["targets"])
        else:
            try:
                if build is not None:
                    document = build(dict(values))
                else:
                    document = apply_edits(base, sample_ops(parameters, values), validate=False)["project"]
            except EditError as exc:
                sample_dir.mkdir(parents=True, exist_ok=True)
                record = wc.error_record("edit", exc.code, [exc.as_dict()], sample_dir=sample_dir,
                                         seconds=time.perf_counter() - t0, selected=normalized["targets"])
            else:
                record = wc.run_sample(document, sample_dir, targets=normalized["targets"])
        record["sample_id"] = sample_id
        record["parameters"] = {p["name"]: {"value": values[p["name"]], "unit": p["unit"]} for p in parameters}
        wc.write_json(sample_dir / "sample.json", record)
        row = add_derived(row_from_record(sample_id, values, parameters, record, study_dir), normalized["derived"])
        rows.append(row)
        if stopped_by is None and record["outcome"] in normalized["stop_on"]:
            stopped_by = sample_id
    best, feasible = select_best(rows, normalized["constraints"], normalized["objective"])
    restricted = bool(normalized["constraints"]) or normalized["objective"] is not None
    status = "NO_FEASIBLE" if restricted and feasible == 0 else "COMPLETED"
    columns = _columns(rows)
    wc.write_csv(study_dir / "table.csv", rows, columns)
    study["catalog"] = wc.run_catalog_identity(rows)
    study["finished"] = wc.utc_now()
    wc.write_json(study_dir / "study.json", study)
    summary = {
        "status": status, "kind": "opensci-example.parameter-sweep-summary", "counts": counts(rows),
        "feasible": feasible, "stopped_by": stopped_by,
        "objective": normalized["objective"], "constraints": normalized["constraints"],
        "best": None if best is None else _best(best, parameters, normalized["objective"]),
        "no_feasible": None if status != "NO_FEASIBLE" else _no_feasible(rows),
        "parameters": [{"name": p["name"], "unit": p["unit"], "count": len(p["values"])} for p in parameters],
        "targets": _target_units(rows), "columns": columns, "rows": rows,
        "identity": {"engine": study["engine"], **study["catalog"],
                     "base_project_hash": None if base_check is None else base_check.get("project_hash")},
        "paths": {"study_dir": study_dir.as_posix(), "study": "study.json", "table": "table.csv",
                  "summary": "summary.json", "samples": "samples"},
    }
    wc.write_json(study_dir / "summary.json", summary)
    return summary


def _columns(rows):
    columns = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    return columns


def _target_units(rows):
    units = {}
    for row in rows:
        for key, value in row.items():
            if key.startswith("target:") and key.endswith(":unit") and value is not None:
                units.setdefault(key[len("target:"):-len(":unit")], value)
    return units


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
            "top_rows": top, "rows_omitted": max(0, len(rows) - limit), "paths": summary["paths"]}


def main(argv=None):
    def workflow():
        parser = wc.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
        parser.add_argument("spec", type=Path, help="Sweep spec JSON")
        parser.add_argument("--out", type=Path, required=True, help="Study directory to create")
        parser.add_argument("--base", type=Path, help="Base Project JSON (overrides spec base_project)")
        parser.add_argument("--overwrite", action="store_true", help="Replace this script's files in --out")
        args = parser.parse_args(argv)
        spec = wc.read_json(args.spec)
        base_path = args.base or (args.spec.parent / spec["base_project"] if spec.get("base_project") else None)
        if base_path is None:
            raise wc.WorkflowError("SPEC_INVALID", "give base_project in the spec or --base")
        summary = run_sweep(spec, args.out, base=wc.read_json(base_path), base_path=base_path,
                            overwrite=args.overwrite)
        return compact(summary)

    return wc.emit(workflow)


if __name__ == "__main__":
    raise SystemExit(main())
