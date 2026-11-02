"""OpenSci side of the diffraction example (requires os-optics 0.10.0).

A 532 nm TEM00 beam (1 mm waist radius at the source, 1 mW) passes a declared
circular APERTURE_STOP 100 mm downstream and a declared IDEAL_THIN_LENS
(f = 200 mm) 1 mm after it; an observation plane sits at the lens's back focal
plane. For each stop radius the Project is run and a small handoff file is
written with what an external diffraction code needs, all taken from the run:

* the Engine's transverse-plane handoff (``RunResults.handoff``) on an observation
  plane in the stop's plane, with the stop outline in that plane's frame: Gaussian
  q per axis, radii, wavefront radii, M^2, power, wavelength, medium, Jones vector,
  the TEM00 field-construction convention and its applicability; it is checked
  against the run's state on the ``pupil`` plane 1 mm upstream propagated with the
  ABCD law;
* the lens focal length from the Project's optical model, and the plane distances
  from the component poses;
* the Engine's own focal-plane result (status, issues, transmitted power,
  Gaussian radius, peak of the Engine irradiance map) for the comparison;
* the run identity (engine build, project / input / result hashes).

The Engine's focal irradiance is a paraxial Gaussian quantity, not a diffracted
field; ``external_focus.py`` computes the diffraction from the handoff file.

Usage: python examples/interop/diffraction/opensci_handoff.py --out run-interop-diffraction [--stop-radius-mm 3 1.2 0.5]
"""

from __future__ import annotations

import math
from pathlib import Path
import sys

if __package__:
    from ..common import ArgumentParser, decimal_text, emit, require_engine, run_identity, write_json
    from .tem00_field import propagate_state
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from common import ArgumentParser, decimal_text, emit, require_engine, run_identity, write_json
    from tem00_field import propagate_state

PLUS_X = [0.7071067811865476, 0.0, 0.7071067811865476, 0.0]  # local +Z along design +X
WAVELENGTH_NM, WAIST_MM, POWER_W = 532.0, 1.0, 0.001
PUPIL_X, STOP_X, LENS_X, FOCAL_MM = 99.0, 100.0, 101.0, 200.0
ENCIRCLED_RADIUS_MM = 0.05
HANDOFF_SCHEMA = "opensci-interop-example/diffraction-handoff/1"


def project_document(stop_radius_mm):
    def component(instance_id, role, model_id, x_mm):
        return {"instance_id": instance_id, "optical_role": role, "optical_model_id": model_id,
                "custom_part": {"custom_part_key": f"own-{instance_id.lower()}", "description": "customer optic"},
                "nominal_pose": {"position_mm": [x_mm, 0.0, 50.0], "quaternion_wxyz": PLUS_X}}

    def plane(observation_id, x_mm):
        return {"observation_id": observation_id, "aperture": {"shape": "circle", "radius_mm": 10},
                "pose": {"position_mm": [x_mm, 0.0, 50.0], "quaternion_wxyz": PLUS_X}}

    return {
        "schema": "opensci.project", "project_schema_version": "2.0", "design_intent": "OPTICAL_SYSTEM",
        "optical_models": [
            {"model_id": "stop", "model": {"model_kind": "APERTURE_STOP", "model_version": "1", "status": "SPECIFIED",
                                           "opening_radius_mm": decimal_text(stop_radius_mm), "edge_radius_mm": "12.7"}},
            {"model_id": "lens", "model": {"model_kind": "IDEAL_THIN_LENS", "model_version": "1", "status": "SPECIFIED",
                                           "focal_length_mm": decimal_text(FOCAL_MM), "clear_radius_mm": "12.7",
                                           "edge_radius_mm": "12.7",
                                           "valid_band_nm": {"min_nm": "500", "max_nm": "600", "status": "SPECIFIED"}}},
        ],
        "components": [
            {"instance_id": "SRC", "optical_role": "source",
             "custom_part": {"custom_part_key": "user-laser", "description": "532 nm laser"},
             "nominal_pose": {"position_mm": [0.0, 0.0, 50.0], "quaternion_wxyz": PLUS_X},
             "source_model": {"model_kind": "GAUSSIAN_BEAM", "wavelength_nm": WAVELENGTH_NM, "power_w": POWER_W,
                              "waist_radius_mm": WAIST_MM, "waist_position_mm": 0.0, "m2": 1.0,
                              "polarization": {"kind": "LINEAR", "angle_deg": 0.0}}},
            component("STOP", "aperture", "stop", STOP_X),
            component("L", "lens", "lens", LENS_X),
        ],
        "observations": [plane("pupil", PUPIL_X), plane("stop_plane", STOP_X), plane("focus", LENS_X + FOCAL_MM)],
        "targets": [{"target_id": "spot", "metric": "beam_radius_mm", "observation_id": "focus", "maximum": 0.1, "unit": "mm"}],
    }


def model(document, model_id):
    return next(m["model"] for m in document["optical_models"] if m["model_id"] == model_id)


def component_x(document, instance_id):
    return next(c for c in document["components"] if c["instance_id"] == instance_id)["nominal_pose"]["position_mm"][0]


def gaussian_at_stop(results, document):
    """The beam at the stop plane from the Engine's handoff, cross-checked against the pupil-plane state + ABCD."""
    handoff = results.handoff("stop_plane", element="STOP")
    beam = handoff["beams"][0]
    gaussian, validity = beam["gaussian"], beam["validity"]
    if not validity["tem00_field_applicable"]:
        raise ValueError(f"the run states that no TEM00 field follows from this beam: {validity['statement']}")
    surface = handoff["element"]["surfaces"][0]
    if not surface["in_plane"] or surface["shape"] != "circle":
        raise ValueError("this example needs a round stop lying in the handoff plane")
    state = {"power_W": beam["power_W"], "wavelength_nm": beam["wavelength_nm"], "medium_index": beam["medium_index"],
             "axes_uvn": {"x": gaussian["axis_x_uvn"], "y": gaussian["axis_y_uvn"]}, "from": "Engine transverse-plane handoff",
             "validity": validity["statement"]}
    for axis in ("x", "y"):
        radius = gaussian[f"curvature_radius_{axis}_mm"]
        state.update({f"w_{axis}_mm": gaussian[f"beam_radius_{axis}_mm"], f"r_{axis}_mm": radius,
                      f"waist_distance_{axis}_mm": gaussian[f"waist_distance_{axis}_mm"],
                      f"rayleigh_range_{axis}_mm": gaussian[f"rayleigh_range_{axis}_mm"], f"m2_{axis}": gaussian[f"m2_{axis}"]})
    # Independent cross-check: the pupil-plane state 1 mm upstream, propagated in air.
    arrival = results.monitor("pupil")["arrivals"][0]
    pupil = arrival["beam"]["gaussian"]
    gap = component_x(document, "STOP") - arrival["beam"]["position_mm"][0]
    w, radius, _ = propagate_state(pupil["waist_distance_x_mm"], pupil["rayleigh_range_x_mm"], gap,
                                   arrival["beam"]["wavelength_nm"], pupil["medium_index"])
    state["cross_check"] = {"method": "pupil-plane state + ABCD free-space propagation", "w_rel_diff": w / state["w_x_mm"] - 1,
                            "r_rel_diff": radius / state["r_x_mm"] - 1 if state["r_x_mm"] else None}
    if abs(state["cross_check"]["w_rel_diff"]) > 1e-9:
        raise ValueError(f"handoff and pupil-plane states disagree: {state['cross_check']}")
    return state, surface, handoff


def engine_focus(results):
    statuses = results.describe()["status"]
    monitor = results.monitor("focus")
    arrivals = monitor["arrivals"]
    focus = {"status": statuses.get("validation_status") or statuses["engine_status"],
             "execution": results.targets()["outcome"]["execution"],
             "quantity_state": monitor["quantity_state"], "power_complete": monitor["power_complete"],
             "total_power_W": monitor["total_power_W"], "power_upper_bound_W": monitor.get("power_upper_bound_W"),
             "issues": sorted({issue.code for issue in results.result.issues}),
             "model_scope_issues": statuses.get("model_scope_issues", []), "arrived": bool(arrivals)}
    if not arrivals:
        return focus
    beam = arrivals[0]["beam"]
    gaussian = beam["gaussian"]
    focus.update({"power_W": beam["power_W"], "gaussian_valid": gaussian["valid"], "gaussian_status": gaussian["status"],
                  "gaussian_reason": gaussian["reason"]})
    if gaussian["valid"]:
        w = gaussian["beam_radius_x_mm"]
        grid = results.irradiance_map("focus", samples=101)
        peak = max(max(row) for row in grid["incoherent_W_per_mm2"])
        focus.update({"beam_radius_x_mm": w, "beam_radius_y_mm": gaussian["beam_radius_y_mm"],
                      "peak_irradiance_W_per_mm2": peak, "irradiance_map_model": grid["model"],
                      f"encircled_power_W_{ENCIRCLED_RADIUS_MM}mm":
                          beam["power_W"] * (1.0 - math.exp(-2.0 * ENCIRCLED_RADIUS_MM ** 2 / w ** 2))})
    return focus


def run_case(stop_radius_mm, out_dir):
    from os_optics.local import load_project, run_optical_project
    from os_optics.results import load_run

    document = project_document(stop_radius_mm)
    run_dir = out_dir / "run"
    run_optical_project(load_project(document), output_dir=str(run_dir))
    results = load_run(run_dir)
    beam, surface, engine_handoff = gaussian_at_stop(results, document)
    handoff = {
        "schema": HANDOFF_SCHEMA, "requires": "os-optics>=0.10.0",
        "identity": run_identity(results), "run_directory": str(run_dir),
        "field_convention": engine_handoff["conventions"]["tem00_field"],
        "beam_at_stop": beam,
        "stop": {"shape": "circle", "radius_mm": surface["radius_mm"], "edge": "hard, infinitely thin",
                 "surface_id": surface["surface_id"], "project_opening_radius_mm": float(model(document, "stop")["opening_radius_mm"])},
        "engine_handoff": {k: engine_handoff[k] for k in ("schema", "schema_version", "observation_id", "plane", "conventions")},
        "lens": {"model_kind": "IDEAL_THIN_LENS", "focal_length_mm": float(model(document, "lens")["focal_length_mm"]),
                 "distance_from_stop_mm": component_x(document, "L") - component_x(document, "STOP"),
                 "phase": "not modelled by the Engine; an external code applies the ideal quadratic phase", "model_id": "lens"},
        "observation": {"observation_id": "focus", "distance_from_lens_mm": FOCAL_MM},
        "source_power_W": POWER_W, "encircled_radius_mm": ENCIRCLED_RADIUS_MM,
        "engine_focus": engine_focus(results),
    }
    write_json(out_dir / "handoff.json", handoff)
    return handoff


def run_example(out_dir, stop_radii_mm):
    installed = require_engine()
    out_dir = Path(out_dir)
    cases = {}
    for radius in stop_radii_mm:
        case_dir = out_dir / f"stop_{decimal_text(radius).replace('.', 'p')}mm"
        handoff = run_case(radius, case_dir)
        focus = handoff["engine_focus"]
        cases[case_dir.name] = {"stop_radius_mm": radius, "engine_status": focus["status"], "issues": focus["issues"],
                                "gaussian_valid": focus.get("gaussian_valid"), "handoff": str(case_dir / "handoff.json")}
    return {"example": "interop/diffraction (OpenSci side)", "outcome": "COMPLETED", "engine_version": installed, "cases": cases}


def main(argv=None):
    def workflow():
        parser = ArgumentParser(description=__doc__.split("\n\n")[0])
        parser.add_argument("--out", type=Path, default=Path("run-interop-diffraction"), help="output directory")
        parser.add_argument("--stop-radius-mm", type=float, nargs="+", default=[3.0, 1.2, 0.5],
                            help="stop radii in mm (default: 3 1.2 0.5)")
        args = parser.parse_args(argv)
        if any(not math.isfinite(r) or r <= 0 for r in args.stop_radius_mm):
            raise ValueError("stop radii must be positive")
        return run_example(args.out, args.stop_radius_mm)

    return emit(workflow)


if __name__ == "__main__":
    raise SystemExit(main())
