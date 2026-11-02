"""OpenSci side of the quantum example (requires os-optics 0.10.0).

Builds small catalog-free Projects from native components (a thin 50:50 plate
splitter, flat mirrors, uncoated windows of a declared N-BK7 Sellmeier glass) and
exports each with the real command ``os-optics project network`` as an
``opensci-linear-optical-network/v1`` document:

* ``hom``: two 780 nm sources onto one splitter, two output planes D1 / D2;
* ``hom_delay_<d>``: the same with source B moved back by d mm (delay scan);
* ``hom_window_group``: a 3 mm window in arm A, source B moved back by the
  window's group-delay length (n_g - 1) t;
* ``hom_window_phase``: the same window, B moved back by the phase-delay length
  (n - 1) t only;
* ``mzi_t<t>``: a 532 nm two-input Mach-Zehnder with a window of thickness t in
  one arm (internal phase and Fresnel loss);
* ``refused_thin_lens``: an ideal thin lens in the HOM layout, which the export
  refuses (its phase is not modelled).

The classical sources only define spatial modes, wavelength and polarization of
the network; the photons, wavepackets and detectors come from ``quantum_inputs.json``.
The group indices used to place source B are an experimental choice made here, not
read from the export.

Usage: python examples/interop/quantum/build_layouts.py --out run-interop-quantum
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import subprocess
import sys

if __package__:
    from ..common import ArgumentParser, decimal_text, emit, pose, require_engine, write_json
    from .reference import N_BK7_SELLMEIER, group_index, sellmeier_index
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from common import ArgumentParser, decimal_text, emit, pose, require_engine, write_json
    from reference import N_BK7_SELLMEIER, group_index, sellmeier_index

INV = 1.0 / math.sqrt(2.0)
HEIGHT = 20.0
GLASS = {"sellmeier": {"b": [str(b) for b in N_BK7_SELLMEIER["b"]], "c_um2": [str(c) for c in N_BK7_SELLMEIER["c_um2"]]},
         "valid_band_nm": ["400", "1000"], "material_id": "N-BK7-DECLARED"}
DELAY_SCAN_MM = tuple(round(-0.3 + 0.05 * i, 2) for i in range(13))
MZI_THICKNESSES_MM = (0.0, 1.0, 1.0003)


class Layout:
    """A Project document assembled from native components and declared sources / planes."""

    def __init__(self):
        self.document = {"schema": "opensci.project", "project_schema_version": "2.0", "design_intent": "OPTICAL_SYSTEM",
                         "optical_models": [], "components": [], "observations": []}

    def source(self, instance_id, wavelength_nm, position, direction):
        # Local +X = design +Z, so polarization angle 0 is vertical (the network's V mode).
        self.document["components"].append({
            "instance_id": instance_id, "optical_role": "source",
            "custom_part": {"custom_part_key": f"laser-{instance_id.lower()}", "description": "alignment laser"},
            "nominal_pose": pose(position, direction),
            "source_model": {"model_kind": "GAUSSIAN_BEAM", "wavelength_nm": wavelength_nm, "power_w": 0.001,
                             "waist_radius_mm": 1.0, "waist_position_mm": 0.0, "m2": 1.0,
                             "polarization": {"kind": "LINEAR", "angle_deg": 0.0}}})

    def native(self, instance_id, class_id, parameters, position, z_axis):
        from os_optics.native_library import add_to_project, build_native_model

        build = build_native_model(class_id, dict(parameters, model_id=instance_id.lower()))
        placed = pose(position, z_axis)
        self.document = add_to_project(self.document, build, instance_id=instance_id,
                                       position_mm=placed["position_mm"], quaternion_wxyz=placed["quaternion_wxyz"])

    def plane(self, observation_id, position, normal):
        self.document["observations"].append({"observation_id": observation_id, "pose": pose(position, normal),
                                              "aperture": {"shape": "circle", "radius_mm": 10}})


def splitter(layout, instance_id, position, normal):
    layout.native(instance_id, "splitter.plate", {"diameter_mm": "25.4", "reflectance": "0.5", "transmittance": "0.5",
                                                  "valid_band_nm": ["400", "1000"]}, position, normal)


def window(layout, instance_id, thickness_mm, center, axis):
    first_face = [c - 0.5 * thickness_mm * a for c, a in zip(center, axis)]
    layout.native(instance_id, "plate.window", {"material": GLASS, "thickness_mm": decimal_text(thickness_mm),
                                                "diameter_mm": "25.4"}, first_face, axis)


def hom(delay_mm=0.0, window_mm=0.0):
    """Sources A (+x) and B (+y) meet on a 50:50 splitter at the origin; B starts ``delay_mm`` further back."""
    layout = Layout()
    layout.source("A", 780.0, (-50.0, 0.0, HEIGHT), (1, 0, 0))
    layout.source("B", 780.0, (0.0, -50.0 - delay_mm, HEIGHT), (0, 1, 0))
    splitter(layout, "BS", (0.0, 0.0, HEIGHT), (-INV, INV, 0.0))
    if window_mm > 0:
        window(layout, "W", window_mm, (-25.0, 0.0, HEIGHT), (1, 0, 0))
    layout.plane("D1", (60.0, 0.0, HEIGHT), (1, 0, 0))
    layout.plane("D2", (0.0, 60.0, HEIGHT), (0, 1, 0))
    return layout


def mzi(window_mm, arm_mm=100.0):
    """Two-input Mach-Zehnder at 532 nm; a window of ``window_mm`` in arm B (BS1 -> MB -> BS2)."""
    layout = Layout()
    layout.source("A", 532.0, (-50.0, 0.0, HEIGHT), (1, 0, 0))
    layout.source("B", 532.0, (0.0, -50.0, HEIGHT), (0, 1, 0))
    splitter(layout, "BS1", (0.0, 0.0, HEIGHT), (-INV, INV, 0.0))
    layout.native("MA", "mirror.flat", {"diameter_mm": "25.4", "reflectance": "1", "valid_band_nm": ["400", "1000"]},
                  (arm_mm, 0.0, HEIGHT), (-INV, INV, 0.0))
    layout.native("MB", "mirror.flat", {"diameter_mm": "25.4", "reflectance": "1", "valid_band_nm": ["400", "1000"]},
                  (0.0, arm_mm, HEIGHT), (INV, -INV, 0.0))
    splitter(layout, "BS2", (arm_mm, arm_mm, HEIGHT), (-INV, INV, 0.0))
    if window_mm > 0:
        window(layout, "WB", window_mm, (arm_mm / 2, arm_mm, HEIGHT), (1, 0, 0))
    layout.plane("port_x", (arm_mm + 60.0, arm_mm, HEIGHT), (1, 0, 0))
    layout.plane("port_y", (arm_mm, arm_mm + 60.0, HEIGHT), (0, 1, 0))
    return layout


def cases():
    lam = 780.0
    n, n_g = sellmeier_index(N_BK7_SELLMEIER, lam), group_index(N_BK7_SELLMEIER, lam)
    built = {"hom": (hom(), {"kind": "hom"})}
    for delay in DELAY_SCAN_MM:
        name = f"hom_delay_{decimal_text(delay).replace('-', 'm').replace('.', 'p')}"
        built[name] = (hom(delay_mm=delay), {"kind": "hom_delay_scan", "source_B_shift_mm": delay})
    built["hom_window_group"] = (hom(delay_mm=round((n_g - 1.0) * 3.0, 6), window_mm=3.0),
                                 {"kind": "hom_window", "window_mm": 3.0, "compensation": "group",
                                  "source_B_shift_mm": round((n_g - 1.0) * 3.0, 6)})
    built["hom_window_phase"] = (hom(delay_mm=round((n - 1.0) * 3.0, 6), window_mm=3.0),
                                 {"kind": "hom_window", "window_mm": 3.0, "compensation": "phase",
                                  "source_B_shift_mm": round((n - 1.0) * 3.0, 6)})
    for t in MZI_THICKNESSES_MM:
        built[f"mzi_t{decimal_text(t).replace('.', 'p')}"] = (mzi(t), {"kind": "mzi", "window_mm": t})
    refused = hom()
    refused.native("TL", "lens.ideal_thin", {"focal_length_mm": "200", "clear_aperture_diameter_mm": "22.86",
                                             "valid_band_nm": ["400", "1000"]}, (30.0, 0.0, HEIGHT), (1, 0, 0))
    built["refused_thin_lens"] = (refused, {"kind": "refusal", "expected": "PHASE_UNDEFINED_IDEAL_LENS"})
    return built


def export(project_path, network_path):
    """Run the public CLI exactly as a user would; return its exit code and parsed stdout."""
    process = subprocess.run([sys.executable, "-m", "os_optics", "project", "network", str(project_path),
                              "--out", str(network_path)], capture_output=True, text=True)
    try:
        stdout = json.loads(process.stdout)
    except ValueError:
        stdout = process.stdout.strip()
    return process.returncode, stdout


def run_example(out_dir):
    installed = require_engine()
    out_dir = Path(out_dir)
    layouts = out_dir / "layouts"
    layouts.mkdir(parents=True, exist_ok=True)
    manifest = {"requires": "os-optics>=0.10.0", "engine_version": installed, "cases": {}}
    for name, (layout, meta) in cases().items():
        project_path = write_json(layouts / f"{name}.project.json", layout.document)
        network_path = layouts / f"{name}.network.json"
        code, stdout = export(project_path, network_path)
        entry = dict(meta, project=str(project_path), exit_code=code)
        if code == 0:
            entry["network"] = str(network_path)
        else:
            problems = stdout.get("error", {}).get("problems", []) if isinstance(stdout, dict) else []
            entry["refusal"] = sorted({p.get("code") for p in problems}) or stdout
        manifest["cases"][name] = entry
    write_json(out_dir / "networks.json", manifest)
    unexpected = [n for n, c in manifest["cases"].items() if (c["exit_code"] == 0) == (c["kind"] == "refusal")]
    return {"example": "interop/quantum (OpenSci side)", "outcome": "UNEXPECTED_RESULT" if unexpected else "COMPLETED",
            "engine_version": installed, "exported": sum(c["exit_code"] == 0 for c in manifest["cases"].values()),
            "refused": {n: c.get("refusal") for n, c in manifest["cases"].items() if c["exit_code"] != 0},
            "unexpected": unexpected, "manifest": str(out_dir / "networks.json")}


def main(argv=None):
    def workflow():
        parser = ArgumentParser(description=__doc__.split("\n\n")[0])
        parser.add_argument("--out", type=Path, default=Path("run-interop-quantum"), help="output directory")
        args = parser.parse_args(argv)
        return run_example(args.out)

    return emit(workflow)


if __name__ == "__main__":
    raise SystemExit(main())
