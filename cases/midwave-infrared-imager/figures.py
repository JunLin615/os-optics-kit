"""Draw the midwave-infrared-imager figures from a run of this case.

    python figures.py RUN --out images

RUN is the directory written by ``os-optics project run project.json --out-dir RUN``. The script reads the imaging
results exactly as the README does, ``os-optics results imaging RUN --monitor image_plane --request
imaging-request.json`` (``--imaging FILE`` reuses that command's saved JSON output for the same run), and writes
mtf.png, spot.png and wavefront.png.
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
from matplotlib.lines import Line2D  # noqa: E402
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


def bars(ax, labels, heights, colors, digits, size):
    drawn = ax.bar(range(len(heights)), heights, 0.66, color=colors)
    ax.bar_label(drawn, labels=[f"{h:.{digits}f}" for h in heights], padding=2, fontsize=size)
    ax.set_xticks(range(len(labels)), labels)
    ax.grid(axis="x", visible=False)
    ax.set_axisbelow(True)


def draw(result, beams, out):
    values = {}
    for quantity in result["quantities"]:
        values[(quantity["scope"]["source_id"], quantity["quantity_id"].split(".", 1)[1])] = quantity["value"]
    arrays = result["arrays"]
    fields = sorted({field for field, _ in beams.values()})
    waves = sorted({wave for _, wave in beams.values()})
    by = {(field, wave): sid for sid, (field, wave) in beams.items()}
    order = [(f, w) for f in fields for w in waves]
    theme = pr.resolve_theme({"preset": "report", "font_scale": 1.2})
    small = theme.font_pt("small")
    color = {wave: theme.series_color(i) for i, wave in enumerate(waves)}
    styles = dict(zip(fields, ("-", "--", ":")))
    reference = by[(fields[0], waves[1])]  # on axis, middle wavelength
    written = []
    with plt.rc_context(theme.rc()):
        # Diffraction MTF of every field and wavelength, with the on-axis diffraction limit at the middle wavelength.
        fig, axes = plt.subplots(1, 2, figsize=(12, 5.6), sharey=True, layout="constrained")
        for ax, direction in zip(axes, ("tangential", "sagittal")):
            for field, wave in order:
                path = by[(field, wave)] + "#0"
                ax.plot(arrays[f"{path}.frequencies_cyc_per_mm"], arrays[f"{path}.mtf_{direction}"], styles[field],
                        color=color[wave], linewidth=1.3)
            path = reference + "#0"
            ax.plot(arrays[f"{path}.frequencies_cyc_per_mm"], arrays[f"{path}.diffraction_limited_mtf_{direction}"],
                    "-.", color=theme.colors["text"], linewidth=1.8)
            ax.set(title=direction.capitalize(), xlabel="spatial frequency (cycles/mm)", xlim=(0, 30), ylim=(0, 1.02))
        axes[0].set_ylabel("MTF")
        handles = [Line2D([], [], color=color[w], label=f"{w:.0f} nm") for w in waves]
        handles += [Line2D([], [], linestyle=styles[f], color=theme.colors["muted"], label=f"{f:g} deg") for f in fields]
        handles.append(Line2D([], [], linestyle="-.", color=theme.colors["text"],
                              label=f"diffraction limit, on axis, {waves[1] / 1000:g} µm"))
        fig.legend(handles=handles, loc="outside lower center", ncols=7)
        fig.suptitle("Scalar diffraction MTF at the image plane, 3-5 µm, F/2")
        written.append(save(fig, out / "mtf.png"))

        labels = [f"{f:g} deg\n{w:.0f} nm" for f, w in order]
        colors = [color[w] for _, w in order]

        # Geometric ray spot RMS radius, with the on-axis 80 % diffraction-limited encircled radius.
        fig, ax = plt.subplots(figsize=(11, 4.6), layout="constrained")
        bars(ax, labels, [values[(by[k], "geometric_spot_rms_radius")] for k in order], colors, 2, small)
        limit = values[(reference, "diffraction_limited_encircled_radius_80")]
        ax.axhline(limit, linestyle="--", color=theme.colors["text"],
                   label=f"diffraction-limited encircled radius 80% (on axis, {waves[1] / 1000:g} µm) {limit:.2f} µm")
        ax.set(ylabel="geometric spot RMS radius (µm)")
        ax.margins(y=0.15)
        ax.legend(loc="upper left")
        ax.set_title("Ray spot at the image plane per field and wavelength (engine geometric ray bundle)")
        written.append(save(fig, out / "spot.png"))

        # Wavefront error and Strehl ratio.
        fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), layout="constrained")
        bars(axes[0], labels, [values[(by[k], "opd_rms_waves")] for k in order], colors, 3, small)
        axes[0].axhline(1.0, linestyle="--", color=theme.colors["text"])
        axes[0].text(-0.4, 1.02, "1 wave: below it the engine marks its geometric MTF out of scope", fontsize=small,
                     va="bottom")
        axes[0].set(ylabel="wavefront OPD RMS (waves)", ylim=(0, 1.2),
                    title="Wavefront error on the reference sphere")
        bars(axes[1], labels, [values[(by[k], "strehl_ratio")] for k in order], colors, 3, small)
        axes[1].set(ylabel="Strehl ratio", ylim=(0, 1.08), title="Strehl ratio of the scalar diffraction model")
        for ax in axes:
            ax.tick_params(axis="x", labelsize=small * 0.9)
        fig.suptitle("Image-plane wavefront, 3-5 µm, F/2 (all values from results imaging)")
        written.append(save(fig, out / "wavefront.png"))
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
