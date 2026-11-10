"""Presentation settings, redraw specifications and asset records shared by ``present``, ``plot`` and ``render``.

Usage (Python; the presentation tools import it):

    from os_optics_kit import presentation as pr
    theme = pr.resolve_theme({"preset": "presentation", "language": "zh-CN", "background": "light"})
    theme.text("axis.samples")            # "样本数"
    spec = pr.load_spec("talk/presentation.json")

Three documents connect the presentation tools:

* the presentation specification ``presentation.json`` (schema ``os-optics-kit.presentation``): theme, named
  sources (saved runs and studies, paths relative to the file) and the assets to draw. It is the redraw
  configuration: change it (or pass ``--set``) and build again; nothing is simulated;
* the asset index ``assets.json`` (schema ``os-optics-kit.assets``) next to it after a build: one entry per asset with
  its files (paths relative to the output directory), preview, captions, size, sources and status;
* one ``provenance.json`` per asset: source identities (project and result hashes, engine build, catalog release),
  data files with sha256 and the resolved settings; kept out of presentation figures.

Fixed figure text comes from ``TEXT`` in English and Simplified Chinese; identifiers (target, plane, parameter and
element IDs, outcome codes) stay as they are unless the theme's ``labels`` gives a display name.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any

from . import workflow_common as wc
from ._version import __version__

SPEC_SCHEMA = "os-optics-kit.presentation"
MANIFEST_SCHEMA = "os-optics-kit.assets"
PROVENANCE_SCHEMA = "os-optics-kit.provenance"
SCHEMA_VERSION = 1
SPEC_FILE = "presentation.json"
MANIFEST_FILE = "assets.json"
ASSETS_DIR = "assets"
PREVIEWS_DIR = "previews"
PREVIEW_WIDTH = 640
LANGUAGES = ("en", "zh-CN")
ASSET_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
SOURCE_ID = ASSET_ID
ASSET_KINDS = ("view", "plot", "compose", "animation")
FORMATS = ("png", "svg", "pdf")

#: named sizes in pixels (width, height); any "WIDTHxHEIGHT" is accepted too
SIZES = {"slide": (1920, 1080), "4k": (3840, 2160), "half": (960, 1080), "square": (1080, 1080),
         "report": (1600, 1000), "thumbnail": (640, 360)}
#: logical canvas height in inches: text keeps its proportion of the image at every pixel size
CANVAS_HEIGHT_IN = 6.75

PRESETS: dict[str, dict[str, Any]] = {
    "presentation": {"language": "en", "background": "light", "palette": "default", "font_scale": 1.0,
                     "base_font_pt": 14.0, "title": True, "legend": "auto", "size": "slide",
                     "formats": ["png", "svg"], "captions": "separate", "provenance_in_figure": False,
                     "grid": True, "font_family": [], "units": {}, "labels": {}},
    "report": {"language": "en", "background": "light", "palette": "default", "font_scale": 1.0,
               "base_font_pt": 10.0, "title": True, "legend": "auto", "size": "report",
               "formats": ["png", "svg", "pdf"], "captions": "figure", "provenance_in_figure": True,
               "grid": True, "font_family": [], "units": {}, "labels": {}},
}
THEME_KEYS = ("preset", *PRESETS["presentation"].keys())

BACKGROUNDS = {
    "light": {"face": "#ffffff", "text": "#1f2328", "muted": "#59636e", "grid": "#e3e6ea", "render": "#ffffff"},
    "dark": {"face": "#111418", "text": "#e8eaed", "muted": "#a3abb5", "grid": "#2c323a", "render": "#111418"},
    "transparent": {"face": "none", "text": "#1f2328", "muted": "#59636e", "grid": "#d5d9de",
                    "render": "transparent"},
}
PALETTES = {
    # brand-neutral, distinguishable without colour vision (order: blue, orange, green, purple, red, grey)
    "default": ["#2a78d6", "#e8743b", "#19a979", "#945ecf", "#d03b3b", "#6b7280"],
    "monochrome": ["#1f2328", "#59636e", "#8c959f", "#afb8c1", "#d0d7de", "#eaeef2"],
}
#: outcome -> colour; the marker shapes stay with plot_results so outcomes stay apart without colour
STATUS_COLORS = {"NOT_APPLICABLE": "#3b6fa1", "PASS": "#0ca30c", "FAIL": "#d03b3b", "UNKNOWN": "#b07d00",
                 "OUT_OF_MODEL": "#ec835a", "ERROR": "#6b6b6b", "MISSING": "#8a8a8a"}

#: font families tried after the theme's own list; the first installed family covering a character draws it
LATIN_FONTS = ["DejaVu Sans", "Arial", "Helvetica", "Liberation Sans"]
CJK_FONTS = ["Noto Sans CJK SC", "Noto Sans SC", "Source Han Sans SC", "Microsoft YaHei", "PingFang SC",
             "Hiragino Sans GB", "WenQuanYi Zen Hei", "SimHei", "Noto Sans CJK JP", "Noto Sans CJK TC"]

#: unit display conversions offered by theme.units ({"mm": "um"}); values are converted by workflow_common.convert
DISPLAY_UNITS = ("nm", "um", "mm", "m", "W", "mW", "uW", "rad", "mrad", "urad", "deg")

TEXT: dict[str, dict[str, str]] = {
    "en": {
        "outcome.NOT_APPLICABLE": "NOT_APPLICABLE (no selected requirements)",
        "outcome.PASS": "PASS",
        "outcome.FAIL": "FAIL",
        "outcome.UNKNOWN": "UNKNOWN (no value or undefined)",
        "outcome.OUT_OF_MODEL": "OUT_OF_MODEL (model scope issue)",
        "outcome.ERROR": "ERROR (refused or not run)",
        "outcome.MISSING": "missing",
        "legend.count": "{label} ({count})",
        "legend.no_exact": "{label}, no exact value ({count})",
        "legend.bounds": "target bounds [{low}, {high}]",
        "legend.minimum": "target minimum {value}",
        "legend.maximum": "target maximum {value}",
        "legend.total_input": "total input {value} W",
        "legend.bounded": "range",
        "legend.lower_bound": "lower bound",
        "note.no_exact_row": "samples without an exact value",
        "note.empty_cell": "empty cell: no exact value",
        "note.no_value": "no value",
        "note.missing": "missing",
        "title.sweep": "{y} vs {x}",
        "title.heatmap": "{y}",
        "title.power": "Received power",
        "title.power_of": "Received power: {source}",
        "title.power_planes": "Observation planes",
        "title.power_planes_note": "solid: known; hatched: lower bound",
        "title.power_sinks": "Where the input power goes",
        "title.view": "System view",
        "title.view_of": "System view: {source}",
        "title.targets": "Target values",
        "axis.power_W": "received power [W]",
        "axis.power": "power [W]",
        "axis.samples": "samples [count]",
        "axis.value": "value",
        "caption.data": "data: {path} (sha256 {digest})",
        "caption.engine": "engine {build}",
        "caption.catalog": "catalog {version}",
        "caption.no_catalog": "catalog none (catalog-free run)",
        "caption.view": "{subject}, {angle}° from the optical axis at {elevation}° elevation.",
        "caption.sweep": "{y} across {count} samples of {x}; {passed} meet the target.",
        "caption.sweep_plain": "{y} across {count} samples of {x}.",
        "caption.power": "Power received by each observation plane and where the input power goes.",
        "caption.targets": "Target values of {runs} with their bounds.",
        "caption.recorded_samples": "Recorded samples, not a time evolution.",
        "gallery.title": "Result gallery",
    },
    "zh-CN": {
        "outcome.NOT_APPLICABLE": "NOT_APPLICABLE（未选定要求）",
        "outcome.PASS": "PASS",
        "outcome.FAIL": "FAIL",
        "outcome.UNKNOWN": "UNKNOWN（无值或未定义）",
        "outcome.OUT_OF_MODEL": "OUT_OF_MODEL（超出模型范围）",
        "outcome.ERROR": "ERROR（被拒绝或未运行）",
        "outcome.MISSING": "缺失",
        "legend.count": "{label} ({count})",
        "legend.no_exact": "{label}，无精确值 ({count})",
        "legend.bounds": "目标范围 [{low}, {high}]",
        "legend.minimum": "目标下限 {value}",
        "legend.maximum": "目标上限 {value}",
        "legend.total_input": "总输入 {value} W",
        "legend.bounded": "范围",
        "legend.lower_bound": "下界",
        "note.no_exact_row": "无精确值的样本",
        "note.empty_cell": "空单元格：无精确值",
        "note.no_value": "无值",
        "note.missing": "缺失",
        "title.sweep": "{y} 随 {x} 的变化",
        "title.heatmap": "{y}",
        "title.power": "接收功率",
        "title.power_of": "接收功率：{source}",
        "title.power_planes": "观测面",
        "title.power_planes_note": "实心：已知；斜线：下界",
        "title.power_sinks": "输入功率去向",
        "title.view": "系统视图",
        "title.view_of": "系统视图：{source}",
        "title.targets": "目标值",
        "axis.power_W": "接收功率 [W]",
        "axis.power": "功率 [W]",
        "axis.samples": "样本数",
        "axis.value": "数值",
        "caption.data": "数据：{path}（sha256 {digest}）",
        "caption.engine": "引擎 {build}",
        "caption.catalog": "目录 {version}",
        "caption.no_catalog": "无目录（不依赖目录的运行）",
        "caption.view": "{subject}，与光轴夹角 {angle}°，仰角 {elevation}°。",
        "caption.sweep": "{y} 在 {x} 的 {count} 个样本上的取值，其中 {passed} 个满足目标。",
        "caption.sweep_plain": "{y} 在 {x} 的 {count} 个样本上的取值。",
        "caption.power": "各观测面接收的功率与输入功率去向。",
        "caption.targets": "{runs} 的目标值及其范围。",
        "caption.recorded_samples": "按记录的样本播放，并非时间演化。",
        "gallery.title": "结果图库",
    },
}


class PresentationError(wc.WorkflowError):
    """A refused presentation request: stable ``code``, a message naming the input and a ``recovery`` step."""

    def __init__(self, code, message, *, recovery=None, **details):
        if recovery is None:
            recovery = {
                "SETTING_INVALID": "run 'os-optics-kit present settings' for the supported keys and values",
                "SETTING_UNKNOWN": "run 'os-optics-kit present settings' for the supported keys and values",
                "SPEC_INVALID": "correct presentation.json; 'os-optics-kit present settings' lists every field",
                "ASSET_UNKNOWN": "run 'os-optics-kit present list DIR' for the asset ids"}.get(code)
        super().__init__(code, message, **({"recovery": recovery} if recovery else {}), **details)


# --------------------------------------------------------------------------------------------- text

def localized(value, language, default=""):
    """``value`` as display text: a string, or ``{"en": ..., "zh-CN": ...}`` (falls back to English, then any)."""
    if value is None:
        return default
    if isinstance(value, dict):
        for key in (language, "en", *value):
            if isinstance(value.get(key), str) and value[key]:
                return value[key]
        return default
    return str(value)


_CJK = "\u2e80-\u9fff\uf900-\ufaff\uff00-\uffef"
_CJK_GAP = re.compile(f"(?<=[{_CJK}]) +(?=[{_CJK}])")
_HAN = "\u2e80-\u2fff\u3040-\u9fff\uf900-\ufaff"  # Chinese characters without full-width punctuation
_LATIN_HAN = re.compile(f"(?<=[A-Za-z0-9%)\\]])(?=[{_HAN}])|(?<=[{_HAN}])(?=[A-Za-z0-9(\\[])")


def tidy(value, language):
    """Chinese spacing: no space between two Chinese characters (``光斑半径 随 位移`` -> ``光斑半径随位移``) and one
    space between Chinese and Latin text (``Original与L2`` -> ``Original 与 L2``), so one template reads well with
    identifiers and with Chinese labels."""
    if language != "zh-CN" or not isinstance(value, str):
        return value
    return _LATIN_HAN.sub(" ", _CJK_GAP.sub("", value))


def text(key, language, **values):
    """Fixed figure text ``TEXT[language][key]`` formatted with ``values``."""
    table = TEXT.get(language) or TEXT["en"]
    return tidy(table[key].format(**values), language)


def fmt_number(value, digits=6):
    """Compact number for labels (``1.99607``, ``0.00054``); None -> ``-``."""
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "-"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    return f"{value:.{digits}g}"


# --------------------------------------------------------------------------------------------- theme

def parse_size(value):
    """``"slide"`` / ``"4k"`` / ... or ``"WIDTHxHEIGHT"`` or ``[w, h]`` -> ``(w, h)`` pixels (100..8192)."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        width, height = value
    elif isinstance(value, str) and value in SIZES:
        width, height = SIZES[value]
    else:
        try:
            width, height = (int(piece) for piece in str(value).lower().split("x"))
        except ValueError:
            raise PresentationError("SETTING_INVALID", f"size {value!r}: use {sorted(SIZES)} or WIDTHxHEIGHT",
                                    recovery="set theme.size to slide, 4k or a size such as 2560x1440",
                                    key="size") from None
    if not all(isinstance(v, int) and 100 <= v <= 8192 for v in (width, height)):
        raise PresentationError("SETTING_INVALID", f"size {value!r}: width and height must be 100..8192 pixels",
                                key="size")
    return int(width), int(height)


@dataclass
class Theme:
    """Resolved presentation settings (``resolve_theme``)."""

    preset: str
    language: str
    background: str
    colors: dict[str, str]
    palette: list[str]
    font_family: list[str]
    font_scale: float
    base_font_pt: float
    title: bool
    legend: str
    size: tuple[int, int]
    formats: list[str]
    captions: str
    provenance_in_figure: bool
    grid: bool
    units: dict[str, str]
    labels: dict[str, Any]
    settings: dict[str, Any] = field(default_factory=dict)

    # -- text
    def text(self, key, **values):
        return text(key, self.language, **values)

    def label(self, identifier, default=None):
        """Display label of an identifier (``target:spot``, ``param:gap``, ``source:nominal``, an ID)."""
        if identifier in self.labels:
            return localized(self.labels[identifier], self.language, identifier)
        bare = identifier.split(":", 1)[-1]
        if bare in self.labels:
            return localized(self.labels[bare], self.language, bare)
        return default if default is not None else bare

    def outcome(self, code):
        key = f"outcome.{code}"
        return self.text(key) if key in TEXT["en"] else code

    # -- units
    def display_unit(self, unit):
        return self.units.get(unit, unit) if unit else unit

    def to_display(self, value, unit):
        """``value`` in ``unit`` converted to the theme's display unit (None stays None)."""
        target = self.display_unit(unit)
        if value is None or target == unit or not isinstance(value, (int, float)) or isinstance(value, bool):
            return value
        if isinstance(value, float) and not math.isfinite(value):
            return value
        return wc.convert(value, unit, target, path="theme.units")

    def axis_label(self, identifier, unit, default=None):
        name = self.label(identifier, default)
        shown = self.display_unit(unit)
        return f"{name} [{shown}]" if shown and shown not in wc.UNITLESS else name

    # -- figure geometry
    def font_pt(self, role="body"):
        factor = {"body": 1.0, "small": 0.8, "title": 1.25, "caption": 0.75, "panel": 1.1}[role]
        return round(self.base_font_pt * self.font_scale * factor, 2)

    def canvas(self, size=None):
        """``(figsize_in, dpi)`` for a pixel size: the canvas height is fixed, so text keeps its proportion."""
        width, height = parse_size(size) if size is not None else self.size
        dpi = height / CANVAS_HEIGHT_IN
        return (width / dpi, CANVAS_HEIGHT_IN), dpi

    def series_color(self, index):
        return self.palette[index % len(self.palette)]

    def status_color(self, outcome):
        return STATUS_COLORS.get(outcome, self.colors["muted"])

    def render_background(self):
        """``render --background`` value for this theme."""
        return self.colors["render"]

    def rc(self):
        """matplotlib rcParams for this theme (fonts with fallback, colours, sizes, editable SVG text)."""
        colors = self.colors
        return {
            "font.family": self.font_family, "font.size": self.font_pt("body"),
            "axes.titlesize": self.font_pt("title"), "axes.labelsize": self.font_pt("body"),
            "xtick.labelsize": self.font_pt("small"), "ytick.labelsize": self.font_pt("small"),
            "legend.fontsize": self.font_pt("small"), "figure.titlesize": self.font_pt("title"),
            "text.color": colors["text"], "axes.labelcolor": colors["text"], "axes.edgecolor": colors["muted"],
            "xtick.color": colors["text"], "ytick.color": colors["text"], "axes.titlecolor": colors["text"],
            "figure.facecolor": colors["face"], "axes.facecolor": colors["face"],
            "savefig.facecolor": colors["face"], "savefig.transparent": self.background == "transparent",
            "legend.labelcolor": colors["text"], "legend.frameon": False,
            "axes.spines.top": False, "axes.spines.right": False, "axes.grid": self.grid,
            "grid.color": colors["grid"], "grid.linewidth": 0.6, "axes.prop_cycle": _cycler(self.palette),
            "svg.fonttype": "none", "svg.hashsalt": "os-optics-kit", "pdf.fonttype": 42,
            "axes.unicode_minus": False,
        }

    def summary(self):
        """The resolved settings as JSON (recorded in the asset index and provenance)."""
        return {"preset": self.preset, "language": self.language, "background": self.background,
                "palette": self.palette, "font_family": self.font_family, "font_scale": self.font_scale,
                "base_font_pt": self.base_font_pt, "title": self.title, "legend": self.legend,
                "size": list(self.size), "formats": self.formats, "captions": self.captions,
                "provenance_in_figure": self.provenance_in_figure, "grid": self.grid, "units": self.units}


def _cycler(colors):
    from cycler import cycler

    return cycler(color=colors)


def _check_color(value, key):
    if not (isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value)):
        raise PresentationError("SETTING_INVALID", f"theme.{key}: {value!r} is not a colour such as '#2a78d6'",
                                key=key)
    return value.lower()


def _luminance(hex_color):
    channels = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def resolve_theme(theme=None, overrides=None):
    """Validated ``Theme`` from a theme object (``preset`` + any keys of ``THEME_KEYS``) and overrides."""
    merged = dict(theme or {})
    merged.update(overrides or {})
    preset = merged.get("preset", "presentation")
    if preset not in PRESETS:
        raise PresentationError("SETTING_INVALID", f"theme.preset {preset!r}: choose {sorted(PRESETS)}",
                                key="preset")
    unknown = sorted(set(merged) - set(THEME_KEYS))
    if unknown:
        raise PresentationError("SETTING_UNKNOWN", f"unknown theme settings {unknown}",
                                recovery="run 'os-optics-kit present settings' for the supported keys",
                                unknown=unknown)
    values = {**copy.deepcopy(PRESETS[preset]), **{k: v for k, v in merged.items() if k != "preset"}}
    language = values["language"]
    if language not in LANGUAGES:
        raise PresentationError("SETTING_INVALID", f"theme.language {language!r}: choose {list(LANGUAGES)}",
                                key="language")
    background = values["background"]
    if background in BACKGROUNDS:
        colors = dict(BACKGROUNDS[background])
    else:
        face = _check_color(background, "background")
        dark = _luminance(face) < 0.2
        colors = {**BACKGROUNDS["dark" if dark else "light"], "face": face, "render": face}
    palette = values["palette"]
    if isinstance(palette, str):
        if palette not in PALETTES:
            raise PresentationError("SETTING_INVALID", f"theme.palette {palette!r}: choose {sorted(PALETTES)} or "
                                    "a list of colours", key="palette")
        palette = list(PALETTES[palette])
    elif isinstance(palette, list) and palette:
        palette = [_check_color(c, "palette") for c in palette]
    else:
        raise PresentationError("SETTING_INVALID", "theme.palette: a palette name or a non-empty list of colours",
                                key="palette")
    scale = values["font_scale"]
    if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not 0.5 <= scale <= 3.0:
        raise PresentationError("SETTING_INVALID", f"theme.font_scale {scale!r}: use a number from 0.5 to 3",
                                key="font_scale")
    base = values["base_font_pt"]
    if isinstance(base, bool) or not isinstance(base, (int, float)) or not 5 <= base <= 40:
        raise PresentationError("SETTING_INVALID", f"theme.base_font_pt {base!r}: use 5 to 40 points",
                                key="base_font_pt")
    if values["legend"] not in ("auto", "outside", "none"):
        raise PresentationError("SETTING_INVALID", "theme.legend: auto, outside or none", key="legend")
    if values["captions"] not in ("separate", "figure"):
        raise PresentationError("SETTING_INVALID", "theme.captions: separate or figure", key="captions")
    formats = values["formats"]
    if not isinstance(formats, list) or not formats or any(f not in FORMATS for f in formats) or "png" not in formats:
        raise PresentationError("SETTING_INVALID", f"theme.formats {formats!r}: png plus any of svg, pdf",
                                key="formats")
    units = values["units"] or {}
    if not isinstance(units, dict):
        raise PresentationError("SETTING_INVALID", "theme.units: an object such as {\"mm\": \"um\"}", key="units")
    for source_unit, shown in units.items():
        if source_unit not in DISPLAY_UNITS or shown not in DISPLAY_UNITS:
            raise PresentationError("SETTING_INVALID", f"theme.units {source_unit!r} -> {shown!r}: units "
                                    f"{list(DISPLAY_UNITS)}", key="units")
        wc.convert(1.0, source_unit, shown, path=f"theme.units.{source_unit}")  # refuses incompatible units
    labels = values["labels"] or {}
    if not isinstance(labels, dict):
        raise PresentationError("SETTING_INVALID", "theme.labels: an object {identifier: text or "
                                "{\"en\": ..., \"zh-CN\": ...}}", key="labels")
    families = values["font_family"]
    if isinstance(families, str):
        families = [families]
    if not isinstance(families, list) or not all(isinstance(f, str) for f in families):
        raise PresentationError("SETTING_INVALID", "theme.font_family: a font family name or a list of names",
                                key="font_family")
    for key in ("title", "provenance_in_figure", "grid"):
        if not isinstance(values[key], bool):
            raise PresentationError("SETTING_INVALID", f"theme.{key}: true or false", key=key)
    return Theme(preset=preset, language=language, background=background, colors=colors, palette=palette,
                 font_family=installed_families(families, language), font_scale=float(scale),
                 base_font_pt=float(base), title=values["title"], legend=values["legend"],
                 size=parse_size(values["size"]), formats=list(formats), captions=values["captions"],
                 provenance_in_figure=values["provenance_in_figure"], grid=values["grid"], units=dict(units),
                 labels=dict(labels), settings={"preset": preset, **values})


# --------------------------------------------------------------------------------------------- fonts

_FONT_CACHE: dict[str, Any] = {}


def _font_index():
    """``{family name: font file}`` of matplotlib's font list (first file per family)."""
    if "index" not in _FONT_CACHE:
        import logging

        from matplotlib import font_manager

        # findfont notices about the weights of installed families do not help the reader of a figure
        logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)

        index: dict[str, str] = {}
        for entry in font_manager.fontManager.ttflist:
            # only upright regular-weight files: a family installed only as Thin or Bold is not used for body text
            weight = entry.weight if isinstance(entry.weight, int) else {"normal": 400, "regular": 400,
                                                                          "book": 400}.get(str(entry.weight), 0)
            if entry.style == "normal" and 350 <= weight <= 500:
                index.setdefault(entry.name, entry.fname)
        _FONT_CACHE["index"] = index
    return _FONT_CACHE["index"]


def installed_families(requested, language):
    """The installed families of ``requested`` + the defaults for ``language``, in order (no duplicates).

    Without matplotlib (``--help``, gallery-only use) the requested list plus defaults is returned unchanged."""
    # Latin text takes the first Latin family; Chinese characters fall back to the first installed CJK family.
    wanted = [*requested, *LATIN_FONTS, *CJK_FONTS]
    try:
        index = _font_index()
    except ImportError:
        return list(dict.fromkeys(wanted))
    found = [name for name in dict.fromkeys(wanted) if name in index]
    return found or ["DejaVu Sans"]


def _charmap(path):
    key = f"cmap:{path}"
    if key not in _FONT_CACHE:
        from matplotlib.ft2font import FT2Font

        try:
            _FONT_CACHE[key] = set(FT2Font(path).get_charmap())
        except (OSError, RuntimeError):
            _FONT_CACHE[key] = set()
    return _FONT_CACHE[key]


def missing_glyphs(strings, families):
    """Characters of ``strings`` that none of the installed ``families`` can draw."""
    index = _font_index()
    maps = [_charmap(index[name]) for name in families if name in index]
    missing = set()
    for value in strings:
        for char in str(value):
            if char.isspace() or ord(char) < 32:
                continue
            if not any(ord(char) in charmap for charmap in maps):
                missing.add(char)
    return sorted(missing)


def check_glyphs(strings, theme):
    """Refuse text that the theme's fonts cannot draw (FONT_MISSING), naming fonts that would cover it."""
    missing = missing_glyphs(strings, theme.font_family)
    if missing:
        hint = "Noto Sans CJK SC" if any(ord(c) > 0x2E80 for c in missing) else "DejaVu Sans"
        raise PresentationError(
            "FONT_MISSING", f"the installed fonts cannot draw {''.join(missing[:20])!r} "
            f"({len(missing)} characters); fonts tried: {theme.font_family}",
            recovery=f"install a font that covers these characters (for Chinese: {hint}, Microsoft YaHei or "
                     "PingFang SC) or set theme.font_family to an installed one", characters="".join(missing[:40]))


# --------------------------------------------------------------------------------------------- specification

def default_spec():
    return {"schema": SPEC_SCHEMA, "schema_version": SCHEMA_VERSION, "title": None,
            "theme": {"preset": "presentation"}, "sources": {}, "assets": []}


def check_asset_id(value, what="asset"):
    if not isinstance(value, str) or not ASSET_ID.fullmatch(value):
        raise PresentationError("SPEC_INVALID", f"{what} id {value!r}: use 1-64 lower-case letters, digits, '_' or "
                                "'-', starting with a letter or digit", key=f"{what}.id")
    return value


def validate_spec(spec):
    """Check the structure of a presentation specification; returns it. Asset-kind fields are checked at build."""
    if not isinstance(spec, dict) or spec.get("schema") != SPEC_SCHEMA:
        raise PresentationError("SPEC_INVALID", f"not a presentation specification (schema {SPEC_SCHEMA!r})",
                                recovery="create one with 'os-optics-kit present init'")
    if spec.get("schema_version") != SCHEMA_VERSION:
        raise PresentationError("SPEC_INVALID", f"schema_version {spec.get('schema_version')!r}; this kit reads "
                                f"version {SCHEMA_VERSION}")
    unknown = sorted(set(spec) - {"schema", "schema_version", "title", "theme", "sources", "assets"})
    if unknown:
        raise PresentationError("SPEC_INVALID", f"unknown top-level keys {unknown}")
    resolve_theme(spec.get("theme"))
    sources = spec.get("sources")
    if not isinstance(sources, dict):
        raise PresentationError("SPEC_INVALID", "sources: an object {id: {\"path\": ...}}")
    for source_id, source in sources.items():
        check_asset_id(source_id, "source")
        if not isinstance(source, dict) or not isinstance(source.get("path"), str) or not source["path"]:
            raise PresentationError("SPEC_INVALID", f"sources.{source_id}: needs \"path\" (relative to the "
                                    "specification file)", key=f"sources.{source_id}")
    assets = spec.get("assets")
    if not isinstance(assets, list):
        raise PresentationError("SPEC_INVALID", "assets: a list of asset objects")
    seen = set()
    for index, asset in enumerate(assets):
        if not isinstance(asset, dict):
            raise PresentationError("SPEC_INVALID", f"assets[{index}] is not an object")
        asset_id = check_asset_id(asset.get("id"))
        if asset_id in seen:
            raise PresentationError("SPEC_INVALID", f"asset id {asset_id!r} is used twice")
        seen.add(asset_id)
        if asset.get("kind") not in ASSET_KINDS:
            raise PresentationError("SPEC_INVALID", f"asset {asset_id}: kind {asset.get('kind')!r}; choose "
                                    f"{list(ASSET_KINDS)}", key=f"assets.{asset_id}.kind")
    return spec


def load_spec(path):
    path = Path(path)
    if not path.is_file():
        raise PresentationError("SPEC_NOT_FOUND", f"{path.as_posix()} does not exist",
                                recovery="create it with 'os-optics-kit present init SOURCE --out DIR'")
    try:
        spec = wc.read_json(path)
    except (ValueError, UnicodeDecodeError) as exc:
        raise PresentationError("SPEC_INVALID", f"{path.name} is not JSON: {exc}") from None
    return validate_spec(spec)


def spec_text(spec):
    """Specification as stored: indented UTF-8 JSON (Chinese text stays readable), LF line ends."""
    return json.dumps(wc.json_safe(spec), indent=1, ensure_ascii=False, allow_nan=False) + "\n"


def save_spec(spec, path):
    validate_spec(spec)
    return wc.write_text_atomic(path, spec_text(spec))


def find_asset(spec, asset_id):
    for asset in spec["assets"]:
        if asset["id"] == asset_id:
            return asset
    raise PresentationError("ASSET_UNKNOWN", f"no asset {asset_id!r}; assets: {[a['id'] for a in spec['assets']]}",
                            asset=asset_id)


def parse_value(text_value):
    """``--set`` value: JSON when it parses (numbers, true/false, lists, objects, quoted strings), else the text.
    A value that starts like a JSON list, object or string but does not parse is refused."""
    try:
        return json.loads(text_value)
    except ValueError:
        if text_value.lstrip()[:1] in ("[", "{", '"'):
            raise PresentationError("SETTING_INVALID", f"--set value {text_value!r} is not valid JSON",
                                    recovery="quote the whole KEY=VALUE for the shell, e.g. "
                                             "--set 'assets.view.camera=[30, 15]'") from None
        return text_value


def set_value(spec, dotted, value):
    """Set ``theme.language`` / ``title`` / ``assets.<id>.<key>[.<key>...]`` / ``sources.<id>.<key>`` in place."""
    parts = dotted.split(".")
    if not parts or not all(parts):
        raise PresentationError("SETTING_INVALID", f"--set {dotted!r}: use KEY=VALUE such as theme.language=zh-CN")
    if parts[0] == "assets":
        if len(parts) < 3:
            raise PresentationError("SETTING_INVALID", f"--set {dotted!r}: use assets.<id>.<key>=VALUE")
        target = find_asset(spec, parts[1])
        if parts[2] in ("id", "kind"):
            raise PresentationError("SETTING_INVALID", f"--set {dotted!r}: an asset's id and kind are fixed; add a "
                                    "new asset instead")
        keys = parts[2:]
    elif parts[0] in ("theme", "sources") and len(parts) >= 2:
        target = spec.setdefault(parts[0], {})
        keys = parts[1:]
    elif parts == ["title"]:
        spec["title"] = value
        return spec
    else:
        raise PresentationError("SETTING_INVALID", f"--set {dotted!r}: keys start with theme., assets.<id>., "
                                "sources.<id>. or are 'title'")
    for key in keys[:-1]:
        nested = target.get(key)
        if not isinstance(nested, dict):
            nested = {}
            target[key] = nested
        target = nested
    if value is None:
        target.pop(keys[-1], None)
    else:
        target[keys[-1]] = value
    return spec


# --------------------------------------------------------------------------------------------- sources

@dataclass
class Source:
    """A saved input of the specification: a run directory or a sweep study directory."""

    id: str
    kind: str  # "run" | "study"
    path: Path
    label: Any = None
    identity: dict[str, Any] = field(default_factory=dict)

    def display(self, theme):
        return localized(self.label, theme.language, self.id)


def source_kind(path):
    path = Path(path)
    if (path / "manifest.json").is_file() and (path / "simulation_result.json").is_file():
        return "run"
    if (path / "summary.json").is_file() and (path / "study.json").is_file():
        return "study"
    return None


def resolve_source(spec_dir, source_id, entry):
    """``Source`` for ``sources.<id>``; refuses a missing or unreadable path with a recovery step."""
    path = Path(entry["path"])
    path = path if path.is_absolute() else Path(spec_dir) / path
    kind = source_kind(path)
    if kind is None:
        raise PresentationError(
            "SOURCE_MISSING", f"source {source_id!r}: {entry['path']} is not a saved run or study directory",
            recovery=f"point sources.{source_id}.path at a run directory (it holds manifest.json) or a study "
                     "directory (it holds summary.json), relative to presentation.json", source=source_id)
    return Source(id=source_id, kind=kind, path=path, label=entry.get("label"), identity=source_identity(kind, path))


def source_identity(kind, path):
    """Identity of a saved source read from its files (never reruns)."""
    path = Path(path)
    if kind == "run":
        from os_optics.results import load_run

        try:
            identity = load_run(path).identity()
        except Exception as exc:  # noqa: BLE001 - the engine's refusal names the problem
            raise PresentationError("SOURCE_UNREADABLE", f"{path.name}: {exc}",
                                    recovery="check the run directory with 'os-optics-kit summary RUN'") from None
        return {key: identity.get(key) for key in ("project_hash", "input_hash", "result_hash", "engine_version",
                                                   "engine_build_id", "catalog_version")}
    summary = path / "summary.json"
    study = wc.read_json(path / "study.json")
    return {"summary_sha256": wc.sha256_file(summary),
            "engine_build_id": ((study.get("engine") or {}).get("engine_build_id")),
            "catalog_version": ((study.get("catalog") or {}).get("catalog_version"))}


def portable_path(path, start):
    """``path`` relative to ``start`` (forward slashes); None when no relative path exists (another drive)."""
    try:
        return Path(os.path.relpath(Path(path).resolve(), Path(start).resolve())).as_posix()
    except ValueError:
        return None


# --------------------------------------------------------------------------------------------- asset records

def fingerprint(*parts):
    """sha256 of the canonical JSON of ``parts`` (the inputs that decide an asset's files)."""
    return wc.canonical_digest([__version__, *parts])


def asset_dir(out_dir, asset_id):
    return Path(out_dir) / ASSETS_DIR / asset_id


def file_record(path, out_dir):
    """``{"path", "sha256", "bytes"}`` of a written file, path relative to the output directory."""
    path = Path(path)
    return {"path": portable_path(path, out_dir), "sha256": wc.sha256_file(path), "bytes": path.stat().st_size}


def image_size(path):
    """``[width, height]`` of a PNG from its header (no imaging library needed)."""
    with open(path, "rb") as handle:
        header = handle.read(24)
    if header[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return [int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")]


def write_preview(image_path, preview_path, width=PREVIEW_WIDTH):
    """A PNG preview at most ``width`` pixels wide (Pillow, shipped with matplotlib)."""
    from PIL import Image

    preview_path = Path(preview_path)
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(image_path) as image:
        image = image.convert("RGBA")
        if image.width > width:
            image = image.resize((width, max(1, round(image.height * width / image.width))), Image.LANCZOS)
        image.save(preview_path, format="PNG", optimize=True)
    return preview_path


def provenance_record(asset, theme, sources, data_files, out_dir, extra=None):
    """Companion record of one asset: sources with identities, data files with sha256, settings, kit version."""
    return {"schema": PROVENANCE_SCHEMA, "schema_version": SCHEMA_VERSION, "asset": asset["id"],
            "kind": asset["kind"], "kit_version": __version__, "generated": wc.utc_now(),
            "sources": [{"id": s.id, "kind": s.kind, "label": s.label, "identity": s.identity} for s in sources],
            "data": [file_record(p, out_dir) for p in data_files], "settings": asset, "theme": theme.summary(),
            **(extra or {})}


def caption_text(asset, theme, automatic):
    """The asset's caption in the theme language: the specification's caption, else the automatic one."""
    return localized(asset.get("caption"), theme.language, "") or localized(automatic, theme.language, "")


def title_text(asset, theme, automatic):
    return localized(asset.get("title"), theme.language, "") or localized(automatic, theme.language, "")


def read_manifest(out_dir):
    path = Path(out_dir) / MANIFEST_FILE
    if not path.is_file():
        return None
    document = wc.read_json(path)
    if document.get("schema") != MANIFEST_SCHEMA:
        return None
    return document


def manifest_text(document):
    return json.dumps(wc.json_safe(document), indent=1, ensure_ascii=False, allow_nan=False) + "\n"


def write_manifest(out_dir, document):
    return wc.write_text_atomic(Path(out_dir) / MANIFEST_FILE, manifest_text(document))


def sha256_text(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
