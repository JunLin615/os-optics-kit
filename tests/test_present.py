"""present: presentation settings, the redraw specification, themed plots and the build of saved results.

Engine, matplotlib and browser tests skip when those are missing, unless OS_OPTICS_KIT_REQUIRE_PRESENTATION=1
(the published-engine CI job sets it, so a missing optional dependency fails there instead of skipping).
"""

from __future__ import annotations

import contextlib
from importlib.util import find_spec
import io
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock

from os_optics_kit import presentation as pres
from os_optics_kit import present

ROOT = Path(__file__).resolve().parents[1]
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


requires_engine = needs(HAS_ENGINE, "os-optics is not installed")
requires_plot = needs(HAS_MATPLOTLIB and HAS_ENGINE, 'needs os-optics and "os-optics-kit[plot]"')


def call(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = present.main(argv)
    return code, json.loads(out.getvalue()) if out.getvalue().strip() else None, err.getvalue()


def tree_digest(path):
    """sha256 of every file under ``path`` (names and bytes)."""
    from os_optics_kit import workflow_common as wc

    return {p.relative_to(path).as_posix(): wc.sha256_file(p) for p in sorted(Path(path).rglob("*")) if p.is_file()}


class ThemeTests(unittest.TestCase):
    def test_presets_and_overrides(self):
        theme = pres.resolve_theme({"preset": "presentation"}, {"language": "zh-CN", "background": "dark",
                                                                "size": "4k", "font_scale": 1.5})
        self.assertEqual(theme.size, (3840, 2160))
        figsize, dpi = theme.canvas()
        self.assertAlmostEqual(figsize[1], pres.CANVAS_HEIGHT_IN)
        self.assertAlmostEqual(dpi * figsize[0], 3840)
        self.assertEqual(theme.font_pt(), 21.0)
        self.assertEqual(theme.render_background(), "#111418")
        report = pres.resolve_theme({"preset": "report"})
        self.assertTrue(report.provenance_in_figure)
        self.assertIn("pdf", report.formats)

    def test_invalid_settings_name_the_key_and_a_recovery(self):
        cases = {"SETTING_UNKNOWN": {"colour": "red"}, "SETTING_INVALID": {"language": "fr"}}
        for code, theme in cases.items():
            with self.assertRaises(pres.PresentationError) as caught:
                pres.resolve_theme(theme)
            self.assertEqual(caught.exception.code, code)
        for bad in ({"size": "10x10"}, {"font_scale": 9}, {"formats": ["svg"]}, {"units": {"mm": "W"}},
                    {"background": "blue"}, {"palette": []}, {"title": "no"}):
            with self.assertRaises(pres.wc.WorkflowError, msg=bad):
                pres.resolve_theme(bad)

    def test_units_and_labels(self):
        theme = pres.resolve_theme({"units": {"mm": "um"}, "language": "zh-CN",
                                    "labels": {"target:spot": {"en": "Spot radius", "zh-CN": "光斑半径"}}})
        self.assertAlmostEqual(theme.to_display(0.0165, "mm"), 16.5)
        self.assertEqual(theme.axis_label("target:spot", "mm"), "光斑半径 [µm]")
        self.assertEqual(theme.label("param:gap"), "gap")
        self.assertIsNone(theme.to_display(None, "mm"))

    def test_every_text_has_both_languages(self):
        self.assertEqual(set(pres.TEXT["en"]), set(pres.TEXT["zh-CN"]))
        for language, table in pres.TEXT.items():
            for key, value in table.items():
                self.assertTrue(value.strip(), f"{language} {key}")
                self.assertIsNone(ABSOLUTE.search(value), key)

    @needs(HAS_MATPLOTLIB, 'needs "os-optics-kit[plot]"')
    def test_glyph_check_refuses_text_the_fonts_cannot_draw(self):
        theme = pres.resolve_theme({"font_family": ["DejaVu Sans"]})
        theme.font_family = ["DejaVu Sans"]  # no fallback: Chinese cannot be drawn
        with self.assertRaises(pres.PresentationError) as caught:
            pres.check_glyphs(["光斑半径"], theme)
        self.assertEqual(caught.exception.code, "FONT_MISSING")
        self.assertIn("recovery", caught.exception.as_dict())
        pres.check_glyphs(["spot radius [mm]"], theme)


class TextAndCameraTests(unittest.TestCase):
    def test_chinese_spacing(self):
        self.assertEqual(pres.tidy("光斑半径 随 透镜位移 的变化", "zh-CN"), "光斑半径随透镜位移的变化")
        self.assertEqual(pres.tidy("Original与L2 sweep", "zh-CN"), "Original 与 L2 sweep")
        self.assertEqual(pres.tidy("spot 随 gap", "en"), "spot 随 gap")
        self.assertEqual(pres.text("legend.count", "zh-CN", label="PASS", count=5), "PASS (5)")

    def test_views_show_light_travelling_left_to_right(self):
        from os_optics_kit import render_views as rv

        for axis in ([1, 0, 0], [0, 1, 0], [-1, 0, 0], [0.6, 0.8, 0]):
            for azimuth, elevation in ((86, 8), (55, 22), (120, -10)):
                direction, up = rv.camera_direction(axis, azimuth, elevation)
                turned, screen_up = rv.left_to_right(direction, up, axis)
                self.assertGreater(rv.dot(rv.cross([-d for d in turned], screen_up), axis), 0.0)
                self.assertAlmostEqual(rv.dot(turned, rv.unit(axis)), rv.dot(direction, rv.unit(axis)))
                self.assertAlmostEqual(turned[2], direction[2])  # same elevation

    def test_folded_systems_keep_the_requested_side(self):
        from os_optics_kit import render_views as rv

        straight = ([-100.0, -12.7, -12.7], [200.0, 12.7, 12.7])
        self.assertTrue(rv.straight_layout(straight, [1, 0, 0], classes=["SOURCE", "LENS", "TARGET_PLANE"]))
        self.assertFalse(rv.straight_layout(straight, [1, 0, 0], classes=["SOURCE", "MIRROR"]))
        self.assertFalse(rv.straight_layout(([-110.0, -12.0, -12.0], [12.0, 400.0, 12.0]), [1, 0, 0]))

    def test_axis_as_list_or_text(self):
        self.assertEqual(present._axis_text([-1, 0, 0]), "-1.0,0.0,0.0")
        self.assertEqual(present._axis_text("0,1,0"), "0,1,0")
        self.assertIsNone(present._axis_text(None))
        with self.assertRaises(pres.PresentationError):
            present._axis_text([1, 0])

    def test_skill_pages_are_shown_by_name(self):
        from os_optics_kit import skills

        for page in ("compare.md", "handoff.md", "animate.md"):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(skills.main(["show", "present", page]), 0)
            self.assertTrue(out.getvalue().startswith("# present:"), page)
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            self.assertEqual(skills.main(["show", "present", "nope.md"]), 4)
        self.assertIn("PAGE_UNKNOWN", err.getvalue())


@needs(HAS_MATPLOTLIB, 'needs "os-optics-kit[plot]"')
class LegacyPlotTests(unittest.TestCase):
    def test_plot_without_a_theme_places_valueless_samples_below_the_lowest_value(self):
        from os_optics_kit import plot_results as pr
        from os_optics_kit import workflow_common as wc

        rows = [{"sample_id": f"s{i}", "outcome": "PASS" if v else "UNKNOWN", "param:x": float(i),
                 "target:t": v, "target:t:unit": "mm"} for i, v in enumerate([0.733, 0.9, None, 1.2])]
        with tempfile.TemporaryDirectory() as tmp:
            study = Path(tmp) / "study"
            wc.write_json(study / "summary.json", {
                "rows": rows, "columns": ["param:x", "target:t", "target:t:unit"],
                "parameters": [{"name": "x", "unit": "mm"}], "identity": {"engine": {"engine_build_id": "b"}}})
            result = pr.plot_sweep(study, "target:t", out_dir=Path(tmp) / "plots")
        # as in os-optics-kit 0.2: 12 % of the value span below the lowest value
        self.assertAlmostEqual(result["no_value_position"], 0.733 - 0.12 * (1.2 - 0.733))


class SpecificationTests(unittest.TestCase):
    def spec(self):
        spec = pres.default_spec()
        spec["sources"] = {"a": {"path": "run-a"}}
        spec["assets"] = [{"id": "view-a", "kind": "view", "source": "a"},
                          {"id": "pair", "kind": "compose", "panels": [{"asset": "view-a"}, {"asset": "plot-a"}]},
                          {"id": "plot-a", "kind": "plot", "type": "power", "source": "a"}]
        return spec

    def test_set_value_paths(self):
        spec = self.spec()
        pres.set_value(spec, "theme.language", "zh-CN")
        pres.set_value(spec, "assets.view-a.camera", [30, 10])
        pres.set_value(spec, "assets.view-a.caption.zh-CN", "标称设计")
        pres.set_value(spec, "title", "Demo")
        self.assertEqual(spec["theme"]["language"], "zh-CN")
        self.assertEqual(pres.find_asset(spec, "view-a")["caption"], {"zh-CN": "标称设计"})
        pres.validate_spec(spec)
        for bad in ("assets.view-a.kind", "assets.nope.camera", "colour", "assets.view-a"):
            with self.assertRaises(pres.PresentationError, msg=bad):
                pres.set_value(spec, bad, 1)
        self.assertEqual(pres.parse_value("[30, 10]"), [30, 10])
        self.assertEqual(pres.parse_value("zh-CN"), "zh-CN")

    def test_validation(self):
        spec = self.spec()
        spec["assets"].append({"id": "view-a", "kind": "view"})
        with self.assertRaisesRegex(pres.PresentationError, "used twice"):
            pres.validate_spec(spec)
        for bad in ({"id": "Bad Id", "kind": "view"}, {"id": "x", "kind": "slide"}):
            spec = self.spec()
            spec["assets"].append(bad)
            with self.assertRaises(pres.PresentationError):
                pres.validate_spec(spec)

    def test_build_order_and_references(self):
        order = [a["id"] for a in present.build_order(self.spec()["assets"])]
        self.assertLess(order.index("plot-a"), order.index("pair"))
        self.assertEqual(present.references({"id": "x", "kind": "compose", "panels": [{"asset": "a"}],
                                             "framing_sources": ["s1", "s2"]}), (["s1", "s2"], ["a"]))
        cyclic = [{"id": "a", "kind": "compose", "panels": [{"asset": "b"}]},
                  {"id": "b", "kind": "compose", "panels": [{"asset": "a"}]}]
        with self.assertRaisesRegex(pres.PresentationError, "cycle"):
            present.build_order(cyclic)

    def test_settings_lists_keys_and_help_needs_no_engine(self):
        code, document, _ = call(["settings"])
        self.assertEqual(code, 0)
        self.assertEqual(set(document["theme"]) - {"preset"}, set(pres.PRESETS["presentation"]))
        for kind in pres.ASSET_KINDS:
            self.assertIn(kind, document["assets"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(present.main(["--help"]), 0)
        self.assertIn("present build", out.getvalue())

    def test_usage_errors_exit_4(self):
        code, document, err = call(["build", "no-such-dir/presentation.json"])
        self.assertEqual(code, 4)
        self.assertEqual(json.loads(err)["error"]["code"], "SPEC_NOT_FOUND")
        self.assertIn("recovery", json.loads(err)["error"])


@requires_plot
class BuildTests(unittest.TestCase):
    """Two saved runs of the catalog-free ideal-lens case and a three-sample study, built without a browser."""

    @classmethod
    def setUpClass(cls):
        from os_optics_kit import sweep_parameters as sp
        from os_optics_kit import workflow_common as wc

        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        project = wc.read_json(ROOT / "cases" / "ideal-lens-focus" / "project.json")
        cls.runs = {}
        for name in ("nominal", "moved"):
            document = json.loads(json.dumps(project))
            if name == "moved":
                document["observations"][0]["pose"]["position_mm"][0] += 2.0  # plane 2 mm behind the focus
            record = wc.run_sample(document, cls.root / name)
            cls.runs[name] = Path(record["run_dir"])
        observation = project["observations"][0]["observation_id"]
        spec = {"parameters": [{"name": "plane_x", "unit": "mm", "values": [108.0, 110.0, 112.0], "ops": [
            {"op": "set_observation_pose", "observation_id": observation,
             "position_mm": ["$value", 0.0, 20.0]}]}], "max_evaluations": 4}
        sp.run_sweep(spec, cls.root / "study", base=project)
        cls.study = cls.root / "study"
        cls.before = {name: tree_digest(path) for name, path in [*cls.runs.items(), ("study", cls.study)]}

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def plot_spec(self, out):
        from os_optics_kit import workflow_common as wc

        code, plan, err = call(["init", str(self.runs["nominal"]), str(self.study), "--out", str(out),
                                "--labels", "Nominal,Plane position", "--title", "Ideal lens focus"])
        self.assertEqual(code, 0, err)
        spec = wc.read_json(out / "presentation.json")
        for source in spec["sources"].values():
            self.assertFalse(Path(source["path"]).is_absolute())
        # browser-free subset: power plot and sweep curves
        spec["assets"] = [a for a in spec["assets"] if a["kind"] == "plot" and a["type"] in ("power", "sweep")]
        pres.save_spec(spec, out / "presentation.json")
        return spec

    def test_build_restyle_reuse_and_data_preservation(self):
        from os_optics_kit import workflow_common as wc

        out = self.root / "talk"
        spec = self.plot_spec(out)
        sweep = next(a["id"] for a in spec["assets"] if a["type"] == "sweep")
        with mock.patch("os_optics.local.run_optical_project", side_effect=AssertionError("solver called")):
            code, result, err = call(["build", str(out / "presentation.json"), "--no-gallery"])
            self.assertEqual(code, 0, err)
            self.assertEqual(result["counts"]["written"], len(spec["assets"]))
            self.assertIn('"progress"', err)
            manifest = wc.read_json(out / "assets.json")
            entry = next(e for e in manifest["assets"] if e["id"] == sweep)
            for role, record in entry["files"].items():
                self.assertEqual(wc.sha256_file(out / record["path"]), record["sha256"], role)
            self.assertTrue((out / entry["preview"]).is_file())
            self.assertEqual(entry["size_px"], [1920, 1080])
            self.assertEqual(set(entry["captions"]), {"en", "zh-CN"})
            svg = (out / entry["files"]["svg"]["path"]).read_text(encoding="utf-8")
            self.assertNotIn("sha256", svg)  # provenance stays in the companion record
            provenance = wc.read_json(out / entry["provenance"])
            self.assertEqual(provenance["sources"][0]["identity"]["summary_sha256"],
                             wc.sha256_file(self.study / "summary.json"))
            # unchanged rebuild reuses every asset
            code, result, _ = call(["build", str(out / "presentation.json"), "--no-gallery", "--quiet"])
            self.assertEqual(result["counts"]["reused"], len(spec["assets"]))
            # follow-up: white background, Chinese labels, larger text, 4k; saved in presentation.json
            code, result, err = call(["build", str(out / "presentation.json"), "--no-gallery", "--quiet",
                                      "--set", "theme.language=zh-CN", "--set", "theme.background=light",
                                      "--set", "theme.font_scale=1.4", "--set", "theme.size=4k",
                                      "--set", f"assets.{sweep}.caption.zh-CN=焦点附近的取值"])
            self.assertEqual(code, 0, err)
            self.assertEqual(result["counts"]["written"], len(spec["assets"]))
        saved = wc.read_json(out / "presentation.json")
        self.assertEqual(saved["theme"]["language"], "zh-CN")
        manifest = wc.read_json(out / "assets.json")
        entry = next(e for e in manifest["assets"] if e["id"] == sweep)
        self.assertEqual(entry["size_px"], [3840, 2160])
        self.assertEqual(entry["caption"], "焦点附近的取值")
        self.assertIn("随", (out / entry["files"]["svg"]["path"]).read_text(encoding="utf-8"))  # Chinese title
        after = {name: tree_digest(path) for name, path in [*self.runs.items(), ("study", self.study)]}
        self.assertEqual(after, self.before)
        for path in out.rglob("*"):
            if path.suffix in (".json", ".txt", ".svg"):
                self.assertIsNone(ABSOLUTE.search(path.read_text(encoding="utf-8")), path.name)

    def test_variant_directory_missing_source_and_stale_assets(self):
        from os_optics_kit import workflow_common as wc

        out = self.root / "variant-base"
        spec = self.plot_spec(out)
        code, result, err = call(["build", str(out / "presentation.json"), "--no-gallery", "--quiet",
                                  "--out", str(self.root / "variant-dark"), "--set", "theme.background=dark"])
        self.assertEqual(code, 0, err)
        variant = wc.read_json(self.root / "variant-dark" / "presentation.json")
        self.assertEqual(variant["theme"]["background"], "dark")
        self.assertNotIn("background", wc.read_json(out / "presentation.json")["theme"])
        self.assertTrue((self.root / "variant-dark" / variant["sources"]["nominal"]["path"] / "manifest.json").is_file())
        # a missing source is reported for its assets only; the others are still built
        spec["sources"]["nominal"]["path"] = "../no-such-run"
        pres.save_spec(spec, out / "presentation.json")
        code, result, err = call(["build", str(out / "presentation.json"), "--no-gallery", "--quiet"])
        self.assertEqual(code, 2, err)
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertEqual({m["code"] for m in result["missing"]}, {"SOURCE_MISSING"})
        self.assertTrue(all(m.get("recovery") for m in result["missing"]))
        self.assertGreater(result["counts"]["written"], 0)
        # removing an asset removes its files
        removed = spec["assets"].pop()
        pres.save_spec(spec, out / "presentation.json")
        call(["build", str(out / "presentation.json"), "--no-gallery", "--quiet"])
        self.assertFalse((out / "assets" / removed["id"]).exists())
        self.assertFalse((out / "previews" / f"{removed['id']}.png").exists())

    def test_two_parameter_study_label_change_crafted_index_and_chinese_folder(self):
        from os_optics_kit import sweep_parameters as sp
        from os_optics_kit import workflow_common as wc

        project = wc.read_json(ROOT / "cases" / "ideal-lens-focus" / "project.json")
        observation = project["observations"][0]["observation_id"]
        spec = {"parameters": [
            {"name": "plane_x", "unit": "mm", "values": [109.0, 111.0], "ops": [
                {"op": "set_observation_pose", "observation_id": observation, "position_mm": ["$value", 0.0, 20.0]}]},
            {"name": "power", "unit": "W", "values": [0.001, 0.002], "ops": [
                {"op": "set_source_model", "instance_id": "SRC", "changes": {"power_w": "$value"}}]}],
            "max_evaluations": 5}
        study = self.root / "光学方案"  # a Chinese folder name
        sp.run_sweep(spec, study, base=project)
        out = self.root / "heatmap"
        code, plan, err = call(["init", str(study), str(self.runs["nominal"]), "--out", str(out)])
        self.assertEqual(code, 0, err)
        saved = wc.read_json(out / "presentation.json")
        self.assertEqual(saved["sources"]["source"]["label"], "光学方案")
        saved["assets"] = [a for a in saved["assets"] if a["kind"] == "plot" and a["type"] in ("sweep", "power")]
        pres.save_spec(saved, out / "presentation.json")
        code, result, err = call(["build", str(out / "presentation.json"), "--no-gallery", "--quiet"])
        self.assertEqual(code, 0, err)  # a two-parameter study gives a heatmap
        heatmap = next(e for e in wc.read_json(out / "assets.json")["assets"] if e["id"].startswith("sweep-"))
        self.assertEqual(heatmap["details"]["x"]["column"], "param:plane_x")
        # a changed source label redraws the assets that show it
        code, result, err = call(["build", str(out / "presentation.json"), "--no-gallery", "--quiet",
                                  "--set", "sources.nominal.label=Adjusted"])
        status = {a["id"]: a["status"] for a in result["assets"]}
        self.assertEqual(status["power-nominal"], "written", result)  # shows the label
        self.assertEqual(status[heatmap["id"]], "reused", result)  # does not use that source
        # a crafted asset index never makes a build delete files outside an asset's own folder
        victim = self.root / "keep-me.txt"
        victim.write_text("user file", encoding="utf-8")
        manifest = wc.read_json(out / "assets.json")
        manifest["assets"].append({"id": "gone", "kind": "plot", "status": "written", "preview": "../keep-me.txt",
                                   "files": {"image": {"path": "../keep-me.txt", "sha256": "0", "bytes": 1}}})
        wc.write_json(out / "assets.json", manifest)
        call(["build", str(out / "presentation.json"), "--no-gallery", "--quiet"])
        self.assertTrue(victim.is_file())

    def test_an_edited_replayed_file_redraws_the_asset(self):
        from os_optics_kit import workflow_common as wc

        out = self.root / "replayed"
        spec = self.plot_spec(out)
        power = next(a for a in spec["assets"] if a["type"] == "power")
        (out / "seq.json").write_text("{}", encoding="utf-8")
        power["sequence"] = "seq.json"  # any file an asset replays is part of its fingerprint
        pres.save_spec(spec, out / "presentation.json")
        call(["build", str(out / "presentation.json"), "--no-gallery", "--quiet", "--only", power["id"]])
        code, result, _ = call(["build", str(out / "presentation.json"), "--no-gallery", "--quiet",
                                "--only", power["id"]])
        self.assertEqual(next(a for a in result["assets"] if a["id"] == power["id"])["status"], "reused")
        (out / "seq.json").write_text('{"edited": true}', encoding="utf-8")
        code, result, _ = call(["build", str(out / "presentation.json"), "--no-gallery", "--quiet",
                                "--only", power["id"]])
        self.assertEqual(next(a for a in result["assets"] if a["id"] == power["id"])["status"], "written")
        self.assertTrue((out / "seq.json").is_file())
        # an asset left out by --only is "stale" once its settings changed, "reused" while they still match
        sweep = next(a for a in spec["assets"] if a["type"] == "sweep")["id"]
        call(["build", str(out / "presentation.json"), "--no-gallery", "--quiet"])
        code, result, _ = call(["build", str(out / "presentation.json"), "--no-gallery", "--quiet", "--only",
                                power["id"], "--set", f"assets.{sweep}.caption=Spot radius near the focus"])
        status = {a["id"]: a["status"] for a in result["assets"]}
        self.assertEqual(status[sweep], "stale")
        self.assertEqual(status[power["id"]], "reused")
        self.assertIsNotNone(wc.read_json(out / "assets.json"))

    def test_output_inside_a_source_is_refused(self):
        out = self.root / "inside"
        self.plot_spec(out)
        code, _, err = call(["build", str(out / "presentation.json"), "--no-gallery",
                             "--out", str(self.runs["nominal"] / "talk")])
        self.assertEqual(code, 4)
        self.assertEqual(json.loads(err)["error"]["code"], "OUTPUT_INVALID")
        self.assertFalse((self.runs["nominal"] / "talk").exists())

    def test_plot_command_theme(self):
        from os_optics_kit import plot_results as pr

        out = self.root / "plot-theme"
        code = pr.main(["sweep", str(self.study), "--y", next(iter(present._study_targets(self.study))),
                        "--out", str(out), "--name", "zh", "--theme", "presentation", "--set", "language=zh-CN",
                        "--set", "background=transparent", "--set", "title=false"])
        self.assertEqual(code, 0)
        self.assertTrue((out / "zh.caption.txt").is_file())
        self.assertIn("sha256", (out / "zh.provenance.txt").read_text(encoding="utf-8"))
        svg = (out / "zh.svg").read_text(encoding="utf-8")
        self.assertNotIn("sha256", svg)


if __name__ == "__main__":
    unittest.main()
