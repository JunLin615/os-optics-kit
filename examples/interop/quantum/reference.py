"""Independent closed-form reference for the quantum example (uses no exported network data).

* Glass: N-BK7 three-term Sellmeier formula with the SCHOTT catalogue coefficients;
  group index n_g = n - lambda dn/dlambda (analytic derivative).
* Uncoated window at normal incidence: Fresnel power reflectance R = ((n-1)/(n+1))^2
  per face, ghost reflections not followed; flux-normalized amplitude 1 - R for two faces.
* Splitter: symmetric lossless 50:50 beam splitter BS = [[1, i], [i, 1]] / sqrt(2). The
  HOM statistics do not depend on the splitter's phase convention; the MZI statistics do
  not either as long as both splitters follow the same convention.
* Two photons, one per input, with mutual indistinguishability p (|<psi_A|psi_B>|^2)
  through a 2 x 2 amplitude matrix M[output, input]. Lost light leaves through loss
  modes whose Gram matrix is L = I - M^H M (any passive completion of M gives the same
  L), so loss amplitudes of the two photons interfere when they share a lossy element:
  P(1,1) = |a|^2 + |b|^2 + 2 p Re(a b*), a = M[0,A] M[1,B], b = M[0,B] M[1,A];
  P(2,0) = (1 + p) |M[0,A] M[0,B]|^2;
  P(1,0) = |M[0,A]|^2 L[B,B] + |M[0,B]|^2 L[A,A] + 2 p Re(M[0,A] conj(M[0,B]) L[A,B]);
  P(0,0) = L[A,A] L[B,B] + p |L[A,B]|^2 (p-weighted mix of the indistinguishable and
  distinguishable cases; the probabilities sum to 1).
* Gaussian wavepackets with rms intensity duration sigma: p(dt) = p0 exp(-dt^2 / (4 sigma^2)).
* Threshold detectors of efficiency eta and dark-count probability d per gate:
  P(click | n photons) = 1 - (1 - d)(1 - eta)^n.

For the 50:50 HOM layout without loss this gives the textbook dip
P_cc = eta^2 (1 - p) / 2 (no dark counts), i.e. visibility p.
"""

from __future__ import annotations

import math

import numpy as np

N_BK7_SELLMEIER = {"b": (1.03961212, 0.231792344, 1.01046945), "c_um2": (0.00600069867, 0.0200179144, 103.560653)}
C_MM_PER_PS = 0.299792458
BS_50_50 = np.array([[1.0, 1.0j], [1.0j, 1.0]]) / math.sqrt(2.0)


def sellmeier_index(coefficients, wavelength_nm):
    l2 = (wavelength_nm * 1e-3) ** 2
    return math.sqrt(1.0 + sum(b * l2 / (l2 - c) for b, c in zip(coefficients["b"], coefficients["c_um2"])))


def group_index(coefficients, wavelength_nm):
    """n_g = n - lambda dn/dlambda from the analytic derivative of the Sellmeier formula."""
    lam = wavelength_nm * 1e-3
    l2 = lam * lam
    n = sellmeier_index(coefficients, wavelength_nm)
    dn2_dlam = sum(-2.0 * b * c * lam / (l2 - c) ** 2 for b, c in zip(coefficients["b"], coefficients["c_um2"]))
    return n - lam * dn2_dlam / (2.0 * n)


def window_amplitude(wavelength_nm, coefficients=N_BK7_SELLMEIER):
    n = sellmeier_index(coefficients, wavelength_nm)
    reflectance = ((n - 1.0) / (n + 1.0)) ** 2
    return 1.0 - reflectance


def overlap(delay_ps, rms_duration_ps, zero_delay_overlap):
    return zero_delay_overlap * math.exp(-delay_ps ** 2 / (4.0 * rms_duration_ps ** 2))


def two_photon_distribution(m, p):
    """Photon-number probabilities {(n1, n2): P} at two outputs for photons in inputs 0 and 1 of M[out, in]."""
    m = np.asarray(m, dtype=complex)
    a, b = m[0, 0] * m[1, 1], m[0, 1] * m[1, 0]
    loss = np.eye(2) - m.conj().T @ m  # Gram matrix of the loss amplitudes, L[j, k] = sum_l conj(N[l, j]) N[l, k]

    def one(i):
        return (abs(m[i, 0]) ** 2 * loss[1, 1].real + abs(m[i, 1]) ** 2 * loss[0, 0].real
                + 2.0 * p * (m[i, 0] * np.conj(m[i, 1]) * loss[0, 1]).real)

    return {
        (1, 1): float(abs(a) ** 2 + abs(b) ** 2 + 2.0 * p * (a * np.conj(b)).real),
        (2, 0): float((1.0 + p) * abs(m[0, 0] * m[0, 1]) ** 2),
        (0, 2): float((1.0 + p) * abs(m[1, 0] * m[1, 1]) ** 2),
        (1, 0): float(one(0)),
        (0, 1): float(one(1)),
        (0, 0): float(loss[0, 0].real * loss[1, 1].real + p * abs(loss[0, 1]) ** 2),
    }


def coincidence_probability(distribution, efficiency, dark_count_probability=0.0):
    def click(n):
        return 1.0 - (1.0 - dark_count_probability) * (1.0 - efficiency) ** n

    return sum(prob * click(n1) * click(n2) for (n1, n2), prob in distribution.items())


def hom_matrix(window_mm=0.0, wavelength_nm=780.0):
    """HOM amplitude matrix: optional uncoated window in input arm A (index 0), then the 50:50 splitter."""
    arm_a = window_amplitude(wavelength_nm) if window_mm > 0 else 1.0
    return BS_50_50 @ np.diag([arm_a, 1.0])


def mzi_matrix(window_mm, wavelength_nm=532.0):
    """Balanced MZI with an uncoated window in internal arm B: phase k0 (n - 1) t and Fresnel loss."""
    if window_mm <= 0:
        return BS_50_50 @ BS_50_50
    n = sellmeier_index(N_BK7_SELLMEIER, wavelength_nm)
    phase = 2.0 * math.pi / (wavelength_nm * 1e-6) * (n - 1.0) * window_mm
    return BS_50_50 @ np.diag([1.0, window_amplitude(wavelength_nm) * np.exp(1j * phase)]) @ BS_50_50


def window_group_delay_ps(window_mm, wavelength_nm, coefficients=N_BK7_SELLMEIER):
    """Extra group delay of a window over the same length of air at normal incidence."""
    return (group_index(coefficients, wavelength_nm) - 1.0) * window_mm / C_MM_PER_PS


def window_phase_delay_ps(window_mm, wavelength_nm, coefficients=N_BK7_SELLMEIER):
    return (sellmeier_index(coefficients, wavelength_nm) - 1.0) * window_mm / C_MM_PER_PS
