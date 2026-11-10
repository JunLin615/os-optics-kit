"""Intensity-only correction of five phase modes with an uncontrolled spherical mode."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import time

import numpy as np
from scipy.optimize import minimize


def write_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def modes(x, y, aperture_mm=1.5):
    r = np.hypot(x, y) / aperture_mm
    theta = np.arctan2(y, x)
    return np.array([
        np.sqrt(3) * (2 * r**2 - 1),
        np.sqrt(6) * r**2 * np.cos(2 * theta),
        np.sqrt(6) * r**2 * np.sin(2 * theta),
        np.sqrt(8) * (3 * r**3 - 2 * r) * np.cos(theta),
        np.sqrt(8) * (3 * r**3 - 2 * r) * np.sin(theta),
        np.sqrt(5) * (6 * r**4 - 6 * r**2 + 1),
    ])


def pupil(n, radius_mm):
    x = (np.arange(n) - n // 2) * 6.0 / n
    xx, yy = np.meshgrid(x, x)
    aperture = np.hypot(xx, yy) <= 1.5
    amplitude = np.exp(-(xx**2 + yy**2) / radius_mm**2) * aperture
    return x, amplitude, modes(xx, yy)


def make_sensor(amplitude, basis, disturbance):
    """The optimizer receives this scalar sensor, without the disturbance coefficients."""
    fixed_phase = np.einsum("i,ijk->jk", disturbance, basis)
    denominator = amplitude.sum() ** 2

    def sensor(correction):
        phase = fixed_phase + np.einsum("i,ijk->jk", correction, basis[:5])
        return float(abs(np.sum(amplitude * np.exp(2j * np.pi * phase))) ** 2 / denominator)

    return sensor


def optimize(sensor, bound):
    history = []

    def objective(correction):
        value = sensor(correction)
        history.append([*correction, value])
        return -value

    objective(np.zeros(5))
    result = minimize(objective, np.zeros(5), method="Powell", bounds=[(-bound, bound)] * 5,
                      options={"maxiter":65, "maxfev":4000, "xtol":2e-5, "ftol":1e-8})
    return result, np.asarray(history)


def polar_reference(radius_mm, coefficients):
    """Independent polar Gaussian quadrature of the coherent pupil integral."""
    nodes, weights = np.polynomial.legendre.leggauss(160)
    radius = 0.75 * (nodes + 1)
    radial_weights = weights * 0.75 * radius
    theta = np.arange(256) * 2 * np.pi / 256
    xx = radius[:, None] * np.cos(theta)
    yy = radius[:, None] * np.sin(theta)
    phase = np.einsum("i,ijk->jk", coefficients, modes(xx, yy))
    amplitude = np.exp(-radius[:, None]**2 / radius_mm**2)
    integral = np.sum(radial_weights[:, None] * amplitude * np.exp(2j * np.pi * phase))
    ideal = np.sum(radial_weights * np.exp(-radius**2 / radius_mm**2)) * len(theta)
    return float(abs(integral / ideal)**2)


def run(out, spherical_waves=0.055, bound_waves=0.6):
    from os_optics.local import run_optical_project, verify_run_artifacts
    from os_optics.results import load_run

    started = time.perf_counter()
    out.mkdir(parents=True, exist_ok=False)
    project_path = Path(__file__).with_name("project.json")
    project = json.loads(project_path.read_text(encoding="utf-8"))
    run_optical_project(project, output_dir=out / "engine")
    result = load_run(out / "engine")
    handoff = result.handoff("modulator")
    write_json(out / "handoff.json", handoff)
    beam = handoff["beams"][0]
    if not beam["validity"]["tem00_field_applicable"]:
        raise ValueError("The modulator handoff must supply a valid TEM00 field")
    gaussian = beam["gaussian"]
    radius = float(gaussian["beam_radius_x_mm"])
    if abs(radius - gaussian["beam_radius_y_mm"]) > 1e-9:
        raise ValueError("This pupil construction requires a circular incident beam")
    lam_mm = float(beam["wavelength_nm"]) * 1e-6
    incident_power = float(beam["power_W"])
    power = incident_power * (1 - math.exp(-2 * 1.5**2 / radius**2))
    # Independent paraxial q propagation: source -> -40 mm lens -> +80 mm lens -> pupil.
    q = 20 + 1j * np.pi * 0.65**2 / lam_mm
    q = 1 / (1 / q + 1 / 40)
    q += 40
    q = 1 / (1 / q - 1 / 80)
    q += 59
    engine_q = gaussian["waist_distance_x_mm"] + 1j * gaussian["rayleigh_range_x_mm"]
    q_relative_error = float(abs(q - engine_q) / abs(engine_q))

    disturbance = np.array([0.24, 0.17, -0.11, 0.12, -0.09, spherical_waves])
    _, small_amplitude, small_basis = pupil(160, radius)
    sensor = make_sensor(small_amplitude, small_basis, disturbance)
    optimum, history = optimize(sensor, bound_waves)
    correction = optimum.x
    x, amplitude, basis = pupil(512, radius)
    dx = x[1] - x[0]
    amplitude *= math.sqrt(power / (np.sum(amplitude**2) * dx**2))
    initial_phase = np.einsum("i,ijk->jk", disturbance, basis)
    corrected_phase = initial_phase + np.einsum("i,ijk->jk", correction, basis[:5])
    phases = np.array([np.zeros_like(amplitude), initial_phase, corrected_phase])
    fields = amplitude[None] * np.exp(2j * np.pi * phases)
    # Zero padding samples the same Fourier integral more densely; it adds no pupil information.
    pad = 768
    padded = np.pad(fields, ((0, 0), (pad, pad), (pad, pad)))
    focal = np.fft.fftshift(np.fft.fft2(np.fft.ifftshift(padded, axes=(-2, -1))), axes=(-2, -1))
    focal *= dx**2 / (lam_mm * 120)
    intensity = abs(focal)**2
    df = lam_mm * 120 / (2048 * dx)
    focal_x = (np.arange(2048) - 1024) * df
    ratios = intensity[:, 1024, 1024] / intensity[0, 1024, 1024]
    direct_ratios = abs(fields.sum(axis=(1, 2)))**2 / abs(fields[0].sum())**2
    coefficients = disturbance.copy()
    coefficients[:5] += correction
    reference = polar_reference(radius, coefficients)
    _, finer_amplitude, finer_basis = pupil(768, radius)
    finer_ratio = make_sensor(finer_amplitude, finer_basis, disturbance)(correction)
    checks = {
        "engine_artifact_problems": verify_run_artifacts(out / "engine"),
        "abcd_q_relative_error": q_relative_error,
        "parseval_relative_error": float(np.max(abs(intensity.sum(axis=(1, 2)) * df**2 / power - 1))),
        "fft_center_vs_coherent_sum_absolute_error": float(np.max(abs(ratios - direct_ratios))),
        "corrected_512_vs_768_absolute_difference": abs(float(ratios[2]) - finer_ratio),
        "corrected_polar_reference": reference,
        "corrected_polar_absolute_difference": abs(float(ratios[2]) - reference),
        "actuators_within_bounds": bool(np.max(abs(correction)) <= bound_waves + 1e-12),
        "onaxis_intensity_improved": bool(ratios[2] > ratios[1]),
    }
    checks["passed"] = bool(not checks["engine_artifact_problems"] and q_relative_error < 1e-9
                            and checks["parseval_relative_error"] < 1e-10
                            and checks["fft_center_vs_coherent_sum_absolute_error"] < 1e-10
                            and checks["corrected_512_vs_768_absolute_difference"] < 0.001
                            and checks["corrected_polar_absolute_difference"] < 0.001
                            and checks["actuators_within_bounds"] and checks["onaxis_intensity_improved"]
                            and optimum.success)
    crop = abs(focal_x) <= 0.08
    np.savez_compressed(out / "science.npz", pupil_x_mm=x, pupil_fields_sqrt_W_per_mm=fields,
                        phase_waves=phases, focal_x_mm=focal_x[crop],
                        focal_intensity_W_per_mm2=intensity[:, crop][:, :, crop],
                        ideal_peak_W_per_mm2=intensity[0, 1024, 1024],
                        history=history, correction_waves=correction, disturbance_waves=disturbance)
    config = {"spherical_waves": spherical_waves, "bound_waves": bound_waves,
              "optimization_grid": 160, "evaluation_grid": 512, "focal_fft_grid": 2048,
              "pupil_window_mm": 6, "aperture_radius_mm": 1.5, "external_focus_mm": 120,
              "fixed_curvature_compensation": True,
              "project_sha256": hashlib.sha256(project_path.read_bytes()).hexdigest()}
    write_json(out / "config.json", config)
    metrics = {"onaxis_intensity_ratio": dict(zip(["ideal", "disturbed", "corrected"], ratios.tolist())),
               "incident_power_W": incident_power, "transmitted_power_W": power,
               "pupil_radius_mm": radius, "correction_waves": correction.tolist(),
               "sensor_evaluations": len(history), "optimizer_success": bool(optimum.success),
               "engine_targets": result.targets(), "checks": checks,
               "versions": {name: importlib.metadata.version(name) for name in
                            ["os-optics", "numpy", "scipy", "matplotlib"]},
               "compute_runtime_seconds": time.perf_counter() - started}
    write_json(out / "metrics.json", metrics)
    render(out)
    metrics["total_runtime_seconds"] = time.perf_counter() - started
    write_json(out / "metrics.json", metrics)
    print(json.dumps({"out": str(out), "status": "PASS" if checks["passed"] else "FAIL",
                      "ratios": metrics["onaxis_intensity_ratio"],
                      "checks_passed": checks["passed"], "runtime_seconds": metrics["total_runtime_seconds"]}))
    return 0 if checks["passed"] else 1


def render(out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    with np.load(out / "science.npz") as data:
        values = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
        images = data["focal_intensity_W_per_mm2"] / data["ideal_peak_W_per_mm2"]
        extent = [data["focal_x_mm"][0] * 1000, data["focal_x_mm"][-1] * 1000] * 2
        fig, axes = plt.subplots(1, 3, figsize=(10.5, 3.5), layout="constrained")
        for axis, image, label in zip(axes, images, ("ideal", "disturbed", "corrected")):
            artist = axis.imshow(image, origin="lower", extent=extent, vmin=0, vmax=1, cmap="inferno")
            axis.set(xlabel="Focal x (um)", ylabel="Focal y (um)",
                     title=f"{label.capitalize()}\nOn-axis ratio {values['onaxis_intensity_ratio'][label]:.3f}")
        fig.colorbar(artist, ax=list(axes), label="Intensity / ideal peak", shrink=0.8)
        fig.savefig(out / "preview.png", dpi=150)
        plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="New output directory")
    parser.add_argument("--spherical-waves", type=float, default=0.055, help="Uncontrolled mode coefficient (waves)")
    parser.add_argument("--bound-waves", type=float, default=0.6, help="Symmetric actuator bound (waves)")
    parser.add_argument("--redraw-only", action="store_true", help="Draw saved science.npz without running solvers")
    args = parser.parse_args()
    if args.redraw_only:
        render(args.out)
        return 0
    if not math.isfinite(args.spherical_waves) or not 0 <= args.spherical_waves <= 0.2:
        parser.error("--spherical-waves must be finite and between 0 and 0.2")
    if not math.isfinite(args.bound_waves) or not 0.01 <= args.bound_waves <= 0.6:
        parser.error("--bound-waves must be finite and between 0.01 and 0.6")
    return run(args.out, args.spherical_waves, args.bound_waves)


if __name__ == "__main__":
    raise SystemExit(main())
