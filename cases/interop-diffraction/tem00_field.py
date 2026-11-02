"""Sampled TEM00 field from an exported Gaussian beam state (client-side construction).

OpenSci reports the Gaussian state of a beam at a plane (1/e^2 intensity radii,
waist distances, Rayleigh ranges, M^2, power); it does not export a field. This
module builds a sampled complex field from that state under stated assumptions:

* fully coherent TEM00 beam: only valid when the run reports M^2 = 1 on both axes
  (M^2 > 1 alone does not determine a mode decomposition or coherence);
* scalar, paraxial field in a homogeneous medium of index n;
* field convention E ~ exp[i(k.r - w t)] (the Engine's convention), k = 2 pi n / lambda;
* waist_distance z > 0 means the waist lies upstream (diverging beam): radius of
  curvature R = z (1 + (z_R / z)^2) > 0 and the transverse phase is exp(+i k r^2 / (2 R));
* the Gouy phase and any piston are omitted (one constant phase across the plane);
* amplitude normalized so that the integral of |U|^2 over the plane is the beam power
  (W, power flux through a plane perpendicular to the beam; units sqrt(W)/mm).

Only numpy is needed; no OpenSci import.
"""

from __future__ import annotations

import math

import numpy as np


def beam_at_distance(waist_radius_mm, waist_distance_mm, wavelength_nm, medium_index=1.0, m2=1.0):
    """1/e^2 radius w and wavefront radius R (mm; inf at the waist) at distance z past the waist."""
    lam_mm = wavelength_nm * 1e-6 / medium_index
    z_r = math.pi * waist_radius_mm ** 2 / (m2 * lam_mm)
    z = waist_distance_mm
    w = waist_radius_mm * math.sqrt(1.0 + (z / z_r) ** 2)
    radius = math.inf if z == 0 else z * (1.0 + (z_r / z) ** 2)
    return w, radius, z_r


def propagate_state(waist_distance_mm, rayleigh_range_mm, distance_mm, wavelength_nm, medium_index=1.0):
    """Free-space propagation of one axis: (z, z_R) -> (w, R, z') after ``distance_mm`` (q = z + d)."""
    z = waist_distance_mm + distance_mm
    lam_mm = wavelength_nm * 1e-6 / medium_index
    w0 = math.sqrt(lam_mm * rayleigh_range_mm / math.pi)
    w = w0 * math.sqrt(1.0 + (z / rayleigh_range_mm) ** 2)
    radius = math.inf if z == 0 else z * (1.0 + (rayleigh_range_mm / z) ** 2)
    return w, radius, z


def sampled_field(x_mm, y_mm, *, w_x_mm, w_y_mm, r_x_mm, r_y_mm, power_w, wavelength_nm, medium_index=1.0):
    """Complex TEM00 field on the grid (x, y) in the beam's own transverse axes (sqrt(W)/mm)."""
    k = 2.0 * math.pi * medium_index / (wavelength_nm * 1e-6)
    x, y = np.asarray(x_mm, dtype=float), np.asarray(y_mm, dtype=float)
    amplitude = math.sqrt(2.0 * power_w / (math.pi * w_x_mm * w_y_mm))
    phase = np.zeros(np.broadcast(x, y).shape)
    if math.isfinite(r_x_mm):
        phase = phase + k * x ** 2 / (2.0 * r_x_mm)
    if math.isfinite(r_y_mm):
        phase = phase + k * y ** 2 / (2.0 * r_y_mm)
    return amplitude * np.exp(-(x / w_x_mm) ** 2 - (y / w_y_mm) ** 2) * np.exp(1j * phase)


def power_inside_circle(radius_mm, w_mm, power_w=1.0):
    """Power of a round TEM00 beam (1/e^2 radius w) inside a centred circle: P (1 - exp(-2 a^2 / w^2))."""
    return power_w * (1.0 - math.exp(-2.0 * radius_mm ** 2 / w_mm ** 2))
