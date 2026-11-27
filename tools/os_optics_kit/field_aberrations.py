"""Field curvature and distortion of a saved run, per field point and wavelength, from published engine data.

os-optics-kit field RUN --monitor PLANE [--efl MM] [--stop-tolerance-mm MM] [--out field.json]

Nothing is traced here. Inputs: the run's project.json (source poses), its simulation_result.json (chief-ray segments)
and optical_scene.json (aperture-stop surfaces), and 'os-optics results rays' at the plane (through the ray fans of this
kit). Each path is one field point at one wavelength. The engine's chief ray of a path is its source's beam-centre ray;
it is the optical chief ray when it passes through the centre of the aperture stop's opening, so every source should
be aimed at that centre. Paths reflected before the plane are not supported: when every path at the plane is reflected
the command refuses (REFLECTED_PATH_UNSUPPORTED, with each path's reflection count), and a reflected path beside
unreflected ones is listed in unreached_fields (PUPIL_FRAME_UNRESOLVED). For each path:

* chief ray: where it crosses the aperture stop, as stop_offset_mm from the centre of the stop's opening (the stop's
  opening_center_mm in its plane, carried to the design frame; aperture_stops lists each stop with that centre). The
  crossing is the chief-ray segment that ends on the stop's Engine surface id (stop_id), whatever its event (a sampled
  stop ends it with complex_transmission). Within --stop-tolerance-mm of the centre it is WITHIN_STOP_TOLERANCE (the
  row repeats the tolerance); further out (CHIEF_RAY_OFF_STOP), when it misses the stop (CHIEF_RAY_STOP_NOT_CROSSED),
  or when the crossing or the stop geometry is not resolved (CHIEF_RAY_STOP_UNRESOLVED, with the detail), distortion
  and focus are null with that reason. A run without an aperture stop reports the beam-centre ray's values
  (NO_APERTURE_STOP);
* field angle: the angle between the source's emission axis and that of the on-axis field (the source whose chief ray
  lands on the plane's origin, within 1e-3 mm), in degrees;
* image height: the chief ray's distance from the plane's origin in (u, v), in mm;
* distortion: (h - h_ref) / h_ref in percent, h_ref = EFL * tan(field angle), with h and h_ref on the same plane.
  Without --efl, the EFL and that plane come from the engine's paraxial analysis ('os-optics project paraxial') of the
  elements on the on-axis field's emission line, at each wavelength, in the air of the Project's environment: h is the
  chief ray's distance from the optical axis where its straight continuation in image space meets the paraxial rear
  focal plane (through rear_focal_point_mm, normal to the outgoing axis), so the plane's own defocus does not enter.
  With --efl the plane is taken as the focal plane of the stated EFL and h is measured on it. references names the
  plane and, without --efl, the paraxial focus's distance from the plane. Without an EFL the command refuses
  (EFL_NOT_STATED). The on-axis field reads 0; a zero-angle field off the origin reads null (ZERO_FIELD_NONZERO_HEIGHT);
* field curvature: the tangential and sagittal focus positions along the plane normal, from the plane, in mm (positive
  behind the plane): the mean longitudinal aberration of the innermost pupil zone of each fan (the paraxial limit of
  the fan within the run's sampling). A null focus carries its reason (FAN_EMPTY, NO_CROSSING, ...).

Every declared source is in fields or in unreached_fields: a path that does not reach the plane with counted rays
(NO_COUNTED_RAYS_AT_MONITOR, NO_RAYS_AT_MONITOR, with the engine's termination), a path whose fans are refused (the
fans code and message), or a source without a path in the run (NO_PATH_IN_RUN). Output is JSON.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Callable

from . import geometry3d, ray_fans

# How far the chief ray nearest the plane origin may be from it and still count as the on-axis field; a caller may only
# tighten it.
AXIS_TOLERANCE_MM = 1e-3
# How far a chief ray may cross the aperture stop from its centre and still count as the field's chief ray.
STOP_TOLERANCE_MM = 1e-2
# Lateral distance from the on-axis field's emission line within which a component counts as on that line.
ON_AXIS_LINE_MM = 1e-6
ZERO_ANGLE_DEG = 1e-6
# How a refusal for a missing EFL tells the caller to recover.
EFL_HINT = "state the EFL with --efl"

# chief_ray_stops: the chief ray crosses a stop (stop_offset_mm measured); the tolerance then decides between the next two
STOP_CROSSED = "STOP_CROSSED"
WITHIN_STOP_TOLERANCE = "WITHIN_STOP_TOLERANCE"
OFF_STOP_CENTRE = "CHIEF_RAY_OFF_STOP"
STOP_NOT_CROSSED = "CHIEF_RAY_STOP_NOT_CROSSED"
STOP_UNRESOLVED = "CHIEF_RAY_STOP_UNRESOLVED"
NO_APERTURE_STOP = "NO_APERTURE_STOP"
STOP_NOT_CHECKED = "STOP_NOT_CHECKED"
# The refusal of a plane that only reflected paths reach.
REFLECTED_PATH_UNSUPPORTED = "REFLECTED_PATH_UNSUPPORTED"
# Chief-ray states whose distortion and focus are reported.
_REPORTED = (WITHIN_STOP_TOLERANCE, NO_APERTURE_STOP, STOP_NOT_CHECKED)
# A chief ray whose direction makes a smaller cosine with the outgoing axis is not carried to the focal plane.
PARALLEL_COSINE = 1e-9

# efl_for(axis_source_id, wavelength_nm) -> efl_mm, basis, and (paraxial) rear_focal_point_mm and axis_direction
EflFor = Callable[[str, float], dict]


class FieldError(RuntimeError):
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.details = details


# the kit's quaternion helpers (Hamilton, active; rotate(q, v) = q v q*)
_rotate = geometry3d.rotate
_qmul = geometry3d.qmul


def source_axes(project: dict) -> dict[str, tuple[float, float, float]]:
    """instance_id -> unit emission axis (the source frame's local +z) of every source component."""
    axes = {}
    for component in project.get("components", []):
        if component.get("optical_role") == "source":
            axes[component["instance_id"]] = _rotate(component["nominal_pose"]["quaternion_wxyz"], (0.0, 0.0, 1.0))
    return axes


def _angle_deg(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    dot = max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b))))
    return math.degrees(math.acos(dot))


def _inner_zone_focus(rows: list[dict], fan: str) -> tuple[float | None, float | None, str | None]:
    """Innermost sampled zone: its mean longitudinal aberration (the paraxial limit within the sampling), its rho, and
    the reason when there is none."""
    if not rows:
        return None, None, f"FAN_EMPTY: no sampled pupil ray lies on the {fan} line through the chief ray"
    values = [row for row in rows if row["longitudinal_mm"] is not None]
    if not values:
        return None, None, f"NO_CROSSING: every {fan} fan ray is parallel to the chief ray at the plane"
    inner = min(abs(row["rho"]) for row in values)
    zone = [row["longitudinal_mm"] for row in values if abs(abs(row["rho"]) - inner) < 1e-9]
    return sum(zone) / len(zone), inner, None


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def check_arguments(efl_mm: float | None, axis_tolerance_mm: float = AXIS_TOLERANCE_MM,
                    stop_tolerance_mm: float = STOP_TOLERANCE_MM) -> None:
    """Refuse an EFL or tolerance that cannot give a meaningful value (BAD_ARGUMENT)."""
    if efl_mm is not None and not (_finite(efl_mm) and efl_mm > 0):
        raise FieldError("BAD_ARGUMENT", f"--efl must be a finite positive length in mm, got {efl_mm!r}")
    if not (_finite(axis_tolerance_mm) and 0 <= axis_tolerance_mm <= AXIS_TOLERANCE_MM):
        raise FieldError("BAD_ARGUMENT", f"axis_tolerance_mm must be a finite length from 0 to {AXIS_TOLERANCE_MM} mm, "
                                         f"got {axis_tolerance_mm!r}")
    if not (_finite(stop_tolerance_mm) and stop_tolerance_mm >= 0):
        raise FieldError("BAD_ARGUMENT", f"--stop-tolerance-mm must be a finite length of at least 0 mm, "
                                         f"got {stop_tolerance_mm!r}")


def _chief_state(path_id: str, stops: dict[str, dict] | None, tolerance: float) -> dict:
    if stops is None:
        return {"status": STOP_NOT_CHECKED, "stop_id": None, "stop_offset_mm": None, "stop_tolerance_mm": None}
    state = dict(stops.get(path_id) or {"status": STOP_NOT_CROSSED, "stop_id": None, "stop_offset_mm": None})
    state["stop_tolerance_mm"] = None
    if state["status"] == STOP_CROSSED:
        state["status"] = OFF_STOP_CENTRE if state["stop_offset_mm"] > tolerance else WITHIN_STOP_TOLERANCE
        state["stop_tolerance_mm"] = tolerance
    return state


def _chief_reason(state: dict, tolerance: float) -> str:
    if state["status"] == OFF_STOP_CENTRE:
        return (f"{OFF_STOP_CENTRE}: the beam-centre ray crosses the aperture stop {state['stop_offset_mm']:.6g} mm from "
                f"the centre of its opening (tolerance {tolerance:g} mm), so it is not the field's chief ray; aim the "
                f"source at the centre of the stop's opening")
    if state["status"] == STOP_NOT_CROSSED:
        return f"{STOP_NOT_CROSSED}: the run has an aperture stop and this path's beam-centre ray does not cross it"
    detail = state.get("detail") or "it matches no aperture stop of the scene"
    return f"{STOP_UNRESOLVED}: the stop crossing of this path's beam-centre ray is not resolved: {detail}"


def _sub(a, b) -> list[float]:
    return [a[k] - b[k] for k in range(3)]


def _dot3(a, b) -> float:
    return sum(a[k] * b[k] for k in range(3))


def _point(value: Any) -> list[float] | None:
    if isinstance(value, (list, tuple)) and len(value) == 3 and all(_finite(c) for c in value):
        return [float(c) for c in value]
    return None


def _unit(value: Any) -> list[float] | None:
    vector = _point(value)
    norm = math.sqrt(_dot3(vector, vector)) if vector is not None else 0.0
    return [c / norm for c in vector] if vector is not None and norm > 0 else None


def height_on_focal_plane(chief: dict, focus_mm: list[float], axis: list[float]) -> tuple[float | None, str | None]:
    """Distance from the optical axis at which the chief ray's straight continuation (``chief``: point_mm and unit
    direction at the plane, in image space) meets the plane through ``focus_mm`` normal to the unit ``axis``; or None
    and the reason."""
    cosine = _dot3(chief["direction"], axis)
    if abs(cosine) < PARALLEL_COSINE:
        return None, "CHIEF_RAY_PARALLEL_TO_FOCAL_PLANE: the chief ray does not meet the paraxial focal plane"
    t = _dot3(_sub(focus_mm, chief["point_mm"]), axis) / cosine
    offset = _sub([chief["point_mm"][k] + t * chief["direction"][k] for k in range(3)], focus_mm)
    along = _dot3(offset, axis)
    return math.sqrt(max(0.0, _dot3(offset, offset) - along * along)), None


def _reference(value: Any, wavelength: float) -> dict:
    """An efl_for reply checked: efl_mm (> 0) and basis, with both or neither of rear_focal_point_mm and
    axis_direction."""
    if not (isinstance(value, dict) and _finite(value.get("efl_mm")) and value["efl_mm"] > 0
            and isinstance(value.get("basis"), str)):
        raise FieldError("EFL_NOT_STATED", f"no usable EFL at {wavelength} nm ({value!r}); state it with --efl")
    focus, axis = value.get("rear_focal_point_mm"), value.get("axis_direction")
    if (focus is None) != (axis is None) or (focus is not None and (_point(focus) is None or _unit(axis) is None)):
        raise FieldError("EFL_NOT_STATED", f"the paraxial rear focal point {focus!r} and outgoing axis {axis!r} at "
                                           f"{wavelength} nm do not give a focal plane; state the EFL with --efl")
    return {"efl_mm": float(value["efl_mm"]), "basis": value["basis"],
            "rear_focal_point_mm": _point(focus), "axis_direction": _unit(axis)}


def analyze(paths: list[dict], axes: dict[str, tuple[float, float, float]], efl_mm: float | None = None,
            axis_tolerance_mm: float = AXIS_TOLERANCE_MM, *, efl_for: EflFor | None = None,
            stops: dict[str, dict] | None = None, stop_tolerance_mm: float = STOP_TOLERANCE_MM,
            chiefs: dict[str, dict] | None = None, plane: dict | None = None) -> dict:
    """Field rows of ray-fan paths. ``efl_mm`` states the EFL, and h is measured on the plane itself. Otherwise
    ``efl_for(axis_source_id, wavelength_nm)`` gives the reference per wavelength (efl_mm, basis, and the paraxial
    rear_focal_point_mm and outgoing axis_direction, design frame); h is then measured on that focal plane from
    ``chiefs`` (path_id -> point_mm and unit direction of the chief ray at the plane, design frame; ``chief_rays``).
    With neither the analysis refuses (EFL_NOT_STATED). ``stops``: path_id -> the chief ray's stop crossing
    (``chief_ray_stops``); None leaves it unchecked (STOP_NOT_CHECKED). ``plane``: the plane (origin_mm), for the
    paraxial focus's distance from it."""
    check_arguments(efl_mm, axis_tolerance_mm, stop_tolerance_mm)
    missing = sorted({p["source_id"] for p in paths} - set(axes))
    if missing:
        raise FieldError("SOURCE_POSE_MISSING", f"no source pose for {', '.join(missing)} in the run's project.json")
    if not paths:
        raise FieldError("PATH_NOT_FOUND", "no path reaches the plane")
    axis_path = min(paths, key=lambda p: math.hypot(*p["chief_uv_mm"]))
    axis_height = math.hypot(*axis_path["chief_uv_mm"])
    if axis_height > axis_tolerance_mm:
        raise FieldError("AXIS_FIELD_MISSING",
                         f"no on-axis field: the chief ray nearest the plane origin is {axis_height:.6g} mm from it "
                         f"({axis_path['path_id']}, tolerance {axis_tolerance_mm:g} mm); field angles and image heights "
                         f"are measured from the on-axis field, so add a source whose chief ray lands on the plane origin")
    axis_source = axis_path["source_id"]
    axis = axes[axis_source]
    references: dict[float, dict] = {}
    for wavelength in sorted({p["wavelength_nm"] for p in paths}):
        if efl_mm is not None:
            references[wavelength] = {"efl_mm": float(efl_mm), "basis": "stated EFL (--efl)",
                                      "rear_focal_point_mm": None, "axis_direction": None}
        elif efl_for is not None:
            references[wavelength] = _reference(efl_for(axis_source, wavelength), wavelength)
        else:
            raise FieldError("EFL_NOT_STATED", "no EFL: state it with --efl (the engine's paraxial EFL is not available "
                                               "to this analysis)")
    rows = []
    for path in paths:
        angle = _angle_deg(axes[path["source_id"]], axis)
        height = math.hypot(*path["chief_uv_mm"])
        chief = _chief_state(path["path_id"], stops, stop_tolerance_mm)
        t_focus, t_rho, t_reason = _inner_zone_focus(path["fans"]["tangential"], "tangential")
        s_focus, s_rho, s_reason = _inner_zone_focus(path["fans"]["sagittal"], "sagittal")
        if chief["status"] not in _REPORTED:
            reason = _chief_reason(chief, stop_tolerance_mm)
            t_focus, t_rho, t_reason = None, None, reason
            s_focus, s_rho, s_reason = None, None, reason
        row = {"path_id": path["path_id"], "source_id": path["source_id"], "wavelength_nm": path["wavelength_nm"],
               "field_angle_deg": angle, "image_height_mm": height, "chief_ray": chief,
               "tangential_focus_mm": t_focus, "tangential_focus_reason": t_reason,
               "sagittal_focus_mm": s_focus, "sagittal_focus_reason": s_reason,
               "astigmatism_mm": None if t_focus is None or s_focus is None else t_focus - s_focus,
               "focus_zone_rho": max((r for r in (t_rho, s_rho) if r is not None), default=None)}
        reference = references[path["wavelength_nm"]]
        distortion_height: float | None = height
        height_reason = None
        if reference["rear_focal_point_mm"] is not None:
            chief_ray = (chiefs or {}).get(path["path_id"])
            if chief_ray is None:
                distortion_height = None
                height_reason = "CHIEF_RAY_MISSING: the chief ray's point and direction at the plane are not available"
            else:
                distortion_height, height_reason = height_on_focal_plane(
                    chief_ray, reference["rear_focal_point_mm"], reference["axis_direction"])
        row["distortion_height_mm"] = distortion_height
        if chief["status"] not in _REPORTED:
            row["reference_height_mm"], row["distortion_pct"] = None, None
            row["distortion_reason"] = _chief_reason(chief, stop_tolerance_mm)
        elif distortion_height is None:
            row["reference_height_mm"], row["distortion_pct"], row["distortion_reason"] = None, None, height_reason
        elif angle <= ZERO_ANGLE_DEG:
            if height <= axis_tolerance_mm:
                row["reference_height_mm"], row["distortion_pct"], row["distortion_reason"] = 0.0, 0.0, None
            else:
                row["reference_height_mm"], row["distortion_pct"] = 0.0, None
                row["distortion_reason"] = (f"ZERO_FIELD_NONZERO_HEIGHT: the field angle is 0 and the chief ray lands "
                                            f"{height:.6g} mm from the plane origin")
        else:
            h_ref = reference["efl_mm"] * math.tan(math.radians(angle))
            row["reference_height_mm"] = h_ref
            row["distortion_pct"] = 100.0 * (distortion_height - h_ref) / h_ref
            row["distortion_reason"] = None
        rows.append(row)
    rows.sort(key=lambda r: (r["wavelength_nm"], r["field_angle_deg"], r["path_id"]))
    published = {}
    origin = _point((plane or {}).get("origin_mm"))
    for wavelength, reference in sorted(references.items()):
        focus, axis = reference["rear_focal_point_mm"], reference["axis_direction"]
        entry = {"efl_mm": reference["efl_mm"], "basis": reference["basis"]}
        if focus is None:
            entry.update(distortion_plane="the plane itself, taken as the focal plane of the stated EFL",
                         rear_focal_point_mm=None, paraxial_focus_from_plane_mm=None)
        else:
            entry.update(distortion_plane="the paraxial rear focal plane (through rear_focal_point_mm, normal to the "
                                          "outgoing axis), each chief ray carried there in a straight line",
                         rear_focal_point_mm=focus,
                         paraxial_focus_from_plane_mm=None if origin is None else _dot3(_sub(focus, origin), axis))
        published[str(wavelength)] = entry
    return {"axis_source_id": axis_source, "references": published, "fields": rows}


# ------------------------------------------------------------------------------------- engine paraxial EFL

def _engine_command() -> list[str]:
    # the same engine the ray fans read the rays with
    engine = shutil.which("os-optics")
    return [engine] if engine else [sys.executable, "-m", "os_optics"]


def axis_components(project: dict, axis_source_id: str, monitor: str, hint: str = EFL_HINT) -> list[str]:
    """Instance ids of the components on the on-axis field's emission line, between its source and the plane, in the
    order light meets them. Refuses (EFL_NOT_STATED, the message ending in ``hint``) when a component ahead of the
    source lies off that line."""
    source = next(c for c in project.get("components", []) if c.get("instance_id") == axis_source_id)
    origin = source["nominal_pose"]["position_mm"]
    axis = _rotate(source["nominal_pose"]["quaternion_wxyz"], (0.0, 0.0, 1.0))

    def place(position: list[float]) -> tuple[float, float]:
        r = [position[k] - origin[k] for k in range(3)]
        along = sum(r[k] * axis[k] for k in range(3))
        return along, math.sqrt(sum((r[k] - along * axis[k]) ** 2 for k in range(3)))

    plane = next((o for o in project.get("observations", []) if o.get("observation_id") == monitor), None)
    end = math.inf
    if plane is not None:
        end, lateral = place(plane["pose"]["position_mm"])
        if lateral > AXIS_TOLERANCE_MM:
            raise FieldError("EFL_NOT_STATED", f"the plane {monitor} is {lateral:.6g} mm off the emission line of the "
                                               f"on-axis field {axis_source_id}; {hint}")
    listed = []
    for component in project.get("components", []):
        if component.get("optical_role") == "source":
            continue
        along, lateral = place(component["nominal_pose"]["position_mm"])
        if along <= 0 or along >= end:
            continue
        if lateral > ON_AXIS_LINE_MM:
            raise FieldError("EFL_NOT_STATED",
                             f"component {component['instance_id']} lies {lateral:.6g} mm off the emission line of the "
                             f"on-axis field {axis_source_id}, so the elements do not stand on one straight axis for "
                             f"the engine's paraxial EFL; {hint}")
        listed.append((along, component["instance_id"]))
    if not listed:
        raise FieldError("EFL_NOT_STATED", f"no element on the emission line of the on-axis field {axis_source_id} "
                                           f"before the plane; {hint}")
    return [instance for _along, instance in sorted(listed)]


def paraxial_reference(project_file: Path, project: dict, axis_source_id: str, monitor: str, wavelength_nm: float,
                       runner=subprocess.run) -> dict:
    """The engine's paraxial EFL of the elements on the on-axis field's line at one wavelength, with its basis, the rear
    focal point (design frame, mm) and the outgoing axis direction (the last stretch of the paraxial axis path)."""
    components = axis_components(project, axis_source_id, monitor)
    argv = ["project", "paraxial", str(project_file), "--components", *components, "--wavelength-nm",
            repr(float(wavelength_nm)), "--source", axis_source_id]
    done = runner(_engine_command() + argv, capture_output=True, text=True, encoding="utf-8")
    try:
        reply = json.loads(done.stdout)
    except (TypeError, ValueError):
        reply = None
    system = reply.get("system") if isinstance(reply, dict) else None
    if done.returncode != 0 or not isinstance(system, dict):
        error = reply.get("error") if isinstance(reply, dict) else None
        detail = ", ".join(p.get("code", "") for p in (error or {}).get("problems", [])) or (error or {}).get("code") \
            or (done.stderr or done.stdout or "").strip()[-300:]
        raise FieldError("EFL_NOT_STATED", f"the engine's paraxial analysis of {' '.join(components)} at "
                                           f"{wavelength_nm} nm gave no EFL ({detail}); state the EFL with --efl")
    return paraxial_efl(reply, components, wavelength_nm)


def paraxial_efl(reply: dict, components: list[str], wavelength_nm: float, hint: str = EFL_HINT) -> dict:
    """The EFL reference of an Engine paraxial reply (``os-optics project paraxial`` / ``paraxial_analysis``):
    ``efl_mm`` (> 0), ``basis``, ``rear_focal_point_mm`` and the outgoing ``axis_direction`` (the last stretch of the
    paraxial axis path). Refuses (EFL_NOT_STATED) an afocal or non-positive EFL or a reply without a focal plane."""
    system = reply.get("system") if isinstance(reply, dict) else None
    system = system if isinstance(system, dict) else {}
    stated: Any = system.get("efl_mm")
    efl = float(stated) if _finite(stated) else math.nan
    if system.get("afocal") or not efl > 0:
        raise FieldError("EFL_NOT_STATED", f"the engine's paraxial EFL of {' '.join(components)} at {wavelength_nm} nm "
                                           f"is {stated!r} (afocal {system.get('afocal')!r}), not a positive length; "
                                           f"the first-order reference needs one; {hint}")
    focus = _point(system.get("rear_focal_point_mm"))
    stretches = reply.get("axis_path")
    axis = _unit(stretches[-1].get("direction") if isinstance(stretches, list) and stretches
                 and isinstance(stretches[-1], dict) else reply.get("optical_axis"))
    if focus is None or axis is None:
        raise FieldError("EFL_NOT_STATED", f"the engine's paraxial analysis of {' '.join(components)} at "
                                           f"{wavelength_nm} nm gives no rear focal point "
                                           f"({system.get('rear_focal_point_mm')!r}) or outgoing axis to measure "
                                           f"the reference height on; {hint}")
    return {"efl_mm": efl, "basis": (f"engine paraxial EFL of {' '.join(components)} at {wavelength_nm} nm in the "
                                     f"air of the Project's environment (os-optics project paraxial)"),
            "rear_focal_point_mm": focus, "axis_direction": axis}


# ------------------------------------------------------------------------------------------ aperture stops

def _opening_centre(surface: dict) -> tuple[float, float] | None:
    """The centre of a stop surface's opening in its plane (local X, Y; mm): its aperture_center_mm, (0, 0) when the
    scene states none; None when the value is not two finite numbers."""
    centre = surface.get("aperture_center_mm")
    if centre is None:
        return 0.0, 0.0
    if isinstance(centre, (list, tuple)) and len(centre) == 2 and all(_finite(c) for c in centre):
        return float(centre[0]), float(centre[1])
    return None


def scene_stops(scene: dict) -> list[dict]:
    """Every aperture-stop surface of a run's optical scene. stop_id: the Engine surface id the run's segments name
    (``<element>/<surface>``; a unit member's ``<element>/<member>-<surface>``); centre_mm: the centre of the stop's
    opening (its aperture_center_mm in the stop plane's local X, Y, carried to the design frame); normal: the unit plane
    normal; opening_center_mm: that centre in the plane's local X, Y. A stop whose plane or opening centre the scene
    does not give has centre_mm None and the reason in geometry_issue."""
    stops: list[dict] = []

    def walk(node: dict, position: list[float], quaternion: list[float], element: str, members: list[str]) -> None:
        for surface in node.get("surfaces") or []:
            if surface.get("role") != "APERTURE_STOP":
                continue
            surface_id = str(surface.get("surface_id"))
            stop_id: str | None
            if not members:
                stop_id = f"{element}/{surface_id}"
            elif len(members) == 1:
                stop_id = f"{element}/{members[0]}-{surface_id}"
            else:  # the Engine names the surfaces of one member level only
                stop_id = None
            label = "/".join([element, *members, surface_id])
            entry: dict = {"stop_id": stop_id, "label": label, "centre_mm": None, "normal": None,
                           "opening_center_mm": None, "clear_radius_mm": surface.get("clear_radius_mm"),
                           "geometry_issue": None}
            opening = _opening_centre(surface)
            vertex = surface.get("vertex_z_mm", 0.0)
            if surface.get("origin_mm") is not None or not (vertex is None or _finite(vertex)):
                entry["geometry_issue"] = (f"the scene does not place the stop surface {label} by a finite vertex_z_mm "
                                           f"on its element axis (vertex_z_mm {vertex!r}, origin_mm "
                                           f"{surface.get('origin_mm')!r})")
            elif opening is None:
                entry["geometry_issue"] = (f"the opening centre of the stop surface {label} "
                                           f"({surface.get('aperture_center_mm')!r}) is not two finite lengths")
            else:
                offset = _rotate(quaternion, (opening[0], opening[1], float(vertex or 0.0)))
                entry.update(centre_mm=[position[k] + offset[k] for k in range(3)],
                             normal=list(_rotate(quaternion, (0.0, 0.0, 1.0))), opening_center_mm=list(opening))
            stops.append(entry)
        for member in node.get("members") or []:
            pose = member.get("pose") or {}
            shift = _rotate(quaternion, tuple(pose.get("position_mm", (0.0, 0.0, 0.0))))  # type: ignore[arg-type]
            walk(member.get("body") or {}, [position[k] + shift[k] for k in range(3)],
                 _qmul(quaternion, pose.get("quaternion_wxyz", [1.0, 0.0, 0.0, 0.0])), element,
                 [*members, str(member.get("member_id"))])

    for element in scene.get("elements") or []:
        pose = element.get("pose") or {}
        walk(element.get("geometry") or {}, list(pose.get("position_mm", [0.0, 0.0, 0.0])),
             list(pose.get("quaternion_wxyz", [1.0, 0.0, 0.0, 0.0])), str(element.get("element_id")), [])
    return stops


def _plane_distance(point: list[float], stop: dict) -> float:
    return _dot3(_sub(point, stop["centre_mm"]), stop["normal"])


def _in_plane(point: list[float], stop: dict) -> bool:
    return abs(_plane_distance(point, stop)) <= 1e-6 * max(1.0, math.sqrt(_dot3(stop["centre_mm"], stop["centre_mm"])))


def _offset(point: list[float], stop: dict) -> float:
    """Distance of a point of the stop plane from the centre of the stop's opening, in that plane."""
    d = _sub(point, stop["centre_mm"])
    normal = _dot3(d, stop["normal"])
    return math.sqrt(max(0.0, _dot3(d, d) - normal * normal))


def segment_end(segment: dict) -> list[float] | None:
    """The end point of a chief-ray segment (design frame, mm): ``end_mm`` of a run's simulation_result.json, or
    ``end_x_mm`` / ``end_y_mm`` / ``end_z_mm`` of ``RunResults.segments()``; None when it states none."""
    point = _point(segment.get("end_mm"))
    if point is None and "end_x_mm" in segment:
        point = _point([segment.get(f"end_{k}_mm") for k in "xyz"])
    return point


def _crossing(segment: dict, by_id: dict[str, dict], stops: list[dict]) -> tuple[dict | None, str | None, str | None]:
    """The stop a segment ends on: (stop, identified_by, None); (None, None, why) when it ends on a stop that is not
    resolved; (None, None, None) when it does not end on a stop."""
    surface_id = segment.get("end_surface_id")
    event = segment.get("end_event")
    if isinstance(surface_id, str) and surface_id:
        stop = by_id.get(surface_id)
        if stop is not None:
            return stop, "ENGINE_SURFACE_ID", None
        if event == "aperture_stop":
            return None, None, (f"the segment ends at an aperture stop ({surface_id}) that is not an aperture-stop "
                                f"surface of the run's optical scene")
        return None, None, None
    if event != "aperture_stop":
        return None, None, None
    point = segment_end(segment)
    if point is None:
        return None, None, "the segment ends at an aperture stop without a surface id or an end point"
    planes = [stop for stop in stops if stop["centre_mm"] is not None and _in_plane(point, stop)]
    if len(planes) == 1:
        return planes[0], "STOP_PLANE", None
    return None, None, (f"the segment ends at an aperture stop without a surface id, and its end point lies in the plane "
                        f"of {len(planes)} aperture stops of the scene, not exactly one")


def stop_crossings(segments: list[dict], stops: list[dict]) -> list[dict]:
    """Every crossing of an aperture stop by one path's chief-ray ``segments`` (in order), as ``stop`` (a
    ``scene_stops`` entry), ``point_mm``, ``offset_mm`` (distance from the centre of the stop's opening, in the stop
    plane), ``identified_by`` and ``problem`` (why the crossing is not resolved; ``point_mm`` / ``offset_mm`` are then
    None). A list ends at its first unresolved crossing."""
    by_id = {stop["stop_id"]: stop for stop in stops if stop["stop_id"] is not None}
    found: list[dict] = []
    for segment in segments:
        stop, identified_by, problem = _crossing(segment, by_id, stops)
        if stop is None and problem is None:
            continue
        point = segment_end(segment)
        if stop is not None:
            if stop["centre_mm"] is None:
                problem = stop["geometry_issue"]
            elif point is None:
                problem = f"the segment ending at {stop['stop_id']} has no end point"
            elif not _in_plane(point, stop):
                problem = (f"the segment ends at {stop['stop_id']} {_plane_distance(point, stop):.6g} mm off "
                           f"the stop plane the scene gives")
        if problem is not None or stop is None or point is None:
            found.append({"stop": stop, "point_mm": None, "offset_mm": None, "identified_by": identified_by,
                          "problem": problem or "the stop crossing is not resolved"})
            break
        found.append({"stop": stop, "point_mm": point, "offset_mm": _offset(point, stop),
                      "identified_by": identified_by, "problem": None})
    return found


def chief_ray_stops(result: dict, scene: dict) -> dict[str, dict]:
    """path_id -> where the path's chief ray crosses the aperture stop: status, stop_id, stop_offset_mm (its distance
    from the centre of the stop's opening, in the stop plane; the largest one when it crosses several), identified_by
    (ENGINE_SURFACE_ID: the segment's end surface is the stop's Engine surface id, whatever its event, e.g. a sampled
    stop's complex_transmission; STOP_PLANE: a segment without a surface id whose aperture_stop end lies in exactly one
    stop plane) and detail (why a crossing is CHIEF_RAY_STOP_UNRESOLVED)."""
    stops = scene_stops(scene)
    states: dict[str, dict] = {}
    for path in result.get("paths") or []:
        state: dict = {"status": NO_APERTURE_STOP, "stop_id": None, "stop_offset_mm": None, "identified_by": None,
                       "detail": None}
        if stops:
            state["status"] = STOP_NOT_CROSSED
            for crossing in stop_crossings(path.get("segments") or [], stops):
                stop = crossing["stop"]
                if crossing["problem"] is not None:
                    state.update(status=STOP_UNRESOLVED, stop_id=None if stop is None else stop["stop_id"],
                                 stop_offset_mm=None, identified_by=crossing["identified_by"],
                                 detail=crossing["problem"])
                    break
                if state["stop_offset_mm"] is None or crossing["offset_mm"] > state["stop_offset_mm"]:
                    state.update(status=STOP_CROSSED, stop_id=stop["stop_id"], stop_offset_mm=crossing["offset_mm"],
                                 identified_by=crossing["identified_by"])
        states[path["path_id"]] = state
    return states


_UNREACHED_MESSAGES = {
    "NO_COUNTED_RAYS_AT_MONITOR": "the path meets the plane only outside its active area",
    "NO_RAYS_AT_MONITOR": "the path ends before the plane"}


def unreached_fields(fans: dict, result: dict, declared: list[str], reached: set[str]) -> list[dict]:
    """Every path or declared source without a row: the fans' unreached and refused paths, and each declared source
    without a path in the run. ``reached``: the source ids that have a row."""
    paths = {p.get("path_id"): p for p in result.get("paths") or [] if isinstance(p, dict)}
    entries = []
    for item in fans.get("unreached") or []:
        reason = item.get("reason")
        message = _UNREACHED_MESSAGES.get(reason, "the path has no counted rays at the plane")
        if item.get("termination"):
            message += f" (termination {item['termination']}" + (
                f" at {item['termination_surface_id']})" if item.get("termination_surface_id") else ")")
        entries.append({"path_id": item.get("path_id"), "source_id": item.get("source_id"),
                        "wavelength_nm": item.get("wavelength_nm"), "termination": item.get("termination"),
                        "termination_surface_id": item.get("termination_surface_id"), "reason": reason,
                        "message": message})
    for item in fans.get("refused") or []:
        path = paths.get(item.get("path_id"), {})
        entries.append({"path_id": item.get("path_id"), "source_id": item.get("source_id"),
                        "wavelength_nm": item.get("wavelength_nm"), "termination": path.get("termination"),
                        "termination_surface_id": path.get("termination_surface_id"), "reason": item.get("code"),
                        "message": f"the path reaches the plane and its fans are refused: {item.get('message')}"})
    listed = reached | {entry["source_id"] for entry in entries}
    for source_id in declared:
        if source_id not in listed:
            entries.append({"path_id": None, "source_id": source_id, "wavelength_nm": None, "termination": None,
                            "termination_surface_id": None, "reason": "NO_PATH_IN_RUN",
                            "message": "the Project declares this source and the run has no path of it"})
    entries.sort(key=lambda e: (str(e["source_id"]), e["wavelength_nm"] or 0.0, str(e["path_id"])))
    return entries


def chief_rays(collected: dict) -> dict[str, dict]:
    """path_id -> the chief ray's point and unit direction at the plane (design frame), from ray_fans.collect()."""
    chiefs = {}
    for path_id, rays in (collected.get("rays") or {}).items():
        chief = next((r for r in rays if r.get("chief") and r.get("counted") is True), None)
        if chief is None:
            continue
        point = _point([chief.get(f"position_{k}_mm") for k in "xyz"])
        direction = _unit([chief.get(f"direction_{k}") for k in "xyz"])
        if point is not None and direction is not None:
            chiefs[path_id] = {"point_mm": point, "direction": direction}
    return chiefs


def reflected_refusal(collected: dict) -> FieldError | None:
    """The refusal of a plane that only reflected paths reach (REFLECTED_PATH_UNSUPPORTED, with each path's reflection
    count before the plane), from ray_fans.collect()'s data; None when a path reaching the plane is not reflected."""
    selected = list(collected.get("selected") or [])
    arrivals = collected.get("arrivals") or {}
    counts = {path_id: ray_fans._reflections(arrivals.get(path_id) or {}) for path_id in selected}
    if not selected or not all(counts.values()):
        return None
    listed = ", ".join(f"{path_id} ({count})" for path_id, count in counts.items())
    return FieldError(
        REFLECTED_PATH_UNSUPPORTED,
        f"every path reaching {collected.get('monitor')} is reflected before it (path, reflections: {listed}); field "
        f"rows need the pupil orientation at the plane, which is not resolved for reflected paths, so the field "
        f"analysis supports paths without a reflection before the plane only",
        details={"monitor": collected.get("monitor"),
                 "reflected_paths": [{"path_id": path_id, "reflections": count} for path_id, count in counts.items()]})


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FieldError("RUN_FILE_UNREADABLE", f"{path}: {exc}") from None


def build(run_dir: Path, monitor: str, efl_mm: float | None = None,
          axis_tolerance_mm: float = AXIS_TOLERANCE_MM, stop_tolerance_mm: float = STOP_TOLERANCE_MM,
          runner=subprocess.run) -> dict:
    check_arguments(efl_mm, axis_tolerance_mm, stop_tolerance_mm)
    project_file = run_dir / "project.json"
    try:
        project = json.loads(project_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FieldError("PROJECT_UNREADABLE", f"{project_file}: {exc}") from None
    result = _read_json(run_dir / "simulation_result.json")
    scene = _read_json(run_dir / "optical_scene.json")
    collected = ray_fans.collect(run_dir, monitor, runner=runner)
    try:
        fans = ray_fans.assemble(collected)
    except ray_fans.FanError as exc:
        refusal = reflected_refusal(collected) if exc.code == "NO_FANS" else None
        if refusal is not None:
            raise refusal from None
        raise

    def efl_for(axis_source_id: str, wavelength_nm: float) -> dict:
        return paraxial_reference(project_file, project, axis_source_id, monitor, wavelength_nm, runner)

    axes = source_axes(project)
    document = analyze(fans["paths"], axes, efl_mm, axis_tolerance_mm, efl_for=efl_for,
                       stops=chief_ray_stops(result, scene), stop_tolerance_mm=stop_tolerance_mm,
                       chiefs=chief_rays(collected), plane=collected["plane"])
    stops = scene_stops(scene)
    return {"schema": "os-optics-kit.field-aberrations", "schema_version": 1, "run": str(run_dir), "monitor": monitor,
            "basis": "published engine ray data at the plane, the run's chief-ray segments and aperture-stop surfaces; "
                     "field angle from source poses; geometric optics",
            "chief_ray": ("the beam-centre ray of each source, checked against the centre of the aperture stop's "
                          f"opening (stop_tolerance_mm {stop_tolerance_mm:g})" if stops else
                          "the beam-centre ray of each source: the run has no aperture stop, so it is the field's "
                          "chief ray only for a source aimed at the entrance-pupil centre"),
            "conventions": {"distortion_pct": "(h - EFL tan(theta)) / (EFL tan(theta)), h (distortion_height_mm) and "
                                              "EFL tan(theta) (reference_height_mm) on one plane; references names "
                                              "the EFL, its basis and that plane per wavelength",
                            "image_height_mm": "distance of the chief ray from the plane origin (the on-axis field's "
                                               "image point, within 1e-3 mm)",
                            "focus_mm": "along the plane normal from the plane, positive behind it; innermost pupil "
                                        "zone (focus_zone_rho names it: its focus differs from the paraxial focus "
                                        "by terms of order rho^2)"},
            "aperture_stops": [{key: stop[key] for key in ("stop_id", "label", "opening_center_mm", "centre_mm",
                                                           "normal", "geometry_issue")} for stop in stops],
            **document,
            "unreached_fields": unreached_fields(fans, result, sorted(axes),
                                                 {row["source_id"] for row in document["fields"]})}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="os-optics-kit field", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run", type=Path)
    parser.add_argument("--monitor", required=True)
    parser.add_argument("--efl", type=float, help="effective focal length (mm, > 0) for the distortion reference; "
                                                    "default: the engine's paraxial EFL")
    parser.add_argument("--stop-tolerance-mm", type=float, default=STOP_TOLERANCE_MM,
                        help="how far a chief ray may cross the aperture stop from the centre of its opening "
                             "(default %(default)s)")
    parser.add_argument("--out", type=Path)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        if exc.code in (0, None):
            return 0
        print(json.dumps({"error": {"code": "BAD_ARGUMENT", "message": "invalid arguments"}}), file=sys.stderr)
        return 4
    try:
        document = build(args.run, args.monitor, args.efl, stop_tolerance_mm=args.stop_tolerance_mm)
    except (FieldError, ray_fans.FanError) as exc:
        error: dict = {"code": exc.code, "message": str(exc)}
        if exc.details is not None:
            error["details"] = exc.details
        print(json.dumps({"error": error}), file=sys.stderr)
        return 4
    text = json.dumps(document, indent=2, allow_nan=False)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(json.dumps({"status": "WRITTEN", "path": str(args.out), "fields": len(document["fields"]),
                          "unreached_fields": len(document["unreached_fields"])}))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
