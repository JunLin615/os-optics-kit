"""Draw the sonnar-telephoto-lens figures from a run of this case.

    python figures.py RUN --out images

RUN is the directory written by ``os-optics project run project.json --out-dir RUN``. The script runs the imaging
analysis of the README's ``os-optics results imaging RUN --monitor image_plane --request imaging-request.json``
in-process (``--imaging FILE`` reads that command's saved JSON output for the same run instead), reads it through
``os_optics_kit.imaging_results`` (only curves and values the engine states as known) and writes
spot.png and mtf.png.
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

    order = sorted(beams, key=lambda sid: beams[sid][0])
    labels = [f"{beams[sid][0]:g}°" for sid in order]
    theme = pr.resolve_theme({"preset": "report", "font_scale": 1.2})
    written = []
    with plt.rc_context(theme.rc()):
        # RMS spot radius per field, with each field's diffraction-limited 80 % encircled-energy radius.
        fig, ax = plt.subplots(figsize=(10, 4.6), layout="constrained")
        heights = [value(sid, "geometric_spot_rms_radius") for sid in order]
        drawn = ax.bar(range(len(order)), heights, 0.6, color=theme.series_color(2))
        ax.bar_label(drawn, labels=[f"{h:.2f}" for h in heights], padding=2, fontsize=theme.font_pt("small"))
        limits = [value(sid, "diffraction_limited_encircled_radius_80") for sid in order]
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
                ax.plot(*curve(sid, "mtf", direction), "o-",
                        color=theme.series_color(i), markersize=4, label=label)
            ax.plot(*curve(order[0], "diffraction_limited_mtf", direction),
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
