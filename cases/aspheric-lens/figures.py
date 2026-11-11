"""Draw the aspheric-lens figures from a run of this case.

    python figures.py RUN --out images

RUN is the directory written by ``os-optics project run project.json --out-dir RUN``. The script reads the imaging
results exactly as the README does, ``os-optics results imaging RUN --monitor image_plane --request
imaging-request.json`` (``--imaging FILE`` reuses that command's saved JSON output for the same run), and writes
mtf.png, strehl.png and spot.png. Only the even-asphere version is drawn: the spherical control has no Project in
this case, so its numbers stay in the README table and in data/engine-aspheric.json.
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
    fields = sorted({field for field, _ in beams.values()})
    waves = sorted({wave for _, wave in beams.values()})
    by = {(field, wave): sid for sid, (field, wave) in beams.items()}
    theme = pr.resolve_theme({"preset": "report", "font_scale": 1.2})
    color = {wave: theme.series_color(i) for i, wave in enumerate(waves)}
    written = []
    with plt.rc_context(theme.rc()):
        # MTF at the middle field, every wavelength, with the 588 nm diffraction limit.
        field = fields[1]
        fig, axes = plt.subplots(1, 2, figsize=(12, 5.4), sharey=True, layout="constrained")
        for ax, direction in zip(axes, ("tangential", "sagittal")):
            for wave in waves:
                path = by[(field, wave)] + "#0"
                ax.plot(arrays[f"{path}.frequencies_cyc_per_mm"], arrays[f"{path}.mtf_{direction}"],
                        color=color[wave], label=f"{wave:.0f} nm")
            path = by[(field, waves[1])] + "#0"
            ax.plot(arrays[f"{path}.frequencies_cyc_per_mm"], arrays[f"{path}.diffraction_limited_mtf_{direction}"],
                    "-.", color=theme.colors["text"], label=f"diffraction limit, {waves[1]:.0f} nm")
            ax.set(title=direction.capitalize(), xlabel="spatial frequency (cycles/mm)", xlim=(0, 110), ylim=(0, 1.02))
        axes[0].set_ylabel("MTF")
        axes[1].legend(loc="upper right")
        fig.suptitle(f"Diffraction MTF of the even-asphere version at {field:g} deg half field")
        written.append(save(fig, out / "mtf.png"))

        # Strehl ratio against half field, every wavelength.
        fig, ax = plt.subplots(figsize=(9, 5), layout="constrained")
        for wave, style in zip(waves, ("-", "--", ":")):
            ax.plot(fields, [values[(by[(f, wave)], "strehl_ratio")] for f in fields], style, marker="o",
                    color=color[wave], label=f"{wave:.0f} nm")
        edge = max(values[(by[(fields[-1], wave)], "strehl_ratio")] for wave in waves)
        ax.annotate(f"edge of field: {edge:.3f} max", (fields[-1], edge), xytext=(fields[-1], 0.36),
                    ha="right", color=theme.colors["muted"],
                    arrowprops={"arrowstyle": "-", "color": theme.colors["muted"]})
        ax.set(xlabel="half field (deg)", ylabel="Strehl ratio", ylim=(0, 1.05))
        ax.set_xticks(fields, [f"{f:g}" for f in fields])
        ax.legend(loc="upper right")
        ax.set_title("Strehl ratio of the scalar diffraction model, even-asphere version")
        written.append(save(fig, out / "strehl.png"))

        # Geometric ray spot RMS radius, grouped by field, one bar per wavelength.
        fig, ax = plt.subplots(figsize=(10.5, 4.6), layout="constrained")
        width = 0.26
        for i, wave in enumerate(waves):
            xs = [g + (i - 1) * width for g in range(len(fields))]
            heights = [values[(by[(f, wave)], "geometric_spot_rms_radius")] for f in fields]
            bars = ax.bar(xs, heights, width * 0.92, color=color[wave], label=f"{wave:.0f} nm")
            ax.bar_label(bars, labels=[f"{h:.2f}" for h in heights], padding=2, fontsize=theme.font_pt("small"))
        ax.set_xticks(range(len(fields)), [f"{f:g} deg" for f in fields])
        ax.set(xlabel="half field", ylabel="geometric spot RMS radius (µm)")
        ax.margins(y=0.12)
        ax.grid(axis="x", visible=False)
        ax.set_axisbelow(True)
        ax.legend(loc="upper left")
        ax.set_title("Ray spot at the image plane, even-asphere version")
        written.append(save(fig, out / "spot.png"))
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
