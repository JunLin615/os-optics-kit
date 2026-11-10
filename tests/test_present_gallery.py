"""Tests of the result gallery, contact sheet and portable bundle (tools/os_optics_kit/gallery.py).

A synthetic built presentation directory (Pillow-made PNGs, no engine) stands in for ``present build``.
Tests that need Pillow, matplotlib, playwright or Chrome skip when those are missing; with
``OS_OPTICS_KIT_REQUIRE_PRESENTATION=1`` they fail instead.
"""

import html.parser
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from urllib.parse import unquote
import zipfile

from os_optics_kit import gallery
from os_optics_kit import presentation as pr

REQUIRE = os.environ.get("OS_OPTICS_KIT_REQUIRE_PRESENTATION") == "1"
SPEC_TITLE = {"en": "Beam expander results", "zh-CN": "扩束器结果"}
CAPTIONS = {
    "system-view": {"en": "Beam expander seen 55 deg from the optical axis.", "zh-CN": "扩束器，与光轴夹角 55°。"},
    "sweep-waist": {"en": "Output waist radius across 9 lens spacings; 7 PASS.",
                    "zh-CN": "输出束腰半径在 9 个透镜间距上的取值；7 个 PASS。"},
    "before-after": {"en": "Spot size before and after the spacing change.", "zh-CN": "间距调整前后的光斑尺寸。"},
    "orbit": {"en": "Orbit around the expander.", "zh-CN": "绕扩束器一周。"},
    "power": {"en": "Received power by observation plane.", "zh-CN": "各观测面的接收功率。"},
}
TITLES = {"system-view": {"en": "System view", "zh-CN": "系统视图"},
          "sweep-waist": {"en": "Waist vs spacing", "zh-CN": "束腰随间距的变化"},
          "before-after": {"en": "Before and after", "zh-CN": "调整前后"},
          "orbit": {"en": "Orbit", "zh-CN": "环绕"},
          "power": {"en": "Received power", "zh-CN": "接收功率"}}
COLORS = {"system-view": (42, 120, 214), "sweep-waist": (232, 116, 59), "before-after": (25, 169, 121),
          "orbit": (148, 94, 207)}
IDENTITY = {"project_hash": "a1b2c3d4e5f6a7b8c9d0" * 3, "result_hash": "0f1e2d3c4b5a69788796" * 3,
            "engine_version": "0.13.0", "engine_build_id": "os-optics-0.13.0-test", "catalog_version": None}


def need(test, module):
    try:
        return importlib.import_module(module)
    except ImportError:
        if REQUIRE:
            raise
        test.skipTest(f"{module} is not installed")


def make_presentation(root, *, language="en", background="light", bilingual=True, extra_assets=0):
    """A built presentation directory as ``present build`` leaves it, plus files a bundle must leave out."""
    from PIL import Image

    root = Path(root)
    out = root / "out"
    (root / "run").mkdir(parents=True)
    (root / "run" / "manifest.json").write_text("{}", encoding="utf-8")
    spec = {"schema": pr.SPEC_SCHEMA, "schema_version": 1, "title": SPEC_TITLE,
            "theme": {"preset": "presentation", "language": language, "background": background},
            "sources": {"nominal": {"path": "../run", "label": {"en": "Nominal", "zh-CN": "标称"}},
                        "l2": {"path": "../l2-sweep"}, "unused": {"path": "../other-run"}},
            "assets": [
                {"id": "system-view", "kind": "view", "source": "nominal", "camera": [55, 22], "beams": True,
                 "title": TITLES["system-view"], "caption": CAPTIONS["system-view"]},
                {"id": "sweep-waist", "kind": "plot", "type": "sweep", "source": "l2", "y": "target:expanded_waist"},
                {"id": "before-after", "kind": "compose", "layout": "row",
                 "panels": [{"asset": "system-view", "label": "Before"}, {"asset": "sweep-waist", "label": "After"}]},
                {"id": "orbit", "kind": "animation", "mode": "orbit", "source": "nominal"},
                {"id": "power", "kind": "plot", "type": "power", "source": "nominal"},
            ]}
    spec["assets"].append({"id": "spot-map", "kind": "plot", "type": "power", "source": "nominal"})
    for index in range(extra_assets):
        spec["assets"].append({"id": f"extra-{index:02d}", "kind": "plot", "type": "power", "source": "nominal"})
    out.mkdir(parents=True)
    pr.save_spec(spec, out / pr.SPEC_FILE)
    theme = pr.resolve_theme({"language": language, "background": background})
    entries = []
    for asset in spec["assets"]:
        asset_id = asset["id"]
        if asset_id == "spot-map":  # not built by a 'present build --only' run
            entries.append({"id": asset_id, "kind": asset["kind"], "status": "pending", "files": {}})
            continue
        folder = pr.asset_dir(out, asset_id)
        folder.mkdir(parents=True)
        captions = dict(CAPTIONS.get(asset_id) or {"en": f"Power at plane {asset_id}.",
                                                   "zh-CN": f"{asset_id} 观测面的功率。"})
        if not bilingual:
            captions.pop("zh-CN")
        source = asset.get("source")
        entry = {"id": asset_id, "kind": asset["kind"], "status": "written",
                 "title": pr.localized(TITLES.get(asset_id, asset_id), language),
                 "titles": TITLES.get(asset_id, {"en": asset_id, "zh-CN": asset_id}),
                 "caption": pr.localized(captions, language), "captions": captions, "files": {},
                 "sources": [source] if source else ["nominal", "l2"],
                 "depends_on": [p["asset"] for p in asset.get("panels", [])], "details": {"frames": 1},
                 "fingerprint": "f" * 64}
        if asset_id == "power":
            entry.update(status="missing_input", error={
                "code": "SOURCE_MISSING", "message": "source 'nominal': ../run is not a saved run directory",
                "recovery": "point sources.nominal.path at a run directory, relative to presentation.json"})
            entries.append(entry)
            continue
        image = folder / f"{asset_id}.png"
        Image.new("RGB", (800, 450), COLORS.get(asset_id, (107, 114, 128))).save(image)
        entry["files"]["image"] = pr.file_record(image, out)
        if asset_id == "system-view":
            svg = folder / f"{asset_id}.svg"
            svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="8" height="4"/>\n', encoding="utf-8")
            entry["files"]["svg"] = pr.file_record(svg, out)
        if asset_id == "orbit":
            frames = [Image.new("RGB", (160, 90), color) for color in ((148, 94, 207), (25, 169, 121))]
            gif = folder / "orbit.gif"
            frames[0].save(gif, save_all=True, append_images=frames[1:], duration=200, loop=0)
            entry["files"]["gif"] = pr.file_record(gif, out)
        data_files = []
        if asset_id == "sweep-waist":
            data = folder / "data.csv"
            data.write_text("spacing_mm,waist_mm\n50,1.2\n", encoding="utf-8")
            data_files.append(data)
        caption_files = {}
        for lang, text_value in captions.items():
            path = folder / f"caption.{lang}.txt"
            path.write_text(text_value + "\n", encoding="utf-8")
            caption_files[lang] = pr.portable_path(path, out)
        sources = [pr.Source(id=s, kind="run", path=root / "run", label=spec["sources"][s].get("label"),
                             identity=IDENTITY) for s in entry["sources"]]
        provenance = pr.provenance_record(asset, theme, sources, data_files, out)
        (folder / "provenance.json").write_text(json.dumps(provenance, ensure_ascii=False), encoding="utf-8")
        preview = pr.write_preview(image, out / pr.PREVIEWS_DIR / f"{asset_id}.png")
        entry.update(preview=pr.portable_path(preview, out), size_px=[800, 450],
                     provenance=f"assets/{asset_id}/provenance.json", caption_files=caption_files)
        entries.append(entry)
    manifest = {"schema": pr.MANIFEST_SCHEMA, "schema_version": 1, "kit_version": "0.0.0",
                "generated": "2026-11-10T00:00:00Z", "title": pr.localized(SPEC_TITLE, language),
                "language": language, "theme": theme.summary(), "spec": pr.SPEC_FILE,
                "spec_sha256": pr.sha256_text(pr.spec_text(spec)), "assets": entries,
                "gallery": {"index": "index.html", "contact_sheet": "contact-sheet.png"},
                "counts": {"written": len(entries) - 2, "reused": 0, "missing_input": 1, "failed": 0,
                           "pending": 1}}
    pr.write_manifest(out, manifest)
    # material a bundle must never carry: build cache, unlisted files, a run directory and its log
    (out / ".cache").mkdir()
    (out / ".cache" / "render.bin").write_bytes(b"cache")
    (out / "assets" / "system-view" / "notes.txt").write_text("draft\n", encoding="utf-8")
    (out / "run-local").mkdir()
    (out / "run-local" / "simulation.log").write_text("log\n", encoding="utf-8")
    return out


class _Links(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key in ("href", "src", "poster") and value:
                self.links.append(value)


def page_data(index_path):
    text_value = Path(index_path).read_text(encoding="utf-8")
    match = re.search(r'<script type="application/json" id="gallery-data">(.*?)</script>', text_value, re.S)
    return text_value, json.loads(match.group(1))


def page_links(index_path):
    """Every link of the page: static attributes plus the paths in the embedded data."""
    text_value, data = page_data(index_path)
    parser = _Links()
    parser.feed(text_value)
    links = [link for link in parser.links if not link.startswith("#")]
    links.extend(value for value in data["links"].values() if value)
    for asset in data["assets"]:
        links.extend(item["href"] for item in asset["files"])
        if asset["preview"]:
            links.append(asset["preview"])
        for key in ("src", "poster"):
            if asset["media"] and asset["media"].get(key):
                links.append(asset["media"][key])
    return links


def unresolved(root, links):
    root = Path(root).resolve()
    problems = []
    for link in links:
        target = (root / unquote(link)).resolve()
        if re.match(r"^[a-z][a-z0-9+.-]*:", link, re.I) or not target.is_relative_to(root) or not target.exists():
            problems.append(link)
    return problems


class GalleryPageTests(unittest.TestCase):
    def setUp(self):
        need(self, "PIL.Image")
        self.tmp = Path(tempfile.mkdtemp(prefix="gallery-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def write(self, **options):
        out = make_presentation(self.tmp / "p", **options)
        result = gallery.write_gallery(out)
        self.assertEqual(result["index"], "index.html")
        return out, result

    def test_page_is_offline_and_every_link_resolves(self):
        out, _ = self.write()
        text_value, data = page_data(out / "index.html")
        self.assertNotIn("http://", text_value)
        self.assertNotIn("https://", text_value)
        self.assertNotRegex(text_value, r"<link\b|@import|fetch\(|XMLHttpRequest|src=\"//")
        links = page_links(out / "index.html")
        self.assertIn("assets/system-view/system-view.png", links)
        self.assertIn("presentation.json", links)
        self.assertEqual(unresolved(out, links), [])
        self.assertFalse([pattern for pattern in gallery.PATH_LEAKS if pattern.search(text_value)])
        self.assertTrue(all(asset["id"] for asset in data["assets"]))

    def test_both_languages_and_default_language(self):
        out, _ = self.write(language="zh-CN")
        text_value, data = page_data(out / "index.html")
        self.assertIn('<html lang="zh-CN"', text_value)
        self.assertEqual(data["language"], "zh-CN")
        self.assertEqual(data["languages"], ["en", "zh-CN"])
        self.assertEqual(data["text"]["en"]["files"], "Files")
        self.assertEqual(data["text"]["zh-CN"]["files"], "文件")
        self.assertEqual(data["text"]["zh-CN"]["gallery"], "结果图库")
        view = data["assets"][0]
        self.assertEqual(view["caption"], CAPTIONS["system-view"])
        self.assertEqual(view["title"], TITLES["system-view"])
        self.assertEqual(data["title"], SPEC_TITLE)

    def test_single_language_has_no_switch(self):
        out, _ = self.write(bilingual=False)
        _, data = page_data(out / "index.html")
        self.assertEqual(data["languages"], ["en"])

    def test_details_files_sources_and_settings(self):
        out, _ = self.write()
        _, data = page_data(out / "index.html")
        view = {a["id"]: a for a in data["assets"]}["system-view"]
        paths = [item["path"] for item in view["files"]]
        self.assertEqual(paths, ["assets/system-view/system-view.png", "assets/system-view/system-view.svg",
                                 "assets/system-view/caption.en.txt", "assets/system-view/caption.zh-CN.txt",
                                 "assets/system-view/provenance.json"])
        self.assertEqual(view["files"][1]["label"], {"en": "Vector image (SVG)", "zh-CN": "矢量图（SVG）"})
        self.assertEqual(view["media"], {"type": "image", "src": "assets/system-view/system-view.png"})
        self.assertEqual(view["size"], "800 × 450 px")
        source = view["sources"][0]
        self.assertEqual(source["label"], {"en": "Nominal", "zh-CN": "标称"})
        self.assertIn(["project", IDENTITY["project_hash"][:12]], source["rows"])
        self.assertIn(["result", IDENTITY["result_hash"][:12]], source["rows"])
        self.assertIn(["engine", "os-optics-0.13.0-test"], source["rows"])
        self.assertIn(["catalog", None], source["rows"])
        self.assertIn(["camera", "[55, 22]"], view["settings"])
        sweep = {a["id"]: a for a in data["assets"]}["sweep-waist"]
        self.assertIn("assets/sweep-waist/data.csv", [item["path"] for item in sweep["files"]])
        orbit = {a["id"]: a for a in data["assets"]}["orbit"]
        self.assertEqual(orbit["media"]["src"], "assets/orbit/orbit.gif")

    def test_compose_files_in_panel_order(self):
        from PIL import Image

        out = make_presentation(self.tmp / "p")
        manifest = gallery.read_manifest(out)
        entry = {a["id"]: a for a in manifest["assets"]}["before-after"]
        folder = out / "assets" / "before-after"
        for name in ("panel-10.png", "panel-2.png", "panel-2.svg", "layout.json"):
            if name.endswith(".png"):
                Image.new("RGB", (8, 8)).save(folder / name)
            else:
                (folder / name).write_text("{}" if name.endswith(".json") else "<svg/>", encoding="utf-8")
        entry["files"].update({"panel-10": {"path": "assets/before-after/panel-10.png"},
                               "panel-2-svg": {"path": "assets/before-after/panel-2.svg"},
                               "panel-2": {"path": "assets/before-after/panel-2.png"},
                               "layout": {"path": "assets/before-after/layout.json"}})
        view = gallery.asset_view(out, entry, None)
        self.assertEqual([item["label"]["en"] for item in view["files"]][:5],
                         ["Image (PNG)", "Panel layout (JSON)", "Panel 2 (PNG)", "Panel 2 (SVG)", "Panel 10 (PNG)"])
        captions = [item["label"] for item in view["files"] if "caption" in item["path"]]
        self.assertEqual(captions, [{"en": "Caption, English (TXT)", "zh-CN": "英文图注（TXT）"},
                                    {"en": "Caption, Chinese (TXT)", "zh-CN": "中文图注（TXT）"}])

    def test_pending_asset_is_not_shown_as_broken(self):
        out, _ = self.write()
        text_value, data = page_data(out / "index.html")
        pending = {a["id"]: a for a in data["assets"]}["spot-map"]
        self.assertEqual((pending["status"], pending["built"], pending["error"]), ("pending", False, None))
        self.assertEqual(data["text"]["en"]["status.pending"], "Not built yet")
        self.assertEqual(data["text"]["zh-CN"]["status.pending"], "尚未生成")

    def test_asset_without_image_shows_its_error(self):
        out, _ = self.write()
        _, data = page_data(out / "index.html")
        power = {a["id"]: a for a in data["assets"]}["power"]
        self.assertFalse(power["built"])
        self.assertIsNone(power["preview"])
        self.assertIsNone(power["media"])
        self.assertEqual(power["error"]["code"], "SOURCE_MISSING")
        self.assertIn("relative to presentation.json", power["error"]["recovery"])

    def test_page_follows_theme_background(self):
        for background, expected in (("dark", "dark"), ("transparent", "light"), ("#101010", "dark"),
                                     ("light", "light")):
            with self.subTest(background=background):
                out = make_presentation(self.tmp / background.strip("#"), background=background)
                gallery.write_gallery(out)
                self.assertIn(f'data-theme="{expected}"', (out / "index.html").read_text(encoding="utf-8"))

    def test_missing_asset_index(self):
        with self.assertRaises(pr.PresentationError) as caught:
            gallery.write_gallery(self.tmp)
        self.assertEqual(caught.exception.code, "MANIFEST_NOT_FOUND")


class TextTests(unittest.TestCase):
    def test_both_languages_carry_every_key(self):
        self.assertEqual(set(gallery.GALLERY_TEXT["en"]), set(gallery.GALLERY_TEXT["zh-CN"]))
        for status in gallery.STATUSES:
            self.assertIn(f"status.{status}", gallery.GALLERY_TEXT["en"])
        for value in gallery.GALLERY_TEXT["zh-CN"].values():
            self.assertNotIn("资产", value)

    def test_file_labels_follow_role_and_extension(self):
        cases = {
            ("image", "a/v.png"): ("Image (PNG)", "图像（PNG）"),
            ("svg", "a/v.svg"): ("Vector image (SVG)", "矢量图（SVG）"),
            ("pdf", "a/v.pdf"): ("Vector image (PDF)", "矢量图（PDF）"),
            ("clean", "a/v.clean.png"): ("Image without annotations (PNG)", "无标注图像（PNG）"),
            ("overlay", "a/v.overlay.svg"): ("Editable text layer (SVG)", "可编辑文字层（SVG）"),
            ("annotated", "a/v.annotated.png"): ("Annotated image (PNG)", "带标注图像（PNG）"),
            ("viewer", "a/viewer.html"): ("Interactive 3D view (HTML)", "交互式三维视图（HTML）"),
            ("mp4", "a/o.mp4"): ("Video (MP4)", "视频（MP4）"),
            ("gif", "a/o.gif"): ("Animation (GIF)", "动画（GIF）"),
            ("sequence", "a/sequence.json"): ("Replayable sequence (JSON)", "可重放序列（JSON）"),
            ("data", "a/data.csv"): ("Data (CSV)", "数据（CSV）"),
            ("data", "a/camera.json"): ("Camera settings (JSON)", "相机参数（JSON）"),
            ("camera", "a/camera.json"): ("Camera settings (JSON)", "相机参数（JSON）"),
            ("layout", "a/layout.json"): ("Panel layout (JSON)", "面板布局（JSON）"),
            ("panel-2", "a/panel-2.png"): ("Panel 2 (PNG)", "面板 2（PNG）"),
            ("panel-2-svg", "a/panel-2.svg"): ("Panel 2 (SVG)", "面板 2（SVG）"),
            ("provenance", "a/provenance.json"): ("Provenance record (JSON)", "来源记录（JSON）"),
            ("thumbnail", "a/t.webp"): ("Thumbnail (WEBP)", "Thumbnail（WEBP）"),
            ("depth_map", "a/depth.exr"): ("Depth map (EXR)", "Depth map（EXR）"),
        }
        for (role, path), expected in cases.items():
            with self.subTest(role=role, path=path):
                self.assertEqual((gallery.file_label(role, path, "en"), gallery.file_label(role, path, "zh-CN")),
                                 expected)


class ContactSheetTests(unittest.TestCase):
    def setUp(self):
        need(self, "PIL.Image")
        need(self, "matplotlib")
        self.tmp = Path(tempfile.mkdtemp(prefix="sheet-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def sheet(self, **options):
        from PIL import Image

        out = make_presentation(self.tmp / str(len(list(self.tmp.iterdir()))), **options)
        try:
            result = gallery.write_gallery(out)
        except pr.PresentationError as exc:
            if exc.code == "FONT_MISSING" and not REQUIRE:
                self.skipTest(exc.details.get("recovery", str(exc)))
            raise
        self.assertEqual(result, {"index": "index.html", "contact_sheet": "contact-sheet.png"})
        path = out / "contact-sheet.png"
        self.assertEqual(path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        with Image.open(path) as image:
            return out, image.size, image.convert("RGB")

    def test_sheet_is_a_png_of_reasonable_size(self):
        _, (width, height), image = self.sheet()
        self.assertLessEqual(width, gallery.CONTACT_SHEET_MAX_WIDTH)
        self.assertGreater(height, 300)
        colors = {color for _, color in image.getcolors(maxcolors=1 << 20)}
        for color in COLORS.values():
            self.assertIn(color, colors)  # every built preview is drawn

    def test_many_assets_stay_within_the_width(self):
        _, (width, _), _ = self.sheet(extra_assets=14)
        self.assertLessEqual(width, gallery.CONTACT_SHEET_MAX_WIDTH)

    def test_chinese_and_dark(self):
        _, (width, _), image = self.sheet(language="zh-CN", background="dark")
        self.assertEqual(image.getpixel((2, 2)), (17, 20, 24))
        self.assertLessEqual(width, gallery.CONTACT_SHEET_MAX_WIDTH)

    def test_contact_sheet_checks_glyphs(self):
        out = make_presentation(self.tmp / "glyphs")
        manifest = gallery.read_manifest(out)
        manifest["assets"][0]["id"] = "\U0001d4d0-view"  # a character no installed font draws
        theme = gallery.theme_from_manifest(manifest)
        with self.assertRaises(pr.PresentationError) as caught:
            gallery.write_contact_sheet(out, manifest, theme)
        self.assertEqual(caught.exception.code, "FONT_MISSING")


class BundleTests(unittest.TestCase):
    def setUp(self):
        need(self, "PIL.Image")
        self.tmp = Path(tempfile.mkdtemp(prefix="bundle-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.out = make_presentation(self.tmp / "p")

    def assert_clean(self, root):
        names = sorted(p.relative_to(root).as_posix() for p in Path(root).rglob("*") if p.is_file())
        for name in names:
            self.assertFalse(name.startswith((".cache", "run-local")), name)
            self.assertNotIn("notes.txt", name)
        self.assertEqual(gallery.scan_paths(root), [])
        self.assertEqual(unresolved(root, page_links(Path(root) / "index.html")), [])
        return names

    def test_zip_holds_the_selection_with_its_redraw_dependencies(self):
        result = gallery.write_bundle(self.out, self.tmp / "share" / "talk.zip", only=["before-after"])
        self.assertEqual(result["bundle"], (self.tmp / "share" / "talk.zip").as_posix())
        self.assertEqual((result["index"], result["asset_index"], result["archive"]),
                         ("talk/index.html", "talk/assets.json", "zip"))
        self.assertEqual(result["assets"], ["before-after"])
        with zipfile.ZipFile(self.tmp / "share" / "talk.zip") as archive:
            names = archive.namelist()
            infos = archive.infolist()
        self.assertEqual(names, sorted(names))
        self.assertTrue(all(name.startswith("talk/") for name in names))
        self.assertTrue(all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in infos))
        self.assertEqual(result["files"], len(names))
        self.assertEqual(result["bytes"], (self.tmp / "share" / "talk.zip").stat().st_size)
        moved = self.tmp / "elsewhere" / "deep"
        moved.mkdir(parents=True)
        with zipfile.ZipFile(self.tmp / "share" / "talk.zip") as archive:
            archive.extractall(moved)
        root = moved / "talk"
        names = self.assert_clean(root)
        self.assertNotIn("previews/system-view.png", names)
        self.assertIn("previews/before-after.png", names)
        self.assertIn("contact-sheet.png" if result["contact_sheet"] else "index.html", names)
        manifest = json.loads((root / "assets.json").read_text(encoding="utf-8"))
        self.assertEqual([a["id"] for a in manifest["assets"]], ["before-after"])
        self.assertEqual(manifest["counts"], {"written": 1, "reused": 0, "stale": 0, "missing_input": 0, "failed": 0,
                                              "pending": 0})
        spec = json.loads((root / "presentation.json").read_text(encoding="utf-8"))
        self.assertEqual([a["id"] for a in spec["assets"]], ["system-view", "sweep-waist", "before-after"])
        self.assertEqual(sorted(spec["sources"]), ["l2", "nominal"])
        self.assertEqual(spec["sources"]["nominal"]["path"], "../run")
        self.assertEqual(manifest["spec_sha256"], pr.sha256_text((root / "presentation.json").read_text("utf-8")))
        pr.validate_spec(spec)

    def test_zip_bytes_are_deterministic(self):
        first = gallery.write_bundle(self.out, self.tmp / "a" / "talk.zip", only=["system-view", "orbit"])
        second = gallery.write_bundle(self.out, self.tmp / "b" / "talk.zip", only=["system-view", "orbit"])
        self.assertEqual(first["bytes"], second["bytes"])
        self.assertEqual((self.tmp / "a" / "talk.zip").read_bytes(), (self.tmp / "b" / "talk.zip").read_bytes())

    def test_directory_bundle_defaults_to_built_assets_and_moves(self):
        dest = self.tmp / "share" / "talk"
        result = gallery.write_bundle(self.out, dest)
        self.assertEqual(result["assets"], ["system-view", "sweep-waist", "before-after", "orbit"])
        self.assertEqual((result["index"], result["archive"]), ("index.html", "directory"))
        moved = self.tmp / "moved"
        shutil.move(str(dest), str(moved))
        names = self.assert_clean(moved)
        self.assertIn("assets/sweep-waist/data.csv", names)
        self.assertIn("assets/system-view/provenance.json", names)
        self.assertFalse(any(name.startswith("assets/power") or name == "previews/power.png" for name in names))
        self.assertEqual(result["files"], len(names))
        self.assertEqual(result["bytes"], sum((moved / name).stat().st_size for name in names))

    def test_existing_destination(self):
        dest = self.tmp / "share" / "talk"
        gallery.write_bundle(self.out, dest, only=["system-view"])
        with self.assertRaises(pr.PresentationError) as caught:
            gallery.write_bundle(self.out, dest, only=["orbit"])
        self.assertEqual(caught.exception.code, "OUTPUT_EXISTS")
        gallery.write_bundle(self.out, dest, only=["orbit"], overwrite=True)
        manifest = json.loads((dest / "assets.json").read_text(encoding="utf-8"))
        self.assertEqual([a["id"] for a in manifest["assets"]], ["orbit"])
        self.assertFalse((dest / "assets" / "system-view").exists())
        (dest / "my-notes.txt").write_text("mine\n", encoding="utf-8")  # no longer only a bundle
        with self.assertRaises(pr.PresentationError) as caught:
            gallery.write_bundle(self.out, dest, overwrite=True)
        self.assertEqual(caught.exception.code, "OUTPUT_EXISTS")
        self.assertTrue((dest / "my-notes.txt").is_file())
        other = self.tmp / "work"
        other.mkdir()
        with self.assertRaises(pr.PresentationError) as caught:
            gallery.write_bundle(self.out, other, overwrite=True)
        self.assertEqual(caught.exception.code, "OUTPUT_EXISTS")
        with self.assertRaises(pr.PresentationError) as caught:
            gallery.write_bundle(self.out, self.out.parent, overwrite=True)
        self.assertEqual(caught.exception.code, "OUTPUT_EXISTS")
        zipped = self.tmp / "talk.zip"
        gallery.write_bundle(self.out, zipped, only=["orbit"])
        with self.assertRaises(pr.PresentationError):
            gallery.write_bundle(self.out, zipped, only=["orbit"])
        gallery.write_bundle(self.out, zipped, only=["orbit"], overwrite=True)

    def test_destination_inside_a_saved_source_is_refused(self):
        run = self.out.parent / "run"
        for dest in (run / "share.zip", run / "deep" / "share"):
            with self.subTest(dest=dest.name), self.assertRaises(pr.PresentationError) as caught:
                gallery.write_bundle(self.out, dest, only=["system-view"])
            self.assertEqual(caught.exception.code, "OUTPUT_INVALID")
            self.assertEqual(caught.exception.details["recovery"],
                             "choose a destination outside the saved runs and studies")
            self.assertFalse(dest.exists())
        # sources that do not exist (l2-sweep) are skipped; a sibling of the run is fine
        gallery.write_bundle(self.out, self.out.parent / "l2-sweep" / "share.zip", only=["system-view"])

    def test_every_refusal_has_a_recovery(self):
        attempts = [lambda: gallery.write_bundle(self.out, self.tmp / "x.zip", only=["nope"]),
                    lambda: gallery.write_bundle(self.out, self.tmp / "x", archive="tar"),
                    lambda: gallery.write_bundle(self.out, self.out.parent),
                    lambda: gallery.write_bundle(self.tmp / "missing", self.tmp / "y"),
                    lambda: gallery.write_gallery(self.tmp / "missing")]
        for attempt in attempts:
            with self.assertRaises(pr.PresentationError) as caught:
                attempt()
            self.assertTrue(caught.exception.details.get("recovery"), caught.exception.code)

    def test_unknown_asset(self):
        with self.assertRaises(pr.PresentationError) as caught:
            gallery.write_bundle(self.out, self.tmp / "x.zip", only=["system-view", "nope"])
        self.assertEqual(caught.exception.code, "ASSET_UNKNOWN")
        self.assertIn("system-view", str(caught.exception))
        self.assertEqual(caught.exception.details["known"][:2], ["system-view", "sweep-waist"])
        self.assertFalse((self.tmp / "x.zip").exists())

    def test_absolute_path_in_a_caption_is_refused(self):
        leaked = "D:" + "\\projects\\talk\\run"
        caption = self.out / "assets" / "sweep-waist" / "caption.en.txt"
        caption.write_text(f"Waist radius from {leaked}.\n", encoding="utf-8")
        for dest in (self.tmp / "leak.zip", self.tmp / "leak"):
            with self.subTest(dest=dest.name), self.assertRaises(pr.PresentationError) as caught:
                gallery.write_bundle(self.out, dest)
            self.assertEqual(caught.exception.code, "BUNDLE_PATH_LEAK")
            self.assertIn("assets/sweep-waist/caption.en.txt", str(caught.exception))
            self.assertFalse(dest.exists())
        self.assertEqual([p.name for p in self.tmp.iterdir() if p.name.startswith(".")], [])
        gallery.write_bundle(self.out, self.tmp / "ok.zip", only=["system-view"])  # other assets still bundle

    def test_other_machine_paths_are_found(self):
        for planted in ("/" + "home/someone/run", "/" + "Users/someone/run", "file" + ":///tmp/x", "C:" + "/x"):
            with self.subTest(planted=planted):
                folder = self.tmp / "scan" / str(abs(hash(planted)))
                folder.mkdir(parents=True)
                (folder / "a.json").write_text(json.dumps({"path": planted}), encoding="utf-8")
                self.assertEqual([name for name, _ in gallery.scan_paths(folder)], ["a.json"])
        clean = self.tmp / "scan-clean"
        clean.mkdir()
        (clean / "a.txt").write_text("ratio 1:2, see https://example.org and ../run, 12:30\n", encoding="utf-8")
        self.assertEqual(gallery.scan_paths(clean), [])

    def test_absolute_source_path_is_refused(self):
        spec = json.loads((self.out / "presentation.json").read_text(encoding="utf-8"))
        spec["sources"]["nominal"]["path"] = "/" + "srv/runs/nominal"
        pr.save_spec(spec, self.out / "presentation.json")
        with self.assertRaises(pr.PresentationError) as caught:
            gallery.write_bundle(self.out, self.tmp / "abs.zip", only=["system-view"])
        self.assertEqual(caught.exception.code, "BUNDLE_PATH_LEAK")
        self.assertIn("sources.nominal.path", str(caught.exception))

    def test_listed_file_outside_the_directory_is_refused(self):
        manifest = gallery.read_manifest(self.out)
        manifest["assets"][0]["files"]["pdf"] = {"path": "../run/manifest.json"}
        pr.write_manifest(self.out, manifest)
        with self.assertRaises(pr.PresentationError) as caught:
            gallery.write_bundle(self.out, self.tmp / "out.zip", only=["system-view"])
        self.assertEqual(caught.exception.code, "MANIFEST_INVALID")


class GalleryBrowserTests(unittest.TestCase):
    """Open an extracted bundle from file:// in Chrome with every non-file request blocked."""

    def setUp(self):
        need(self, "PIL.Image")
        need(self, "matplotlib")
        need(self, "playwright.sync_api")
        from os_optics_kit.render_views import find_browser

        self.browser = find_browser(None)
        if not self.browser:
            if REQUIRE:
                self.fail("Chrome/Chromium is not installed")
            self.skipTest("Chrome/Chromium is not installed")
        self.tmp = Path(tempfile.mkdtemp(prefix="gallery-browser-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_extracted_bundle_in_a_browser(self):
        from playwright.sync_api import sync_playwright

        out = make_presentation(self.tmp / "p")
        gallery.write_bundle(out, self.tmp / "talk.zip")
        target = self.tmp / "unpacked"
        with zipfile.ZipFile(self.tmp / "talk.zip") as archive:
            archive.extractall(target)
        index = target / "talk" / "index.html"
        aborted, errors = [], []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=self.browser, headless=True)
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 860})
                page.on("pageerror", lambda exc: errors.append(str(exc)))

                def route(request_route):
                    if request_route.request.url.startswith("file:"):
                        request_route.continue_()
                    else:
                        aborted.append(request_route.request.url)
                        request_route.abort()

                page.route("**/*", route)
                page.goto(index.resolve().as_uri())
                page.wait_for_function("[...document.images].every(img => img.complete)", timeout=15000)
                widths = page.evaluate("[...document.images].map(img => [img.getAttribute('src'), img.naturalWidth])")
                self.assertEqual(len(widths), 4)
                self.assertTrue(all(width > 0 for _, width in widths), widths)
                caption = page.locator('a[data-asset="system-view"] .caption')
                self.assertEqual(caption.inner_text(), CAPTIONS["system-view"]["en"])
                page.click('button[data-lang="zh-CN"]')
                self.assertEqual(caption.inner_text(), CAPTIONS["system-view"]["zh-CN"])
                self.assertEqual(page.evaluate("document.documentElement.lang"), "zh-CN")
                page.click('button[data-lang="en"]')
                page.click('a[data-asset="system-view"]')
                page.wait_for_selector("#viewer:not([hidden])")
                page.wait_for_function("document.getElementById('full-image')?.complete", timeout=10000)
                full = page.evaluate("(() => { const img = document.getElementById('full-image');"
                                     " return [img.getAttribute('src'), img.naturalWidth, img.naturalHeight]; })()")
                self.assertEqual(full, ["assets/system-view/system-view.png", 800, 450])
                hrefs = page.evaluate("[...document.querySelectorAll('#panel a')].map(a => a.getAttribute('href'))")
                self.assertIn("assets/system-view/provenance.json", hrefs)
                self.assertIn("presentation.json", hrefs)
                self.assertEqual(unresolved(index.parent, hrefs), [])
                page.keyboard.press("ArrowRight")
                self.assertEqual(page.inner_text("#viewer-id"), "sweep-waist")
                page.keyboard.press("ArrowLeft")
                self.assertEqual(page.inner_text("#viewer-id"), "system-view")
                page.keyboard.press("Escape")
                page.wait_for_selector("#viewer", state="hidden")
                gallery.write_gallery(out)  # the built directory also lists an asset not built yet
                page.goto((out / "index.html").resolve().as_uri())
                tile = page.locator('a[data-asset="spot-map"] .problem.calm')
                self.assertEqual(tile.locator("strong").inner_text(), "Not built yet")
                self.assertEqual(page.locator('a[data-asset="power"] .problem.calm').count(), 0)
            finally:
                browser.close()
        self.assertEqual(aborted, [])
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
