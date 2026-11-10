"""Cylindrical light-sheet thickness/range study; outputs stay in --out."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import subprocess
import sys
import time

import numpy as np


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def pose(z):
    return {"position_mm": [0, 0, z], "quaternion_wxyz": [1, 0, 0, 0]}


def build(config, out):
    """Use the public native builder for the real two-interface prescription."""
    f = config["focal_length_mm"]
    params = {
        "focal_length_mm": f, "design_wavelength_nm": 488,
        "material": {"constant_index": "1.52", "valid_band_nm": [450, 650]},
        "diameter_mm": 12, "clear_aperture_diameter_mm": 10,
        "form": "PLANO_EXIT", "cylinder_axis": "X", "center_thickness_mm": 2,
        "ar_coating": {"reflectance": "0.005", "valid_aoi_deg": [0, 20]}, "model_id": "CYL",
    }
    write(out / "lens-parameters.json", params)
    command = [sys.executable, "-m", "os_optics", "native", "build", "lens.singlet",
               "--params-json", str(out / "lens-parameters.json"), "-o", str(out / "lens.json")]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=True)
    (out / "native-build.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    native = json.loads((out / "lens.json").read_text(encoding="utf-8"))
    lens = native["component_template"]
    lens.update(instance_id="CYL", nominal_pose=pose(20))
    source = {
        "instance_id": "LASER", "optical_role": "source", "nominal_pose": pose(0),
        "custom_part": {"custom_part_key": "LASER", "description": "488 nm Gaussian source"},
        "source_model": {"model_kind": "GAUSSIAN_BEAM", "wavelength_nm": 488,
                         "power_w": 0.01, "waist_radius_mm": config["input_waist_mm"],
                         "waist_position_mm": 0, "m2": 1,
                         "polarization": {"kind": "LINEAR", "angle_deg": 0}},
    }
    observations = [{"observation_id": name, "pose": pose(z), "absorbing": False,
                     "aperture": {"shape": "circle", "radius_mm": 10}, "medium": "air"}
                    for name, z in [("after", 23), ("nominal-focus", 20 + f)]]
    targets = [
        {"target_id": "thin-waist", "metric": "waist_radius_y_mm", "observation_id": "after",
         "maximum": config["max_thickness_fwhm_um"] / (1000 * math.sqrt(2 * math.log(2))), "unit": "mm"},
        {"target_id": "sheet-range", "metric": "rayleigh_range_y_mm", "observation_id": "after",
         "minimum": config["min_range_mm"] / 2, "unit": "mm"},
    ]
    return {"schema": "opensci.project", "project_schema_version": "2.0",
            "components": [source, lens], "optical_models": [native["optical_model"]],
            "model_materials": native["model_materials"], "model_coatings": native["model_coatings"],
            "observations": observations, "targets": targets}


def calculate(config, out):
    from os_optics.local import run_optical_project, verify_run_artifacts
    from os_optics.results import load_run

    started = time.perf_counter()
    project = build(config, out)
    write(out / "configuration.json", config)
    write(out / "input.json", project)
    run_optical_project(project, output_dir=out / "run")
    run = load_run(out / "run")
    handoff = run.handoff("after")
    write(out / "handoff.json", handoff)
    write(out / "targets.json", run.targets())
    identity = run.describe()
    write(out / "engine-description.json", identity)
    problems = verify_run_artifacts(out / "run")
    if problems:
        raise RuntimeError(f"Engine artifact integrity problems: {problems}")
    beam = handoff["beams"][0]
    gaussian = beam["gaussian"]
    qx, qy = (complex(*gaussian[f"q_{axis}_mm"]) for axis in ("x", "y"))
    # Independent ABCD: refraction into n, translation through glass,
    # refraction out at the plane surface, then 1 mm of air to the observation.
    n = 1.52
    wavelength_mm = 488e-6
    source_q = 20 + 1j * math.pi * config["input_waist_mm"] ** 2 / wavelength_mm
    surfaces = project["optical_models"][0]["model"]["surfaces"]
    radius = float(surfaces[0]["radius_mm"])
    thickness = float(surfaces[1]["vertex_z_mm"]) - float(surfaces[0]["vertex_z_mm"])
    reference_y = (n * source_q / (1 + (1 - n) * source_q / radius) + thickness) / n + 1
    reference_x = source_q + thickness / n + 1
    errors = {"qx_relative_error": abs(qx - reference_x) / abs(reference_x),
              "qy_relative_error": abs(qy - reference_y) / abs(reference_y),
              "power_relative_error": abs(beam["power_W"] / (0.01 * 0.995**2) - 1)}
    if max(errors.values()) > 1e-9:
        raise RuntimeError(f"Independent reference mismatch: {errors}")
    waist_y = gaussian["waist_radius_y_mm"]
    zr = gaussian["rayleigh_range_y_mm"]
    focus = 23 - gaussian["waist_distance_y_mm"]
    dz = np.linspace(-2 * zr, 2 * zr, 241)
    z = focus + dz
    qx_z, qy_z = qx + z - 23, qy + z - 23
    wx = np.sqrt(wavelength_mm / np.pi * abs(qx_z)**2 / qx_z.imag)
    wy = np.sqrt(wavelength_mm / np.pi * abs(qy_z)**2 / qy_z.imag)
    y = np.linspace(-5 * waist_y, 5 * waist_y, 401)
    # External separable TEM00 intensity from the native q and power.
    longitudinal = 2 * beam["power_W"] / (np.pi * wx[:, None] * wy[:, None]) * np.exp(
        -2 * (y[None, :] / wy[:, None])**2)
    marginal = np.sqrt(2 / np.pi) * beam["power_W"] / waist_y * np.exp(-2 * (y / waist_y)**2)
    power_integral = float(np.trapezoid(marginal, y))
    errors["waist_marginal_power_relative_error"] = abs(power_integral / beam["power_W"] - 1)
    if errors["waist_marginal_power_relative_error"] > 1e-8:
        raise RuntimeError("Waist marginal integration failed")
    np.savez_compressed(out / "profiles.npz", z_mm=z, z_from_waist_mm=dz, y_mm=y,
                        wide_radius_mm=wx, thin_radius_mm=wy,
                        intensity_x0_W_per_mm2=longitudinal, waist_marginal_W_per_mm=marginal)
    metrics = {"thickness_fwhm_um": 1000 * math.sqrt(2 * math.log(2)) * waist_y,
               "range_sqrt2_mm": 2 * zr, "waist_radius_y_mm": waist_y,
               "focus_z_mm": focus, "power_W": beam["power_W"],
               "wide_fwhm_at_focus_mm": math.sqrt(2 * math.log(2)) * float(wx[len(wx)//2]),
               "checks": errors, "engine_identity": identity["run"],
               "runtime_calculation_s": time.perf_counter() - started,
               "dependencies": {name: importlib.metadata.version(name)
                                for name in ["os-optics", "numpy", "matplotlib"]},
               "python": platform.python_version()}
    write(out / "metrics.json", metrics)
    return metrics


def preview(out):
    """Plot the saved case-specific longitudinal map and width profile only."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    arrays = np.load(out / "profiles.npz")
    metrics = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    z, y = arrays["z_from_waist_mm"], arrays["y_mm"] * 1000
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6), layout="constrained")
    im = axes[0].imshow(arrays["intensity_x0_W_per_mm2"].T, origin="lower", aspect="auto",
                        extent=[z[0], z[-1], y[0], y[-1]], cmap="inferno")
    axes[0].set(xlabel="Distance from waist (mm)", ylabel="Thin axis y (um)", title="Gaussian light sheet, x = 0")
    fig.colorbar(im, ax=axes[0], label="W/mm²")
    axes[1].plot(z, arrays["thin_radius_mm"] * math.sqrt(2 * math.log(2)) * 1000)
    axes[1].axhline(metrics["thickness_fwhm_um"] * math.sqrt(2), color="gray", linestyle="--")
    axes[1].axvspan(-metrics["range_sqrt2_mm"] / 2, metrics["range_sqrt2_mm"] / 2, alpha=.15)
    axes[1].set(xlabel="Distance from waist (mm)", ylabel="Thickness FWHM (um)",
                title=f"{metrics['thickness_fwhm_um']:.3f} um waist; {metrics['range_sqrt2_mm']:.3f} mm range")
    fig.savefig(out / "preview.png", dpi=150)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--focal-length-mm", type=float, default=50)
    parser.add_argument("--input-waist-mm", type=float, default=1)
    parser.add_argument("--plot-only", action="store_true", help="Redraw saved profiles without running the engine")
    args = parser.parse_args()
    if not (25 <= args.focal_length_mm <= 110 and .5 <= args.input_waist_mm <= 1.5):
        parser.error("Use focal length 25–110 mm and input waist 0.5–1.5 mm")
    if not args.plot_only and args.out.exists() and any(args.out.iterdir()):
        parser.error("Output directory is not empty; choose a new --out or use --plot-only")
    args.out.mkdir(parents=True, exist_ok=True)
    if not args.plot_only:
        metrics = calculate({"focal_length_mm": args.focal_length_mm, "input_waist_mm": args.input_waist_mm,
                             "max_thickness_fwhm_um": 10, "min_range_mm": .45}, args.out)
    else:
        metrics = json.loads((args.out / "metrics.json").read_text(encoding="utf-8"))
    preview(args.out)
    passed = metrics["thickness_fwhm_um"] <= 10 and metrics["range_sqrt2_mm"] >= .45
    print(json.dumps({"status": "PASS" if passed else "FAIL", "output": str(args.out), "thickness_fwhm_um": metrics["thickness_fwhm_um"],
                      "range_sqrt2_mm": metrics["range_sqrt2_mm"]}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
