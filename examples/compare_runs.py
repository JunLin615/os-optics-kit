"""Compare saved runs, or the samples of a sweep study, without rerunning anything.

Usage:
  python examples/compare_runs.py RUN_DIR RUN_DIR ... [--project CURRENT.json] [--labels a,b,...]
                                  [--reference LABEL] [--out DIR]
  python examples/compare_runs.py STUDY_DIR [--project CURRENT.json] [--out DIR]

Each argument is a run directory (it holds manifest.json) or a study directory written by sweep_parameters.py (its
samples become the compared runs, labelled by sample id; a sample that was refused or not run stays in the
comparison as an ERROR entry with missing data). A run directory named "run" is labelled by its parent directory.

What is compared, by ID:
* targets: value, unit and status per run, the difference to the reference run (first run, or --reference) when
  both have exact applicable values in the same unit; a target absent from a run is listed with "missing": true;
* observation planes: received power (W) and number of arriving beams per run, missing planes listed;
* inputs: every field of each run's project.json, keyed by object ID (pose:<id>.position_mm, source:<id>.<field>,
  binding:<id>.optical_sku / .catalog_version / .optical_model_id, component:<id>.<field>,
  observation:<id>.<field>, target:<id>.<field>, model:<id>.<path>, material:<id>.<path>, coating:<id>.<path>,
  project.<field>), plus each run's catalog release and Engine build. Only inputs that differ are listed, with the
  unit of the field name. inputs_compared states the result: the number of differing inputs, or that the Projects
  are identical (equal project hashes), or that the project hashes differ although no field does;
* outcome class per run (ERROR, OUT_OF_MODEL, UNKNOWN, FAIL, PASS, NOT_APPLICABLE), decided by the study's selected targets for
  study samples and by all targets for run directories (--targets overrides both; an id in no run is refused),
  with model-scope findings, warnings, and target issue codes retained independently; only selected-target
  applicability determines the OUT_OF_MODEL shorthand;
* freshness, when --project is given: CURRENT, HISTORICAL or UNDETERMINED (Engine reasons, including PHYSICS_CHANGED,
  TARGETS_CHANGED and RUN_UNVERIFIED) for every run; the outcome of a historical run is labelled e.g. "HISTORICAL PASS".

Writes DIR/compare.json (everything) and DIR/compare.csv (long format: section, id, field, unit, run, value,
status, assessment, quantity_state, exact, power_complete, power_upper_bound_W, delta, reference) when --out is given. Stdout: one compact JSON document (at most eight runs and ten input
differences in full; the files hold everything). Exit 0 compared, 4 invalid input.
"""

from __future__ import annotations

import argparse
from pathlib import Path

if __package__:
    from . import workflow_common as wc
else:
    import workflow_common as wc

MAX_RUNS_SHOWN = 8
MAX_DIFFERENCES_SHOWN = 10


def expand_inputs(paths, labels=None):
    """``[(label, run_dir or None, sample record or None)]`` for run directories and study directories."""
    entries = []
    for path in [Path(p) for p in paths]:
        if (path / "manifest.json").is_file() or (path / "local_validation.json").is_file():
            name = path.resolve().parent.name if path.name == "run" else path.name  # samples/<id>/run -> <id>
            entries.append((name, path, None))
        elif (path / "summary.json").is_file() and (path / "samples").is_dir():
            for sample_dir in sorted(p for p in (path / "samples").iterdir() if p.is_dir()):
                record = wc.read_json(sample_dir / "sample.json") if (sample_dir / "sample.json").is_file() else None
                run_dir = sample_dir / "run"
                entries.append((sample_dir.name, run_dir if (run_dir / "manifest.json").is_file() else None, record))
        else:
            raise wc.WorkflowError("RUN_NOT_READABLE", f"{path} is neither a run directory nor a study directory",
                                   path=path.as_posix())
    if labels:
        if len(labels) != len(entries):
            raise wc.WorkflowError("BAD_ARGUMENT", f"{len(labels)} labels for {len(entries)} runs")
        entries = [(label, run_dir, record) for label, (_old, run_dir, record) in zip(labels, entries)]
    seen = set()
    for label, _run_dir, _record in entries:
        if label in seen:
            raise wc.WorkflowError("BAD_ARGUMENT", f"duplicate run label {label!r}; use --labels")
        seen.add(label)
    return entries


#: Project collections compared object by object: (key, id field, key prefix).
COLLECTIONS = (("components", "instance_id", "component"), ("observations", "observation_id", "observation"),
               ("targets", "target_id", "target"), ("optical_models", "model_id", "model"),
               ("model_materials", "material_id", "material"), ("model_coatings", "coating_id", "coating"))
#: Component fields with a short key prefix: nominal pose, source model, catalog product / declared model.
COMPONENT_PREFIXES = {"nominal_pose": "pose", "source_model": "source", "product": "binding"}
#: A list value longer than this (JSON characters) is compared and shown as its sha256 prefix and length.
MAX_VALUE_CHARS = 200


def _leaf(value):
    value = wc.canonical_numbers(value)
    if isinstance(value, list) and len(wc.canonical_json(value)) > MAX_VALUE_CHARS:
        return {"sha256": wc.canonical_digest(value)[:16], "length": len(value)}
    return value


def _flatten(value, prefix, out):
    if isinstance(value, dict) and value:
        for key in sorted(value):
            _flatten(value[key], f"{prefix}.{key}", out)
    else:
        out[prefix] = _leaf(value)
    return out


def project_inputs(project):
    """Flat ``{key: value}`` of the whole Project document (module docstring).

    Objects are keyed by their ID: ``pose:<id>.position_mm``, ``source:<id>.<field>``, ``binding:<id>.optical_sku``
    / ``.catalog_version`` / ``.optical_model_id``, ``component:<id>.<field>``, ``observation:<id>.<field>`` (pose
    fields directly, e.g. ``observation:<id>.position_mm``), ``target:<id>.<field>``, ``model:<id>.<path>``,
    ``material:<id>.<path>``, ``coating:<id>.<path>``; every other top-level field as ``project.<path>``. Nested
    objects are flattened to their leaves; lists are compared whole (long lists by sha256 prefix and length);
    integers compare equal to the same float."""
    out = {}
    collections = {key for key, _id, _prefix in COLLECTIONS}
    for key, id_field, prefix in COLLECTIONS:
        for item in project.get(key) or []:
            if not isinstance(item, dict):
                continue
            ident = item.get(id_field)
            for field in sorted(item):
                if field == id_field:
                    continue
                value = item[field]
                if key == "components" and field in COMPONENT_PREFIXES:
                    _flatten(value, f"{COMPONENT_PREFIXES[field]}:{ident}", out)
                elif key == "components" and field == "optical_model_id":
                    out[f"binding:{ident}.optical_model_id"] = value
                elif key == "observations" and field == "pose":
                    _flatten(value, f"observation:{ident}", out)
                else:
                    _flatten(value, f"{prefix}:{ident}.{field}", out)
    for field in sorted(project):
        if field not in collections:
            _flatten(project[field], f"project.{field}", out)
    return out


TARGET_VALUE_FIELDS = ("minimum", "maximum", "target_value", "tolerance_abs")


def _key_unit(key, entries=()):
    """Unit of an input key: the field-name suffix, or for target bounds the target's own unit."""
    head, _, field = key.rpartition(".")
    if head.startswith("target:") and field in TARGET_VALUE_FIELDS:
        units = {e["inputs"].get(f"{head}.unit") for e in entries} - {None}
        return units.pop() if len(units) == 1 else None
    return wc.field_unit(field)


def load_entry(label, run_dir, record, selected=None):
    """One compared run: outcome (decided by ``selected`` target ids, default all), targets, warnings, inputs."""
    entry = {"label": label, "run_dir": None if run_dir is None else Path(run_dir).as_posix()}
    if run_dir is None:
        entry.update({"outcome": "ERROR", "status": None, "targets": [], "observations": {}, "inputs": {},
                      "model_scope_issues": [], "out_of_model_issues": [], "warnings": [], "identity": (record or {}).get("identity") or {},
                      "error": (record or {}).get("error") or {"code": "RUN_MISSING"}, "power_budget": None})
        return entry
    from os_optics.results import load_run

    summary, fields = wc.read_saved_run(run_dir, selected)
    results = load_run(run_dir, verify=False)
    entry.update({"outcome": fields["outcome"], "status": fields["status"], "targets": fields["targets"],
                  "execution": fields["execution"], "requirements": fields["requirements"],
                  "model_scope_issues": fields["model_scope_issues"],
                  "out_of_model_issues": fields["out_of_model_issues"], "warnings": fields["warnings"],
                  "identity": fields["identity"], "power_budget": summary.get("power_budget"),
                  "observations": {m["observation_id"]: {"total_power_W": m["total_power_W"],
                                                         "arrival_count": m["arrival_count"], "status": m["status"],
                                                         "quantity_state": m["quantity_state"],
                                                         "power_complete": m["power_complete"],
                                                         "power_upper_bound_W": m.get("power_upper_bound_W")}
                                   for m in results.monitors()},
                  "inputs": project_inputs(wc.read_json(Path(run_dir) / "project.json"))})
    entry["inputs"]["run.catalog_version"] = entry["identity"].get("catalog_version")
    entry["inputs"]["run.engine_build_id"] = entry["identity"].get("engine_build_id")
    return entry


def _ordered_union(lists):
    out = []
    for items in lists:
        for item in items:
            if item not in out:
                out.append(item)
    return out


def compare(paths, *, labels=None, reference=None, project=None, targets=None):
    """Comparison document (module docstring). ``project``: current Project document or path for freshness.

    ``targets``: target ids deciding every run's outcome (default: a study sample uses the study's selected targets,
    a run directory all of its targets); an id that no run has is refused (TARGET_UNKNOWN)."""
    entries = []
    for label, run_dir, record in expand_inputs(paths, labels):
        selected = targets if targets is not None else (record or {}).get("selected_targets")
        entry = load_entry(label, run_dir, record, selected)
        entry["selected_targets"] = selected
        entries.append(entry)
    if not entries:
        raise wc.WorkflowError("BAD_ARGUMENT", "nothing to compare")
    if targets is not None:
        known = sorted({t["id"] for e in entries for t in e["targets"]})
        unknown = [t for t in targets if t not in known]
        if unknown and known:
            raise wc.WorkflowError("TARGET_UNKNOWN", f"targets {unknown} are in none of the runs; known: {known}",
                                   unknown=unknown, known=known)
    names = [e["label"] for e in entries]
    ref = reference or names[0]
    if ref not in names:
        raise wc.WorkflowError("BAD_ARGUMENT", f"reference {ref!r} is not one of the runs {names}")
    ref_entry = entries[names.index(ref)]
    if project is not None:
        for entry in entries:
            if entry["run_dir"] is None:
                entry["freshness"] = {"freshness": "HISTORICAL", "reasons": ["RUN_UNVERIFIED"]}
            else:
                entry["freshness"] = wc.freshness(project, entry["run_dir"])
            entry["outcome_label"] = entry["outcome"] if entry["freshness"]["freshness"] == "CURRENT" else \
                f"{entry['freshness']['freshness']} {entry['outcome']}"
    target_rows = []
    for target_id in _ordered_union([[t["id"] for t in e["targets"]] for e in entries]):
        row = {"id": target_id, "runs": {}}
        ref_target = next((t for t in ref_entry["targets"] if t["id"] == target_id), None)
        for entry in entries:
            target = next((t for t in entry["targets"] if t["id"] == target_id), None)
            if target is None:
                row["runs"][entry["label"]] = {"missing": True}
                continue
            row.setdefault("metric", target["metric"])
            row.setdefault("unit", target["unit"])
            cell = {key: target.get(key) for key in ("value", "unit", "status", "assessment", "quantity_state",
                                                       "applicability_issue_codes", "power_upper_bound_W", "power_complete")}
            cell["exact"] = wc.target_value_is_exact(target)
            if ref_target is not None and wc.target_value_is_exact(target) and \
                    wc.target_value_is_exact(ref_target) and target["unit"] == ref_target["unit"]:
                cell["delta"] = target["value"] - ref_target["value"]
            row["runs"][entry["label"]] = cell
        target_rows.append(row)
    observation_rows = []
    for obs_id in _ordered_union([list(e["observations"]) for e in entries]):
        observation_rows.append({"id": obs_id, "unit": "W", "runs": {
            e["label"]: e["observations"].get(obs_id) or {"missing": True} for e in entries}})
    differences = []
    compared = [e for e in entries if e["run_dir"] is not None]
    for key in _ordered_union([list(e["inputs"]) for e in compared]):
        values = {e["label"]: e["inputs"].get(key, "<missing>" if e["run_dir"] else None) for e in entries}
        present = {wc.canonical_json(e["inputs"].get(key, "<missing>")) for e in compared}
        if len(present) > 1:
            differences.append({"input": key, "unit": _key_unit(key, compared), "values": values})
    hashes = {e["label"]: e["identity"].get("project_hash") for e in compared}
    equal = len(set(hashes.values())) <= 1
    if differences:
        statement = f"{len(differences)} inputs differ"
    elif equal:
        statement = "no input differs: the runs have identical Projects (equal project hashes)"
    else:
        statement = ("the project hashes differ although no field of the runs' project.json files differs (a run "
                     "directory may hold another run's project.json)")
    selected = {wc.canonical_json(e["selected_targets"]) for e in entries}
    runs = []
    for entry in entries:
        item = {"label": entry["label"], "outcome": entry["outcome"], "status": entry["status"],
                "model_scope_issues": sorted({i.get("code") for i in entry["model_scope_issues"]}),
                "out_of_model_issues": sorted({i.get("code") for i in entry.get("out_of_model_issues") or []}),
                "warnings": list(entry.get("warnings") or []),
                "target_issue_codes": {t["id"]: list(t["issue_codes"]) for t in entry["targets"]
                                       if t.get("issue_codes")},
                "run_dir": entry["run_dir"], "identity": entry["identity"]}
        for key in ("freshness", "outcome_label", "error", "power_budget", "execution", "requirements"):
            if entry.get(key) is not None:
                item[key] = entry[key]
        runs.append(item)
    return {"status": "COMPLETED", "kind": "opensci-example.run-comparison", "reference": ref,
            "selected_targets": entries[0]["selected_targets"] if len(selected) == 1 else "per run", "runs": runs,
            "targets": target_rows, "observations": observation_rows, "input_differences": differences,
            "inputs_compared": {"runs": len(compared), "fields": len(_ordered_union([list(e["inputs"])
                                                                                       for e in compared])),
                                "project_hashes": hashes, "project_hashes_equal": equal, "statement": statement}}


def csv_rows(document):
    rows = []
    for row in document["targets"]:
        for label, cell in row["runs"].items():
            rows.append({"section": "target", "id": row["id"], "field": row.get("metric"), "unit": row.get("unit"),
                         "run": label, "value": cell.get("value"), "status": cell.get("status") or (
                             "MISSING" if cell.get("missing") else None),
                         "delta": cell.get("delta"), "reference": document["reference"],
                         "assessment": cell.get("assessment"), "quantity_state": cell.get("quantity_state"),
                         "exact": cell.get("exact"), "power_complete": cell.get("power_complete"),
                         "power_upper_bound_W": cell.get("power_upper_bound_W")})
    for row in document["observations"]:
        for label, cell in row["runs"].items():
            rows.append({"section": "observation", "id": row["id"], "field": "total_power_W", "unit": "W",
                         "run": label, "value": cell.get("total_power_W"),
                         "status": "MISSING" if cell.get("missing") else cell.get("status"),
                         "quantity_state": cell.get("quantity_state"), "power_complete": cell.get("power_complete"),
                         "power_upper_bound_W": cell.get("power_upper_bound_W")})
    for diff in document["input_differences"]:
        for label, value in diff["values"].items():
            rows.append({"section": "input", "id": diff["input"], "field": diff["input"].split(".")[-1],
                         "unit": diff["unit"], "run": label, "value": value})
    for run in document["runs"]:
        rows.append({"section": "outcome", "id": run["label"], "field": "outcome", "run": run["label"],
                     "value": run.get("outcome_label", run["outcome"]), "status": run["status"]})
    return rows


CSV_COLUMNS = ["section", "id", "field", "unit", "run", "value", "status", "assessment", "quantity_state", "exact", "power_complete", "power_upper_bound_W", "delta", "reference"]


def write_outputs(document, out_dir):
    out_dir = Path(out_dir)
    json_path = wc.write_json(out_dir / "compare.json", document)
    csv_path = wc.write_csv(out_dir / "compare.csv", csv_rows(document), CSV_COLUMNS)
    return {"json": json_path.as_posix(), "csv": csv_path.as_posix()}


def compact(document, paths=None):
    runs = document["runs"]
    shown = runs[:MAX_RUNS_SHOWN]
    labels = [r["label"] for r in shown]
    return {
        "status": document["status"], "reference": document["reference"], "run_count": len(runs),
        "runs": [{key: r.get(key) for key in ("label", "outcome", "outcome_label", "status", "model_scope_issues", "execution", "requirements")
                  if r.get(key) is not None}
                 | ({"out_of_model_issues": r["out_of_model_issues"]} if r.get("out_of_model_issues") else {})
                 | ({"warnings": r["warnings"]} if r.get("warnings") else {})
                 | ({"freshness": r["freshness"]["freshness"], "reasons": r["freshness"]["reasons"]}
                    if r.get("freshness") else {})
                 for r in shown],
        "runs_omitted": max(0, len(runs) - MAX_RUNS_SHOWN),
        "targets": [{"id": t["id"], "unit": t.get("unit"),
                     "runs": {label: t["runs"][label] for label in labels}} for t in document["targets"]],
        "observations_W": [{"id": o["id"], "runs": {label: o["runs"][label] for label in labels}}
                           for o in document["observations"]],
        "inputs_compared": document["inputs_compared"]["statement"],
        "input_differences": document["input_differences"][:MAX_DIFFERENCES_SHOWN],
        "input_differences_omitted": max(0, len(document["input_differences"]) - MAX_DIFFERENCES_SHOWN),
        "paths": paths,
    }


def main(argv=None):
    def workflow():
        parser = wc.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
        parser.add_argument("paths", nargs="+", type=Path, help="Run directories or one/more study directories")
        parser.add_argument("--project", type=Path, help="Current Project JSON: report each run's freshness")
        parser.add_argument("--labels", help="Comma-separated run labels (default: directory / sample names)")
        parser.add_argument("--reference", help="Label of the reference run for deltas (default: the first)")
        parser.add_argument("--targets", help="Comma-separated target ids deciding the outcome (default: the study's "
                            "selected targets for study samples, all targets for run directories)")
        parser.add_argument("--out", type=Path, help="Directory for compare.json and compare.csv")
        args = parser.parse_args(argv)
        document = compare(args.paths, labels=args.labels.split(",") if args.labels else None,
                           reference=args.reference, project=args.project,
                           targets=args.targets.split(",") if args.targets else None)
        paths = write_outputs(document, args.out) if args.out else None
        return compact(document, paths)

    return wc.emit(workflow)


if __name__ == "__main__":
    raise SystemExit(main())
