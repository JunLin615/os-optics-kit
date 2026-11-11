"""Draw the sonnar-telephoto-lens figures from a run of this case.

    python figures.py RUN --out images

RUN is the directory written by ``os-optics project run project.json --out-dir RUN``. The script reads the imaging
results exactly as the README does, ``os-optics results imaging RUN --monitor image_plane --request
imaging-request.json`` (``--imaging FILE`` reuses that command's saved JSON output for the same run), and writes
spot.png and mtf.png.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from os_optics_kit import presentation as pr  # noqa: E402

HERE = Path(__file__).resolve().parent
MONITOR = "image_plane"
DPI = 120
MAX_BYTES = 400 * 1024


def imaging(run, saved=None):
    """The JSON of ``os-optics results imaging RUN --monitor image_plane --request imaging-request.json``."""
    if saved is not None:
        result = json.loads(Path(saved).read_text(encoding="utf-8"))
    else:
        command = [sys.executable, "-m", "os_optics", "results", "imaging", str(run), "--monitor", MONITOR,
                   "--request", str(HERE / "imaging-request.json")]
        done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
        if done.returncode not in (0, 2):  # 2: a quantity outside its model, with the reason in the JSON
            raise SystemExit(f"results imaging exited {done.returncode}: {(done.stderr or done.stdout).strip()}")
        result = json.loads(done.stdout)
    manifest = json.loads((Path(run) / "manifest.json").read_text(encoding="utf-8"))
    if result["identity"]["physical"]["engine_input_hash"] != manifest["engine_run"]["input_hash"]["digest"]:
        raise SystemExit("the imaging results are not from RUN (engine_input_hash differs)")
    return result


def sources(run):
    """``{source_id: (half field in degrees, wavelength in nm)}`` from the sources of RUN/project.json."""
    project = json.loads((Path(run) / "project.json").read_text(encoding="utf-8"))
    out = {}
    for component in project["components"]:
        if component.get("optical_role") != "source":
            continue
        w, x, y, z = component["nominal_pose"]["quaternion_wxyz"]
        axis_x = 2 * (x * z + w * y)  # the beam's local +z in the design frame, against the +x optical axis
        field = round(math.degrees(math.acos(max(-1.0, min(1.0, axis_x)))), 3)
        out[component["instance_id"]] = (field, component["source_model"]["wavelength_nm"])
    return out


def save(fig, path):
    fig.savefig(path, dpi=DPI, metadata={"Software": None})
    plt.close(fig)
    size = path.stat().st_size
    if size > MAX_BYTES:
        raise SystemExit(f"{path.name} is {size} bytes, above {MAX_BYTES}")
    return {"file": path.name, "bytes": size}


def draw(result, beams, out):
    values = {}
    for quantity in result["quantities"]:
        values[(quantity["scope"]["source_id"], quantity["quantity_id"].split(".", 1)[1])] = quantity["value"]
    arrays = result["arrays"]
    order = sorted(beams, key=lambda sid: beams[sid][0])
    labels = [f"{beams[sid][0]:g}°" for sid in order]
    theme = pr.resolve_theme({"preset": "report", "font_scale": 1.2})
    written = []
    with plt.rc_context(theme.rc()):
        # RMS spot radius per field, with each field's diffraction-limited 80 % encircled-energy radius.
        fig, ax = plt.subplots(figsize=(10, 4.6), layout="constrained")
        heights = [values[(sid, "geometric_spot_rms_radius")] for sid in order]
        drawn = ax.bar(range(len(order)), heights, 0.6, color=theme.series_color(2))
        ax.bar_label(drawn, labels=[f"{h:.2f}" for h in heights], padding=2, fontsize=theme.font_pt("small"))
        limits = [values[(sid, "diffraction_limited_encircled_radius_80")] for sid in order]
        ax.hlines(limits, [i - 0.3 for i in range(len(order))], [i + 0.3 for i in range(len(order))],
                  colors=theme.colors["text"], linestyles="--", linewidth=1.8,
                  label="diffraction-limited EE80 radius (" + ", ".join(f"{v:.2f}" for v in limits) + " µm)")
        ax.set_xticks(range(len(order)), labels)
        ax.set(xlabel="half field", ylabel="RMS spot radius (µm)")
        ax.margins(y=0.15)
        ax.grid(axis="x", visible=False)
        ax.set_axisbelow(True)
        ax.legend(loc="upper left")
        ax.set_title("RMS spot radius per field (engine results imaging, 588 nm)")
        written.append(save(fig, out / "spot.png"))

        # Diffraction MTF per field with the on-axis diffraction limit.
        fig, axes = plt.subplots(1, 2, figsize=(12, 5.2), sharey=True, layout="constrained")
        for ax, direction in zip(axes, ("tangential", "sagittal")):
            for i, (sid, label) in enumerate(zip(order, labels)):
                path = sid + "#0"
                ax.plot(arrays[f"{path}.frequencies_cyc_per_mm"], arrays[f"{path}.mtf_{direction}"], "o-",
                        color=theme.series_color(i), markersize=4, label=label)
            path = order[0] + "#0"
            ax.plot(arrays[f"{path}.frequencies_cyc_per_mm"], arrays[f"{path}.diffraction_limited_mtf_{direction}"],
                    "--", color=theme.colors["text"], label="diffraction limit, 0°")
            ax.set(title=direction.capitalize(), xlabel="spatial frequency (cycles/mm)", xlim=(0, 100), ylim=(0, 1.02))
        axes[0].set_ylabel("MTF")
        handles, names = axes[0].get_legend_handles_labels()
        fig.legend(handles, names, loc="outside lower center", ncols=len(names))
        fig.suptitle("Modulation transfer function at the stated aperture")
        written.append(save(fig, out / "mtf.png"))
    return written


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run", type=Path, help="run directory of this case's project.json")
    parser.add_argument("--out", type=Path, default=HERE / "images", help="directory for the PNG files")
    parser.add_argument("--imaging", type=Path, help="saved JSON of the same 'results imaging' command for RUN")
    args = parser.parse_args(argv)
    result = imaging(args.run, args.imaging)
    args.out.mkdir(parents=True, exist_ok=True)
    print(json.dumps({"figures": draw(result, sources(args.run), args.out)}))


if __name__ == "__main__":
    main()
