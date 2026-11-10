"""Standard plots from saved results (no simulation): sweep curves, 2-parameter heatmaps, signed bar charts,
histograms with bounds, and the power distribution of one run. PNG and SVG (and PDF with a theme).

Usage:
  os-optics-kit plot sweep STUDY_DIR --y target:spot_radius [--out DIR] [--theme T] [--set KEY=VALUE ...]
  os-optics-kit plot power RUN_DIR [--out DIR] [--theme T]
  os-optics-kit plot bars DATA.json [--out DIR]        # {"labels", "values", "unit", "title", "ylabel"?}
  os-optics-kit plot histogram DATA.json [--out DIR]   # {"values", "unit", "title", "xlabel"?,
                                                       #  "bounds"?: {"minimum", "maximum"}, "bins"?}

sweep: one parameter gives a curve of the column against the parameter; two parameters give a heatmap (first
parameter on x). Columns: "target:<id>", "derived:<name>" or "param:<name>" as in os-optics-kit sweep. Every
sample is marked by its outcome (PASS, FAIL, UNKNOWN, OUT_OF_MODEL, ERROR) with its own marker and label; samples
without a value are never omitted: on a curve they are drawn as hollow markers on a dotted line below the lowest
value, labelled "samples without an exact value" (that position carries no value), with their own legend entry per outcome;
on a heatmap their cell stays empty with the outcome marker. The target's bounds (minimum / maximum, or target value
+- tolerance) are drawn as a band on a curve and as lines on the heatmap colour bar.
power: power received by each observation plane and the run's power ledger sinks, in W.

Without --theme, axis labels carry units and a caption in the figure names the data file (path relative to the
output directory and sha256 prefix), the Engine build and the catalog release. --theme presentation (or report, or
a JSON file holding a theme object or a presentation.json) applies presentation settings: language (en, zh-CN),
background (light, dark, transparent), font scale, size (slide, 4k, WIDTHxHEIGHT), title on/off, display units and
labels; --set theme keys such as --set language=zh-CN --set font_scale=1.4. The presentation preset keeps the data
file and identities out of the figure: they are written to <name>.caption.txt and <name>.provenance.txt.
Run 'os-optics-kit present settings' for every theme key.

Output (default DIR: "plots" inside a study directory; for a run directory, "plots" next to it, so the run's own
files stay as its manifest lists them): <name>.png and <name>.svg (text kept as text). Stdout: one JSON document with
the written paths. Exit 0, 4 on error. Needs matplotlib (python -m pip install "os-optics-kit[plot]"); without it
the command exits 4 with error code PLOT_EXTRA_MISSING.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import math
from pathlib import Path

from . import presentation as pres
from . import workflow_common as wc

PROG = "os-optics-kit plot"

#: outcome -> (marker, colour, legacy label); colours follow a fixed status palette, markers keep them apart
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
LEGACY_RC = {"svg.fonttype": "none", "svg.hashsalt": "opensci-example", "font.size": 9,
             "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
             "grid.color": "#e3e3e3", "grid.linewidth": 0.6}


def _pyplot():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update(LEGACY_RC)
    return plt


class Style:
    """How one figure is drawn: the legacy figure (no theme) or a presentation theme."""

    def __init__(self, theme=None, size=None, *, provenance=None, companions=True):
        self.legacy = theme is None
        self.provenance = provenance  # provenance text replacing the computed one (present: the asset's data.json)
        self.companions = companions  # write <name>.caption.txt / .provenance.txt next to the figure
        self.title_override = None  # present: the asset's own title replaces the automatic one
        self.theme = theme or pres.resolve_theme({"preset": "report", "base_font_pt": 9.0})
        self.size = size

    @property
    def language(self):
        return self.theme.language

    def t(self, key, **values):
        return self.theme.text(key, **values)

    def outcome(self, code):
        return STATUS_STYLE[code][2] if self.legacy else self.theme.outcome(code)

    def color(self, code):
        return STATUS_STYLE[code][1]

    def line(self, index=0):
        return LINE_COLOR if self.legacy else self.theme.series_color(index)

    def fs(self, legacy_pt, role="small"):
        return legacy_pt if self.legacy else self.theme.font_pt(role)

    def label(self, identifier, unit):
        if self.legacy:
            name = identifier.split(":", 1)[-1]
            return f"{name} [{unit}]" if unit else name
        return self.theme.axis_label(identifier, unit)

    def name(self, identifier):
        return identifier.split(":", 1)[-1] if self.legacy else self.theme.label(identifier)

    def value(self, value, unit):
        return value if self.legacy else self.theme.to_display(value, unit)

    def figure(self, plt, legacy_figsize, **kwargs):
        if self.legacy:
            return plt.subplots(figsize=legacy_figsize, **kwargs)
        figsize, dpi = self.theme.canvas(self.size)
        return plt.subplots(figsize=figsize, dpi=dpi, **kwargs)

    def title(self, ax, text, **kwargs):
        if self.legacy or self.theme.title:
            ax.set_title(self.title_override or text, **kwargs)

    def legend(self, ax, **kwargs):
        if self.legacy:
            ax.legend(**kwargs)
            return
        if self.theme.legend == "none":
            return
        if self.theme.legend == "outside":
            kwargs = {**kwargs, "loc": "upper left", "bbox_to_anchor": (1.02, 1.0), "borderaxespad": 0.0}
        elif self.theme.background != "transparent":
            # inside the axes the legend gets the figure background, so a curve behind it stays readable
            kwargs = {**kwargs, "frameon": True, "facecolor": self.theme.colors["face"], "edgecolor": "none",
                      "framealpha": 0.88}
        ax.legend(**kwargs)


@contextmanager
def styled(style):
    plt = _pyplot()
    rc = LEGACY_RC if style.legacy else style.theme.rc()
    with plt.rc_context(rc):
        yield plt


def caption(source, out_dir, *, engine_build=None, catalog_version=None, language="en"):
    """Caption text: data file relative to the output directory, sha256 prefix, Engine build, catalog release."""
    parts = []
    if source is not None:
        parts.append(pres.text("caption.data", language, path=wc.relative(source, out_dir),
                               digest=wc.sha256_file(source)[:12]))
    parts.append(pres.text("caption.engine", language, build=engine_build or "unknown"))
    parts.append(pres.text("caption.catalog", language, version=catalog_version) if catalog_version
                 else pres.text("caption.no_catalog", language))
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


def save_themed(fig, out_dir, name, style, provenance_text, *, caption_text=None, layout="tight", companions=True):
    """Write the theme's formats. Captions in the figure only with captions "figure"; the provenance text goes into
    the figure only with provenance_in_figure, else to <name>.provenance.txt. Refuses text the fonts cannot draw."""
    import matplotlib.pyplot as plt
    from matplotlib.text import Text

    theme = style.theme
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    footer = []
    if theme.captions == "figure" and caption_text:
        footer.append(caption_text)
    if theme.provenance_in_figure and provenance_text:
        footer.append(provenance_text)
    if layout == "tight":
        fig.tight_layout()
        fit_legends(fig)
    if footer:
        fig.text(0.01, 0.01, "\n".join(footer), fontsize=theme.font_pt("caption"), color=theme.colors["muted"],
                 ha="left", va="bottom")
        lines = sum(text.count("\n") + 1 for text in footer)
        fig.subplots_adjust(bottom=min(0.45, fig.subplotpars.bottom + 0.045 * lines * theme.font_scale))
    pres.check_glyphs([artist.get_text() for artist in fig.findobj(Text)], theme)
    written = {}
    figsize, dpi = theme.canvas(style.size)
    for kind in theme.formats:
        path = out_dir / f"{name}.{kind}"
        metadata = {"png": {"Software": None}, "svg": {"Date": None, "Creator": None},
                    "pdf": {"CreationDate": None, "Creator": None, "Producer": None}}[kind]
        fig.savefig(path, dpi=dpi, metadata=metadata, transparent=theme.background == "transparent")
        written[kind] = path.as_posix()
    plt.close(fig)
    if companions and theme.captions == "separate" and caption_text:
        written["caption_file"] = wc.write_text_atomic(out_dir / f"{name}.caption.txt", caption_text + "\n").as_posix()
    if companions and provenance_text and not theme.provenance_in_figure:
        written["provenance_file"] = wc.write_text_atomic(out_dir / f"{name}.provenance.txt",
                                                          provenance_text + "\n").as_posix()
    written["caption"] = caption_text
    return written


def fit_legends(fig):
    """Shrink the axes so legends placed outside them stay on the canvas."""
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    right = max((legend.get_window_extent(renderer).x1 / fig.bbox.width for ax in fig.axes
                 for legend in [ax.get_legend()] if legend is not None), default=0.0)
    if right > 0.995:
        fig.subplots_adjust(right=max(0.3, fig.subplotpars.right - (right - 0.985)))


def finish(fig, out_dir, name, style, provenance_text, caption_text=None):
    if style.legacy:
        return save(fig, out_dir, name, provenance_text)
    return save_themed(fig, out_dir, name, style, style.provenance or provenance_text, caption_text=caption_text,
                       companions=style.companions)


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


def _band(ax, bounds, style, unit=None, horizontal=True):
    if not bounds or (bounds["minimum"] is None and bounds["maximum"] is None):
        return
    low = style.value(bounds["minimum"], unit)
    high = style.value(bounds["maximum"], unit)
    span = ax.axhspan if horizontal else ax.axvspan
    line = ax.axhline if horizontal else ax.axvline
    color = BAND_COLOR if style.legacy else style.theme.series_color(0)
    if low is not None and high is not None:
        span(low, high, color=color, alpha=0.12, label=style.t("legend.bounds", low=f"{low:.6g}", high=f"{high:.6g}"))
    for value, key in ((low, "legend.minimum"), (high, "legend.maximum")):
        if value is not None:
            line(value, color=color, linewidth=0.9, linestyle="--",
                 label=style.t(key, value=f"{value:.6g}") if low is None or high is None else None)


def study_column(summary, y):
    """Plotted values of a study column: rows, unit and the parameters (shared with compose and animate)."""
    rows = summary["rows"]
    if not rows or not any(y in row for row in rows):
        raise wc.WorkflowError("COLUMN_UNKNOWN", f"column {y!r} is not in the study table; columns: "
                               f"{[c for c in summary['columns'] if ':' in c and not c.endswith(':unit')]}")
    unit = next((row.get(f"{y}:unit") for row in rows if row.get(f"{y}:unit")), None)
    return rows, unit, summary["parameters"]


def plot_sweep(study_dir, y, *, out_dir=None, name=None, theme=None, size=None, caption_text=None, style=None):
    """Curve (1 parameter) or heatmap (2 parameters) of column ``y`` from a study's summary.json."""
    summary_path, summary = _study(study_dir)
    rows, unit, parameters = study_column(summary, y)
    out_dir = Path(out_dir) if out_dir else Path(study_dir) / "plots"
    style = style or Style(theme, size)
    releases = sorted({r["catalog_version"] for r in rows if r.get("catalog_version")})
    text = caption(summary_path, out_dir, engine_build=summary["identity"]["engine"]["engine_build_id"],
                   catalog_version=", ".join(releases) or None, language=style.language)
    stem = name or ("sweep_" if len(parameters) == 1 else "heatmap_") + y.replace(":", "_")
    bounds = _bounds(study_dir, rows, y.split(":", 1)[1]) if y.startswith("target:") else None
    if caption_text is None and not style.legacy and len(parameters) == 1:
        caption_text = style.t("caption.sweep", y=style.name(y), x=style.name(f"param:{parameters[0]['name']}"),
                               count=len(rows), passed=sum(1 for row in rows if row["outcome"] == "PASS"))
    with styled(style) as plt:
        if len(parameters) == 1:
            fig, ax = style.figure(plt, (6.4, 4.2))
            info = draw_curve(ax, rows, parameters[0], y, unit, bounds, style)
        else:
            fig, ax = style.figure(plt, (6.4, 4.8))
            info = draw_heatmap(fig, ax, rows, parameters, y, unit, bounds, style)
        return {**info, "bounds": bounds, "unit": unit,
                "out": finish(fig, out_dir, stem, style, text, caption_text)}


def _has_exact_plot_value(row, column):
    value = row.get(column)
    return (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
            and row.get(f"{column}:exact") is not False)


def draw_curve(ax, rows, parameter, y, unit, bounds, style, *, y_limits=None):
    """One-parameter curve on ``ax``; ``y_limits`` (display units) fixes the value axis across panels."""
    x_col = f"param:{parameter['name']}"
    x_unit = parameter["unit"]
    valued = [(style.value(r[x_col], x_unit), style.value(r[y], unit)) for r in rows if _has_exact_plot_value(r, y)]
    if valued:
        ordered = sorted(rows, key=lambda row: row[x_col])
        xs = [style.value(row[x_col], x_unit) for row in ordered]
        ys = [style.value(row[y], unit) if _has_exact_plot_value(row, y) else float("nan") for row in ordered]
        ax.plot(xs, ys, color=style.line(0), linewidth=1.5, zorder=2, label=style.name(y))
    _band(ax, bounds, style, unit)
    values = [v for _x, v in valued]
    if y_limits is not None:
        low, high = y_limits
    else:
        low, high = (min(values), max(values)) if values else (0.0, 1.0)
    span = (high - low) or abs(high) or 1.0
    lower = low - 0.12 * span  # position of the valueless samples: below every value, never on a real point
    marker_size = 46 if style.legacy else 46 * style.theme.font_scale * (style.theme.base_font_pt / 9.0)
    counts = {}
    for outcome, (marker, color, _label) in STATUS_STYLE.items():
        pts = [r for r in rows if r["outcome"] == outcome]
        if not pts:
            continue
        counts[outcome] = len(pts)
        label = style.outcome(outcome)
        with_value = [(style.value(r[x_col], x_unit), style.value(r[y], unit)) for r in pts
                      if _has_exact_plot_value(r, y)]
        without = [style.value(r[x_col], x_unit) for r in pts if not _has_exact_plot_value(r, y)]
        if with_value:
            ax.scatter(*zip(*with_value), marker=marker, s=marker_size, color=color, edgecolors="white",
                       linewidths=0.8, zorder=3, label=style.t("legend.count", label=label, count=len(pts)))
        if without:
            ax.scatter(without, [lower] * len(without), marker=marker, s=marker_size, facecolors="none",
                       edgecolors=color, linewidths=1.2, zorder=3, clip_on=False,
                       label=style.t("legend.no_exact", label=label, count=len(without)))
    missing = len(rows) - len(valued)
    if missing:
        ax.axhline(lower, color="#8a8a8a", linewidth=0.8, linestyle=":", zorder=1)
        ax.annotate(style.t("note.no_exact_row"), xy=(0.0, lower), xycoords=("axes fraction", "data"),
                    xytext=(2, 3), textcoords="offset points", fontsize=style.fs(6.5, "caption"),
                    color="#555555" if style.legacy else style.theme.colors["muted"])
    if y_limits is not None:
        pad = 0.05 * span
        ax.set_ylim(min(low, lower if missing else low) - pad, high + pad)
    ax.set_xlabel(style.label(x_col, x_unit))
    ax.set_ylabel(style.label(y, unit))
    style.title(ax, style.t("title.sweep", y=style.name(y), x=style.name(x_col)))
    style.legend(ax, fontsize=style.fs(7), loc="best", frameon=False)
    return {"points": len(rows), "with_value": len(valued), "without_value": missing,
            "no_value_position": lower if missing else None, "by_outcome": counts,
            "x": {"column": x_col, "unit": style.theme.display_unit(x_unit) if not style.legacy else x_unit},
            "y": {"column": y, "unit": style.theme.display_unit(unit) if not style.legacy else unit}}


def draw_heatmap(fig, ax, rows, parameters, y, unit, bounds, style, *, color_limits=None):
    """Two-parameter heatmap on ``ax``; ``color_limits`` (display units) shares the colour scale across panels."""
    import numpy as np

    px, py = parameters
    xs = sorted({r[f"param:{px['name']}"] for r in rows})
    ys = sorted({r[f"param:{py['name']}"] for r in rows})
    grid = np.full((len(ys), len(xs)), np.nan)
    for r in rows:
        if _has_exact_plot_value(r, y):
            grid[ys.index(r[f"param:{py['name']}"]), xs.index(r[f"param:{px['name']}"])] = style.value(r[y], unit)
    mesh = ax.pcolormesh(np.arange(len(xs) + 1) - 0.5, np.arange(len(ys) + 1) - 0.5, np.ma.masked_invalid(grid),
                         cmap="Blues", shading="flat", edgecolors="white" if style.theme.background != "dark"
                         else style.theme.colors["face"], linewidth=1.0)
    limits = [style.value(v, unit) for v in ((bounds or {}).get("minimum"), (bounds or {}).get("maximum"))
              if v is not None]
    if color_limits is not None:
        mesh.set_clim(*color_limits)
    elif limits and np.isfinite(grid).any():  # the colour scale reaches the bounds, so their lines are visible
        mesh.set_clim(min(np.nanmin(grid), *limits), max(np.nanmax(grid), *limits))
    bar = fig.colorbar(mesh, ax=ax)
    bar.set_label(style.label(y, unit))
    drawn = []
    for value, key in (((bounds or {}).get("minimum"), "legend.minimum"),
                       ((bounds or {}).get("maximum"), "legend.maximum")):
        if value is not None:
            shown = style.value(value, unit)
            bar.ax.axhline(shown, color="#d03b3b", linewidth=1.4, linestyle="--")
            drawn.append(style.t(key, value=f"{shown:.6g}"))
    for outcome, (marker, color, _label) in STATUS_STYLE.items():
        pts = [r for r in rows if r["outcome"] == outcome]
        if pts:
            ax.scatter([xs.index(r[f"param:{px['name']}"]) for r in pts],
                       [ys.index(r[f"param:{py['name']}"]) for r in pts], marker=marker, s=30, color=color,
                       edgecolors="white", linewidths=0.6,
                       label=style.t("legend.count", label=style.outcome(outcome), count=len(pts)))
    ax.set_xticks(range(len(xs)), [f"{style.value(v, px['unit']):.6g}" for v in xs])
    ax.set_yticks(range(len(ys)), [f"{style.value(v, py['unit']):.6g}" for v in ys])
    ax.set_xlabel(style.label(f"param:{px['name']}", px["unit"]))
    ax.set_ylabel(style.label(f"param:{py['name']}", py["unit"]))
    ax.grid(False)
    if style.legacy:
        ax.set_title(f"{y.split(':', 1)[1]} (empty cell: no exact value)" + (
            f"; {', '.join(drawn)} (dashed on the colour bar)" if drawn else ""), fontsize=8)
    else:
        style.title(ax, f"{style.name(y)} ({style.t('note.empty_cell')})")
    style.legend(ax, fontsize=style.fs(6.5), loc="upper left", bbox_to_anchor=(0.0, -0.14), ncol=3, frameon=False)
    if style.legacy:
        fig.subplots_adjust(bottom=0.3)
    return {"points": len(rows), "with_value": int(np.isfinite(grid).sum()),
            "without_value": len(rows) - int(np.isfinite(grid).sum()), "bound_lines": drawn}


def plot_bars(labels, values, unit, out_dir, name, *, title, ylabel=None, source=None, engine_build=None,
              catalog_version=None, errors=None, theme=None, size=None, caption_text=None, style=None):
    """Signed horizontal bar chart (e.g. sensitivities per parameter). ``values`` None are labelled "no value"."""
    style = style or Style(theme, size)
    with styled(style) as plt:
        if style.legacy:
            fig, ax = plt.subplots(figsize=(6.4, max(2.4, 0.42 * len(labels) + 1.4)))
        else:
            fig, ax = style.figure(plt, None)
        shown = [v if isinstance(v, (int, float)) else 0.0 for v in values]
        colors = [style.line(0) if v >= 0 else (style.line(1) if not style.legacy else "#e8743b") for v in shown]
        ax.barh(range(len(labels)), shown, color=colors, height=0.6, xerr=errors, edgecolor="white")
        for i, value in enumerate(values):
            ax.text(shown[i], i, f" {value:.4g}" if isinstance(value, (int, float)) else f" {style.t('note.no_value')}",
                    va="center", ha="left" if shown[i] >= 0 else "right", fontsize=style.fs(7),
                    color="#333333" if style.legacy else style.theme.colors["text"])
        ax.axvline(0.0, color="#555555", linewidth=0.8)
        ax.set_yticks(range(len(labels)), labels)
        ax.invert_yaxis()
        ax.set_xlabel(ylabel or (f"{style.t('axis.value')} [{unit}]" if unit else style.t("axis.value")))
        style.title(ax, title)
        if style.legacy:
            fig.tight_layout(rect=(0, 0.08, 1, 1))  # room for long bar labels on the left
        text = caption(source, out_dir, engine_build=engine_build, catalog_version=catalog_version,
                       language=style.language)
        return {"out": finish(fig, out_dir, name, style, text, caption_text)}


def plot_histogram(values, unit, out_dir, name, *, title, xlabel=None, bounds=None, bins=20, source=None,
                   engine_build=None, catalog_version=None, theme=None, size=None, caption_text=None,
                   style=None):
    """Histogram of finite ``values`` with optional bounds band; the counts of all and of plotted values are shown."""
    style = style or Style(theme, size)
    finite = [v for v in values if isinstance(v, (int, float)) and v == v]
    with styled(style) as plt:
        fig, ax = style.figure(plt, (6.4, 4.0))
        if finite:
            ax.hist(finite, bins=bins, color=style.line(0), edgecolor="white", linewidth=0.8,
                    label="samples" if style.legacy else style.t("axis.samples"))
        _band(ax, bounds, style, None, horizontal=False)
        ax.set_xlabel(xlabel or (f"{style.t('axis.value')} [{unit}]" if unit else style.t("axis.value")))
        ax.set_ylabel(style.t("axis.samples"))
        if style.legacy:
            ax.set_title(f"{title} (plotted {len(finite)} of {len(values)} values)")
        else:
            style.title(ax, f"{title} ({len(finite)}/{len(values)})")
        style.legend(ax, fontsize=style.fs(7), frameon=False)
        text = caption(source, out_dir, engine_build=engine_build, catalog_version=catalog_version,
                       language=style.language)
        return {"out": finish(fig, out_dir, name, style, text, caption_text), "plotted": len(finite),
                "total": len(values)}


def power_data(run_dir):
    """Monitors, power budget and identity of a saved run (public RunResults API; no simulation)."""
    from os_optics.results import load_run

    results = load_run(Path(run_dir))
    return results.monitors(), results.power_budget(), results.identity()


def draw_power(left, right, monitors, budget, style):
    """Received power per observation plane (left) and power-ledger sinks (right)."""
    names = [m["observation_id"] for m in monitors]
    color = style.line(0)
    for index, monitor in enumerate(monitors):
        state, value = monitor["quantity_state"], monitor["total_power_W"]
        if state in ("KNOWN", "KNOWN_ZERO") and value is not None:
            left.bar(index, value, color=color, width=0.6, edgecolor="white")
        elif state == "BOUNDED" and value is not None:
            upper = monitor.get("power_upper_bound_W")
            left.bar(index, value, color="none", hatch="//", edgecolor=color, width=0.6)
            if upper is not None:
                left.errorbar(index, value, yerr=[[0.0], [max(0.0, upper - value)]],
                              fmt="none", color=color, capsize=4)
            left.annotate(style.t("legend.bounded") if upper is not None else style.t("legend.lower_bound"),
                          (index, value), xytext=(0, 4), textcoords="offset points", ha="center",
                          fontsize=style.fs(7))
        else:
            # Axes coordinates mark unavailable data without placing a false zero on the power axis.
            left.text(index, 0.02, state or "unavailable", transform=left.get_xaxis_transform(),
                      ha="center", va="bottom", fontsize=style.fs(7), rotation=90)
    left.set_xticks(range(len(names)), [style.name(n) for n in names], rotation=20)
    left.set_xlim(-0.5, max(0.5, len(names) - 0.5))
    left.set_ylabel(style.t("axis.power_W"))
    if style.legacy:
        left.set_title("observation planes\nsolid: known; hatched: lower bound", fontsize=9)
    else:
        style.title(left, f"{style.t('title.power_planes')}\n{style.t('title.power_planes_note')}",
                    fontsize=style.fs(9, "body"))
    total = budget.get("total_input_W")
    if total:
        left.axhline(total, color="#555555", linestyle="--", linewidth=0.9,
                     label=style.t("legend.total_input", value=f"{total:.6g}"))
        style.legend(left, fontsize=style.fs(7), frameon=False)
    sinks = budget.get("total_sinks") or {}
    right.barh(range(len(sinks)), list(sinks.values()), color="#8a8a8a", height=0.6, edgecolor="white")
    right.set_yticks(range(len(sinks)), list(sinks))
    right.set_xlabel(style.t("axis.power"))
    if style.legacy:
        right.set_title("power ledger sinks")
    else:
        style.title(right, style.t("title.power_sinks"), fontsize=style.fs(9, "body"))
    return sinks


def plot_power(run_dir, *, out_dir=None, name="power_distribution", theme=None, size=None, caption_text=None,
               style=None):
    """Power received per observation plane and the power-ledger sinks of one saved run (W)."""
    run_dir = Path(run_dir)
    monitors, budget, identity = power_data(run_dir)
    out_dir = Path(out_dir) if out_dir else run_dir.parent / "plots"
    style = style or Style(theme, size)
    if caption_text is None and not style.legacy:
        caption_text = style.t("caption.power")
    with styled(style) as plt:
        fig, (left, right) = style.figure(plt, (8.0, 3.8), ncols=2, gridspec_kw={"width_ratios": [3, 2]})
        sinks = draw_power(left, right, monitors, budget, style)
        text = caption(run_dir / "simulation_result.json", out_dir, engine_build=identity.get("engine_build_id"),
                       catalog_version=identity.get("catalog_version"), language=style.language)
        if style.legacy:
            fig.tight_layout(rect=(0, 0.08, 1, 1))
        return {"out": finish(fig, out_dir, name, style, text, caption_text),
                "observations": {m["observation_id"]: {key: m.get(key) for key in (
                    "total_power_W", "quantity_state", "power_complete", "power_upper_bound_W")} for m in monitors},
                "sinks_W": sinks, "conserved": budget.get("conserved")}


def theme_from_arguments(theme_arg, settings):
    """``--theme`` (preset name, a theme JSON object file, or a presentation.json) plus ``--set`` theme keys."""
    if theme_arg is None and not settings:
        return None
    base = {"preset": "presentation"}
    if theme_arg in pres.PRESETS:
        base = {"preset": theme_arg}
    elif theme_arg is not None:
        document = wc.read_json(theme_arg)
        base = document.get("theme", {}) if document.get("schema") == pres.SPEC_SCHEMA else document
    overrides = {}
    for item in settings or []:
        key, sep, value = item.partition("=")
        if not sep:
            raise pres.PresentationError("SETTING_INVALID", f"--set {item!r}: use KEY=VALUE, e.g. language=zh-CN")
        overrides[key.removeprefix("theme.")] = pres.parse_value(value)
    return pres.resolve_theme(base, overrides)


def main(argv=None):
    def workflow():
        parser = wc.ArgumentParser(prog=PROG, description=__doc__,
                                    formatter_class=argparse.RawDescriptionHelpFormatter)
        parser.add_argument("kind", choices=["sweep", "power", "bars", "histogram"], help="Plot kind")
        parser.add_argument("path", type=Path, help="Study directory, run directory or data JSON")
        parser.add_argument("--y", help="sweep: column to plot (target:<id>, derived:<name>)")
        parser.add_argument("--out", type=Path, help="Output directory")
        parser.add_argument("--name", help="Output file stem")
        parser.add_argument("--theme", help="presentation, report, or a JSON file with a theme or a presentation.json")
        parser.add_argument("--set", action="append", metavar="KEY=VALUE", help="theme setting, e.g. language=zh-CN")
        args = parser.parse_args(argv)
        theme = theme_from_arguments(args.theme, args.set)
        if args.kind == "sweep":
            if not args.y:
                raise ValueError("sweep needs --y COLUMN")
            result = plot_sweep(args.path, args.y, out_dir=args.out, name=args.name, theme=theme)
        elif args.kind == "power":
            result = plot_power(args.path, out_dir=args.out, name=args.name or "power_distribution", theme=theme)
        else:
            data = wc.read_json(args.path)
            out = args.out or args.path.parent / "plots"
            common = {"source": args.path, "engine_build": data.get("engine_build_id"),
                      "catalog_version": data.get("catalog_version"), "theme": theme}
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
