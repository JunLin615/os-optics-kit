"""Standard plots from saved results (no simulation): sweep curves, 2-parameter heatmaps, signed bar charts,
histograms with bounds, and the power distribution of one run. PNG and SVG.

Usage:
  python examples/plot_results.py sweep STUDY_DIR --y target:spot_radius [--out DIR]
  python examples/plot_results.py power RUN_DIR [--out DIR]
  python examples/plot_results.py bars DATA.json [--out DIR]   # {"labels", "values", "unit", "title", "ylabel"?}
  python examples/plot_results.py histogram DATA.json [--out DIR]   # {"values", "unit", "title", "xlabel"?,
                                                                     #  "bounds"?: {"minimum", "maximum"}, "bins"?}

sweep: one parameter gives a curve of the column against the parameter; two parameters give a heatmap (first
parameter on x). Columns: "target:<id>", "derived:<name>" or "param:<name>" as in sweep_parameters.py. Every
sample is marked by its outcome (PASS, FAIL, UNKNOWN, OUT_OF_MODEL, ERROR) with its own marker and label; samples
without a value are never omitted: on a curve they are drawn as hollow markers on a dotted line below the lowest
value, labelled "samples without an exact value" (that position carries no value), with their own legend entry per outcome;
on a heatmap their cell stays empty with the outcome marker. The target's bounds (minimum / maximum, or target value
+- tolerance) are drawn as a band on a curve and as lines on the heatmap colour bar.
power: power received by each observation plane and the run's power ledger sinks, in W.

Axis labels carry units; a caption names the data file (path relative to the output directory and sha256 prefix),
the Engine build and the catalog release. Output (default DIR: "plots" inside a study directory; for a run
directory, "plots" next to it, so the run's own files stay as its manifest lists them): <name>.png and <name>.svg
(text kept as text). Stdout: one JSON document with the written paths. Exit 0, 4 on error.
"""

from __future__ import annotations

import argparse
from pathlib import Path

if __package__:
    from . import workflow_common as wc
else:
    import workflow_common as wc

#: outcome -> (marker, colour, label); colours follow a fixed status palette, markers keep them apart without colour
STATUS_STYLE = {
    "NOT_APPLICABLE": ("o", "#3b6fa1", "NOT_APPLICABLE (no selected requirements)"),
    "PASS": ("o", "#0ca30c", "PASS"),
    "FAIL": ("X", "#d03b3b", "FAIL"),
    "UNKNOWN": ("D", "#b07d00", "UNKNOWN (no value or undefined)"),
    "OUT_OF_MODEL": ("^", "#ec835a", "OUT_OF_MODEL (model scope issue)"),
    "ERROR": ("s", "#6b6b6b", "ERROR (refused / not run)"),
}
LINE_COLOR = "#2a78d6"
BAND_COLOR = "#2a78d6"


def _pyplot():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"svg.fonttype": "none", "svg.hashsalt": "opensci-example", "font.size": 9,
                         "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
                         "grid.color": "#e3e3e3", "grid.linewidth": 0.6})
    return plt


def caption(source, out_dir, *, engine_build=None, catalog_version=None):
    """Caption text: data file relative to the output directory, sha256 prefix, Engine build, catalog release."""
    parts = []
    if source is not None:
        parts.append(f"data: {wc.relative(source, out_dir)} (sha256 {wc.sha256_file(source)[:12]})")
    parts.append(f"engine {engine_build or 'unknown'}")
    parts.append(f"catalog {catalog_version or 'none (catalog-free run)'}")
    return " | ".join(parts)


def save(fig, out_dir, name, text):
    """Write <name>.png and <name>.svg with the caption; returns the paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    fig.text(0.01, 0.01, text, fontsize=6.5, color="#555555", ha="left", va="bottom")
    fig.subplots_adjust(bottom=0.2)
    png, svg = out_dir / f"{name}.png", out_dir / f"{name}.svg"
    fig.savefig(png, dpi=150, metadata={"Software": None})
    fig.savefig(svg, metadata={"Date": None, "Creator": None})
    import matplotlib.pyplot as plt

    plt.close(fig)
    return {"png": png.as_posix(), "svg": svg.as_posix(), "caption": text}


def _label(column, unit):
    name = column.split(":", 1)[-1]
    return f"{name} [{unit}]" if unit else name


def _study(study_dir):
    study_dir = Path(study_dir)
    summary_path = study_dir / "summary.json"
    if not summary_path.is_file():
        raise wc.WorkflowError("STUDY_NOT_READABLE", f"{study_dir} has no summary.json", path=study_dir.as_posix())
    return summary_path, wc.read_json(summary_path)


def _bounds(study_dir, rows, target_id):
    for row in rows:
        sample = Path(study_dir) / "samples" / row["sample_id"] / "sample.json"
        if sample.is_file():
            for target in wc.read_json(sample).get("targets", []):
                if target["id"] == target_id and target.get("bounds"):
                    return wc.target_bounds(target)
    return None


def _band(ax, bounds, horizontal=True):
    if not bounds or (bounds["minimum"] is None and bounds["maximum"] is None):
        return
    low = bounds["minimum"] if bounds["minimum"] is not None else None
    high = bounds["maximum"] if bounds["maximum"] is not None else None
    span = ax.axhspan if horizontal else ax.axvspan
    line = ax.axhline if horizontal else ax.axvline
    if low is not None and high is not None:
        span(low, high, color=BAND_COLOR, alpha=0.12, label=f"target bounds [{low:.6g}, {high:.6g}]")
    for value, text in ((low, "minimum"), (high, "maximum")):
        if value is not None:
            line(value, color=BAND_COLOR, linewidth=0.9, linestyle="--",
                 label=f"target {text} {value:.6g}" if low is None or high is None else None)


def plot_sweep(study_dir, y, *, out_dir=None, name=None):
    """Curve (1 parameter) or heatmap (2 parameters) of column ``y`` from a study's summary.json."""
    summary_path, summary = _study(study_dir)
    rows = summary["rows"]
    parameters = summary["parameters"]
    if not rows or not any(y in row for row in rows):
        raise wc.WorkflowError("COLUMN_UNKNOWN", f"column {y!r} is not in the study table; columns: "
                               f"{[c for c in summary['columns'] if ':' in c and not c.endswith(':unit')]}")
    out_dir = Path(out_dir) if out_dir else Path(study_dir) / "plots"
    unit = next((row.get(f"{y}:unit") for row in rows if row.get(f"{y}:unit")), None)
    releases = sorted({r["catalog_version"] for r in rows if r.get("catalog_version")})
    text = caption(summary_path, out_dir, engine_build=summary["identity"]["engine"]["engine_build_id"],
                   catalog_version=", ".join(releases) or None)
    stem = name or ("sweep_" if len(parameters) == 1 else "heatmap_") + y.replace(":", "_")
    bounds = _bounds(study_dir, rows, y.split(":", 1)[1]) if y.startswith("target:") else None
    if len(parameters) == 1:
        fig, info = _curve(rows, parameters[0], y, unit, bounds)
        return {**info, "bounds": bounds, "out": save(fig, out_dir, stem, text)}
    fig, info = _heatmap(rows, parameters, y, unit, bounds)
    return {**info, "bounds": bounds, "out": save(fig, out_dir, stem, text)}


def _has_exact_plot_value(row, column):
    import math
    value = row.get(column)
    return (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
            and row.get(f"{column}:exact") is not False)


def _curve(rows, parameter, y, unit, bounds):
    plt = _pyplot()
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    x_col = f"param:{parameter['name']}"
    valued = [(r[x_col], r[y]) for r in rows if _has_exact_plot_value(r, y)]
    if valued:
        ordered = sorted(rows, key=lambda row: row[x_col])
        xs = [row[x_col] for row in ordered]
        ys = [row[y] if _has_exact_plot_value(row, y) else float("nan") for row in ordered]
        ax.plot(xs, ys, color=LINE_COLOR, linewidth=1.5, zorder=2, label=_label(y, None))
    _band(ax, bounds)
    values = [v for _x, v in valued]
    low, high = (min(values), max(values)) if values else (0.0, 1.0)
    span = (high - low) or abs(high) or 1.0
    lower = low - 0.12 * span  # position of the valueless samples: below every value, never on a real point
    counts = {}
    for outcome, (marker, color, label) in STATUS_STYLE.items():
        pts = [r for r in rows if r["outcome"] == outcome]
        if not pts:
            continue
        counts[outcome] = len(pts)
        with_value = [(r[x_col], r[y]) for r in pts if _has_exact_plot_value(r, y)]
        without = [r[x_col] for r in pts if not _has_exact_plot_value(r, y)]
        if with_value:
            ax.scatter(*zip(*with_value), marker=marker, s=46, color=color, edgecolors="white", linewidths=0.8,
                       zorder=3, label=f"{label} ({len(pts)})")
        if without:
            ax.scatter(without, [lower] * len(without), marker=marker, s=46, facecolors="none", edgecolors=color,
                       linewidths=1.2, zorder=3, clip_on=False, label=f"{label}, no exact value ({len(without)})")
    missing = len(rows) - len(valued)
    if missing:
        ax.axhline(lower, color="#8a8a8a", linewidth=0.8, linestyle=":", zorder=1)
        ax.annotate("samples without an exact value", xy=(0.0, lower), xycoords=("axes fraction", "data"),
                    xytext=(2, 3), textcoords="offset points", fontsize=6.5, color="#555555")
    ax.set_xlabel(_label(x_col, parameter["unit"]))
    ax.set_ylabel(_label(y, unit))
    ax.set_title(f"{y.split(':', 1)[1]} vs {parameter['name']}")
    ax.legend(fontsize=7, loc="best", frameon=False)
    return fig, {"points": len(rows), "with_value": len(valued), "without_value": missing,
                 "no_value_position": lower if missing else None, "by_outcome": counts}


def _heatmap(rows, parameters, y, unit, bounds):
    import numpy as np

    plt = _pyplot()
    px, py = parameters
    xs = sorted({r[f"param:{px['name']}"] for r in rows})
    ys = sorted({r[f"param:{py['name']}"] for r in rows})
    grid = np.full((len(ys), len(xs)), np.nan)
    for r in rows:
        if _has_exact_plot_value(r, y):
            grid[ys.index(r[f"param:{py['name']}"]), xs.index(r[f"param:{px['name']}"])] = r[y]
    fig, ax = plt.subplots(figsize=(6.4, 4.8))
    mesh = ax.pcolormesh(np.arange(len(xs) + 1) - 0.5, np.arange(len(ys) + 1) - 0.5, np.ma.masked_invalid(grid),
                         cmap="Blues", shading="flat", edgecolors="white", linewidth=1.0)
    limits = [v for v in ((bounds or {}).get("minimum"), (bounds or {}).get("maximum")) if v is not None]
    if limits and np.isfinite(grid).any():  # the colour scale reaches the bounds, so their lines are visible
        mesh.set_clim(min(np.nanmin(grid), *limits), max(np.nanmax(grid), *limits))
    bar = fig.colorbar(mesh, ax=ax)
    bar.set_label(_label(y, unit))
    drawn = []
    for value, text in (((bounds or {}).get("minimum"), "minimum"), ((bounds or {}).get("maximum"), "maximum")):
        if value is not None:
            bar.ax.axhline(value, color="#d03b3b", linewidth=1.4, linestyle="--")
            drawn.append(f"target {text} {value:.6g}")
    for outcome, (marker, color, label) in STATUS_STYLE.items():
        pts = [r for r in rows if r["outcome"] == outcome]
        if pts:
            ax.scatter([xs.index(r[f"param:{px['name']}"]) for r in pts],
                       [ys.index(r[f"param:{py['name']}"]) for r in pts], marker=marker, s=30, color=color,
                       edgecolors="white", linewidths=0.6, label=f"{label} ({len(pts)})")
    ax.set_xticks(range(len(xs)), [f"{v:.6g}" for v in xs])
    ax.set_yticks(range(len(ys)), [f"{v:.6g}" for v in ys])
    ax.set_xlabel(_label(f"param:{px['name']}", px["unit"]))
    ax.set_ylabel(_label(f"param:{py['name']}", py["unit"]))
    ax.grid(False)
    ax.set_title(f"{y.split(':', 1)[1]} (empty cell: no exact value)" + (
        f"; {', '.join(drawn)} (dashed on the colour bar)" if drawn else ""), fontsize=8)
    ax.legend(fontsize=6.5, loc="upper left", bbox_to_anchor=(0.0, -0.14), ncol=3, frameon=False)
    fig.subplots_adjust(bottom=0.3)
    return fig, {"points": len(rows), "with_value": int(np.isfinite(grid).sum()),
                 "without_value": len(rows) - int(np.isfinite(grid).sum())}


def plot_bars(labels, values, unit, out_dir, name, *, title, ylabel=None, source=None, engine_build=None,
              catalog_version=None, errors=None):
    """Signed horizontal bar chart (e.g. sensitivities per parameter). ``values`` None are labelled "no value"."""
    plt = _pyplot()
    fig, ax = plt.subplots(figsize=(6.4, max(2.4, 0.42 * len(labels) + 1.4)))
    shown = [v if isinstance(v, (int, float)) else 0.0 for v in values]
    colors = ["#2a78d6" if v >= 0 else "#e8743b" for v in shown]
    ax.barh(range(len(labels)), shown, color=colors, height=0.6, xerr=errors, edgecolor="white")
    for i, value in enumerate(values):
        ax.text(shown[i], i, f" {value:.4g}" if isinstance(value, (int, float)) else " no value",
                va="center", ha="left" if shown[i] >= 0 else "right", fontsize=7, color="#333333")
    ax.axvline(0.0, color="#555555", linewidth=0.8)
    ax.set_yticks(range(len(labels)), labels)
    ax.invert_yaxis()
    ax.set_xlabel(ylabel or (f"value [{unit}]" if unit else "value"))
    ax.set_title(title)
    fig.tight_layout(rect=(0, 0.08, 1, 1))  # room for long bar labels on the left
    text = caption(source, out_dir, engine_build=engine_build, catalog_version=catalog_version)
    return {"out": save(fig, out_dir, name, text)}


def plot_histogram(values, unit, out_dir, name, *, title, xlabel=None, bounds=None, bins=20, source=None,
                   engine_build=None, catalog_version=None):
    """Histogram of finite ``values`` with optional bounds band; the counts of all and of plotted values are shown."""
    plt = _pyplot()
    finite = [v for v in values if isinstance(v, (int, float)) and v == v]
    fig, ax = plt.subplots(figsize=(6.4, 4.0))
    if finite:
        ax.hist(finite, bins=bins, color=LINE_COLOR, edgecolor="white", linewidth=0.8, label="samples")
    _band(ax, bounds, horizontal=False)
    ax.set_xlabel(xlabel or (f"value [{unit}]" if unit else "value"))
    ax.set_ylabel("samples [count]")
    ax.set_title(f"{title} (plotted {len(finite)} of {len(values)} values)")
    ax.legend(fontsize=7, frameon=False)
    text = caption(source, out_dir, engine_build=engine_build, catalog_version=catalog_version)
    return {"out": save(fig, out_dir, name, text), "plotted": len(finite), "total": len(values)}


def plot_power(run_dir, *, out_dir=None, name="power_distribution"):
    """Power received per observation plane and the power-ledger sinks of one saved run (W)."""
    from os_optics.results import load_run

    run_dir = Path(run_dir)
    results = load_run(run_dir)
    identity = results.identity()
    budget = results.power_budget()
    monitors = results.monitors()
    out_dir = Path(out_dir) if out_dir else run_dir.parent / "plots"
    plt = _pyplot()
    fig, (left, right) = plt.subplots(1, 2, figsize=(8.0, 3.8), gridspec_kw={"width_ratios": [3, 2]})
    names = [m["observation_id"] for m in monitors]
    for index, monitor in enumerate(monitors):
        state, value = monitor["quantity_state"], monitor["total_power_W"]
        if state in ("KNOWN", "KNOWN_ZERO") and value is not None:
            left.bar(index, value, color=LINE_COLOR, width=0.6, edgecolor="white")
        elif state == "BOUNDED" and value is not None:
            upper = monitor.get("power_upper_bound_W")
            left.bar(index, value, color="none", hatch="//", edgecolor=LINE_COLOR, width=0.6)
            if upper is not None:
                left.errorbar(index, value, yerr=[[0.0], [max(0.0, upper - value)]],
                              fmt="none", color=LINE_COLOR, capsize=4)
            left.annotate("bounded" if upper is not None else "lower bound", (index, value),
                          xytext=(0, 4), textcoords="offset points", ha="center", fontsize=7)
        else:
            # Axes coordinates mark unavailable data without placing a false zero on the power axis.
            left.text(index, 0.02, state or "unavailable", transform=left.get_xaxis_transform(),
                      ha="center", va="bottom", fontsize=7, rotation=90)
    left.set_xticks(range(len(names)), names, rotation=20)
    left.set_xlim(-0.5, max(0.5, len(names) - 0.5))
    left.set_ylabel("received power [W]")
    left.set_title("observation planes\nsolid: known; hatched: lower bound", fontsize=9)
    total = budget.get("total_input_W")
    if total:
        left.axhline(total, color="#555555", linestyle="--", linewidth=0.9, label=f"total input {total:.6g} W")
        left.legend(fontsize=7, frameon=False)
    sinks = budget.get("total_sinks") or {}
    right.barh(range(len(sinks)), list(sinks.values()), color="#8a8a8a", height=0.6, edgecolor="white")
    right.set_yticks(range(len(sinks)), list(sinks))
    right.set_xlabel("power [W]")
    right.set_title("power ledger sinks")
    text = caption(run_dir / "simulation_result.json", out_dir, engine_build=identity.get("engine_build_id"),
                   catalog_version=identity.get("catalog_version"))
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    return {"out": save(fig, out_dir, name, text), "observations": {m["observation_id"]: {key: m.get(key) for key in
                ("total_power_W", "quantity_state", "power_complete", "power_upper_bound_W")} for m in monitors},
            "sinks_W": sinks,
            "conserved": budget.get("conserved")}


def main(argv=None):
    def workflow():
        parser = wc.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
        parser.add_argument("kind", choices=["sweep", "power", "bars", "histogram"], help="Plot kind")
        parser.add_argument("path", type=Path, help="Study directory, run directory or data JSON")
        parser.add_argument("--y", help="sweep: column to plot (target:<id>, derived:<name>)")
        parser.add_argument("--out", type=Path, help="Output directory")
        parser.add_argument("--name", help="Output file stem")
        args = parser.parse_args(argv)
        if args.kind == "sweep":
            if not args.y:
                raise ValueError("sweep needs --y COLUMN")
            result = plot_sweep(args.path, args.y, out_dir=args.out, name=args.name)
        elif args.kind == "power":
            result = plot_power(args.path, out_dir=args.out, name=args.name or "power_distribution")
        else:
            data = wc.read_json(args.path)
            out = args.out or args.path.parent / "plots"
            common = {"source": args.path, "engine_build": data.get("engine_build_id"),
                      "catalog_version": data.get("catalog_version")}
            if args.kind == "bars":
                result = plot_bars(data["labels"], data["values"], data.get("unit"), out, args.name or "bars",
                                   title=data.get("title", ""), ylabel=data.get("ylabel"), **common)
            else:
                result = plot_histogram(data["values"], data.get("unit"), out, args.name or "histogram",
                                        title=data.get("title", ""), xlabel=data.get("xlabel"),
                                        bounds=data.get("bounds"), bins=data.get("bins", 20), **common)
        return {"status": "COMPLETED", **result}

    return wc.emit(workflow)


if __name__ == "__main__":
    raise SystemExit(main())
