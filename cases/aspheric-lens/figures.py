"""Draw the aspheric-lens figures from a run of this case.

    python figures.py RUN --out images

RUN is the directory written by ``os-optics project run project.json --out-dir RUN``. The script runs the imaging
analysis of the README's ``os-optics results imaging RUN --monitor image_plane --request imaging-request.json``
in-process (``--imaging FILE`` reads that command's saved JSON output for the same run instead), reads it through
``os_optics_kit.imaging_results`` (only curves and values the engine states as known) and writes
mtf.png, strehl.png and spot.png. Only the even-asphere version is drawn: the spherical control has no Project in
this case, so its numbers stay in the README table and in data/engine-aspheric.json.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from os_optics_kit import imaging_results as ir, mtf, presentation as pr  # noqa: E402
from os_optics_kit.workflow_common import WorkflowError  # noqa: E402

HERE = Path(__file__).resolve().parent
MONITOR = "image_plane"
DPI = 120
MAX_BYTES = 400 * 1024


def imaging(run, saved=None):
    """The imaging analysis of RUN at image_plane with imaging-request.json, run in-process, or its saved JSON
    (``--imaging``), which must come from RUN."""
    request = json.loads((HERE / "imaging-request.json").read_text(encoding="utf-8"))
    try:
        return mtf.imaging_of(run, MONITOR, request, saved=saved)
    except WorkflowError as exc:
        raise SystemExit(f"{exc.code}: {exc}") from None


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
    line = {entry["source_id"]: entry["line_id"] for entry in ir.lines(result)}  # one spectral line per source

    def value(sid, name):
        quantity = ir.scalar(result, line[sid], name)
        if quantity["value"] is None:
            raise SystemExit(f"{quantity['quantity_id']} is {quantity['state']} ({quantity['reason']}): not drawn")
        return quantity["value"]

    def curve(sid, kind, direction):
        record = ir.curve(result, line[sid], kind, direction)
        if not record["usable"]:
            raise SystemExit(f"{record['quantity_id']} is {record['status']} ({record['reason']}): not drawn")
        return record["frequencies_cyc_per_mm"], record["values"]

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
                ax.plot(*curve(by[(field, wave)], "mtf", direction),
                        color=color[wave], label=f"{wave:.0f} nm")
            ax.plot(*curve(by[(field, waves[1])], "diffraction_limited_mtf", direction),
                    "-.", color=theme.colors["text"], label=f"diffraction limit, {waves[1]:.0f} nm")
            ax.set(title=direction.capitalize(), xlabel="spatial frequency (cycles/mm)", xlim=(0, 110), ylim=(0, 1.02))
        axes[0].set_ylabel("MTF")
        axes[1].legend(loc="upper right")
        fig.suptitle(f"Diffraction MTF of the even-asphere version at {field:g} deg half field")
        written.append(save(fig, out / "mtf.png"))

        # Strehl ratio against half field, every wavelength.
        fig, ax = plt.subplots(figsize=(9, 5), layout="constrained")
        for wave, style in zip(waves, ("-", "--", ":")):
            ax.plot(fields, [value(by[(f, wave)], "strehl_ratio") for f in fields], style, marker="o",
                    color=color[wave], label=f"{wave:.0f} nm")
        edge = max(value(by[(fields[-1], wave)], "strehl_ratio") for wave in waves)
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
            heights = [value(by[(f, wave)], "geometric_spot_rms_radius") for f in fields]
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
