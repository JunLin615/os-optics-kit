"""Assemble a factual report (report.md + report.json) from a sweep study or saved runs, without rerunning.

Usage:
  python examples/build_report.py STUDY_DIR [--project CURRENT.json] [--plots PNG_OR_DIR ...]
                                  [--changes NEW.changes.json] [--out DIR]
  python examples/build_report.py RUN_DIR [RUN_DIR ...] [--project CURRENT.json] [--plots ...] [--changes ...]
                                  [--out DIR]

Contents: Engine and catalog identity; inputs (base project file and hash, sweep parameters with units, targets,
constraints and objective, or the compared runs' input differences); the edit's change record (every operation with
before / after values) when --changes is given; target outcomes with units and statuses; outcome counts with their
denominators; best sample or the no-feasible outcome; diagnostics (issue codes, model-scope issues, warnings,
refusal codes); freshness of every run against --project (a historical PASS is labelled HISTORICAL PASS); plots as
relative links; and the artifact list with relative paths and sha256. Numbers are copied from the saved files;
nothing is interpreted beyond the computed comparisons. From Python, ``build_report(..., sections=[{"title",
"lines"}], artifacts=[(path, role)])`` adds the caller's own sections (Markdown lines, e.g. an independent reference
comparison) after Diagnostics and lists the caller's files with the other artifacts.

Output (default DIR: STUDY_DIR/report, or ./report for run directories): report.md, report.json. The report links
use paths relative to DIR. Stdout: one JSON document with the paths and counts. Exit 0, 4 on invalid input.
"""

from __future__ import annotations

import argparse
from pathlib import Path

if __package__:
    from . import workflow_common as wc
    from .compare_runs import compare
else:
    import workflow_common as wc
    from compare_runs import compare

MAX_SAMPLE_ROWS = 40


def _is_study(path):
    return (Path(path) / "summary.json").is_file() and (Path(path) / "study.json").is_file()


def _artifact(path, out_dir, role):
    path = Path(path)
    return {"role": role, "path": wc.relative(path, out_dir), "sha256": wc.sha256_file(path),
            "bytes": path.stat().st_size}


def _plot_files(plots):
    files = []
    for item in plots or []:
        item = Path(item)
        if item.is_dir():
            files.extend(sorted(p for p in item.iterdir() if p.suffix in (".png", ".svg")))
        elif item.is_file():
            files.append(item)
            svg = item.with_suffix(".svg")
            if item.suffix == ".png" and svg.is_file() and svg not in files:
                files.append(svg)
        else:
            raise wc.WorkflowError("PLOT_NOT_FOUND", f"{item} does not exist", path=item.as_posix())
    return files


def _fmt(value):
    if isinstance(value, float):
        return f"{value:.9g}"
    if value is None:
        return "-"
    return str(value)


def _table(header, rows):
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(" --- " for _ in header) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(_fmt(v).replace("|", "/") for v in row) + " |")
    return lines


def study_section(study_dir, out_dir, project=None):
    study_dir = Path(study_dir)
    study = wc.read_json(study_dir / "study.json")
    summary = wc.read_json(study_dir / "summary.json")
    rows = summary["rows"]
    artifacts = [_artifact(study_dir / name, out_dir, role) for name, role in (
        ("study.json", "study"), ("summary.json", "summary"), ("table.csv", "table"))]
    samples = []
    for row in rows:
        sample_dir = study_dir / "samples" / row["sample_id"]
        record = wc.read_json(sample_dir / "sample.json") if (sample_dir / "sample.json").is_file() else {}
        entry = {"sample_id": row["sample_id"], "outcome": row["outcome"], "status": row["status"],
                 "parameters": record.get("parameters"), "targets": record.get("targets", []),
                 "execution": record.get("execution"), "requirements": record.get("requirements"),
                 "model_scope_issues": record.get("model_scope_issues", []),
                 "out_of_model_issues": record.get("out_of_model_issues", []), "warnings": record.get("warnings", []),
                 "error": record.get("error"), "identity": record.get("identity"),
                 "run_dir": None if row.get("run_dir") is None else wc.relative(study_dir / row["run_dir"], out_dir)}
        for name, role in (("project.json", "sample project"), ("sample.json", "sample record"),
                           ("run/manifest.json", "run manifest")):
            if (sample_dir / name).is_file():
                artifacts.append(_artifact(sample_dir / name, out_dir, f"{role} {row['sample_id']}"))
        if project is not None:
            entry["freshness"] = wc.freshness(project, sample_dir / "run") if entry["run_dir"] else \
                {"freshness": "HISTORICAL", "reasons": ["RUN_UNVERIFIED"]}
            if entry["freshness"]["freshness"] != "CURRENT":
                entry["outcome_label"] = f"{entry['freshness']['freshness']} {entry['outcome']}"
        samples.append(entry)
    best = summary["best"]
    if best is not None:
        chosen = next((s for s in samples if s["sample_id"] == best["sample_id"]), {})
        best = {**best, **({"outcome_label": chosen["outcome_label"]} if "outcome_label" in chosen else {})}
    return {"kind": "study", "study_dir": wc.relative(study_dir, out_dir),
            "base_project": study.get("base_project"), "parameters": study["normalized"]["parameters"],
            "selected_targets": study["normalized"].get("targets"), "constraints": summary["constraints"],
            "objective": summary["objective"], "budget": study.get("budget"), "stop_on": study.get("stop_on"),
            "counts": summary["counts"], "feasible": summary["feasible"], "best": best,
            "no_feasible": summary["no_feasible"], "stopped_by": summary.get("stopped_by"),
            "target_units": summary["targets"], "engine": study["engine"], "catalog": study["catalog"],
            "environment": study.get("environment"), "samples": samples}, artifacts


def runs_section(paths, out_dir, project=None):
    document = compare(paths, project=project)
    artifacts = []
    for run in document["runs"]:
        if run["run_dir"]:
            for name, role in (("manifest.json", "run manifest"), ("project.json", "run project"),
                               ("local_validation.json", "local validation")):
                if (Path(run["run_dir"]) / name).is_file():
                    artifacts.append(_artifact(Path(run["run_dir"]) / name, out_dir, f"{role} {run['label']}"))
            run["run_dir"] = wc.relative(run["run_dir"], out_dir)
    return {"kind": "runs", **{k: v for k, v in document.items() if k not in ("status", "kind")}}, artifacts


def _diagnostics(section):
    issues, scope, warnings, errors = {}, {}, {}, {}
    entries = section["samples"] if section["kind"] == "study" else section["runs"]
    for entry in entries:
        for item in [*(entry.get("model_scope_issues") or []), *(entry.get("out_of_model_issues") or [])]:
            code = item.get("code") if isinstance(item, dict) else item
            scope[code] = scope.get(code, 0) + 1
        for code in entry.get("warnings") or []:
            warnings[code] = warnings.get(code, 0) + 1
        error = entry.get("error")
        if error:
            for problem in error.get("problems") or [{"code": error.get("code")}]:
                errors[problem.get("code")] = errors.get(problem.get("code"), 0) + 1
        for target in entry.get("targets") or []:
            for code in target.get("issue_codes") or []:
                issues[code] = issues.get(code, 0) + 1
        for codes in (entry.get("target_issue_codes") or {}).values():  # compared runs: {target id: codes}
            for code in codes:
                issues[code] = issues.get(code, 0) + 1
    return {"target_issue_codes": issues, "model_scope_issues": scope, "warnings": warnings, "refusals": errors}


def build_report(paths, *, out_dir=None, project=None, plots=None, changes=None, sections=None, artifacts=None):
    """Write report.md and report.json; returns the report document.

    ``sections``: extra report sections ``{"title": str, "lines": [Markdown lines]}`` placed after Diagnostics
    (links in the lines are relative to the report directory). ``artifacts``: extra files ``(path, role)`` listed,
    with sha256, among the report's artifacts."""
    paths = [Path(p) for p in paths]
    if not paths:
        raise wc.WorkflowError("BAD_ARGUMENT", "give a study directory or run directories")
    study = len(paths) == 1 and _is_study(paths[0])
    if not study and any(_is_study(p) for p in paths):
        raise wc.WorkflowError("BAD_ARGUMENT", "give one study directory, or run directories only")
    out_dir = Path(out_dir) if out_dir else (paths[0] / "report" if study else Path("report"))
    out_dir.mkdir(parents=True, exist_ok=True)
    section, found = study_section(paths[0], out_dir, project) if study else runs_section(paths, out_dir, project)
    report = {"kind": "opensci-example.report", "generated": wc.utc_now(), "engine": wc.engine_identity(),
              "catalog": wc.catalog_identity(), "section": section, "diagnostics": _diagnostics(section)}
    if project is not None:
        found.append(_artifact(project, out_dir, "current project"))
        report["current_project"] = wc.relative(project, out_dir)
    if changes is not None:
        record = wc.read_json(changes)
        report["changes"] = {"file": wc.relative(changes, out_dir), "base": record.get("base"),
                             "result": record.get("result"), "ops_digest": record.get("ops_digest"),
                             "warnings": record.get("warnings"), "changes": record.get("changes")}
        found.append(_artifact(changes, out_dir, "change record"))
        output = (record.get("output") or {}).get("file")
        if output and Path(output).is_file():
            found.append(_artifact(output, out_dir, "edited project"))
    report["sections"] = [{"title": str(item["title"]), "lines": [str(line) for line in item.get("lines") or []]}
                          for item in sections or []]
    for path, role in artifacts or []:
        if not Path(path).is_file():
            raise wc.WorkflowError("ARTIFACT_NOT_FOUND", f"{path} does not exist", path=Path(path).as_posix())
        found.append(_artifact(path, out_dir, role))
    report["plots"] = [_artifact(p, out_dir, "plot") for p in _plot_files(plots)]
    found.extend(report["plots"])
    report["artifacts"] = found
    wc.write_json(out_dir / "report.json", report)
    text = "\n".join(render_markdown(report)) + "\n"
    with open(out_dir / "report.md", "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    report["paths"] = {"markdown": (out_dir / "report.md").as_posix(), "json": (out_dir / "report.json").as_posix()}
    return report


def _target_cells(targets):
    return "; ".join(f"{t['id']} {_fmt(t.get('value'))} {t.get('unit') or ''} {t.get('assessment')} ({t.get('status')}; quantity {t.get('quantity_state') or 'see metric'})".strip()
                     for t in targets) or "-"


def render_markdown(report):
    section = report["section"]
    lines = ["# OpenSci result report", "",
             f"Generated {report['generated']} with os-optics {report['engine']['engine_version']} "
             f"(build {report['engine']['engine_build_id']}); active catalog "
             f"{report['catalog']['catalog_version'] or 'none'}.", ""]
    lines += ["## Inputs", ""]
    if section["kind"] == "study":
        base = section.get("base_project") or {}
        lines.append(f"Study summary: [{section['study_dir']}/summary.json]({section['study_dir']}/summary.json)")
        if base:
            lines.append(f"Base project: {base.get('file') or '-'}, project hash `{base.get('project_hash')}`")
        lines.append("")
        lines += _table(["parameter", "unit", "values"], [
            [p["name"], p["unit"] or "-", ", ".join(_fmt(v) for v in p["values"])] for p in section["parameters"]])
        lines += ["", f"Selected targets: {section['selected_targets'] or 'all'}; constraints: "
                      f"{wc.canonical_json(section['constraints'])}; objective: "
                      f"{wc.canonical_json(section['objective'])}; budget: "
                      f"{wc.canonical_json(section['budget'])}.", ""]
    else:
        lines.append(f"Compared runs: {', '.join(r['label'] for r in section['runs'])} (reference "
                     f"{section['reference']}).")
        lines.append("")
        compared = section.get("inputs_compared") or {}
        if section["input_differences"]:
            labels = [r["label"] for r in section["runs"]]
            lines.append(f"Inputs that differ ({len(section['input_differences'])} of {compared.get('fields')} "
                         "compared fields of the runs' project.json files):")
            lines.append("")
            lines += _table(["input", "unit", *labels], [
                [d["input"], d["unit"] or "-", *[wc.canonical_json(d["values"].get(lab)) for lab in labels]]
                for d in section["input_differences"]])
        elif compared.get("project_hashes_equal"):
            lines.append(f"Inputs: the runs' Projects are identical ({compared.get('fields')} fields compared, equal "
                         "project hashes).")
        else:
            hashes = ", ".join(f"{label} `{digest}`"
                               for label, digest in (compared.get("project_hashes") or {}).items())
            lines.append(f"Inputs: the project hashes differ ({hashes}) although no field of the runs' project.json "
                         f"files differs ({compared.get('fields')} fields compared).")
        lines.append("")
    if "changes" in report:
        changes = report["changes"]
        lines += ["## Changes", "",
                  f"Change record [{changes['file']}]({changes['file']}); base project hash "
                  f"`{(changes['base'] or {}).get('project_hash')}` -> `{(changes['result'] or {}).get('project_hash')}`.",
                  ""]
        rows = []
        for change in changes["changes"] or []:
            for obj in change["affected"]:
                for field in obj["fields"]:
                    rows.append([change["op_index"], change["op"], f"{obj['kind']} {obj['id']}", field["path"],
                                 wc.canonical_json(field["before"]), wc.canonical_json(field["after"])])
        lines += _table(["op", "name", "object", "field", "before", "after"], rows) + [""]
        for warning in changes.get("warnings") or []:
            lines.append(f"- {warning['code']}: {warning['message']}")
        lines.append("")
    lines += ["## Outcomes", "", "Outcome shorthand describes selected requirements. Execution completion, quantity evidence, and individual assessments remain separate in the JSON records.", ""]
    if section["kind"] == "study":
        counts = section["counts"]
        lines += _table(["outcome", "samples"], [[k, v] for k, v in counts["by_outcome"].items()])
        pf, pe = counts["pass_fraction"], counts["pass_fraction_among_evaluable"]
        lines += ["", f"PASS fraction: {pf['numerator']}/{pf['denominator']} of all requested samples; among "
                      f"evaluable samples (PASS + FAIL): {pe['numerator']}/{pe['denominator']}.", ""]
        if section["best"]:
            best = section["best"]
            objective = best.get("objective") or {}
            lines.append(f"Best sample: {best['sample_id']} ({best.get('outcome_label', best['outcome'])}), parameters "
                         f"{wc.canonical_json(best['parameters'])}, {objective.get('column')} = "
                         f"{_fmt(objective.get('value'))} {objective.get('unit') or ''} ({objective.get('sense')}).")
        elif section["no_feasible"]:
            lines.append(f"No feasible sample. Failed constraints (samples): "
                         f"{wc.canonical_json(section['no_feasible']['failed_constraints'])}.")
        lines.append(f"Feasible samples: {section['feasible']}.")
        lines.append("")
        shown = section["samples"][:MAX_SAMPLE_ROWS]
        rows = []
        for s in shown:
            params = ", ".join(f"{k}={_fmt(v['value'])} {v['unit'] or ''}".strip()
                               for k, v in (s["parameters"] or {}).items())
            run = f"[run]({s['run_dir']})" if s["run_dir"] else "-"
            rows.append([s["sample_id"], s.get("outcome_label", s["outcome"]), params, _target_cells(s["targets"]),
                         (s["error"] or {}).get("code") or "-", run])
        lines += _table(["sample", "outcome", "parameters", "targets (value unit status)", "error", "run"], rows)
        if len(section["samples"]) > MAX_SAMPLE_ROWS:
            lines.append(f"\n{len(section['samples']) - MAX_SAMPLE_ROWS} more samples in "
                         f"[table.csv]({section['study_dir']}/table.csv).")
        lines.append("")
    else:
        rows = []
        for t in section["targets"]:
            for label, cell in t["runs"].items():
                rows.append([t["id"], label, _fmt(cell.get("value")) if not cell.get("missing") else "missing",
                             t.get("unit") or "-", cell.get("assessment") or "-", cell.get("quantity_state") or "see metric",
                             _fmt(cell.get("power_upper_bound_W")), cell.get("status") or "-", _fmt(cell.get("delta"))])
        lines += _table(["target", "run", "value", "unit", "assessment", "quantity", "power upper bound (W)", "status", f"delta vs {section['reference']}"], rows)
        lines += [""] + _table(["run", "outcome", "status", "model scope issues", "run directory"], [
            [r["label"], r.get("outcome_label", r["outcome"]), r["status"],
             ", ".join([*r["model_scope_issues"], *r.get("out_of_model_issues", [])]) or "-",
             f"[{r['run_dir']}]({r['run_dir']})" if r["run_dir"] else "-"] for r in section["runs"]]) + [""]
    diag = report["diagnostics"]
    lines += ["## Diagnostics", ""]
    for name, title in (("model_scope_issues", "Model-scope issues"), ("target_issue_codes", "Target issue codes"),
                        ("warnings", "Warnings"), ("refusals", "Refusals / not run")):
        values = diag[name]
        lines.append(f"- {title}: " + (", ".join(f"{k} x{v}" for k, v in values.items()) if values else "none"))
    lines.append("")
    for extra in report.get("sections") or []:
        lines += [f"## {extra['title']}", "", *extra["lines"], ""]
    if report.get("current_project"):
        lines += ["## Freshness", "", f"Against [{report['current_project']}]({report['current_project']}):", ""]
        entries = section["samples"] if section["kind"] == "study" else section["runs"]
        lines += _table(["run", "freshness", "reasons", "outcome"], [
            [e.get("sample_id") or e.get("label"), e["freshness"]["freshness"],
             ", ".join(e["freshness"]["reasons"]) or "-", e.get("outcome_label", e["outcome"])] for e in entries])
        lines.append("")
    if report["plots"]:
        lines += ["## Plots", ""]
        for plot in report["plots"]:
            if plot["path"].endswith(".png"):
                lines += [f"![{Path(plot['path']).stem}]({plot['path']})", ""]
            else:
                lines += [f"Vector version: [{plot['path']}]({plot['path']})", ""]
    lines += ["## Artifacts", ""]
    lines += _table(["role", "path", "sha256", "bytes"], [
        [a["role"], f"[{a['path']}]({a['path']})", a["sha256"][:16], a["bytes"]] for a in report["artifacts"]])
    return lines


def main(argv=None):
    def workflow():
        parser = wc.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
        parser.add_argument("paths", nargs="+", type=Path, help="One study directory, or run directories")
        parser.add_argument("--project", type=Path, help="Current Project JSON for freshness")
        parser.add_argument("--plots", nargs="*", type=Path, help="Plot files or directories to link")
        parser.add_argument("--changes", type=Path, help="Change record of an edit (edit_project.py)")
        parser.add_argument("--out", type=Path, help="Report directory")
        args = parser.parse_args(argv)
        report = build_report(args.paths, out_dir=args.out, project=args.project, plots=args.plots,
                              changes=args.changes)
        section = report["section"]
        return {"status": "COMPLETED", "kind": section["kind"], "paths": report["paths"],
                "counts": section.get("counts"), "best": section.get("best"),
                "no_feasible": section.get("no_feasible"), "diagnostics": report["diagnostics"],
                "artifacts": len(report["artifacts"]), "plots": len(report["plots"])}

    return wc.emit(workflow)


if __name__ == "__main__":
    raise SystemExit(main())
