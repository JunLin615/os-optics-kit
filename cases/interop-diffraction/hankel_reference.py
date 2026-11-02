"""Independent reference: focal-plane field of a truncated TEM00 beam behind an ideal lens.

A round TEM00 field A(r) = exp(-r^2 / w^2) exp(+i k r^2 / (2 R)) is cut by a hard
circular stop of radius a and focused by an aberration-free thin lens of focal
length f. In the Fresnel approximation the back-focal-plane field is the Fourier
transform of the stop field times a quadratic phase that does not change the
irradiance, for any stop-to-lens distance (Goodman, Introduction to Fourier
Optics, sec. 5.2). For a round field the transform is a Hankel transform:

    U_f(rho) = (k / f) * integral_0^a A(r) J0(k rho r / f) r dr

normalized here by the transmitted power P_T = 2 pi integral_0^a |A|^2 r dr, so
``irradiance_per_watt`` is W/mm^2 per watt transmitted through the stop. Only numpy
and scipy (adaptive quadrature) are used; no OpenSci data beyond the inputs.
"""

from __future__ import annotations

import math

import numpy as np
from scipy import integrate, special


class HankelFocus:
    def __init__(self, *, stop_radius_mm, w_mm, r_mm, focal_length_mm, wavelength_nm, medium_index=1.0):
        self.a = float(stop_radius_mm)
        self.w = float(w_mm)
        self.r = float(r_mm)
        self.f = float(focal_length_mm)
        self.k = 2.0 * math.pi * medium_index / (wavelength_nm * 1e-6)
        self.transmitted_fraction = 1.0 - math.exp(-2.0 * self.a ** 2 / self.w ** 2)
        self._p_t = 2.0 * math.pi * integrate.quad(lambda r: abs(self._a(r)) ** 2 * r, 0.0, self.a, limit=200)[0]

    def _a(self, r):
        curvature = 0.0 if not math.isfinite(self.r) else self.k * r * r / (2.0 * self.r)
        return np.exp(-r * r / (self.w * self.w)) * np.exp(1j * curvature)

    def field(self, rho_mm):
        scale = self.k * rho_mm / self.f

        def part(fn):
            return integrate.quad(lambda r: fn(self._a(r) * special.j0(scale * r) * r), 0.0, self.a, limit=400)[0]

        return (self.k / self.f) * complex(part(np.real), part(np.imag))

    def irradiance_per_watt(self, rho_mm):
        """Focal-plane irradiance (W/mm^2) per watt transmitted through the stop."""
        return abs(self.field(rho_mm)) ** 2 / self._p_t

    def encircled_fraction(self, rho_mm):
        """Fraction of the transmitted power inside radius rho of the focal plane."""
        value = integrate.quad(lambda p: abs(self.field(p)) ** 2 * p, 0.0, rho_mm, limit=200)[0]
        return 2.0 * math.pi * value / self._p_t


def gaussian_focus_radius_mm(w_mm, focal_length_mm, wavelength_nm, medium_index=1.0):
    """Untruncated-Gaussian focal 1/e^2 radius for a collimated input: lambda f / (pi w)."""
    return wavelength_nm * 1e-6 / medium_index * focal_length_mm / (math.pi * w_mm)
