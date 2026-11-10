"""Comparison figures and multi-panel layouts for ``os-optics-kit present`` (plot type ``targets``, asset kind
``compose``). Everything is drawn from saved runs and from assets built earlier in the same build; nothing is
simulated and nothing is written outside the asset's own folder.

Plot type ``targets``::

    {"id": "targets-compare", "kind": "plot", "type": "targets", "sources": ["swapped", "final", "nominal"],
     "targets": ["expanded_waist"], "reference": "swapped"}

One panel per target (and per unit, when runs report a target in different units), one point per run in the
target's unit (converted to the theme's display unit), the target bounds as a band, the assessment of every run by
colour, marker and text. A bounded or indeterminate value is drawn hollow and labelled; a target that a run does not
have is marked "missing" at that run's position, never as a value. Values, units and deltas come from
``compare_runs.compare``; ``reference`` adds the difference to that run.

Asset kind ``compose``::

    {"id": "before-after", "kind": "compose", "layout": "before-after",
     "panels": [{"asset": "view-swapped"}, {"asset": "view-final", "label": {"en": "After", "zh-CN": "调整后"}}],
     "spacing": 0.03, "panel_letters": true, "shared": {"y": "auto", "color": "auto", "framing": true}}

Layouts: ``row``, ``column``, ``grid`` (``columns``, a panel may ``span`` several columns, optional ``heights``),
``before-after`` (two panels with an arrow, labelled Before/After unless labels are given) and ``system-plot`` (a
view and a plot side by side, the view wider). Raster panels (views, animation stills, other compositions) are
placed with their aspect ratio; sweep, target and power plots are re-drawn from their data so that ``shared.y`` /
``shared.color`` give one value axis / colour scale across panels. Scales are shared only when the display units
are equal (``auto``: also the same quantity); ``details.scales`` states what was applied and why. Shared framing of
views comes from the view assets' ``framing_sources`` (``present init`` sets it for several runs); the composition
reports ``details.framing`` as ``shared`` or ``independent`` from the views' recorded boxes.

Outputs in ``assets/<id>/``: ``<id>.png`` (+ ``.svg`` / ``.pdf`` per theme, raster panels embedded),
``panel-<n>.png`` per panel at its pixel size (re-drawn plots also ``panel-<n>.svg``), ``layout.json`` (order,
labels, spacing, applied scales, pixel boxes) and ``data.json`` (the plotted data). Reorder, relabel or restyle by
editing the specification and building again.
"""

from __future__ import annotations

from contextlib import contextmanager
import dataclasses
import logging
import math
from pathlib import Path
from typing import Any

from . import presentation as pres
from . import workflow_common as wc

LAYOUTS = ("row", "column", "grid", "before-after", "system-plot")
DEFAULT_SPACING = 0.03
ARROW_SPACING = 0.07
SYSTEM_VIEW_RATIO = 1.4
LAYOUT_SCHEMA = "os-optics-kit.compose-layout"
TARGETS_SCHEMA = "os-optics-kit.targets-plot"
MAX_TARGET_COLUMNS = 3
#: states of a run's value on a target panel; only the first three have a position on the value axis
VALUE_STATES = ("exact", "bounded", "inexact")
SCALE_CHOICES = "auto | true | false | [min, max] in display units"

#: text of this module in both languages (keys that are not here come from presentation.TEXT)
TEXT: dict[str, dict[str, str]] = {
    "en": {
        "compose.before": "Before",
        "compose.after": "After",
        "compose.label_with": "{label}: {detail}",
        "title.before_after": "Before and after",
        "title.compare": "Comparison: {labels}",
        "title.system_plot": "{view} and {plot}",
        "title.targets_unit": "{target} [{unit}]",
        "caption.targets_runs": "The chart shows {targets} for {runs} with the target bounds.",
        "caption.targets_runs_many": "The chart shows {count} targets for {runs} with their bounds.",
        "caption.targets_target": "For {target}, {parts}.",
        "caption.targets_run_all": "{run} meets all {count} targets.",
        "caption.targets_run_some": "{run} meets {met} of {count} targets",
        "caption.targets_run_outside": "outside the bounds: {targets}",
        "caption.targets_run_inexact": "no exact value: {targets}",
        "caption.targets_run_no_value": "no value: {targets}",
        "caption.targets_run_missing": "not reported: {targets}",
        "caption.targets_run_other": "{code}: {targets}",
        "caption.targets_met": "{runs} {verb} within bounds",
        "caption.targets_unmet": "{runs} {verb} outside the bounds",
        "caption.targets_inexact": "{runs} {have} no exact value",
        "caption.targets_no_value": "{runs} {have} no value",
        "caption.targets_other": "{runs} {verb} {code}",
        "caption.targets_missing": "{runs} {do} not report this target",
        "caption.before_after": "The left panel shows {before} before the change and the right panel shows {after} "
                                "after it.",
        "caption.panels_row": "The panels show {labels} side by side.",
        "caption.panels_column": "The panels show {labels} one above the other.",
        "caption.panels_grid": "The panels show {labels}.",
        "caption.system_plot": "The system view of {view} is shown next to {plot}.",
        "caption.same_camera_framing": "The views share one camera and one framing.",
        "caption.same_camera": "The views share one camera; their framing differs.",
        "caption.same_framing": "The views share one framing; their cameras differ.",
        "caption.shared_axis": "The plots share one value axis covering {low} to {high} {unit}.",
        "caption.shared_axis_exact": "The plots share one value axis from {low} to {high} {unit}.",
        "caption.shared_color": "The heatmaps share one colour scale from {low} to {high} {unit}.",
        "legend.bounds_per_run": "target bounds per run",
        "label.reference": "reference",
        "note.inexact": "no exact value",
    },
    "zh-CN": {
        "compose.before": "调整前",
        "compose.after": "调整后",
        "compose.label_with": "{label}：{detail}",
        "title.before_after": "调整前后对比",
        "title.compare": "对比：{labels}",
        "title.system_plot": "{view}与{plot}",
        "title.targets_unit": "{target} [{unit}]",
        "caption.targets_runs": "图中显示{runs}的{targets}及目标范围。",
        "caption.targets_runs_many": "图中显示{runs}的 {count} 个目标值及目标范围。",
        "caption.targets_target": "{target}：{parts}。",
        "caption.targets_run_all": "{run}满足全部 {count} 个目标。",
        "caption.targets_run_some": "{run}满足 {count} 个目标中的 {met} 个",
        "caption.targets_run_outside": "超出范围：{targets}",
        "caption.targets_run_inexact": "无精确值：{targets}",
        "caption.targets_run_no_value": "无数值：{targets}",
        "caption.targets_run_missing": "未报告：{targets}",
        "caption.targets_run_other": "{code}：{targets}",
        "caption.targets_met": "{runs}在范围内",
        "caption.targets_unmet": "{runs}超出范围",
        "caption.targets_inexact": "{runs}没有精确值",
        "caption.targets_no_value": "{runs}没有数值",
        "caption.targets_other": "{runs}为 {code}",
        "caption.targets_missing": "{runs}没有该目标",
        "caption.before_after": "左图为调整前的{before}，右图为调整后的{after}。",
        "caption.panels_row": "各面板并列显示{labels}。",
        "caption.panels_column": "各面板自上而下显示{labels}。",
        "caption.panels_grid": "各面板显示{labels}。",
        "caption.system_plot": "{view}的系统视图与{plot}并排显示。",
        "caption.same_camera_framing": "各视图使用相同的相机与取景范围。",
        "caption.same_camera": "各视图使用相同的相机，取景范围不同。",
        "caption.same_framing": "各视图使用相同的取景范围，相机不同。",
        "caption.shared_axis": "各图共用一个数值轴，涵盖 {low} 至 {high} {unit}。",
        "caption.shared_axis_exact": "各图共用一个数值轴，范围为 {low} 至 {high} {unit}。",
        "caption.shared_color": "各热图共用一个色标，范围为 {low} 至 {high} {unit}。",
        "legend.bounds_per_run": "各运行的目标范围",
        "label.reference": "参考",
        "note.inexact": "无精确值",
    },
}


def text(key, language, **values):
    """Text of this module (``TEXT``), else of ``presentation.TEXT``, formatted with ``values``."""
    table = TEXT.get(language) or TEXT["en"]
    if key in table:
        return pres.tidy(table[key].format(**values), language)
    return pres.text(key, language, **values)


def sentence(value, language):
    """English sentences start with a capital letter, also when they begin with a label such as ``mirror``."""
    if language == "en" and value[:1].islower():
        return value[:1].upper() + value[1:]
    return value


def join_names(names, language, *, enumeration=False):
    """``A``, ``A and B``, ``A, B and C`` (``A和B``, ``A、B和C``; with ``enumeration`` ``A、B、C``, so a sentence that
    already joins with 和 does not use it twice)."""
    names = [str(n) for n in names]
    if len(names) <= 1:
        return "".join(names)
    if language == "zh-CN":
        return "、".join(names) if enumeration else "、".join(names[:-1]) + "和" + names[-1]
    return ", ".join(names[:-1]) + " and " + names[-1]


def settings():
    """Fields of the plot type ``targets`` and the asset kind ``compose`` (merged into ``present settings``)."""
    return {
        "plot": {"targets": {
            "sources": "[run source ids]: one point per run, labelled with the source label",
            "targets": "[target ids] (default: every target of the runs, in first-seen order)",
            "reference": "run source id: show the difference of every run to it (optional)"}},
        "compose": {
            "layout": {"row": "panels side by side", "column": "panels one above the other",
                       "grid": "columns per row (columns); a panel may span several columns",
                       "before-after": "two panels with an arrow, labelled Before/After unless labels are given",
                       "system-plot": "a view and a plot side by side, the view wider"},
            "panels": "[{\"asset\": id, \"label\": text or {en, zh-CN} (default: the panel's source label, else its "
                      "title), \"span\": columns (grid)}] in display order",
            "columns": "grid: number of columns (default: about the square root of the panel count)",
            "heights": "grid/column: relative row heights (default: views fitted, plots share the rest)",
            "spacing": DEFAULT_SPACING, "panel_letters": "true: a, b, c before the panel labels",
            "trim": "true: crop uniform margins of raster panels (views sharing one framing get one common crop)",
            "shared": {"y": f"{SCALE_CHOICES}: one value axis for re-drawn sweep and target plots",
                       "color": f"{SCALE_CHOICES}: one colour scale for re-drawn heatmaps",
                       "framing": "true: views of different runs use one framing box (set framing_sources on the "
                                  "view assets; present init does so for several runs)"},
            "outputs": "<id>.png/.svg/.pdf, panel-<n>.png (+ .svg for re-drawn plots), layout.json, data.json"},
    }


def _fail(code, message, recovery=None, **details):
    return pres.PresentationError(code, message, recovery=recovery, **details)


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _fmt(value):
    """Numbers in figures and captions: 6 significant digits, trailing zeros trimmed (presentation.fmt_number)."""
    return pres.fmt_number(value)


def _fmt_signed(value):
    text_value = _fmt(value)
    return text_value if text_value.startswith("-") else f"+{text_value}"


def _denoise(value, scale):
    """``value`` with round-off noise (below 1e-9 of the panel's scale) shown as 0."""
    if _number(value) and _number(scale) and scale > 0 and abs(value) < 1e-9 * scale:
        return 0.0
    return value


def _localized_all(value, default):
    return {lang: pres.localized(value, lang, default) for lang in pres.LANGUAGES}


def _source_index(ctx, source_id):
    sources = list((ctx.spec.get("sources") or {}))
    return sources.index(source_id) if source_id in sources else 0


# --------------------------------------------------------------------------------------------- figure helpers

def _style(theme):
    from . import plot_results as pr

    return pr.Style(theme, companions=False)


@contextmanager
def drawing(theme):
    """``plot_results.styled`` for ``theme`` without matplotlib's font-weight fallback notices on stderr."""
    from . import plot_results as pr

    logger = logging.getLogger("matplotlib.font_manager")
    level = logger.level
    logger.setLevel(logging.ERROR)
    try:
        with pr.styled(_style(theme)) as plt:
            yield plt
    finally:
        logger.setLevel(level)


def new_figure(plt, theme, size=None):
    """A figure of the theme's pixel size (or ``size`` [w, h] px) with constrained layout."""
    if size is None:
        figsize, dpi = theme.canvas()
    else:
        dpi = theme.canvas()[1]
        figsize = (size[0] / dpi, size[1] / dpi)
    fig = plt.figure(figsize=figsize, dpi=dpi, layout="constrained")
    pad = 0.012 * pres.CANVAS_HEIGHT_IN
    fig.get_layout_engine().set(w_pad=pad, h_pad=pad, wspace=0.0, hspace=0.0)
    return fig


def _wrap(value, width_px, font_pt, dpi):
    """Wrap ``value`` to lines of about ``width_px`` (Latin characters ~0.55 em, CJK 1 em)."""
    em = font_pt * dpi / 72
    lines = []
    for paragraph in str(value).split("\n"):
        line, used = "", 0.0
        for word in _tokens(paragraph):
            size = sum(1.0 if ord(c) > 0x2E80 else 0.56 for c in word) * em
            if line and used + size > width_px:
                lines.append(line.rstrip())
                line, used = word.lstrip(), size
            else:
                line, used = line + word, used + size
        lines.append(line.rstrip())
    return "\n".join(lines)


def _tokens(paragraph):
    """Words with their leading space; every CJK character is its own token."""
    tokens: list[str] = []
    current = ""
    for char in paragraph:
        if ord(char) > 0x2E80:
            if current:
                tokens.append(current)
            tokens.append(char)
            current = ""
        elif char == " ":
            if current:
                tokens.append(current)
            current = " "
        else:
            current += char
    if current:
        tokens.append(current)
    return tokens


def frame(fig, theme, title, footer):
    """Figure title (theme.title) and footer text (caption with captions "figure", provenance with
    provenance_in_figure); constrained layout keeps room for both."""
    width_px = fig.get_figwidth() * fig.dpi
    if title and theme.title:
        fig.suptitle(_wrap(title, 0.96 * width_px, theme.font_pt("title"), fig.dpi), x=0.012, ha="left",
                     fontsize=theme.font_pt("title"), fontweight="bold")
    if footer:
        fig.supxlabel(_wrap(footer, 0.96 * width_px, theme.font_pt("caption"), fig.dpi), x=0.012, ha="left",
                      fontsize=theme.font_pt("caption"), color=theme.colors["muted"])


def footer_text(theme, caption, provenance):
    parts = []
    if theme.captions == "figure" and caption:
        parts.append(caption)
    if theme.provenance_in_figure and provenance:
        parts.append(provenance)
    return "\n".join(parts)


def _undrawn_tick_labels(fig):
    """Tick labels matplotlib keeps for ticks outside the view interval (not drawn)."""
    hidden = set()
    for ax in fig.get_axes():
        for axis in (ax.xaxis, ax.yaxis):
            low, high = sorted(axis.get_view_interval())
            span = (high - low) or 1.0
            for tick in [*axis.get_major_ticks(), *axis.get_minor_ticks()]:
                loc = tick.get_loc()
                if loc is None or not low - 1e-9 * span <= loc <= high + 1e-9 * span:
                    hidden.update({id(tick.label1), id(tick.label2)})
    return hidden


def text_overflow(fig, *, cell_pad=0.0):
    """Drawn texts that leave the canvas or, in a composition, their panel cell (``cell_pad`` px tolerance):
    ``[(text, [x, y, w, h])]``. Axis labels, tick labels, titles, legends and annotations are all checked."""
    from matplotlib.text import Text

    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    width, height = fig.bbox.width, fig.bbox.height
    hidden = _undrawn_tick_labels(fig)
    out = []
    seen = set()

    def check(artist, x0, y0, x1, y1):
        if id(artist) in hidden or id(artist) in seen or not artist.get_visible() or not artist.get_text().strip():
            return
        box = artist.get_window_extent(renderer)
        if box.x0 < x0 - 1 or box.y0 < y0 - 1 or box.x1 > x1 + 1 or box.y1 > y1 + 1:
            seen.add(id(artist))
            out.append((artist.get_text(), [round(float(v), 1) for v in box.bounds]))

    for sub in getattr(fig, "subfigs", []):
        cell = sub.bbox
        for artist in sub.findobj(Text):
            check(artist, max(0.0, cell.x0 - cell_pad), max(0.0, cell.y0 - cell_pad), min(width, cell.x1 + cell_pad),
                  min(height, cell.y1 + cell_pad))
    for artist in fig.findobj(Text):
        check(artist, 0.0, 0.0, width, height)
    return out


LEGEND_SPOTS = ("upper right", "upper left", "lower right", "lower left", "center right", "center left",
                "upper center", "lower center")


def _data_boxes(ax, renderer):
    """Display boxes of the data on ``ax``: curve paths, scatter markers and bars (reference lines excluded)."""
    from matplotlib.collections import PathCollection
    from matplotlib.transforms import Bbox

    paths, boxes = [], []
    for line in ax.get_lines():
        if len(line.get_xdata()) > 2 and line.get_visible():
            paths.append(line.get_transform().transform_path(line.get_path()))
    for collection in ax.collections:
        if isinstance(collection, PathCollection) and collection.get_visible():
            sizes = collection.get_sizes()
            radius = (math.sqrt(float(max(sizes))) if len(sizes) else 6.0) * ax.figure.dpi / 72 / 2
            for x, y in collection.get_offset_transform().transform(collection.get_offsets()):
                boxes.append(Bbox.from_extents(x - radius, y - radius, x + radius, y + radius))
    for container in ax.containers:
        for patch in getattr(container, "patches", []):
            boxes.append(patch.get_window_extent(renderer))
    return paths, boxes


def legend_over_data(ax, renderer, box=None):
    """True when the legend of ``ax`` (or ``box``) covers a curve, a marker or a bar of that axes."""
    legend = ax.get_legend()
    if box is None:
        if legend is None or not legend.get_visible():
            return False
        box = legend.get_window_extent(renderer)
    paths, boxes = _data_boxes(ax, renderer)
    return any(path.intersects_bbox(box, filled=False) for path in paths) or any(
        _overlaps(box, other) for other in boxes)


def _free_spot(ax, legend, renderer, dpi, spots=LEGEND_SPOTS):
    from matplotlib.transforms import Bbox

    size = legend.get_window_extent(renderer)
    frame_box = ax.get_window_extent(renderer)
    pad = legend.borderaxespad * legend.prop.get_size_in_points() * dpi / 72
    width, height = size.width, size.height
    if width + 2 * pad > frame_box.width or height + 2 * pad > frame_box.height:
        return None
    for spot in spots:
        vertical, _, horizontal = spot.partition(" ")
        x0 = {"left": frame_box.x0 + pad, "right": frame_box.x1 - pad - width,
              "center": (frame_box.x0 + frame_box.x1 - width) / 2}[horizontal]
        y0 = {"upper": frame_box.y1 - pad - height, "lower": frame_box.y0 + pad,
              "center": (frame_box.y0 + frame_box.y1 - height) / 2}[vertical]
        if not legend_over_data(ax, renderer, Bbox.from_bounds(x0, y0, width, height)):
            return spot
    return None


def place_legend(fig, ax, *, headroom=True):
    """Move the legend of ``ax`` to a place inside the axes where it covers no data: a free corner, else (unless the
    value range is fixed) a free band added above the data; only when neither exists, beside the axes."""
    legend = ax.get_legend()
    if legend is None or not legend.get_visible():
        return None
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    spot = _free_spot(ax, legend, renderer, fig.dpi)
    if spot is None and headroom:
        frame_box = ax.get_window_extent(renderer)
        pad = legend.borderaxespad * legend.prop.get_size_in_points() * fig.dpi / 72
        needed = legend.get_window_extent(renderer).height + 2 * pad
        if needed < 0.6 * frame_box.height:
            low, high = ax.get_ylim()
            ax.set_ylim(low, high + needed * (high - low) / max(frame_box.height - needed, 1.0))
            fig.canvas.draw()
            spot = _free_spot(ax, legend, renderer, fig.dpi, ("upper right", "upper left", "upper center"))
            if spot is None:
                ax.set_ylim(low, high)
    if spot is not None:
        legend.set_loc(spot)
        return spot
    legend.set_loc("upper left")
    legend.set_bbox_to_anchor((1.02, 1.0), transform=ax.transAxes)
    return "outside"


def save_figure(fig, out_dir, name, theme, *, formats=None):
    """Write ``<name>.<format>`` for the theme's formats (or ``formats``); refuses text the fonts cannot draw."""
    import matplotlib.pyplot as plt
    from matplotlib.text import Text

    pres.check_glyphs([artist.get_text() for artist in fig.findobj(Text)], theme)
    metadata = {"png": {"Software": None}, "svg": {"Date": None, "Creator": None},
                "pdf": {"CreationDate": None, "Creator": None, "Producer": None}}
    written = {}
    for kind in formats or theme.formats:
        path = Path(out_dir) / f"{name}.{kind}"
        fig.savefig(path, dpi=fig.dpi, metadata=metadata[kind], transparent=theme.background == "transparent")
        written[kind] = path
    plt.close(fig)
    return written


# --------------------------------------------------------------------------------------------- targets plot

def _status_code(cell):
    """Outcome colour/marker code of one run's target cell."""
    if cell.get("missing"):
        return "MISSING"
    if cell.get("applicability_issue_codes") or cell.get("status") == "OUT_OF_MODEL_SCOPE":
        return "OUT_OF_MODEL"
    return {"MET": "PASS", "UNMET": "FAIL", "NOT_APPLICABLE": "NOT_APPLICABLE"}.get(cell.get("assessment"),
                                                                                     "UNKNOWN")


def _state(cell):
    if cell.get("missing"):
        return "missing"
    if not _number(cell.get("value")):
        return "no_value"
    if cell.get("exact"):
        return "exact"
    if cell.get("quantity_state") == "BOUNDED":
        return "bounded"
    return "inexact"


def _run_bounds(path):
    """``{target id: {minimum, maximum}}`` of a saved run (target units), read from its result files."""
    from os_optics.results import load_run

    out = {}
    for target in load_run(Path(path), verify=False).targets()["targets"]:
        bounds = wc.target_bounds({"bounds": target.get("bounds") or {}})
        if bounds["minimum"] is not None or bounds["maximum"] is not None:
            out[target["target_id"]] = bounds
    return out


def targets_data(asset, ctx, theme):
    """The plotted data of a targets plot: runs, panels (one per target and unit) and their entries."""
    from . import compare_runs as cr

    source_ids = asset.get("sources")
    if not isinstance(source_ids, list) or not source_ids or not all(isinstance(s, str) for s in source_ids):
        raise _fail("SPEC_INVALID", f"asset {asset['id']}: 'sources' must list one or more run source ids",
                    recovery=f"set assets.{asset['id']}.sources to run ids such as {list(ctx.spec.get('sources', {}))}",
                    key=f"assets.{asset['id']}.sources")
    if len(set(source_ids)) != len(source_ids):
        raise _fail("SPEC_INVALID", f"asset {asset['id']}: a source is listed twice in 'sources'")
    sources = [ctx.source(s) for s in source_ids]
    for source in sources:
        if source.kind != "run":
            raise _fail("SPEC_INVALID", f"asset {asset['id']}: source {source.id!r} is a study; a targets plot "
                        "compares saved runs", recovery="list run sources, or plot the study with type sweep")
    reference = asset.get("reference")
    if reference is not None and reference not in source_ids:
        raise _fail("SPEC_INVALID", f"asset {asset['id']}: reference {reference!r} is not one of {source_ids}",
                    key=f"assets.{asset['id']}.reference")
    document = cr.compare([s.path for s in sources], labels=source_ids, reference=reference)
    rows = {row["id"]: row for row in document["targets"]}
    wanted = asset.get("targets") or list(rows)
    if isinstance(wanted, str):
        wanted = [wanted]
    unknown = [t for t in wanted if t not in rows]
    if not rows or unknown:
        raise _fail("TARGET_UNKNOWN", f"asset {asset['id']}: targets {unknown or wanted} are in none of the runs "
                    f"{source_ids}; targets of the runs: {sorted(rows)}",
                    recovery=f"set assets.{asset['id']}.targets to some of {sorted(rows)}" if rows else
                    "compare runs that declare targets", targets=unknown)
    bounds = {s.id: _run_bounds(s.path) for s in sources}
    runs_doc = {r["label"]: r for r in document["runs"]}
    runs = [{"source": s.id, "label": _localized_all(s.label, s.id), "color_index": _source_index(ctx, s.id),
             "outcome": runs_doc[s.id]["outcome"],
             "engine_build_id": (runs_doc[s.id].get("identity") or {}).get("engine_build_id"),
             "catalog_version": (runs_doc[s.id].get("identity") or {}).get("catalog_version")} for s in sources]
    panels = []
    for target_id in wanted:
        row = rows[target_id]
        units: list[str] = []
        for source_id in source_ids:
            cell = row["runs"][source_id]
            if not cell.get("missing") and cell.get("unit") not in units:
                units.append(cell.get("unit"))
        units = units or [row.get("unit")]
        for group, unit in enumerate(units):
            entries = []
            for source_id in source_ids:
                cell = row["runs"][source_id]
                if not cell.get("missing") and cell.get("unit") != unit:
                    continue  # another unit: its own panel
                if cell.get("missing") and group > 0:
                    continue  # a missing target is marked once, on the first panel of that target
                entry = {"source": source_id, "state": _state(cell), "outcome": _status_code(cell),
                         "assessment": cell.get("assessment"), "status": cell.get("status"),
                         "quantity_state": cell.get("quantity_state"), "value": cell.get("value"),
                         "unit": None if cell.get("missing") else cell.get("unit"),
                         "bounds": bounds[source_id].get(target_id) if not cell.get("missing") else None}
                if reference is not None and "delta" in cell and source_id != reference:
                    entry["delta"] = cell["delta"]
                if cell.get("power_upper_bound_W") is not None and row.get("metric") == "power_W":
                    entry["power_upper_bound_W"] = cell["power_upper_bound_W"]
                entry["reference"] = source_id == reference
                entries.append(entry)
            panels.append({"target": target_id, "metric": row.get("metric"), "unit": unit,
                           "split_by_unit": len(units) > 1, "entries": entries})
    data = {"schema": TARGETS_SCHEMA, "schema_version": 1, "runs": runs, "reference": reference,
            "targets": wanted, "panels": panels}
    add_display_values(data, theme)
    return data


def add_display_values(data, theme):
    """Display unit and display values (theme.units) of every panel entry, in place."""
    for panel in data["panels"]:
        panel["display_unit"] = theme.display_unit(panel["unit"])
        for entry in panel["entries"]:
            entry["display_value"] = _display(theme, entry.get("value"), panel["unit"])
            if "delta" in entry:
                entry["display_delta"] = _display(theme, entry["delta"], panel["unit"])
            if entry.get("bounds"):
                entry["display_bounds"] = {k: _display(theme, v, panel["unit"]) for k, v in entry["bounds"].items()}
    return data


def _display(theme, value, unit):
    if not _number(value) or not unit:
        return value if _number(value) else None
    try:
        return theme.to_display(value, unit)
    except wc.WorkflowError:
        return value


@dataclasses.dataclass
class ValueAxis:
    """A value axis of a re-drawn panel: its display unit, quantity and the values it must show."""

    panel: int
    quantity: str
    unit: str | None
    values: list[float]
    part: int = 0


def target_axes(data, panel_index=0):
    """``ValueAxis`` of every panel of a targets plot (display values and bounds)."""
    axes = []
    for part, panel in enumerate(data["panels"]):
        values: list[float] = []
        for entry in panel["entries"]:
            if entry["state"] in VALUE_STATES and _number(entry.get("display_value")):
                values.append(entry["display_value"])
            for bound in (entry.get("display_bounds") or {}).values():
                if _number(bound):
                    values.append(bound)
        axes.append(ValueAxis(panel_index, f"target:{panel['target']}", panel.get("display_unit"), values, part))
    return axes


def _common_bounds(entries):
    """The bounds shared by every run that has the target, or None when they differ."""
    seen = [entry.get("display_bounds") for entry in entries if entry["state"] != "missing"]
    if not seen:
        return None, True
    first = seen[0]
    same = all(b == first for b in seen)
    return (first if same else None), same


def _bounds_text(bounds, unit, language):
    low, high = bounds.get("minimum"), bounds.get("maximum")
    suffix = f" {unit}" if unit and unit not in wc.UNITLESS else ""
    if low is not None and high is not None:
        return text("legend.bounds", language, low=_fmt(low), high=_fmt(high)) + suffix
    if low is not None:
        return text("legend.minimum", language, value=_fmt(low)) + suffix
    if high is not None:
        return text("legend.maximum", language, value=_fmt(high)) + suffix
    return ""


def _limits(values, explicit=None):
    """Value-axis range: an explicit [min, max] exactly, else the values with a margin (labels get room later)."""
    if explicit is not None:
        return float(explicit[0]), float(explicit[1])
    if values:
        low, high = min(values), max(values)
    else:
        low, high = 0.0, 1.0
    span = (high - low) or abs(high) or 1.0
    return low - 0.1 * span, high + 0.1 * span


def draw_target_panel(ax, panel, runs, theme, *, y_limits=None, fixed=False):
    """One target on ``ax``: a point per run (outcome colour and marker), the bounds, missing runs marked."""
    from . import plot_results as pr

    language = theme.language
    entries = panel["entries"]
    unit = panel.get("display_unit")
    colors = theme.colors
    shared_bounds, same = _common_bounds(entries)
    band_color = theme.colors["muted"]  # neutral: the series colours identify runs
    title_lines = [text("title.targets_unit", language, target=theme.label(f"target:{panel['target']}"),
                        unit=unit) if panel.get("split_by_unit") else theme.label(f"target:{panel['target']}")]
    if shared_bounds:
        low, high = shared_bounds.get("minimum"), shared_bounds.get("maximum")
        if low is not None and high is not None:
            ax.axhspan(low, high, color=band_color, alpha=0.16, linewidth=0, zorder=0)
        for value in (low, high):
            if value is not None:
                ax.axhline(value, color=band_color, linewidth=1.0, linestyle="--", zorder=1)
        title_lines.append(_bounds_text(shared_bounds, unit, language))
    elif not same:
        for index, entry in enumerate(entries):
            bounds = entry.get("display_bounds") or {}
            low, high = bounds.get("minimum"), bounds.get("maximum")
            if low is not None and high is not None:
                ax.fill_between([index - 0.42, index + 0.42], low, high, color=band_color, alpha=0.16, linewidth=0)
            for value in (low, high):
                if value is not None:
                    ax.hlines(value, index - 0.42, index + 0.42, color=band_color, linewidth=1.0, linestyle="--")
        title_lines.append(text("legend.bounds_per_run", language))
    values = [v for axis in target_axes({"panels": [panel]}) for v in axis.values]
    scale = max((abs(v) for v in values), default=0.0)
    if fixed and y_limits is not None:
        ax.set_ylim(*_limits(values, y_limits))  # an explicit range is drawn exactly
    else:
        ax.set_ylim(*_limits(list(y_limits) if y_limits is not None else values))
    y_low, y_high = ax.get_ylim()

    def free_height(index):
        """Axes height (fraction) for a mark without a value: away from the left neighbour's value text."""
        before = entries[index - 1] if index > 0 else None
        if before is None or before["state"] not in VALUE_STATES or not _number(before.get("display_value")):
            return 0.5
        fraction = (before["display_value"] - y_low) / ((y_high - y_low) or 1.0)
        return 0.22 if fraction > 0.5 else 0.78
    body = theme.font_pt("body")
    marker_area = (0.95 * body) ** 2
    small = theme.font_pt("small")
    shown = {}
    placed = []
    # value labels keep a backing of the figure colour so bound lines and grid never run through their text
    backing = None if colors["face"] == "none" else {"boxstyle": "round,pad=0.12", "facecolor": colors["face"],
                                                     "edgecolor": "none", "alpha": 0.85}
    for index, entry in enumerate(entries):
        code = entry["outcome"]
        color = theme.status_color(code)
        marker = pr.STATUS_STYLE.get(code, ("o",))[0]
        state = entry["state"]
        status_word = entry.get("assessment") or ("OUT_OF_MODEL" if code == "OUT_OF_MODEL" else code)
        if code == "OUT_OF_MODEL":
            status_word = "OUT_OF_MODEL"
        if state in VALUE_STATES:
            value = entry["display_value"]
            filled = state == "exact"
            ax.scatter([index], [value], marker=marker, s=marker_area, zorder=3, linewidths=1.6,
                       facecolors=color if filled else "none", edgecolors=colors["face"] if filled and
                       colors["face"] != "none" else color)
            lines = [_fmt(_denoise(value, scale))]
            if entry.get("reference"):
                lines.append(text("label.reference", language))
            elif _number(entry.get("display_delta")):
                suffix = f" {unit}" if unit and unit not in wc.UNITLESS else ""
                lines.append(f"Δ {_fmt_signed(_denoise(entry['display_delta'], scale))}{suffix}")
            upper = ax.annotate("\n".join(lines), (index, value), xytext=(0.75 * body, 0.15 * body),
                                textcoords="offset points", ha="left", va="bottom", fontsize=small,
                                color=colors["text"], zorder=4, bbox=backing)
            note = status_word
            if state == "bounded":
                note = "\n".join((status_word, text("legend.bounded", language)))
            elif state == "inexact":
                note = "\n".join((status_word, text("note.inexact", language)))
            lower = ax.annotate(note, (index, value), xytext=(0.75 * body, -0.15 * body),
                                textcoords="offset points", ha="left", va="top", fontsize=small, color=color,
                                fontweight="bold", zorder=4, bbox=backing)
            placed.append({"index": index, "value": value, "labels": [upper, lower], "side": "right",
                           "beside_pt": 0.75 * body, "gap_pt": 0.15 * body})
        elif state == "no_value":
            ax.text(index, free_height(index), f"{text('note.no_value', language)}\n{status_word}", ha="center", va="center",
                    transform=ax.get_xaxis_transform(), fontsize=small, color=color,
                    bbox={"boxstyle": "round,pad=0.4", "facecolor": "none", "edgecolor": color,
                          "linestyle": "--"})
        else:
            missing_color = theme.status_color("MISSING")
            ax.text(index, free_height(index), text("note.missing", language), ha="center", va="center",
                    transform=ax.get_xaxis_transform(), fontsize=small, color=missing_color, fontstyle="italic",
                    bbox={"boxstyle": "round,pad=0.45", "facecolor": "none", "edgecolor": missing_color,
                          "linestyle": "--"})
        shown[entry["source"]] = state
    labels = [_tick_label(runs[e["source"]]["label"], language) for e in entries]
    ax.set_xticks(range(len(entries)), labels)
    for tick, entry in zip(ax.get_xticklabels(), entries):
        tick.set_color(theme.series_color(runs[entry["source"]]["color_index"]))
        tick.set_fontweight("bold")
    ax.set_xlim(-0.6, len(entries) - 0.25)
    ax.grid(axis="x", visible=False)
    if unit and unit not in wc.UNITLESS:
        ax.set_ylabel(f"{text('axis.value', language)} [{unit}]")
    else:
        ax.set_ylabel(text("axis.value", language))
    ax.set_title("\n".join(line for line in title_lines if line), fontsize=theme.font_pt("body"),
                 color=colors["text"])
    return {"target": panel["target"], "unit": unit, "states": shown, "y_limits": [float(v) for v in ax.get_ylim()],
            "ax": ax, "labels": placed, "marker_pt": math.sqrt(marker_area), "explicit": bool(fixed)}


def _overlaps(box, other, pad=0.0):
    return not (box.x1 + pad <= other.x0 or other.x1 + pad <= box.x0 or box.y1 + pad <= other.y0
                or other.y1 + pad <= box.y0)


def _label_box(item, renderer):
    from matplotlib.transforms import Bbox

    return Bbox.union([label.get_window_extent(renderer) for label in item["labels"]])


LABEL_SIDES = ("right", "left", "above", "below")


def _set_side(item, side, renderer, dpi):
    """Place the value line (upper) and the assessment line (lower) of a point: beside it on the right or left,
    or stacked and centred above or below it. Offsets are in points from the point."""
    upper, lower = item["labels"]
    gap = item["gap_pt"]
    if side in ("right", "left"):
        sign = 1 if side == "right" else -1
        align = "left" if side == "right" else "right"
        upper.set_position((sign * item["beside_pt"], gap))
        upper.set_horizontalalignment(align)
        upper.set_verticalalignment("bottom")
        lower.set_position((sign * item["beside_pt"], -gap))
        lower.set_horizontalalignment(align)
        lower.set_verticalalignment("top")
        return
    lower_pt = lower.get_window_extent(renderer).height * 72 / dpi
    upper_pt = upper.get_window_extent(renderer).height * 72 / dpi
    start = item["beside_pt"]
    for label in (upper, lower):
        label.set_horizontalalignment("center")
    if side == "above":
        lower.set_position((0, start))
        lower.set_verticalalignment("bottom")
        upper.set_position((0, start + lower_pt + gap))
        upper.set_verticalalignment("bottom")
    else:
        upper.set_position((0, -start))
        upper.set_verticalalignment("top")
        lower.set_position((0, -start - upper_pt - gap))
        lower.set_verticalalignment("top")


def _choose_sides(items, markers, frame_box, renderer, dpi):
    """Side of every point's labels with the fewest overlaps (labels with other points and with each other, and
    labels leaving the axes sideways); the right side is preferred when it is free."""
    import itertools

    candidates = []
    for item in items:
        boxes = {}
        for side in LABEL_SIDES:
            _set_side(item, side, renderer, dpi)
            boxes[side] = _label_box(item, renderer)
        _set_side(item, item.get("side", "right"), renderer, dpi)
        candidates.append(boxes)

    def own_cost(number, side):
        box = candidates[number][side]
        cost = sum(_overlaps(box, marker) for k, marker in enumerate(markers) if k != number)
        cost += 0.5 * (box.x0 < frame_box.x0 or box.x1 > frame_box.x1)
        return cost + {"right": 0.0, "left": 0.01, "above": 0.02, "below": 0.03}[side]

    def total(choice):
        cost = sum(own_cost(number, side) for number, side in enumerate(choice))
        for first, second in itertools.combinations(range(len(choice)), 2):
            cost += _overlaps(candidates[first][choice[first]], candidates[second][choice[second]])
        return cost

    if len(items) <= 6:
        return min(itertools.product(LABEL_SIDES, repeat=len(items)), key=total)
    chosen: list[str] = []  # many points: one point after the other
    for number in range(len(items)):
        chosen.append(min(LABEL_SIDES, key=lambda side: own_cost(number, side) + sum(
            _overlaps(candidates[number][side], candidates[k][chosen[k]]) for k in range(len(chosen)))))
    return chosen


def settle_target_labels(fig, infos, *, adjust_limits=True):
    """Give the value labels of target panels room: beside their point on the side that keeps them off the other
    points and labels, and (unless the range is fixed) enough head- and footroom inside the axes."""
    from matplotlib.transforms import Bbox

    for _round in range(4):
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
        changed = False
        for info in infos:
            ax, items = info["ax"], info["labels"]
            if not items:
                continue
            radius = info["marker_pt"] * fig.dpi / 72 / 2 + 2
            points = [ax.transData.transform((item["index"], item["value"])) for item in items]
            markers = [Bbox.from_extents(x - radius, y - radius, x + radius, y + radius) for x, y in points]
            sides = _choose_sides(items, markers, ax.get_window_extent(renderer), renderer, fig.dpi)
            taken: list[Any] = []
            for item, side in zip(items, sides):
                _set_side(item, side, renderer, fig.dpi)
                if side != item.get("side", "right"):
                    item["side"], changed = side, True
                taken.append(_label_box(item, renderer))
            if not adjust_limits or info.get("explicit"):
                continue
            frame_box = ax.get_window_extent(renderer)
            pad = 0.25 * info["marker_pt"] * fig.dpi / 72
            top = max(b.y1 for b in taken) + pad - frame_box.y1
            bottom = frame_box.y0 - (min(b.y0 for b in taken) - pad)
            if top > 0.5 or bottom > 0.5:
                low, high = ax.get_ylim()
                per_px = (high - low) / max(frame_box.height - max(top, 0) - max(bottom, 0), 1.0)
                ax.set_ylim(low - max(bottom, 0) * per_px, high + max(top, 0) * per_px)
                changed = True
        if not changed:
            break
    for info in infos:
        info["y_limits"] = [float(v) for v in info["ax"].get_ylim()]


def fit_axis_text(fig, axes):
    """Wrap axis labels and titles that are longer than their axes, so no text leaves its panel."""
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    changed = False
    for ax in axes:
        if not ax.get_visible():
            continue
        frame_box = ax.get_window_extent(renderer)
        for label, room in ((ax.xaxis.label, frame_box.width), (ax.yaxis.label, frame_box.height),
                            (ax.title, frame_box.width)):
            value = label.get_text()
            if not value:
                continue
            extent = label.get_window_extent(renderer)
            length = extent.height if label is ax.yaxis.label else extent.width
            if length > room * 1.02:
                wrapped = "\n".join(_wrap(line, 0.95 * room, label.get_fontsize(), fig.dpi)
                                    for line in value.split("\n"))
                if wrapped != value:
                    label.set_text(wrapped)
                    changed = True
    return changed


def _tick_label(label, language):
    value = pres.localized(label, language, "")
    if len(value) > 16 and " " in value:
        middle = len(value) // 2
        cut = min((i for i, c in enumerate(value) if c == " "), key=lambda i: abs(i - middle))
        return value[:cut] + "\n" + value[cut + 1:]
    return value


def target_columns(container, data, theme):
    """Panels per row: as many as fit the container's width (each run needs its label and value text)."""
    runs = max((len(p["entries"]) for p in data["panels"]), default=1)
    font_px = theme.font_pt("small") * container.figure.dpi / 72
    needed = font_px * (5.0 + 7.5 * runs)
    fitting = max(1, int(container.bbox.width // needed))
    return max(1, min(len(data["panels"]), MAX_TARGET_COLUMNS, fitting))


def draw_targets(container, data, theme, *, y_limits=None, fixed=False):
    """Every panel of a targets plot in ``container`` (a figure or subfigure); returns per-panel facts."""
    panels = data["panels"]
    columns = target_columns(container, data, theme)
    rows = math.ceil(len(panels) / columns)
    axes = container.subplots(rows, columns, squeeze=False)
    runs = {run["source"]: run for run in data["runs"]}
    info = []
    for index, panel in enumerate(panels):
        info.append(draw_target_panel(axes[index // columns][index % columns], panel, runs, theme,
                                      y_limits=y_limits, fixed=fixed))
    for index in range(len(panels), rows * columns):
        axes[index // columns][index % columns].remove()
    return info


def _caption_key(entry):
    if entry["state"] == "missing":
        return "missing"
    if entry["state"] == "no_value":
        return "no_value"
    if entry["state"] != "exact":
        return "inexact"
    if entry["outcome"] in ("PASS", "FAIL"):
        return "met" if entry["outcome"] == "PASS" else "unmet"
    return f"other:{entry['assessment'] or entry['outcome']}"


def targets_caption(data, language, theme_for_labels):
    """Factual caption: targets and runs, then per target (two targets or fewer) or per run (more targets) which
    values are within the bounds, outside, inexact or not reported."""
    runs = {run["source"]: pres.localized(run["label"], language, run["source"]) for run in data["runs"]}
    theme = theme_for_labels(language)
    names = {t: theme.label(f"target:{t}") for t in data["targets"]}
    joined_runs = join_names(list(runs.values()), language)
    if len(names) > 4:
        first = text("caption.targets_runs_many", language, count=len(names), runs=joined_runs)
    else:
        first = text("caption.targets_runs", language, targets=join_names(list(names.values()), language,
                                                                          enumeration=True), runs=joined_runs)
    sentences = [sentence(first, language)]
    by_target: dict[str, dict[str, list[str]]] = {}
    by_run: dict[str, dict[str, list[str]]] = {source: {} for source in runs}
    for panel in data["panels"]:
        groups = by_target.setdefault(panel["target"], {})
        for entry in panel["entries"]:
            key = _caption_key(entry)
            groups.setdefault(key, []).append(runs[entry["source"]])
            by_run[entry["source"]].setdefault(key, []).append(names.get(panel["target"], panel["target"]))
    separator = "；" if language == "zh-CN" else "; "
    if len(names) > 2:
        for source, groups in by_run.items():
            met = len(groups.get("met", []))
            if met == len(names):
                sentences.append(sentence(text("caption.targets_run_all", language, run=runs[source],
                                               count=len(names)), language))
                continue
            parts = [text("caption.targets_run_some", language, run=runs[source], met=met, count=len(names))]
            for key, text_key in (("unmet", "outside"), ("inexact", "inexact"), ("no_value", "no_value"),
                                  ("missing", "missing")):
                if groups.get(key):
                    parts.append(text(f"caption.targets_run_{text_key}", language,
                                      targets=join_names(groups[key], language, enumeration=True)))
            for key in sorted(k for k in groups if k.startswith("other:")):
                parts.append(text("caption.targets_run_other", language, code=key.split(":", 1)[1],
                                  targets=join_names(groups[key], language, enumeration=True)))
            end = "。" if language == "zh-CN" else "."
            sentences.append(sentence(separator.join(parts) + end, language))
        return (" " if language == "en" else "").join(sentences)
    for target_id, groups in by_target.items():
        parts = []
        for key in ("met", "unmet", "inexact", "no_value", *sorted(k for k in groups if k.startswith("other:")),
                    "missing"):
            if key not in groups:
                continue
            members = groups[key]
            plural = len(members) > 1
            joined = join_names(members, language)
            if key in ("met", "unmet"):
                parts.append(text(f"caption.targets_{key}", language, runs=joined, verb="are" if plural else "is"))
            elif key in ("inexact", "no_value"):
                parts.append(text(f"caption.targets_{key}", language, runs=joined, have="have" if plural else "has"))
            elif key == "missing":
                parts.append(text("caption.targets_missing", language, runs=joined, do="do" if plural else "does"))
            else:
                parts.append(text("caption.targets_other", language, runs=joined, verb="are" if plural else "is",
                                  code=key.split(":", 1)[1]))
        sentences.append(sentence(text("caption.targets_target", language, target=names.get(target_id, target_id),
                                       parts=separator.join(parts)), language))
    return (" " if language == "en" else "").join(sentences)


def _language_theme(ctx, asset):
    cache: dict[str, Any] = {}

    def theme_for(language):
        if language not in cache:
            cache[language] = pres.resolve_theme(ctx.spec.get("theme"), {**(asset.get("theme") or {}),
                                                                          "language": language})
        return cache[language]

    return theme_for


def _provenance(data_file, out, runs, language):
    from . import plot_results as pr

    builds = sorted({r["engine_build_id"] for r in runs if r.get("engine_build_id")})
    catalogs = sorted({r["catalog_version"] for r in runs if r.get("catalog_version")})
    return pr.caption(data_file, out, engine_build=", ".join(builds) or None,
                      catalog_version=", ".join(catalogs) or None, language=language)


def build_targets_plot(asset, ctx, theme, out):
    """Plot type ``targets``: target values of several saved runs with their bounds and assessments."""

    data = targets_data(asset, ctx, theme)
    data_file = wc.write_json(Path(out) / "data.json", data)
    theme_for = _language_theme(ctx, asset)
    title = {lang: text("title.targets", lang) for lang in pres.LANGUAGES}
    caption = {lang: targets_caption(data, lang, theme_for) for lang in pres.LANGUAGES}
    shown_title = pres.title_text(asset, theme, title)
    shown_caption = pres.caption_text(asset, theme, caption)
    with drawing(theme) as plt:
        fig = new_figure(plt, theme)
        info = draw_targets(fig, data, theme)
        frame(fig, theme, shown_title, footer_text(theme, shown_caption, _provenance(data_file, out, data["runs"],
                                                                                      theme.language)))
        fit_axis_text(fig, [i["ax"] for i in info])
        settle_target_labels(fig, info)
        overflow = text_overflow(fig)
        written = save_figure(fig, out, asset["id"], theme)
    files: dict[str, Path] = {"image": written["png"]}
    for kind in ("svg", "pdf"):
        if kind in written:
            files[kind] = written[kind]
    details = {"runs": [r["source"] for r in data["runs"]], "reference": data["reference"],
               "targets": data["targets"],
               "panels": [{"target": p["target"], "unit": p["unit"], "display_unit": p["display_unit"],
                           "values": {e["source"]: e.get("value") for e in p["entries"]},
                           "states": {e["source"]: e["state"] for e in p["entries"]},
                           "assessments": {e["source"]: e.get("assessment") for e in p["entries"]
                                           if e["state"] != "missing"},
                           **({"deltas": {e["source"]: e["delta"] for e in p["entries"] if "delta" in e}}
                              if data["reference"] else {}),
                           "y_limits": i["y_limits"]} for p, i in zip(data["panels"], info)],
               "missing": [{"target": p["target"], "source": e["source"]} for p in data["panels"]
                           for e in p["entries"] if e["state"] == "missing"],
               "split_by_unit": sorted({p["target"] for p in data["panels"] if p["split_by_unit"]}),
               "text_fits": not overflow, **({"text_outside": [t for t, _b in overflow][:10]} if overflow else {})}
    return {"files": files, "data_files": [data_file], "title": title, "caption": caption,
            "sources": [r["source"] for r in data["runs"]], "details": details}


# --------------------------------------------------------------------------------------------- compose

@dataclasses.dataclass
class Panel:
    """One resolved panel of a composition."""

    index: int
    asset_id: str
    entry: dict
    definition: dict
    mode: str  # image | sweep | heatmap | targets | power
    labels: dict[str, str]
    data: Any = None
    image: Path | None = None
    span: int = 1
    run_color: int | None = None
    axes: list[ValueAxis] = dataclasses.field(default_factory=list)
    color_axis: ValueAxis | None = None
    image_size: tuple[int, int] | None = None
    crop: tuple[int, int, int, int] | None = None  # (left, top, right, bottom) pixels of the source image
    given_label: bool = False

    @property
    def raster(self):
        return self.mode == "image"


def _find_definition(ctx, asset_id):
    for asset in ctx.spec.get("assets", []):
        if asset.get("id") == asset_id:
            return asset
    return {}


def _default_label(entry, ctx):
    sources = entry.get("sources") or []
    if len(sources) == 1:
        try:
            source = ctx.source(sources[0])
        except pres.PresentationError:
            source = None
        if source is not None:
            return _localized_all(source.label, source.id)
    definition = _find_definition(ctx, entry.get("id"))
    if definition.get("title") is not None:
        return _localized_all(definition["title"], entry.get("id"))
    if entry.get("kind") == "plot" and definition.get("type") == "targets":
        return {lang: text("title.targets", lang) for lang in pres.LANGUAGES}
    title = entry.get("title") or entry.get("id")
    return {lang: title for lang in pres.LANGUAGES}


def _given_label(value, ctx):
    if isinstance(value, dict) and set(value) == {"source"}:  # {"source": id}: that source's label
        source = ctx.source(value["source"])
        return _localized_all(source.label, source.id)
    if isinstance(value, (str, dict)):
        default = pres.localized(value, "en", "")
        return _localized_all(value, default)
    raise _fail("SPEC_INVALID", f"panel label {value!r}: use text or {{\"en\": ..., \"zh-CN\": ...}}")


def _read_data(entry):
    path = (entry.get("_abs") or {}).get("data")
    if path is None or not Path(path).is_file():
        return None
    return wc.read_json(path)


def _sweep_axis(data, index, theme):
    rows, y, unit = data["rows"], data["y"], data.get("unit")
    values = [_display(theme, r[y], unit) for r in rows if _exact_row(r, y)]
    return values, theme.display_unit(unit) if unit else unit


def _exact_row(row, column):
    value = row.get(column)
    return _number(value) and row.get(f"{column}:exact") is not False


def resolve_panels(asset, ctx, theme):
    """``Panel`` objects of a compose asset, with their drawing mode, labels and value axes."""
    specs = asset.get("panels")
    if not isinstance(specs, list) or not specs:
        raise _fail("SPEC_INVALID", f"asset {asset['id']}: 'panels' must list one or more {{\"asset\": id}}",
                    key=f"assets.{asset['id']}.panels")
    layout = asset.get("layout", "row")
    panels = []
    for position, spec in enumerate(specs):
        if not isinstance(spec, dict) or not isinstance(spec.get("asset"), str):
            raise _fail("SPEC_INVALID", f"asset {asset['id']}: panels[{position}] needs \"asset\": <asset id>",
                        key=f"assets.{asset['id']}.panels")
        entry = ctx.built(spec["asset"])
        if entry is None or entry.get("status") not in ("written", "reused"):
            raise _fail("PANEL_MISSING", f"asset {asset['id']} uses asset {spec['asset']!r}, which is not available",
                        recovery=f"add or fix asset {spec['asset']!r}, or remove it from {asset['id']}",
                        asset=spec["asset"])
        definition = _find_definition(ctx, spec["asset"])
        default = _default_label(entry, ctx)
        if spec.get("label") is not None:
            labels = _given_label(spec["label"], ctx)
        elif layout == "before-after" and position < 2:
            word = "compose.before" if position == 0 else "compose.after"
            labels = {lang: text("compose.label_with", lang, label=text(word, lang), detail=default[lang])
                      for lang in pres.LANGUAGES}
        else:
            labels = default
        span = spec.get("span", 1)
        if not isinstance(span, int) or isinstance(span, bool) or span < 1:
            raise _fail("SPEC_INVALID", f"asset {asset['id']}: panels[{position}].span must be a whole number >= 1")
        panel = Panel(index=position + 1, asset_id=spec["asset"], entry=entry, definition=definition,
                      mode="image", labels=labels, span=span, given_label=spec.get("label") is not None)
        sources = entry.get("sources") or []
        if entry.get("kind") == "view" and len(sources) == 1:
            panel.run_color = _source_index(ctx, sources[0])
        data = _read_data(entry) if entry.get("kind") == "plot" else None
        plot_type = definition.get("type")
        if data is not None and plot_type == "sweep" and data.get("rows") and data.get("parameters"):
            values, unit = _sweep_axis(data, position, theme)
            for bound in ((entry.get("details") or {}).get("bounds") or {}).values():
                if _number(bound):
                    values.append(_display(theme, bound, data.get("unit")))
            if len(data["parameters"]) == 1:
                panel.mode = "sweep"
                panel.axes = [ValueAxis(panel.index, data["y"], unit, values)]
            else:
                panel.mode = "heatmap"
                panel.color_axis = ValueAxis(panel.index, data["y"], unit, values)
            panel.data = data
        elif data is not None and plot_type == "targets" and data.get("panels"):
            panel.mode = "targets"
            panel.data = add_display_values(data, theme)
            panel.axes = target_axes(panel.data, panel.index)
        elif data is not None and plot_type == "power" and "monitors" in data:
            panel.mode = "power"
            panel.data = data
        else:
            image = (entry.get("_abs") or {}).get("image")
            if image is None or not Path(image).is_file():
                raise _fail("PANEL_MISSING", f"asset {asset['id']}: panel asset {spec['asset']!r} has no image",
                            recovery=f"rebuild asset {spec['asset']!r}", asset=spec["asset"])
            panel.image = Path(image)
            size = pres.image_size(panel.image)
            if not size:
                from PIL import Image

                with Image.open(panel.image) as opened:
                    size = list(opened.size)
            panel.image_size = (int(size[0]), int(size[1]))
        panels.append(panel)
    _distinct_labels(panels, ctx, asset)
    return panels


def _panel_titles(panel, ctx, asset):
    """Both-language titles of a panel's asset: what it shows (a sweep's quantity and parameter, a view's run)."""
    out = {}
    for lang in pres.LANGUAGES:
        theme = pres.resolve_theme(ctx.spec.get("theme"), {**(asset.get("theme") or {}), "language": lang})
        if panel.mode in ("sweep", "heatmap") and panel.data:
            y = panel.data["y"]
            if panel.mode == "sweep":
                parameter = f"param:{panel.data['parameters'][0]['name']}"
                out[lang] = pres.text("title.sweep", lang, y=theme.label(y), x=theme.label(parameter))
            else:
                out[lang] = pres.text("title.heatmap", lang, y=theme.label(y))
        elif panel.mode == "targets" and panel.data:
            out[lang] = join_names([theme.label(f"target:{t}") for t in panel.data.get("targets", [])], lang,
                                   enumeration=True) or text("title.targets", lang)
        elif panel.mode == "power":
            out[lang] = pres.text("title.power", lang)
        elif panel.entry.get("kind") == "view":
            out[lang] = pres.text("title.view_of", lang, source=panel.labels[lang])
        else:
            out[lang] = panel.entry.get("title") or panel.asset_id
    return out


def _distinct_labels(panels, ctx, asset):
    """Default labels that two panels would share (two plots of one study) name what each panel shows."""
    for lang in pres.LANGUAGES:
        counts: dict[str, int] = {}
        for panel in panels:
            counts[panel.labels[lang]] = counts.get(panel.labels[lang], 0) + 1
        for panel in panels:
            if not panel.given_label and counts[panel.labels[lang]] > 1:
                panel.labels = {**panel.labels, lang: _panel_titles(panel, ctx, asset)[lang]}


def decide_scale(requested, axes, *, what):
    """Shared scale decision: ``{"requested", "applied", "limits", "unit", "panels", "reason"?, "units"?}``.

    ``requested``: "auto" (share when the display units and quantities are equal), true (when the units are equal),
    false, or [min, max] in display units (applied when the units are equal)."""
    out: dict[str, Any] = {"requested": requested, "applied": False}
    if requested not in ("auto", True, False) and not (
            isinstance(requested, list) and len(requested) == 2 and all(_number(v) for v in requested)
            and requested[0] < requested[1]):
        raise _fail("SPEC_INVALID", f"shared.{what} {requested!r}: use {SCALE_CHOICES}", key=f"shared.{what}")
    if not axes:
        out["reason"] = "NO_REDRAWN_PLOTS"
        return out
    out["panels"] = sorted({a.panel for a in axes})
    units = {a.unit or "" for a in axes}
    if requested is False:
        out["reason"] = "OFF"
        return out
    if len(units) > 1:
        out.update({"reason": "UNITS_DIFFER", "units": {f"{a.panel}" + (f".{a.part + 1}" if a.part else ""):
                                                        a.unit for a in axes}})
        return out
    if requested == "auto" and len({a.quantity for a in axes}) > 1:
        out.update({"reason": "QUANTITIES_DIFFER", "quantities": sorted({a.quantity for a in axes})})
        return out
    if requested == "auto" and len(axes) < 2:
        out["reason"] = "SINGLE_AXIS"
        return out
    if isinstance(requested, list):
        limits = [float(requested[0]), float(requested[1])]
        out.update({"exact": True, "axis_range": limits})  # drawn exactly as given
    else:
        values = [v for a in axes for v in a.values]
        if not values:
            out["reason"] = "NO_VALUES"
            return out
        limits = [float(min(values)), float(max(values))]
    out.update({"applied": True, "limits": limits, "unit": units.pop() or None})
    return out


def _framing(panels, requested):
    """``("shared" | "independent" | "not_applicable", detail)`` from the view panels' recorded boxes."""
    views = [p for p in panels if p.entry.get("kind") == "view"]
    if len(views) < 2:
        return "not_applicable", {}
    boxes = [wc.canonical_json(wc.canonical_numbers((p.entry.get("details") or {}).get("box_mm"))) for p in views]
    cameras = [wc.canonical_json((p.entry.get("details") or {}).get("camera")) for p in views]
    state = "shared" if len(set(boxes)) == 1 and boxes[0] != "null" else "independent"
    detail: dict[str, Any] = {"requested": bool(requested), "camera": "shared" if len(set(cameras)) == 1
                              else "independent", "views": [p.asset_id for p in views]}
    if state == "independent" and requested:
        sources = [s for p in views for s in (p.entry.get("sources") or [])]
        detail["recovery"] = (f"set framing_sources {sources} on the view assets {[p.asset_id for p in views]} "
                              "and build again")
    return state, detail


@dataclasses.dataclass
class Geometry:
    rows: int
    columns: int
    width_ratios: list[float]
    height_ratios: list[float]
    cells: list[tuple[int, int, int]]  # (row, first column, last column + 1) per panel
    gap_px: float


def _aspect(panel):
    if panel.crop is not None:
        left, top, right, bottom = panel.crop
        return (right - left) / max(1, bottom - top)
    if panel.image_size is None:
        return None
    return panel.image_size[0] / panel.image_size[1]


TRIM_THRESHOLD = 10  # colour difference (0..255) below which a pixel counts as background
TRIM_PAD = 0.02  # margin kept around the content, fraction of the larger image side


def _content_box(path):
    """Box of the pixels that differ from the background (corner colour or transparency); None when uniform."""
    from PIL import Image, ImageChops

    with Image.open(path) as image:
        image = image.convert("RGBA")
        alpha = image.getchannel("A")
        if alpha.getextrema()[0] < 255:  # transparent background: the opaque pixels are the content
            return alpha.point(lambda v: 255 if v > TRIM_THRESHOLD else 0).getbbox()
        rgb = image.convert("RGB")
        background = Image.new("RGB", rgb.size, rgb.getpixel((0, 0)))
        difference = ImageChops.difference(rgb, background).convert("L")
        return difference.point(lambda v: 255 if v > TRIM_THRESHOLD else 0).getbbox()


def trim_rasters(panels, framing):
    """Crop the uniform margins of raster panels. Views that share one framing (equal recorded boxes, equal image
    sizes) get one common crop, so their scale and position stay comparable."""
    groups: dict[str, list[Panel]] = {}
    for panel in panels:
        if panel.raster and panel.image is not None and panel.image_size is not None:
            shared = framing == "shared" and panel.entry.get("kind") == "view"
            key = f"views:{panel.image_size}" if shared else f"panel:{panel.index}"
            groups.setdefault(key, []).append(panel)
    for members in groups.values():
        boxes = [box for box in (_content_box(p.image) for p in members) if box]
        if not boxes:
            continue
        width, height = members[0].image_size
        pad = round(TRIM_PAD * max(width, height))
        left = max(0, min(b[0] for b in boxes) - pad)
        top = max(0, min(b[1] for b in boxes) - pad)
        right = min(width, max(b[2] for b in boxes) + pad)
        bottom = min(height, max(b[3] for b in boxes) + pad)
        if (right - left) * (bottom - top) < 0.97 * width * height:
            for panel in members:
                panel.crop = (left, top, right, bottom)


def raster_pixels(panel):
    """The (cropped) RGBA image of a raster panel as a PIL image."""
    from PIL import Image

    with Image.open(panel.image) as image:
        image = image.convert("RGBA")
        return image.crop(panel.crop) if panel.crop else image.copy()


def wrap_row(panels, *, spacing=DEFAULT_SPACING, size=(1920, 1080), header_px=0.0, reserved_px=0.0):
    """Columns for a row of very wide views when two rows show them clearly larger (None: keep one row)."""
    count = len(panels)
    if count < 3 or not all(p.raster for p in panels):
        return None
    width, height = size
    gap = spacing * width
    aspect = min(_aspect(p) or 16 / 9 for p in panels)
    usable_h = height - reserved_px

    def image_height(rows):
        columns = math.ceil(count / rows)
        cell_w = (width - gap * (columns - 1)) / columns
        return min(cell_w / aspect, (usable_h - gap * (rows - 1)) / rows - header_px)

    one, two = image_height(1), image_height(2)
    return math.ceil(count / 2) if two > 1.25 * one else None


def compose_geometry(layout, panels, *, columns=None, spacing=DEFAULT_SPACING, size=(1920, 1080), heights=None,
                     header_px=0.0, reserved_px=0.0, spans=None, center_last=False):
    """Rows, columns, ratios and cells of a layout for a canvas of ``size`` pixels."""
    count = len(panels)
    width, height = size
    if layout in ("before-after",):
        spacing = max(spacing, ARROW_SPACING)
    gap = spacing * width
    if layout in ("row", "before-after", "system-plot"):
        ncols, cells = count, [(0, i, i + 1) for i in range(count)]
        ratios = [1.0] * count
        if layout == "system-plot":
            ratios = [SYSTEM_VIEW_RATIO if p.raster else 1.0 for p in panels]
            if all(p.raster for p in panels) or not any(p.raster for p in panels):
                ratios = [SYSTEM_VIEW_RATIO] + [1.0] * (count - 1)
        rows = 1
    elif layout == "column":
        ncols, rows, cells, ratios = 1, count, [(i, 0, 1) for i in range(count)], [1.0]
    else:
        ncols = int(columns) if columns else max(1, math.ceil(math.sqrt(count)))
        cells, row, col = [], 0, 0
        for number, panel in enumerate(panels):
            span = min(spans[number] if spans else panel.span, ncols)
            if col + span > ncols:
                row, col = row + 1, 0
            cells.append((row, col, col + span))
            col += span
        if center_last and col < ncols:  # a shorter last row sits in the middle
            shift = (ncols - col) // 2
            cells = [(r, c0 + shift, c1 + shift) if r == row else (r, c0, c1) for r, c0, c1 in cells]
        rows, ratios = row + 1, [1.0] * ncols
    total_ratio = sum(ratios)
    usable_w = width - gap * (ncols - 1)
    usable_h = height - reserved_px - gap * (rows - 1)
    if heights:
        height_ratios = [float(h) for h in heights][:rows] + [1.0] * max(0, rows - len(heights))
    else:
        needs: list[float | None] = []
        for row in range(rows):
            members = [(p, c) for p, c in zip(panels, cells) if c[0] == row]
            if members and all(p.raster for p, _c in members):
                fitted = []
                for p, (_r, c0, c1) in members:
                    cell_w = usable_w * sum(ratios[c0:c1]) / total_ratio + gap * (c1 - c0 - 1)
                    fitted.append(cell_w / (_aspect(p) or 16 / 9) + header_px)
                needs.append(max(fitted))
            else:
                needs.append(None)
        fixed = sum(n for n in needs if n is not None)
        flexible = sum(1 for n in needs if n is None)
        if flexible:
            share = max((usable_h - fixed) / flexible, 0.45 * usable_h / rows)
            height_ratios = [n if n is not None else share for n in needs]
        else:
            height_ratios = [float(n) for n in needs if n is not None]
            spare = (usable_h - fixed - 2 * gap) / 2
            if spare > 0.02 * usable_h:  # views only: centre the panels vertically with two empty rows
                height_ratios = [spare, *height_ratios, spare]
                cells = [(r + 1, c0, c1) for r, c0, c1 in cells]
                rows += 2
    return Geometry(rows=rows, columns=ncols, width_ratios=ratios, height_ratios=height_ratios, cells=cells,
                    gap_px=gap)


def _draw_panel_content(container, panel, theme, scales):
    """Draw ``panel`` into ``container`` (subfigure or figure); returns facts (applied limits) for layout.json."""
    from . import plot_results as pr

    style = _style(dataclasses.replace(theme, title=False))
    facts: dict[str, Any] = {}
    shared_y = bool(scales["y"].get("applied")) and panel.index in scales["y"].get("panels", [])
    axis_range = scales["y"].get("axis_range") if shared_y else None
    y_limits = axis_range or (scales["y"].get("limits") if shared_y else None)
    c_limits = scales["color"].get("limits") if scales["color"].get("applied") and panel.index in scales[
        "color"].get("panels", []) else None
    if panel.mode == "image":
        import numpy as np

        ax = container.add_subplot()
        ax.imshow(np.asarray(raster_pixels(panel)), interpolation="antialiased")
        ax.set_axis_off()
        facts["axes"] = [ax]
    elif panel.mode == "sweep":
        data = panel.data
        ax = container.add_subplot()
        info = pr.draw_curve(ax, data["rows"], data["parameters"][0], data["y"], data.get("unit"),
                             (panel.entry.get("details") or {}).get("bounds"), style, y_limits=y_limits)
        facts.update({"axes": [ax], "value_axes": [ax], "legend_axes": [ax], "points": info["points"]})
    elif panel.mode == "heatmap":
        data = panel.data
        ax = container.add_subplot()
        pr.draw_heatmap(container, ax, data["rows"], data["parameters"], data["y"], data.get("unit"),
                        (panel.entry.get("details") or {}).get("bounds"), style, color_limits=c_limits)
        facts.update({"axes": [ax], "color_limits": c_limits})
    elif panel.mode == "targets":
        info = draw_targets(container, panel.data, theme, y_limits=y_limits, fixed=axis_range is not None)
        facts.update({"axes": container.get_axes(), "value_axes": [i["ax"] for i in info], "targets": info})
    else:  # power
        left, right = container.subplots(1, 2, width_ratios=[3, 2])
        pr.draw_power(left, right, panel.data["monitors"], panel.data["power_budget"] or {}, style)
        facts.update({"axes": [left, right], "legend_axes": [left]})
    for ax in facts.get("value_axes", []):
        if axis_range is not None:
            ax.set_ylim(*axis_range)
    if facts.get("value_axes"):
        ranges = [[float(v) for v in ax.get_ylim()] for ax in facts["value_axes"]]
        facts["y_limits"] = ranges[0] if panel.mode == "sweep" else ranges
    return facts


def finish_panel(fig, fact, *, adjust_limits=True):
    """Give target value labels room, move legends off the data (headroom unless the range is fixed) and wrap axis
    texts longer than their axes."""
    plot_axes = [ax for ax in fact.get("axes", []) if ax.axison]
    if plot_axes:
        fit_axis_text(fig, plot_axes)
    if fact.get("targets"):
        settle_target_labels(fig, fact["targets"], adjust_limits=adjust_limits)
        fact["y_limits"] = [i["y_limits"] for i in fact["targets"]]
    for ax in fact.get("legend_axes", []):
        spot = place_legend(fig, ax, headroom=adjust_limits)
        if spot:
            fact.setdefault("legends", []).append(spot)
    if plot_axes and fit_axis_text(fig, plot_axes):
        fig.canvas.draw()


def legends_over_data(fig, panels, facts):
    """Indexes of panels whose legend covers data."""
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    return [panel.index for panel, fact in zip(panels, facts)
            if any(legend_over_data(ax, renderer) for ax in fact.get("legend_axes", []))]


def unify_value_axes(panels, facts, scales):
    """One drawn range for every value axis of a shared scale: exactly the given [min, max], else the union of the
    panels' own ranges (which include room for their labels)."""
    if not scales["y"].get("applied"):
        return
    shared = [ax for panel, fact in zip(panels, facts) if panel.index in scales["y"].get("panels", [])
              for ax in fact.get("value_axes", [])]
    if not shared:
        return
    if scales["y"].get("exact"):
        low, high = scales["y"]["axis_range"]
    else:
        low = min(ax.get_ylim()[0] for ax in shared)
        high = max(ax.get_ylim()[1] for ax in shared)
    scales["y"]["axis_range"] = [float(low), float(high)]
    for panel, fact in zip(panels, facts):
        axes = fact.get("value_axes", [])
        if panel.index in scales["y"].get("panels", []) and axes:
            for ax in axes:
                ax.set_ylim(low, high)
            fact["y_limits"] = [float(low), float(high)] if panel.mode == "sweep" else [[float(low), float(high)]
                                                                                       for _ax in axes]


def _union_px(axes, renderer):
    from matplotlib.transforms import Bbox

    boxes = [ax.get_window_extent(renderer) for ax in axes if ax.get_visible()]
    return Bbox.union(boxes) if boxes else None


def _box(bbox, height):
    """``[x, y, width, height]`` pixels, origin top-left."""
    return [round(bbox.x0), round(height - bbox.y1), round(bbox.width), round(bbox.height)]


def _views_sentence(framing, detail, language):
    camera = detail.get("camera") == "shared"
    if framing == "shared" and camera:
        return text("caption.same_camera_framing", language)
    if framing == "shared":
        return text("caption.same_framing", language)
    if framing == "independent" and camera:
        return text("caption.same_camera", language)
    return None


def _compose_texts(asset, panels, ctx, scales, framing, framing_detail, layout):
    title, caption = {}, {}
    for lang in pres.LANGUAGES:
        labels = list(dict.fromkeys(p.labels[lang] for p in panels))  # distinct subjects, display order
        if layout == "before-after" and len(panels) == 2:
            title[lang] = text("title.before_after", lang)
            plain = [pres.localized(_default_label(p.entry, ctx), lang, "") for p in panels]
            first = text("caption.before_after", lang, before=plain[0], after=plain[1])
        elif layout == "system-plot" and len(panels) == 2:
            view = next((p for p in panels if p.raster), panels[0])
            plot = next((p for p in panels if p is not view), panels[1])
            title[lang] = text("title.system_plot", lang, view=view.labels[lang], plot=plot.labels[lang])
            first = text("caption.system_plot", lang, view=view.labels[lang], plot=plot.labels[lang])
        else:
            runs = _run_labels(panels, ctx, lang)
            title[lang] = text("title.compare", lang, labels=join_names(runs if len(runs) > 1 else labels, lang))
            kind = {"row": "caption.panels_row", "column": "caption.panels_column"}.get(layout, "caption.panels_grid")
            first = text(kind, lang, labels=join_names(labels, lang))
        sentences = [sentence(first, lang)]
        views = _views_sentence(framing, framing_detail, lang)
        if views:
            sentences.append(views)
        for what, key in (("y", "caption.shared_axis"), ("color", "caption.shared_color")):
            scale = scales[what]
            if scale.get("applied"):
                if what == "y" and scale.get("exact"):
                    key = "caption.shared_axis_exact"
                sentences.append(text(key, lang, low=_fmt(scale["limits"][0]), high=_fmt(scale["limits"][1]),
                                      unit=scale.get("unit") or ""))
        caption[lang] = (" " if lang == "en" else "").join(s.replace(" 。", "。").replace(" .", ".")
                                                           for s in sentences)
    return title, caption


def _run_labels(panels, ctx, language):
    """Labels of the run sources shown in the panels, in first-seen order."""
    out = []
    for panel in panels:
        for source_id in panel.entry.get("sources") or []:
            try:
                source = ctx.source(source_id)
            except pres.PresentationError:
                continue
            label = pres.localized(source.label, language, source.id)
            if source.kind == "run" and label not in out:
                out.append(label)
    return out


def _check_compose(asset):
    layout = asset.get("layout", "row")
    if layout not in LAYOUTS:
        raise _fail("SPEC_INVALID", f"asset {asset['id']}: layout {layout!r}; choose {list(LAYOUTS)}",
                    key=f"assets.{asset['id']}.layout")
    count = len(asset.get("panels") or [])
    if layout in ("before-after", "system-plot") and count != 2:
        raise _fail("SPEC_INVALID", f"asset {asset['id']}: layout {layout} takes exactly two panels, not {count}",
                    recovery="use layout row or grid for more panels", key=f"assets.{asset['id']}.panels")
    spacing = asset.get("spacing", DEFAULT_SPACING)
    if not _number(spacing) or not 0 <= spacing <= 0.2:
        raise _fail("SPEC_INVALID", f"asset {asset['id']}: spacing {spacing!r}: a fraction 0..0.2 of the width",
                    key=f"assets.{asset['id']}.spacing")
    columns = asset.get("columns")
    if columns is not None and (not isinstance(columns, int) or isinstance(columns, bool) or columns < 1):
        raise _fail("SPEC_INVALID", f"asset {asset['id']}: columns must be a whole number >= 1",
                    key=f"assets.{asset['id']}.columns")
    heights = asset.get("heights")
    if heights is not None and (not isinstance(heights, list) or not all(_number(h) and h > 0 for h in heights)):
        raise _fail("SPEC_INVALID", f"asset {asset['id']}: heights must list positive numbers",
                    key=f"assets.{asset['id']}.heights")
    if asset.get("wrap", "auto") not in ("auto", False):
        raise _fail("SPEC_INVALID", f"asset {asset['id']}: wrap is auto or false", key=f"assets.{asset['id']}.wrap")
    shared = asset.get("shared", {})
    if not isinstance(shared, dict) or set(shared) - {"y", "color", "framing"}:
        raise _fail("SPEC_INVALID", f"asset {asset['id']}: shared takes y, color and framing",
                    key=f"assets.{asset['id']}.shared")
    return layout, float(spacing), columns, heights, {"y": "auto", "color": "auto", "framing": True, **shared}


def build_compose(asset, ctx):
    """Asset kind ``compose``: panels of earlier assets in one layout with shared scales and framing."""
    from PIL import Image


    layout, spacing, columns, heights, shared = _check_compose(asset)
    theme = ctx.asset_theme(asset)
    out = ctx.asset_dir(asset["id"])
    panels = resolve_panels(asset, ctx, theme)
    letters = bool(asset.get("panel_letters", True))
    scales = {"y": decide_scale(shared["y"], [a for p in panels for a in p.axes], what="y"),
              "color": decide_scale(shared["color"], [p.color_axis for p in panels if p.color_axis], what="color")}
    framing, framing_detail = _framing(panels, shared["framing"])
    if asset.get("trim", True):
        trim_rasters(panels, framing)
    title, caption = _compose_texts(asset, panels, ctx, scales, framing, framing_detail, layout)
    shown_title = pres.title_text(asset, theme, title)
    shown_caption = pres.caption_text(asset, theme, caption)
    footer = footer_text(theme, shown_caption, None)
    width, height = theme.size
    dpi = theme.canvas()[1]
    header_px = theme.font_pt("panel") * dpi / 72 * 1.9
    reserved = (theme.font_pt("title") * dpi / 72 * 2.2 if theme.title and shown_title else 0.0) + (
        theme.font_pt("caption") * dpi / 72 * 1.6 * (footer.count("\n") + 2) if footer else 0.0)
    arrangement, spans = layout, None
    if layout == "row" and asset.get("wrap", "auto") == "auto":
        wrapped = wrap_row(panels, spacing=spacing, size=(width, height), header_px=header_px, reserved_px=reserved)
        if wrapped:  # two rows on half columns, so a shorter second row can be centred
            arrangement, columns, spans = "grid", 2 * wrapped, [2] * len(panels)
            title, caption = _compose_texts(asset, panels, ctx, scales, framing, framing_detail, arrangement)
            shown_title = pres.title_text(asset, theme, title)
            shown_caption = pres.caption_text(asset, theme, caption)
            footer = footer_text(theme, shown_caption, None)
    geometry = compose_geometry(arrangement, panels, columns=columns, spacing=spacing, size=(width, height),
                                heights=heights, header_px=header_px, reserved_px=reserved, spans=spans,
                                center_last=spans is not None)
    language = theme.language
    with drawing(theme) as plt:
        fig = new_figure(plt, theme)
        frame(fig, theme, shown_title, footer)
        avg_w = (width - geometry.gap_px * (geometry.columns - 1)) / geometry.columns
        avg_h = (height - reserved - geometry.gap_px * (geometry.rows - 1)) / geometry.rows
        grid = fig.add_gridspec(geometry.rows, geometry.columns, width_ratios=geometry.width_ratios,
                                height_ratios=geometry.height_ratios,
                                wspace=geometry.gap_px / max(avg_w, 1.0) if geometry.columns > 1 else 0.0,
                                hspace=geometry.gap_px / max(avg_h, 1.0) if geometry.rows > 1 else 0.0)
        subfigures, facts = [], []
        for panel, (row, c0, c1) in zip(panels, geometry.cells):
            sub = fig.add_subfigure(grid[row, c0:c1])
            sub.set_facecolor("none")
            label = panel.labels[language]
            header = f"{chr(ord('a') + panel.index - 1)}   {label}" if letters and len(panels) > 1 else label
            marker_room = theme.font_pt("panel") * dpi / 72 * 1.25 if panel.run_color is not None else 0.0
            cell_w = (width - geometry.gap_px * (geometry.columns - 1)) * sum(
                geometry.width_ratios[c0:c1]) / sum(geometry.width_ratios) + geometry.gap_px * (c1 - c0 - 1)
            header = _wrap(header, 0.94 * cell_w - marker_room, theme.font_pt("panel") * 1.1, dpi)
            sub.suptitle(header, x=0.004 + marker_room / max(cell_w, 1), ha="left", fontsize=theme.font_pt("panel"),
                         fontweight="bold")
            facts.append(_draw_panel_content(sub, panel, theme, scales))
            subfigures.append(sub)
        for panel, fact in zip(panels, facts):
            fixed = bool(scales["y"].get("exact")) and panel.index in scales["y"].get("panels", [])
            finish_panel(fig, fact, adjust_limits=not fixed)
        unify_value_axes(panels, facts, scales)
        fig.canvas.draw()
        fig.set_layout_engine("none")  # freeze the settled layout: boxes, arrow and panel files use it
        renderer = fig.canvas.get_renderer()
        boxes = []
        for panel, sub, fact in zip(panels, subfigures, facts):
            content = _union_px(fact["axes"], renderer)
            if panel.raster and content is not None:
                content = fact["axes"][0].get_window_extent(renderer)
            boxes.append({"cell": sub.bbox.frozen(), "content": content})
            header = sub._suptitle
            if panel.raster and content is not None and header is not None:
                # the label sits directly above the image, which is centred in its cell
                above = content.y1 + 0.35 * theme.font_pt("panel") * dpi / 72
                header.set_y(min(1.0, (above - sub.bbox.y0) / max(sub.bbox.height, 1.0)))
                header.set_va("bottom")
            if panel.run_color is not None and header is not None:
                _run_marker(fig, header.get_window_extent(renderer), theme.series_color(panel.run_color))
        if layout == "before-after" and len(panels) == 2 and boxes[0]["content"] and boxes[1]["content"]:
            _arrow(fig, boxes[0]["content"], boxes[1]["content"], theme)
        overflow = text_overflow(fig, cell_pad=geometry.gap_px / 2)
        covered = legends_over_data(fig, panels, facts)
        written = save_figure(fig, out, asset["id"], theme)
    panel_records = []
    for panel, box, fact in zip(panels, boxes, facts):
        record: dict[str, Any] = {"index": panel.index, "asset": panel.asset_id, "kind": panel.entry.get("kind"),
                                  "mode": panel.mode, "label": panel.labels,
                                  "letter": chr(ord("a") + panel.index - 1) if letters and len(panels) > 1 else None,
                                  "cell_px": _box(box["cell"], height),
                                  "content_px": _box(box["content"], height) if box["content"] else None}
        if panel.span > 1:
            record["span"] = panel.span
        if panel.crop:
            record["crop_px"] = list(panel.crop)
        if "y_limits" in fact:
            record["y_limits"] = fact["y_limits"]
        if fact.get("color_limits"):
            record["color_limits"] = fact["color_limits"]
        files, panel_overflow, panel_covered = _write_panel_file(panel, box, out, theme, scales, Image)
        overflow += [(f"panel-{panel.index}: {t}", b) for t, b in panel_overflow]
        if panel_covered and panel.index not in covered:
            covered.append(panel.index)
        if fact.get("legends"):
            record["legend"] = fact["legends"]
        record["files"] = {kind: path.name for kind, path in files.items()}
        panel_records.append((record, files))
    layout_doc = {"schema": LAYOUT_SCHEMA, "schema_version": 1, "asset": asset["id"], "layout": layout,
                  "arrangement": arrangement, "size_px": [width, height], "columns": geometry.columns,
                  "rows": geometry.rows,
                  "spacing": spacing, "panel_letters": letters, "language": language,
                  "shared": {"requested": shared, "y": scales["y"], "color": scales["color"],
                             "framing": framing},
                  "panels": [r for r, _f in panel_records]}
    layout_file = wc.write_json(out / "layout.json", layout_doc)
    data_doc = {"asset": asset["id"], "layout": layout, "panels": [_panel_data(p) for p in panels]}
    data_file = wc.write_json(out / "data.json", data_doc)
    files: dict[str, Path] = {"image": written["png"]}
    for kind in ("svg", "pdf"):
        if kind in written:
            files[kind] = written[kind]
    files["layout"] = layout_file
    for record, panel_files in panel_records:
        for kind, path in panel_files.items():
            files[f"panel-{record['index']}" + ("" if kind == "png" else f"-{kind}")] = path
    details = {"layout": layout, "panels": [{"asset": r["asset"], "mode": r["mode"], "label": r["label"][language],
                                             "cell_px": r["cell_px"]} for r, _f in panel_records],
               "scales": scales, "framing": framing, **({"framing_detail": framing_detail} if framing_detail else {}),
               "text_fits": not overflow and not covered,
               **({"text_outside": [t for t, _b in overflow][:10]} if overflow else {}),
               **({"legend_over_data": covered} if covered else {})}
    return {"files": files, "data_files": [data_file], "title": title, "caption": caption,
            "sources": list(dict.fromkeys(s for p in panels for s in (p.entry.get("sources") or []))),
            "depends_on": list(dict.fromkeys(p.asset_id for p in panels)), "details": details}


def _panel_data(panel):
    if panel.mode == "image":
        return {"index": panel.index, "asset": panel.asset_id, "mode": "image",
                "image_sha256": wc.sha256_file(panel.image) if panel.image else None}
    data = panel.data
    if panel.mode in ("sweep", "heatmap"):
        data = {key: data.get(key) for key in ("study", "y", "unit", "parameters", "rows")}
        data["bounds"] = (panel.entry.get("details") or {}).get("bounds")
    return {"index": panel.index, "asset": panel.asset_id, "mode": panel.mode, "data": data}


def _run_marker(fig, text_box, color):
    """A small square in the run's series colour before a panel label."""
    from matplotlib.patches import Rectangle

    size = text_box.height * 0.62
    x0 = text_box.x0 - size * 1.6
    y0 = text_box.y0 + (text_box.height - size) / 2
    width, height = fig.bbox.width, fig.bbox.height
    fig.add_artist(Rectangle((x0 / width, y0 / height), size / width, size / height, transform=fig.transFigure,
                             facecolor=color, edgecolor="none"))


def _arrow(fig, left, right, theme):
    from matplotlib.patches import FancyArrowPatch

    width, height = fig.bbox.width, fig.bbox.height
    gap = right.x0 - left.x1
    y = (max(left.y0, right.y0) + min(left.y1, right.y1)) / 2
    start, end = left.x1 + 0.18 * gap, right.x0 - 0.18 * gap
    if end <= start:
        return
    fig.add_artist(FancyArrowPatch((start / width, y / height), (end / width, y / height), transform=fig.transFigure,
                                   arrowstyle="-|>", mutation_scale=theme.font_pt("title") * 1.6,
                                   linewidth=theme.font_pt("body") / 5, color=theme.colors["muted"]))


def _write_panel_file(panel, box, out, theme, scales, image_module):
    """``panel-<n>.png`` at the panel's own pixel size (re-drawn plots also as ``panel-<n>.svg``)."""

    name = f"panel-{panel.index}"
    if panel.raster:
        content = box["content"] or box["cell"]
        size = (max(1, round(content.width)), max(1, round(content.height)))
        image = raster_pixels(panel)
        if image.size != size:
            image = image.resize(size, image_module.LANCZOS)
        path = out / f"{name}.png"
        image.save(path, format="PNG", optimize=True)
        return {"png": path}, [], False
    cell = box["cell"]
    size = (max(100, round(cell.width)), max(100, round(cell.height)))
    with drawing(theme) as plt:
        fig = new_figure(plt, theme, size)
        fact = _draw_panel_content(fig, panel, theme, scales)
        shared = bool(scales["y"].get("applied")) and panel.index in scales["y"].get("panels", [])
        finish_panel(fig, fact, adjust_limits=not shared)
        if shared and scales["y"].get("axis_range"):
            for ax in fact.get("value_axes", []):
                ax.set_ylim(*scales["y"]["axis_range"])
        overflow = text_overflow(fig)
        covered = legends_over_data(fig, [panel], [fact])
        return save_figure(fig, out, name, theme, formats=["png", "svg"]), overflow, bool(covered)
