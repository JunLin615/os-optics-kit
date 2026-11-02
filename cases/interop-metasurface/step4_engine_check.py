"""Step 4: use the fitted metalens model in OpenSci and check it (requires os-optics 0.10.0).

The step-3 ``TABULATED_THIN_LENS`` model is declared in a catalog-free Project and
traced with the same Gaussian input as the external calculation (waist at the
metasurface, x polarization). The Engine's waist position, waist radius and delivered
power are compared with

* the step-3 thin-lens prediction: the Engine must reproduce the model it was given;
* the external focus on the test set (four test wavelengths, a 25 um beam, a 1.5 deg
  field angle), which step 3 never used: the deviation must stay inside the model's
  stated errors.

The calibration set (table rows, calibration wavelengths, 20 um beam, accepted field
angles) set those errors in step 3, so its deviations are reported as an envelope, not
counted as a check. One Project with a multi-line source traces every wavelength in
one run. Outside the model's domain the Engine must not return a result as valid:

* a 450 nm source (outside the 490-580 nm rows): ``OUT_OF_MODEL_SCOPE``;
* every calibration angle beyond ``max_field_angle_deg``: ``OUT_OF_MODEL_SCOPE``;
* a target asking for a metric the model was not validated for: ``OUT_OF_MODEL_SCOPE``.

``--model per-wavelength-ideal`` runs the same comparison with one IDEAL_THIN_LENS per
wavelength (focal length and transmittance interpolated from the same rows), the
single-line equivalent of the tabulated model; that model kind cannot state a field-
angle domain, so its results beyond the angle domain are reported, not judged.

Usage: python examples/interop/metasurface/step4_engine_check.py run-interop-metasurface [--model tabulated]
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import sys

if __package__:
    from ..common import ArgumentParser, comparison_status, decimal_text, emit, environment, pose, require_engine, run_identity, write_json
    from .step1_unit_cells import CALIBRATION_NM, TABLE_NM, TEST_NM
    from .step3_fit_model import aperture_fraction, interpolate, thin_lens_focus
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from common import ArgumentParser, comparison_status, decimal_text, emit, environment, pose, require_engine, run_identity, write_json
    from step1_unit_cells import CALIBRATION_NM, TABLE_NM, TEST_NM
    from step3_fit_model import aperture_fraction, interpolate, thin_lens_focus

LENS_X_MM, HEIGHT_MM, PLANE_X_MM = 0.0, 50.0, 0.3
SOURCE_DISTANCE_MM = 1.0
MODEL_AGREEMENT_REL = 1e-6  # Engine vs the thin-lens prediction of the same model


def model_rows(model):
    return [{"wavelength_nm": float(r["wavelength_nm"]), "focal_length_um": float(r["focal_length_mm"]) * 1000,
             "transmittance": float(r["transmittance"])} for r in model["model"]["rows"]]


def project(model, mode, *, wavelengths_nm, waist_um, tilt_deg=0.0, target_metric="waist_radius_x_mm",
            band_override=None):
    """Catalog-free Project: Gaussian source -> metalens at the origin plane -> observation plane 0.3 mm behind."""
    rows = model_rows(model)
    if mode == "tabulated":
        models = [copy.deepcopy(model)]
    else:
        wavelength = wavelengths_nm[0]
        f_um, transmittance = interpolate(rows, min(max(wavelength, rows[0]["wavelength_nm"]), rows[-1]["wavelength_nm"]))
        band = band_override or (wavelength, wavelength)
        ideal = model["model"]
        models = [{"model_id": model["model_id"], "model": {
            "model_kind": "IDEAL_THIN_LENS", "model_version": "1", "focal_length_mm": decimal_text(f_um / 1000, 9),
            "transmittance": decimal_text(transmittance, 6), "clear_radius_mm": ideal["clear_radius_mm"],
            "edge_radius_mm": ideal["edge_radius_mm"], "status": "SPECIFIED",
            "valid_band_nm": {"min_nm": decimal_text(band[0]), "max_nm": decimal_text(band[1]), "status": "SPECIFIED"}}}]
    theta = math.radians(tilt_deg)
    direction = (math.cos(theta), 0.0, math.sin(theta))
    start = [LENS_X_MM - SOURCE_DISTANCE_MM * direction[0], 0.0, HEIGHT_MM - SOURCE_DISTANCE_MM * direction[2]]
    source = {"model_kind": "GAUSSIAN_BEAM", "waist_radius_mm": waist_um / 1000, "waist_position_mm": SOURCE_DISTANCE_MM,
              "m2": 1.0, "polarization": {"kind": "LINEAR", "angle_deg": 0.0}}
    if len(wavelengths_nm) == 1:
        source.update(wavelength_nm=float(wavelengths_nm[0]), power_w=0.001)
    else:
        source["spectral_lines"] = [{"wavelength_nm": float(w), "power_w": 0.001} for w in wavelengths_nm]
    return {
        "schema": "opensci.project", "project_schema_version": "2.0", "design_intent": "OPTICAL_SYSTEM",
        "optical_models": models,
        "components": [
            {"instance_id": "SRC", "optical_role": "source", "nominal_pose": pose(start, direction),
             "custom_part": {"custom_part_key": "user-laser", "description": "tunable laser"}, "source_model": source},
            {"instance_id": "META", "optical_role": "lens", "optical_model_id": model["model_id"],
             "custom_part": {"custom_part_key": "own-metalens", "description": "customer metalens"},
             "nominal_pose": pose((LENS_X_MM, 0.0, HEIGHT_MM), (1, 0, 0))},
        ],
        "observations": [{"observation_id": "after", "aperture": {"shape": "circle", "radius_mm": 1},
                          "pose": pose((PLANE_X_MM, 0.0, HEIGHT_MM), (1, 0, 0))}],
        "targets": spot_targets(target_metric, wavelengths_nm),
    }


def spot_targets(metric, wavelengths_nm):
    """One target on the plane behind the lens; with several spectral lines one per line (selected by wavelength)."""
    unit = "mm" if metric.endswith("_mm") else "W/mm^2"
    if len(wavelengths_nm) == 1:
        return [{"target_id": "spot", "metric": metric, "observation_id": "after", "maximum": 0.01, "unit": unit}]
    return [{"target_id": f"spot_{decimal_text(float(w)).replace('.', 'p')}nm", "metric": metric, "observation_id": "after",
             "wavelength_nm": decimal_text(float(w)), "maximum": 0.01, "unit": unit} for w in wavelengths_nm]


def run(document):
    from os_optics.local import load_project, run_optical_project
    from os_optics.results import load_run

    results = load_run(run_optical_project(load_project(document)))
    statuses = results.describe()["status"]
    compact = results.targets()
    outcome = {"status": statuses.get("validation_status") or statuses["engine_status"],
               "execution": compact["outcome"]["execution"], "targets": compact["targets"],
               "engine_status": statuses["engine_status"],
               "issues": sorted({issue.code for issue in results.result.issues}),
               "model_scope_issues": [i.get("code") if isinstance(i, dict) else str(i)
                                      for i in statuses.get("model_scope_issues", [])],
               "target_issue_codes": sorted({str(getattr(code, "value", code)) for target in results.result.targets
                                             for code in (target.issue_codes or ())}),
               "identity": run_identity(results), "beams": []}
    for arrival in results.monitor("after")["arrivals"]:
        beam = arrival["beam"]
        gaussian = beam["gaussian"]
        entry = {"wavelength_nm": beam["wavelength_nm"], "power_W": beam["power_W"], "gaussian_valid": gaussian["valid"]}
        if gaussian["valid"]:
            waist = [p - gaussian["waist_distance_x_mm"] * d for p, d in zip(beam["position_mm"], beam["direction"])]
            entry.update({"waist_axial_um": (waist[0] - LENS_X_MM) * 1000, "waist_lateral_um": (waist[2] - HEIGHT_MM) * 1000,
                          "waist_radius_um": gaussian["waist_radius_x_mm"] * 1000})
        outcome["beams"].append(entry)
    outcome["selected_quantities_available"] = (bool(outcome["beams"])
        and all(b["gaussian_valid"] for b in outcome["beams"])
        and all(t["value"] is not None and not t["applicability_issue_codes"]
                and t["status"] in ("PASS", "WARNING", "FAIL") for t in compact["targets"]))
    return outcome


def compare(beam, external, model, waist_um, clear_um, f_um, transmittance):
    z_model, w_model = thin_lens_focus(f_um, waist_um, beam["wavelength_nm"])
    power_model = 0.001 * transmittance * aperture_fraction(waist_um, clear_um)
    return {
        "engine": {k: beam.get(k) for k in ("waist_axial_um", "waist_radius_um", "power_W")},
        "external": {"z_focus_um": external["z_focus_um"], "w_focus_um": external["w_focus_um"],
                     "power_W": 0.001 * external["focusing_efficiency"]},
        "engine_vs_model_rel": max(abs(beam["waist_axial_um"] / z_model - 1), abs(beam["waist_radius_um"] / w_model - 1),
                                   abs(beam["power_W"] / power_model - 1)),
        "engine_vs_external": {"z_abs_err_um": beam["waist_axial_um"] - external["z_focus_um"],
                               "z_rel_err": beam["waist_axial_um"] / external["z_focus_um"] - 1,
                               "w_rel_err": beam["waist_radius_um"] / external["w_focus_um"] - 1,
                               "power_rel_err": beam["power_W"] / (0.001 * external["focusing_efficiency"]) - 1},
    }


def bounds_of(model):
    metrics = {m["metric"]: m for m in model["model"]["supported_metrics"]}
    return {"z_abs_um": float(metrics["waist_distance_x_mm"]["abs_error"]) * 1000,
            "w_rel": float(metrics["waist_radius_x_mm"]["rel_error"]), "power_rel": float(metrics["power_W"]["rel_error"])}


def within(errors, bounds):
    return (abs(errors["z_abs_err_um"]) <= bounds["z_abs_um"] and abs(errors["w_rel_err"]) <= bounds["w_rel"]
            and abs(errors["power_rel_err"]) <= bounds["power_rel"])


def field_entry(outcome, row, max_angle):
    beam = outcome["beams"][0] if outcome["beams"] else {}
    entry = {"tilt_deg": row["tilt_deg"], "wavelength_nm": row["wavelength_nm"], "set": row.get("set"),
             "execution": outcome["execution"], "targets": outcome["targets"],
             "selected_quantities_available": outcome["selected_quantities_available"],
             "inside_model_domain": row["tilt_deg"] <= max_angle, "status": outcome["status"],
             "issues": outcome["issues"], "model_scope_issues": outcome["model_scope_issues"],
             "target_issue_codes": outcome["target_issue_codes"],
             "engine": {k: beam.get(k) for k in ("waist_axial_um", "waist_lateral_um", "waist_radius_um", "power_W")},
             "external": {"z_focus_um": row["z_focus_um"], "lateral_um": row["focus_xy_um"][0], "w_focus_um": row["w_focus_um"],
                          "power_W": 0.001 * row["focusing_efficiency"]}}
    if beam.get("waist_axial_um") is not None:
        entry["engine_vs_external"] = {
            "z_abs_err_um": beam["waist_axial_um"] - row["z_focus_um"],
            "lateral_abs_err_um": beam["waist_lateral_um"] - row["focus_xy_um"][0],
            "w_rel_err": beam["waist_radius_um"] / row["w_focus_um"] - 1,
            "power_rel_err": beam["power_W"] / (0.001 * row["focusing_efficiency"]) - 1}
    return entry


def run_example(directory, mode):
    installed = require_engine()
    directory = Path(directory)
    missing = [name for name in ("metalens_model.json", "step3_report.json") if not (directory / name).is_file()]
    if missing:
        raise FileNotFoundError(f"{directory} has no {' or '.join(missing)}: run step3_fit_model.py on it first")
    model = json.loads((directory / "metalens_model.json").read_text(encoding="utf-8"))
    step3 = json.loads((directory / "step3_report.json").read_text(encoding="utf-8"))
    table = json.loads(Path(step3["focus_table"]).read_text(encoding="utf-8"))
    design = table["design"]
    waist_um, clear_um = design["input_waist_radius_um"], design["diameter_um"] / 2
    rows = model_rows(model)
    bounds = bounds_of(model)
    max_angle = float(model["model"]["max_field_angle_deg"])
    external = {(r["wavelength_nm"], r["tilt_deg"], r["input_waist_radius_um"]): r for r in table["rows"]}

    def normal_case(lam, waist, row, role):
        outcome = run(project(model, mode, wavelengths_nm=[lam], waist_um=waist))
        f_um, transmittance = interpolate(rows, lam)
        entry = {"role": role, "status": outcome["status"], "issues": outcome["issues"], "identity": outcome["identity"]}
        entry.update({key: outcome[key] for key in ("execution", "targets", "selected_quantities_available")})
        entry.update(compare(outcome["beams"][0], row, model, waist, clear_um, f_um, transmittance))
        entry["within_stated_errors"] = within(entry["engine_vs_external"], bounds)
        return entry

    calibration, test = {}, {}
    for lam in TABLE_NM + CALIBRATION_NM:
        calibration[f"{lam}nm"] = normal_case(lam, waist_um, external[(lam, 0.0, waist_um)],
                                              "table row" if lam in TABLE_NM else "calibration wavelength")
    for lam in TEST_NM:
        test[f"{lam}nm"] = normal_case(lam, waist_um, external[(lam, 0.0, waist_um)], "test wavelength")
    for (lam, tilt, waist), row in sorted(external.items()):
        if tilt == 0.0 and waist != waist_um:
            target = test if row.get("set") == "test" else calibration
            target[f"{lam}nm_input_{waist:g}um"] = normal_case(lam, waist, row, f"{row.get('set', 'calibration')} beam size")

    field = {}
    for (lam, tilt, waist), row in sorted(external.items()):
        if tilt > 0.0:
            outcome = run(project(model, mode, wavelengths_nm=[lam], waist_um=waist, tilt_deg=tilt))
            field[f"{lam}nm_{tilt:g}deg"] = field_entry(outcome, row, max_angle)

    counter = {"450nm": run(project(model, mode, wavelengths_nm=[450.0], waist_um=waist_um,
                                    band_override=(rows[0]["wavelength_nm"], rows[-1]["wavelength_nm"])))}
    counter["450nm"].update(expected="OUT_OF_MODEL_SCOPE", expected_code="MODEL_WAVELENGTH_OUTSIDE_BAND")
    beyond = [f for f in field.values() if not f["inside_model_domain"]]
    for entry in beyond:
        name = f"{entry['tilt_deg']:g}deg"
        counter[name] = {k: entry[k] for k in ("status", "issues", "model_scope_issues", "target_issue_codes", "engine",
                                                "external", "targets", "execution")}
        if mode == "tabulated":
            counter[name].update(expected="OUT_OF_MODEL_SCOPE", expected_code="MODEL_FIELD_ANGLE_EXCEEDED")
        else:
            counter[name].update(expected="not stated by IDEAL_THIN_LENS", expected_code=None)
    multi_line = None
    if mode == "tabulated":
        counter["unsupported_metric"] = run(project(model, mode, wavelengths_nm=[532], waist_um=waist_um,
                                                    target_metric="peak_irradiance_W_per_mm2"))
        counter["unsupported_metric"].update(expected="OUT_OF_MODEL_SCOPE", expected_code="METRIC_NOT_VALIDATED_FOR_ELEMENT")
        all_lines = TABLE_NM + CALIBRATION_NM + TEST_NM
        multi = run(project(model, mode, wavelengths_nm=list(all_lines), waist_um=waist_um))
        lines = {round(b["wavelength_nm"]): b for b in multi["beams"]}
        multi_line = {"status": multi["status"], "issues": multi["issues"], "identity": multi["identity"], "lines": {}}
        multi_line.update({key: multi[key] for key in ("execution", "targets", "selected_quantities_available")})
        for lam in all_lines:
            f_um, transmittance = interpolate(rows, lam)
            multi_line["lines"][f"{lam}nm"] = compare(lines[lam], external[(lam, 0.0, waist_um)], model, waist_um,
                                                      clear_um, f_um, transmittance)
    for outcome in counter.values():
        codes = set(outcome["issues"]) | set(outcome.get("target_issue_codes", [])) | set(outcome["model_scope_issues"])
        outcome["as_expected"] = ((outcome["status"] == outcome["expected"] and outcome["expected_code"] in codes)
                                  if outcome["expected"].isupper() else None)
        if outcome["as_expected"] is not None:
            outcome["as_expected"] = outcome["as_expected"] and all(t["assessment"] == "INDETERMINATE" for t in outcome["targets"])

    test_field = [f for f in field.values() if f["set"] == "test"]
    calibration_field = [f for f in field.values() if f["set"] != "test" and f["inside_model_domain"]]
    test_cases = list(test.values())
    in_domain = list(calibration.values()) + test_cases + calibration_field + test_field
    checks = {
        "engine_vs_model_rel": {"value": max(c["engine_vs_model_rel"] for c in list(calibration.values()) + test_cases),
                                "tolerance": MODEL_AGREEMENT_REL},
        "test_cases_outside_stated_errors": {"value": sum(1 for c in test_cases if not c["within_stated_errors"])
                                             + sum(1 for f in test_field if not within_field(f, bounds)), "tolerance": 0},
        "in_domain_selected_quantities_unavailable": {
            "value": sum(not c["selected_quantities_available"] for c in in_domain)
                     + int(bool(multi_line) and not multi_line["selected_quantities_available"]), "tolerance": 0},
        "counterexamples_not_as_expected": {"value": sum(1 for c in counter.values() if c["as_expected"] is False),
                                            "tolerance": 0},
    }
    if multi_line:
        checks["multi_line_engine_vs_model_rel"] = {"value": max(l["engine_vs_model_rel"] for l in multi_line["lines"].values()),
                                                    "tolerance": MODEL_AGREEMENT_REL}
    outcome = comparison_status(all(c["value"] <= c["tolerance"] for c in checks.values()))
    # The calibration set defined the bounds: its deviations are reported as an envelope, not as a check.
    envelope = {"calibration_cases_outside_bounds": sum(1 for c in calibration.values() if not c["within_stated_errors"])
                + sum(1 for f in calibration_field if not within_field(f, bounds))}
    report = {"example": "interop/metasurface step 4", "outcome": outcome, "model_mode": mode, "engine_version": installed,
              "environment": environment("numpy"), "stated_errors": bounds, "max_field_angle_deg": max_angle,
              "checks": checks, "calibration_envelope": envelope, "test_cases": test, "calibration_cases": calibration,
              "field_angles": field, "counterexamples": counter, "multi_line": multi_line}
    write_json(directory / f"step4_report_{mode}.json", report)
    keys = ("z_abs_err_um", "z_rel_err", "w_rel_err", "power_rel_err")
    return {"example": report["example"], "outcome": outcome, "model_mode": mode, "checks": checks,
            "stated_errors": bounds, "calibration_envelope": envelope,
            "test_max_abs": {k: max(abs(c["engine_vs_external"][k]) for c in test_cases) for k in keys},
            "test_field_angles": [{"tilt_deg": f["tilt_deg"], "wavelength_nm": f["wavelength_nm"], "status": f["status"],
                                   **(f.get("engine_vs_external") or {})} for f in test_field],
            "counterexamples": {k: {"status": v["status"], "expected": v["expected"], "as_expected": v["as_expected"],
                                    "target_assessments": {t["target_id"]: t["assessment"] for t in v["targets"]},
                                    "expected_assessment": "INDETERMINATE" if v["expected"].isupper() else None,
                                    "issues": v["issues"] + v["model_scope_issues"],
                                    "target_issue_codes": v.get("target_issue_codes", [])} for k, v in counter.items()},
            "report": str(directory / f"step4_report_{mode}.json")}


def within_field(entry, bounds):
    errors = entry.get("engine_vs_external")
    if not errors:
        return False
    return (abs(errors["z_abs_err_um"]) <= bounds["z_abs_um"] and abs(errors["lateral_abs_err_um"]) <= bounds["z_abs_um"]
            and abs(errors["w_rel_err"]) <= bounds["w_rel"] and abs(errors["power_rel_err"]) <= bounds["power_rel"])


def main(argv=None):
    def workflow():
        parser = ArgumentParser(description=__doc__.split("\n\n")[0])
        parser.add_argument("directory", type=Path, help="directory with metalens_model.json and step3_report.json")
        parser.add_argument("--model", choices=("tabulated", "per-wavelength-ideal"), default="tabulated",
                            help="model kind to declare (default: the TABULATED_THIN_LENS model itself)")
        args = parser.parse_args(argv)
        return run_example(args.directory, args.model)

    return emit(workflow)


if __name__ == "__main__":
    raise SystemExit(main())
