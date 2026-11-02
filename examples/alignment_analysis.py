"""Alignment analysis: beam position and pointing errors after a fold mirror, from two observation planes, with
one-parameter studies of mirror tilt and source offset; optionally a clearly labelled synthetic-measurement fit.

Usage:
  python examples/alignment_analysis.py --out DIR [design inputs] [--study NAME=VALUES ...]
                                        [--synthetic-measurements [--synthetic-axis u|v]
                                         [--synthetic-planes-mm 100,175,250,325,400]
                                         [--synthetic-residuals-mm 0.004,-0.006,0.002,0,0]
                                         [--synthetic-camera-zero-mm 0.05]]
  python examples/alignment_analysis.py --list-parameters

Design (examples/optical_designs.py ``alignment``, no catalog needed): Gaussian source at x = -source_to_mirror
emitting along +X -> flat mirror M1 at the origin folding the beam in the design XY plane at mirror_aoi_deg (45: into
+Y) -> planes ``near`` and ``far`` on the nominal reflected axis at near_plane_mm and far_plane_mm after the mirror.
Plane axes: u = design +Z x (nominal reflected direction) (45 deg: design -X), v = design +Z. Mirror tilts turn M1
about its in-face axes through its face centre: local Y (= design +Z) moves the beam along u by 2 theta, local X
(in the plane of incidence) moves it along v by 2 theta cos(AOI). Source tilts turn the source about its local X
(= design +Y) and local Y (= design +Z).

Targets (checked by the Engine): near/far position_u_mm and position_v_mm within +-position_tolerance_mm;
direction_cosine_u / _v on far within +-sin(pointing_tolerance_mrad). Analysis per run (optical_designs.
alignment_errors): chief-ray positions on both planes, two-plane pointing 1000 atan2(far - near, separation) in mrad,
and whether each plane was reached.

Default studies (12 samples): mirror_tilt_local_y_mrad -1..60 (0.25 mrad misses the far position tolerance while the
near plane passes; 1 mrad misses both and the pointing tolerance; 15 mrad adds INCOMPLETE_ENVELOPE; at 60 mrad the
beam misses the 12.5 mm far plane: OBSERVATION_NOT_REACHED, outcome UNKNOWN, no far values), source_offset_z_mm
0.05 / 0.15 (a pure offset: 0.15 mm misses the position tolerance on both planes, pointing stays 0) and
mirror_tilt_local_x_mrad -0.25 / 0.1 / 0.5 (out of the plane of incidence: 2 theta cos 45 deg).

Independent checks: exact vector reflection and plane intersection (optical_designs.mirror_alignment_reference) for
positions (absolute 1e-9 mm), two-plane pointing (1e-9 mrad) and far direction cosines (1e-12); the small-angle
expectation pointing_u = 2 theta_y - phi_y, pointing_v = -2 theta_x cos(AOI) - phi_x (theta: mirror tilt, phi: source
tilt; tolerance 1000 D^2 mrad, D = 2|theta_x| + 2|theta_y| + |phi_x| + |phi_y| in rad: the neglected second-order
terms).

Synthetic-measurement variant (--synthetic-measurements): the nominal design gets extra planes at the given
distances after the mirror (edit_project add_observation), the Engine chief-ray position on each plane gets an
invented residual and an invented constant camera zero, and every row of synthetic/synthetic_measurements.csv is
labelled SYNTHETIC in its data_origin column. examples/analyze_measurements.py subtracts the camera zero and fits a
line; the fitted slope gives the pointing (mrad), reported against the small-angle expectation and the Engine's
two-plane pointing. These rows are not measurements.

Output: DIR as described in examples/design_workflow.py, plus DIR/synthetic/ with the variant. Stdout: one compact
JSON document.
Exit code from the nominal design: 0 PASS (WARNING included), 1 FAIL, 2 UNKNOWN, 3 OUT_OF_MODEL, 4 ERROR (the
Engine refused the nominal design: the summary holds nominal.error) or refused input (JSON error on stderr,
nothing run). Unmet or out-of-model study samples do not change the exit code; they are counted and listed in the
output.
"""

from __future__ import annotations

import copy
import math

if __package__:
    from . import design_workflow as dw
    from . import workflow_common as wc
else:
    import design_workflow as dw
    import workflow_common as wc

DEFAULT_STUDIES = [
    "mirror_tilt_local_y_mrad=-1,0,0.1,0.25,1,15,60",
    "source_offset_z_mm=0.05,0.15",
    "mirror_tilt_local_x_mrad=-0.25,0.1,0.5",
]
REFERENCE = ("independent exact vector reflection and plane intersection "
             "(optical_designs.mirror_alignment_reference) and the small-angle 2 theta expectation")
SYNTHETIC_LABEL = "SYNTHETIC: Engine chief-ray position + invented residual + invented camera zero; not a measurement"


def _designs():
    if __package__:
        from . import optical_designs as D
    else:
        import optical_designs as D
    return D


def small_angle_pointing_mrad(params):
    """Small-angle pointing (mrad) on the planes: u = 2 theta_y - phi_y, v = -2 theta_x cos(AOI) - phi_x."""
    D = _designs()
    u = D.mirror_deviation_mrad(params["mirror_tilt_local_y_mrad"], axis="in_plane") - \
        float(params["source_tilt_local_y_mrad"])
    v = -D.mirror_deviation_mrad(params["mirror_tilt_local_x_mrad"], axis="out_of_plane",
                                 mirror_aoi_deg=params["mirror_aoi_deg"]) - float(params["source_tilt_local_x_mrad"])
    return {"u": u, "v": v}


def second_order_tolerance_mrad(params):
    """Tolerance of the small-angle expectation: 1000 * D^2 mrad with D = (2|theta_x| + 2|theta_y| + |phi_x| +
    |phi_y|) in rad, the size of the neglected second-order terms (at least 1e-9 mrad)."""
    deviation = 1e-3 * (2.0 * abs(float(params["mirror_tilt_local_x_mrad"])) +
                        2.0 * abs(float(params["mirror_tilt_local_y_mrad"])) +
                        abs(float(params["source_tilt_local_x_mrad"])) + abs(float(params["source_tilt_local_y_mrad"])))
    return max(1e-9, 1e3 * deviation * deviation)


def checks(params, record):
    """Engine positions / pointing (saved run) vs exact geometry and the small-angle expectation for one sample."""
    D = _designs()
    reference = D.mirror_alignment_reference(**params)
    errors = D.alignment_errors(record["run_dir"]) if record.get("run_dir") else None
    measured = errors or {}
    values = dw.target_values(record)
    exact = "exact vector geometry"
    rows = []
    for plane in ("near", "far"):
        for axis in ("u", "v"):
            rows.append(dw.compare_value(f"{plane} position_{axis}", "mm", measured.get(f"{plane}_{axis}_mm"),
                                         reference[f"{plane}_{axis}_mm"], method=exact, abs_tol=1e-9))
    for axis in ("u", "v"):
        rows.append(dw.compare_value(f"two-plane pointing_{axis}", "mrad", measured.get(f"pointing_{axis}_mrad"),
                                     reference[f"pointing_{axis}_mrad"], method=exact, abs_tol=1e-9))
        rows.append(dw.compare_value(f"far direction_cosine_{axis}", "1", values.get(f"far_pointing_{axis}"),
                                     reference[f"far_direction_cosine_{axis}"], method=exact, abs_tol=1e-12))
    expected = small_angle_pointing_mrad(params)
    tolerance = second_order_tolerance_mrad(params)
    for axis in ("u", "v"):
        rows.append(dw.compare_value(f"two-plane pointing_{axis}", "mrad", measured.get(f"pointing_{axis}_mrad"),
                                     expected[axis], method="small-angle 2 theta", abs_tol=tolerance))
    analysis = None
    if errors is not None:
        analysis = {key: errors.get(key) for key in (
            "reached", "near_u_mm", "near_v_mm", "far_u_mm", "far_v_mm", "pointing_u_mrad", "pointing_v_mrad",
            "far_direction_pointing_u_mrad", "far_direction_pointing_v_mrad", "separation_mm")}
        analysis["small_angle_pointing_mrad"] = expected
    return {"rows": rows, "analysis": analysis}


# --------------------------------------------------------------------------------------------- synthetic variant

def _values(text, name):
    try:
        return [float(item) for item in str(text).split(",") if item.strip()]
    except ValueError:
        raise wc.WorkflowError("BAD_ARGUMENT", f"--{name}: give comma-separated numbers in mm") from None


def synthetic_options(parser):
    group = parser.add_argument_group("synthetic-measurement variant (rows labelled SYNTHETIC)")
    group.add_argument("--synthetic-measurements", action="store_true",
                       help="add the synthetic-measurement fit of the nominal design (one extra Engine run)")
    group.add_argument("--synthetic-axis", choices=["u", "v"], default="u", help="plane axis of the positions (u)")
    group.add_argument("--synthetic-planes-mm", default="100,175,250,325,400",
                       help="plane distances after the mirror [mm] (default 100,175,250,325,400)")
    group.add_argument("--synthetic-residuals-mm", default="0.004,-0.006,0.002,0,0",
                       help="invented residual per plane [mm], same count as the planes (default "
                            "0.004,-0.006,0.002,0,0)")
    group.add_argument("--synthetic-camera-zero-mm", type=float, default=0.05,
                       help="invented constant camera zero added to every position and given as the background "
                            "column [mm] (default 0.05)")


def configure(config, args):
    if not args.synthetic_measurements:
        return config
    planes = _values(args.synthetic_planes_mm, "synthetic-planes-mm")
    residuals = _values(args.synthetic_residuals_mm, "synthetic-residuals-mm")
    if len(planes) < 3 or len(residuals) != len(planes) or len(set(planes)) != len(planes) or min(planes) <= 0:
        raise wc.WorkflowError("BAD_ARGUMENT", "--synthetic-planes-mm needs at least three distinct positive "
                               "distances and --synthetic-residuals-mm one residual per plane")
    if not math.isfinite(args.synthetic_camera_zero_mm):
        raise wc.WorkflowError("BAD_ARGUMENT", "--synthetic-camera-zero-mm must be finite")
    options = {"axis": args.synthetic_axis, "planes_mm": planes, "residuals_mm": residuals,
               "camera_zero_mm": args.synthetic_camera_zero_mm}
    return {**config, "planned_extra_evaluations": 1,
            "after_studies": lambda out_dir, inputs, doc, record: synthetic_variant(out_dir, inputs, doc, options)}


def _plane_id(distance):
    return "plane_" + f"{distance:g}".replace(".", "p").replace("-", "m") + "mm"


def synthetic_variant(out_dir, inputs, nominal_doc, options):
    """Engine positions on several planes + invented residuals -> labelled CSV -> analyze_measurements line fit."""
    if __package__:
        from . import analyze_measurements as am
        from .edit_project import apply_edits
    else:
        import analyze_measurements as am
        from edit_project import apply_edits

    axis = options["axis"]
    near = next(o for o in nominal_doc["observations"] if o["observation_id"] == "near")
    direction = [c / float(inputs["near_plane_mm"]) for c in near["pose"]["position_mm"]]
    known = {float(inputs["near_plane_mm"]): "near", float(inputs["far_plane_mm"]): "far"}
    ids, ops = [], []
    for distance in options["planes_mm"]:
        if distance in known:
            ids.append(known[distance])
            continue
        plane = copy.deepcopy(near)
        plane["observation_id"] = _plane_id(distance)
        plane["pose"]["position_mm"] = [distance * c for c in direction]
        ops.append({"op": "add_observation", "observation": plane})
        ids.append(plane["observation_id"])
    document = apply_edits(nominal_doc, ops)["project"] if ops else nominal_doc
    synthetic_dir = out_dir / "synthetic"
    record = wc.run_sample(document, synthetic_dir)
    wc.write_json(synthetic_dir / "sample.json", record)
    rows = []
    if record["run_dir"]:
        from os_optics.results import load_run

        results = load_run(record["run_dir"], verify=False)
    for distance, plane_id, residual in zip(options["planes_mm"], ids, options["residuals_mm"]):
        arrivals = results.arrivals(plane_id) if record["run_dir"] else []
        engine = arrivals[0][f"{axis}_mm"] if arrivals else None
        rows.append({"distance_after_mirror_mm": distance,
                     f"centroid_{axis}_mm": None if engine is None else engine + residual + options["camera_zero_mm"],
                     f"camera_zero_{axis}_mm": options["camera_zero_mm"], f"engine_{axis}_mm": engine,
                     "invented_residual_mm": residual, "plane_id": plane_id, "data_origin": SYNTHETIC_LABEL})
    columns = list(rows[0])
    csv_path = wc.write_csv(synthetic_dir / "synthetic_measurements.csv", rows, columns)
    config = am.MeasurementConfig(x="distance_after_mirror_mm", signal=f"centroid_{axis}_mm",
                                  background=f"camera_zero_{axis}_mm", x_unit="mm", signal_unit="mm")
    fit_summary = am.analyze_measurements(csv_path, synthetic_dir / "analysis", config)
    fit = fit_summary.get("fit")
    expected = small_angle_pointing_mrad(inputs)[axis]
    reference = _designs().mirror_alignment_reference(**inputs)[f"pointing_{axis}_mrad"]
    result = {"synthetic": True, "label": SYNTHETIC_LABEL, "axis": axis, "planes_mm": options["planes_mm"],
              "invented_residuals_mm": options["residuals_mm"], "invented_camera_zero_mm": options["camera_zero_mm"],
              "analysis_status": fit_summary["status"], "rows": fit_summary["rows"],
              "expected_small_angle_pointing_mrad": expected, "exact_geometry_pointing_mrad": reference,
              "fitted_pointing_mrad": None, "fitted_pointing_standard_error_mrad": None,
              "difference_to_expectation_mrad": None, "within_3_standard_errors": None,
              "paths": {"csv": wc.relative(csv_path, out_dir),
                        "analysis_summary": wc.relative(synthetic_dir / "analysis" / "summary.json", out_dir),
                        "plot": wc.relative(synthetic_dir / "analysis" / "measurements.png", out_dir),
                        "run": wc.relative(synthetic_dir / "run", out_dir) if record["run_dir"] else None}}
    if fit:
        slope, error = fit["slope"], fit["slope_standard_error"]
        pointing = 1e3 * math.atan(slope)
        pointing_error = 1e3 * error / (1.0 + slope * slope)
        result.update({"fit_slope_mm_per_mm": slope, "fit_slope_standard_error": error, "fit_rmse_mm": fit["rmse"],
                       "fitted_pointing_mrad": pointing, "fitted_pointing_standard_error_mrad": pointing_error,
                       "difference_to_expectation_mrad": pointing - expected,
                       "within_3_standard_errors": abs(pointing - expected) <= 3.0 * pointing_error + 1e-12})
    lines = [f"Every row of [{result['paths']['csv']}](../{result['paths']['csv']}) is labelled `SYNTHETIC` "
             "(column data_origin): Engine chief-ray positions on "
             f"{len(rows)} planes plus invented residuals {options['residuals_mm']} mm and an invented camera zero "
             f"{options['camera_zero_mm']} mm. The fit is examples/analyze_measurements.py "
             f"([summary](../{result['paths']['analysis_summary']}), [plot](../{result['paths']['plot']})).", "",
             "| quantity | value | unit |", "| --- | --- | --- |"]
    for key, unit in (("fitted_pointing_mrad", "mrad"), ("fitted_pointing_standard_error_mrad", "mrad"),
                      ("expected_small_angle_pointing_mrad", "mrad"), ("exact_geometry_pointing_mrad", "mrad"),
                      ("difference_to_expectation_mrad", "mrad"), ("within_3_standard_errors", "-"),
                      ("fit_rmse_mm", "mm"), ("analysis_status", "-")):
        value = result.get(key)
        lines.append(f"| {key} | {f'{value:.12g}' if isinstance(value, float) else value} | {unit} |")
    files = [(csv_path, "synthetic measurements (labelled SYNTHETIC)"),
             (synthetic_dir / "analysis" / "summary.json", "synthetic fit summary"),
             (synthetic_dir / "analysis" / "processed.csv", "synthetic fit processed rows")]
    compact = {key: result.get(key) for key in (
        "synthetic", "label", "axis", "analysis_status", "fitted_pointing_mrad", "fitted_pointing_standard_error_mrad",
        "expected_small_angle_pointing_mrad", "exact_geometry_pointing_mrad", "difference_to_expectation_mrad",
        "within_3_standard_errors")}
    compact["rows_used"] = f"{result['rows']['used']}/{result['rows']['total']}"
    compact["csv"] = result["paths"]["csv"]
    return {"synthetic": result, "synthetic_compact": compact, "extra_evaluations": 1,
            "compact_extra": {"synthetic": "synthetic_compact"},
            "report_sections": [{"title": "Synthetic-measurement fit (SYNTHETIC data)", "lines": lines}],
            "report_artifacts": files}


def config():
    D = _designs()
    return {"workflow": "alignment_analysis", "design": "alignment", "info": D.DESIGN_INFO["alignment"],
            "builder": D.alignment, "checks": checks, "reference_method": REFERENCE,
            "default_studies": DEFAULT_STUDIES, "max_evaluations": 40, "configure": configure,
            "analysis_keys": ["far_u_mm", "far_v_mm", "pointing_u_mrad", "pointing_v_mrad"],
            "compact_analysis": True}


def main(argv=None):
    return dw.main(config(), {}, argv, description=__doc__, extra_options=synthetic_options)


if __name__ == "__main__":
    raise SystemExit(main())
