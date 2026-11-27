"""Field points of a Project at stated angles or heights: chief-ray hit point and angle, a first-order comparison and,
on request, the optical MTF at a fixed image plane.

os-optics-kit field-points PROJECT --plane PLANE --out DIR
    (--angles-deg A[,B..] | --object-heights-mm H[,..] | --image-heights-mm H[,..])
    [--azimuth-deg PHI] [--source SRC] [--configuration ID] [--wavelengths-nm W[,..]]
    [--chief-ray-wavelength-nm W] [--expect-field-kind ANGULAR|OBJECT_HEIGHT|IMAGE_HEIGHT]
    [--mtf-frequencies F[,..]] [--imaging-request FILE] [--launch-grid N] [--no-convergence]
    [--aim-tolerance-mm T] [--height-tolerance-mm T] [--pivot-mm X,Y,Z] [--write-run] [--overwrite]

Each field point is a copy of the reference source (--source, else the Project's only source) turned about a
design-frame axis across the beam and placed so that its chief ray (the beam-centre ray the Engine traces) crosses the
centre of the aperture stop within --aim-tolerance-mm (ray aiming, measured by Engine evaluations). All points are
extra sources of one evaluated Project (the input's other sources removed), so one evaluation and one imaging call
cover them; the input Project is never modified. Field kinds, one per request: ANGULAR (degrees from the reference
axis; object at infinity or at a finite distance), OBJECT_HEIGHT (mm in the object plane; finite object only) and
IMAGE_HEIGHT (mm on the plane from the on-axis chief ray's hit point; the angle or object height is solved).
--azimuth-deg sets the field direction in the plane's (u, v) axes, from +u toward +v: an ANGULAR beam is turned
toward it, an OBJECT_HEIGHT object point moves along it. --expect-field-kind refuses a request of another kind (the
kind the Project's documentation states). The object conjugate comes from the reference source's waist (within one
Rayleigh range of the source: infinity; at least ten upstream: a point object at the waist).

Per point: the chief ray's hit point and height on the plane, its angle to the plane normal, the stop offset and aim
state, the first-order comparison (FIRST_ORDER_COMPARISON: f tan(theta) at infinity, m h at a finite object, from
the Engine's paraxial analysis; not a distortion figure), pupil rays launched/arrived and, with --mtf-frequencies,
the tangential and sagittal optical MTF of every spectral line from the Engine's imaging analysis (one curve per line,
no polychromatic weighting) with sampled minima per line and over all lines (CONFIRMED only when every sample is an
exact value). A point that cannot be evaluated keeps a reason (NO_ARRIVAL, OUTSIDE_PLANE, STOP_NOT_REACHED,
CHIEF_RAY_PATH_DIFFERS, AIM_NOT_CONVERGED, HEIGHT_NOT_REACHED, TRACE_BUDGET_EXHAUSTED) and leaves the minima
UNCONFIRMED. The evaluated Project raises the trace budget when the field points need more (derivation).

Writes DIR/field-points.json (schema os-optics-kit.field-points 1), DIR/evaluated-project.json, DIR/imaging.json
(imaging-<n>.json for more than 32 points; 'os-optics-kit mtf --imaging' draws them) and, with --write-run, the
evaluated run DIR/run for 'os-optics-kit field', 'fans' and 'mtf'. Exit 0 COMPLETED (every point evaluated and every
MTF sample exact), 2 INCOMPLETE, 4 refused (JSON error on stderr).
"""

from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass, field
import hashlib
import math
from pathlib import Path
import re
import time
from typing import Any, Sequence

from . import field_aberrations as fa
from . import field_geometry as fg
from . import geometry3d as g
from . import imaging_results as ir
from . import workflow_common as wc

PROG = "os-optics-kit field-points"
SCHEMA = "os-optics-kit.field-points"
SCHEMA_VERSION = 1
EXIT_CODES = {**wc.WORKFLOW_EXIT_CODES, "INCOMPLETE": 2}
COMPLETED, INCOMPLETE = "COMPLETED", "INCOMPLETE"
EVALUATED, NOT_EVALUATED = "EVALUATED", "NOT_EVALUATED"
AIMED, AIM_NOT_CONVERGED_STATE, NOT_AIMED = "AIMED", "NOT_CONVERGED", "NOT_AIMED"
FIRST_ORDER_LABEL = "FIRST_ORDER_COMPARISON"
DEFAULT_AIM_TOLERANCE_MM = 1e-3
DEFAULT_HEIGHT_TOLERANCE_MM = 1e-4
#: Engine evaluations of the aiming / height solve, all points together
MAX_EVALUATIONS = 30
#: a point whose chief ray misses the stop this many times is STOP_NOT_REACHED
MAX_MISSES = 3
#: the Engine's imaging analysis takes at most this many fields (source ids) per call
MAX_IMAGING_FIELDS = 32
#: keys of an imaging request this command sets itself
IMAGING_KEYS = ("fields", "wavelengths_nm", "frequencies_cyc_per_mm", "crossing_index")
#: probe chief rays of the entrance-pupil estimate: a turn (deg) and a shift (fraction of the stop's clear radius)
PROBE_TILT_DEG = 1.0
PROBE_SHIFT_FRACTION = 0.1
#: the plane's aperture radius while aiming (mm): every chief ray's hit point is read, the pose is unchanged
WORKING_APERTURE_MM = 1.0e4
#: spectral lines of one source match within this many nm
LINE_MATCH_NM = 1e-6
AXIS_SOURCE_ID = "FP_AXIS"
#: the evaluated Project's trace budget covers this many times the field points' own chief-ray paths and steps
BUDGET_HEADROOM = 2
#: the entries field-points writes; ``produced(request)`` gives those of one request
FILES = ("field-points.json", "evaluated-project.json", "imaging.json", re.compile(r"imaging-\d+\.json"), "run")
SCOPE = {
    "supported": ("centred, rotationally symmetric imaging prescriptions: a reference GAUSSIAN_BEAM source on the "
                  "optical axis, one aperture stop (or a stated pivot), field points traced through one crossing "
                  "of a fixed observation plane, the optical MTF of each spectral line from the Engine's imaging "
                  "analysis"),
    "not_supported": ["decentred or tilted systems (the chief ray is aimed at the stop centre from the reference axis)",
                      "a reflection before the plane (folded paths)",
                      "extended or non-Gaussian sources as the reference",
                      "configurations as field points (a Project's configurations are design states)",
                      "a polychromatic or spectrally weighted MTF (the Engine gives one curve per spectral line)",
                      "paraxial image heights (IMAGE_HEIGHT is the real chief-ray height)"]}


class FieldPointsError(wc.WorkflowError):
    """A refused field-points request (exit 4)."""


class _Parser(wc.ArgumentParser):
    def error(self, message):  # usage errors are BAD_ARGUMENT
        raise FieldPointsError("BAD_ARGUMENT", message)


# ------------------------------------------------------------------------------------------- request

@dataclass
class Request:
    """A field-points request (the CLI's options; ``values`` in the kind's unit, in request order)."""

    project: Path
    plane: str
    kind: str
    values: list[float]
    azimuth_deg: float = 0.0
    source: str | None = None
    configuration: str | None = None
    wavelengths_nm: list[float] | None = None
    chief_ray_wavelength_nm: float | None = None
    expect_field_kind: str | None = None
    mtf_frequencies: list[float] | None = None
    imaging_request: dict | None = None
    imaging_request_file: str | None = None
    launch_grid: int | None = None
    convergence: bool = True
    aim_tolerance_mm: float = DEFAULT_AIM_TOLERANCE_MM
    height_tolerance_mm: float = DEFAULT_HEIGHT_TOLERANCE_MM
    pivot_mm: list[float] | None = None
    write_run: bool = False


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def check_request(request: Request) -> None:
    """Refuse a request whose values cannot mean anything (BAD_ARGUMENT, FIELD_KIND_MISMATCH), before any Engine
    use."""
    if request.kind not in fg.FIELD_KINDS:
        raise FieldPointsError("BAD_ARGUMENT", f"field kind must be one of {', '.join(fg.FIELD_KINDS)}")
    if request.expect_field_kind is not None and request.expect_field_kind != request.kind:
        raise FieldPointsError(
            "FIELD_KIND_MISMATCH", f"the request gives {request.kind} field points and the Project's field kind is "
            f"{request.expect_field_kind}: an angle, an object height and an image height are not interchangeable",
            requested=request.kind, expected=request.expect_field_kind)
    if not request.values:
        raise FieldPointsError("BAD_ARGUMENT", "state at least one field point")
    upper = fg.MAX_ANGLE_DEG if request.kind == "ANGULAR" else math.inf
    for value in request.values:
        if not (_finite(value) and 0.0 <= value < upper):
            raise FieldPointsError("BAD_ARGUMENT", f"field values must be finite, at least 0 (the direction is "
                                   f"--azimuth-deg){'' if upper == math.inf else f' and below {upper:g} deg'}; "
                                   f"got {value!r}")
    for name, value in (("azimuth", request.azimuth_deg), ("aim tolerance", request.aim_tolerance_mm),
                        ("height tolerance", request.height_tolerance_mm)):
        if not _finite(value) or (name != "azimuth" and value <= 0):
            raise FieldPointsError("BAD_ARGUMENT", f"the {name} must be a finite number"
                                   f"{'' if name == 'azimuth' else ' above 0 mm'}, got {value!r}")
    if request.wavelengths_nm is not None:
        if not request.wavelengths_nm or any(not (_finite(w) and w > 0) for w in request.wavelengths_nm):
            raise FieldPointsError("BAD_ARGUMENT", "wavelengths must be positive vacuum wavelengths in nm")
        if len({round(w / LINE_MATCH_NM) for w in request.wavelengths_nm}) != len(request.wavelengths_nm):
            raise FieldPointsError("BAD_ARGUMENT", "wavelengths must be distinct")
    if request.mtf_frequencies is not None:
        frequencies = request.mtf_frequencies
        if not frequencies or len(frequencies) > 128 or any(not (_finite(f) and f >= 0) for f in frequencies):
            raise FieldPointsError("BAD_ARGUMENT", "MTF frequencies: 1 to 128 values of at least 0 cycles/mm")
    if request.pivot_mm is not None and (len(request.pivot_mm) != 3 or not all(_finite(c) for c in request.pivot_mm)):
        raise FieldPointsError("BAD_ARGUMENT", "--pivot-mm takes three finite coordinates X,Y,Z (mm, design frame)")
    stated = sorted(key for key in IMAGING_KEYS if (request.imaging_request or {}).get(key) is not None)
    if stated:
        raise FieldPointsError("BAD_ARGUMENT", f"the imaging request states {', '.join(stated)}, which field-points "
                               "sets itself (the field points, their lines, --mtf-frequencies, the chief ray's "
                               "crossing)", keys=stated)
    if request.mtf_frequencies is None and (request.imaging_request or request.launch_grid is not None
                                            or not request.convergence):
        raise FieldPointsError("BAD_ARGUMENT", "--imaging-request, --launch-grid and --no-convergence set the MTF's "
                               "pupil sampling: give --mtf-frequencies")


# --------------------------------------------------------------------------------------------- input

def read_input(path: Path) -> tuple[dict, dict]:
    """The input Project and its identity: the file sha256 as read, ``sha256_lf`` (after CRLF -> LF, the hash
    os-optics-lens binds a record's Project to) and the Engine's project hash, input hash and catalog release."""
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise FieldPointsError("PROJECT_UNREADABLE", f"{path}: {exc}") from None
    try:
        import json

        document = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise FieldPointsError("PROJECT_UNREADABLE", f"{path} is not a JSON Project: {exc}") from None
    if not isinstance(document, dict):
        raise FieldPointsError("PROJECT_UNREADABLE", f"{path} is not a JSON Project object")
    checked = wc.check(document)
    if not checked.get("ok"):
        error = checked.get("error") or {}
        raise FieldPointsError("PROJECT_REFUSED", "the Engine refuses the input Project",
                               problems=list(error.get("problems") or [])[:10])
    identity = {"path": Path(path).as_posix(), "sha256": hashlib.sha256(data).hexdigest(),
                "sha256_lf": hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest(),
                "project_hash": checked.get("project_hash"), "engine_input_hash": checked.get("engine_input_hash"),
                "catalog_version": checked.get("catalog_version"), "configuration_id": None}
    return document, identity


def configured(document: dict, configuration_id: str | None) -> dict:
    """The Project of one configuration (the Engine's configured Project), or the Project itself without
    configurations. CONFIGURATION_REQUIRED / CONFIGURATION_UNKNOWN otherwise."""
    ids = [c.get("configuration_id") for c in document.get("configurations") or []]
    if not ids:
        if configuration_id is not None:
            raise FieldPointsError("CONFIGURATION_UNKNOWN", f"the Project declares no configurations, so there is no "
                                   f"{configuration_id!r}", configurations=[])
        return copy.deepcopy(document)
    if configuration_id is None:
        raise FieldPointsError("CONFIGURATION_REQUIRED", "the Project declares configurations: choose one with "
                               "--configuration", configurations=ids)
    if configuration_id not in ids:
        raise FieldPointsError("CONFIGURATION_UNKNOWN", f"no configuration {configuration_id!r}", configurations=ids)
    from os_optics.local.configurations import configured_project

    return configured_project(document, configuration_id).payload()


def _sources(document: dict) -> list[dict]:
    return [c for c in document.get("components") or [] if c.get("optical_role") == "source"]


def reference_source(document: dict, source_id: str | None) -> dict:
    sources = _sources(document)
    if source_id is None:
        if len(sources) != 1:
            raise FieldPointsError("SOURCE_REQUIRED", "the Project has several sources: name the reference source "
                                   "(on the optical axis) with --source",
                                   sources=[s.get("instance_id") for s in sources])
        return sources[0]
    found = next((s for s in sources if s.get("instance_id") == source_id), None)
    if found is None:
        raise FieldPointsError("SOURCE_NOT_FOUND", f"no source {source_id!r}",
                               sources=[s.get("instance_id") for s in sources])
    return found


def spectral_lines(model: dict, wavelengths: list[float] | None) -> list[dict]:
    """The field points' lines ``[{wavelength_nm, power_w}]``: the reference source's own (its stated powers), or
    ``wavelengths`` at the reference's line powers where they match all of them, else the reference's total power in
    equal shares."""
    stated: list[dict] | None = None
    if model.get("spectral_lines"):
        stated = [{"wavelength_nm": float(line["wavelength_nm"]), "power_w": float(line["power_w"])}
                  for line in model["spectral_lines"]]
    elif model.get("wavelength_nm") is not None:
        stated = [{"wavelength_nm": float(model["wavelength_nm"]), "power_w": float(model.get("power_w") or 0.0)}]
    if wavelengths is None:
        if stated is None:
            raise FieldPointsError("WAVELENGTHS_REQUIRED", "the reference source states a spectral density, not "
                                   "lines: give the field points' vacuum wavelengths with --wavelengths-nm")
        return stated
    if stated is not None and len(stated) == len(wavelengths) and all(
            abs(a["wavelength_nm"] - b) <= LINE_MATCH_NM for a, b in zip(stated, wavelengths)):
        return stated
    total = sum(line["power_w"] for line in stated) if stated else float(model.get("power_w") or 1e-3)
    return [{"wavelength_nm": float(w), "power_w": total / len(wavelengths)} for w in wavelengths]


def _with_lines(model: dict, lines: list[dict]) -> dict:
    out = {k: v for k, v in model.items() if k not in ("wavelength_nm", "power_w", "spectral_lines",
                                                       "spectral_density")}
    if len(lines) == 1:
        out.update(wavelength_nm=lines[0]["wavelength_nm"], power_w=lines[0]["power_w"])
    else:
        out["spectral_lines"] = [dict(line) for line in lines]
    return out


# ---------------------------------------------------------------------------------------- the points

@dataclass
class Point:
    """One field point being built: its request, parameterization (LINE: angle; POINT: object height), aim and
    solve state, and the measurements of the last evaluation."""

    index: int | None
    requested: float
    source_id: str
    mode: str
    parameter: float
    target_height_mm: float | None = None
    aim: fg.Aim | None = None
    done: bool = False
    aimed: bool = False
    height_reached: bool = True
    misses: int = 0
    iterations: int = 0
    history: list[tuple[float, float]] = field(default_factory=list)
    measured: dict | None = None
    pose: dict | None = None
    angle_deg: float | None = None
    reason: str | None = None
    message: str | None = None


def _component(reference: dict, point: Point, frame: fg.Frame, lines: list[dict], description: str,
               aiming_copy: bool = False) -> dict:
    offset = point.aim.offset if point.aim is not None else [0.0, 0.0]
    placed = fg.pose(frame, point.mode, point.parameter, offset)
    expected = point.parameter if point.mode == fg.LINE else None
    point.angle_deg = fg.verify_turned(placed["quaternion_wxyz"], frame, placed["axis"], expected)
    point.pose = placed
    component = copy.deepcopy(reference)
    component["instance_id"] = point.source_id
    component["nominal_pose"] = {"position_mm": placed["position_mm"], "quaternion_wxyz": placed["quaternion_wxyz"]}
    component["source_model"] = _with_lines(reference["source_model"], lines)
    if aiming_copy:  # the chief ray's path does not depend on the polarization: one beam per line while aiming
        component["source_model"]["polarization"] = {"kind": "LINEAR", "angle_deg": 0.0}
    part = component.get("custom_part")
    if isinstance(part, dict):
        component["custom_part"] = {**part, "description": description}
    return component


def _project(base: dict, reference: dict, points: Sequence[Point], frame: fg.Frame, lines: list[dict],
             plane_id: str, aperture: dict | None, label: str, aiming_copy: bool = False,
             budget: dict | None = None) -> dict:
    """``base`` (the flattened input) with its sources replaced by the field points, its targets removed, the
    plane's aperture replaced by ``aperture`` and the compute budget by ``budget`` (None: as declared).
    ``aiming_copy``: a working Project of the aim (``lines`` is then the chief-ray line alone, linearly
    polarized)."""
    document = copy.deepcopy(base)
    document["components"] = [c for c in document.get("components") or [] if c.get("optical_role") != "source"] + [
        _component(reference, p, frame, lines, f"{label} {p.source_id} of {reference['instance_id']}", aiming_copy)
        for p in points]
    document["targets"] = []
    if budget is not None:
        document["compute_budget"] = budget
    if aperture is not None:
        for observation in document.get("observations") or []:
            if observation.get("observation_id") == plane_id:
                observation["aperture"] = aperture
    return document


def beams(sources: int, lines: int, polarization: dict | None) -> int:
    """The beam paths a set of field-point sources starts: one per line, two for unpolarized light (the Engine traces
    it as two incoherent components)."""
    kind = str((polarization or {}).get("kind") or "")
    return sources * lines * (2 if kind == "UNPOLARIZED" or kind.startswith("PARTIAL") else 1)


def trace_budget(declared: dict | None, limits: dict, paths: int, steps_per_path: int) -> dict | None:
    """The compute budget of a field-point Project: the declared one (``limits``: the Engine's effective budget of
    it), with ``max_paths`` and ``max_trace_steps`` raised to BUDGET_HEADROOM times what ``paths`` chief-ray paths of
    ``steps_per_path`` steps need; None when the declared budget already covers them. The Engine traces the
    strongest paths first, so the field points come before their reflections."""
    need = {"max_paths": BUDGET_HEADROOM * paths, "max_trace_steps": BUDGET_HEADROOM * paths * steps_per_path}
    raised = {key: value for key, value in need.items() if value > int(limits.get(key) or 0)}
    return None if not raised else {**(declared or {}), **raised}


def execution_of(evaluation: Any) -> dict:
    """The Engine's execution record of an evaluation: outcome (COMPLETED or PARTIAL), termination and budgets."""
    outcome = evaluation.result.outcome.model_dump(mode="json")
    return dict(outcome.get("execution") or {})


def _working_aperture(declared: dict) -> dict:
    return {"shape": "circle", "radius_mm": WORKING_APERTURE_MM, "center_offset_mm": [0.0, 0.0],
            "obstruction_radius_mm": 0.0} if declared.get("shape") != "rectangle" else {
        "shape": "rectangle", "half_width_mm": WORKING_APERTURE_MM, "half_height_mm": WORKING_APERTURE_MM,
        "center_offset_mm": [0.0, 0.0]}


# ------------------------------------------------------------------------------------------- engine

def _evaluate(session: Any, document: dict, outputs: tuple[str, ...] = ("readings",)) -> Any:
    from os_optics.local import LocalRunError

    try:
        return session.evaluate(document, outputs=outputs)
    except LocalRunError as exc:
        raise FieldPointsError("EVALUATION_REFUSED", f"the Engine refuses the field-point Project: {exc}",
                               problems=_problems(exc)) from None


def _problems(exc: Exception) -> list[dict]:
    listed = [p.as_dict() for p in getattr(exc, "problems", ()) if hasattr(p, "as_dict")]
    return (listed or [{"code": getattr(exc, "code", None) or type(exc).__name__, "message": str(exc)}])[:10]


def _ancestry(path_id: str, parents: dict[str, str | None]) -> list[str]:
    """A path and the paths it branched from, the first one first."""
    chain: list[str] = []
    current: str | None = path_id
    while current is not None and len(chain) < 64:
        chain.append(current)
        current = parents.get(current)
    return chain[::-1]


def measure(results: Any, plane_id: str, source_ids: Sequence[str], line_count: int, stops: list[dict],
            stop: dict | None) -> dict[str, list[dict]]:
    """source id -> per spectral line, the chief ray of the line's path at the plane: path_id, uv_mm, aoi_deg (to
    the plane normal), crossing_index, pupil rays launched/arrived, pupil_rms_radius_mm, ``where`` (ARRIVED,
    OUTSIDE_PLANE, NO_ARRIVAL), and its crossing of ``stop`` (crossing_mm, stop_offset_mm, stop_problem). The path
    read is ``<source>#<index>`` (the unpolarized first component), else the line's strongest arrival."""
    monitor = results.monitor(plane_id)
    paths = results.paths()
    parents = {p["path_id"]: p.get("parent_path_id") for p in paths}
    lineages = {p["path_id"]: list(p.get("lineage") or []) for p in paths}
    arrivals: dict[tuple[Any, Any], list[dict]] = {}
    for arrival in monitor.get("arrivals") or []:
        beam = arrival.get("beam") or {}
        arrivals.setdefault((beam.get("source_id"), beam.get("spectral_index")), []).append(arrival)
    outside = {row.get("path_id"): row for row in monitor.get("outside_active_area") or []}
    found: dict[str, list[dict]] = {}
    for source_id in source_ids:
        rows = []
        for index in range(line_count):
            path_id = f"{source_id}#{index}"
            candidates = arrivals.get((source_id, index)) or []
            arrival = next((a for a in candidates if a["beam"].get("path_id") == path_id), None) or max(
                candidates, key=lambda a: a["beam"].get("power_W") or 0.0, default=None)
            row: dict[str, Any] = {"source_id": source_id, "path_id": path_id, "where": "NO_ARRIVAL", "uv_mm": None,
                                   "aoi_deg": None, "surfaces": None,
                                   "crossing_index": None, "pupil_rays_launched": None, "pupil_rays_arrived": None,
                                   "pupil_rms_radius_mm": None, "crossing_mm": None, "stop_offset_mm": None,
                                   "stop_problem": None}
            if arrival is not None:
                row.update(path_id=arrival["beam"].get("path_id"), where="ARRIVED", uv_mm=list(arrival["uv_mm"]),
                           aoi_deg=arrival.get("aoi_deg"), crossing_index=arrival.get("crossing_index"),
                           pupil_rays_launched=arrival.get("pupil_ray_count"),
                           pupil_rays_arrived=arrival.get("pupil_rays_arrived"),
                           pupil_rms_radius_mm=arrival.get("pupil_rms_radius_mm"))
            elif path_id in outside:
                row.update(where="OUTSIDE_PLANE", uv_mm=list(outside[path_id].get("uv_mm") or []) or None,
                           crossing_index=outside[path_id].get("crossing_index"))
            if row["path_id"] in parents:
                ancestry = _ancestry(row["path_id"], parents)
                row["surfaces"] = [event for item in ancestry for event in lineages.get(item, [])]
            if stop is not None and row["path_id"] in parents:
                segments = [s for item in _ancestry(row["path_id"], parents) for s in results.segments(item)]
                for crossing in fa.stop_crossings(segments, stops):
                    if crossing["stop"] is stop or crossing["problem"] is not None:
                        row.update(crossing_mm=crossing["point_mm"], stop_offset_mm=crossing["offset_mm"],
                                   stop_problem=crossing["problem"])
                        break
            rows.append(row)
        found[source_id] = rows
    return found


def paraxial_reference(document: dict, reference_id: str, plane_id: str, wavelength_nm: float,
                       frame: fg.Frame, plane_origin: Sequence[float]) -> dict:
    """The first-order reference of the request at one wavelength, from the Engine's paraxial analysis of the
    elements on the reference axis: EFL (object at infinity) with the paraxial focus's distance from the plane, or
    the magnification A of the ABCD from the object plane to the plane (finite object). ``reason`` / ``message`` when
    the analysis is refused."""
    from os_optics.local import LocalRunError, paraxial_analysis
    from os_optics.local.paraxial import ParaxialError

    hint = "the first-order comparison is left out"
    record: dict[str, Any] = {"kind": "EFL_TAN_THETA" if frame.conjugate == fg.INFINITE else
                              "MAGNIFICATION_TIMES_HEIGHT", "wavelength_nm": wavelength_nm, "components": None,
                              "efl_mm": None, "magnification": None, "reason": None, "message": None}
    try:
        components = fa.axis_components(document, reference_id, plane_id, hint=hint)
        record["components"] = components
        reply = paraxial_analysis(document, {"components": components, "wavelength_nm": wavelength_nm,
                                             "source_id": reference_id})
        if frame.conjugate == fg.INFINITE:
            efl = fa.paraxial_efl(reply, components, wavelength_nm, hint=hint)
            record.update(efl_mm=efl["efl_mm"], basis=efl["basis"], rear_focal_point_mm=efl["rear_focal_point_mm"],
                          paraxial_focus_from_plane_mm=g.dot(g.sub(efl["rear_focal_point_mm"], plane_origin),
                                                             efl["axis_direction"]))
        else:
            assert frame.object_point is not None
            elements = reply.get("elements") or []
            entrance = elements[0]["entrance_vertex_mm"]
            exit_ = elements[-1]["exit_vertex_mm"]
            matrix = fg.object_to_plane(reply["system"]["abcd"], g.dot(g.sub(entrance, frame.object_point), frame.axis),
                                        g.dot(g.sub(plane_origin, exit_), frame.axis))
            record.update(magnification=matrix[0][0], abcd_object_to_plane=matrix,
                          basis=f"engine paraxial ABCD of {' '.join(components)} at {wavelength_nm:g} nm, from the "
                                "object plane to the plane (os-optics project paraxial)")
    except fa.FieldError as exc:
        record.update(reason=exc.code, message=str(exc))
    except (ParaxialError, LocalRunError) as exc:
        record.update(reason=getattr(exc, "code", None) or "PARAXIAL_REFUSED", message=str(exc))
    return record


# --------------------------------------------------------------------------------------------- solve

def _lines_index(lines: list[dict], wavelength: float | None) -> int:
    if wavelength is None:
        return 0
    for i, line in enumerate(lines):
        if abs(line["wavelength_nm"] - wavelength) <= LINE_MATCH_NM:
            return i
    listed = ", ".join(f"{line['wavelength_nm']:g}" for line in lines)
    raise FieldPointsError("BAD_ARGUMENT", f"the chief-ray wavelength {wavelength:g} nm is not one of the field "
                           f"points' lines ({listed} nm)")


def _height(row: dict, axis_row: dict | None) -> float | None:
    if row.get("uv_mm") is None or axis_row is None or axis_row.get("uv_mm") is None:
        return None
    return math.hypot(row["uv_mm"][0] - axis_row["uv_mm"][0], row["uv_mm"][1] - axis_row["uv_mm"][1])


def _restart(point: Point, points: Sequence[Point]) -> None:
    """A new start for a point whose chief ray missed the stop: the offset of the nearest smaller field whose chief
    ray crossed it, scaled with tan(theta) (LINE) or the object height (POINT); without one, half its offset (toward
    the straight line through the aim centre)."""
    assert point.aim is not None
    known = [p for p in points if p is not point and p.measured is not None and p.measured.get("crossing_mm")
             and p.aim is not None and 0 < abs(p.parameter) < abs(point.parameter)]
    point.aim.last = None
    if not known:
        point.aim.offset = [0.5 * c for c in point.aim.offset]
        return
    near = max(known, key=lambda p: abs(p.parameter))
    if point.mode == fg.LINE:
        ratio = math.tan(math.radians(point.parameter)) / math.tan(math.radians(near.parameter))
    else:
        ratio = point.parameter / near.parameter
    assert near.aim is not None
    point.aim.offset = [c * ratio for c in near.aim.offset]


def solve(session: Any, context: dict, points: list[Point]) -> dict:
    """Aim every point's chief ray at the stop centre and, for IMAGE_HEIGHT, solve its angle or object height. Each
    Engine evaluation traces, on the working plane, the points still being solved and the on-axis point, as
    aiming copies (the chief-ray line alone, linearly polarized: the chief ray's path is the same). Returns the
    loop's facts."""
    frame: fg.Frame = context["frame"]
    aiming = context["aiming"]
    tolerance, height_tolerance = context["aim_tolerance_mm"], context["height_tolerance_mm"]
    chief_line = [context["lines"][context["chief_index"]]]
    axis_point = context["axis_point"]
    upper = fg.MAX_ANGLE_DEG if frame.conjugate == fg.INFINITE else context["max_object_height_mm"]
    evaluations = 0
    for evaluations in range(1, MAX_EVALUATIONS + 1):
        traced = [p for p in points if not p.done or p is axis_point]
        document = _project(context["base"], context["reference"], traced, frame, chief_line, context["plane_id"],
                            context["working_aperture"], "aim of field point", aiming_copy=True,
                            budget=context["budget"](len(traced), 1, True))
        results = _evaluate(session, document).results()
        measured = measure(results, context["plane_id"], [p.source_id for p in traced], 1, context["stops"],
                           context["stop"])
        axis_row = measured[axis_point.source_id][0]
        pending = False
        for point in traced:
            point.measured = measured[point.source_id][0]
            if point.done:
                continue
            point.iterations = evaluations
            row = point.measured
            crossing = row.get("crossing_mm")
            if aiming and crossing is None:
                point.misses += 1
                if point.misses >= MAX_MISSES:
                    point.done, point.reason = True, "STOP_NOT_REACHED"
                    point.message = row.get("stop_problem") or (f"the field point's chief ray does not cross the "
                                                                f"stop ({point.misses} attempts)")
                else:
                    _restart(point, points)
                    pending = True
                continue
            point.aimed = not aiming or (row["stop_offset_mm"] is not None and row["stop_offset_mm"] <= tolerance)
            height = _height(row, axis_row)
            if point.target_height_mm is not None:
                point.height_reached = height is not None and abs(height - point.target_height_mm) <= height_tolerance
            if point.aimed and point.height_reached:
                point.done = True
                point.reason, point.message = _final_state(row, axis_row.get("surfaces"))
                continue
            pending = True
            if not point.aimed:
                assert point.aim is not None and crossing is not None
                point.aim.step(fg.aim_error(frame, point.mode, point.parameter, crossing))
            if point.target_height_mm is not None and not point.height_reached and height is not None:
                point.history.append((point.parameter, height))
                new = fg.secant(point.history, point.target_height_mm, upper)
                if point.aim is not None:
                    before = fg.initial_offset(frame, point.mode, point.parameter)
                    after = fg.initial_offset(frame, point.mode, new)
                    point.aim.shift([after[0] - before[0], after[1] - before[1]])
                point.parameter = new
        if not pending:
            break
    for point in points:
        if point.reason is None and not point.aimed:
            point.reason = "AIM_NOT_CONVERGED"
            point.message = (f"the chief ray crosses the stop {point.measured and point.measured.get('stop_offset_mm')!r} "
                             f"mm from its centre after {MAX_EVALUATIONS} evaluations (tolerance {tolerance:g} mm)")
        elif point.reason is None and not point.height_reached:
            point.reason = "HEIGHT_NOT_REACHED"
            height = _height(point.measured or {}, axis_point.measured)
            point.message = (f"the chief ray lands {height!r} mm from the on-axis image point after {MAX_EVALUATIONS} "
                             f"evaluations, requested {point.target_height_mm:g} mm (tolerance {height_tolerance:g} "
                             "mm): the height may lie beyond the system's field")
    return {"evaluations": evaluations}


# ---------------------------------------------------------------------------------------------- MTF

def _final_state(row: dict, axis_surfaces: list[str] | None,
                 execution: dict | None = None) -> tuple[str | None, str | None]:
    """The reason a field point is not evaluated, from its chief ray: it reaches the plane outside the evaluated
    area (OUTSIDE_PLANE) or not at all (NO_ARRIVAL; TRACE_BUDGET_EXHAUSTED when the evaluation's trace budget ran out
    on its source), or it meets other surfaces, or the same ones with other events, than the on-axis chief ray
    (CHIEF_RAY_PATH_DIFFERS: it misses an element, or is reflected before the plane)."""
    if row["where"] == "OUTSIDE_PLANE":
        return "OUTSIDE_PLANE", "the chief ray meets the plane outside its evaluated area"
    budgets = (execution or {}).get("budgets") or {}
    if row["where"] != "ARRIVED" and row.get("source_id") in (budgets.get("affected_source_ids") or []):
        return "TRACE_BUDGET_EXHAUSTED", (f"the evaluation's trace budget ({', '.join(budgets.get('exhausted') or [])}) "
                                          "ran out before this line was traced")
    if row["where"] != "ARRIVED":
        return "NO_ARRIVAL", "the chief ray does not reach the plane"
    surfaces = row.get("surfaces")
    if axis_surfaces is not None and surfaces is not None and surfaces != axis_surfaces:
        missed = [s for s in axis_surfaces if s not in surfaces]
        extra = [s for s in surfaces if s not in axis_surfaces]
        return "CHIEF_RAY_PATH_DIFFERS", (f"the chief ray does not meet the on-axis chief ray's surfaces in order "
                                          f"(missed: {', '.join(missed) or 'none'}; other: {', '.join(extra) or 'none'})")
    return None, None


def _imaging_calls(points: Sequence[Point]) -> list[list[Point]]:
    """Evaluated points grouped by the chief ray's crossing index, at most MAX_IMAGING_FIELDS per call."""
    groups: dict[int, list[Point]] = {}
    for point in points:
        groups.setdefault(int((point.measured or {}).get("crossing_index") or 0), []).append(point)
    calls = []
    for _index, members in sorted(groups.items()):
        for start in range(0, len(members), MAX_IMAGING_FIELDS):
            calls.append(members[start:start + MAX_IMAGING_FIELDS])
    return calls


def _sample(point: Point | None, index: int, requested: float, line: dict, line_index: int, direction: str,
            frequency: float, record: dict | None, reason: str | None, message: str | None,
            imaging_file: str | None) -> dict:
    sample: dict[str, Any] = {"point_index": index, "requested": requested, "line_id": None,
                              "wavelength_nm": line["wavelength_nm"], "direction": direction,
                              "frequency_cyc_per_mm": frequency, "value": None, "status": None, "value_kind": "NO_VALUE",
                              "reason": reason, "message": message, "converged": None, "model_basis": None,
                              "imaging_file": imaging_file}
    if point is not None:
        sample["line_id"] = f"{point.source_id}#{line_index}"
    if record is not None:
        value, status = ir.value_at(record, frequency)
        sample.update(value=value, status=status, converged=record.get("converged"),
                      model_basis=record.get("model_basis"),
                      value_kind=record["value_kind"] if value is not None else "NO_VALUE")
        if value is None:
            sample["reason"] = record.get("reason") if not record.get("usable") else status
            sample["message"] = record.get("message") if not record.get("usable") else None
    else:
        sample["status"] = reason
    return sample


def minima(samples: Sequence[dict], frequencies: Sequence[float], wavelengths: Sequence[float],
           field_grid: Sequence[float]) -> list[dict]:
    """Sampled minima per frequency: ``per_line`` (each wavelength over every point and direction) and
    ``all_lines`` (every point, direction and line). CONFIRMED when every sample is an EXACT value, else UNCONFIRMED
    with ``value`` the minimum of the EXACT values (``bound`` UPPER: the sampled minimum is at most this) and each
    other sample in ``missing`` with its reason. A per-line minimum is not a weighted multispectral MTF."""

    def reduce(chosen: list[dict]) -> dict:
        exact = [s for s in chosen if s["value_kind"] == "EXACT" and s["value"] is not None]
        missing = [{"point_index": s["point_index"], "requested": s["requested"], "direction": s["direction"],
                    "wavelength_nm": s["wavelength_nm"], "value": s["value"], "value_kind": s["value_kind"],
                    "reason": s["reason"] or s["status"]} for s in chosen
                   if not (s["value_kind"] == "EXACT" and s["value"] is not None)]
        limiting = min(exact, key=lambda s: s["value"], default=None)
        confirmed = not missing and bool(exact)
        return {"value": None if limiting is None else limiting["value"],
                "status": "CONFIRMED" if confirmed else "UNCONFIRMED", "bound": None if confirmed or limiting is None
                else "UPPER", "samples": len(chosen), "exact_samples": len(exact), "missing": missing,
                "limiting_sample": None if limiting is None else {
                    "point_index": limiting["point_index"], "requested": limiting["requested"],
                    "direction": limiting["direction"], "wavelength_nm": limiting["wavelength_nm"]}}

    out = []
    for frequency in frequencies:
        at = [s for s in samples if s["frequency_cyc_per_mm"] == frequency]
        out.append({"metric": "per_line_minimum", "frequency_cyc_per_mm": frequency, "field_grid": list(field_grid),
                    "directions": list(ir.DIRECTIONS), "wavelengths_nm": list(wavelengths),
                    "per_line": [{"wavelength_nm": w, **reduce([s for s in at if s["wavelength_nm"] == w])}
                                 for w in wavelengths],
                    "all_lines": reduce(at),
                    "note": ("the minimum over the sampled points, directions and lines of per-line MTF values; not a "
                             "spectrally weighted (multispectral) MTF")})
    return out


def mtf(context: dict, points: list[Point], results_for_imaging: Any, out_dir: Path) -> tuple[list[dict], list[dict]]:
    """One imaging analysis per call group of evaluated points; the per-sample records and the imaging entries."""
    from os_optics.results import ResultsError

    lines, frequencies = context["lines"], context["frequencies"]
    calls = _imaging_calls([p for p in points if p.reason is None and p.index is not None])
    entries: list[dict] = []
    curves: dict[str, tuple[dict, dict, str]] = {}
    for number, members in enumerate(calls, start=1):
        name = "imaging.json" if len(calls) == 1 else f"imaging-{number}.json"
        request = {**context["imaging_base"], "fields": [p.source_id for p in members],
                   "wavelengths_nm": [line["wavelength_nm"] for line in lines],
                   "frequencies_cyc_per_mm": list(frequencies),
                   "crossing_index": int((members[0].measured or {}).get("crossing_index") or 0)}
        entry: dict[str, Any] = {"file": name, "fields": request["fields"], "request": request, "seconds": None,
                                 "identity": None, "error": None}
        started = time.perf_counter()
        try:
            document = ir.load(results_for_imaging.imaging(context["plane_id"], request))
        except ResultsError as exc:
            entry["error"] = {"code": getattr(exc, "code", None) or type(exc).__name__, "message": str(exc)}
            entries.append(entry)
            continue
        entry["seconds"] = round(time.perf_counter() - started, 3)
        wc.write_json(out_dir / name, document)
        entry.update(sha256=wc.sha256_file(out_dir / name), identity=ir.identity(document))
        entries.append(entry)
        by_line = {item["line_id"]: item for item in ir.lines(document)}
        for record in ir.curves(document, kinds=("mtf",), line_ids=[f"{p.source_id}#{i}" for p in members
                                                                     for i in range(len(lines))]):
            curves[f"{record['line_id']}.{record['direction']}"] = (record, by_line.get(record["line_id"]) or {}, name)
    samples: list[dict] = []
    failed = {source: entry["error"] for entry in entries if entry["error"] for source in entry["fields"]}
    for point in points:
        if point.index is None:
            continue
        for line_index, line in enumerate(lines):
            for direction in ir.DIRECTIONS:
                for frequency in frequencies:
                    if point.reason is not None:
                        samples.append(_sample(point, point.index, point.requested, line, line_index, direction,
                                               frequency, None, point.reason, point.message, None))
                        continue
                    if point.source_id in failed:
                        error = failed[point.source_id]
                        samples.append(_sample(point, point.index, point.requested, line, line_index, direction,
                                               frequency, None, error["code"], error["message"], None))
                        continue
                    record, line_entry, name = curves[f"{point.source_id}#{line_index}.{direction}"]
                    sample = _sample(point, point.index, point.requested, line, line_index, direction, frequency,
                                     record, None, None, name)
                    if record["status"] == ir.ABSENT and line_entry.get("field_reason"):
                        sample.update(reason=line_entry["field_reason"], status=line_entry.get("field_state"))
                    samples.append(sample)
    return samples, entries


# -------------------------------------------------------------------------------------------- report

def _round(value: Any, digits: int = 12) -> Any:
    return float(f"{value:.{digits}g}") if _finite(value) else value


def _first_order(reference: dict, point: Point, height: float | None, frame: fg.Frame) -> dict:
    record: dict[str, Any] = {"kind": reference["kind"], "label": FIRST_ORDER_LABEL,
                              "wavelength_nm": reference["wavelength_nm"], "efl_mm": reference["efl_mm"],
                              "magnification": reference["magnification"], "reference_height_mm": None,
                              "difference_mm": None, "relative_difference": None, "reason": reference["reason"],
                              "message": reference["message"]}
    if reference["reason"] is not None or point.pose is None:
        return record
    if frame.conjugate == fg.INFINITE:
        expected = fg.efl_reference_height(reference["efl_mm"], point.angle_deg or 0.0)
    else:
        assert frame.object_point is not None and point.pose["object_point_mm"] is not None
        object_height = g.norm(g.sub(point.pose["object_point_mm"], frame.object_point))
        expected = abs(reference["magnification"] * object_height)
    record["reference_height_mm"] = expected
    if height is not None:
        record["difference_mm"] = height - expected
        record["relative_difference"] = (height - expected) / expected if expected > 0 else None
    return record


def _point_report(point: Point, context: dict, final: dict[str, list[dict]], samples: list[dict]) -> dict:
    frame: fg.Frame = context["frame"]
    chief = context["chief_index"]
    axis_rows = final[context["axis_point"].source_id]
    rows = final[point.source_id]
    row = rows[chief]
    height = _height(row, axis_rows[chief])
    object_height = None
    if frame.conjugate == fg.FINITE and point.pose is not None and point.pose["object_point_mm"] is not None:
        assert frame.object_point is not None
        object_height = g.norm(g.sub(point.pose["object_point_mm"], frame.object_point))
    inside = None if row["uv_mm"] is None else fg.inside_aperture(context["declared_aperture"], row["uv_mm"])
    status = EVALUATED if point.reason is None else NOT_EVALUATED
    aim_state = NOT_AIMED if not context["aiming"] else (AIMED if point.aimed else AIM_NOT_CONVERGED_STATE)
    uv = row["uv_mm"]
    axis_uv = axis_rows[chief]["uv_mm"]
    lines = []
    for index, line in enumerate(context["lines"]):
        item = rows[index]
        lines.append({"wavelength_nm": line["wavelength_nm"], "path_id": item["path_id"], "where": item["where"],
                      "image_point_uv_mm": item["uv_mm"], "image_height_mm": _height(item, axis_rows[chief]),
                      "angle_deg": item["aoi_deg"], "crossing_index": item["crossing_index"],
                      "stop_offset_mm": item["stop_offset_mm"], "pupil_rays_launched": item["pupil_rays_launched"],
                      "pupil_rays_arrived": item["pupil_rays_arrived"]})
    return {
        "index": point.index, "requested": point.requested, "source_id": point.source_id, "status": status,
        "reason": point.reason, "message": point.message,
        "field": {"angle_deg": point.angle_deg, "object_height_mm": object_height,
                  "object_point_mm": None if point.pose is None else point.pose["object_point_mm"]},
        "pose": None if point.pose is None else {"position_mm": point.pose["position_mm"],
                                                 "quaternion_wxyz": point.pose["quaternion_wxyz"],
                                                 "emission_axis": point.pose["axis"]},
        "chief_ray": {"wavelength_nm": context["lines"][chief]["wavelength_nm"], "path_id": row["path_id"],
                      "where": row["where"], "image_point_uv_mm": uv, "image_height_mm": height,
                      "image_azimuth_deg": None if uv is None or axis_uv is None or height is None or height == 0
                      else math.degrees(math.atan2(uv[1] - axis_uv[1], uv[0] - axis_uv[0])),
                      "angle_deg": row["aoi_deg"], "crossing_index": row["crossing_index"],
                      "stop_offset_mm": row["stop_offset_mm"], "aim": aim_state, "iterations": point.iterations,
                      "within_declared_aperture": inside},
        "first_order": _first_order(context["first_order"], point, height, frame),
        "spot": {"pupil_rays_launched": row["pupil_rays_launched"], "pupil_rays_arrived": row["pupil_rays_arrived"],
                 "pupil_rms_radius_mm": row["pupil_rms_radius_mm"]},
        "lines": lines,
        "mtf": [{k: v for k, v in s.items() if k not in ("point_index", "requested")} for s in samples
                if s["point_index"] == point.index]}


# ----------------------------------------------------------------------------------------------- run

def produced(request: Request) -> tuple:
    """The entries of ``FILES`` this request writes: ``run`` only with ``write_run``, the imaging documents only with
    ``mtf_frequencies``; ``--overwrite`` removes these and nothing else."""
    return tuple(entry for entry in FILES
                 if (entry != "run" or request.write_run)
                 and (request.mtf_frequencies is not None or not str(getattr(entry, "pattern", entry)).startswith("imaging")))


def run(request: Request, out_dir: str | Path, *, overwrite: bool = False, session: Any = None) -> dict:
    """Build, aim and evaluate the field points of ``request``; write the report files into ``out_dir``; return the
    report (``field-points.json``)."""
    started = time.perf_counter()
    check_request(request)
    document, identity = read_input(Path(request.project))
    identity["configuration_id"] = request.configuration
    base = configured(document, request.configuration)
    plane = next((o for o in base.get("observations") or [] if o.get("observation_id") == request.plane), None)
    if plane is None:
        raise FieldPointsError("PLANE_NOT_FOUND", f"no observation plane {request.plane!r}",
                               planes=[o.get("observation_id") for o in base.get("observations") or []])
    reference = reference_source(base, request.source)
    lines = spectral_lines(reference.get("source_model") or {}, request.wavelengths_nm)
    chief = _lines_index(lines, request.chief_ray_wavelength_nm)
    chief_wavelength = lines[chief]["wavelength_nm"]
    try:
        conj = fg.conjugate(reference, chief_wavelength)
    except fg.FieldGeometryError as exc:
        raise FieldPointsError(exc.code, str(exc), **exc.details) from None
    if conj["kind"] == fg.INFINITE and request.kind == "OBJECT_HEIGHT":
        raise FieldPointsError("OBJECT_AT_INFINITY", "the reference source is collimated (object at infinity): an "
                               "object height has no meaning there; give field angles", conjugate=conj)
    imaging_base: dict[str, Any] = dict(request.imaging_request or {})
    if request.launch_grid is not None:
        imaging_base["launch_grid"] = request.launch_grid
    if not request.convergence:
        imaging_base["convergence"] = False
    if request.mtf_frequencies is not None:
        from os_optics.results.imaging import ImagingError, parse_request

        try:
            parse_request({**imaging_base, "fields": ["FP0"], "frequencies_cyc_per_mm": request.mtf_frequencies})
        except ImagingError as exc:
            raise FieldPointsError("IMAGING_INVALID", str(exc)) from None
    produced_files, inputs = produced(request), (request.project, request.imaging_request_file)
    wc.check_output(out_dir, overwrite, produced_files, keep=inputs)  # refuse now; remove stale files before writing

    from os_optics.local import EvaluationSession

    session = session or EvaluationSession()
    # the reference alone, on the working plane: the plane's frame, the scene's aperture stops, the reference chief ray
    declared_aperture = copy.deepcopy(plane.get("aperture") or {})
    working = _working_aperture(declared_aperture)
    reference_doc = copy.deepcopy(base)
    reference_doc["targets"] = []
    reference_doc["components"] = [c for c in base.get("components") or [] if c.get("optical_role") != "source"
                                   or c.get("instance_id") == reference["instance_id"]]
    for observation in reference_doc.get("observations") or []:
        if observation.get("observation_id") == request.plane:
            observation["aperture"] = working
    first = _evaluate(session, reference_doc, ("readings", "view"))
    limits = (execution_of(first).get("budgets") or {}).get("limits") or {}
    results = first.results()
    plane_frame = results.monitor(request.plane)["plane"]
    stops = fa.scene_stops(first.optical_view.model_dump(mode="json"))
    usable = [s for s in stops if s["centre_mm"] is not None]
    stop: dict | None = None
    if request.pivot_mm is not None:
        centre = [float(c) for c in request.pivot_mm]
        stop = usable[0] if len(usable) == 1 else None
    elif not stops:
        raise FieldPointsError("NO_APERTURE_STOP", "the Project has no aperture stop to aim the chief rays at: state "
                               "the point the field points turn about with --pivot-mm (they are then NOT_AIMED)")
    elif len(stops) > 1 or not usable:
        raise FieldPointsError("STOP_AMBIGUOUS" if len(stops) > 1 else "STOP_UNRESOLVED",
                               "the chief ray is aimed at one aperture stop and the scene gives "
                               + (f"{len(stops)}" if len(stops) > 1 else "an unplaced one") + "; state --pivot-mm",
                               stops=[{"label": s["label"], "geometry_issue": s["geometry_issue"]} for s in stops])
    else:
        stop = usable[0]
        centre = stop["centre_mm"]
    aiming = request.pivot_mm is None
    try:
        frame = fg.make_frame(reference, conj, plane_frame["u_axis"], plane_frame["v_axis"], request.azimuth_deg, centre)
    except fg.FieldGeometryError as exc:
        raise FieldPointsError(exc.code, str(exc), **exc.details) from None
    reference_lines = _reference_wavelengths(reference)
    reference_rows = measure(results, request.plane, [reference["instance_id"]], len(reference_lines), stops,
                             stop)[reference["instance_id"]]
    reference_row = reference_rows[next((i for i, w in enumerate(reference_lines) if w is not None
                                         and abs(w - chief_wavelength) <= LINE_MATCH_NM), 0)]
    # chief-ray steps of one path: a step to each surface it meets (the plane included) and one to leave the scene
    steps_per_path = len(reference_row["surfaces"] or []) + 2
    declared_budget = base.get("compute_budget")
    polarization = (reference.get("source_model") or {}).get("polarization")

    def budget(sources: int, line_count: int, aiming_copy: bool) -> dict | None:
        paths = beams(sources, line_count, None if aiming_copy else polarization)
        return trace_budget(declared_budget, limits, paths, steps_per_path)

    first_order = paraxial_reference(base, reference["instance_id"], request.plane, chief_wavelength, frame,
                                     plane_frame["origin_mm"])

    # the points: LINE (an angle) at infinity and for ANGULAR, POINT (an object height) otherwise
    mode = fg.LINE if (request.kind == "ANGULAR" or (request.kind == "IMAGE_HEIGHT" and conj["kind"] == fg.INFINITE)) \
        else fg.POINT
    points: list[Point] = []
    for index, value in enumerate(request.values):
        point = Point(index=index, requested=value, source_id=f"FP{index}", mode=mode, parameter=value)
        if request.kind == "IMAGE_HEIGHT":
            point.target_height_mm = value
            point.parameter = _first_guess(value, first_order, conj["kind"], frame, plane_frame["origin_mm"])
        points.append(point)
    taken = {c.get("instance_id") for c in base.get("components") or [] if c.get("optical_role") != "source"}
    for point in points:
        while point.source_id in taken:
            point.source_id += "_"
    axis_point = next((p for p in points if p.requested == 0.0), None)
    if axis_point is None:
        axis_id = AXIS_SOURCE_ID
        while axis_id in taken:
            axis_id += "_"
        axis_point = Point(index=None, requested=0.0, source_id=axis_id, mode=mode, parameter=0.0)
        points.append(axis_point)
    if request.kind == "IMAGE_HEIGHT" and axis_point.index is not None:
        axis_point.target_height_mm, axis_point.parameter = None, 0.0

    # the entrance-pupil estimate from three probe chief rays, then the aim of every point
    clear: Any = (stop or {}).get("clear_radius_mm")
    shift = PROBE_SHIFT_FRACTION * (float(clear) if _finite(clear) and clear > 0 else 1.0)
    pupil: dict[str, Any] = {"pupil_magnification": None, "entrance_pupil_from_stop_mm": None,
                             "basis": "NOT_AIMED: --pivot-mm"}
    evaluations = 1
    if aiming:
        probes = [Point(None, 0.0, "PROBE_AXIS", fg.LINE, 0.0), Point(None, 0.0, "PROBE_SHIFT", fg.LINE, 0.0),
                  Point(None, PROBE_TILT_DEG, "PROBE_TILT", fg.LINE, PROBE_TILT_DEG)]
        probes[1].aim = fg.Aim(offset=[shift, 0.0], gain=1.0, max_step_mm=shift)
        while any(p.source_id in taken for p in probes):
            for p in probes:
                p.source_id += "_"
        probe_doc = _project(base, reference, probes, frame, [lines[chief]], request.plane, working, "aim probe",
                             aiming_copy=True, budget=budget(len(probes), 1, True))
        probe_rows = measure(_evaluate(session, probe_doc).results(), request.plane, [p.source_id for p in probes], 1,
                             stops, stop)
        evaluations += 1
        crossings = [probe_rows[p.source_id][0]["crossing_mm"] for p in probes]
        pupil = fg.pupil_estimate(frame, crossings[0], crossings[1], crossings[2], shift, PROBE_TILT_DEG)
        frame.pupil = g.add(frame.centre, g.scale(frame.axis, pupil["entrance_pupil_from_stop_mm"]))
        max_step = 10.0 * shift / PROBE_SHIFT_FRACTION
        for point in points:
            point.aim = fg.Aim(offset=fg.initial_offset(frame, point.mode, point.parameter),
                               gain=fg.offset_gain(frame, point.mode, point.parameter, pupil["pupil_magnification"]),
                               max_step_mm=max_step)
    context: dict[str, Any] = {
        "frame": frame, "aiming": aiming, "aim_tolerance_mm": request.aim_tolerance_mm,
        "height_tolerance_mm": request.height_tolerance_mm, "chief_index": chief, "axis_point": axis_point,
        "base": base, "reference": reference, "lines": lines, "plane_id": request.plane, "working_aperture": working,
        "stops": stops, "stop": stop, "declared_aperture": declared_aperture, "first_order": first_order,
        "imaging_base": imaging_base, "frequencies": sorted(set(request.mtf_frequencies or [])),
        "max_object_height_mm": _max_object_height(frame), "budget": budget}
    loop = solve(session, context, points)
    evaluations += loop["evaluations"]
    if (axis_point.measured or {}).get("uv_mm") is None:
        raise FieldPointsError("AXIS_NOT_REACHED", "the on-axis field point's chief ray does not reach the plane: "
                               "image heights have no origin", axis=axis_point.measured)

    # the evaluated Project: the declared plane, widened (pose unchanged) when a field point would fall outside it
    footprints: list[tuple[Sequence[float], float]] = [
        (p.measured["uv_mm"], p.measured.get("pupil_rms_radius_mm") or 0.0) for p in points
        if p.reason is None and p.measured is not None and p.measured.get("uv_mm") is not None]
    widened = fg.widened_aperture(declared_aperture, footprints)
    evaluated_budget = budget(len(points), len(lines), False)
    evaluated = _project(base, reference, points, frame, lines, request.plane, widened, "field point",
                         budget=evaluated_budget)
    out = wc.prepare_output(out_dir, overwrite, produced_files, keep=inputs)
    evaluated_file = out / "evaluated-project.json"
    wc.write_json(evaluated_file, evaluated)
    evaluated_check = wc.check(evaluated)
    final_ev = _evaluate(session, evaluated)
    final_execution = execution_of(final_ev)
    evaluations += 1
    final = measure(final_ev.results(), request.plane, [p.source_id for p in points], len(lines), stops, stop)
    axis_surfaces = final[axis_point.source_id][chief]["surfaces"]
    for point in points:
        row = final[point.source_id][chief]
        point.measured = row
        if point.reason is None:
            point.reason, point.message = _final_state(row, axis_surfaces, final_execution)
    for rows in final.values():  # a line the budget left untraced says so
        for item in rows:
            if item["where"] == "NO_ARRIVAL" and _final_state(item, None, final_execution)[0] == "TRACE_BUDGET_EXHAUSTED":
                item["where"] = "TRACE_BUDGET_EXHAUSTED"
    run_dir = None
    imaging_source = final_ev.results()
    if request.write_run:
        from os_optics.results import load_run

        run_dir = out / "run"
        final_ev.write_run(run_dir)
        imaging_source = load_run(run_dir)
    samples: list[dict] = []
    imaging_entries: list[dict] = []
    if request.mtf_frequencies is not None:
        samples, imaging_entries = mtf(context, points, imaging_source, out)
    requested = [p for p in points if p.index is not None]
    reports = [_point_report(p, context, final, samples) for p in requested]
    minima_rows = minima(samples, context["frequencies"], [line["wavelength_nm"] for line in lines],
                         request.values) if request.mtf_frequencies is not None else []
    complete = all(p["status"] == EVALUATED for p in reports) and all(s["value_kind"] == "EXACT" for s in samples)
    engine = wc.engine_identity()
    report = {
        "schema": SCHEMA, "schema_version": SCHEMA_VERSION, "status": COMPLETED if complete else INCOMPLETE,
        "input": identity,
        "evaluated": {"file": "evaluated-project.json", "sha256": wc.sha256_file(evaluated_file),
                      "project_hash": evaluated_check.get("project_hash"),
                      "engine_input_hash": evaluated_check.get("engine_input_hash"),
                      "catalog_version": evaluated_check.get("catalog_version"),
                      "run": None if run_dir is None else "run", "execution": final_execution,
                      "derivation": {**_derivation(base, reference, points, frame, request, declared_aperture, widened,
                                                   identity),
                                     "compute_budget": {"declared": declared_budget,
                                                        "evaluated": evaluated_budget or declared_budget,
                                                        "raised": evaluated_budget is not None,
                                                        "rule": (f"max_paths and max_trace_steps at least "
                                                                 f"{BUDGET_HEADROOM} x the field points' chief-ray "
                                                                 "paths and steps")}}},
        "engine": {"version": engine["engine_version"], "build_id": engine["engine_build_id"]},
        "plane": {"observation_id": request.plane, "pose": plane.get("pose"), "origin_mm": plane_frame["origin_mm"],
                  "u_axis": plane_frame["u_axis"], "v_axis": plane_frame["v_axis"], "normal": plane_frame["normal"],
                  "aperture_declared": declared_aperture, "aperture_evaluated": widened or declared_aperture,
                  "widened": widened is not None,
                  "on_axis_image_point_uv_mm": (axis_point.measured or {}).get("uv_mm")},
        "conditions": {
            "field_kind": request.kind, "unit": fg.FIELD_UNITS[request.kind], "azimuth_deg": request.azimuth_deg,
            "field_direction": frame.direction, "requested": list(request.values), "conjugate": conj,
            "reference_source": _reference_facts(reference, frame, plane_frame, reference_row, centre),
            "chief_ray_wavelength_nm": chief_wavelength, "wavelengths_nm": [line["wavelength_nm"] for line in lines],
            "line_powers_w": [line["power_w"] for line in lines],
            "frequencies_cyc_per_mm": context["frequencies"] if request.mtf_frequencies is not None else None,
            "imaging_request": imaging_base if request.mtf_frequencies is not None else None,
            "imaging_request_file": request.imaging_request_file,
            "aim": {"tolerance_mm": request.aim_tolerance_mm, "aimed_at": "APERTURE_STOP_CENTRE" if aiming else "PIVOT",
                    "centre_mm": centre, "stop": None if stop is None else stop["label"], **pupil},
            "height_tolerance_mm": request.height_tolerance_mm if request.kind == "IMAGE_HEIGHT" else None,
            "image_height_origin": "the on-axis field point's chief-ray hit point on the plane",
            "first_order": context["first_order"]},
        "points": reports,
        "minima": minima_rows,
        "imaging": imaging_entries,
        "execution": {"evaluations": evaluations, "imaging_calls": len(imaging_entries),
                      "seconds": round(time.perf_counter() - started, 3)},
        "scope": SCOPE}
    wc.write_json(out / "field-points.json", report)
    return report


def _reference_wavelengths(source: dict) -> list[float | None]:
    """The reference source's spectral lines in index order (``[None] * n`` for a spectral density)."""
    model = source.get("source_model") or {}
    if model.get("spectral_lines"):
        return [float(line["wavelength_nm"]) for line in model["spectral_lines"]]
    if model.get("wavelength_nm") is not None:
        return [float(model["wavelength_nm"])]
    density = model.get("spectral_density") or {}
    return [None] * max(1, int(density.get("line_count") or 1))


def _max_object_height(frame: fg.Frame) -> float:
    """An upper bound of the object-height solve: the object distance to the stop (a 45 deg field)."""
    if frame.object_point is None:
        return math.inf
    return abs(g.dot(g.sub(frame.centre, frame.object_point), frame.axis))


def _first_guess(height: float, reference: dict, conjugate_kind: str, frame: fg.Frame,
                 plane_origin: Sequence[float]) -> float:
    """The first-order start of an IMAGE_HEIGHT solve: atan(h / f) or h / |m|; without a first-order reference,
    the height over the stop-to-plane distance (an angle) or the height itself."""
    if height == 0.0:
        return 0.0
    if conjugate_kind == fg.INFINITE:
        efl: Any = reference.get("efl_mm")
        length = efl if _finite(efl) and efl > 0 else abs(g.dot(g.sub(plane_origin, frame.centre), frame.axis)) or 1.0
        return math.degrees(math.atan2(height, length))
    m: Any = reference.get("magnification")
    return height / abs(m) if _finite(m) and abs(m) > 1e-9 else height


def _reference_facts(reference: dict, frame: fg.Frame, plane_frame: dict, row: dict,
                     centre: Sequence[float]) -> dict:
    return {"source_id": reference["instance_id"], "position_mm": frame.origin, "emission_axis": frame.axis,
            "plane_normal": plane_frame["normal"],
            "axis_to_plane_normal_deg": g.angle_deg(frame.axis, g.unit(plane_frame["normal"])),
            "aim_centre_off_axis_mm": g.norm(g.across(g.sub(centre, frame.origin), frame.axis)),
            "chief_ray_uv_mm": row["uv_mm"], "chief_ray_stop_offset_mm": row["stop_offset_mm"],
            "note": ("the reference source's emission axis is the request's optical axis; its chief ray should land "
                     "on the plane's on-axis point and cross the stop centre")}


def _derivation(base: dict, reference: dict, points: Sequence[Point], frame: fg.Frame, request: Request,
                declared: dict, widened: dict | None, identity: dict) -> dict:
    return {"from": {"sha256": identity["sha256"], "configuration_id": request.configuration},
            "flattened_configuration": request.configuration,
            "removed_sources": [s.get("instance_id") for s in _sources(base)],
            "removed_targets": [t.get("target_id") for t in base.get("targets") or []],
            "added_sources": [{"source_id": p.source_id, "role": "FIELD_POINT" if p.index is not None else "ON_AXIS",
                               "point_index": p.index, "copy_of": reference["instance_id"],
                               "pose": None if p.pose is None else {"position_mm": p.pose["position_mm"],
                                                                    "quaternion_wxyz": p.pose["quaternion_wxyz"]}}
                              for p in points],
            "plane": {"observation_id": request.plane, "pose": "unchanged", "aperture_declared": declared,
                      "aperture_evaluated": widened or declared, "widened": widened is not None}}


# ----------------------------------------------------------------------------------------------- CLI

def _numbers(text: str, *, what: str) -> list[float]:
    try:
        return [float(item) for item in text.split(",") if item.strip() != ""]
    except ValueError:
        raise FieldPointsError("BAD_ARGUMENT", f"{what}: expected comma-separated numbers, got {text!r}") from None


def request_from_arguments(args: argparse.Namespace) -> Request:
    given = [(kind, getattr(args, name)) for kind, name in (("ANGULAR", "angles_deg"),
                                                             ("OBJECT_HEIGHT", "object_heights_mm"),
                                                             ("IMAGE_HEIGHT", "image_heights_mm"))
             if getattr(args, name) is not None]
    if len(given) > 1:
        raise FieldPointsError("FIELD_KIND_MIXED", "one field kind per request: an angle, an object height and an "
                               "image height are not interchangeable", kinds=[kind for kind, _ in given])
    if not given:
        raise FieldPointsError("BAD_ARGUMENT", "give the field points: --angles-deg, --object-heights-mm or "
                               "--image-heights-mm")
    kind, text = given[0]
    imaging = None
    if args.imaging_request is not None:
        try:
            imaging = wc.read_json(args.imaging_request)
        except (OSError, ValueError) as exc:
            raise FieldPointsError("BAD_ARGUMENT", f"--imaging-request: {exc}") from None
        if not isinstance(imaging, dict):
            raise FieldPointsError("BAD_ARGUMENT", "--imaging-request: expected a JSON object (an imaging request)")
    return Request(
        project=Path(args.project), plane=args.plane, kind=kind, values=_numbers(text, what="field points"),
        azimuth_deg=args.azimuth_deg, source=args.source, configuration=args.configuration,
        wavelengths_nm=None if args.wavelengths_nm is None else _numbers(args.wavelengths_nm, what="--wavelengths-nm"),
        chief_ray_wavelength_nm=args.chief_ray_wavelength_nm, expect_field_kind=args.expect_field_kind,
        mtf_frequencies=None if args.mtf_frequencies is None else _numbers(args.mtf_frequencies,
                                                                            what="--mtf-frequencies"),
        imaging_request=imaging, imaging_request_file=args.imaging_request, launch_grid=args.launch_grid,
        convergence=not args.no_convergence, aim_tolerance_mm=args.aim_tolerance_mm,
        height_tolerance_mm=args.height_tolerance_mm,
        pivot_mm=None if args.pivot_mm is None else _numbers(args.pivot_mm, what="--pivot-mm"),
        write_run=args.write_run)


def summary(report: dict, out_dir: str | Path) -> dict:
    """The compact stdout document: status, files, counts, one row per point and the minima over all lines."""
    files = {"report": "field-points.json", "evaluated_project": report["evaluated"]["file"],
             "imaging": [entry["file"] for entry in report["imaging"] if entry.get("identity")],
             "run": report["evaluated"]["run"]}
    points = report["points"]
    return {"status": report["status"], "out": Path(out_dir).as_posix(), "files": files,
            "counts": {"points": len(points), "evaluated": sum(p["status"] == EVALUATED for p in points),
                       "mtf_samples": sum(len(p["mtf"]) for p in points),
                       "exact_samples": sum(s["value_kind"] == "EXACT" for p in points for s in p["mtf"])},
            "field_kind": report["conditions"]["field_kind"], "plane_widened": report["plane"]["widened"],
            "points": [{"index": p["index"], "requested": p["requested"], "status": p["status"], "reason": p["reason"],
                        "angle_deg": _round(p["field"]["angle_deg"], 9),
                        "image_height_mm": _round(p["chief_ray"]["image_height_mm"], 9),
                        "chief_ray_angle_deg": _round(p["chief_ray"]["angle_deg"], 9), "aim": p["chief_ray"]["aim"],
                        "first_order_difference_mm": _round(p["first_order"]["difference_mm"], 6)} for p in points],
            "minima": [{"frequency_cyc_per_mm": m["frequency_cyc_per_mm"], "value": m["all_lines"]["value"],
                        "status": m["all_lines"]["status"], "limiting_sample": m["all_lines"]["limiting_sample"]}
                       for m in report["minima"]],
            "execution": report["execution"]}


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog=PROG, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project", help="the input Project (never modified)")
    parser.add_argument("--plane", required=True, help="observation plane (fixed image plane) to read")
    parser.add_argument("--out", required=True, help="output directory (new or empty)")
    parser.add_argument("--angles-deg", help="ANGULAR field points: degrees from the reference axis, A[,B..]")
    parser.add_argument("--object-heights-mm", help="OBJECT_HEIGHT field points (finite object): mm, H[,..]")
    parser.add_argument("--image-heights-mm", help="IMAGE_HEIGHT field points: real chief-ray heights on the plane, mm")
    parser.add_argument("--azimuth-deg", type=float, default=0.0,
                        help="field direction in the plane's (u, v) axes, from +u toward +v (default 0)")
    parser.add_argument("--source", help="reference source on the optical axis (default: the only source)")
    parser.add_argument("--configuration", help="configuration of a Project that declares configurations")
    parser.add_argument("--wavelengths-nm", help="the field points' vacuum wavelengths (default: the reference's lines)")
    parser.add_argument("--chief-ray-wavelength-nm", type=float,
                        help="the line whose chief ray is aimed and measured (default: the first line)")
    parser.add_argument("--expect-field-kind", choices=fg.FIELD_KINDS,
                        help="refuse a request of another kind (the field kind the Project's documentation states)")
    parser.add_argument("--mtf-frequencies", help="optical MTF at these frequencies, cycles/mm, F[,..]")
    parser.add_argument("--imaging-request", help="imaging request JSON for the pupil sampling (launch_grid, psf_*, "
                                                  "reference, ...); fields, lines, frequencies and crossing are set here")
    parser.add_argument("--launch-grid", type=int, help="imaging launch grid (16..256; the Engine default 64)")
    parser.add_argument("--no-convergence", action="store_true", help="skip the imaging convergence pass")
    parser.add_argument("--aim-tolerance-mm", type=float, default=DEFAULT_AIM_TOLERANCE_MM,
                        help="how far from the stop centre an aimed chief ray may cross (default %(default)s)")
    parser.add_argument("--height-tolerance-mm", type=float, default=DEFAULT_HEIGHT_TOLERANCE_MM,
                        help="IMAGE_HEIGHT solve tolerance (default %(default)s)")
    parser.add_argument("--pivot-mm", help="turn the field points about X,Y,Z (design frame, mm) instead of aiming "
                                           "them at the aperture stop (NOT_AIMED)")
    parser.add_argument("--write-run", action="store_true",
                        help="also write the evaluated run DIR/run (for field, fans and mtf)")
    parser.add_argument("--overwrite", action="store_true", help="replace this command's files in a non-empty --out")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()

    def workflow():
        args = parser.parse_args(argv)
        request = request_from_arguments(args)
        report = run(request, args.out, overwrite=args.overwrite)
        return summary(report, args.out)

    return wc.emit(workflow, EXIT_CODES)


__all__ = ["EXIT_CODES", "FieldPointsError", "Point", "Request", "SCHEMA", "build_parser", "check_request",
           "configured", "main", "measure", "minima", "paraxial_reference", "read_input", "reference_source", "run",
           "solve", "spectral_lines", "summary"]
