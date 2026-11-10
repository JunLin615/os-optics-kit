"""Presentation material from saved results: system views, result plots, comparisons, animations, an offline
gallery and a portable bundle, all redrawn from one saved configuration. Nothing is simulated.

Usage:
  os-optics-kit present init SOURCE [SOURCE ...] --out DIR [--labels A,B] [--title TEXT] [--theme PRESET]
                             [--set KEY=VALUE ...] [--animate]
  os-optics-kit present build DIR/presentation.json [--set KEY=VALUE ...] [--only ID,...] [--out DIR2]
                             [--no-gallery] [--browser PATH] [--quiet]
  os-optics-kit present list DIR
  os-optics-kit present settings
  os-optics-kit present gallery DIR
  os-optics-kit present bundle DIR --out DEST[.zip] [--only ID,...] [--overwrite]

SOURCE is a saved run directory (it holds manifest.json) or a sweep study directory (summary.json); its id in
presentation.json is the folder name (a folder named "run" takes its parent's name). init writes
DIR/presentation.json: the theme, the sources (paths relative to the file) and default assets — a system view and
the power distribution per run, a target chart for runs with targets, a curve per study target, and with several
runs a target comparison and a side-by-side view; --animate adds an orbit and a playback of the first study.
Edit the file or use --set, then build.

build draws every asset into the specification's directory (or --out DIR2, which receives its own
presentation.json): assets/<id>/ (image, vector formats, data, captions, provenance.json), previews/<id>.png,
assets.json (the asset index), index.html (offline gallery) and contact-sheet.png. --set applies settings and
saves them in the built presentation.json, so a restyled variant can be rebuilt:
  --set theme.language=zh-CN --set theme.background=light --set theme.font_scale=1.3 --set theme.size=4k
  --set assets.system-view.camera=[30,15] --set 'assets.sweep.caption={"en": "...", "zh-CN": "..."}'
Assets whose inputs and settings are unchanged are reused, not redrawn. An asset whose source, target or panel is
missing is reported with a recovery step; the other assets are still built (status INCOMPLETE, exit 2).

settings prints every theme key, size, asset kind and field. list prints the compact asset index of a built
directory. gallery rewrites index.html and contact-sheet.png. bundle copies selected assets, their captions,
data, provenance and redraw settings with an offline gallery into a directory or .zip (relative links only).

Needs matplotlib (os-optics-kit[plot]); views and animations need Chrome/Chromium and os-optics-kit[render];
MP4 needs os-optics-kit[media]. Stdout: one compact JSON document; progress lines on stderr.
Exit codes: 0 done; 2 INCOMPLETE: some assets were not built (a missing source, target or panel, or a missing
font, browser or media extra), each named in missing[] with a recovery step; 4 refused specification, setting or
output directory.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
from typing import Any

from . import presentation as pres
from . import workflow_common as wc
from ._version import __version__

PROG = "os-optics-kit present"
EXIT_CODES = {"COMPLETED": 0, "INCOMPLETE": 2}
DEFAULT_CAMERA = [55.0, 22.0]
VIEW_MARGIN = 0.08


# --------------------------------------------------------------------------------------------- build context

class BuildContext:
    """What an asset handler may use: the specification, theme, sources, its output folder, cache and progress."""

    def __init__(self, spec, spec_dir, out_dir, *, browser=None, quiet=False):
        self.spec = spec
        self.spec_dir = Path(spec_dir)
        self.out_dir = Path(out_dir)
        self.theme = pres.resolve_theme(spec.get("theme"))
        self._browser = browser
        self.quiet = quiet
        self._sources: dict[str, pres.Source] = {}
        self._built: dict[str, dict] = {}
        self._progress_mark: dict[str, int] = {}

    @property
    def browser(self):
        """The Chrome/Chromium of --browser, else one found on this machine (None when there is none)."""
        if self._browser is None:
            from .render_views import find_browser

            self._browser = find_browser(None) or ""
        return self._browser or None

    def asset_theme(self, asset):
        return pres.resolve_theme(self.spec.get("theme"), asset.get("theme"))

    def source(self, source_id):
        if source_id not in self._sources:
            entry = (self.spec.get("sources") or {}).get(source_id)
            if entry is None:
                raise pres.PresentationError(
                    "SOURCE_UNKNOWN", f"source {source_id!r} is not declared in presentation.json",
                    recovery=f"add sources.{source_id}.path or use one of {sorted(self.spec.get('sources') or {})}",
                    source=source_id)
            self._sources[source_id] = pres.resolve_source(self.spec_dir, source_id, entry)
        return self._sources[source_id]

    def asset_dir(self, asset_id):
        path = pres.asset_dir(self.out_dir, asset_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def cache_dir(self, key):
        path = self.out_dir / ".cache" / key
        path.mkdir(parents=True, exist_ok=True)
        return path

    def built(self, asset_id):
        return self._built.get(asset_id)

    def progress(self, asset_id, done, total):
        """Coarse progress on stderr: at most about ten lines per asset."""
        if self.quiet or total <= 0:
            return
        step = max(1, total // 10)
        if done == total or done - self._progress_mark.get(asset_id, 0) >= step:
            self._progress_mark[asset_id] = done
            print(json.dumps({"progress": {"asset": asset_id, "done": done, "total": total}}), file=sys.stderr,
                  flush=True)


# --------------------------------------------------------------------------------------------- handlers

def _camera(value):
    camera = list(value if value is not None else DEFAULT_CAMERA)
    if len(camera) not in (2, 3) or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in camera):
        raise pres.PresentationError("SPEC_INVALID", f"camera {value!r}: use [angle to axis, elevation, roll?] in "
                                     "degrees")
    return [float(v) for v in camera] + ([0.0] if len(camera) == 2 else [])


def padded_box(box, margin):
    low, high = box
    size = max(high[i] - low[i] for i in range(3)) or 1.0
    pad = margin * size
    return [[v - pad for v in low], [v + pad for v in high]]


def view_box(asset, ctx, view):
    """Framing of a view asset: ``box`` [min, max] mm, else ``framing_sources`` (shared framing of several runs),
    else the ``focus`` of its own run; padded by ``margin`` (fraction of the largest box size)."""
    from .render_views import ViewScene, union_box

    focus = asset.get("focus", "optics")
    if asset.get("box") is not None:
        box = asset["box"]
    elif asset.get("framing_sources"):
        boxes = []
        for source_id in asset["framing_sources"]:
            try:
                source = ctx.source(source_id)
            except pres.PresentationError:
                continue  # reported with the view of that run; the others keep their shared framing
            boxes.append(view.box(focus) if source.path == ctx.source(asset["source"]).path
                         else ViewScene(source.path).box(focus))
        box = union_box(boxes)
    else:
        box = view.box(focus)
    return padded_box(box, float(asset.get("margin", VIEW_MARGIN)))


def build_view(asset, ctx):
    """A system view of a saved run through the engine's viewer: clean PNG, editable text overlay (SVG), an
    annotated PNG when the theme shows titles, and optionally the interactive viewer page."""
    from .render_views import (RenderError, ViewScene, camera_direction, left_to_right, render_shots,
                               straight_layout)

    theme = ctx.asset_theme(asset)
    source = ctx.source(_required(asset, "source"))
    if source.kind != "run":
        raise pres.PresentationError("SPEC_INVALID", f"asset {asset['id']}: a view needs a run source, "
                                     f"{source.id!r} is a study", recovery="pick a run source or a sample run")
    azimuth, elevation, roll = _camera(asset.get("camera"))
    out = ctx.asset_dir(asset["id"])
    try:
        view = ViewScene(source.path, axis=_axis_text(asset.get("axis")))
        box = view_box(asset, ctx, view)
        direction, up = camera_direction(view.axis, azimuth, elevation)
        orientation = asset.get("orientation", "auto")
        if orientation not in ("auto", "left-to-right", "as-given"):
            raise pres.PresentationError("SPEC_INVALID", f"asset {asset['id']}: orientation {orientation!r}; use "
                                         "auto, left-to-right or as-given", key=f"assets.{asset['id']}.orientation")
        if orientation == "left-to-right" or (orientation == "auto" and straight_layout(view.box("scene"), view.axis,
                                                                    classes=[c for c, _p in view.parts.values()])):
            direction, up = left_to_right(direction, up, view.axis)
        records = render_shots(source.path, [{"name": asset["id"], "direction": direction, "up": up,
                                              "roll_deg": roll}], out, size=theme.size,
                               background=theme.render_background(), box=box,
                               focus=asset.get("focus", "optics"), beams=asset.get("beams", True),
                               labels=asset.get("labels", False), edges=asset.get("edges", False),
                               browser=ctx.browser, view=view)
    except RenderError as error:
        raise pres.PresentationError(error.code, str(error), recovery=_render_recovery(error.code),
                                     **error.details) from None
    record = records[0]
    if record["status"] != "captured":
        raise pres.PresentationError(record["error"]["code"], record["error"]["message"],
                                     recovery=_render_recovery(record["error"]["code"]))
    image = out / f"{asset['id']}.png"
    label = {lang: pres.localized(source.label, lang, source.id) for lang in pres.LANGUAGES}
    several = len(ctx.spec.get("sources") or {}) > 1
    title = {lang: pres.text("title.view_of", lang, source=label[lang]) if several else pres.text("title.view", lang)
             for lang in pres.LANGUAGES}
    caption = {lang: pres.text("caption.view", lang, subject=label[lang] if several else title[lang],
                               angle=f"{azimuth:g}", elevation=f"{elevation:g}") for lang in pres.LANGUAGES}
    files: dict[str, Path] = {"image": image}
    shown_title = pres.title_text(asset, theme, title)
    shown_caption = pres.caption_text(asset, theme, caption)
    provenance = ""
    if theme.provenance_in_figure:  # report preset: engine build and catalog release with the image
        identity = source.identity
        catalog = identity.get("catalog_version")
        provenance = " | ".join((pres.text("caption.engine", theme.language,
                                           build=identity.get("engine_build_id") or "-"),
                                 pres.text("caption.catalog", theme.language, version=catalog) if catalog
                                 else pres.text("caption.no_catalog", theme.language)))
    files["overlay"] = write_overlay(out / f"{asset['id']}.overlay.svg", theme, shown_title if theme.title else "",
                                     " | ".join(part for part in (shown_caption, provenance) if part))
    if theme.title or theme.captions == "figure" or theme.provenance_in_figure:
        footer = " | ".join(part for part in (shown_caption if theme.captions == "figure" else "", provenance)
                            if part)
        files["annotated"] = write_annotated(image, out / f"{asset['id']}.annotated.png", theme,
                                             shown_title if theme.title else "", footer)
    if asset.get("interactive"):
        viewer = out / "viewer.html"
        viewer.write_bytes(view.interactive_page())
        files["viewer"] = viewer
    camera = {"angle_to_axis_deg": azimuth, "elevation_deg": elevation, "roll_deg": roll,
              "applied": record.get("applied_camera"), "box_mm": [record["box_min"], record["box_max"]]}
    camera_file = wc.write_json(out / "camera.json", camera)
    return {"files": files, "data_files": [camera_file], "title": title, "caption": caption, "sources": [source.id],
            "details": {"camera": [azimuth, elevation, roll], "focus": asset.get("focus", "optics"),
                        "box_mm": camera["box_mm"], "beams": asset.get("beams", True)}}


def _axis_text(value):
    """A view's ``axis``: [X, Y, Z] or "X,Y,Z" -> the render command's "X,Y,Z" (None: the view's main axis)."""
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, list) and len(value) == 3 and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                                           for v in value):
        return ",".join(repr(float(v)) for v in value)
    raise pres.PresentationError("SPEC_INVALID", f"axis {value!r}: give the optical axis as [X, Y, Z]",
                                 recovery="set the asset's axis to three numbers, e.g. [1, 0, 0]")


def _render_recovery(code):
    return {"BROWSER_NOT_FOUND": "install Chrome or Chromium, or pass --browser PATH",
            "CAPTURE_NOT_INSTALLED": 'python -m pip install "os-optics-kit[render]"',
            "FOCUS_UNKNOWN": "set the asset's focus to optics, scene or element:<id> of a part in the run",
            "CAMERA_INVALID": "use |elevation| <= min(angle, 180 - angle) with 0 <= angle <= 180",
            "CAMERA_UNDEFINED": "set the asset's axis to the optical axis X,Y,Z",
            "CAPTURE_TIMEOUT": "rebuild; on a slow machine render fewer or smaller views"}.get(code)


def write_overlay(path, theme, title, caption):
    """Transparent SVG the size of the image with the title (top) and caption (bottom) as editable text."""
    width, height = theme.size
    title_px = theme.font_pt("title") * height / pres.CANVAS_HEIGHT_IN / 72
    caption_px = theme.font_pt("caption") * height / pres.CANVAS_HEIGHT_IN / 72 * 1.4
    family = ", ".join(f"'{name}'" for name in theme.font_family) + ", sans-serif"
    color = theme.colors["text"]

    def esc(value):
        return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
             f'viewBox="0 0 {width} {height}" font-family="{esc(family)}" fill="{color}">']
    if title:
        parts.append(f'<text id="title" x="{width * 0.03:.1f}" y="{height * 0.04 + title_px:.1f}" '
                     f'font-size="{title_px:.1f}" font-weight="600">{esc(title)}</text>')
    if caption:
        parts.append(f'<text id="caption" x="{width * 0.03:.1f}" y="{height * 0.96:.1f}" '
                     f'font-size="{caption_px:.1f}" fill="{theme.colors["muted"]}">{esc(caption)}</text>')
    parts.append("</svg>")
    return wc.write_text_atomic(path, "\n".join(parts) + "\n")


def write_annotated(image, path, theme, title, caption):
    """The render with its title above and caption below, in the theme's fonts and colours (same pixel size)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.image as mpimg
    import matplotlib.pyplot as plt

    pres.check_glyphs([title, caption], theme)
    figsize, dpi = theme.canvas()
    with plt.rc_context(theme.rc()):
        fig = plt.figure(figsize=figsize, dpi=dpi)
        top = 0.88 if title else 0.98
        bottom = 0.08 if caption else 0.02
        ax = fig.add_axes((0.02, bottom, 0.96, top - bottom))
        ax.imshow(mpimg.imread(image))
        ax.set_axis_off()
        if title:
            fig.text(0.03, 0.95, title, fontsize=theme.font_pt("title"), fontweight="bold", va="center")
        if caption:
            fig.text(0.03, 0.035, caption, fontsize=theme.font_pt("caption"), color=theme.colors["muted"],
                     va="center")
        fig.savefig(path, dpi=dpi, transparent=theme.background == "transparent", metadata={"Software": None})
        plt.close(fig)
    return Path(path)


def _required(asset, key):
    value = asset.get(key)
    if value is None:
        raise pres.PresentationError("SPEC_INVALID", f"asset {asset['id']}: {key!r} is required",
                                     key=f"assets.{asset['id']}.{key}")
    return value


def _plot_style(theme, out, data_file, identity, language, asset=None):
    from . import plot_results as pr

    provenance = pr.caption(data_file, out, engine_build=identity.get("engine_build_id"),
                            catalog_version=identity.get("catalog_version"), language=language)
    style = pr.Style(theme, provenance=provenance, companions=False)
    style.title_override = pres.localized((asset or {}).get("title"), language, "") or None
    return style


def build_plot_sweep(asset, ctx, theme, out):
    from . import plot_results as pr

    source = ctx.source(_required(asset, "source"))
    if source.kind != "study":
        raise pres.PresentationError("SPEC_INVALID", f"asset {asset['id']}: a sweep plot needs a study source")
    summary = wc.read_json(source.path / "summary.json")
    y = _required(asset, "y")
    rows, unit, parameters = pr.study_column(summary, y)
    data = {"study": source.id, "y": y, "unit": unit, "parameters": parameters,
            "rows": [{key: row.get(key) for key in row if key == "sample_id" or key == "outcome" or key == y
                      or key.startswith(f"{y}:") or key.startswith("param:")} for row in rows]}
    data_file = wc.write_json(out / "data.json", data)
    style = _plot_style(theme, out, data_file, source.identity, theme.language, asset)
    names = {lang: (pres.resolve_theme({**ctx.spec.get("theme", {}), "language": lang}).label(y),
                    pres.resolve_theme({**ctx.spec.get("theme", {}), "language": lang}).label(
                        f"param:{parameters[0]['name']}")) for lang in pres.LANGUAGES}
    title = {lang: pres.text("title.sweep", lang, y=names[lang][0], x=names[lang][1]) for lang in pres.LANGUAGES}
    caption = {lang: pr.sweep_caption(lang, rows, y, names[lang][0], names[lang][1]) for lang in pres.LANGUAGES}
    result = pr.plot_sweep(source.path, y, out_dir=out, name=asset["id"], style=style,
                           caption_text=pres.caption_text(asset, theme, caption))
    files = {"image": Path(result["out"]["png"])}
    for kind in ("svg", "pdf"):
        if kind in result["out"]:
            files[kind] = Path(result["out"][kind])
    details = {"y": result.get("y"), "x": result.get("x"), "points": result["points"],
               "by_outcome": result.get("by_outcome"),
               "without_value": result["without_value"], "bounds": result["bounds"], "unit": unit}
    return {"files": files, "data_files": [data_file], "title": title, "caption": caption, "sources": [source.id],
            "details": details}


def build_plot_power(asset, ctx, theme, out):
    from . import plot_results as pr

    source = ctx.source(_required(asset, "source"))
    if source.kind != "run":
        raise pres.PresentationError("SPEC_INVALID", f"asset {asset['id']}: a power plot needs a run source")
    monitors, budget, identity = pr.power_data(source.path)
    data_file = wc.write_json(out / "data.json", {"run": source.id, "monitors": monitors, "power_budget": budget})
    style = _plot_style(theme, out, data_file, identity, theme.language, asset)
    several = len(ctx.spec.get("sources") or {}) > 1
    title = {lang: pres.text("title.power_of", lang, source=pres.localized(source.label, lang, source.id))
             if several else pres.text("title.power", lang) for lang in pres.LANGUAGES}
    caption = {lang: pres.text("caption.power", lang) for lang in pres.LANGUAGES}
    result = pr.plot_power(source.path, out_dir=out, name=asset["id"], style=style,
                           caption_text=pres.caption_text(asset, theme, caption))
    files = {"image": Path(result["out"]["png"])}
    for kind in ("svg", "pdf"):
        if kind in result["out"]:
            files[kind] = Path(result["out"][kind])
    return {"files": files, "data_files": [data_file], "title": title, "caption": caption, "sources": [source.id],
            "details": {"observations_W": result["observations"], "sinks_W": result["sinks_W"],
                        "conserved": result["conserved"]}}


def _targets_plot(asset, ctx, theme, out):
    from . import compose_figures

    return compose_figures.build_targets_plot(asset, ctx, theme, out)


def _compose(asset, ctx):
    from . import compose_figures

    return compose_figures.build_compose(asset, ctx)


def _animation(asset, ctx):
    from . import animate

    return animate.build_animation(asset, ctx)


def _plot_types():
    return {"sweep": build_plot_sweep, "power": build_plot_power, "targets": _targets_plot}


def build_plot(asset, ctx):
    theme = ctx.asset_theme(asset)
    kind = _required(asset, "type")
    types = _plot_types()
    if kind not in types:
        raise pres.PresentationError("SPEC_INVALID", f"asset {asset['id']}: plot type {kind!r}; choose "
                                     f"{sorted(types)}")
    return types[kind](asset, ctx, theme, ctx.asset_dir(asset["id"]))


def _handlers():
    return {"view": build_view, "plot": build_plot, "compose": _compose, "animation": _animation}


# --------------------------------------------------------------------------------------------- build

def references(asset):
    """``(source ids, asset ids)`` an asset definition refers to (keys source/study/sources/framing_sources/asset)."""
    sources, assets = [], []

    def walk(value, key=None):
        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, k)
        elif isinstance(value, list):
            for item in value:
                walk(item, key)
        elif isinstance(value, str):
            if key in ("source", "study", "sources", "framing_sources"):
                sources.append(value)
            elif key in ("asset", "assets"):
                assets.append(value)

    walk({k: v for k, v in asset.items() if k not in ("id", "kind", "title", "caption", "theme")})
    return list(dict.fromkeys(sources)), list(dict.fromkeys(assets))


def build_order(assets):
    """Assets in an order where every referenced asset comes first; refuses unknown references and cycles."""
    by_id = {a["id"]: a for a in assets}
    order, state = [], {}

    def visit(asset_id, chain):
        if state.get(asset_id) == "done":
            return
        if state.get(asset_id) == "visiting":
            raise pres.PresentationError("SPEC_INVALID", f"assets refer to each other in a cycle: {chain}")
        state[asset_id] = "visiting"
        for ref in references(by_id[asset_id])[1]:
            if ref in by_id:
                visit(ref, [*chain, ref])
        state[asset_id] = "done"
        order.append(by_id[asset_id])

    for asset in assets:
        visit(asset["id"], [asset["id"]])
    return order


def _entry_files(files, out_dir):
    return {role: pres.file_record(path, out_dir) for role, path in files.items() if path is not None}


def _reusable(previous, fingerprint, out_dir):
    """Whether the previous build's files of an asset can stand: same fingerprint, every listed file inside the
    output directory and unchanged."""
    if not previous or previous.get("fingerprint") != fingerprint or previous.get("status") not in (
            "written", "reused", "stale"):
        return False
    root = Path(out_dir).resolve()
    for relative in [*(r.get("path") for r in (previous.get("files") or {}).values()), previous.get("preview")]:
        if not isinstance(relative, str):
            return False
        path = (root / relative).resolve()
        if root not in path.parents or not path.is_file():
            return False
    return all(wc.sha256_file(root / record["path"]) == record.get("sha256")
               for record in previous.get("files", {}).values())


def build_asset(asset, ctx, previous):
    """Draw one asset (or reuse it) and return its asset-index entry."""
    theme = ctx.asset_theme(asset)
    source_ids, asset_ids = references(asset)
    optional = set(asset.get("framing_sources") or []) - {asset.get("source")}
    identities, problems = {}, []
    for source_id in source_ids:
        try:
            identities[source_id] = ctx.source(source_id).identity
        except pres.PresentationError as error:
            if source_id not in optional:  # a missing framing source only narrows the shared framing
                problems.append(error)
    for asset_id in asset_ids:
        dependency = ctx.built(asset_id)
        if dependency is None or dependency["status"] not in ("written", "reused", "stale"):
            problems.append(pres.PresentationError(
                "PANEL_MISSING", f"asset {asset['id']} uses asset {asset_id!r}, which is not available",
                recovery=f"add or fix asset {asset_id!r}, or remove it from {asset['id']}", asset=asset_id))
    entry: dict[str, Any] = {"id": asset["id"], "kind": asset["kind"]}
    if problems:
        if pres.asset_dir(ctx.out_dir, asset["id"]).is_dir():  # no file of an earlier build stays behind
            _clear_previous(pres.asset_dir(ctx.out_dir, asset["id"]), asset, ctx)
        (ctx.out_dir / pres.PREVIEWS_DIR / f"{asset['id']}.png").unlink(missing_ok=True)
        return {**entry, "status": "missing_input", "title": pres.localized(asset.get("title"), theme.language, ""),
                "caption": pres.localized(asset.get("caption"), theme.language, ""), "files": {},
                "sources": source_ids, "depends_on": asset_ids, "error": problems[0].as_dict()}
    # source labels and the set of sources decide titles and captions; a replayed sequence decides the frames
    fingerprint = asset_fingerprint(asset, ctx, theme, identities, source_ids, asset_ids)
    if _reusable(previous, fingerprint, ctx.out_dir):
        reused = {**previous, "status": "reused"}
        reused["_abs"] = {role: ctx.out_dir / record["path"] for role, record in previous["files"].items()}
        return reused
    return _draw(asset, ctx, theme, source_ids, asset_ids, entry, fingerprint)


def asset_fingerprint(asset, ctx, theme, identities, source_ids, asset_ids):
    """The inputs that decide an asset's files: definition, settings, source identities and labels, panels,
    a replayed file."""
    replayed = [wc.sha256_file(ctx.spec_dir / asset[key]) for key in ("sequence",)
                if isinstance(asset.get(key), str) and (ctx.spec_dir / asset[key]).is_file()]
    return pres.fingerprint(asset, theme.summary(), theme.labels, identities,
                            [(ctx.built(a) or {}).get("fingerprint") for a in asset_ids],
                                   {s: (ctx.spec.get("sources") or {}).get(s) for s in source_ids},
                            len(ctx.spec.get("sources") or {}), replayed)


def _draw(asset, ctx, theme, source_ids, asset_ids, entry, fingerprint):
    out = ctx.asset_dir(asset["id"])
    _clear_previous(out, asset, ctx)
    try:
        result = _handlers()[asset["kind"]](asset, ctx)
    except (pres.PresentationError, wc.WorkflowError) as error:
        status = "missing_input" if error.code in MISSING_CODES else "failed"
        return {**entry, "status": status, "title": pres.localized(asset.get("title"), theme.language, ""),
                "caption": pres.localized(asset.get("caption"), theme.language, ""), "files": {},
                "sources": source_ids, "depends_on": asset_ids, "error": error.as_dict()}
    except Exception as error:  # noqa: BLE001 - an optional extra or an unexpected problem fails this asset only
        return {**entry, "status": "failed", "title": pres.localized(asset.get("title"), theme.language, ""),
                "caption": "", "files": {}, "sources": source_ids, "depends_on": asset_ids,
                "error": wc.error_document(error)}
    title = pres.title_text(asset, theme, result.get("title"))
    caption = pres.caption_text(asset, theme, result.get("caption"))
    captions = {lang: pres.localized(asset.get("caption"), lang, "") or pres.localized(result.get("caption"), lang, "")
                for lang in pres.LANGUAGES}
    caption_files = {}
    for lang, value in captions.items():
        if value:
            caption_files[lang] = wc.write_text_atomic(out / f"caption.{lang}.txt", value + "\n")
    sources = [ctx.source(s) for s in result.get("sources", source_ids)]
    provenance = wc.write_text_atomic(out / "provenance.json", pres.manifest_text(pres.provenance_record(
        asset, theme, sources, result.get("data_files", []), ctx.out_dir,
        extra={"depends_on": result.get("depends_on", asset_ids)})))
    files = dict(result["files"])
    for index, path in enumerate(result.get("data_files", [])):
        files.setdefault("data" if index == 0 else f"data{index + 1}", Path(path))
    preview = pres.write_preview(files["image"], ctx.out_dir / pres.PREVIEWS_DIR / f"{asset['id']}.png")
    titles = {lang: pres.localized(asset.get("title"), lang, "") or pres.localized(result.get("title"), lang, "")
              for lang in pres.LANGUAGES}
    return {**entry, "status": "written", "title": title, "caption": caption,
            "titles": {lang: value for lang, value in titles.items() if value},
            "captions": {lang: value for lang, value in captions.items() if value},
            "files": _entry_files(files, ctx.out_dir), "preview": pres.portable_path(preview, ctx.out_dir),
            "size_px": pres.image_size(files["image"]), "provenance": pres.portable_path(provenance, ctx.out_dir),
            "caption_files": {lang: pres.portable_path(path, ctx.out_dir) for lang, path in caption_files.items()},
            "sources": [s.id for s in sources], "depends_on": result.get("depends_on", asset_ids),
            "details": wc.json_safe(result.get("details") or {}), "fingerprint": fingerprint,
            "_abs": files}


def _current(asset, ctx, entry):
    """Whether an asset drawn earlier still matches the current settings and sources."""
    source_ids, asset_ids = references(asset)
    try:
        identities = {s: ctx.source(s).identity for s in source_ids}
    except pres.PresentationError:
        return False
    fingerprint = asset_fingerprint(asset, ctx, ctx.asset_theme(asset), identities, source_ids, asset_ids)
    return _reusable(entry, fingerprint, ctx.out_dir)


def _clear_previous(out, asset, ctx):
    """Remove the files of this asset's folder before drawing it again, except files the asset definition refers to
    (a ``sequence`` to replay), so no file of an earlier build stays listed or bundled."""
    keep = set()
    for key in ("sequence",):
        if isinstance(asset.get(key), str):
            keep.add((ctx.spec_dir / asset[key]).resolve())
    for path in out.iterdir():
        if path.is_file() and path.resolve() not in keep:
            path.unlink()


MISSING_CODES = {"SOURCE_MISSING", "SOURCE_UNKNOWN", "SOURCE_UNREADABLE", "COLUMN_UNKNOWN", "TARGET_UNKNOWN",
                 "FOCUS_UNKNOWN", "PANEL_MISSING", "STUDY_NOT_READABLE", "RUN_UNREADABLE", "SAMPLES_MISSING"}


def rebase_sources(spec, spec_dir, out_dir):
    """Copy of ``spec`` whose source paths are relative to ``out_dir`` instead of ``spec_dir``."""
    rebased = copy.deepcopy(spec)
    for asset in rebased.get("assets", []):  # files an asset replays (an animation's sequence.json)
        if isinstance(asset.get("sequence"), str) and not Path(asset["sequence"]).is_absolute():
            moved = pres.portable_path(Path(spec_dir) / asset["sequence"], out_dir)
            if moved is not None:
                asset["sequence"] = moved
    for source_id, entry in rebased.get("sources", {}).items():
        path = Path(entry["path"])
        absolute = path if path.is_absolute() else Path(spec_dir) / path
        relative = pres.portable_path(absolute, out_dir)
        if relative is None:
            raise pres.PresentationError("OUTPUT_INVALID", f"source {source_id!r} is on another drive than the "
                                         "output directory, so it has no relative path",
                                         recovery="build into a directory on the same drive as the sources")
        entry["path"] = relative
    return rebased


def check_output(out_dir, sources, overwrite):
    out_dir = Path(out_dir).resolve()
    for source in sources:
        root = source.path.resolve()
        if out_dir == root or root in out_dir.parents:
            raise pres.PresentationError("OUTPUT_INVALID", f"the output directory is inside source {source.id!r}",
                                         recovery="choose an output directory outside the saved runs and studies")
    if out_dir.is_dir() and any(out_dir.iterdir()):
        ours = (out_dir / pres.SPEC_FILE).is_file() or (out_dir / pres.MANIFEST_FILE).is_file()
        if not ours and not overwrite:
            raise pres.PresentationError("OUTPUT_EXISTS", f"{out_dir.name} already holds other files",
                                         recovery="choose a new --out directory")


def build(spec_path, *, settings=(), only=None, out_dir=None, gallery=True, browser=None, quiet=False,
          overwrite=False):
    """Build the assets of a specification; returns the compact result (see the module docstring)."""
    spec_path = Path(spec_path)
    spec = pres.load_spec(spec_path)
    for item in settings:
        key, sep, value = item.partition("=")
        if not sep:
            raise pres.PresentationError("SETTING_INVALID", f"--set {item!r}: use KEY=VALUE, e.g. "
                                         "theme.language=zh-CN")
        pres.set_value(spec, key.strip(), pres.parse_value(value))
    pres.validate_spec(spec)
    spec_dir = spec_path.parent
    out_dir = Path(out_dir) if out_dir else spec_dir
    in_place = out_dir.resolve() == spec_dir.resolve()
    ctx_spec = spec if in_place else rebase_sources(spec, spec_dir, out_dir)
    ctx = BuildContext(ctx_spec, out_dir, out_dir, browser=browser, quiet=quiet)
    declared = []
    for source_id in ctx_spec.get("sources", {}):
        try:
            declared.append(ctx.source(source_id))
        except pres.PresentationError:
            pass  # reported on the assets that use it
    check_output(out_dir, declared, overwrite)
    out_dir.mkdir(parents=True, exist_ok=True)
    if settings or not in_place:
        pres.save_spec(ctx_spec, out_dir / pres.SPEC_FILE)
    previous_manifest = pres.read_manifest(out_dir) or {}
    previous = {entry["id"]: entry for entry in previous_manifest.get("assets", [])}
    wanted = None
    if only:
        wanted = set(only)
        unknown = sorted(wanted - {a["id"] for a in ctx_spec["assets"]})
        if unknown:
            raise pres.PresentationError("ASSET_UNKNOWN", f"no assets {unknown}",
                                         recovery="run 'os-optics-kit present list DIR' for the asset ids")
    assets = [a for a in ctx_spec["assets"] if a.get("enabled", True)]
    order = build_order(assets)
    if wanted is not None:  # the selected assets, every asset they use and every composition that uses them
        for asset in reversed(order):
            if asset["id"] in wanted:
                wanted.update(references(asset)[1])
        for asset in order:  # compositions drawn before are redrawn with the new panel
            if set(references(asset)[1]) & wanted and asset["id"] in previous:
                wanted.add(asset["id"])
    entries: dict[str, dict] = {}
    for index, asset in enumerate(order):
        if wanted is not None and asset["id"] not in wanted:
            if asset["id"] in previous:
                entry = {**previous[asset["id"]]}
                if entry.get("status") in ("written", "reused", "stale"):  # carried over, not drawn now
                    entry["status"] = "reused" if _current(asset, ctx, entry) else "stale"
                entry["_abs"] = {role: out_dir / record["path"] for role, record in entry.get("files", {}).items()}
            else:
                entry = {"id": asset["id"], "kind": asset["kind"], "status": "pending", "files": {}}
        else:
            entry = build_asset(asset, ctx, previous.get(asset["id"]))
        ctx._built[asset["id"]] = entry
        entries[asset["id"]] = entry
        if not quiet:
            print(json.dumps({"progress": {"assets_done": index + 1, "assets_total": len(order), "asset": asset["id"],
                                           "status": entry["status"]}}), file=sys.stderr, flush=True)
    _remove_stale(out_dir, previous, entries)
    ordered = [{k: v for k, v in entries[a["id"]].items() if k != "_abs"} for a in assets]
    counts = {status: sum(1 for e in ordered if e["status"] == status)
              for status in ("written", "reused", "stale", "missing_input", "failed", "pending")}
    theme = ctx.theme
    manifest = {"schema": pres.MANIFEST_SCHEMA, "schema_version": pres.SCHEMA_VERSION, "kit_version": __version__,
                "generated": wc.utc_now(), "title": pres.localized(ctx_spec.get("title"), theme.language, ""),
                "titles": {lang: pres.localized(ctx_spec.get("title"), lang, "") for lang in pres.LANGUAGES
                           if pres.localized(ctx_spec.get("title"), lang, "")},
                "language": theme.language, "theme": theme.summary(), "spec": pres.SPEC_FILE,
                "spec_sha256": wc.sha256_file(out_dir / pres.SPEC_FILE), "assets": ordered, "gallery": None,
                "counts": counts}
    pres.write_manifest(out_dir, manifest)
    if gallery:
        from . import gallery as gallery_module

        manifest["gallery"] = gallery_module.write_gallery(out_dir, manifest)
        pres.write_manifest(out_dir, manifest)
    missing = [{"asset": e["id"], **{k: e["error"].get(k) for k in ("code", "message", "recovery") if
                                     e["error"].get(k)}} for e in ordered if e.get("error")]
    return {"status": "INCOMPLETE" if missing else "COMPLETED", "out": out_dir.as_posix(),
            "spec": pres.SPEC_FILE, "index": pres.MANIFEST_FILE, "gallery": manifest["gallery"], "counts": counts,
            "assets": [compact_entry(e) for e in ordered], "missing": missing}


def _remove_stale(out_dir, previous, entries):
    """Delete the files of assets that the previous build wrote and this build no longer has. Only files inside
    that asset's own folder and its preview are deleted, whatever the previous assets.json lists."""
    out_dir = Path(out_dir).resolve()
    for asset_id, entry in previous.items():
        if asset_id in entries or not isinstance(asset_id, str) or not pres.ASSET_ID.fullmatch(asset_id):
            continue
        folder = pres.asset_dir(out_dir, asset_id)
        preview = out_dir / pres.PREVIEWS_DIR / f"{asset_id}.png"
        listed = [*(r.get("path") for r in (entry.get("files") or {}).values() if isinstance(r, dict)),
                  entry.get("preview"), entry.get("provenance"), *(entry.get("caption_files") or {}).values()]
        for relative in listed:
            if not isinstance(relative, str):
                continue
            path = (out_dir / relative).resolve()
            if path == preview or path.parent == folder.resolve():
                path.unlink(missing_ok=True)
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()


def compact_entry(entry):
    files = entry.get("files") or {}
    out = {"id": entry["id"], "kind": entry["kind"], "status": entry["status"], "title": entry.get("title"),
           "preview": entry.get("preview"), "image": (files.get("image") or {}).get("path")}
    for role in ("mp4", "gif", "svg"):
        if role in files:
            out[role] = files[role]["path"]
    if entry.get("error"):
        out["error"] = entry["error"].get("code")
    return out


# --------------------------------------------------------------------------------------------- init

def _source_id(path, taken):
    """Source id from the folder name (a folder named "run" takes its parent's name): lower-case ASCII letters,
    digits, '-' and '_'; a name without any of them (e.g. a Chinese folder name) becomes source, source-2, ..."""
    def ascii_id(name):
        return "".join(c if (c.isascii() and c.isalnum()) or c in "_-" else "-" for c in name.lower()).strip("-_")

    base = ascii_id(Path(path).resolve().name)
    if base == "run":  # samples/<id>/run or <name>/run
        base = ascii_id(Path(path).resolve().parent.name)
    base = (base or "source")[:48].strip("-_") or "source"
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def _run_targets(path):
    from os_optics.results import load_run

    return [t["target_id"] for t in load_run(path).targets()["targets"]]


def _study_targets(path):
    summary = wc.read_json(Path(path) / "summary.json")
    return [c for c in summary.get("columns", []) if c.startswith("target:") and ":" not in c[len("target:"):]
            and any(isinstance(r.get(c), (int, float)) for r in summary.get("rows", []))]


def init(paths, out_dir, *, labels=None, title=None, theme=None, settings=(), animate=False, overwrite=False):
    """Write ``out_dir/presentation.json`` with default assets for the sources; returns the compact plan."""
    out_dir = Path(out_dir)
    spec_path = out_dir / pres.SPEC_FILE
    if spec_path.exists() and not overwrite:
        raise pres.PresentationError("OUTPUT_EXISTS", f"{pres.SPEC_FILE} already exists in {out_dir.name}",
                                     recovery="edit it and run 'present build', or choose a new --out directory")
    for path in paths:
        root = Path(path).resolve()
        if root == out_dir.resolve() or root in out_dir.resolve().parents:
            raise pres.PresentationError("OUTPUT_INVALID", f"--out is inside the source {Path(path).name}",
                                         recovery="choose an output directory outside the saved runs and studies")
    if labels is not None and len(labels) != len(paths):
        raise pres.PresentationError("BAD_ARGUMENT", f"{len(labels)} labels for {len(paths)} sources")
    spec = pres.default_spec()
    if title:
        spec["title"] = title
    if theme:
        spec["theme"]["preset"] = theme
    runs, studies = [], []
    for index, path in enumerate(paths):
        kind = pres.source_kind(path)
        if kind is None:
            raise pres.PresentationError("SOURCE_MISSING", f"{Path(path).name} is not a saved run or study directory",
                                         recovery="pass run directories (manifest.json) or sweep study directories "
                                                  "(summary.json)")
        source_id = _source_id(path, spec["sources"])
        relative = pres.portable_path(path, out_dir)
        if relative is None:
            raise pres.PresentationError("OUTPUT_INVALID", f"{Path(path).name} is on another drive than --out",
                                         recovery="choose an output directory on the same drive")
        entry: dict[str, Any] = {"path": relative}
        folder = Path(path).resolve()
        name = folder.parent.name if folder.name.lower() == "run" else folder.name
        if labels:
            entry["label"] = labels[index]
        elif name.lower() != source_id:  # e.g. a Chinese folder name: the id is ASCII, the label keeps the name
            entry["label"] = name
        spec["sources"][source_id] = entry
        (runs if kind == "run" else studies).append((source_id, Path(path)))
    assets = spec["assets"]
    for position, (source_id, path) in enumerate(runs):
        assets.append({"id": f"view-{source_id}", "kind": "view", "source": source_id, "camera": DEFAULT_CAMERA,
                       "focus": "optics", "beams": True, "interactive": position == 0,
                       **({"framing_sources": [s for s, _ in runs]} if len(runs) > 1 else {})})
        assets.append({"id": f"power-{source_id}", "kind": "plot", "type": "power", "source": source_id})
        targets = _run_targets(path)
        if targets and len(runs) == 1:
            assets.append({"id": f"targets-{source_id}", "kind": "plot", "type": "targets", "sources": [source_id]})
    if len(runs) > 1:
        if any(_run_targets(path) for _, path in runs):
            assets.append({"id": "targets-compare", "kind": "plot", "type": "targets",
                           "sources": [s for s, _ in runs]})
        # a row: two runs may be alternatives, so "before"/"after" is left to the user (layout before-after)
        assets.append({"id": "views-compare", "kind": "compose", "layout": "row",
                       "panels": [{"asset": f"view-{s}"} for s, _ in runs]})
    for source_id, path in studies:
        for column in _study_targets(path)[:4]:
            assets.append({"id": f"sweep-{source_id}-{column.split(':', 1)[1]}"[:64].lower().replace(":", "-"),
                           "kind": "plot", "type": "sweep", "source": source_id, "y": column})
    if runs and studies:
        sweep = next((a for a in assets if a.get("type") == "sweep"), None)
        if sweep:
            base = (wc.read_json(studies[0][1] / "study.json").get("base_project") or {}).get("project_hash")
            # the view of the design the study varied: the run of the study's base project, else the first run
            run_id = next((s for s, path in runs if base and pres.source_identity("run", path).get("project_hash")
                           == base), runs[0][0])
            assets.append({"id": "system-and-result", "kind": "compose", "layout": "system-plot",
                           "panels": [{"asset": f"view-{run_id}"}, {"asset": sweep["id"]}]})
    if animate:
        if runs:
            assets.append({"id": f"orbit-{runs[0][0]}", "kind": "animation", "mode": "orbit", "source": runs[0][0]})
        if studies:
            columns = _study_targets(studies[0][1])
            if columns:
                assets.append({"id": f"playback-{studies[0][0]}", "kind": "animation", "mode": "playback",
                               "source": studies[0][0], "y": columns[0]})
    for item in settings:
        key, sep, value = item.partition("=")
        if not sep:
            raise pres.PresentationError("SETTING_INVALID", f"--set {item!r}: use KEY=VALUE")
        pres.set_value(spec, key.strip(), pres.parse_value(value))
    out_dir.mkdir(parents=True, exist_ok=True)
    pres.save_spec(spec, spec_path)
    return {"status": "COMPLETED", "spec": spec_path.as_posix(), "sources": {k: v["path"] for k, v in
                                                                             spec["sources"].items()},
            "assets": [{"id": a["id"], "kind": a["kind"], **({"type": a["type"]} if "type" in a else {}),
                        **({"layout": a["layout"]} if "layout" in a else {}),
                        **({"mode": a["mode"]} if "mode" in a else {})} for a in assets],
            "label_keys": _label_keys(runs, studies),
            "next": f"os-optics-kit present build {spec_path.as_posix()}"}


def _label_keys(runs, studies):
    """Identifiers shown in the figures (targets, swept parameters): theme.labels gives them display names."""
    keys = []
    for _source_id, path in runs:
        keys += [f"target:{t}" for t in _run_targets(path)]
    for _source_id, path in studies:
        summary = wc.read_json(Path(path) / "summary.json")
        keys += _study_targets(path) + [f"param:{p['name']}" for p in summary.get("parameters", [])]
    return list(dict.fromkeys(keys))


# --------------------------------------------------------------------------------------------- settings / list

def settings_document():
    """Every theme key with its values, the sizes, asset kinds and their fields (compact)."""
    from . import animate, compose_figures

    presets = {name: {k: v for k, v in values.items() if k not in ("labels", "units", "font_family")}
               for name, values in pres.PRESETS.items()}
    return {"status": "COMPLETED", "spec_schema": pres.SPEC_SCHEMA, "languages": list(pres.LANGUAGES),
            "theme": {"preset": sorted(pres.PRESETS), "language": list(pres.LANGUAGES),
                      "background": ["light", "dark", "transparent", "#rrggbb"],
                      "palette": [*sorted(pres.PALETTES), "[#rrggbb, ...]"], "font_scale": "0.5..3",
                      "base_font_pt": "5..40", "title": "true|false", "legend": ["auto", "outside", "none"],
                      "size": {**{k: f"{w}x{h}" for k, (w, h) in pres.SIZES.items()}, "custom": "WIDTHxHEIGHT"},
                      "formats": ["png", "svg", "pdf"], "captions": ["separate", "figure"],
                      "provenance_in_figure": "true|false", "grid": "true|false",
                      "units": {"from": list(pres.DISPLAY_UNITS), "example": {"mm": "um"}},
                      "labels": {"example": {"target:spot_radius": {"en": "Spot radius", "zh-CN": "光斑半径"}}},
                      "font_family": "name or list; Chinese falls back to an installed CJK font"},
            "presets": presets,
            "assets": {
                "common": {"id": "[a-z0-9][a-z0-9_-]{0,63}", "title": "text or {en, zh-CN}",
                           "caption": "text or {en, zh-CN}", "theme": "per-asset theme keys, e.g. {\"size\": \"half\"}",
                           "enabled": "true|false"},
                "view": {"source": "run source id", "camera": "[angle to optical axis, elevation, roll?] deg",
                         "focus": "optics | scene | element:<id>", "beams": "true", "labels": "false",
                         "edges": "false", "margin": VIEW_MARGIN, "framing_sources": "[run ids] shared framing",
                         "box": "[[x,y,z],[x,y,z]] mm", "interactive": "false: also write the interactive viewer",
                         "orientation": "auto (default: a straight system is shown with light travelling left "
                                        "to right; a folded one from the requested side) | left-to-right | "
                                        "as-given"},
                "plot": {"type": {"sweep": {"source": "study id", "y": "target:<id> | derived:<name> | param:<name>"},
                                  "power": {"source": "run id"},
                                  **compose_figures.settings()["plot"]}},
                "compose": compose_figures.settings()["compose"],
                "animation": animate.settings()},
            "set_examples": ["theme.language=zh-CN", "theme.background=light", "theme.font_scale=1.3",
                             "theme.size=4k", "theme.title=false", "assets.<id>.camera=[30,15]",
                             "assets.<id>.caption={\"en\": \"...\", \"zh-CN\": \"...\"}"]}


def list_assets(out_dir):
    manifest = pres.read_manifest(out_dir)
    if manifest is None:
        raise pres.PresentationError("NOT_BUILT", f"{Path(out_dir).name} has no {pres.MANIFEST_FILE}",
                                     recovery="run 'os-optics-kit present build DIR/presentation.json'")
    return {"status": "COMPLETED", "title": manifest.get("title"), "language": manifest.get("language"),
            "gallery": manifest.get("gallery"), "counts": manifest.get("counts"),
            "assets": [{**compact_entry(e), "caption": (e.get("caption") or "")[:200]} for e in manifest["assets"]]}


# --------------------------------------------------------------------------------------------- command

def _split(value):
    return [item.strip() for item in value.split(",") if item.strip()] if value else None


def main(argv=None):
    def workflow():
        parser = wc.ArgumentParser(prog=PROG, description=__doc__,
                                   formatter_class=argparse.RawDescriptionHelpFormatter)
        sub = parser.add_subparsers(dest="action", required=True, parser_class=wc.ArgumentParser)
        p_init = sub.add_parser("init", help="write presentation.json for saved runs and studies")
        p_init.add_argument("sources", nargs="+", type=Path)
        p_init.add_argument("--out", type=Path, required=True)
        p_init.add_argument("--labels", help="comma-separated display labels, one per source")
        p_init.add_argument("--title")
        p_init.add_argument("--theme", choices=sorted(pres.PRESETS))
        p_init.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
        p_init.add_argument("--animate", action="store_true", help="add an orbit and a study playback")
        p_init.add_argument("--overwrite", action="store_true")
        p_build = sub.add_parser("build", help="draw the assets of a presentation.json")
        p_build.add_argument("spec", type=Path)
        p_build.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
        p_build.add_argument("--only", help="comma-separated asset ids")
        p_build.add_argument("--out", type=Path, help="another directory for this variant")
        p_build.add_argument("--no-gallery", action="store_true")
        p_build.add_argument("--browser", help="Chrome/Chromium executable")
        p_build.add_argument("--quiet", action="store_true", help="no progress lines on stderr")
        p_build.add_argument("--overwrite", action="store_true", help="build into a non-empty directory")
        p_list = sub.add_parser("list", help="compact asset index of a built directory")
        p_list.add_argument("dir", type=Path)
        sub.add_parser("settings", help="theme keys, sizes, asset kinds and fields")
        p_gallery = sub.add_parser("gallery", help="rewrite index.html and contact-sheet.png")
        p_gallery.add_argument("dir", type=Path)
        p_bundle = sub.add_parser("bundle", help="portable bundle of selected assets")
        p_bundle.add_argument("dir", type=Path)
        p_bundle.add_argument("--out", type=Path, required=True)
        p_bundle.add_argument("--only", help="comma-separated asset ids")
        p_bundle.add_argument("--overwrite", action="store_true")
        args = parser.parse_args(argv)
        if args.action == "init":
            return init(args.sources, args.out, labels=_split(args.labels), title=args.title, theme=args.theme,
                        settings=args.set, animate=args.animate, overwrite=args.overwrite)
        if args.action == "build":
            return build(args.spec, settings=args.set, only=_split(args.only), out_dir=args.out,
                         gallery=not args.no_gallery, browser=args.browser, quiet=args.quiet,
                         overwrite=args.overwrite)
        if args.action == "list":
            return list_assets(args.dir)
        if args.action == "settings":
            return settings_document()
        from . import gallery as gallery_module

        if args.action == "gallery":
            if pres.read_manifest(args.dir) is None:
                raise pres.PresentationError("NOT_BUILT", f"{args.dir.name} has no {pres.MANIFEST_FILE}",
                                             recovery="run 'os-optics-kit present build' first")
            return {"status": "COMPLETED", **gallery_module.write_gallery(args.dir)}
        return {"status": "COMPLETED", **gallery_module.write_bundle(args.dir, args.out, only=_split(args.only),
                                                                     overwrite=args.overwrite)}

    return wc.emit(workflow, EXIT_CODES)


if __name__ == "__main__":
    raise SystemExit(main())

