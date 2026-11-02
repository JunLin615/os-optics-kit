"""Shared steps of the parameterized design workflows (beam_expansion_focus.py, polarization_control.py,
alignment_analysis.py).

A workflow builds one design of optical_designs.py from the customer's inputs and then, without new physics code:

1. runs the nominal design once (``workflow_common.run_sample``: saved Project and native run directory);
2. runs bounded one-parameter studies with ``sweep_parameters.run_sweep`` (one saved Project and run per sample,
   every outcome class kept);
3. compares each study with the nominal run by target and plane ID (``compare_runs.compare``);
4. plots the targets whose values change across the study (``plot_results.plot_sweep``) and the nominal power
   distribution (``plot_results.plot_power``);
5. compares Engine values with the design's independent closed-form reference (plain Python formulas of
   optical_designs.py, labelled "independent check");
6. assembles reports with ``build_report.build_report``.

Study syntax (``--study``, repeatable): ``NAME=V1,V2,...`` or ``NAME=START..STOP:COUNT`` (inclusive, evenly
spaced). NAME is a design parameter; a bare value is in the parameter's unit (its name suffix, e.g. ``gap_error_mm``
in mm) and a value with a unit suffix is converted (``gap_error_mm=-500um,0,0.5mm``; length nm/um/mm/m, angle
deg/rad/mrad/urad, power W/mW/uW). The total number of Engine evaluations (nominal + all samples) is checked against
``--max-evaluations`` before anything runs; every sample Project is built before the first run, so an invalid value
refuses the whole request (exit 4) instead of failing half way.

Output directory layout:
  workflow.json                     full workflow summary (stdout holds a bounded version)
  nominal/project.json, run/        nominal Project and run; sample.json (sample record), closed_form.csv / .json,
                                    plots/power_distribution.png / .svg
  studies/<NAME>/                   sweep study (study.json, summary.json, table.csv, samples/<id>/{project.json,
                                    run/, sample.json}), compare/compare.json / .csv (nominal vs every sample),
                                    plots/<target>.png / .svg, closed_form.csv / .json, report/report.md / .json
  report/report.md, report.json     nominal run, inputs, closed-form check and the study index with links

Outcome classes per sample (workflow_common): ERROR, OUT_OF_MODEL (selected target applicability),
UNKNOWN (indeterminate), FAIL (unmet), PASS (all selected targets met), NOT_APPLICABLE (no selected requirements); their
warning codes are listed.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import re
import shutil
import time

if __package__:
    from . import workflow_common as wc
    from . import sweep_parameters as sp
    from . import compare_runs as cr
    from . import build_report as br
else:
    import workflow_common as wc
    import sweep_parameters as sp
    import compare_runs as cr
    import build_report as br

#: exit code of a workflow = outcome class of its nominal run
EXIT_CODES = {"PASS": 0, "FAIL": 1, "UNKNOWN": 2, "OUT_OF_MODEL": 3, "ERROR": 4, "NOT_APPLICABLE": 0, "DESCRIBED": 0}
SWEEP_UNITS = ("nm", "um", "µm", "mm", "m", "deg", "rad", "mrad", "urad", "W", "mW", "uW", "1", "")
MAX_NOTABLE = 8
_VALUE = re.compile(r"^\s*(?P<number>[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*(?P<unit>[A-Za-zµ]*)\s*$")


# --------------------------------------------------------------------------------------------- inputs

def parse_value(text, unit, *, path="value"):
    """``"0.5"`` (in ``unit``) or ``"500um"`` (converted to ``unit``) -> float."""
    match = _VALUE.match(str(text))
    if not match:
        raise wc.WorkflowError("BAD_ARGUMENT", f"{path}: {text!r} is not a number with an optional unit", path=path)
    value, given = float(match["number"]), match["unit"]
    if not math.isfinite(value):
        raise wc.WorkflowError("BAD_ARGUMENT", f"{path}: {text!r} is not finite", path=path)
    if not given or given == unit:
        return value
    if unit is None or unit not in SWEEP_UNITS:
        raise wc.WorkflowError("UNIT_INCOMPATIBLE", f"{path}: give a bare number (unit {unit or 'none'})", path=path,
                               unit=given)
    return wc.convert(value, given, unit, path=path)


def _quantity(unit, name):
    def parse(text):
        try:
            return parse_value(text, unit, path=f"--{name}")
        except wc.WorkflowError as exc:
            raise argparse.ArgumentTypeError(str(exc)) from None
    parse.__name__ = f"number[{unit}]" if unit else "number"
    return parse


def _pair(unit, name):
    one = _quantity(unit, name)

    def parse(text):
        parts = str(text).split(",")
        if len(parts) != 2:
            raise argparse.ArgumentTypeError(f"--{name}: give MIN,MAX")
        return (one(parts[0]), one(parts[1]))
    parse.__name__ = f"MIN,MAX[{unit}]"
    return parse


def _studyable(info):
    default = info["default"]
    return info["unit"] is not None and not isinstance(default, (tuple, list, str))


def parse_study(text, parameters):
    """``NAME=V1,V2`` / ``NAME=START..STOP:COUNT`` -> ``{"name", "unit", "values"}`` (module docstring)."""
    name, sep, spec = str(text).partition("=")
    name = name.strip()
    if not sep or not spec.strip():
        raise wc.WorkflowError("STUDY_INVALID", f"--study {text!r}: use NAME=V1,V2,... or NAME=START..STOP:COUNT")
    info = parameters.get(name)
    if info is None or not _studyable(info):
        names = sorted(n for n, i in parameters.items() if _studyable(i))
        raise wc.WorkflowError("STUDY_INVALID", f"--study {text!r}: {name!r} is not a numeric design parameter; "
                               f"choose one of {', '.join(names)}", parameter=name)
    unit = info["unit"]
    path = f"--study {name}"
    if ".." in spec:
        span, _colon, count = spec.partition(":")
        start, _dots, stop = span.partition("..")
        try:
            count = int(count)
        except ValueError:
            raise wc.WorkflowError("STUDY_INVALID", f"{path}: range needs START..STOP:COUNT") from None
        if count < 1:
            raise wc.WorkflowError("STUDY_INVALID", f"{path}: COUNT must be at least 1")
        low, high = parse_value(start, unit, path=path), parse_value(stop, unit, path=path)
        values = [low] if count == 1 else [low + (high - low) * i / (count - 1) for i in range(count)]
    else:
        values = [parse_value(item, unit, path=path) for item in spec.split(",") if item.strip()]
    if not values or len(set(values)) != len(values):
        raise wc.WorkflowError("STUDY_INVALID", f"{path}: give distinct values")
    return {"name": name, "unit": unit, "values": values}


def add_design_options(parser, design_info, defaults, choices=None):
    """One option per design parameter: ``--f1-mm`` etc. (name and unit from optical_designs.DESIGN_INFO)."""
    group = parser.add_argument_group("design inputs (bare numbers in the unit of the option name; a unit suffix "
                                      "such as 500um or 0.8mW is converted)")
    for name, info in design_info["parameters"].items():
        flag = "--" + name.replace("_", "-")
        default = defaults.get(name, info["default"])
        text = f"{info['meaning']} [{info['unit'] or '-'}]; {info['role']}; default {default}"
        if choices and name in choices:
            group.add_argument(flag, dest=name, choices=choices[name], default=default, help=text)
        elif isinstance(info["default"], (tuple, list)):
            group.add_argument(flag, dest=name, type=_pair(info["unit"], name), default=default, metavar="MIN,MAX",
                               help=text)
        else:
            group.add_argument(flag, dest=name, type=_quantity(info["unit"] if info["unit"] in SWEEP_UNITS else None,
                                                               name),
                               default=default, metavar="VALUE", help=text)


def add_study_options(parser, default_studies, max_evaluations):
    group = parser.add_argument_group("study and output")
    group.add_argument("--study", action="append", metavar="NAME=VALUES",
                       help="one-parameter study, repeatable: NAME=V1,V2,... or NAME=START..STOP:COUNT; replaces "
                            "the default studies: " + "; ".join(default_studies))
    group.add_argument("--no-studies", action="store_true", help="run the nominal design only")
    group.add_argument("--max-evaluations", type=int, default=max_evaluations,
                       help=f"upper bound on Engine runs (nominal + all samples), checked before running "
                            f"(default {max_evaluations})")
    group.add_argument("--max-plots", type=int, default=3, help="plots per study (targets that change; default 3)")
    group.add_argument("--out", type=Path, default=None, help="output directory (new or empty)")
    group.add_argument("--overwrite", action="store_true", help="replace this workflow's files in --out")
    group.add_argument("--list-parameters", action="store_true",
                       help="print the design parameters (unit, default, meaning, role) and exit")


def inputs_from_args(args, design_info):
    return {name: getattr(args, name) for name in design_info["parameters"]}


def describe(config, defaults):
    info = config["info"]
    return {"status": "DESCRIBED", "workflow": config["workflow"], "design": config["design"],
            "summary": info["summary"], "model_basis": info["model_basis"], "frames": info["frames"],
            "parameters": {name: {**p, "default": defaults.get(name, p["default"]), "studyable": _studyable(p)}
                           for name, p in info["parameters"].items()},
            "observations": info["observations"], "targets": info["targets"],
            "default_studies": config["default_studies"], "reference": config["reference_method"]}


# --------------------------------------------------------------------------------------------- comparison rows

def compare_value(quantity, unit, engine, reference, *, method, rel=None, abs_tol=None, period=None):
    """One Engine-vs-reference row; ``agree`` None when either value is missing. ``period``: compare modulo it."""
    row = {"quantity": quantity, "unit": unit, "engine": engine, "reference": reference, "method": method,
           "tolerance": {"rel": rel} if rel is not None else {"abs": abs_tol}, "abs_diff": None, "rel_diff": None,
           "agree": None}
    if not isinstance(engine, (int, float)) or not isinstance(reference, (int, float)):
        row["note"] = "not compared: no Engine value" if not isinstance(engine, (int, float)) else \
            "not compared: no reference value"
        return row
    diff = engine - reference
    if period:
        diff = (diff + period / 2.0) % period - period / 2.0
    row["abs_diff"] = abs(diff)
    row["rel_diff"] = abs(diff) / abs(reference) if rel is not None and reference else None
    if rel is not None:
        row["agree"] = abs(diff) <= rel * max(abs(reference), abs(engine))
    else:
        row["agree"] = abs(diff) <= abs_tol
    return row


def target_values(record):
    return {t["id"]: t.get("value") for t in record.get("targets") or []}


def closed_form_totals(rows):
    compared = [r for r in rows if r["agree"] is not None]
    by_method = {}
    for row in compared:
        entry = by_method.setdefault(row["method"], {"compared": 0, "agreed": 0, "max_abs_diff": 0.0,
                                                     "max_rel_diff": 0.0})
        entry["compared"] += 1
        entry["agreed"] += bool(row["agree"])
        entry["max_abs_diff"] = max(entry["max_abs_diff"], row["abs_diff"])
        if row["rel_diff"] is not None:
            entry["max_rel_diff"] = max(entry["max_rel_diff"], row["rel_diff"])
    return {"compared": len(compared), "agreed": sum(bool(r["agree"]) for r in compared),
            "not_compared": len(rows) - len(compared), "by_method": by_method,
            "disagreements": [{k: r.get(k) for k in ("sample_id", "quantity", "engine", "reference", "abs_diff")}
                              for r in compared if not r["agree"]][:MAX_NOTABLE]}


CLOSED_FORM_COLUMNS = ["sample_id", "parameter", "parameter_value", "parameter_unit", "quantity", "unit", "engine",
                       "reference", "abs_diff", "rel_diff", "tolerance", "agree", "method", "note"]


def _closed_form_table(rows, limit=60):
    lines = ["| sample | parameter | quantity | unit | Engine | reference | abs diff | agree | method |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for row in rows[:limit]:
        value = row.get("parameter_value")
        param = "-" if value is None else f"{row['parameter']}={value:.9g} {row.get('parameter_unit') or ''}".strip()
        lines.append("| " + " | ".join([
            row.get("sample_id") or "-", param, row["quantity"], row["unit"] or "-", _num(row["engine"]),
            _num(row["reference"]), _num(row["abs_diff"]), "-" if row["agree"] is None else str(row["agree"]).lower(),
            row["method"]]) + " |")
    if len(rows) > limit:
        lines.append(f"\n{len(rows) - limit} more rows in closed_form.csv.")
    return lines


def _num(value):
    return "-" if not isinstance(value, (int, float)) else f"{value:.12g}"


def _short(value):
    if isinstance(value, float):
        return float(f"{value:.10g}")
    if isinstance(value, dict):
        return {k: _short(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_short(v) for v in value]
    return value


# --------------------------------------------------------------------------------------------- running

def _prepare(out_dir, overwrite):
    out_dir = Path(out_dir)
    names = ("workflow.json", "nominal", "studies", "report", "synthetic")
    if out_dir.exists() and any(out_dir.iterdir()):
        if not overwrite:
            raise wc.WorkflowError("OUTPUT_EXISTS", f"{out_dir} is not empty; choose a new directory or use "
                                   "--overwrite", out=out_dir.as_posix())
        for name in names:
            path = out_dir / name
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _build(builder, params, label):
    try:
        return builder(**params)
    except Exception as exc:  # noqa: BLE001 - the builder's refusal names the input
        raise wc.WorkflowError("INPUT_INVALID", f"{label}: the design cannot be built: {exc}",
                               input=label) from None


def _notable(row, record, analysis_keys, analysis):
    """Compact entry of a sample that is not a clean PASS (or carries warnings)."""
    not_pass = {t["id"]: t["status"] for t in record.get("targets") or [] if t["status"] != "PASS"}
    entry = {"value": row["value"], "outcome": record["outcome"], "status": record["status"],
             "targets": not_pass or ("all PASS" if record.get("targets") else "none")}
    codes = sorted({i.get("code") for i in record.get("model_scope_issues") or []} |
                   {i.get("code") for i in record.get("issues") or []})
    if codes:
        entry["codes"] = codes
    if record.get("error"):
        entry["error"] = {"stage": record["error"].get("stage"), "code": record["error"].get("code"),
                          "problems": sorted({p.get("code") for p in record["error"].get("problems") or []})}
    if analysis and analysis_keys:
        entry["analysis"] = {k: analysis.get(k) for k in analysis_keys if k in analysis}
    return entry


def _changing_targets(rows, order, nominal):
    """Target ids whose values change across the samples or against the nominal run (or are missing somewhere),
    largest change relative to the target's bound width first. ``nominal``: the nominal sample record."""
    reference = {t["id"]: t for t in nominal.get("targets") or []}
    ranked = []
    for index, target_id in enumerate(order):
        column = f"target:{target_id}"
        values = [r.get(column) for r in rows if column in r]
        if not values:
            continue
        if target_id in reference:
            values.append(reference[target_id].get("value"))
        numbers = [v for v in values if isinstance(v, (int, float))]
        spread = (max(numbers) - min(numbers)) if numbers else 0.0
        scale = max([abs(v) for v in numbers] + [1e-300])
        bounds = wc.target_bounds(reference[target_id]) if target_id in reference else {}
        low, high = bounds.get("minimum"), bounds.get("maximum")
        width = (high - low) if low is not None and high is not None else abs(high if high is not None else
                                                                              low if low is not None else scale)
        missing = len(numbers) != len(values)
        if missing or (spread > 1e-9 * scale and spread > 1e-12):
            ranked.append((-(math.inf if missing else spread / (width or scale)), index, target_id))
    return [target_id for _rank, _index, target_id in sorted(ranked)]


def _plot_power(run_dir, out_dir):
    try:
        if __package__:
            from . import plot_results as pr
        else:
            import plot_results as pr
        return pr.plot_power(run_dir, out_dir=out_dir)["out"], None
    except ImportError as exc:
        return None, f"plots skipped: {exc}"


def _plot_sweep(study_dir, target_ids, out_dir):
    try:
        if __package__:
            from . import plot_results as pr
        else:
            import plot_results as pr
    except ImportError as exc:
        return [], f"plots skipped: {exc}"
    written = []
    for target_id in target_ids:
        result = pr.plot_sweep(study_dir, f"target:{target_id}", out_dir=out_dir, name=target_id)
        written.append({"target": target_id, **result["out"]})
    return written, None


def run_workflow(config, inputs, studies, out_dir, *, max_evaluations, max_plots=3, overwrite=False):
    """Run the nominal design and the studies; write every artifact; return the full workflow summary.

    ``config``: ``workflow`` (script name), ``design`` (DESIGN_INFO key), ``info`` (its DESIGN_INFO entry),
    ``builder`` (keyword builder of optical_designs), ``checks(params, record) -> {"rows", "analysis"}`` (independent
    reference rows, optional analysis dict), ``reference_method`` (text), ``analysis_keys`` (analysis fields shown
    for notable samples), ``default_studies``."""
    started = time.perf_counter()
    target_order = list(config["info"]["targets"])
    total = 1 + sum(len(s["values"]) for s in studies) + int(config.get("planned_extra_evaluations", 0))
    if total > max_evaluations:
        raise wc.WorkflowError("BUDGET_EXCEEDED", f"the request needs {total} Engine runs (nominal + samples), more "
                               f"than --max-evaluations {max_evaluations}; nothing was run", evaluations=total,
                               max_evaluations=max_evaluations)
    nominal_doc = _build(config["builder"], inputs, "nominal design")
    planned = []
    for study in studies:
        docs = {}
        for value in study["values"]:
            params = {**inputs, study["name"]: value}
            docs[value] = (_build(config["builder"], params, f"{study['name']}={value!r}"), params)
        planned.append((study, docs))
    out_dir = _prepare(out_dir, overwrite)

    nominal_dir = out_dir / "nominal"
    record = wc.run_sample(nominal_doc, nominal_dir)
    record["parameters"] = {name: value for name, value in inputs.items()}
    wc.write_json(nominal_dir / "sample.json", record)
    checks = config["checks"](inputs, record)
    nominal_rows = [{"sample_id": "nominal", "parameter": None, "parameter_value": None, "parameter_unit": None,
                     **row} for row in checks["rows"]]
    wc.write_json(nominal_dir / "closed_form.json", {"rows": nominal_rows, "analysis": checks.get("analysis")})
    wc.write_csv(nominal_dir / "closed_form.csv", nominal_rows, CLOSED_FORM_COLUMNS)
    nominal_run = nominal_dir / "run" if record["run_dir"] else None
    power_plot, plot_note = _plot_power(nominal_run, nominal_dir / "plots") if nominal_run else (None, None)
    evaluations = 1
    all_rows = list(nominal_rows)

    study_summaries = []
    for study, docs in planned:
        t0 = time.perf_counter()
        study_dir = out_dir / "studies" / study["name"]
        values = study["values"]
        unit = study["unit"] if study["unit"] in SWEEP_UNITS else None
        spec = {"description": f"{config['workflow']}: one-parameter study of {study['name']} "
                               f"({config['design']} design, other inputs as in nominal/project.json)",
                "parameters": [{"name": study["name"], "unit": unit, "values": values}],
                "max_evaluations": len(values)}
        summary = sp.run_sweep(spec, study_dir, base=nominal_doc, base_path=nominal_dir / "project.json",
                               build=lambda p, docs=docs, name=study["name"]: docs[p[name]][0])
        evaluations += len(values)
        cf_rows, notable, analyses = [], [], {}
        for row in summary["rows"]:
            sample = wc.read_json(study_dir / "samples" / row["sample_id"] / "sample.json")
            value = row[f"param:{study['name']}"]
            result = config["checks"](docs[value][1], sample)
            analyses[row["sample_id"]] = result.get("analysis")
            for item in result["rows"]:
                cf_rows.append({"sample_id": row["sample_id"], "parameter": study["name"], "parameter_value": value,
                                "parameter_unit": study["unit"], **item})
            if sample["outcome"] != "PASS" or sample.get("warnings"):
                notable.append(_notable({"value": value}, sample, config.get("analysis_keys"),
                                        result.get("analysis")))
        wc.write_json(study_dir / "closed_form.json", {"rows": cf_rows, "analysis": analyses,
                                                       "reference": config["reference_method"]})
        wc.write_csv(study_dir / "closed_form.csv", cf_rows, CLOSED_FORM_COLUMNS)
        all_rows.extend(cf_rows)
        paths = [nominal_run, study_dir] if nominal_run else [study_dir]
        comparison = cr.compare(paths, reference="nominal" if nominal_run else None)
        compare_files = cr.write_outputs(comparison, study_dir / "compare")
        plotted = _changing_targets(summary["rows"], target_order, record)[:max(0, max_plots)]
        plots, note = _plot_sweep(study_dir, plotted, study_dir / "plots")
        totals = closed_form_totals(cf_rows)
        report = br.build_report(
            [study_dir], out_dir=study_dir / "report", plots=[study_dir / "plots"] if plots else None,
            sections=[{"title": "Independent closed-form check", "lines": [
                f"Reference: {config['reference_method']}. Compared {totals['compared']} values, "
                f"{totals['agreed']} within tolerance, {totals['not_compared']} not compared (no Engine value).", "",
                *_closed_form_table(cf_rows), "", "Data: [closed_form.csv](../closed_form.csv), "
                "[closed_form.json](../closed_form.json)."]},
                {"title": "Comparison with the nominal run", "lines": [
                    f"Targets and planes aligned by ID, deltas against the nominal run: "
                    f"[compare.json](../compare/compare.json), [compare.csv](../compare/compare.csv)."]}],
            artifacts=[(study_dir / "closed_form.csv", "closed-form check"),
                       (study_dir / "closed_form.json", "closed-form check"),
                       (Path(compare_files["json"]), "comparison with nominal"),
                       (Path(compare_files["csv"]), "comparison with nominal")])
        counts = summary["counts"]
        study_summaries.append({
            "parameter": study["name"], "unit": study["unit"], "values": values,
            "counts": counts["by_outcome"],
            "pass_fraction": f"{counts['pass_fraction']['numerator']}/{counts['pass_fraction']['denominator']}",
            "notable": notable, "closed_form": totals, "plots": [p["target"] for p in plots],
            "plots_note": note, "seconds": round(time.perf_counter() - t0, 3),
            "engine_seconds": round(sum(r["seconds"] for r in summary["rows"]), 3),
            "paths": {"study": wc.relative(study_dir, out_dir), "table": wc.relative(study_dir / "table.csv", out_dir),
                      "report": wc.relative(report["paths"]["markdown"], out_dir),
                      "compare": wc.relative(compare_files["json"], out_dir),
                      "closed_form": wc.relative(study_dir / "closed_form.csv", out_dir)}})

    totals = closed_form_totals(all_rows)
    nominal = {"outcome": record["outcome"], "status": record["status"],
               "execution": record.get("execution"), "requirements": record.get("requirements"),
               "targets": [{k: t.get(k) for k in ("id", "value", "unit", "status", "assessment", "quantity_state", "power_upper_bound_W")} for t in record["targets"]],
               "model_scope_issues": sorted({i.get("code") for i in record["model_scope_issues"]}),
               "out_of_model_issues": sorted({i.get("code") for i in record.get("out_of_model_issues") or []}),
               "warnings": record["warnings"], "error": record["error"],
               "identity": record["identity"], "run_dir": wc.relative(nominal_run, out_dir) if nominal_run else None,
               "analysis": checks.get("analysis")}
    summary = {"status": record["outcome"], "workflow": config["workflow"], "design": config["design"],
               "inputs": inputs, "nominal": nominal, "closed_form": totals,
               "closed_form_reference": config["reference_method"], "studies": study_summaries,
               "evaluations": evaluations, "engine": wc.engine_identity(),
               "catalog": {"catalog_version": nominal["identity"].get("catalog_version")},
               "installed_catalog": wc.catalog_identity(),
               "environment": wc.environment(), "provenance": config["info"]["provenance"],
               "compact_analysis": bool(config.get("compact_analysis"))}
    extra = config.get("after_studies")
    if extra is not None:
        summary.update(extra(out_dir, inputs, nominal_doc, record))
        summary["evaluations"] = evaluations + summary.get("extra_evaluations", 0)
    if nominal_run is not None:
        report = br.build_report(
            [nominal_run], out_dir=out_dir / "report", project=nominal_dir / "project.json",
            plots=[Path(power_plot["png"])] if power_plot else None,
            sections=_top_sections(config, summary, out_dir), artifacts=_top_artifacts(summary, out_dir))
        summary["paths"] = {"out": out_dir.as_posix(), "workflow": "workflow.json",
                            "report": wc.relative(report["paths"]["markdown"], out_dir),
                            "nominal_run": nominal["run_dir"], "plots_note": plot_note}
    else:
        summary["paths"] = {"out": out_dir.as_posix(), "workflow": "workflow.json", "report": None,
                            "nominal_run": None, "report_note": "no report: the nominal design was refused"}
    summary["seconds"] = round(time.perf_counter() - started, 3)
    wc.write_json(out_dir / "workflow.json", summary)
    return summary


def _top_sections(config, summary, out_dir):
    inputs = config["info"]["parameters"]
    lines = ["| input | value | unit | role | meaning |", "| --- | --- | --- | --- | --- |"]
    for name, value in summary["inputs"].items():
        info = inputs[name]
        if isinstance(value, float):
            shown = f"{value:.12g}"
        elif isinstance(value, (tuple, list)):
            shown = ", ".join(f"{v:.12g}" for v in value)
        else:
            shown = str(value)
        lines.append(f"| {name} | {shown} | {info['unit'] or '-'} | {info['role']} | "
                     f"{info['meaning'].replace('|', '&#124;')} |")
    sections = [{"title": "Design inputs", "lines": [
        f"Design `{config['design']}` of examples/optical_designs.py: {config['info']['summary']} "
        f"Model basis: {config['info']['model_basis']}. Frames: {config['info']['frames']}.", "", *lines]}]
    nominal_cf = wc.read_json(out_dir / "nominal" / "closed_form.json")
    sections.append({"title": "Independent closed-form check (nominal)", "lines": [
        f"Reference: {config['reference_method']}.", "", *_closed_form_table(nominal_cf["rows"]), "",
        "Data: [nominal/closed_form.csv](../nominal/closed_form.csv). All samples: "
        f"{summary['closed_form']['compared']} values compared, {summary['closed_form']['agreed']} within tolerance, "
        f"{summary['closed_form']['not_compared']} not compared (no Engine value)."]})
    if summary["studies"]:
        rows = ["| parameter | unit | values | PASS | FAIL | UNKNOWN | OUT_OF_MODEL | ERROR | report |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for s in summary["studies"]:
            c = s["counts"]
            rows.append(f"| {s['parameter']} | {s['unit'] or '-'} | {', '.join(f'{v:.6g}' for v in s['values'])} | "
                        f"{c['PASS']} | {c['FAIL']} | {c['UNKNOWN']} | {c['OUT_OF_MODEL']} | {c['ERROR']} | "
                        f"[report](../{s['paths']['report']}) / [table](../{s['paths']['table']}) |")
        notes = []
        for s in summary["studies"]:
            for n in s["notable"]:
                targets = n["targets"] if isinstance(n["targets"], str) else ", ".join(
                    f"{k} {v}" for k, v in n["targets"].items())
                codes = ", ".join(n.get("codes", [])) or "-"
                notes.append(f"- {s['parameter']} = {n['value']:.6g} {s['unit'] or ''}: {n['outcome']} (run status "
                             f"{n['status']}; targets not PASS: {targets}; codes {codes})")
        sections.append({"title": "Studies", "lines": [
            "Each study changes one input of the nominal design; PASS counts use all requested samples as the "
            "denominator.", "", *rows, "", "Samples that are not a clean PASS:", "", *(notes or ["- none"])]})
    for item in summary.get("report_sections") or []:
        sections.append(item)
    return sections


def _top_artifacts(summary, out_dir):
    files = [(out_dir / "nominal" / "closed_form.csv", "closed-form check (nominal)"),
             (out_dir / "nominal" / "sample.json", "nominal sample record")]
    for s in summary["studies"]:
        files.append((out_dir / s["paths"]["report"], f"study report {s['parameter']}"))
        files.append((out_dir / s["paths"]["table"], f"study table {s['parameter']}"))
    for path, role in summary.get("report_artifacts") or []:
        files.append((Path(path), role))
    return files


def compact(summary):
    """Bounded stdout document of a workflow summary."""
    studies = []
    for s in summary["studies"]:
        cf = s["closed_form"]
        studies.append({"parameter": s["parameter"], "unit": s["unit"], "values": _short(s["values"]),
                        "counts": {k: v for k, v in s["counts"].items() if v}, "pass_fraction": s["pass_fraction"],
                        "notable": _short(s["notable"][:MAX_NOTABLE]),
                        "notable_omitted": max(0, len(s["notable"]) - MAX_NOTABLE),
                        "closed_form": {"compared": cf["compared"], "agreed": cf["agreed"],
                                        "not_compared": cf["not_compared"]},
                        "report": s["paths"]["report"]})
    nominal = summary["nominal"]
    cf = summary["closed_form"]
    out = {"status": summary["status"], "workflow": summary["workflow"], "design": summary["design"],
           "inputs": _short(summary["inputs"]),
           "nominal": {"outcome": nominal["outcome"], "status": nominal["status"],
                       "execution": nominal.get("execution"), "requirements": nominal.get("requirements"),
                       "targets": _short(nominal["targets"]), "model_scope_issues": nominal["model_scope_issues"],
                       "warnings": nominal["warnings"]},
           "closed_form": {"reference": summary["closed_form_reference"], "compared": cf["compared"],
                           "agreed": cf["agreed"], "not_compared": cf["not_compared"],
                           "max_abs_diff": _short({m: v["max_abs_diff"] for m, v in cf["by_method"].items()}),
                           "disagreements": _short(cf["disagreements"])},
           "studies": studies, "evaluations": summary["evaluations"], "seconds": summary["seconds"],
           "engine_build_id": summary["engine"]["engine_build_id"],
           "catalog_version": summary["catalog"]["catalog_version"], "paths": summary["paths"]}
    if nominal.get("error"):
        out["nominal"]["error"] = nominal["error"]
    if nominal.get("analysis") and summary.get("compact_analysis"):
        out["nominal"]["analysis"] = _short(nominal["analysis"])
    for key, source in (summary.get("compact_extra") or {}).items():
        out[key] = _short(summary[source])
    return out


def main(config, defaults, argv=None, *, description, extra_options=None):
    """CLI of a workflow script: options from DESIGN_INFO, studies, run, compact stdout (exit per EXIT_CODES)."""
    def workflow():
        parser = wc.ArgumentParser(description=description, formatter_class=argparse.RawDescriptionHelpFormatter)
        add_design_options(parser, config["info"], defaults, config.get("choices"))
        add_study_options(parser, config["default_studies"], config["max_evaluations"])
        if extra_options is not None:
            extra_options(parser)
        args = parser.parse_args(argv)
        if args.list_parameters:
            return describe(config, defaults)
        if args.out is None:
            raise wc.WorkflowError("BAD_ARGUMENT", "--out DIR is required")
        if args.max_evaluations < 1:
            raise wc.WorkflowError("BAD_ARGUMENT", "--max-evaluations must be at least 1")
        parameters = config["info"]["parameters"]
        texts = [] if args.no_studies else (args.study or config["default_studies"])
        studies = [parse_study(text, parameters) for text in texts]
        if len({s["name"] for s in studies}) != len(studies):
            raise wc.WorkflowError("STUDY_INVALID", "each parameter may be studied once per run")
        run_config = dict(config)
        if config.get("configure") is not None:
            run_config = config["configure"](run_config, args)
        summary = run_workflow(run_config, inputs_from_args(args, config["info"]), studies, args.out,
                               max_evaluations=args.max_evaluations, max_plots=args.max_plots,
                               overwrite=args.overwrite)
        return compact(summary)

    return wc.emit(workflow, EXIT_CODES)
