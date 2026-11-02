"""External side of the diffraction example: focal-plane diffraction from OpenSci handoff files.

Reads only the ``handoff.json`` files written by ``opensci_handoff.py`` (no OpenSci
import, so it also runs in a separate environment, e.g. one with numpy < 2 for
Diffractio). For each case it builds the sampled TEM00 field at the stop with the
convention in ``tem00_field.py``, applies the hard stop and the ideal lens phase,
and computes the back-focal-plane irradiance with:

* LightPipes (Fresnel ``Forvard``, absolute W/mm^2) on several grids and windows:
  the convergence study;
* prysm (matrix DFT), POPPY (Fraunhofer, ``ArrayOpticalElement`` with the sampled
  amplitude and OPD) and, when installed, Diffractio (chirp z-transform, Rayleigh-
  Sommerfeld kernel) as cross-checks of the normalized radial profile;
* an independent Hankel-integral reference (``hankel_reference.py``).

It then compares the Engine's paraxial Gaussian focal values from the handoff with
the reference. Reads the documented diffraction handoff schema. Diffractio results
use a ``_diffractio`` filename suffix so they preserve the primary comparison.

Usage: python examples/interop/diffraction/external_focus.py run-interop-diffraction
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import sys
import time
import warnings

import numpy as np

if __package__:
    from ..common import ArgumentParser, emit, environment, write_json
    from .hankel_reference import HankelFocus
    from .tem00_field import sampled_field
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from common import ArgumentParser, emit, environment, write_json
    from hankel_reference import HankelFocus
    from tem00_field import sampled_field

HANDOFF_SCHEMA = "opensci-interop-example/diffraction-handoff/1"
# LightPipes grids: (samples per side, window side in mm). The last three settings define the convergence spread.
LIGHTPIPES_GRIDS = ((512, 6.0), (1024, 6.0), (2048, 6.0), (4096, 6.0), (2048, 9.0), (4096, 9.0))
PROFILE_RADII_MM = tuple(round(0.005 * i, 3) for i in range(21))  # 0 ... 0.1 mm
TOLERANCES = {"lightpipes_peak_rel": 5e-3, "lightpipes_encircled_abs": 5e-3, "convergence_peak_rel": 5e-3,
              "convergence_encircled_abs": 5e-3, "lightpipes_profile_abs": 5e-3,
              "cross_check_profile_abs": 1e-3, "cross_check_peak_rel": 1e-3,
              "engine_power_rel": 1e-9}
ENGINE_ACCURATE_REL = 0.01  # the Engine's Gaussian peak is called diffraction-accurate within 1 %


def radial_line(image, dx_mm, radii_mm, center=None):
    """Values along +x from the centre pixel, linearly interpolated at ``radii_mm``."""
    n = image.shape[0]
    cy, cx = center if center is not None else (n // 2, n // 2)
    line = image[cy, cx:]
    return np.interp(radii_mm, np.arange(line.size) * dx_mm, line)


def encircled(image, dx_mm, radius_mm):
    n = image.shape[0]
    coords = (np.arange(n) - n // 2) * dx_mm
    xx, yy = np.meshgrid(coords, coords)
    return float(image[np.hypot(xx, yy) <= radius_mm].sum() * dx_mm * dx_mm)


def circle_coverage(xx, yy, radius_mm, dx_mm, subsamples=8):
    """Fraction of each pixel inside a centred circle (edge pixels supersampled), so the sampled stop keeps its area."""
    r = np.hypot(xx, yy)
    coverage = (r <= radius_mm).astype(float)
    edge = np.abs(r - radius_mm) <= dx_mm
    offsets = (np.arange(subsamples) + 0.5) / subsamples - 0.5
    ox, oy = np.meshgrid(offsets * dx_mm, offsets * dx_mm)
    ex, ey = xx[edge][:, None], yy[edge][:, None]
    inside = np.hypot(ex + ox.ravel()[None, :], ey + oy.ravel()[None, :]) <= radius_mm
    coverage[edge] = inside.mean(axis=1)
    return coverage


def stop_field(case, n, window_mm):
    """Sampled TEM00 field times the hard stop (area-weighted edge pixels): sqrt(W)/mm on an n x n grid."""
    beam = case["beam_at_stop"]
    dx = window_mm / n
    coords = (np.arange(n) - n // 2) * dx
    xx, yy = np.meshgrid(coords, coords)
    field = sampled_field(xx, yy, w_x_mm=beam["w_x_mm"], w_y_mm=beam["w_y_mm"],
                          r_x_mm=beam["r_x_mm"] or math.inf, r_y_mm=beam["r_y_mm"] or math.inf,
                          power_w=beam["power_W"], wavelength_nm=beam["wavelength_nm"], medium_index=beam["medium_index"])
    return field * circle_coverage(xx, yy, case["stop"]["radius_mm"], dx), dx


def lightpipes_focus(case, n, window_mm):
    import LightPipes as lp

    lam_m = case["beam_at_stop"]["wavelength_nm"] * 1e-9
    field, dx = stop_field(case, n, window_mm)
    beam = lp.Begin(window_mm * 1e-3, lam_m, n)
    beam.field = field * 1e3  # sqrt(W)/mm -> sqrt(W)/m
    beam = lp.Forvard(beam, case["lens"]["distance_from_stop_mm"] * 1e-3)
    beam = lp.Lens(beam, case["lens"]["focal_length_mm"] * 1e-3)
    beam = lp.Forvard(beam, case["observation"]["distance_from_lens_mm"] * 1e-3)
    return np.asarray(lp.Intensity(beam, 0)) * 1e-6, dx  # W/mm^2


def prysm_profile(case, radii):
    from prysm.propagation import Wavefront

    a = case["stop"]["radius_mm"]
    n = 1024
    window = 2.2 * a
    field, dx = stop_field(case, n, window)
    lam_nm = case["beam_at_stop"]["wavelength_nm"]
    k = 2 * math.pi / (lam_nm * 1e-6)
    opd_nm = np.angle(field) / k * 1e6
    wavefront = Wavefront.from_amp_and_phase(np.abs(field), opd_nm, lam_nm * 1e-3, dx)
    out = wavefront.focus_fixed_sampling(case["lens"]["focal_length_mm"], 0.5, 1024)  # focal dx in micrometres
    image = np.abs(out.data) ** 2
    return radial_line(image, 0.5e-3, radii), {"pupil_samples": n, "pupil_window_mm": window, "focal_dx_um": 0.5}


def poppy_profile(case, radii):
    import astropy.units as u
    import poppy

    a, f = case["stop"]["radius_mm"], case["lens"]["focal_length_mm"]
    n = 1024
    window = 2.2 * a
    field, dx = stop_field(case, n, window)
    lam_m = case["beam_at_stop"]["wavelength_nm"] * 1e-9
    k = 2 * math.pi / lam_m
    element = poppy.ArrayOpticalElement(transmission=np.abs(field) / np.abs(field).max(), opd=np.angle(field) / k,
                                        pixelscale=dx * 1e-3 * u.m / u.pixel, name="stop field")
    system = poppy.OpticalSystem(pupil_diameter=window * 1e-3 * u.m, npix=n, oversample=2)
    system.add_pupil(element)
    pixel_mm = 0.001
    system.add_detector(pixelscale=pixel_mm / f * 206264.80624709636, fov_pixels=241, oversample=1)
    psf = system.calc_psf(wavelength=lam_m, normalize="first")
    image = psf[0].data
    scale = psf[0].header["PIXELSCL"] / 206264.80624709636 * f
    return radial_line(image, scale, radii), {"pupil_samples": n, "pupil_window_mm": window, "focal_dx_um": scale * 1e3}


def diffractio_profile(case, radii):
    """Diffractio chirp z-transform (Rayleigh-Sommerfeld kernel): absolute irradiance (W/mm^2) on a symmetric grid.

    The lens phase is applied at the stop plane: in the Fresnel approximation the 1 mm stop-to-lens gap does not
    change the back-focal-plane irradiance (see hankel_reference.py); LightPipes propagates the gap explicitly. The
    profile is read from the located intensity maximum: with diffractio 1.0.0 the CZT output of this grid is offset by
    one output sample from the requested coordinates and its samples are spaced (x_end - x_start) / N instead of
    (x_end - x_start) / (N - 1), so only the located peak irradiance is compared, not the radial profile.
    """
    from diffractio.scalar_fields_XY import Scalar_field_XY

    a = case["stop"]["radius_mm"]
    n = 1024
    window = 2.2 * a
    field, dx = stop_field(case, n, window)
    lam_um = case["beam_at_stop"]["wavelength_nm"] * 1e-3
    coords_um = (np.arange(n) - n // 2) * dx * 1e3
    at_lens = Scalar_field_XY(coords_um, coords_um, lam_um)
    k = 2 * math.pi / lam_um
    f_um = case["lens"]["focal_length_mm"] * 1e3
    xx, yy = np.meshgrid(coords_um, coords_um)
    at_lens.u = field * np.exp(-1j * k * (xx ** 2 + yy ** 2) / (2 * f_um))
    step_um = (radii[1] - radii[0]) * 1e3
    count = len(radii)
    out = np.arange(-count + 1, count) * step_um
    focal = at_lens.CZT(case["observation"]["distance_from_lens_mm"] * 1e3, out, out)
    image = np.abs(np.asarray(focal.u)) ** 2  # sqrt(W)/mm field -> W/mm^2
    row, col = np.unravel_index(int(np.argmax(image)), image.shape)
    return None, {"pupil_samples": n, "pupil_window_mm": window, "kernel": "Rayleigh-Sommerfeld (CZT)",
                    "stop_to_lens_gap": "not propagated (no effect on focal irradiance in the Fresnel approximation)",
                    "peak_W_per_mm2": float(image[row, col]), "peak_offset_samples": [int(row) - count + 1, int(col) - count + 1]}


def analyze_case(path):
    case = json.loads(Path(path).read_text(encoding="utf-8"))
    if case.get("schema") != HANDOFF_SCHEMA:
        raise ValueError(f"{path}: not a diffraction handoff file")
    beam, a = case["beam_at_stop"], case["stop"]["radius_mm"]
    if beam["m2_x"] != 1.0 or beam["m2_y"] != 1.0 or abs(beam["w_x_mm"] / beam["w_y_mm"] - 1.0) > 1e-9:
        raise ValueError("this reference needs a round TEM00 beam (M^2 = 1, w_x = w_y)")
    started = time.perf_counter()
    reference = HankelFocus(stop_radius_mm=a, w_mm=beam["w_x_mm"], r_mm=beam["r_x_mm"] or math.inf,
                            focal_length_mm=case["lens"]["focal_length_mm"], wavelength_nm=beam["wavelength_nm"],
                            medium_index=beam["medium_index"])
    p_t = beam["power_W"] * reference.transmitted_fraction
    rho = case["encircled_radius_mm"]
    ref_peak = p_t * reference.irradiance_per_watt(0.0)
    ref_ee = reference.encircled_fraction(rho)
    ref_profile = np.array([reference.irradiance_per_watt(r) for r in PROFILE_RADII_MM])
    ref_profile_rel = ref_profile / ref_profile[0]
    reference_seconds = time.perf_counter() - started

    lightpipes = []
    for n, window in LIGHTPIPES_GRIDS:
        started = time.perf_counter()
        image, _ = lightpipes_focus(case, n, window)
        dx = window / n
        peak = float(image[n // 2, n // 2])
        ee = encircled(image, dx, rho) / p_t
        lightpipes.append({"samples": n, "window_mm": window, "dx_um": dx * 1e3, "peak_W_per_mm2": peak,
                           "peak_rel_err": peak / ref_peak - 1.0, "encircled_fraction": ee, "encircled_abs_err": ee - ref_ee,
                           "grid_power_W": float(image.sum() * dx * dx),
                           "profile_max_abs_err": float(np.max(np.abs(
                               radial_line(image, dx, PROFILE_RADII_MM) / peak - ref_profile_rel))),
                           "seconds": round(time.perf_counter() - started, 3)})
    finest = lightpipes[-1]
    spread = lightpipes[-3:]
    convergence = {"settings": [[r["samples"], r["window_mm"]] for r in spread],
                   "peak_rel_spread": max(r["peak_W_per_mm2"] for r in spread) / min(r["peak_W_per_mm2"] for r in spread) - 1.0,
                   "encircled_abs_spread": max(r["encircled_fraction"] for r in spread) - min(r["encircled_fraction"] for r in spread)}

    cross = {}
    for name, function in (("prysm", prysm_profile), ("poppy", poppy_profile), ("diffractio", diffractio_profile)):
        started = time.perf_counter()
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                values, settings = function(case, PROFILE_RADII_MM)
        except ImportError as exc:
            cross[name] = {"available": False, "reason": f"cannot import: {exc}"}
            continue
        cross[name] = {"available": True, "settings": settings, "seconds": round(time.perf_counter() - started, 3)}
        if values is not None:
            values = np.asarray(values, dtype=float)
            cross[name]["profile_max_abs_err"] = float(np.max(np.abs(values / values[0] - ref_profile_rel)))
        if "peak_W_per_mm2" in settings:
            cross[name]["peak_rel_err"] = settings["peak_W_per_mm2"] / ref_peak - 1.0

    engine = case["engine_focus"]
    engine_cmp = {"status": engine["status"], "issues": engine["issues"], "gaussian_valid": engine.get("gaussian_valid")}
    if engine.get("power_W") is not None:
        engine_cmp["transmitted_power_rel_err"] = engine["power_W"] / p_t - 1.0
    if engine.get("gaussian_valid"):
        engine_peak = engine["peak_irradiance_W_per_mm2"]
        engine_ee = engine[f"encircled_power_W_{rho}mm"] / p_t
        engine_cmp.update({"peak_W_per_mm2": engine_peak, "peak_rel_err": engine_peak / ref_peak - 1.0,
                           "encircled_fraction": engine_ee, "encircled_abs_err": engine_ee - ref_ee})
        engine_cmp["diffraction_accurate"] = abs(engine_cmp["peak_rel_err"]) <= ENGINE_ACCURATE_REL
    else:
        engine_cmp["diffraction_accurate"] = None
        engine_cmp["note"] = "the Engine withholds the Gaussian focal state; use the external result"

    checks = {
        "lightpipes_peak_rel": abs(finest["peak_rel_err"]),
        "lightpipes_encircled_abs": abs(finest["encircled_abs_err"]),
        "convergence_peak_rel": convergence["peak_rel_spread"],
        "convergence_encircled_abs": convergence["encircled_abs_spread"],
        "lightpipes_profile_abs": finest["profile_max_abs_err"],
    }
    profiles = [c["profile_max_abs_err"] for c in cross.values() if "profile_max_abs_err" in c]
    if profiles:
        checks["cross_check_profile_abs"] = max(profiles)
    absolute = [abs(c["peak_rel_err"]) for c in cross.values() if "peak_rel_err" in c]
    if absolute:
        checks["cross_check_peak_rel"] = max(absolute)
    if "transmitted_power_rel_err" in engine_cmp:
        checks["engine_power_rel"] = abs(engine_cmp["transmitted_power_rel_err"])
    outcome = "WITHIN_TOLERANCE" if all(checks[k] <= TOLERANCES[k] for k in checks) else "OUT_OF_TOLERANCE"
    report = {
        "handoff": str(path), "comparison_status": outcome, "identity": case["identity"], "stop_radius_mm": a,
        "beam_at_stop": {k: beam[k] for k in ("w_x_mm", "r_x_mm", "power_W", "wavelength_nm")},
        "transmitted_fraction": reference.transmitted_fraction,
        "reference": {"method": "Hankel integral (scipy quad)", "peak_W_per_mm2": ref_peak, f"encircled_fraction_{rho}mm": ref_ee,
                      "profile_radii_mm": list(PROFILE_RADII_MM), "profile_W_per_mm2": (ref_profile * p_t).tolist(),
                      "seconds": round(reference_seconds, 2)},
        "lightpipes": lightpipes, "convergence": convergence, "cross_checks": cross, "engine": engine_cmp,
        "checks": {k: {"value": v, "tolerance": TOLERANCES[k]} for k, v in checks.items()},
    }
    return report


def plot(reports, path):
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None
    figure, axes = plt.subplots(1, len(reports), figsize=(4.2 * len(reports), 3.6), squeeze=False)
    for axis, report in zip(axes[0], reports):
        radii = np.array(report["reference"]["profile_radii_mm"]) * 1e3
        axis.plot(radii, report["reference"]["profile_W_per_mm2"], "k-", label="Hankel reference")
        engine = report["engine"]
        if engine.get("gaussian_valid"):
            w = math.sqrt(2 * report["beam_at_stop"]["power_W"] * report["transmitted_fraction"]
                          / (math.pi * engine["peak_W_per_mm2"]))
            axis.plot(radii, engine["peak_W_per_mm2"] * np.exp(-2 * (radii * 1e-3 / w) ** 2), "C1--",
                      label="Engine Gaussian")
        axis.set_title(f"stop radius {report['stop_radius_mm']:g} mm: {engine['status']}")
        axis.set_xlabel("focal-plane radius (um)")
        axis.set_ylabel("irradiance (W/mm^2)")
        axis.legend(fontsize=8)
    figure.tight_layout()
    figure.savefig(path, dpi=110)
    plt.close(figure)
    return str(path)


def run_example(directory):
    directory = Path(directory)
    packages = environment("numpy", "scipy", "LightPipes", "prysm", "poppy", "diffractio", "matplotlib")
    suffix = "_diffractio" if packages["packages"].get("diffractio") else ""
    report_name = f"external_report{suffix}.json"
    handoffs = sorted(directory.glob("*/handoff.json"))
    if not handoffs:
        raise ValueError(f"no */handoff.json under {directory}; run opensci_handoff.py first")
    reports = []
    for path in handoffs:
        report = analyze_case(path)
        write_json(path.parent / report_name, report)
        reports.append(report)
    reports.sort(key=lambda r: -r["stop_radius_mm"])
    figure = plot(reports, directory / f"focus_profiles{suffix}.png")
    summary = {
        "example": "interop/diffraction (external side)",
        "outcome": ("WITHIN_TOLERANCE" if all(r["comparison_status"] == "WITHIN_TOLERANCE" for r in reports)
                    else "OUT_OF_TOLERANCE"),
        "environment": packages,
        "cases": [{
            "stop_radius_mm": r["stop_radius_mm"], "comparison_status": r["comparison_status"],
            "engine_status": r["engine"]["status"],
            "engine_issues": r["engine"]["issues"], "reference_peak_W_per_mm2": r["reference"]["peak_W_per_mm2"],
            "engine_peak_rel_err": r["engine"].get("peak_rel_err"),
            "engine_diffraction_accurate": r["engine"]["diffraction_accurate"],
            "lightpipes_finest_peak_rel_err": r["lightpipes"][-1]["peak_rel_err"],
            "convergence_peak_rel_spread": r["convergence"]["peak_rel_spread"],
            "cross_check_profile_err": {k: v.get("profile_max_abs_err") for k, v in r["cross_checks"].items()},
            "cross_check_peak_rel_err": {k: v["peak_rel_err"] for k, v in r["cross_checks"].items() if "peak_rel_err" in v},
            "report": str(Path(r["handoff"]).parent / report_name),
        } for r in reports],
        "figure": figure,
    }
    write_json(directory / f"external_summary{suffix}.json", summary)
    return summary


def main(argv=None):
    def workflow():
        parser = ArgumentParser(description=__doc__.split("\n\n")[0])
        parser.add_argument("directory", type=Path, help="directory written by opensci_handoff.py")
        args = parser.parse_args(argv)
        return run_example(args.directory)

    return emit(workflow)


if __name__ == "__main__":
    raise SystemExit(main())
