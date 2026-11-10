"""Design a circular flat top and test a fixed mask with changed illumination."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.metadata
import json
from pathlib import Path
import time

import numpy as np
from scipy.fft import fft2, fftfreq, fftshift, ifft2, ifftshift
from scipy.integrate import cumulative_trapezoid
from scipy.ndimage import map_coordinates

HERE = Path(__file__).resolve().parent


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def ft(field):
    return fftshift(fft2(ifftshift(field), norm="ortho"))


def ift(field):
    return fftshift(ifft2(ifftshift(field), norm="ortho"))


def input_field(handoff, n, window_mm):
    beam = handoff["beams"][0]
    if not beam["validity"]["tem00_field_applicable"]:
        raise ValueError("The illumination handoff must contain an unclipped TEM00 field.")
    g = beam["gaussian"]
    wavelength = beam["wavelength_nm"] * 1e-6 / beam["medium_index"]
    x = (np.arange(n) - n // 2) * window_mm / n
    xx, yy = np.meshgrid(x, x)
    wx, wy = g["beam_radius_x_mm"], g["beam_radius_y_mm"]
    field = np.sqrt(2 * beam["power_W"] / (np.pi * wx * wy)) * np.exp(-xx**2 / wx**2 - yy**2 / wy**2)
    field = field.astype(complex)
    for coord, axis in [(xx, "x"), (yy, "y")]:
        radius = g[f"curvature_radius_{axis}_mm"]
        if radius is not None:
            field *= np.exp(1j * np.pi * coord**2 / (wavelength * radius))
    # The handoff plane is 1 mm before the phase mask and ideal Fourier lens.
    frequency = fftfreq(n, window_mm / n)
    transfer = np.exp(-1j * np.pi * wavelength * (frequency[:, None]**2 + frequency[None, :]**2))
    return x, ifft2(fft2(field) * transfer), wavelength


def field_metrics(field, focal_x, radius_mm):
    xx, yy = np.meshgrid(focal_x, focal_x)
    r = np.hypot(xx, yy)
    intensity = abs(field)**2
    core = r < 0.75 * radius_mm
    target = np.exp(-2 * (r / radius_mm)**16)
    target_region = target > 0.1 * target.max()
    return {
        "core_cv": float(np.std(intensity[core]) / np.mean(intensity[core])),
        "target_region_power_fraction": float(intensity[target_region].sum() / intensity.sum()),
        "power_W": float(intensity.sum() * (focal_x[1] - focal_x[0])**2),
    }


def engine_run(project, directory):
    from os_optics.local import load_project, run_optical_project
    from os_optics.results import load_run

    run_optical_project(load_project(project), output_dir=str(directory))
    results = load_run(directory)
    description, targets = results.describe(), results.targets()
    if not description["outcome"]["execution"]["complete"]:
        raise AssertionError("The native optical calculation did not complete.")
    if targets["engine_status"] != "PASS" or any(t["assessment"] != "MET" for t in targets["targets"]):
        raise AssertionError("The native illumination targets were not met.")
    return results.handoff("phase_input"), description, targets


def check_illumination(handoff, source_waist_mm):
    """Independent q propagation through f=-40 mm and f=60 mm lenses."""
    lam = handoff["beams"][0]["wavelength_nm"] * 1e-6
    q = 1j * np.pi * source_waist_mm**2 / lam + 20
    q = 1 / (1 / q + 1 / 40) + 20
    q = 1 / (1 / q - 1 / 60) + 15
    reference = np.sqrt(-lam / (np.pi * (1 / q).imag))
    measured = handoff["beams"][0]["gaussian"]["beam_radius_x_mm"]
    error = abs(measured / reference - 1)
    if error > 1e-10:
        raise AssertionError(f"Illumination differs from the ABCD reference: {error}")
    return float(error)


def compute(args):
    start = time.perf_counter()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if (out / "science.npz").exists() or (out / "native-default").exists():
        raise ValueError("Use a new output directory for a new calculation, or --redraw for saved results.")
    project = json.loads((HERE / "project.json").read_text(encoding="utf-8"))
    variant = copy.deepcopy(project)
    source = next(c for c in variant["components"] if c["optical_role"] == "source")
    source["source_model"]["waist_radius_mm"] *= args.width_scale
    for target in variant["targets"]:
        if target["target_id"] == "illumination_radius":
            target["minimum"] *= args.width_scale
            target["maximum"] *= args.width_scale
    handoff, identity, targets = engine_run(project, out / "native-default")
    varied_handoff, varied_identity, varied_targets = engine_run(variant, out / "native-width-variant")
    write_json(out / "handoff-default.json", handoff)
    write_json(out / "handoff-width-variant.json", varied_handoff)
    n, window, radius, focal = args.grid, 4.608, args.radius_mm, 100.0
    x, incident, lam = input_field(handoff, n, window)
    xx, yy = np.meshgrid(x, x)
    r = np.hypot(xx, yy)
    dx = window / n
    focal_x = fftshift(fftfreq(n, dx)) * lam * focal
    fx, fy = np.meshgrid(focal_x, focal_x)
    target = np.exp(-2 * (np.hypot(fx, fy) / radius)**16)
    target /= target.sum()
    amplitude = abs(incident)
    energy = (amplitude**2).sum()
    w = handoff["beams"][0]["gaussian"]["beam_radius_x_mm"]
    # Radial energy transport gives a smooth stationary-phase starting point.
    pupil_r = np.linspace(0, 3 * w, 8193)
    output_r = np.linspace(0, radius * 5 / 3, 8193)
    cdf = cumulative_trapezoid(output_r * np.exp(-2 * (output_r / radius)**16), output_r, initial=0)
    cdf /= cdf[-1]
    mapping = np.interp(1 - np.exp(-2 * pupil_r**2 / w**2), cdf, output_r)
    phase_r = cumulative_trapezoid(2 * np.pi * mapping / (lam * focal), pupil_r, initial=0)
    phase = np.interp(r, pupil_r, phase_r)
    initial_phase = phase.copy()
    history = []
    for _ in range(args.iterations):
        spectrum = ft(amplitude * np.exp(1j * phase))
        intensity = abs(spectrum)**2 / energy
        history.append(float(np.sum((intensity - target)**2) / np.sum(target**2)))
        phase = np.angle(ift(np.sqrt(target * energy) * np.exp(1j * np.angle(spectrum))))
    mask = phase - np.angle(incident)
    factor = n * dx**2 / (lam * focal)
    quadratic = np.exp(1j * np.pi * (fx**2 + fy**2) / (lam * focal))
    output = ft(incident * np.exp(1j * mask)) * factor * quadratic
    initial_output = ft(amplitude * np.exp(1j * initial_phase)) * factor * quadratic
    _, variant_input, _ = input_field(varied_handoff, n, window)
    variant_output = ft(variant_input * np.exp(1j * mask)) * factor * quadratic
    nominal = field_metrics(output, focal_x, radius)
    changed = field_metrics(variant_output, focal_x, radius)
    initial = field_metrics(initial_output, focal_x, radius)
    # Direct quadrature independently checks Fourier indexing and absolute amplitude.
    quadrature_errors = []
    modulated = incident * np.exp(1j * mask)
    for ix in range(n // 2 - 5, n // 2 + 6):
        direct = np.sum(modulated * np.exp(-2j * np.pi * xx * focal_x[ix] / (lam * focal))) * dx**2 / (lam * focal)
        calculated = output[n // 2, ix] / quadratic[n // 2, ix]
        quadrature_errors.append(abs(direct - calculated) / max(abs(calculated), 1e-12))
    checks = {
        "abcd_default_radius_relative_error": check_illumination(handoff, 0.5),
        "abcd_variant_radius_relative_error": check_illumination(varied_handoff, 0.5 * args.width_scale),
        "fourier_quadrature_max_complex_relative_error": float(max(quadrature_errors)),
        "default_energy_relative_error": abs(nominal["power_W"] / (energy * dx**2) - 1),
        "variant_energy_relative_error": abs(changed["power_W"] / ((abs(variant_input)**2).sum() * dx**2) - 1),
    }
    if max(checks.values()) > 1e-9:
        raise AssertionError(f"Independent checks failed: {checks}")
    # Evaluate the same physical mask at half the input pitch, keeping the
    # window and focal-plane frequency pitch fixed. Interpolate a phasor,
    # never the discontinuous wrapped angle.
    fine_n = 2 * n
    _, fine_incident, _ = input_field(handoff, fine_n, window)
    coords = np.meshgrid(np.arange(fine_n) / 2, np.arange(fine_n) / 2, indexing="ij")
    phasor = np.exp(1j * mask)
    fine_phasor = (map_coordinates(phasor.real, coords, order=3, mode="grid-wrap")
                   + 1j * map_coordinates(phasor.imag, coords, order=3, mode="grid-wrap"))
    fine_phasor /= np.maximum(abs(fine_phasor), 1e-30)
    fine_spectrum = ft(fine_incident * fine_phasor) * fine_n * (dx / 2)**2 / (lam * focal)
    fine_crop = fine_spectrum[n // 2:3 * n // 2, n // 2:3 * n // 2]
    comparison_roi = np.hypot(fx, fy) < radius * 1.25
    sampling_error = float(np.linalg.norm((abs(fine_crop)**2 - abs(output)**2)[comparison_roi])
                           / np.linalg.norm(abs(fine_crop[comparison_roi])**2))
    checks["fixed_mask_half_pitch_intensity_relative_l2"] = sampling_error
    if sampling_error > 0.01:
        raise AssertionError(f"Fixed-mask sampling discrepancy exceeds 1%: {sampling_error}")
    if args.grid == 384 and args.radius_mm == 0.42 and args.iterations == 251:
        if nominal["core_cv"] > 0.02 or nominal["target_region_power_fraction"] < 0.99:
            raise AssertionError(f"Default flat-top quality changed: {nominal}")
    config = {
        "grid": n, "window_mm": window, "target_radius_mm": radius,
        "target_intensity": "exp(-2*(r/target_radius_mm)^16)",
        "core_roi": "r < 0.75 * target_radius_mm", "efficiency_roi": "target > 0.1 * max(target)",
        "iterations": args.iterations, "input_width_scale": args.width_scale,
        "focal_length_mm": focal, "wavelength_mm": lam,
        "field_unit": "sqrt(W/mm^2)", "intensity_unit": "W/mm^2", "array_axes": "y,x",
        "phase_convention": "exp(-i omega t), carrier exp(ikz) factored out",
        "phase_mask_sha256": hashlib.sha256(mask.astype("<f8").tobytes()).hexdigest(),
        "sampling_check": "Cubic complex-phasor interpolation to half input pitch; fixed window; focal r < 1.25 * target radius",
    }
    np.savez_compressed(out / "science.npz", input_x_mm=x, focal_x_mm=focal_x,
                        input_complex=incident, variant_input_complex=variant_input,
                        phase_mask_rad=mask, target_unit_sum=target,
                        output_complex=output, variant_output_complex=variant_output,
                        initial_output_complex=initial_output, loss_history=np.asarray(history))
    write_json(out / "configuration.json", config)
    result = {
        "default": nominal, "width_variant": changed, "smooth_initialization": initial,
        "checks": checks, "engine_default": identity, "engine_variant": varied_identity,
        "initial_target_relative_squared_error": history[0],
        "final_target_relative_squared_error": float(np.sum((abs(output / (factor * quadratic))**2 / energy - target)**2) / np.sum(target**2)),
        "engine_targets_default": targets, "engine_targets_variant": varied_targets,
        "dependencies": {p: importlib.metadata.version(p) for p in ["os-optics", "os-optics-kit", "numpy", "scipy", "matplotlib"]},
        "compute_seconds": time.perf_counter() - start,
    }
    write_json(out / "results.json", result)
    return result


def redraw(out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    with np.load(out / "science.npz") as data:
        x = data["focal_x_mm"]
        nominal = abs(data["output_complex"])**2
        changed = abs(data["variant_output_complex"])**2
        initial = abs(data["initial_output_complex"])**2
        config = json.loads((out / "configuration.json").read_text(encoding="utf-8"))
        result = json.loads((out / "results.json").read_text(encoding="utf-8"))
        fig, axes = plt.subplots(1, 3, figsize=(14, 3.5), layout="constrained")
        fig.get_layout_engine().set(wspace=0.08)
        extent = [x[0], x[-1], x[0], x[-1]]
        limit = config["target_radius_mm"] * 1.5
        vmax = max(nominal.max(), changed.max())
        for ax, intensity, label, key in zip(axes[:2], [nominal, changed],
                ["Designed illumination", f"Input waist x {config['input_width_scale']:g}"],
                ["default", "width_variant"]):
            im = ax.imshow(intensity, origin="lower", extent=extent, cmap="inferno", vmin=0, vmax=vmax)
            ax.set(xlim=(-limit, limit), ylim=(-limit, limit), xlabel="x (mm)", ylabel="y (mm)",
                   title=f"{label}\nCore CV = {result[key]['core_cv']:.4f}")
        fig.colorbar(im, ax=list(axes[:2]), label="Irradiance (W/mm2)", shrink=0.8)
        axes[2].plot(x, initial[len(x)//2], ":", label="Smooth initialization")
        axes[2].plot(x, nominal[len(x)//2], label="Optimized")
        axes[2].plot(x, changed[len(x)//2], "--", label="Changed input, fixed mask")
        axes[2].set(xlim=(-limit, limit), xlabel="x (mm)", title="Focal-plane center line (W/mm2)")
        axes[2].legend(fontsize=8)
        fig.savefig(out / "preview.png", dpi=150)
        plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--width-scale", type=float, default=0.9)
    parser.add_argument("--radius-mm", type=float, default=0.42)
    parser.add_argument("--grid", type=int, default=384)
    parser.add_argument("--iterations", type=int, default=251)
    parser.add_argument("--redraw", action="store_true", help="Plot saved arrays without running the engine or optimization")
    args = parser.parse_args()
    if not (0.8 <= args.width_scale <= 1.2 and 0.3 <= args.radius_mm <= 0.55):
        parser.error("Use width scale 0.8 to 1.2 and target radius 0.3 to 0.55 mm.")
    if args.grid < 256 or args.grid % 2 or args.iterations < 1:
        parser.error("Use an even grid >=256 and at least one iteration.")
    payload = {"status": "PASS", "mode": "redraw" if args.redraw else "compute"}
    if not args.redraw:
        result = compute(args)
        payload.update({k: result[k] for k in ["default", "width_variant", "checks", "compute_seconds"]})
    redraw(args.out)
    payload.update({"preview": str(args.out / "preview.png"), "data": str(args.out / "science.npz")})
    print(json.dumps(payload))


if __name__ == "__main__":
    main()
