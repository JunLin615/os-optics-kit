"""Geometry of field points in a centred imaging Project (pure Python: no Engine, numpy or matplotlib).

A field point is one copy of a reference Gaussian source, **turned** about a design-frame axis across the beam and
placed so that its chief ray (the source's axis ray, which the Engine traces as the beam centre) passes through the
centre of the aperture stop. It is never shifted only (the incidence angle would stay that of the axis) and never
turned about its own emission axis (local +Z: the beam would not change direction).

Frames and signs:

* the reference axis ``a`` is the reference source's emission axis (its local +Z in the design frame);
* the field direction ``d`` is the azimuth direction ``cos(phi) u + sin(phi) v`` in the image plane's (u, v) axes,
  made perpendicular to ``a``;
* ANGULAR: the field point's emission axis is ``cos(theta) a + sin(theta) d``: the beam is turned towards ``d`` by
  ``theta`` (the turn axis ``a x d`` lies across the beam). A lens of positive power images it on the ``d`` side;
* OBJECT_HEIGHT (finite object only): the object point (the waist of the reference source) moves by ``h`` along ``d``
  in the object plane (normal to ``a``); the field point's axis aims from it at the stop. A real, inverted image lies
  on the ``-d`` side;
* IMAGE_HEIGHT: ``theta`` (object at infinity) or ``h`` (finite object) solved until the chief ray's height on the
  plane, measured from the on-axis chief ray's hit point, equals the request.

Object conjugate (:func:`conjugate`): the reference source's waist decides it. Within one Rayleigh range of the source
(``|waist_position_mm| <= z_R``) the rays leave nearly parallel: an object at infinity. A waist at least
``FINITE_RAYLEIGH_RANGES`` Rayleigh ranges upstream (``waist_position_mm <= -10 z_R``) is a point object at the waist.
Anything between is refused (``CONJUGATE_UNDETERMINED``): it is neither a collimated field nor a point object.

Ray aiming (:class:`Aim`): the chief ray's crossing of the stop plane is measured by the Engine; the lateral offset of a
field point's line (ANGULAR: its direction is fixed) or of its aim point at the stop (OBJECT_HEIGHT: its object point
is fixed) is corrected by a two-dimensional Broyden iteration, started from the paraxial entrance-pupil estimate
(:func:`pupil_estimate`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Sequence

from . import geometry3d as g

FIELD_KINDS = ("ANGULAR", "OBJECT_HEIGHT", "IMAGE_HEIGHT")
FIELD_UNITS = {"ANGULAR": "deg", "OBJECT_HEIGHT": "mm", "IMAGE_HEIGHT": "mm"}
INFINITE = "INFINITE"
FINITE = "FINITE"
#: a waist within this many Rayleigh ranges of the source origin: object at infinity
INFINITE_RAYLEIGH_RANGES = 1.0
#: a waist at least this many Rayleigh ranges upstream of the source origin: a point object at the waist
FINITE_RAYLEIGH_RANGES = 10.0
#: the largest field angle a field point may be turned by
MAX_ANGLE_DEG = 89.0
#: tolerance of the turned-source guard (degrees, and mm relative to max(1, h))
TURN_TOLERANCE_DEG = 1e-9
#: parameterizations of a field point: a line of fixed direction (ANGULAR), an object point of fixed position
LINE = "LINE"
POINT = "POINT"


class FieldGeometryError(ValueError):
    """A request the geometry cannot serve: ``code`` (CONJUGATE_UNDETERMINED, OBJECT_AT_INFINITY,
    FIELD_POINT_NOT_TURNED, AZIMUTH_ALONG_AXIS, SOURCE_UNSUPPORTED), message and details."""

    def __init__(self, code: str, message: str, **details: Any):
        super().__init__(message)
        self.code = code
        self.details = details


# ------------------------------------------------------------------------------------------- conjugate

def _positive(model: dict, key: str) -> float | None:
    value: Any = model.get(key)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def rayleigh_range_mm(model: dict, wavelength_nm: float) -> float:
    """The Rayleigh range ``pi w0^2 / (M^2 lambda)`` of a Gaussian source model at one vacuum wavelength (the
    shorter of the two sections when the model states ``waist_radius_y_mm`` / ``m2_y``)."""
    ranges = []
    for radius_key, m2_key in (("waist_radius_mm", "m2"), ("waist_radius_y_mm", "m2_y")):
        radius = _positive(model, radius_key)
        if radius is None:
            continue
        m2 = _positive(model, m2_key) or _positive(model, "m2") or 1.0
        ranges.append(math.pi * radius * radius / (m2 * wavelength_nm * 1e-6))
    if not ranges:
        raise FieldGeometryError("SOURCE_UNSUPPORTED", "the reference source states no positive waist_radius_mm")
    return min(ranges)


def conjugate(source: dict, wavelength_nm: float) -> dict:
    """The object conjugate of a reference Gaussian source: ``kind`` INFINITE or FINITE, the rule, the waist
    position and Rayleigh range at ``wavelength_nm`` and their ratio, and the object point (FINITE: the waist,
    design frame). Refuses (CONJUGATE_UNDETERMINED) a waist between the two regimes."""
    model = source.get("source_model") or {}
    if model.get("model_kind") != "GAUSSIAN_BEAM":
        raise FieldGeometryError("SOURCE_UNSUPPORTED", f"the reference source {source.get('instance_id')} is a "
                                 f"{model.get('model_kind')} source; field points copy a GAUSSIAN_BEAM source")
    stated: Any = model.get("waist_position_mm") or 0.0
    try:
        waist = float(stated)
    except (TypeError, ValueError):
        raise FieldGeometryError("SOURCE_UNSUPPORTED", "the reference source's waist_position_mm is not a number") \
            from None
    z_r = rayleigh_range_mm(model, wavelength_nm)
    ratio = waist / z_r
    pose = source["nominal_pose"]
    axis = g.unit(g.rotate(pose["quaternion_wxyz"], (0.0, 0.0, 1.0)))
    record: dict[str, Any] = {"kind": None, "waist_position_mm": waist, "rayleigh_range_mm": z_r, "waist_position_rayleigh_ranges": ratio,
              "wavelength_nm": wavelength_nm, "object_point_mm": None,
              "rule": (f"object at infinity when the reference waist lies within {INFINITE_RAYLEIGH_RANGES:g} Rayleigh "
                       f"range of the source origin (|waist_position_mm| <= z_R); a point object at the waist when it "
                       f"lies at least {FINITE_RAYLEIGH_RANGES:g} Rayleigh ranges upstream (waist_position_mm <= "
                       f"-{FINITE_RAYLEIGH_RANGES:g} z_R); z_R = pi w0^2 / (M^2 lambda)")}
    if abs(ratio) <= INFINITE_RAYLEIGH_RANGES:
        record["kind"] = INFINITE
    elif ratio <= -FINITE_RAYLEIGH_RANGES:
        record["kind"] = FINITE
        record["object_point_mm"] = g.add(pose["position_mm"], g.scale(axis, waist))
    else:
        raise FieldGeometryError(
            "CONJUGATE_UNDETERMINED",
            f"the reference source's waist lies {waist:.6g} mm from its origin, {ratio:.4g} Rayleigh ranges "
            f"(z_R {z_r:.6g} mm at {wavelength_nm:g} nm): neither a collimated field nor a point object; put the waist "
            f"at the source (object at infinity) or far upstream (a point object)", **record)
    return record


# ---------------------------------------------------------------------------------------------- frame

@dataclass
class Frame:
    """The reference of a field-point request (design frame): the reference source's emission ``axis``, origin,
    quaternion and waist position, the field ``direction`` and ``across`` (= axis x direction), the conjugate kind and
    object point, the aim ``centre`` (stop centre, or the pivot) and the entrance-pupil centre estimate ``pupil``."""

    axis: list[float]
    direction: list[float]
    across: list[float]
    origin: list[float]
    quaternion: list[float]
    waist_position_mm: float
    conjugate: str
    object_point: list[float] | None
    centre: list[float]
    pupil: list[float]


def field_direction(axis: Sequence[float], u_axis: Sequence[float], v_axis: Sequence[float],
                    azimuth_deg: float) -> list[float]:
    """The unit field direction across the axis: ``cos(phi) u + sin(phi) v`` without its part along ``axis``."""
    phi = math.radians(azimuth_deg)
    raw = g.add(g.scale(u_axis, math.cos(phi)), g.scale(v_axis, math.sin(phi)))
    part = g.across(raw, axis)
    if g.norm(part) < 1e-6:
        raise FieldGeometryError("AZIMUTH_ALONG_AXIS", f"the azimuth {azimuth_deg:g} deg points along the reference "
                                 "axis in the plane's frame: it gives no direction across the beam")
    return g.unit(part)


def make_frame(source: dict, conjugate_record: dict, u_axis: Sequence[float], v_axis: Sequence[float],
               azimuth_deg: float, centre: Sequence[float]) -> Frame:
    pose = source["nominal_pose"]
    axis = g.unit(g.rotate(pose["quaternion_wxyz"], (0.0, 0.0, 1.0)))
    direction = field_direction(axis, u_axis, v_axis, azimuth_deg)
    return Frame(axis=axis, direction=direction, across=g.cross(axis, direction),
                 origin=[float(c) for c in pose["position_mm"]], quaternion=[float(c) for c in pose["quaternion_wxyz"]],
                 waist_position_mm=float(conjugate_record["waist_position_mm"]), conjugate=conjugate_record["kind"],
                 object_point=conjugate_record["object_point_mm"], centre=[float(c) for c in centre],
                 pupil=[float(c) for c in centre])


# --------------------------------------------------------------------------------------------- poses

def angular_axis(frame: Frame, theta_deg: float) -> list[float]:
    theta = math.radians(theta_deg)
    return g.add(g.scale(frame.axis, math.cos(theta)), g.scale(frame.direction, math.sin(theta)))


def basis(frame: Frame, mode: str, parameter: float) -> tuple[list[float], list[float]]:
    """The two directions of a field point's aim offset: across its line (LINE) or across the axis (POINT)."""
    if mode == LINE:
        new_axis = angular_axis(frame, parameter)
        first = g.unit(g.across(frame.direction, new_axis))
        return first, g.cross(new_axis, first)
    return list(frame.direction), list(frame.across)


def _station(frame: Frame) -> list[float]:
    """The plane (normal to the axis, through this point) holding a LINE field point's source origin (object at
    infinity) or its object point (finite object)."""
    return frame.object_point if frame.conjugate == FINITE and frame.object_point is not None else frame.origin


def object_point(frame: Frame, height_mm: float) -> list[float]:
    assert frame.object_point is not None
    return g.add(frame.object_point, g.scale(frame.direction, height_mm))


def pose(frame: Frame, mode: str, parameter: float, offset: Sequence[float]) -> dict:
    """The pose of a field point: ``position_mm``, ``quaternion_wxyz`` (the reference turned by the smallest turn
    from its axis to the new one, about a design-frame axis across the beam), ``axis`` and ``object_point_mm``
    (finite object). ``parameter``: the angle in degrees (LINE) or the object height in mm (POINT); ``offset``: the
    aim offset (mm) along :func:`basis`."""
    first, second = basis(frame, mode, parameter)
    if mode == LINE:
        new_axis = angular_axis(frame, parameter)
        through = g.add(frame.centre, g.add(g.scale(first, offset[0]), g.scale(second, offset[1])))
        station = _station(frame)
        t = g.dot(g.sub(station, through), frame.axis) / g.dot(new_axis, frame.axis)
        on_station = g.add(through, g.scale(new_axis, t))
        obj = on_station if frame.conjugate == FINITE else None
    else:
        obj = object_point(frame, parameter)
        target = g.add(frame.centre, g.add(g.scale(first, offset[0]), g.scale(second, offset[1])))
        new_axis = g.unit(g.sub(target, obj))
        on_station = obj
    # a point object sits at the waist: the source origin lies -waist_position_mm along the new axis from it
    position = g.sub(obj, g.scale(new_axis, frame.waist_position_mm)) if obj is not None else on_station
    quaternion = g.qmul(g.turn_between(frame.axis, new_axis), frame.quaternion)
    return {"position_mm": position, "quaternion_wxyz": quaternion, "axis": new_axis, "object_point_mm": obj}


def initial_offset(frame: Frame, mode: str, parameter: float) -> list[float]:
    """The aim offset whose chief ray passes the entrance-pupil centre estimate ``frame.pupil`` (zero when it is the
    aim centre itself)."""
    first, second = basis(frame, mode, parameter)
    if mode == LINE:
        shift = g.sub(frame.pupil, frame.centre)
        return [g.dot(shift, first), g.dot(shift, second)]
    obj = object_point(frame, parameter)
    toward = g.sub(frame.pupil, obj)
    along = g.dot(toward, frame.axis)
    if abs(along) < 1e-12:
        return [0.0, 0.0]
    target = g.add(obj, g.scale(toward, g.dot(g.sub(frame.centre, obj), frame.axis) / along))
    shift = g.sub(target, frame.centre)
    return [g.dot(shift, first), g.dot(shift, second)]


def offset_gain(frame: Frame, mode: str, parameter: float, pupil_magnification: float) -> float:
    """The paraxial change of the stop crossing per unit aim offset: the pupil magnification, scaled for a POINT field
    point by the ratio of its object point's distances to the entrance pupil and to the stop."""
    if mode == LINE:
        return pupil_magnification
    obj = object_point(frame, parameter)
    to_pupil = g.dot(g.sub(frame.pupil, obj), frame.axis)
    to_centre = g.dot(g.sub(frame.centre, obj), frame.axis)
    return pupil_magnification * (to_pupil / to_centre if abs(to_centre) > 1e-12 else 1.0)


def aim_error(frame: Frame, mode: str, parameter: float, crossing_mm: Sequence[float]) -> list[float]:
    """The stop crossing's offset from the aim centre along the field point's offset basis (mm)."""
    first, second = basis(frame, mode, parameter)
    miss = g.sub(crossing_mm, frame.centre)
    return [g.dot(miss, first), g.dot(miss, second)]


def verify_turned(quaternion: Sequence[float], frame: Frame, expected_axis: Sequence[float],
                  expected_angle_deg: float | None = None) -> float:
    """The field angle (deg) of a built source, after checking that its emission axis is the intended one and, when
    given, that its angle to the reference axis is the requested one; FIELD_POINT_NOT_TURNED otherwise (a guard
    against a shifted-only source or a turn about the beam's own axis)."""
    emitted = g.unit(g.rotate(quaternion, (0.0, 0.0, 1.0)))
    angle = g.angle_deg(emitted, frame.axis)
    miss = g.angle_deg(emitted, g.unit(expected_axis))
    off = None if expected_angle_deg is None else abs(angle - expected_angle_deg)
    if miss > TURN_TOLERANCE_DEG or (off is not None and off > TURN_TOLERANCE_DEG):
        raise FieldGeometryError("FIELD_POINT_NOT_TURNED",
                                 f"the built source emits at {angle:.12g} deg to the reference axis, "
                                 f"{miss:.3g} deg from its intended axis (requested angle {expected_angle_deg!r})",
                                 angle_deg=angle, axis_miss_deg=miss, requested_angle_deg=expected_angle_deg)
    return angle


# ------------------------------------------------------------------------------------- pupil and aiming

def pupil_estimate(frame: Frame, axis_crossing: Sequence[float] | None, shift_crossing: Sequence[float] | None,
                   tilt_crossing: Sequence[float] | None, shift_mm: float, tilt_deg: float) -> dict:
    """The paraxial entrance pupil from three probe chief rays (all crossing the stop plane): along the axis through
    the stop centre, shifted by ``shift_mm`` along the field direction, and turned by ``tilt_deg`` about the stop
    centre. ``pupil_magnification`` m = (shifted crossing) / shift; ``entrance_pupil_from_stop_mm`` z = (turned
    crossing) / (m tan(tilt)), along the axis (negative: the entrance pupil lies before the stop). Without three
    crossings, or with |m| < 1e-6, the estimate is the stop itself (m = 1, z = 0) and ``basis`` says why."""
    if axis_crossing is None or shift_crossing is None or tilt_crossing is None:
        return {"pupil_magnification": 1.0, "entrance_pupil_from_stop_mm": 0.0,
                "basis": "NO_PROBE_CROSSING: a probe chief ray did not cross the stop; aiming starts at the stop"}
    m = g.dot(g.sub(shift_crossing, axis_crossing), frame.direction) / shift_mm
    if not (math.isfinite(m) and abs(m) >= 1e-6):
        return {"pupil_magnification": 1.0, "entrance_pupil_from_stop_mm": 0.0,
                "basis": f"PUPIL_MAGNIFICATION_ZERO: the shifted probe moved the stop crossing by {m * shift_mm:.3g} mm"}
    tilt = g.dot(g.sub(tilt_crossing, axis_crossing), frame.direction)
    z = tilt / (m * math.tan(math.radians(tilt_deg)))
    return {"pupil_magnification": m, "entrance_pupil_from_stop_mm": z,
            "basis": (f"paraxial: three probe chief rays (on the axis, shifted {shift_mm:g} mm, turned {tilt_deg:g} deg "
                      "about the stop centre) traced by the Engine")}


@dataclass
class Aim:
    """One field point's aim: the offset along its basis, a Broyden estimate of d(crossing)/d(offset), and the last
    (offset, error) pair."""

    offset: list[float]
    gain: float
    max_step_mm: float
    jacobian: list[list[float]] = field(default_factory=list)
    last: tuple[list[float], list[float]] | None = None

    def __post_init__(self) -> None:
        if not self.jacobian:
            self.jacobian = [[self.gain, 0.0], [0.0, self.gain]]

    def step(self, error: Sequence[float]) -> list[float]:
        """The next offset after a measured ``error`` at the current one (Broyden's update of the estimate, a step
        no longer than ``max_step_mm``)."""
        error = [float(error[0]), float(error[1])]
        if self.last is not None:
            ds = [self.offset[0] - self.last[0][0], self.offset[1] - self.last[0][1]]
            de = [error[0] - self.last[1][0], error[1] - self.last[1][1]]
            dd = ds[0] * ds[0] + ds[1] * ds[1]
            if dd > 0:
                j = self.jacobian
                r = [de[0] - (j[0][0] * ds[0] + j[0][1] * ds[1]), de[1] - (j[1][0] * ds[0] + j[1][1] * ds[1])]
                self.jacobian = [[j[0][0] + r[0] * ds[0] / dd, j[0][1] + r[0] * ds[1] / dd],
                                 [j[1][0] + r[1] * ds[0] / dd, j[1][1] + r[1] * ds[1] / dd]]
        j = self.jacobian
        det = j[0][0] * j[1][1] - j[0][1] * j[1][0]
        if not math.isfinite(det) or abs(det) < 1e-12 * max(1.0, self.gain * self.gain):
            self.jacobian = j = [[self.gain, 0.0], [0.0, self.gain]]
            det = self.gain * self.gain
        dx = [-(j[1][1] * error[0] - j[0][1] * error[1]) / det, -(-j[1][0] * error[0] + j[0][0] * error[1]) / det]
        length = math.hypot(*dx)
        if length > self.max_step_mm:
            dx = [c * self.max_step_mm / length for c in dx]
        self.last = (list(self.offset), error)
        self.offset = [self.offset[0] + dx[0], self.offset[1] + dx[1]]
        return self.offset

    def shift(self, delta: Sequence[float]) -> None:
        """Move the offset by ``delta`` without a measurement (the field parameter changed); the estimate is kept."""
        self.offset = [self.offset[0] + delta[0], self.offset[1] + delta[1]]
        self.last = None


def secant(history: Sequence[tuple[float, float]], target: float, upper: float) -> float:
    """The next field parameter of a one-dimensional solve ``height(parameter) = target`` from the (parameter,
    height) history: proportional from one point, secant from the last two, kept within [0, upper]."""
    p1, h1 = history[-1]
    if len(history) == 1 or history[-2][1] == h1:
        guess = p1 * target / h1 if h1 > 0 else (p1 * 2.0 if p1 > 0 else upper * 1e-3)
    else:
        p0, h0 = history[-2]
        guess = p1 + (target - h1) * (p1 - p0) / (h1 - h0)
    if not math.isfinite(guess):
        guess = p1
    return min(max(guess, 0.0), upper)


# --------------------------------------------------------------------------------------- first order

def efl_reference_height(efl_mm: float, angle_deg: float) -> float:
    """``f tan(theta)``: the first-order image height of a field angle (object at infinity)."""
    return efl_mm * math.tan(math.radians(angle_deg))


def object_to_plane(abcd: Sequence[Sequence[float]], object_to_input_mm: float,
                    output_to_plane_mm: float) -> list[list[float]]:
    """The reduced paraxial ABCD from the object plane to the image plane, ``T(d_out) M T(d_in)``, in air; its A is
    the lateral magnification between them (B = 0 when the plane is the paraxial image of the object plane)."""
    (a, b), (c, d) = abcd
    a1, b1, c1, d1 = a, a * object_to_input_mm + b, c, c * object_to_input_mm + d
    return [[a1 + output_to_plane_mm * c1, b1 + output_to_plane_mm * d1], [c1, d1]]


# ------------------------------------------------------------------------------------------ apertures

def inside_aperture(aperture: dict, uv: Sequence[float]) -> bool:
    """Whether a point (u, v, mm) of an observation plane lies in its declared active area."""
    cu, cv = (aperture.get("center_offset_mm") or [0.0, 0.0])[:2]
    du, dv = float(uv[0]) - float(cu), float(uv[1]) - float(cv)
    if aperture.get("shape") == "rectangle":
        return abs(du) <= float(aperture["half_width_mm"]) and abs(dv) <= float(aperture["half_height_mm"])
    radius = math.hypot(du, dv)
    return float(aperture.get("obstruction_radius_mm") or 0.0) <= radius <= float(aperture["radius_mm"])


#: a widened aperture covers each chief ray's hit plus this many pupil RMS radii, then this relative margin
WIDEN_PUPIL_RADII = 3.0
WIDEN_MARGIN = 1.05


def widened_aperture(aperture: dict, footprints: Sequence[tuple[Sequence[float], float]]) -> dict | None:
    """The declared aperture grown (same shape, centre and pose) to hold every ``(uv_mm, pupil_rms_radius_mm)``
    footprint with WIDEN_PUPIL_RADII pupil radii and a WIDEN_MARGIN margin, and without a central obstruction that a
    chief ray falls into; None when the declared aperture already holds every footprint."""
    cu, cv = (aperture.get("center_offset_mm") or [0.0, 0.0])[:2]
    reach = [(abs(float(uv[0]) - float(cu)), abs(float(uv[1]) - float(cv)), WIDEN_PUPIL_RADII * max(0.0, float(rms)))
             for uv, rms in footprints]
    widened = dict(aperture)
    if aperture.get("shape") == "rectangle":
        half_w = max([float(aperture["half_width_mm"])] + [WIDEN_MARGIN * (du + m) for du, _dv, m in reach])
        half_h = max([float(aperture["half_height_mm"])] + [WIDEN_MARGIN * (dv + m) for _du, dv, m in reach])
        widened.update(half_width_mm=half_w, half_height_mm=half_h)
    else:
        radius = max([float(aperture["radius_mm"])] + [WIDEN_MARGIN * (math.hypot(du, dv) + m) for du, dv, m in reach])
        widened["radius_mm"] = radius
        obstruction = float(aperture.get("obstruction_radius_mm") or 0.0)
        if obstruction > 0 and any(math.hypot(du, dv) < obstruction for du, dv, _m in reach):
            widened["obstruction_radius_mm"] = 0.0
    return None if widened == aperture else widened


__all__ = ["Aim", "FIELD_KINDS", "FIELD_UNITS", "FINITE", "FINITE_RAYLEIGH_RANGES", "FieldGeometryError", "Frame",
           "INFINITE", "INFINITE_RAYLEIGH_RANGES", "LINE", "MAX_ANGLE_DEG", "POINT", "aim_error", "angular_axis",
           "basis", "conjugate", "efl_reference_height", "field_direction", "initial_offset", "inside_aperture",
           "make_frame", "object_point", "object_to_plane", "offset_gain", "pose", "pupil_estimate",
           "rayleigh_range_mm", "secant", "verify_turned", "widened_aperture"]
