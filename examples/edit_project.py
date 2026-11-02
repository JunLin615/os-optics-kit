"""Edit a Project by object ID with explicit units; validate with the Engine; save it as a new file.

Usage:
  python examples/edit_project.py BASE.json --edits EDITS.json --out NEW.json [--run RUN_DIR]
  python examples/edit_project.py BASE.json --describe [--ids L2,focus]

EDITS.json is a JSON list of operations (or {"ops": [...]}). The operations are the Designer's ``updateProject``
vocabulary, applied in order to a copy of BASE; ``changes`` objects are merged and a ``null`` value removes a field:

  set_component_pose     {instance_id, position_mm?, quaternion_wxyz?}     absolute nominal pose, design frame
  set_source_model       {instance_id, changes}   e.g. {"wavelength_nm": 532, "power_w": {"value": 5, "unit": "mW"}}
  set_observation_pose   {observation_id, position_mm?, quaternion_wxyz?} (device frame when device_instance_id)
  set_observation        {observation_id, changes}
  set_target             {target_id, changes}     bounds in the target's unit (changes.unit when given)
  set_component_product  {instance_id, optical_sku, catalog_version?}
  set_component_model    {instance_id, optical_model_id, custom_part_key?, description?}
  set_retained_dofs      {instance_id, retained_dofs: [{axis, control}]}   set_retained_dof {instance_id, axis, control|null}
  set_design_intent      {design_intent: LAB_ASSEMBLY|OPTICAL_SYSTEM|null}
  set_compute_budget     {compute_budget: {max_paths, max_trace_steps, max_interactions_per_path, max_interference_pairs}|null}
  add_component / remove_component, add_observation / remove_observation, add_target / remove_target
  add_optical_model / update_optical_model / remove_optical_model, add_model_material / update_model_material /
  remove_model_material, add_model_coating / remove_model_coating
  transform              {selector: {ids|groups|instances|kinds}, translation_mm?, rotation?: {axis, angle_deg} |
                          {quaternion_wxyz}, frame?: "design" | {"component": ID}, pivot?: "frame_origin" |
                          "selection_centroid" | {"point_mm": [x, y, z]} | {"component": ID}}
                          rigid move computed by os_optics.composition; translation and axis are in the frame's
                          axes, so frame {"component": "L2"} with translation_mm [0, 0, 5] moves L2 5 mm along its
                          own optical axis (local +Z). Workspace frames {"group": ID} and {"instance": ID} need a
                          Designer workspace; a Project file has neither, so the Engine refuses them (FRAME_INVALID)

Units: a number is in the field's schema unit (name suffix _mm, _nm, _w/_W, _deg, _rad; target bounds in the
target's unit) or is given as {"value": x, "unit": "u"} with u in nm/um/mm/m, deg/rad/mrad/urad, W/mW/uW. Rotation
angles are degrees; quaternions [w, x, y, z] are unitless. An unknown or incompatible unit refuses the edit.

An argument that is left out is refused (BAD_ARGUMENT), as in the Designer; give null to release a DOF
(set_retained_dof control) or to remove the design intent.

Atomic and retry-safe: every operation applies to the unchanged BASE (never modified); the edited Project is checked
with json_api.check_project and written only when the Engine accepts it, as the normalized document. The change record
(NEW.changes.json, or --changes) holds every operation as given and normalized, the sha256 digest of the normalized
operations (numbers in schema units and compared as floats, so the same edit written in other units has the same
digest), before/after values of every affected field, the base and output project hashes and the output file sha256.
A repeated command whose existing record has the same base hash and operations digest, and whose output file still
matches it, reports already_applied: true and writes nothing. Any other existing output is refused unless
--overwrite. --expect-base-hash H refuses (BASE_CHANGED, with the current hash) when BASE no longer hashes to H.

--describe prints a compact index of the Project's objects (id, kind, role); with --ids it adds, per object, the
current editable values with units and frames, the supported operations, retained DOFs, transform frames and pivots,
and the model / catalog wavelength bands from public data (advisory: the Engine run decides validity).

Output: one JSON document on stdout. Exit 0 edited / already applied / described. With --run, status and exit
follow the selected requirements: 0 PASS/NOT_APPLICABLE, 1 FAIL, 2 UNKNOWN, 3 OUT_OF_MODEL_SCOPE when a selected
target is outside its applicable model; run.status keeps the Engine's aggregate status,
and a run the Engine refuses gives RUN_REFUSED (exit 4). Exit 4 also for a refused edit or invalid input (JSON
error on stderr).
"""

from __future__ import annotations

import argparse
import copy
from pathlib import Path

if __package__:
    from . import workflow_common as wc
else:
    import workflow_common as wc

TARGET_VALUE_KEYS = ("minimum", "maximum", "target_value", "tolerance_abs")
RECORD_KIND = "opensci-example.project-edit"


class EditError(wc.WorkflowError):
    """A refused edit. ``code``: PROJECT_OP_INVALID (``op_index``, ``op_code``: UNKNOWN_OP, NOT_FOUND, DUPLICATE_ID,
    BAD_ARGUMENT, IN_USE, UNIT_UNKNOWN, UNIT_INCOMPATIBLE), WORKSPACE_OPERATION_REFUSED (a transform; ``problems``),
    PROJECT_REJECTED (the Engine check; ``problems``), BASE_CHANGED, OUTPUT_EXISTS."""


def _op_error(op_code, message, index):
    return EditError("PROJECT_OP_INVALID", f"op {index}: {message}", op_index=index, op_code=op_code)


# --------------------------------------------------------------------------------------------- normalization

def _target_unit_for(unit):
    def unit_for(key):
        return unit if key in TARGET_VALUE_KEYS else wc.field_unit(key)
    return unit_for


def normalize_op(op, index, document):
    """The op with every quantity converted to its schema unit (D3); raises EditError for unknown units."""
    if not isinstance(op, dict):
        raise _op_error("BAD_ARGUMENT", "each op must be an object", index)
    from os_optics.local.project_ops import PROJECT_OPS

    name = op.get("op")
    if name not in PROJECT_OPS:
        raise _op_error("UNKNOWN_OP", f"unknown op {name!r}; known: {', '.join(PROJECT_OPS)}", index)
    try:
        if name in ("set_target", "add_target"):
            if name == "set_target":
                current = next((t for t in document.get("targets") or [] if t.get("target_id") == op.get("target_id")),
                               {})
                changes = op.get("changes") if isinstance(op.get("changes"), dict) else {}
                unit = changes.get("unit") or current.get("unit")
            else:
                unit = (op.get("target") or {}).get("unit") if isinstance(op.get("target"), dict) else None
            return wc.normalize_quantities(op, unit_for=_target_unit_for(unit))
        if name in ("add_optical_model", "update_optical_model", "add_model_material", "update_model_material",
                    "add_model_coating"):
            return copy.deepcopy(op)  # model documents use decimal strings; passed through as given
        if name == "transform":
            out = wc.normalize_quantities({k: v for k, v in op.items() if k != "rotation"})
            if "rotation" in op:
                rotation = op["rotation"]
                if isinstance(rotation, dict) and "angle_deg" in rotation:
                    rotation = {**rotation, "angle_deg": wc.to_unit(rotation["angle_deg"], "deg",
                                                                     path="rotation.angle_deg")}
                out["rotation"] = wc.normalize_quantities(rotation, path="rotation")
            return out
        return wc.normalize_quantities(op)
    except wc.WorkflowError as exc:
        if exc.code in ("UNIT_UNKNOWN", "UNIT_INCOMPATIBLE", "BAD_ARGUMENT"):
            raise EditError("PROJECT_OP_INVALID", f"op {index}: {exc}", op_index=index, op_code=exc.code,
                            **exc.details) from None
        raise


# Engine operations own edit semantics; the adapter below records display differences.

_COLLECTIONS = (("components", "instance_id", "component"), ("observations", "observation_id", "observation"),
                ("targets", "target_id", "target"), ("optical_models", "model_id", "optical_model"),
                ("model_materials", "material_id", "model_material"), ("model_coatings", "coating_id", "model_coating"))


def _flatten(value, prefix=""):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            out.update(_flatten(item, f"{prefix}.{key}" if prefix else key))
        return out or {prefix: {}}
    return {prefix: value}


def _diff(before, after):
    a, b = _flatten(before or {}), _flatten(after or {})
    return [{"path": path, "before": a.get(path), "after": b.get(path)}
            for path in sorted(set(a) | set(b)) if a.get(path) != b.get(path)]


def _affected(before, after):
    """Objects whose content differs between two documents, with their changed fields."""
    out = []
    for collection, key, kind in _COLLECTIONS:
        old = {item.get(key): item for item in before.get(collection) or []}
        new = {item.get(key): item for item in after.get(collection) or []}
        for ident in list(old) + [i for i in new if i not in old]:
            if old.get(ident) != new.get(ident):
                entry = {"kind": kind, "id": ident, "fields": _diff(old.get(ident), new.get(ident))}
                if ident not in new:
                    entry["removed"] = True
                elif ident not in old:
                    entry["added"] = True
                out.append(entry)
    rest_a = {k: v for k, v in before.items() if k not in {c for c, _, _ in _COLLECTIONS}}
    rest_b = {k: v for k, v in after.items() if k not in {c for c, _, _ in _COLLECTIONS}}
    fields = _diff(rest_a, rest_b)
    if fields:
        out.append({"kind": "project", "id": None, "fields": fields})
    return out


def ops_digest(normalized_ops):
    """sha256 of the normalized operations with every number as a float: the same edit written in other units (1 mm,
    1000 um) or as 1 / 1.0 has the same digest."""
    return wc.canonical_digest(wc.canonical_numbers(normalized_ops))


def normalize_ops(document, ops):
    if isinstance(ops, dict) and "ops" in ops:
        ops = ops["ops"]
    if not isinstance(ops, list) or not ops:
        raise EditError("PROJECT_OP_INVALID", "ops must be a non-empty array", op_index=-1, op_code="BAD_ARGUMENT")
    return list(ops), [normalize_op(op, index, document) for index, op in enumerate(ops)]


def apply_edits(document, ops, *, validate=True, expected_project_hash=None):
    """Normalize explicit units, delegate semantic edits to the Engine, and record the change.

    The Engine applies the whole batch before validation. A refused operation or final
    check leaves the original document and files unchanged.
    """
    from os_optics.local.project_ops import (
        ProjectOpError, ProjectUpdateError, apply_project_ops, update_project,
    )

    base = copy.deepcopy(document)
    given, normalized = normalize_ops(base, ops)
    changes, current = [], base
    try:
        for index, op in enumerate(normalized):
            before = current
            current = apply_project_ops(current, [op])
            changes.append({"op_index": index, "op": op["op"], "affected": _affected(before, current)})
        updated = update_project(base, normalized, expected_project_hash=expected_project_hash) if validate else None
    except (ProjectOpError, ProjectUpdateError) as exc:
        details = exc.as_dict()
        code, message = details.pop("code"), details.pop("message")
        if isinstance(exc, ProjectOpError):
            details["op_index"] = index
        raise EditError(code, message, **details) from None
    record = {"kind": RECORD_KIND, "ops": given, "normalized_ops": normalized,
              "ops_digest": ops_digest(normalized), "changes": changes, "warnings": []}
    if updated is None:
        return {"project": current, "record": record}
    checked, base_check = updated.check, wc.check(base)
    record["warnings"] = updated.warnings
    record["engine_update"] = {k: v for k, v in updated.as_dict().items() if k != "project"}
    record["base"] = {"project_hash": updated.base_project_hash, "document_sha256": wc.canonical_digest(base),
                      "engine_input_hash": base_check.get("engine_input_hash"),
                      "check": "accepted" if base_check.get("ok") else "refused"}
    record["result"] = {"project_hash": updated.project_hash, "engine_input_hash": checked["engine_input_hash"],
                        "catalog_version": checked.get("catalog_version")}
    record["engine"] = wc.engine_identity()
    return {"project": updated.project, "record": record}


def base_hash(document):
    """Engine project hash of ``document`` (None when the Engine refuses it)."""
    return wc.check(document).get("project_hash")


def _replay_key(record):
    base = record.get("base") or {}
    return base.get("project_hash") or base.get("document_sha256"), record.get("ops_digest")


def edit_file(base_path, ops, out_path, *, changes_path=None, overwrite=False, expect_base_hash=None):
    """File workflow of the CLI: read BASE, apply, write OUT + change record (atomic, retry-safe; module docstring).

    Returns ``{"status": "EDITED" | "ALREADY_APPLIED", ...}``."""
    base_path, out_path = Path(base_path), Path(out_path)
    changes_path = Path(changes_path) if changes_path else out_path.with_name(out_path.stem + ".changes.json")
    if out_path.resolve() == base_path.resolve():
        raise EditError("BAD_ARGUMENT", "--out must differ from the base file (the base is never modified)")
    if changes_path.resolve() in (base_path.resolve(), out_path.resolve()):
        raise EditError("BAD_ARGUMENT", "the change record must differ from both the base and output Project files")
    document = wc.read_json(base_path)
    base_check = wc.check(document)
    current_base = base_check.get("project_hash")
    try:
        edited = apply_edits(document, ops, expected_project_hash=expect_base_hash)
    except EditError as exc:
        if exc.code == "PROJECT_CHANGED":
            raise EditError("BASE_CHANGED", str(exc), expected_hash=expect_base_hash,
                            current_hash=exc.details.get("project_hash")) from None
        raise
    _given, normalized = normalize_ops(document, ops)
    key = (current_base or wc.canonical_digest(document), ops_digest(normalized))
    if out_path.exists() and not overwrite:
        record = wc.read_json(changes_path) if changes_path.exists() else None
        if record is None:
            raise EditError("OUTPUT_EXISTS", f"{out_path} exists without a change record; use --overwrite to "
                            "replace it", out=out_path.as_posix(), mismatch=["RECORD_MISSING"])
        mismatch = []
        if _replay_key(record)[0] != key[0]:
            mismatch.append("BASE_CHANGED")
        if record.get("ops_digest") != key[1]:
            mismatch.append("OPS_CHANGED")
        if (record.get("output") or {}).get("sha256") != wc.sha256_file(out_path):
            mismatch.append("OUTPUT_MODIFIED")
        if mismatch:
            raise EditError("OUTPUT_EXISTS", f"{out_path} holds a different edit ({', '.join(mismatch)}); use "
                            "--overwrite to replace it", out=out_path.as_posix(), mismatch=mismatch,
                            recorded_base_hash=_replay_key(record)[0], current_base_hash=key[0],
                            recorded_ops_digest=record.get("ops_digest"), ops_digest=key[1])
        return {"status": "ALREADY_APPLIED", "already_applied": True, "out": out_path.as_posix(),
                "changes": changes_path.as_posix(), "project_hash": record["result"]["project_hash"],
                "base_project_hash": key[0], "ops_digest": key[1]}
    text = wc.json_text(edited["project"])
    record = edited["record"]
    record["base"]["file"] = base_path.as_posix()
    record["output"] = {"file": out_path.as_posix(), "sha256": wc.sha256_bytes(text.encode("utf-8"))}
    record["created"] = wc.utc_now()
    wc.write_text_atomic(out_path, text)
    wc.write_text_atomic(changes_path, wc.json_text(record))
    return {"status": "EDITED", "already_applied": False, "out": out_path.as_posix(),
            "changes": changes_path.as_posix(), "project_hash": record["result"]["project_hash"],
            "base_project_hash": key[0], "ops_digest": key[1], "warnings": record["warnings"],
            "affected": [{"op_index": c["op_index"], "op": c["op"],
                          "objects": [{"kind": a["kind"], "id": a["id"], "fields": [f["path"] for f in a["fields"]]}
                                      for a in c["affected"]]} for c in record["changes"]]}


# --------------------------------------------------------------------------------------------- describe

def _rotate(q, v):
    w, x, y, z = q
    n = (w * w + x * x + y * y + z * z) ** 0.5 or 1.0
    w, x, y, z = w / n, x / n, y / n, z / n
    matrix = ((1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)),
              (2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)),
              (2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)))
    return [round(sum(matrix[r][c] * v[c] for c in range(3)), 12) + 0.0 for r in range(3)]


def _pose_view(pose, frame):
    q = pose.get("quaternion_wxyz", [1.0, 0.0, 0.0, 0.0])
    return {"frame": frame, "position_mm": pose.get("position_mm"), "quaternion_wxyz": q,
            "local_axes": {axis: _rotate(q, vec) for axis, vec in (("x", (1, 0, 0)), ("y", (0, 1, 0)),
                                                                   ("z", (0, 0, 1)))}}


def _with_units(values):
    out = {}
    for key, value in (values or {}).items():
        unit = wc.field_unit(key)
        out[key] = {"value": value, "unit": unit} if unit and not isinstance(value, dict) else value
    return out


def _catalog_band(sku):
    from os_optics.local import json_api

    reply = json_api.catalog_product(sku)
    if not reply.get("ok"):
        codes = [p.get("code") for p in reply.get("error", {}).get("problems", [])]
        return {"available": False, "codes": codes}
    product = reply["product"]
    return {"catalog_version": reply["catalog_version"],
            "working_band_nm": (product.get("specifications") or {}).get("working_band_nm"),
            "model_valid_band_nm": [m.get("valid_band_nm") for m in product.get("simulation_models") or []]}


CONVENTIONS = {
    "transform": {"frames": ["design", {"component": "<id>"}],
                  "pivots": ["frame_origin", "selection_centroid", {"point_mm": "[x, y, z] in the frame"},
                             {"component": "<id>"}],
                  "rotation": "{axis, angle_deg} or {quaternion_wxyz}; axis and translation_mm in the frame's axes"},
    "pose": "x_design = R(quaternion_wxyz) x_local + position_mm; local_axes = local x, y, z in design axes",
    "component_origin": "transmissive element: first surface vertex on axis, local +Z = optical axis; reflector or "
                        "plate: coated-face centre, local +Z = outward normal",
    "observation_plane": "local +Z = plane normal; u, v = local x, y",
    "units": "bare numbers in the field's unit (name suffix); or {value, unit}: nm/um/mm/m, deg/rad/mrad/urad, W/mW/uW",
    "retained_dofs": "design intent only (axes Tx..Rz in the component frame); they do not drive a run",
}


def describe(document, ids=None):
    """Compact object index (``ids`` None) or per-object editing facts (module docstring)."""
    comps = document.get("components") or []
    obs = document.get("observations") or []
    targets = document.get("targets") or []
    models = {m.get("model_id"): m for m in document.get("optical_models") or []}
    if ids is None:
        index = []
        for c in comps:
            binding = (c.get("product") or {}).get("optical_sku") or c.get("optical_model_id") or (
                "source_model" if c.get("source_model") else None)
            index.append({"id": c["instance_id"], "kind": "component", "role": c.get("optical_role"),
                          "binding": binding, "position_mm": (c.get("nominal_pose") or {}).get("position_mm")})
        for o in obs:
            index.append({"id": o["observation_id"], "kind": "observation",
                          "position_mm": (o.get("pose") or {}).get("position_mm"),
                          **({"device_instance_id": o["device_instance_id"]} if o.get("device_instance_id") else {})})
        for t in targets:
            index.append({"id": t["target_id"], "kind": "target", "metric": t.get("metric"), "unit": t.get("unit"),
                          "observation_id": t.get("observation_id")})
        return {"objects": index, "detail": "add --ids ID[,ID...] for editable values, units, frames and ops"}
    units = wc.target_metric_units()
    details, missing = [], []
    for ident in ids:
        c = next((x for x in comps if x.get("instance_id") == ident), None)
        o = next((x for x in obs if x.get("observation_id") == ident), None)
        t = next((x for x in targets if x.get("target_id") == ident), None)
        if c is not None:
            entry = {"id": ident, "kind": "component", "role": c.get("optical_role"),
                     "pose": _pose_view(c.get("nominal_pose") or {}, "design"),
                     "retained_dofs": c.get("retained_dofs") or [],
                     "ops": ["set_component_pose", "transform", "set_retained_dof", "set_retained_dofs",
                             "remove_component"], "applicability": {}}
            if c.get("source_model"):
                entry["source_model"] = _with_units(c["source_model"])
                entry["ops"].insert(1, "set_source_model")
            if c.get("product"):
                entry["product"] = c["product"]
                entry["ops"] += ["set_component_product", "set_component_model"]
                entry["applicability"]["catalog"] = _catalog_band(c["product"].get("optical_sku"))
            if c.get("optical_model_id"):
                model = (models.get(c["optical_model_id"]) or {}).get("model") or {}
                entry["optical_model"] = {"optical_model_id": c["optical_model_id"],
                                          "model_kind": model.get("model_kind")}
                entry["ops"] += ["set_component_model", "set_component_product", "update_optical_model"]
                entry["applicability"]["model_valid_band_nm"] = model.get("valid_band_nm")
            details.append(entry)
        elif o is not None:
            frame = {"component": o["device_instance_id"]} if o.get("device_instance_id") else "design"
            details.append({"id": ident, "kind": "observation", "pose": _pose_view(o.get("pose") or {}, frame),
                            "values": _with_units({k: v for k, v in o.items() if k not in ("pose", "observation_id")}),
                            "targets": [x["target_id"] for x in targets if x.get("observation_id") == ident],
                            "ops": ["set_observation_pose", "set_observation", "transform", "remove_observation"]})
        elif t is not None:
            entry = {"id": ident, "kind": "target", "metric": t.get("metric"), "unit": t.get("unit"),
                     "metric_unit": units.get(t.get("metric")), "observation_id": t.get("observation_id"),
                     "bounds": {k: t[k] for k in ("minimum", "maximum", "target_value", "tolerance_abs",
                                                  "tolerance_rel") if t.get(k) is not None},
                     "bounds_unit": t.get("unit"), "ops": ["set_target", "remove_target"]}
            if entry["metric_unit"] is not None and entry["metric_unit"] != t.get("unit"):
                entry["warning"] = "TARGET_UNIT_MISMATCH"
            details.append(entry)
        else:
            missing.append(ident)
    return {"objects": details, "missing": missing, "conventions": CONVENTIONS}


# --------------------------------------------------------------------------------------------- CLI

def main(argv=None):
    def workflow():
        parser = wc.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
        parser.add_argument("base", type=Path, help="Base Project JSON (never modified)")
        parser.add_argument("--edits", type=Path, help="JSON list of operations, or {\"ops\": [...]}")
        parser.add_argument("--out", type=Path, help="New Project file to write")
        parser.add_argument("--changes", type=Path, help="Change record path (default: OUT stem + .changes.json)")
        parser.add_argument("--overwrite", action="store_true", help="Replace an existing OUT holding another edit")
        parser.add_argument("--expect-base-hash", help="Refuse with BASE_CHANGED unless BASE has this project hash")
        parser.add_argument("--run", type=Path, help="Also run the edited Project into this run directory "
                            "(reused without rerunning when it already holds a CURRENT run of it)")
        parser.add_argument("--describe", action="store_true", help="Print the object index instead of editing")
        parser.add_argument("--ids", help="With --describe: comma-separated object ids to detail")
        args = parser.parse_args(argv)
        if args.describe:
            ids = [i for i in args.ids.split(",") if i] if args.ids else None
            return {"status": "DESCRIBED", **describe(wc.read_json(args.base), ids)}
        if args.edits is None or args.out is None:
            raise ValueError("--edits and --out are required (or use --describe)")
        result = edit_file(args.base, wc.read_json(args.edits), args.out, changes_path=args.changes,
                           overwrite=args.overwrite, expect_base_hash=args.expect_base_hash)
        if args.run is not None:
            result["run"] = run_edited(args.out, args.run)
            result["status"] = outcome_status(result["run"])
        return result

    return wc.emit(workflow, {**wc.WORKFLOW_EXIT_CODES, "DESCRIBED": 0, "RUN_REFUSED": 4})


def outcome_status(run):
    """Exit status of the selected requirements; execution evidence is kept separately."""
    outcome = run.get("outcome")
    if outcome == "OUT_OF_MODEL":
        return "OUT_OF_MODEL_SCOPE"
    if outcome in ("UNKNOWN", "FAIL"):
        return outcome
    return outcome or run["status"]


def run_edited(project_path, run_dir):
    """Run a saved Project into ``run_dir`` (or reuse a CURRENT run there); compact summary with the outcome."""
    from os_optics.local import LocalRunError, load_project, run_optical_project

    run_dir = Path(run_dir)
    reused = False
    if (run_dir / "local_validation.json").exists() and \
            wc.freshness(project_path, run_dir)["freshness"] == "CURRENT":
        reused = True
    else:
        try:
            run_optical_project(load_project(wc.read_json(project_path)), output_dir=run_dir)
        except LocalRunError as exc:
            return {"status": "RUN_REFUSED", "run_dir": run_dir.as_posix(),
                    "problems": [p.as_dict() for p in exc.problems]}
    summary, fields = wc.read_saved_run(run_dir)
    return {"status": summary["status"], "outcome": fields["outcome"], "reused": reused,
            "run_dir": run_dir.as_posix(), "targets": fields["targets"],
            "execution": fields["execution"], "requirements": fields["requirements"],
            "model_scope_issues": fields["model_scope_issues"], "out_of_model_issues": fields["out_of_model_issues"],
            "warnings": fields["warnings"], "identity": fields["identity"]}


if __name__ == "__main__":
    raise SystemExit(main())
