"""present: target comparison plots and multi-panel compositions (os_optics_kit.compose_figures).

Offline tests use synthetic data; engine tests build three small catalog-free runs inside the test and a
presentation of them. Matplotlib, engine and browser tests skip when those are missing, unless
OS_OPTICS_KIT_REQUIRE_PRESENTATION=1 (then a missing dependency fails instead of skipping).
Run from the repository root: python -m unittest tests.test_present_compose -v
"""

from __future__ import annotations

import contextlib
import copy
from importlib.util import find_spec
import io
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock

from os_optics_kit import compose_figures as cf
from os_optics_kit import presentation as pres
from os_optics_kit import present
from os_optics_kit import workflow_common as wc

REQUIRE = os.environ.get("OS_OPTICS_KIT_REQUIRE_PRESENTATION") == "1"
HAS_ENGINE = find_spec("os_optics") is not None
HAS_MATPLOTLIB = find_spec("matplotlib") is not None
ABSOLUTE = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]|/home/|/Users/|file://")


def needs(available, reason):
    """Skip without the dependency, or fail when the environment requires presentation coverage."""
    if available or REQUIRE:
        return lambda item: item
    return unittest.skip(reason)


def has_browser():
    if find_spec("playwright") is None:
        return False
    from os_optics_kit.render_views import find_browser

    return find_browser(None) is not None


requires_plot = needs(HAS_MATPLOTLIB, 'needs "os-optics-kit[plot]"')
requires_engine_plot = needs(HAS_MATPLOTLIB and HAS_ENGINE, 'needs os-optics and "os-optics-kit[plot]"')


def tree_digest(path):
    """sha256 of every file under ``path`` (names and bytes)."""
    return {p.relative_to(path).as_posix(): wc.sha256_file(p) for p in sorted(Path(path).rglob("*")) if p.is_file()}


def chinese_fonts_missing(theme):
    return bool(pres.missing_glyphs(["目标值调整前后缺失"], theme.font_family))


# --------------------------------------------------------------------------------------------- synthetic data

def entry(source, state, value=None, unit="mm", assessment="MET", bounds=(1.9, 2.1), delta=None):
    outcome = {"MET": "PASS", "UNMET": "FAIL", "INDETERMINATE": "UNKNOWN"}.get(assessment, "UNKNOWN")
    item = {"source": source, "state": state, "outcome": "MISSING" if state == "missing" else outcome,
            "assessment": None if state == "missing" else assessment, "status": None, "quantity_state": None,
            "value": value, "unit": None if state == "missing" else unit, "reference": False,
            "bounds": None if state == "missing" or bounds is None else {"minimum": bounds[0], "maximum": bounds[1]}}
    if delta is not None:
        item["delta"] = delta
    return item


def targets_document(theme, unit_b="mm"):
    """Three runs, two targets: run c lacks target 'waist', run b reports 'spot' bounded."""
    runs = [{"source": s, "label": {"en": e, "zh-CN": z}, "color_index": i, "outcome": "PASS"}
            for i, (s, e, z) in enumerate((("a", "Nominal", "标称"), ("b", "Swapped L2", "更换 L2"),
                                           ("c", "Final", "最终")))]
    panels = [{"target": "waist", "metric": "waist_radius_x_mm", "unit": "mm", "split_by_unit": False,
               "entries": [entry("a", "exact", 2.0), entry("b", "exact", 1.44, assessment="UNMET"),
                           entry("c", "missing")]},
              {"target": "spot", "metric": "beam_radius_mm", "unit": unit_b, "split_by_unit": False,
               "entries": [entry("a", "exact", 0.015, unit=unit_b, bounds=(0, 0.02)),
                           entry("b", "bounded", 0.018, unit=unit_b, assessment="INDETERMINATE", bounds=(0, 0.02)),
                           entry("c", "no_value", None, unit=unit_b, assessment="INDETERMINATE", bounds=(0, 0.02))]}]
    data = {"schema": cf.TARGETS_SCHEMA, "schema_version": 1, "runs": runs, "reference": None,
            "targets": ["waist", "spot"], "panels": panels}
    return cf.add_display_values(data, theme)


def raster(path, size, color=(40, 120, 200)):
    from PIL import Image

    image = Image.new("RGB", size, (255, 255, 255))
    for x in range(size[0] // 4, 3 * size[0] // 4):
        for y in range(size[1] // 3, 2 * size[1] // 3):
            image.putpixel((x, y), color)
    image.save(path)
    return Path(path)


class TextTests(unittest.TestCase):
    def test_every_text_has_both_languages(self):
        self.assertEqual(set(cf.TEXT["en"]), set(cf.TEXT["zh-CN"]))
        for language, table in cf.TEXT.items():
            for key, value in table.items():
                self.assertTrue(value.strip(), f"{language} {key}")
                self.assertIsNone(ABSOLUTE.search(value), key)
        self.assertEqual(cf.text("compose.before", "zh-CN"), "调整前")
        self.assertEqual(cf.text("compose.after", "en"), "After")
        self.assertEqual(cf.text("title.targets", "zh-CN"), "目标值")  # falls back to presentation.TEXT

    def test_join_names(self):
        self.assertEqual(cf.join_names(["A", "B", "C"], "en"), "A, B and C")
        self.assertEqual(cf.join_names(["A", "B", "C"], "zh-CN"), "A、B和C")
        self.assertEqual(cf.join_names(["A"], "en"), "A")

    def test_settings_document_fields(self):
        settings = cf.settings()
        self.assertIn("targets", settings["plot"])
        self.assertEqual(set(settings["compose"]["layout"]), set(cf.LAYOUTS))
        self.assertIn("framing", settings["compose"]["shared"])

    def test_targets_caption_states_facts(self):
        theme = pres.resolve_theme({})
        data = targets_document(theme)
        caption = cf.targets_caption(data, "en", lambda lang: pres.resolve_theme({"language": lang}))
        self.assertIn("waist and spot of Nominal, Swapped L2 and Final with the target bounds.", caption)
        self.assertIn("waist: Nominal is within bounds; Swapped L2 is outside the bounds; missing for Final.",
                      caption)
        self.assertIn("Swapped L2 has no exact value", caption)
        zh = cf.targets_caption(data, "zh-CN", lambda lang: pres.resolve_theme({"language": lang}))
        self.assertIn("最终缺失", zh)
        self.assertIsNone(ABSOLUTE.search(caption + zh))


class ScaleTests(unittest.TestCase):
    def axes(self, *items):
        return [cf.ValueAxis(panel, quantity, unit, values) for panel, quantity, unit, values in items]

    def test_units_differ_keep_own_scales(self):
        axes = self.axes((1, "target:waist", "mm", [1.0, 2.0]), (2, "target:waist", "um", [1500.0]))
        for requested in ("auto", True, [0, 3]):
            decision = cf.decide_scale(requested, axes, what="y")
            self.assertFalse(decision["applied"])
            self.assertEqual(decision["reason"], "UNITS_DIFFER")
            self.assertEqual(decision["units"], {"1": "mm", "2": "um"})

    def test_shared_axis_decision(self):
        same = self.axes((1, "target:waist", "mm", [1.0, 2.0]), (2, "target:waist", "mm", [1.5, 2.5]))
        decision = cf.decide_scale("auto", same, what="y")
        self.assertEqual((decision["applied"], decision["limits"], decision["unit"]), (True, [1.0, 2.5], "mm"))
        mixed = self.axes((1, "target:waist", "mm", [1.0]), (2, "target:spot", "mm", [0.01]))
        self.assertEqual(cf.decide_scale("auto", mixed, what="y")["reason"], "QUANTITIES_DIFFER")
        self.assertEqual(cf.decide_scale(True, mixed, what="y")["limits"], [0.01, 1.0])
        self.assertEqual(cf.decide_scale([0, 5], mixed, what="y")["limits"], [0.0, 5.0])
        self.assertEqual(cf.decide_scale(False, same, what="y")["reason"], "OFF")
        self.assertEqual(cf.decide_scale("auto", [], what="color")["reason"], "NO_REDRAWN_PLOTS")
        with self.assertRaises(pres.PresentationError) as caught:
            cf.decide_scale([3, 1], same, what="y")
        self.assertEqual(caught.exception.code, "SPEC_INVALID")


class GeometryTests(unittest.TestCase):
    def panel(self, index, mode="image", size=(1920, 1080), span=1):
        return cf.Panel(index=index, asset_id=f"p{index}", entry={"kind": "view" if mode == "image" else "plot"},
                        definition={}, mode=mode, labels={"en": f"P{index}", "zh-CN": f"P{index}"},
                        image_size=size if mode == "image" else None, span=span)

    def test_layouts(self):
        views = [self.panel(i) for i in (1, 2, 3)]
        row = cf.compose_geometry("row", views, size=(1920, 1080))
        # views only: two empty rows centre the fitted row vertically
        self.assertEqual((row.rows, row.columns), (3, 3))
        self.assertEqual(row.cells, [(1, 0, 1), (1, 1, 2), (1, 2, 3)])
        self.assertAlmostEqual(row.gap_px, 0.03 * 1920)
        system = cf.compose_geometry("system-plot", [self.panel(1, "targets"), self.panel(2)], size=(1920, 1080))
        self.assertGreater(system.width_ratios[1], system.width_ratios[0])  # the view is wider
        grid = cf.compose_geometry("grid", [*views, self.panel(4, "targets", span=3)], columns=3,
                                   size=(1920, 1080), header_px=60)
        self.assertEqual(grid.cells[3], (1, 0, 3))
        self.assertEqual(grid.rows, 2)
        self.assertLess(grid.height_ratios[0], grid.height_ratios[1])  # views fitted, the plot takes the rest
        before = cf.compose_geometry("before-after", views[:2], size=(1920, 1080))
        self.assertGreaterEqual(before.gap_px, cf.ARROW_SPACING * 1920 - 1e-9)
        column = cf.compose_geometry("column", [self.panel(1, "targets"), self.panel(2, "sweep")])
        self.assertEqual(column.cells, [(0, 0, 1), (1, 0, 1)])

    def test_invalid_compose_settings(self):
        for asset in ({"id": "x", "layout": "diagonal", "panels": [{"asset": "a"}]},
                      {"id": "x", "layout": "before-after", "panels": [{"asset": "a"}]},
                      {"id": "x", "layout": "row", "panels": [{"asset": "a"}], "spacing": 0.5},
                      {"id": "x", "layout": "grid", "panels": [{"asset": "a"}], "columns": 0},
                      {"id": "x", "layout": "row", "panels": [{"asset": "a"}], "shared": {"z": True}}):
            with self.assertRaises(pres.PresentationError, msg=asset) as caught:
                cf._check_compose(asset)
            self.assertEqual(caught.exception.code, "SPEC_INVALID")


@requires_plot
class DrawingTests(unittest.TestCase):
    def draw(self, theme, data, **kwargs):
        with cf.drawing(theme) as plt:
            fig = cf.new_figure(plt, theme)
            info = cf.draw_targets(fig, data, theme, **kwargs)
            cf.frame(fig, theme, theme.text("title.targets"), "")
            return fig, info

    def test_missing_target_is_marked_not_drawn_as_zero(self):
        from matplotlib.collections import PathCollection
        import matplotlib.pyplot as plt

        theme = pres.resolve_theme({})
        data = targets_document(theme)
        fig, info = self.draw(theme, data)
        try:
            waist_ax = fig.get_axes()[0]
            points = [tuple(offset) for c in waist_ax.collections if isinstance(c, PathCollection)
                      for offset in c.get_offsets()]
            self.assertEqual(sorted(points), [(0.0, 2.0), (1.0, 1.44)])  # no point for the missing run
            low, high = waist_ax.get_ylim()
            self.assertGreater(low, 0.5)  # nothing pulls the value axis towards zero
            texts = [t.get_text() for t in waist_ax.texts]
            self.assertIn("missing", texts)
            self.assertEqual(info[0]["states"], {"a": "exact", "b": "exact", "c": "missing"})
            spot_texts = [t.get_text() for t in fig.get_axes()[1].texts]
            self.assertTrue(any("bounded" in t for t in spot_texts))
            self.assertTrue(any(t.startswith("no value") for t in spot_texts))
            self.assertEqual(cf.text_overflow(fig), [])
        finally:
            plt.close(fig)

    def test_shared_limits_and_display_units(self):
        import matplotlib.pyplot as plt

        theme = pres.resolve_theme({"units": {"mm": "um"}})
        data = targets_document(theme)
        self.assertEqual(data["panels"][0]["display_unit"], "um")
        self.assertAlmostEqual(data["panels"][0]["entries"][0]["display_value"], 2000.0)
        fig, info = self.draw(theme, data, y_limits=[0.0, 2500.0])
        try:
            low, high = info[0]["y_limits"]
            self.assertLess(low, 0.0)
            self.assertGreater(high, 2500.0)
            self.assertEqual(fig.get_axes()[0].get_ylabel(), "value [um]")
        finally:
            plt.close(fig)

    def test_chinese_labels_pass_the_glyph_check_and_fit(self):
        import matplotlib.pyplot as plt
        from matplotlib.text import Text

        theme = pres.resolve_theme({"language": "zh-CN", "background": "dark", "size": "4k"})
        if chinese_fonts_missing(theme) and not REQUIRE:
            self.skipTest("no installed font covers Chinese")
        fig, _info = self.draw(theme, targets_document(theme))
        try:
            strings = [t.get_text() for t in fig.findobj(Text)]
            self.assertIn("缺失", strings)
            pres.check_glyphs(strings, theme)
            self.assertEqual(cf.text_overflow(fig), [])
        finally:
            plt.close(fig)


@requires_plot
class SyntheticComposeTests(unittest.TestCase):
    """build_compose on a build context with hand-made panel entries (no engine, no browser)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.spec = pres.default_spec()
        self.spec["theme"] = {"preset": "presentation"}
        self.spec["sources"] = {}
        self.spec["assets"] = [{"id": "view-a", "kind": "view"}, {"id": "view-b", "kind": "view"},
                               {"id": "targets", "kind": "plot", "type": "targets"},
                               {"id": "targets-um", "kind": "plot", "type": "targets"}]
        self.ctx = present.BuildContext(self.spec, self.dir, self.dir)
        theme = self.ctx.theme
        for name, box in (("view-a", [[0, 0, 0], [10, 1, 1]]), ("view-b", [[0, 0, 0], [10, 1, 1]])):
            folder = self.ctx.asset_dir(name)
            image = raster(folder / f"{name}.png", (960, 540))
            self.ctx._built[name] = {"id": name, "kind": "view", "status": "written", "title": name,
                                     "sources": [], "details": {"box_mm": box, "camera": [55, 22, 0]},
                                     "_abs": {"image": image}}
        for name, unit in (("targets", "mm"), ("targets-um", "um")):
            data = targets_document(theme, unit_b=unit)
            if unit == "um":
                data["panels"] = data["panels"][1:]
                data["targets"] = ["spot"]
            folder = self.ctx.asset_dir(name)
            data_file = wc.write_json(folder / "data.json", data)
            self.ctx._built[name] = {"id": name, "kind": "plot", "status": "written", "title": "Target values",
                                     "sources": ["a", "b", "c"], "details": {}, "_abs": {"data": data_file}}

    def tearDown(self):
        self._tmp.cleanup()

    def build(self, **asset):
        asset = {"id": "combo", "kind": "compose", **asset}
        return asset, cf.build_compose(asset, self.ctx)

    def test_before_after_files_layout_and_labels(self):
        _asset, result = self.build(layout="before-after", panels=[{"asset": "view-a"}, {"asset": "view-b"}])
        out = self.dir / "assets" / "combo"
        self.assertEqual(pres.image_size(result["files"]["image"]), [1920, 1080])
        self.assertIn("svg", result["files"])
        layout = wc.read_json(out / "layout.json")
        self.assertEqual([p["asset"] for p in layout["panels"]], ["view-a", "view-b"])
        self.assertEqual(layout["panels"][0]["label"]["en"], "Before: view-a")
        self.assertEqual(layout["panels"][1]["label"]["zh-CN"], "调整后：view-b")
        self.assertEqual(result["details"]["framing"], "shared")
        self.assertTrue(result["details"]["text_fits"])
        self.assertEqual(result["depends_on"], ["view-a", "view-b"])
        for record in layout["panels"]:  # a panel file has the pixel size of its place in the composition
            size = pres.image_size(out / record["files"]["png"])
            self.assertEqual(size, record["content_px"][2:])
            self.assertIsNotNone(record["crop_px"])  # uniform margins trimmed, one crop for both views
        self.assertEqual(layout["panels"][0]["crop_px"], layout["panels"][1]["crop_px"])
        self.assertIn("Before: view-a; after: view-b.", result["caption"]["en"])
        for path in out.rglob("*.json"):
            self.assertIsNone(ABSOLUTE.search(path.read_text(encoding="utf-8")), path.name)

    def test_reorder_relabel_restyle_without_new_data(self):
        _asset, first = self.build(layout="grid", columns=2, panels=[
            {"asset": "view-a"}, {"asset": "view-b"}, {"asset": "targets", "span": 2}])
        _asset, second = self.build(layout="grid", columns=2, panel_letters=False, spacing=0.06, panels=[
            {"asset": "targets", "span": 2}, {"asset": "view-b", "label": {"en": "After", "zh-CN": "调整后"}},
            {"asset": "view-a", "label": "Before"}])
        layout = wc.read_json(self.dir / "assets" / "combo" / "layout.json")
        self.assertEqual([p["asset"] for p in layout["panels"]], ["targets", "view-b", "view-a"])
        self.assertEqual([p["label"]["en"] for p in layout["panels"]], ["Target values", "After", "Before"])
        self.assertEqual(layout["panels"][1]["label"]["zh-CN"], "调整后")
        self.assertIsNone(layout["panels"][0]["letter"])
        self.assertEqual(layout["spacing"], 0.06)
        self.assertEqual(layout["panels"][0]["mode"], "targets")
        self.assertIn("panel-1-svg", second["files"])  # the re-drawn plot is also vector

    def test_shared_scale_needs_equal_units(self):
        _asset, result = self.build(layout="row", shared={"y": True},
                                    panels=[{"asset": "targets"}, {"asset": "targets-um"}])
        scales = result["details"]["scales"]["y"]
        self.assertFalse(scales["applied"])
        self.assertEqual(scales["reason"], "UNITS_DIFFER")
        _asset, result = self.build(layout="row", shared={"y": [0, 3]},
                                    panels=[{"asset": "targets"}])
        self.assertTrue(result["details"]["scales"]["y"]["applied"])
        layout = wc.read_json(self.dir / "assets" / "combo" / "layout.json")
        for low, high in layout["panels"][0]["y_limits"]:
            self.assertLess(low, 0)
            self.assertGreater(high, 3)

    def test_views_with_different_boxes_report_independent_framing(self):
        self.ctx._built["view-b"]["details"]["box_mm"] = [[0, 0, 0], [20, 1, 1]]
        self.ctx._built["view-b"]["sources"] = ["b"]
        _asset, result = self.build(layout="row", panels=[{"asset": "view-a"}, {"asset": "view-b"}])
        self.assertEqual(result["details"]["framing"], "independent")
        self.assertIn("framing_sources", result["details"]["framing_detail"]["recovery"])
        self.assertNotIn("share one camera and framing", result["caption"]["en"])

    def test_report_preset_writes_pdf_with_caption_in_figure(self):
        self.spec["theme"] = {"preset": "report"}
        self.ctx = self._rebind()
        _asset, result = self.build(layout="row", panels=[{"asset": "view-a"}, {"asset": "targets"}])
        self.assertIn("pdf", result["files"])
        self.assertEqual(pres.image_size(result["files"]["image"]), list(pres.SIZES["report"]))
        self.assertTrue(result["details"]["text_fits"])
        svg = result["files"]["svg"].read_text(encoding="utf-8")
        self.assertIn("<image", svg)  # the raster panel is embedded
        self.assertIn("side by side", svg)  # captions "figure": the caption is text in the figure

    def test_chinese_dark_4k(self):
        self.spec["theme"] = {"preset": "presentation", "language": "zh-CN", "background": "dark", "size": "4k"}
        self.ctx = self._rebind()
        if chinese_fonts_missing(self.ctx.theme) and not REQUIRE:
            self.skipTest("no installed font covers Chinese")
        _asset, result = self.build(layout="system-plot", panels=[{"asset": "view-a"}, {"asset": "targets"}])
        self.assertEqual(pres.image_size(result["files"]["image"]), [3840, 2160])
        self.assertTrue(result["details"]["text_fits"])
        self.assertEqual(result["details"]["panels"][1]["label"], "目标值")

    def _rebind(self):
        ctx = present.BuildContext(self.spec, self.dir, self.dir)
        ctx._built = self.ctx._built
        return ctx


# --------------------------------------------------------------------------------------------- engine

@requires_engine_plot
class PresentBuildTests(unittest.TestCase):
    """Three saved runs made in the test, a presentation with target plots and compositions, rebuilt."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("OPENSCI_CATALOG_CHECK_INTERVAL", "0")
        from os_optics_kit import edit_project as ep
        from tests.workflow_fixtures import by_id, expander

        cls._tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls._tmp.name)
        base = expander()
        detuned = expander(f2=160.0)
        detuned["targets"] = copy.deepcopy(base["targets"])  # same targets: expanded_radius now outside
        far = copy.deepcopy(by_id(base["observations"], "observation_id", "focus"))
        far.update({"observation_id": "far", "pose": {**far["pose"], "position_mm": [450.0, 0.0, 0.0]}})
        extended = ep.apply_edits(base, [
            {"op": "add_observation", "observation": far},
            {"op": "add_target", "target": {"target_id": "far_radius", "metric": "beam_radius_mm",
                                            "observation_id": "far", "unit": "mm", "maximum": 5,
                                            "parameters": []}}])["project"]
        cls.runs = {}
        for name, document in (("nominal", base), ("detuned", detuned), ("extended", extended)):
            record = wc.run_sample(document, cls.dir / "runs" / name)
            assert record["run_dir"], record
            cls.runs[name] = cls.dir / "runs" / name / "run"
        cls.spec_dir = cls.dir / "talk"
        cls.spec_dir.mkdir()
        spec = pres.default_spec()
        spec["theme"] = {"preset": "presentation", "formats": ["png", "svg"]}
        spec["sources"] = {name: {"path": f"../runs/{name}/run", "label": {"en": name.title(), "zh-CN": zh}}
                           for name, zh in (("nominal", "标称"), ("detuned", "失调"), ("extended", "扩展"))}
        spec["assets"] = [
            {"id": "targets", "kind": "plot", "type": "targets", "sources": ["detuned", "nominal", "extended"],
             "reference": "nominal"},
            {"id": "far", "kind": "plot", "type": "targets", "sources": ["nominal", "extended"],
             "targets": ["far_radius"]},
            {"id": "power-nominal", "kind": "plot", "type": "power", "source": "nominal"},
            {"id": "result-pair", "kind": "compose", "layout": "row",
             "panels": [{"asset": "targets"}, {"asset": "power-nominal"}]}]
        cls.browser = has_browser()
        if cls.browser:
            for name in ("detuned", "nominal"):
                spec["assets"].append({"id": f"view-{name}", "kind": "view", "source": name, "camera": [55, 22],
                                       "framing_sources": ["detuned", "nominal"]})
            spec["assets"] += [
                {"id": "before-after", "kind": "compose", "layout": "before-after",
                 "panels": [{"asset": "view-detuned"}, {"asset": "view-nominal"}]},
                {"id": "system-plot", "kind": "compose", "layout": "system-plot",
                 "panels": [{"asset": "view-nominal"}, {"asset": "targets"}]}]
        pres.save_spec(spec, cls.spec_dir / pres.SPEC_FILE)
        cls.before = {name: tree_digest(path) for name, path in cls.runs.items()}
        with mock.patch("os_optics.local.run_optical_project", side_effect=AssertionError("solver called")):
            cls.result = cls.build_spec()

    @classmethod
    def build_spec(cls, *settings):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            return present.build(cls.spec_dir / pres.SPEC_FILE, settings=settings, gallery=False, quiet=True)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def entry(self, asset_id):
        manifest = pres.read_manifest(self.spec_dir)
        return next(e for e in manifest["assets"] if e["id"] == asset_id)

    def test_build_completed_without_solver_and_sources_unchanged(self):
        self.assertEqual(self.result["status"], "COMPLETED", self.result.get("missing"))
        for name, path in self.runs.items():
            self.assertEqual(tree_digest(path), self.before[name], name)
        expected = {"targets", "far", "power-nominal", "result-pair"} | (
            {"view-detuned", "view-nominal", "before-after", "system-plot"} if self.browser else set())
        self.assertEqual({e["id"] for e in self.result["assets"]}, expected)

    def test_target_values_and_units_match_compare(self):
        from os_optics_kit import compare_runs as cr

        data = wc.read_json(self.spec_dir / "assets" / "targets" / "data.json")
        document = cr.compare([self.runs[n] for n in ("detuned", "nominal", "extended")],
                              labels=["detuned", "nominal", "extended"], reference="nominal")
        rows = {row["id"]: row for row in document["targets"]}
        self.assertEqual(data["targets"], list(rows))
        for panel in data["panels"]:
            row = rows[panel["target"]]
            self.assertEqual(panel["unit"], row["unit"])
            for item in panel["entries"]:
                cell = row["runs"][item["source"]]
                if cell.get("missing"):
                    self.assertEqual(item["state"], "missing")
                    self.assertIsNone(item["value"])
                    continue
                self.assertEqual(item["value"], cell["value"])
                self.assertEqual(item["unit"], cell["unit"])
                self.assertEqual(item["assessment"], cell["assessment"])
                if "delta" in cell and item["source"] != "nominal":
                    self.assertAlmostEqual(item["delta"], cell["delta"])
                else:
                    self.assertNotIn("delta", item)
        waist = next(p for p in data["panels"] if p["target"] == "expanded_radius")
        states = {e["source"]: e["assessment"] for e in waist["entries"]}
        self.assertEqual(states["detuned"], "UNMET")
        self.assertEqual(states["nominal"], "MET")
        self.assertIsNone(ABSOLUTE.search((self.spec_dir / "assets" / "targets" / "data.json").read_text("utf-8")))

    def test_missing_target_is_preserved(self):
        entry = self.entry("far")
        self.assertEqual(entry["details"]["missing"], [{"target": "far_radius", "source": "nominal"}])
        self.assertIsNone(entry["details"]["panels"][0]["values"]["nominal"])
        self.assertIn("missing for Nominal", entry["captions"]["en"])
        self.assertIn("标称缺失", entry["captions"]["zh-CN"])

    def test_composition_of_redrawn_plots(self):
        entry = self.entry("result-pair")
        self.assertEqual([p["mode"] for p in entry["details"]["panels"]], ["targets", "power"])
        for role in ("image", "svg", "layout", "panel-1", "panel-1-svg", "panel-2", "data"):
            self.assertIn(role, entry["files"])
        self.assertEqual(entry["depends_on"], ["targets", "power-nominal"])
        self.assertTrue(entry["details"]["text_fits"])

    @needs(has_browser(), "needs Chrome/Chromium and playwright")
    def test_views_before_after_and_system_plot(self):
        before_after = self.entry("before-after")
        self.assertEqual(before_after["details"]["framing"], "shared")
        layout = wc.read_json(self.spec_dir / before_after["files"]["layout"]["path"])
        self.assertEqual([p["label"]["en"] for p in layout["panels"]], ["Before: Detuned", "After: Nominal"])
        system = self.entry("system-plot")
        self.assertEqual([p["mode"] for p in system["details"]["panels"]], ["image", "targets"])
        cells = [p["cell_px"] for p in system["details"]["panels"]]
        self.assertGreater(cells[0][2], cells[1][2])  # the view is wider

    def test_relabel_reorder_rebuild_reuses_the_rest(self):
        spec = pres.load_spec(self.spec_dir / pres.SPEC_FILE)
        compose = pres.find_asset(spec, "result-pair")
        compose["panels"] = [{"asset": "power-nominal", "label": {"en": "Power", "zh-CN": "功率"}},
                             {"asset": "targets"}]
        pres.save_spec(spec, self.spec_dir / pres.SPEC_FILE)
        try:
            with mock.patch("os_optics.local.run_optical_project", side_effect=AssertionError("solver called")):
                result = self.build_spec("theme.language=zh-CN")
            statuses = {a["id"]: a["status"] for a in result["assets"]}
            self.assertEqual(statuses["result-pair"], "written")
            layout = wc.read_json(self.spec_dir / "assets" / "result-pair" / "layout.json")
            self.assertEqual([p["asset"] for p in layout["panels"]], ["power-nominal", "targets"])
            self.assertEqual([p["label"]["zh-CN"] for p in layout["panels"]], ["功率", "目标值"])
            self.assertEqual(layout["language"], "zh-CN")
            for name, path in self.runs.items():
                self.assertEqual(tree_digest(path), self.before[name], name)
        finally:
            compose["panels"] = [{"asset": "targets"}, {"asset": "power-nominal"}]
            spec["theme"].pop("language", None)
            pres.save_spec(spec, self.spec_dir / pres.SPEC_FILE)

    def test_unknown_target_is_a_missing_input(self):
        spec = json.loads((self.spec_dir / pres.SPEC_FILE).read_text("utf-8"))
        spec["assets"].append({"id": "bad", "kind": "plot", "type": "targets", "sources": ["nominal"],
                               "targets": ["no_such_target"]})
        bad_dir = self.dir / "bad"
        bad_dir.mkdir(exist_ok=True)
        pres.save_spec(spec, bad_dir / pres.SPEC_FILE)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            result = present.build(bad_dir / pres.SPEC_FILE, gallery=False, quiet=True, only=["bad"])
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertEqual(result["missing"][0]["code"], "TARGET_UNKNOWN")
        self.assertIn("recovery", result["missing"][0])


if __name__ == "__main__":
    unittest.main()
