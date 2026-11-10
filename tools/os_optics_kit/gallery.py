"""Offline result gallery, contact sheet and portable asset bundle of a built presentation directory.

Usage (Python; ``os-optics-kit present gallery`` and ``present bundle`` call these):

    from os_optics_kit import gallery
    gallery.write_gallery("talk/out")                              # index.html + contact-sheet.png
    gallery.write_bundle("talk/out", "share/talk.zip", only=["system-view"])

A built presentation directory holds ``presentation.json`` (the redraw configuration), ``assets.json`` (the asset
index), ``assets/<id>/...`` and ``previews/<id>.png``. The gallery is one self-contained page: its data is embedded
as JSON, it loads nothing from the network and works from ``file://``. The contact sheet labels every preview with
its asset id, so an agent can pick an image there and find the original under ``assets/<id>/``.

A bundle copies only the selected assets' listed files, their previews, the filtered asset index and redraw
configuration, and a regenerated gallery; every link is relative. A ``.zip`` destination gives a deterministic
archive with one top-level folder named after the archive.
"""

from __future__ import annotations

import dataclasses
import html
import json
import math
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
from typing import Any
from urllib.parse import quote
import zipfile

from . import presentation as pr
from . import workflow_common as wc

INDEX_FILE = "index.html"
CONTACT_SHEET_FILE = "contact-sheet.png"
BUILT_STATUSES = ("written", "reused")
#: largest contact sheet width in pixels
CONTACT_SHEET_MAX_WIDTH = 2400
ZIP_DATE = (1980, 1, 1, 0, 0, 0)

#: gallery and contact-sheet text in both languages (figure text stays in presentation.TEXT)
GALLERY_TEXT: dict[str, dict[str, str]] = {
    "en": {
        "assets": "{count} assets",
        "asset": "1 asset",
        "contact_sheet": "Contact sheet",
        "asset_index": "Asset index",
        "redraw": "Redraw settings",
        "language": "Language",
        "files": "Files",
        "sources": "Sources",
        "settings": "Asset settings",
        "values": "Result values",
        "kind": "Kind",
        "status": "Status",
        "size": "Size",
        "engine": "Engine",
        "catalog": "Catalog",
        "catalog_none": "none",
        "project": "Project",
        "result": "Result",
        "summary": "Study summary",
        "previous": "Previous",
        "next": "Next",
        "close": "Close",
        "open": "Open {id}",
        "position": "{index} of {count}",
        "status.written": "Drawn",
        "status.reused": "Up to date",
        "status.missing_input": "Missing input",
        "status.failed": "Not drawn",
        "status.pending": "Not built yet",
        "pending_hint": "Run 'os-optics-kit present build' to draw it.",
        "no_preview": "No preview",
        "next_step": "Next step: {recovery}",
        "kind.view": "3D view",
        "kind.plot": "Plot",
        "kind.compose": "Composite figure",
        "kind.animation": "Animation",
        "file.format": "{label} ({format})",
        "file.image": "Image",
        "file.svg": "Vector image",
        "file.pdf": "Vector image",
        "file.clean": "Image without annotations",
        "file.overlay": "Editable text layer",
        "file.annotated": "Annotated image",
        "file.viewer": "Interactive 3D view",
        "file.mp4": "Video",
        "file.gif": "Animation",
        "file.sequence": "Replayable sequence",
        "file.data": "Data",
        "file.layout": "Panel layout",
        "file.panel": "Panel {number}",
        "file.camera": "Camera settings",
        "file.caption": "Caption, {language}",
        "file.provenance": "Provenance record",
        "language.en": "English",
        "language.zh-CN": "中文",
        "caption.en": "English",
        "caption.zh-CN": "Chinese",
        "empty": "No assets yet.",
    },
    "zh-CN": {
        "assets": "共 {count} 项素材",
        "asset": "共 1 项素材",
        "contact_sheet": "缩略图总览",
        "asset_index": "素材索引",
        "redraw": "重绘设置",
        "language": "语言",
        "files": "文件",
        "sources": "来源",
        "settings": "素材设置",
        "values": "结果数值",
        "kind": "类型",
        "status": "状态",
        "size": "尺寸",
        "engine": "引擎",
        "catalog": "器件目录",
        "catalog_none": "无",
        "project": "项目",
        "result": "结果",
        "summary": "研究汇总",
        "previous": "上一项",
        "next": "下一项",
        "close": "关闭",
        "open": "打开 {id}",
        "position": "{index} / {count}",
        "status.written": "已绘制",
        "status.reused": "已是最新",
        "status.missing_input": "缺少输入",
        "status.failed": "未能绘制",
        "status.pending": "尚未生成",
        "pending_hint": "运行 os-optics-kit present build 即可生成。",
        "no_preview": "无预览",
        "next_step": "下一步：{recovery}",
        "kind.view": "三维视图",
        "kind.plot": "图表",
        "kind.compose": "组合图",
        "kind.animation": "动画",
        "file.format": "{label}（{format}）",
        "file.image": "图像",
        "file.svg": "矢量图",
        "file.pdf": "矢量图",
        "file.clean": "无标注图像",
        "file.overlay": "可编辑文字层",
        "file.annotated": "带标注图像",
        "file.viewer": "交互式三维视图",
        "file.mp4": "视频",
        "file.gif": "动画",
        "file.sequence": "可重放序列",
        "file.data": "数据",
        "file.layout": "面板布局",
        "file.panel": "面板 {number}",
        "file.camera": "相机参数",
        "file.caption": "{language}图注",
        "file.provenance": "来源记录",
        "language.en": "English",
        "language.zh-CN": "中文",
        "caption.en": "英文",
        "caption.zh-CN": "中文",
        "empty": "暂无素材。",
    },
}
FILE_ROLES = ("image", "annotated", "clean", "svg", "pdf", "overlay", "mp4", "gif", "viewer", "sequence", "layout",
              "camera", "data")
PANEL_ROLE = re.compile(r"panel-(\d+)(?:-[a-z0-9]+)?")
STATUSES = ("written", "reused", "missing_input", "failed", "pending")
#: asset settings the page shows elsewhere (id, kind, title, caption)
SHOWN_KEYS = ("id", "kind", "title", "caption")

#: absolute paths of a machine; a bundle carrying one is refused (BUNDLE_PATH_LEAK)
PATH_LEAKS = (
    re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]"),
    re.compile(r"(?<![\w.~-])/(?:home|Users|root)/"),
    re.compile(r"file://", re.I),
    re.compile(r"(?<![\w\\])\\\\[A-Za-z0-9][\w.$-]*\\[\w.$-]+"),  # a network share \\host\share
)


def gtext(key, locale, **values):
    """Gallery text ``GALLERY_TEXT[locale][key]`` formatted with ``values``."""
    table = GALLERY_TEXT.get(locale) or GALLERY_TEXT["en"]
    return pr.tidy(table[key].format(**values), locale)


def count_text(count, language):
    return gtext("asset", language) if count == 1 else gtext("assets", language, count=count)


# --------------------------------------------------------------------------------------------- inputs

def read_manifest(out_dir):
    """The asset index of a built presentation directory; refuses a directory without one."""
    manifest = pr.read_manifest(out_dir)
    if manifest is None or not isinstance(manifest.get("assets"), list):
        raise pr.PresentationError(
            "MANIFEST_NOT_FOUND", f"{Path(out_dir).name or out_dir} has no asset index ({pr.MANIFEST_FILE})",
            recovery="build the presentation first with 'os-optics-kit present build SPEC --out DIR'")
    return manifest


def theme_from_manifest(manifest):
    """``Theme`` from the asset index's resolved settings (``Theme.summary()``) and its language."""
    summary = manifest.get("theme") if isinstance(manifest.get("theme"), dict) else {}
    settings = {key: value for key, value in summary.items() if key in pr.THEME_KEYS}
    if manifest.get("language") in pr.LANGUAGES:
        settings["language"] = manifest["language"]
    attempts = (settings, {key: settings[key] for key in ("preset", "language", "background") if key in settings}, {})
    for attempt in attempts:
        try:
            return pr.resolve_theme(attempt)
        except pr.PresentationError:
            continue
    return pr.resolve_theme({})


def safe_relative(value):
    """``value`` as a POSIX path relative to the output directory, or None when it is absolute, leaves the directory,
    or passes through a hidden folder (``.cache``)."""
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        return None
    if PureWindowsPath(value).is_absolute() or value.startswith("/"):
        return None
    parts = PurePosixPath(value).parts
    if not parts or any(part in ("..", ".") or part.startswith(".") for part in parts):
        return None
    return PurePosixPath(*parts).as_posix()


def _existing(root, value):
    """Relative path of ``value`` when it is safe and exists under ``root``."""
    relative = safe_relative(value)
    if relative is None:
        return None
    path = Path(root) / relative
    try:
        path.resolve().relative_to(Path(root).resolve())
    except ValueError:
        return None
    return relative if path.exists() else None


def _record_path(record):
    if isinstance(record, dict):
        return record.get("path")
    return record if isinstance(record, str) else None


def read_spec(root, manifest):
    """The redraw configuration next to the asset index (``manifest["spec"]``), or None."""
    relative = _existing(root, manifest.get("spec") or pr.SPEC_FILE)
    if relative is None:
        return None, None
    try:
        spec = wc.read_json(Path(root) / relative)
    except (OSError, ValueError, UnicodeDecodeError):
        return None, None
    if not isinstance(spec, dict) or spec.get("schema") != pr.SPEC_SCHEMA:
        return None, None
    return spec, relative


def _provenance(root, entry):
    relative = _existing(root, entry.get("provenance"))
    if relative is None:
        return None, {}
    try:
        record = wc.read_json(Path(root) / relative)
    except (OSError, ValueError, UnicodeDecodeError):
        return relative, {}
    return relative, record if isinstance(record, dict) else {}


def _data_paths(entry, provenance):
    """Data files of an asset: listed in its index entry (``data_files``/``data``) or its provenance record."""
    values = []
    for key in ("data_files", "data"):
        if isinstance(entry.get(key), list):
            values.extend(_record_path(item) for item in entry[key])
    if isinstance(provenance.get("data"), list):
        values.extend(_record_path(item) for item in provenance["data"])
    return [value for value in dict.fromkeys(values) if value]


def listed_paths(entry, provenance):
    """Every path an asset entry lists (files, preview, provenance, captions, data), in a stable order."""
    files = entry.get("files") if isinstance(entry.get("files"), dict) else {}
    values = [_record_path(files[role]) for role in _role_order(files)]
    values.append(entry.get("preview"))
    values.append(entry.get("provenance"))
    captions = entry.get("caption_files") if isinstance(entry.get("caption_files"), dict) else {}
    values.extend(_record_path(captions[language]) for language in sorted(captions))
    values.extend(_data_paths(entry, provenance))
    return [value for value in dict.fromkeys(values) if value]


# --------------------------------------------------------------------------------------------- gallery data

def _href(relative):
    return quote(relative, safe="/")


def _both(value, fallback=""):
    """``{"en": ..., "zh-CN": ...}`` of a string or language object (missing languages fall back)."""
    return {language: pr.localized(value, language, fallback) for language in pr.LANGUAGES}


def _has_both(value):
    return isinstance(value, dict) and all(isinstance(value.get(lang), str) and value[lang] for lang in pr.LANGUAGES)


def _caption_texts(root, entry):
    captions = dict(entry["captions"]) if isinstance(entry.get("captions"), dict) else {}
    files = entry.get("caption_files") if isinstance(entry.get("caption_files"), dict) else {}
    for language in pr.LANGUAGES:
        if captions.get(language):
            continue
        relative = _existing(root, _record_path(files.get(language)))
        if relative:
            try:
                captions[language] = (Path(root) / relative).read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError):
                pass
    if not any(captions.get(language) for language in pr.LANGUAGES) and entry.get("caption"):
        return str(entry["caption"])
    return {language: captions[language] for language in pr.LANGUAGES if captions.get(language)}


def _titles(entry, spec_asset):
    if _has_both(entry.get("titles")):
        return entry["titles"]
    title = spec_asset.get("title") if spec_asset else None
    if _has_both(title):
        return title
    return entry.get("title") or ""


def _compact(value, limit=240):
    text_value = value if isinstance(value, str) else json.dumps(wc.json_safe(value), ensure_ascii=False,
                                                                 separators=(", ", ": "))
    return text_value if len(text_value) <= limit else text_value[:limit - 1] + "…"


def _short(value, length=12):
    return value[:length] if isinstance(value, str) else None


def _source_rows(source_id, identity):
    rows = []
    engine = identity.get("engine_build_id") or identity.get("engine_version")
    if engine:
        rows.append(["engine", _compact(engine, 64)])
    if "catalog_version" in identity:
        rows.append(["catalog", identity.get("catalog_version") or None])
    for key, label in (("project_hash", "project"), ("result_hash", "result"), ("summary_sha256", "summary")):
        if identity.get(key):
            rows.append([label, _short(identity[key])])
    return rows


def _sources(entry, provenance, spec):
    by_id = {}
    for record in provenance.get("sources") or []:
        if isinstance(record, dict) and record.get("id"):
            by_id[record["id"]] = record
    spec_sources = (spec or {}).get("sources") or {}
    ids = [s for s in entry.get("sources") or [] if isinstance(s, str)] or list(by_id)
    result = []
    for source_id in dict.fromkeys(ids):
        record = by_id.get(source_id, {})
        label = (spec_sources.get(source_id) or {}).get("label") or record.get("label") or source_id
        identity = record.get("identity") if isinstance(record.get("identity"), dict) else {}
        result.append({"id": source_id, "label": _both(label, source_id), "rows": _source_rows(source_id, identity)})
    return result


def file_format(relative):
    """Format shown next to a file label, from its extension (``PNG``, ``SVG``, ``JSON``)."""
    return PurePosixPath(relative).suffix.lstrip(".").upper()


def file_label(role, relative, language):
    """Label of a listed file: what it is (from its role) and its format (from its extension)."""
    panel = PANEL_ROLE.fullmatch(role)
    if panel:
        label = gtext("file.panel", language, number=int(panel.group(1)))
    elif role == "data" and PurePosixPath(relative).name == "camera.json":
        label = gtext("file.camera", language)
    elif f"file.{role}" in GALLERY_TEXT["en"]:
        label = gtext(f"file.{role}", language)
    else:
        label = role.replace("-", " ").replace("_", " ").strip().capitalize() or role
    shown = file_format(relative)
    return gtext("file.format", language, label=label, format=shown) if shown else label


def _role_order(roles):
    def key(role):
        panel = PANEL_ROLE.fullmatch(role)
        if role in FILE_ROLES:
            return (0, FILE_ROLES.index(role), 0, role)
        if panel:
            return (1, 0, int(panel.group(1)), role)
        return (2, 0, 0, role)

    return sorted(roles, key=key)


def _file_items(root, entry, provenance_path, provenance):
    items: list[dict[str, Any]] = []

    def add(relative, labels):
        relative = _existing(root, relative)
        if relative is None or any(item["path"] == relative for item in items):
            return
        path = Path(root) / relative
        size = path.stat().st_size if path.is_file() else None
        items.append({"path": relative, "href": _href(relative), "bytes": size, "label": labels})

    files = entry.get("files") if isinstance(entry.get("files"), dict) else {}
    for role in _role_order(files):
        relative = _record_path(files[role])
        if relative:
            add(relative, {lang: file_label(role, relative, lang) for lang in pr.LANGUAGES})
    captions = entry.get("caption_files") if isinstance(entry.get("caption_files"), dict) else {}
    for language in sorted(captions):
        relative = _record_path(captions[language])
        if relative:
            add(relative, {lang: gtext("file.format", lang, format=file_format(relative), label=gtext(
                "file.caption", lang, language=gtext(f"caption.{language}", lang)
                if language in pr.LANGUAGES else language)) for lang in pr.LANGUAGES})
    if provenance_path:
        add(provenance_path, {lang: file_label("provenance", provenance_path, lang) for lang in pr.LANGUAGES})
    for relative in _data_paths(entry, provenance):
        add(relative, {lang: file_label("data", relative, lang) for lang in pr.LANGUAGES})
    # two data files of one format would read alike: name them
    for lang in pr.LANGUAGES:
        labels = [item["label"][lang] for item in items]
        for item in items:
            if labels.count(item["label"][lang]) > 1:
                item["label"][lang] = f"{item['label'][lang]} · {PurePosixPath(item['path']).name}"
    return items


def _media(root, entry):
    files = entry.get("files") if isinstance(entry.get("files"), dict) else {}

    def find(role):
        return _existing(root, _record_path(files.get(role)))

    image = find("image")
    preview = _existing(root, entry.get("preview"))
    if find("mp4"):
        poster = image or preview
        return {"type": "video", "src": _href(find("mp4")), "poster": _href(poster) if poster else None}
    still = find("gif") or image or preview
    return {"type": "image", "src": _href(still)} if still else None


def asset_view(root, entry, spec):
    """One asset of the gallery page: texts in both languages, links relative to the page."""
    spec_assets = {a.get("id"): a for a in (spec or {}).get("assets") or [] if isinstance(a, dict)}
    spec_asset = spec_assets.get(entry.get("id"))
    provenance_path, provenance = _provenance(root, entry)
    status = entry.get("status") or "written"
    built = status in BUILT_STATUSES
    preview = _existing(root, entry.get("preview")) if built else None
    error = entry.get("error") if isinstance(entry.get("error"), dict) else None
    settings = [[key, _compact(value)] for key, value in (spec_asset or {}).items() if key not in SHOWN_KEYS]
    details = entry.get("details")
    size = entry.get("size_px")
    return {
        "id": entry.get("id"), "kind": entry.get("kind"), "status": status, "built": built,
        "title": _titles(entry, spec_asset), "caption": _caption_texts(root, entry),
        "preview": _href(preview) if preview else None, "media": _media(root, entry) if built else None,
        "size": f"{size[0]} × {size[1]} px" if isinstance(size, list) and len(size) == 2 else None,
        "files": _file_items(root, entry, provenance_path, provenance),
        "sources": _sources(entry, provenance, spec), "settings": settings,
        "details": json.dumps(wc.json_safe(details), ensure_ascii=False, indent=1)[:6000] if details else None,
        "error": {"code": error.get("code"), "message": error.get("message"), "recovery": error.get("recovery")}
        if error else None,
    }


def gallery_data(root, manifest, spec=None, spec_path=None, contact_sheet=None):
    """The JSON embedded in ``index.html``."""
    language = manifest.get("language") if manifest.get("language") in pr.LANGUAGES else "en"
    assets = [asset_view(root, entry, spec) for entry in manifest["assets"] if isinstance(entry, dict)]
    spec_title = (spec or {}).get("title")
    title = spec_title or manifest.get("title") or {lang: pr.text("gallery.title", lang) for lang in pr.LANGUAGES}
    bilingual = any(_has_both(asset["caption"]) for asset in assets)
    text = {lang: {**GALLERY_TEXT[lang], "gallery": pr.text("gallery.title", lang)} for lang in pr.LANGUAGES}
    return {"language": language, "languages": list(pr.LANGUAGES) if bilingual else [language],
            "title": _both(title), "text": text, "assets": assets,
            "links": {"contact_sheet": _href(contact_sheet) if contact_sheet else None,
                      "asset_index": _href(pr.MANIFEST_FILE) if (Path(root) / pr.MANIFEST_FILE).is_file() else None,
                      "redraw": _href(spec_path) if spec_path else None}}


def _dark(theme):
    return theme.colors.get("text") == pr.BACKGROUNDS["dark"]["text"]


def _script_json(document):
    text_value = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
    return text_value.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def gallery_html(root, manifest, theme, spec=None, spec_path=None, contact_sheet=None):
    """The self-contained gallery page (no external resources)."""
    data = gallery_data(root, manifest, spec, spec_path, contact_sheet)
    language = data["language"]
    accent = theme.palette[0] if theme.palette else "#2a78d6"
    page = (_PAGE.replace("__LANG__", language).replace("__THEME__", "dark" if _dark(theme) else "light")
            .replace("__ACCENT__", accent if re.fullmatch(r"#[0-9a-fA-F]{6}", accent) else "#2a78d6")
            .replace("__TITLE__", html.escape(data["title"][language]))
            .replace("__DATA__", _script_json(data)))
    return page


# --------------------------------------------------------------------------------------------- contact sheet

def _sheet_colors(theme):
    colors = dict(theme.colors)
    if theme.background == "transparent" or colors.get("face") in (None, "none"):
        colors = dict(pr.BACKGROUNDS["light"])
    return colors


def _fit(text_artist, renderer, width_px, min_size):
    """Shrink a text artist until it is at most ``width_px`` wide (asset ids are never shortened)."""
    while text_artist.get_window_extent(renderer).width > width_px and text_artist.get_fontsize() > min_size:
        text_artist.set_fontsize(text_artist.get_fontsize() - 1)


def _truncate(text_artist, renderer, width_px):
    full = text_artist.get_text()
    if text_artist.get_window_extent(renderer).width <= width_px:
        return
    low, high = 0, len(full)
    while low < high:
        middle = (low + high + 1) // 2
        text_artist.set_text(full[:middle].rstrip() + "…")
        if text_artist.get_window_extent(renderer).width <= width_px:
            low = middle
        else:
            high = middle - 1
    text_artist.set_text(full[:low].rstrip() + "…")


def regular_families(families):
    """``families`` without those installed only in thin weights (a variable Noto Sans SC registers as weight 100
    and would draw every Chinese character hairline-thin); unchanged when nothing else would remain."""
    try:
        from matplotlib import font_manager
    except ImportError:
        return list(families)
    weights: dict[str, set[int]] = {}
    for entry in font_manager.fontManager.ttflist:
        weight = entry.weight if isinstance(entry.weight, int) else font_manager.weight_dict.get(entry.weight, 400)
        weights.setdefault(entry.name, set()).add(int(weight))
    kept = [family for family in families if family not in weights or max(weights[family]) >= 350]
    return kept or list(families)


def write_contact_sheet(out_dir, manifest, theme) -> Path:
    """``contact-sheet.png``: every preview with its asset id (large) and a short title, in the theme's fonts."""
    try:
        import matplotlib
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
        from matplotlib.patches import Rectangle
        from PIL import Image
    except ImportError:
        raise pr.PresentationError("DEPENDENCY_MISSING", "drawing the contact sheet needs matplotlib",
                                   recovery="python -m pip install \"os-optics-kit[plot]\"") from None
    import numpy as np

    out_dir = Path(out_dir)
    theme = dataclasses.replace(theme, font_family=regular_families(theme.font_family))
    language = theme.language
    entries = [entry for entry in manifest.get("assets") or [] if isinstance(entry, dict)]
    spec, _ = read_spec(out_dir, manifest)
    spec_assets = {a.get("id"): a for a in (spec or {}).get("assets") or [] if isinstance(a, dict)}
    titles = [pr.localized(_titles(entry, spec_assets.get(entry.get("id"))), language, "") for entry in entries]
    heading = pr.localized((spec or {}).get("title") or manifest.get("title"), language, "") \
        or pr.text("gallery.title", language)
    count = count_text(len(entries), language)
    status_texts = {status: gtext(f"status.{status}", language) for status in STATUSES
                    if status not in BUILT_STATUSES}
    strings = [heading, count, "…", gtext("no_preview", language), *status_texts.values(), *titles,
               *(str(entry.get("id")) for entry in entries)]
    pr.check_glyphs(strings, theme)

    colors = _sheet_colors(theme)
    columns = max(1, min(4, math.ceil(math.sqrt(len(entries))))) if entries else 1
    label_h, gap, header_h, margin = 92, 24, 84, 24
    cell_w = (CONTACT_SHEET_MAX_WIDTH - 2 * margin) // 4
    image_h = round((cell_w - 24) * 9 / 16)
    width = columns * cell_w + 2 * margin
    rows = max(1, math.ceil(len(entries) / columns))
    height = header_h + rows * (image_h + label_h + gap) + margin
    dpi = 100
    rc = {**theme.rc(), "figure.facecolor": colors["face"], "savefig.facecolor": colors["face"],
          "text.color": colors["text"], "savefig.transparent": False}
    with matplotlib.rc_context(rc):
        figure = Figure(figsize=(width / dpi, height / dpi), dpi=dpi, facecolor=colors["face"])
        canvas = FigureCanvasAgg(figure)
        renderer = canvas.get_renderer()

        def box(x, y, w, h):  # pixel box (top-left origin) -> figure fraction
            return [x / width, 1 - (y + h) / height, w / width, h / height]

        def at(x, y):
            return x / width, 1 - y / height

        def put(x, y, value, **style):  # text at a pixel position (top-left origin)
            fx, fy = at(x, y)
            return figure.text(fx, fy, value, **style)

        title = put(margin, 30, heading, fontsize=22, weight="bold", va="center", color=colors["text"])
        _truncate(title, renderer, width - 2 * margin - 200)
        put(width - margin, 30, count, fontsize=15, va="center", ha="right", color=colors["muted"])
        for index, entry in enumerate(entries):
            row, column = divmod(index, columns)
            x = margin + column * cell_w + 12
            y = header_h + row * (image_h + label_h + gap)
            inner_w = cell_w - 24
            figure.patches.append(Rectangle(at(x, y + image_h), inner_w / width, image_h / height,
                                            transform=figure.transFigure, facecolor=colors["grid"],
                                            edgecolor="none", alpha=0.35, zorder=-1))
            status = entry.get("status") or "written"
            preview = _existing(out_dir, entry.get("preview")) if status in BUILT_STATUSES else None
            if preview:
                axes = figure.add_axes(box(x, y, inner_w, image_h))
                with Image.open(out_dir / preview) as image:
                    axes.imshow(np.asarray(image.convert("RGBA")), interpolation="antialiased")
                axes.set_axis_off()
            else:
                message = status_texts.get(status, gtext("no_preview", language))
                put(x + inner_w / 2, y + image_h / 2, message, fontsize=18, ha="center", va="center",
                            color=pr.STATUS_COLORS["FAIL"] if status == "failed" else colors["muted"])
            label = put(x, y + image_h + 30, str(entry.get("id")), fontsize=24, weight="bold",
                                va="center", color=colors["text"], family=["DejaVu Sans Mono", *theme.font_family])
            _fit(label, renderer, inner_w, 11)
            caption = put(x, y + image_h + 68, titles[index], fontsize=14, va="center",
                                  color=colors["muted"])
            _truncate(caption, renderer, inner_w)
        path = out_dir / CONTACT_SHEET_FILE
        tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
        figure.savefig(tmp, format="png", dpi=dpi, facecolor=colors["face"], metadata={"Software": None})
    os.replace(tmp, path)
    return path


# --------------------------------------------------------------------------------------------- gallery

def _write_gallery(root, manifest, *, require_sheet=False):
    root = Path(root)
    theme = theme_from_manifest(manifest)
    spec, spec_path = read_spec(root, manifest)
    sheet = root / CONTACT_SHEET_FILE
    try:
        write_contact_sheet(root, manifest, theme)
        contact_sheet = CONTACT_SHEET_FILE
    except pr.PresentationError as exc:
        if exc.code != "DEPENDENCY_MISSING" or require_sheet:
            raise
        sheet.unlink(missing_ok=True)  # a sheet of an earlier build would no longer match the index
        contact_sheet = None
    wc.write_text_atomic(root / INDEX_FILE, gallery_html(root, manifest, theme, spec, spec_path, contact_sheet))
    return {"index": INDEX_FILE, "contact_sheet": contact_sheet}


def write_gallery(out_dir, manifest=None) -> dict:
    """(Re)write ``index.html`` and ``contact-sheet.png`` of a built presentation directory.

    Returns ``{"index": "index.html", "contact_sheet": "contact-sheet.png"}`` (paths relative to ``out_dir``;
    ``contact_sheet`` is None when matplotlib is not installed)."""
    out_dir = Path(out_dir)
    if manifest is None:
        manifest = read_manifest(out_dir)
    return _write_gallery(out_dir, manifest)


# --------------------------------------------------------------------------------------------- bundle

def _closure(selected, entries, spec_assets):
    """Selected ids plus every asset they depend on (``depends_on`` and compose/animation panels)."""
    result: list[str] = []
    pending = list(selected)
    while pending:
        asset_id = pending.pop(0)
        if asset_id in result:
            continue
        result.append(asset_id)
        entry = entries.get(asset_id) or {}
        spec_asset = spec_assets.get(asset_id) or {}
        dependencies = list(entry.get("depends_on") or []) + list(spec_asset.get("depends_on") or [])
        for panel in spec_asset.get("panels") or []:
            if isinstance(panel, dict) and isinstance(panel.get("asset"), str):
                dependencies.append(panel["asset"])
        pending.extend(d for d in dependencies if isinstance(d, str) and d not in result)
    return result


def _source_ids(spec_asset, entry):
    ids = [s for s in entry.get("sources") or [] if isinstance(s, str)]
    if isinstance(spec_asset.get("source"), str):
        ids.append(spec_asset["source"])
    for key in ("sources", "runs"):
        if isinstance(spec_asset.get(key), list):
            ids.extend(s for s in spec_asset[key] if isinstance(s, str))
    for panel in spec_asset.get("panels") or []:
        if isinstance(panel, dict) and isinstance(panel.get("source"), str):
            ids.append(panel["source"])
    return ids


def source_dirs(out_dir, spec, spec_path):
    """``[(source id, resolved directory)]`` of the specification's sources that exist (paths relative to it)."""
    if not spec:
        return []
    base = (Path(out_dir) / (spec_path or pr.SPEC_FILE)).parent
    found = []
    for source_id, source in (spec.get("sources") or {}).items():
        path = source.get("path") if isinstance(source, dict) else None
        if not isinstance(path, str) or not path:
            continue
        directory = (base / path).resolve()
        if directory.is_dir():
            found.append((source_id, directory))
    return found


def _filtered_spec(spec, keep, entries):
    keep_set = set(keep)
    assets = [a for a in spec.get("assets") or [] if isinstance(a, dict) and a.get("id") in keep_set]
    used: list[str] = []
    for asset in assets:
        used.extend(_source_ids(asset, entries.get(asset["id"]) or {}))
    sources = {key: value for key, value in (spec.get("sources") or {}).items() if key in set(used)}
    for source_id, source in sources.items():
        path = source.get("path") if isinstance(source, dict) else None
        if isinstance(path, str) and (PureWindowsPath(path).is_absolute() or PureWindowsPath(path).drive
                                      or path.startswith(("/", "\\"))):
            raise pr.PresentationError(
                "BUNDLE_PATH_LEAK", f"presentation.json: sources.{source_id}.path is an absolute path",
                recovery=f"set sources.{source_id}.path relative to presentation.json and build again",
                file=pr.SPEC_FILE)
    return {**spec, "sources": sources, "assets": assets}


def _bundle_files(out_dir, entry):
    """Relative paths of the files an asset entry lists (folders expanded), all inside ``out_dir``."""
    _, provenance = _provenance(out_dir, entry)
    result = []
    for value in listed_paths(entry, provenance):
        relative = safe_relative(value)
        if relative is None:
            raise pr.PresentationError(
                "MANIFEST_INVALID", f"asset {entry.get('id')}: {value!r} is not a path inside the presentation "
                "directory", recovery="build the presentation again with 'os-optics-kit present build SPEC'",
                asset=entry.get("id"))
        path = out_dir / relative
        if path.is_dir():
            result.extend(p.relative_to(out_dir).as_posix() for p in sorted(path.rglob("*"))
                          if p.is_file() and not any(part.startswith(".") for part in p.relative_to(path).parts))
        elif path.is_file():
            result.append(relative)
        else:
            raise pr.PresentationError(
                "ASSET_FILE_MISSING", f"asset {entry.get('id')}: {relative} is listed but missing",
                recovery="build the presentation again with 'os-optics-kit present build SPEC'",
                asset=entry.get("id"))
    return result


def _is_text(data):
    if b"\0" in data[:8192]:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def scan_paths(root):
    """``[(relative file, match)]`` of absolute machine paths in the text files under ``root``."""
    found = []
    for path in sorted(Path(root).rglob("*")):
        if not path.is_file():
            continue
        data = path.read_bytes()
        if not _is_text(data):
            continue
        text_value = data.decode("utf-8")
        for pattern in PATH_LEAKS:
            match = pattern.search(text_value)
            if match:
                found.append((path.relative_to(root).as_posix(), match.group(0)))
                break
    return found


def _is_previous_bundle(directory):
    """A directory written by ``write_bundle``: an asset index and nothing it does not list."""
    directory = Path(directory)
    manifest = pr.read_manifest(directory) if (directory / pr.MANIFEST_FILE).is_file() else None
    if not manifest or not isinstance(manifest.get("assets"), list):
        return False
    allowed = {INDEX_FILE, CONTACT_SHEET_FILE, pr.MANIFEST_FILE, pr.SPEC_FILE}
    for entry in manifest["assets"]:
        if isinstance(entry, dict):
            try:
                allowed.update(_bundle_files(directory, entry))
            except pr.PresentationError:
                return False
    present = {p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()}
    return present <= allowed


def _is_previous_zip(path, stem):
    if not zipfile.is_zipfile(path):
        return False
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
    return f"{stem}/{pr.MANIFEST_FILE}" in names and all(name.startswith(f"{stem}/") for name in names)


def _write_zip(staging, target, stem):
    files = sorted(p.relative_to(staging).as_posix() for p in staging.rglob("*") if p.is_file())
    with zipfile.ZipFile(target, "w") as archive:
        for relative in files:
            info = zipfile.ZipInfo(f"{stem}/{relative}", date_time=ZIP_DATE)
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, (staging / relative).read_bytes(), compresslevel=6)
    return len(files)


def _counts(entries):
    counts = {status: 0 for status in STATUSES}
    for entry in entries:
        status = entry.get("status")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _archive_mode(dest, archive):
    if archive is None:
        return dest.suffix.lower() == ".zip"
    if isinstance(archive, bool):
        return archive
    if archive in ("zip", "directory"):
        return archive == "zip"
    raise pr.PresentationError("SETTING_INVALID", f"archive {archive!r}: use zip or directory",
                               recovery="end the destination in .zip for an archive, or pass archive=\"directory\"")


def write_bundle(out_dir, dest, *, only=None, archive=None, overwrite=False) -> dict:
    """Portable copy of selected assets: gallery, contact sheet, filtered asset index and redraw configuration.

    ``dest`` ending in ``.zip`` (or ``archive="zip"``) writes a deterministic zip archive whose entries sit in one
    folder named after the archive; otherwise a directory. Returns ``{"bundle", "index", "asset_index",
    "contact_sheet", "assets", "files", "bytes"}``: ``index``/``asset_index``/``contact_sheet`` are paths inside the
    bundle (inside the archive's folder for a zip), ``bytes`` is the size of the bundle on disk."""
    out_dir = Path(out_dir).resolve()
    manifest = read_manifest(out_dir)
    entries = {entry.get("id"): entry for entry in manifest["assets"] if isinstance(entry, dict)}
    if only:
        unknown = [asset_id for asset_id in only if asset_id not in entries]
        if unknown:
            raise pr.PresentationError(
                "ASSET_UNKNOWN", f"no asset {', '.join(map(repr, unknown))}; assets: {list(entries)}",
                recovery="choose ids from 'os-optics-kit present list DIR'", asset=unknown[0], known=list(entries))
        selected = [asset_id for asset_id in entries if asset_id in set(only)]
    else:
        selected = [asset_id for asset_id, entry in entries.items() if entry.get("status") in BUILT_STATUSES]
    if not selected:
        raise pr.PresentationError("BUNDLE_EMPTY", "no built assets to bundle",
                                   recovery="build the presentation with 'os-optics-kit present build SPEC'")

    dest_given = Path(dest)
    zipped = _archive_mode(dest_given, archive)
    dest = dest_given.resolve()
    stem = dest.stem if zipped and dest.suffix.lower() == ".zip" else dest.name
    if dest == out_dir or out_dir.is_relative_to(dest):
        raise pr.PresentationError("OUTPUT_EXISTS", "the bundle destination contains the presentation directory",
                                   recovery="choose a destination outside the presentation directory")
    spec, spec_path = read_spec(out_dir, manifest)
    for source_id, source_dir in source_dirs(out_dir, spec, spec_path):
        if dest == source_dir or dest.is_relative_to(source_dir):
            raise pr.PresentationError(
                "OUTPUT_INVALID", f"{dest_given.name} is inside the saved source {source_id!r}",
                recovery="choose a destination outside the saved runs and studies", source=source_id)
    if dest.exists():
        recognized = _is_previous_zip(dest, stem) if zipped else dest.is_dir() and _is_previous_bundle(dest)
        if not overwrite or not recognized:
            raise pr.PresentationError(
                "OUTPUT_EXISTS", f"{dest_given.name} already exists" + ("" if recognized or not overwrite else
                                                                        " and is not a presentation bundle"),
                recovery="choose a new destination" + (" or pass --overwrite" if recognized else ""))

    spec_assets = {a.get("id"): a for a in (spec or {}).get("assets") or [] if isinstance(a, dict)}
    keep = _closure(selected, entries, spec_assets)
    files: list[str] = []
    for asset_id in selected:
        files.extend(_bundle_files(out_dir, entries[asset_id]))
    files = list(dict.fromkeys(files))
    bundled_spec = _filtered_spec(spec, keep, entries) if spec else None

    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = dest.parent / f".{stem}.bundle-tmp-{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    try:
        staging.mkdir()
        for relative in files:
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(out_dir / relative, target)
        bundle_manifest = {key: value for key, value in manifest.items() if key not in ("spec", "spec_sha256")}
        bundle_manifest["assets"] = [entries[asset_id] for asset_id in selected]
        if bundled_spec is not None:
            spec_file = wc.write_text_atomic(staging / pr.SPEC_FILE, pr.spec_text(bundled_spec))
            bundle_manifest["spec"] = pr.SPEC_FILE
            bundle_manifest["spec_sha256"] = wc.sha256_file(spec_file)
        bundle_manifest["gallery"] = {"index": INDEX_FILE, "contact_sheet": CONTACT_SHEET_FILE}
        bundle_manifest["counts"] = _counts(bundle_manifest["assets"])
        pr.write_manifest(staging, bundle_manifest)  # first, so the page links the asset index
        gallery = _write_gallery(staging, bundle_manifest)
        if gallery != bundle_manifest["gallery"]:
            bundle_manifest["gallery"] = gallery
            pr.write_manifest(staging, bundle_manifest)
        leaks = scan_paths(staging)
        if leaks:
            relative, match = leaks[0]
            raise pr.PresentationError(
                "BUNDLE_PATH_LEAK", f"{relative} contains the absolute path {match!r}"
                + (f" (and {len(leaks) - 1} more files)" if len(leaks) > 1 else ""),
                recovery="replace absolute paths in that file with relative ones, then bundle again",
                file=relative, files=[item[0] for item in leaks])
        count = sum(1 for p in staging.rglob("*") if p.is_file())
        if zipped:
            partial = dest.with_name(f".{dest.name}.tmp-{os.getpid()}")
            _write_zip(staging, partial, stem)
            os.replace(partial, dest)
            size = dest.stat().st_size
        else:
            size = sum(p.stat().st_size for p in staging.rglob("*") if p.is_file())
            if dest.exists():
                previous = dest.with_name(f".{dest.name}.old-{os.getpid()}")
                os.replace(dest, previous)
                os.replace(staging, dest)
                shutil.rmtree(previous, ignore_errors=True)
            else:
                os.replace(staging, dest)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    prefix = f"{stem}/" if zipped else ""
    return {"bundle": dest_given.as_posix(), "archive": "zip" if zipped else "directory",
            "index": prefix + INDEX_FILE, "asset_index": prefix + pr.MANIFEST_FILE,
            "contact_sheet": prefix + gallery["contact_sheet"] if gallery["contact_sheet"] else None,
            "assets": selected, "files": count, "bytes": size}


# --------------------------------------------------------------------------------------------- page template

_PAGE = r"""<!doctype html>
<html lang="__LANG__" data-theme="__THEME__">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>__TITLE__</title>
<style>
:root{--accent:__ACCENT__;--bg:#ffffff;--surface:#f6f8fa;--card:#ffffff;--text:#1f2328;--muted:#59636e;
--line:#d8dee4;--thumb:#f1f3f5;--error:#b42318;--error-bg:#fdf3f2;--overlay:#ffffff;
--sans:system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,"Noto Sans","Noto Sans CJK SC",
"Noto Sans SC","Source Han Sans SC","Microsoft YaHei","PingFang SC","Hiragino Sans GB","WenQuanYi Zen Hei",sans-serif;
--mono:ui-monospace,"SFMono-Regular","Cascadia Mono",Consolas,"Liberation Mono",Menlo,"Noto Sans Mono CJK SC",
"Microsoft YaHei",monospace}
[data-theme=dark]{--accent:#79b0f2;--bg:#111418;--surface:#181c21;--card:#181c21;--text:#e8eaed;--muted:#a3abb5;--line:#2c323a;
--thumb:#0b0d10;--error:#ff8a80;--error-bg:#2a1715;--overlay:#111418}
*{box-sizing:border-box}
html,body{margin:0;background:var(--bg);color:var(--text)}
body{font:15px/1.55 var(--sans);-webkit-font-smoothing:antialiased}
a{color:var(--accent)}
[hidden]{display:none!important}
header{display:flex;flex-wrap:wrap;gap:14px 28px;align-items:flex-end;justify-content:space-between;
padding:28px 32px 20px;border-bottom:1px solid var(--line)}
h1{font-size:26px;line-height:1.25;margin:0 0 4px;font-weight:650}
.sub{color:var(--muted);font-size:14px}
.tools{display:flex;flex-wrap:wrap;gap:10px 18px;align-items:center;font-size:14px}
#links{display:inline-flex;flex-wrap:wrap;gap:6px 18px}
.tools a{text-decoration:none}
.tools a:hover{text-decoration:underline}
.lang{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden}
.lang button{border:0;background:transparent;color:var(--text);font:inherit;padding:4px 12px;cursor:pointer}
.lang button[aria-pressed=true]{background:var(--text);color:var(--bg)}
main{padding:24px 32px 56px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:22px}
.card{display:flex;flex-direction:column;background:var(--card);border:1px solid var(--line);border-radius:10px;
overflow:hidden;color:inherit;text-decoration:none}
.card:hover{border-color:var(--accent)}
.card:focus-visible,button:focus-visible,.stage a:focus-visible,aside a:focus-visible{outline:2px solid var(--accent);
outline-offset:2px}
.thumb{aspect-ratio:16/9;background:var(--thumb);display:flex;align-items:center;justify-content:center;
overflow:hidden}
.thumb img{max-width:100%;max-height:100%;object-fit:contain;display:block}
.meta{padding:12px 14px 14px}
.id{font-family:var(--mono);font-size:13.5px;font-weight:650;color:var(--accent);word-break:break-all}
.title{font-weight:600;margin:2px 0 4px}
.caption{color:var(--muted);font-size:13.5px;margin:0;display:-webkit-box;-webkit-line-clamp:3;
-webkit-box-orient:vertical;overflow:hidden}
.problem{padding:16px 18px;color:var(--error);background:var(--error-bg);width:100%;height:100%;font-size:13.5px;
overflow:auto}
.problem strong{display:block;margin-bottom:4px}
.problem.calm{color:var(--muted);background:var(--surface);display:flex;flex-direction:column;
justify-content:center;align-items:center;text-align:center}
.problem p{margin:4px 0;color:var(--text)}
.empty{color:var(--muted)}
#viewer{position:fixed;inset:0;background:var(--overlay);display:flex;flex-direction:column;z-index:10}
.vbar{display:flex;align-items:center;gap:12px;padding:10px 20px;border-bottom:1px solid var(--line)}
.vbar .grow{flex:1;min-width:0}
.vbar .vtitle{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.vbar .pos{color:var(--muted);font-size:13px;white-space:nowrap}
.btn{border:1px solid var(--line);background:var(--card);color:var(--text);font:inherit;font-size:14px;
padding:5px 12px;border-radius:8px;cursor:pointer}
.btn:hover:not(:disabled){border-color:var(--accent)}
.btn:disabled{opacity:.4;cursor:default}
.vbody{flex:1;display:flex;min-height:0}
.stage{flex:1;display:flex;align-items:center;justify-content:center;padding:20px;min-width:0;background:var(--thumb)}
.stage img,.stage video{max-width:100%;max-height:100%;object-fit:contain;display:block}
.stage .problem{max-width:560px;height:auto;border-radius:10px}
aside{width:390px;flex:none;overflow:auto;border-left:1px solid var(--line);padding:16px 20px 28px;font-size:14px}
aside h2{font-size:12.5px;letter-spacing:.04em;text-transform:uppercase;color:var(--muted);margin:20px 0 6px;
font-weight:600}
aside .lead{margin:0}
ul.files{list-style:none;padding:0;margin:0}
ul.files li{display:flex;justify-content:space-between;gap:12px;padding:3px 0}
ul.files .bytes{color:var(--muted);font-size:12.5px;white-space:nowrap}
dl.kv{display:grid;grid-template-columns:max-content 1fr;gap:3px 14px;margin:0;font-size:13.5px}
dl.kv dt{color:var(--muted)}
dl.kv dd{margin:0;word-break:break-word}
dl.kv dd.code{font-family:var(--mono);font-size:12.5px}
.source{margin:0 0 10px}
.source strong{display:block;font-weight:600}
details pre{white-space:pre-wrap;word-break:break-word;font-family:var(--mono);font-size:12px;
background:var(--surface);padding:10px;border-radius:8px;margin:6px 0 0}
details summary{cursor:pointer;color:var(--muted)}
@media (max-width:820px){header,main{padding-left:16px;padding-right:16px}
.vbody{flex-direction:column;overflow:auto}.stage{min-height:55vh;flex:none}
aside{width:auto;border-left:0;border-top:1px solid var(--line)}
.vbar{flex-wrap:wrap;padding:10px 16px}.vbar .grow{flex-basis:100%}.vbar .pos{margin-right:auto}}
</style>
</head>
<body>
<header>
<div><h1 id="page-title">__TITLE__</h1><div class="sub" id="page-sub"></div></div>
<div class="tools"><span id="links"></span><span class="lang" id="lang" role="group"></span></div>
</header>
<main><div class="grid" id="grid"></div></main>
<div id="viewer" role="dialog" aria-modal="true" aria-labelledby="viewer-title" hidden>
<div class="vbar"><div class="grow"><div class="id" id="viewer-id"></div><div class="vtitle" id="viewer-title"></div>
</div><span class="pos" id="viewer-pos"></span><button class="btn" id="prev" type="button"></button>
<button class="btn" id="next" type="button"></button><button class="btn" id="close" type="button"></button></div>
<div class="vbody"><div class="stage" id="stage"></div><aside id="panel"></aside></div>
</div>
<script type="application/json" id="gallery-data">__DATA__</script>
<script>
(function () {
  "use strict";
  var data = JSON.parse(document.getElementById("gallery-data").textContent);
  var lang = data.language, current = -1, opener = null;
  var viewer = document.getElementById("viewer");

  function t(key, values) {
    var table = data.text[lang] || data.text.en;
    var s = table[key] !== undefined ? table[key] : data.text.en[key];
    if (s === undefined) { return ""; }
    return String(s).replace(/\{(\w+)\}/g, function (m, k) { return values && values[k] !== undefined ? values[k] : m; });
  }
  function loc(v) {
    if (v === null || v === undefined) { return ""; }
    if (typeof v === "string") { return v; }
    return v[lang] || v.en || v["zh-CN"] || "";
  }
  function el(tag, attrs, kids) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (attrs[k] === null || attrs[k] === undefined) { return; }
      if (k === "text") { node.textContent = attrs[k]; } else if (k === "cls") { node.className = attrs[k]; }
      else { node.setAttribute(k, attrs[k]); }
    });
    (kids || []).forEach(function (kid) { if (kid) { node.appendChild(kid); } });
    return node;
  }
  function bytes(n) {
    if (n === null || n === undefined) { return ""; }
    if (n < 1024) { return n + " B"; }
    if (n < 1048576) { return Math.round(n / 1024) + " KB"; }
    return (n / 1048576).toFixed(1) + " MB";
  }
  function problem(a) {
    var e = a.error || {};
    var label = t("status." + a.status) || a.status;
    if (a.status === "pending" && !e.message) {
      return el("div", {cls: "problem calm"}, [el("strong", {text: label}), el("p", {text: t("pending_hint")})]);
    }
    return el("div", {cls: "problem"}, [
      el("strong", {text: label}),
      e.message ? el("p", {text: e.message}) : null,
      e.recovery ? el("p", {text: t("next_step", {recovery: e.recovery})}) : null]);
  }

  function renderHeader() {
    var title = loc(data.title);
    document.title = title;
    document.documentElement.lang = lang;
    document.getElementById("page-title").textContent = title;
    var count = data.assets.length;
    document.getElementById("page-sub").textContent = t("gallery") + " · " +
      (count === 1 ? t("asset") : t("assets", {count: count}));
    var links = document.getElementById("links");
    links.textContent = "";
    ["contact_sheet", "asset_index", "redraw"].forEach(function (key) {
      if (data.links[key]) { links.appendChild(el("a", {href: data.links[key], text: t(key)})); }
    });
    var box = document.getElementById("lang");
    box.textContent = "";
    box.hidden = data.languages.length < 2;
    box.setAttribute("aria-label", t("language"));
    data.languages.forEach(function (code) {
      var b = el("button", {type: "button", "data-lang": code, "aria-pressed": String(code === lang),
                            text: t("language." + code)});
      b.addEventListener("click", function () { setLanguage(code); });
      box.appendChild(b);
    });
  }

  function renderGrid() {
    var grid = document.getElementById("grid");
    grid.textContent = "";
    if (!data.assets.length) { grid.appendChild(el("p", {cls: "empty", text: t("empty")})); }
    data.assets.forEach(function (a, i) {
      var thumb = el("div", {cls: "thumb"}, [
        a.built && a.preview ? el("img", {src: a.preview, alt: loc(a.title) || a.id, decoding: "async"}) :
          (a.built ? el("span", {cls: "empty", text: t("no_preview")}) : problem(a))]);
      var card = el("a", {cls: "card", href: "#" + a.id, "data-asset": a.id, "aria-label": t("open", {id: a.id})}, [
        thumb, el("div", {cls: "meta"}, [el("div", {cls: "id", text: a.id}),
          loc(a.title) ? el("div", {cls: "title", text: loc(a.title)}) : null,
          loc(a.caption) ? el("p", {cls: "caption", text: loc(a.caption)}) : null])]);
      card.addEventListener("click", function (ev) { ev.preventDefault(); show(i); });
      grid.appendChild(card);
    });
  }

  function kv(rows) {
    var dl = el("dl", {cls: "kv"});
    rows.forEach(function (r) {
      dl.appendChild(el("dt", {text: r[0]}));
      dl.appendChild(el("dd", {cls: r[2] ? "code" : null, text: r[1]}));
    });
    return dl;
  }

  function renderViewer() {
    var a = data.assets[current];
    document.getElementById("viewer-id").textContent = a.id;
    document.getElementById("viewer-title").textContent = loc(a.title);
    document.getElementById("viewer-pos").textContent = t("position", {index: current + 1, count: data.assets.length});
    var prev = document.getElementById("prev"), next = document.getElementById("next");
    prev.textContent = "← " + t("previous"); next.textContent = t("next") + " →";
    document.getElementById("close").textContent = t("close");
    prev.disabled = current === 0; next.disabled = current === data.assets.length - 1;
    var stage = document.getElementById("stage");
    stage.textContent = "";
    if (a.built && a.media && a.media.type === "video") {
      stage.appendChild(el("video", {id: "full-video", src: a.media.src, poster: a.media.poster, controls: "",
                                     playsinline: "", preload: "metadata", "aria-label": loc(a.title)}));
    } else if (a.built && a.media) {
      stage.appendChild(el("img", {id: "full-image", src: a.media.src, alt: loc(a.title) || a.id}));
    } else if (!a.built) {
      stage.appendChild(problem(a));
    }
    var panel = document.getElementById("panel");
    panel.textContent = "";
    if (loc(a.caption)) { panel.appendChild(el("p", {cls: "lead", text: loc(a.caption)})); }
    var facts = [[t("kind"), t("kind." + a.kind) || a.kind], [t("status"), t("status." + a.status) || a.status]];
    if (a.size) { facts.push([t("size"), a.size]); }
    panel.appendChild(kv(facts));
    if (a.files.length) {
      panel.appendChild(el("h2", {text: t("files")}));
      var ul = el("ul", {cls: "files"});
      a.files.forEach(function (f) {
        ul.appendChild(el("li", {}, [el("a", {href: f.href, text: loc(f.label), title: f.path}),
                                     el("span", {cls: "bytes", text: bytes(f.bytes)})]));
      });
      panel.appendChild(ul);
    }
    if (a.sources.length) {
      panel.appendChild(el("h2", {text: t("sources")}));
      a.sources.forEach(function (s) {
        var rows = s.rows.map(function (r) {
          var value = r[0] === "catalog" && !r[1] ? t("catalog_none") : r[1];
          return [t(r[0]), value, r[0] !== "catalog"];
        });
        panel.appendChild(el("div", {cls: "source"}, [el("strong", {text: loc(s.label)}), rows.length ? kv(rows) : null]));
      });
    }
    if (a.settings.length) {
      panel.appendChild(el("h2", {text: t("settings")}));
      panel.appendChild(kv(a.settings.map(function (r) { return [r[0], r[1], true]; })));
      if (data.links.redraw) {
        panel.appendChild(el("p", {}, [el("a", {href: data.links.redraw, text: t("redraw")})]));
      }
    }
    if (a.details) {
      panel.appendChild(el("details", {}, [el("summary", {text: t("values")}), el("pre", {text: a.details})]));
    }
  }

  function show(i) {
    if (i < 0 || i >= data.assets.length) { return; }
    if (current < 0) { opener = document.activeElement; }
    current = i;
    viewer.hidden = false;
    document.body.style.overflow = "hidden";
    renderViewer();
    var hash = "#" + data.assets[i].id;
    if (location.hash !== hash) {
      try { history.replaceState(null, "", hash); } catch (e) { /* hash stays as it is */ }
    }
    document.getElementById("close").focus();
  }
  function hide() {
    if (current < 0) { return; }
    current = -1;
    viewer.hidden = true;
    document.body.style.overflow = "";
    try { history.replaceState(null, "", location.pathname + location.search); } catch (e) { /* ignore */ }
    if (opener && opener.focus) { opener.focus(); }
  }
  function setLanguage(code) {
    lang = code;
    renderHeader();
    renderGrid();
    if (current >= 0) { renderViewer(); }
  }
  function fromHash() {
    var id = decodeURIComponent(location.hash.replace(/^#/, ""));
    var index = data.assets.map(function (a) { return a.id; }).indexOf(id);
    if (index >= 0) { show(index); } else { hide(); }
  }

  document.getElementById("prev").addEventListener("click", function () { show(current - 1); });
  document.getElementById("next").addEventListener("click", function () { show(current + 1); });
  document.getElementById("close").addEventListener("click", hide);
  document.addEventListener("keydown", function (ev) {
    if (current < 0 || ev.altKey || ev.ctrlKey || ev.metaKey) { return; }
    if (ev.key === "Escape") { ev.preventDefault(); hide(); }
    else if (ev.key === "ArrowLeft") { ev.preventDefault(); show(current - 1); }
    else if (ev.key === "ArrowRight") { ev.preventDefault(); show(current + 1); }
    else if (ev.key === "Home") { ev.preventDefault(); show(0); }
    else if (ev.key === "End") { ev.preventDefault(); show(data.assets.length - 1); }
  });
  window.addEventListener("hashchange", fromHash);
  renderHeader();
  renderGrid();
  if (location.hash) { fromHash(); }
}());
</script>
</body>
</html>
"""
