"""Small 3-D vector and quaternion helpers of the kit's geometry tools (pure Python, no numpy).

Vectors are 3-sequences of floats in the design frame (mm for points). Quaternions are ``[w, x, y, z]``, Hamilton,
active, as the Engine's ``PoseV1.quaternion_wxyz``: ``x_design = R(q) x_local + position``. A source emits along its
local +Z, so its emission axis is ``rotate(q, (0, 0, 1))``. ``qmul(turn, q)`` applies ``turn`` about a design-frame
axis after ``q`` (pre-multiplication); ``qmul(q, turn)`` would turn about the body's own (local) axis.
"""

from __future__ import annotations

import math
from typing import Sequence

Vector = Sequence[float]


def add(a: Vector, b: Vector) -> list[float]:
    return [a[k] + b[k] for k in range(3)]


def sub(a: Vector, b: Vector) -> list[float]:
    return [a[k] - b[k] for k in range(3)]


def scale(a: Vector, factor: float) -> list[float]:
    return [a[k] * factor for k in range(3)]


def dot(a: Vector, b: Vector) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a: Vector, b: Vector) -> list[float]:
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def norm(a: Vector) -> float:
    return math.sqrt(dot(a, a))


def unit(a: Vector) -> list[float]:
    """``a`` scaled to length 1; ValueError for a zero or non-finite vector."""
    length = norm(a)
    if not (math.isfinite(length) and length > 0.0):
        raise ValueError(f"not a direction: {list(a)!r}")
    return [c / length for c in a]


def across(a: Vector, axis: Vector) -> list[float]:
    """The part of ``a`` perpendicular to the unit ``axis``."""
    return sub(a, scale(axis, dot(a, axis)))


def angle_deg(a: Vector, b: Vector) -> float:
    """The angle between two unit vectors, in degrees (atan2 form: exact near 0 and 180 degrees)."""
    return math.degrees(math.atan2(norm(cross(a, b)), dot(a, b)))


def rotate(q: Sequence[float], v: Vector) -> tuple[float, float, float]:
    """``v`` rotated by the quaternion ``q`` (normalized here): ``q v q*``."""
    w, x, y, z = q
    length = math.sqrt(w * w + x * x + y * y + z * z)
    w, x, y, z = w / length, x / length, y / length, z / length
    vx, vy, vz = v
    tx = 2 * (y * vz - z * vy)
    ty = 2 * (z * vx - x * vz)
    tz = 2 * (x * vy - y * vx)
    return (vx + w * tx + (y * tz - z * ty), vy + w * ty + (z * tx - x * tz), vz + w * tz + (x * ty - y * tx))


def qmul(a: Sequence[float], b: Sequence[float]) -> list[float]:
    """Hamilton product ``a b``."""
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return [aw * bw - ax * bx - ay * by - az * bz, aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx, aw * bz + ax * by - ay * bx + az * bw]


def axis_angle(axis: Vector, angle_rad: float) -> list[float]:
    """The quaternion of a turn by ``angle_rad`` about the unit ``axis`` (right hand)."""
    half = 0.5 * angle_rad
    s = math.sin(half)
    return [math.cos(half), axis[0] * s, axis[1] * s, axis[2] * s]


def turn_between(a: Vector, b: Vector) -> list[float]:
    """The smallest turn taking the unit vector ``a`` to the unit vector ``b``: about ``a x b``, by their angle (the
    identity when they are parallel). Refuses (ValueError) opposite vectors, whose turn axis is not defined."""
    axis = cross(a, b)
    sine, cosine = norm(axis), dot(a, b)
    if sine <= 1e-15:
        if cosine > 0:
            return [1.0, 0.0, 0.0, 0.0]
        raise ValueError("opposite directions: the turn axis is not defined")
    return axis_angle(scale(axis, 1.0 / sine), math.atan2(sine, cosine))


def frame(q: Sequence[float]) -> tuple[tuple[float, float, float], ...]:
    """The local X, Y and Z axes of a pose's quaternion, in the design frame."""
    return rotate(q, (1.0, 0.0, 0.0)), rotate(q, (0.0, 1.0, 0.0)), rotate(q, (0.0, 0.0, 1.0))


__all__ = ["across", "add", "angle_deg", "axis_angle", "cross", "dot", "frame", "norm", "qmul", "rotate", "scale",
           "sub", "turn_between", "unit"]
