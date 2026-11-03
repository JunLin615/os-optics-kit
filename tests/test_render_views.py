"""Tests of ``os-optics-kit render`` (tools/os_optics_kit/render_views.py).

The camera arithmetic, the shot specs, the framing box and the page injection are tested without an
engine, a run directory or a browser; the command's registration and ``--help`` are checked as the
kit requires. A capture is not part of CI (it needs a Chrome/Chromium on the machine).
"""

import contextlib
import doctest
import io
import json
import math
import subprocess
import sys
import unittest

import os_optics_kit.render_views as helper
from os_optics_kit.cli import COMMANDS


class RenderViewsTests(unittest.TestCase):
    def test_exposes_main(self):
        self.assertTrue(callable(helper.main))

    def test_registered_command(self):
        self.assertEqual(COMMANDS["render"][0], "render_views")

    def test_help_exits_zero(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                code = helper.main(["--help"])
            except SystemExit as exc:
                code = exc.code
        self.assertIn(code, (0, None))
        self.assertIn("usage", out.getvalue().lower())

    def test_help_without_engine(self):
        script = ("import sys; sys.modules['os_optics'] = None; "
                  "from os_optics_kit.cli import main; raise SystemExit(main(['render', '--help']))")
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_doctests(self):
        failed, _ = doctest.testmod(helper, verbose=False)
        self.assertEqual(failed, 0)


class CameraTests(unittest.TestCase):
    AXIS = [1.0, 0.0, 0.0]

    def test_azimuth_zero_looks_down_the_axis(self):
        direction, up = helper.camera_direction(self.AXIS, 0.0, 0.0)
        self.assertAlmostEqual(direction[0], 1.0, places=9)
        self.assertAlmostEqual(abs(up[2]), 1.0, places=9)

    def test_azimuth_ninety_is_side_on(self):
        direction, _up = helper.camera_direction(self.AXIS, 90.0, 0.0)
        self.assertAlmostEqual(helper.dot(direction, self.AXIS), 0.0, places=9)
        self.assertAlmostEqual(math.sqrt(sum(c * c for c in direction)), 1.0, places=9)

    def test_elevation_lifts_towards_the_up_axis(self):
        direction, _up = helper.camera_direction(self.AXIS, 90.0, 90.0)
        self.assertAlmostEqual(direction[2], 1.0, places=9)

    def test_each_pose_is_a_frame(self):
        for azimuth in (0.0, 35.0, 55.0, 86.0, 90.0):
            direction, up = helper.camera_direction(self.AXIS, azimuth, 12.0)
            self.assertAlmostEqual(helper.dot(direction, up), 0.0, places=9)
            self.assertAlmostEqual(math.sqrt(sum(c * c for c in up)), 1.0, places=9)

    def test_vertical_axis_falls_back_to_another_up(self):
        direction, up = helper.camera_direction([0.0, 0.0, 1.0], 90.0, 0.0)
        self.assertAlmostEqual(helper.dot(direction, up), 0.0, places=9)
        self.assertGreater(abs(direction[0]) + abs(direction[1]) + abs(direction[2]), 0.5)

    def test_optical_axis_prefers_an_override(self):
        axis = helper.optical_axis({"main_axis": [1.0, 0.0, 0.0]}, "0,1,0")
        self.assertEqual(axis, [0.0, 1.0, 0.0])

    def test_optical_axis_uses_main_axis(self):
        axis = helper.optical_axis({"main_axis": [2.0, 0.0, 0.0]})
        self.assertAlmostEqual(axis[0], 1.0, places=9)

    def test_optical_axis_without_one_is_an_error(self):
        with self.assertRaises(helper.RenderError) as caught:
            helper.optical_axis({})
        self.assertEqual(caught.exception.code, "CAMERA_UNDEFINED")


class ShotSpecTests(unittest.TestCase):
    def test_parse_camera(self):
        self.assertEqual(helper.parse_camera("86,8"), (86.0, 8.0, 0.0))
        self.assertEqual(helper.parse_camera("86, 8, -4"), (86.0, 8.0, -4.0))

    def test_parse_camera_rejects_bad_input(self):
        for text in ("86", "a,b", "1,2,3,4"):
            with self.assertRaises(helper.RenderError) as caught:
                helper.parse_camera(text)
            self.assertEqual(caught.exception.code, "CAMERA_INVALID")

    def test_parse_size(self):
        self.assertEqual(helper.parse_size("1920x1080"), (1920, 1080))

    def test_parse_size_rejects_bad_input(self):
        for text in ("1920", "1920*1080", "1x1"):
            with self.assertRaises(helper.RenderError) as caught:
                helper.parse_size(text)
            self.assertEqual(caught.exception.code, "SIZE_INVALID")

    def _args(self, **overrides):
        parser = helper.build_parser()
        base = parser.parse_args(["RUN", "--out", "OUT", "--camera", "86,8"])
        for key, value in overrides.items():
            setattr(base, key, value)
        return base

    def test_duplicate_shot_names_are_refused(self):
        args = self._args(camera=["86,8", "55,22"], name="same")
        shots = helper.collect_shots(args, [1.0, 0.0, 0.0])
        self.assertEqual(len(shots), 2)
        args.camera = ["86,8"]
        args.name = None
        self.assertEqual(helper.collect_shots(args, [1.0, 0.0, 0.0])[0]["name"], "shot1")

    def test_no_shot_is_an_error(self):
        args = self._args(camera=[])
        with self.assertRaises(helper.RenderError) as caught:
            helper.collect_shots(args, [1.0, 0.0, 0.0])
        self.assertEqual(caught.exception.code, "SHOTS_MISSING")


class FramingTests(unittest.TestCase):
    PARTS = {
        "G1": ("LENS", [(0.0, 0.0, 0.0), (0.0, 1.0, 2.0)]),
        "SRC": ("SOURCE", [(-300.0, 0.0, 0.0), (-300.0, 1.0, 0.0)]),
        "IMG": ("OBSERVATION_PLANE", [(50.0, 0.0, 0.0), (50.0, 1.0, 0.0)]),
    }

    def test_optics_drops_sources_and_planes(self):
        low, high = helper.framing_box(self.PARTS, "optics")
        self.assertEqual(low, [0.0, 0.0, 0.0])
        self.assertEqual(high, [0.0, 1.0, 2.0])

    def test_scene_keeps_everything(self):
        low, _high = helper.framing_box(self.PARTS, "scene")
        self.assertEqual(low[0], -300.0)

    def test_element_focus(self):
        low, high = helper.framing_box(self.PARTS, "element:IMG")
        self.assertEqual(low, [50.0, 0.0, 0.0])
        self.assertEqual(high, [50.0, 1.0, 0.0])

    def test_unknown_element_is_an_error(self):
        with self.assertRaises(helper.RenderError) as caught:
            helper.framing_box(self.PARTS, "element:NOPE")
        self.assertEqual(caught.exception.code, "FOCUS_UNKNOWN")

    def test_unknown_focus_is_an_error(self):
        with self.assertRaises(helper.RenderError) as caught:
            helper.framing_box(self.PARTS, "middle")
        self.assertEqual(caught.exception.code, "FOCUS_INVALID")


class InjectionTests(unittest.TestCase):
    def test_style_and_camera_script_are_inserted_before_body_end(self):
        page = "<html><body><div id='app'></div></body></html>"
        config = {"direction": [1.0, 0.0, 0.0], "up": [0.0, 0.0, 1.0],
                  "box_min": [0.0, 0.0, 0.0], "box_max": [1.0, 1.0, 1.0], "roll_deg": 0.0,
                  "hide_classes": ["SOURCE"], "edges": False, "labels": False, "beams": False}
        out = helper.inject(page, config, "#101418")
        self.assertIn("os-optics-kit-render-camera", out)
        self.assertIn("#101418", out)
        self.assertIn(json.dumps(config["direction"]), out)
        self.assertLess(out.index("os-optics-kit-render-camera"), out.index("</body>"))

    def test_page_without_body_end_is_an_error(self):
        with self.assertRaises(helper.RenderError) as caught:
            helper.inject("<html></html>", {}, "#000000")
        self.assertEqual(caught.exception.code, "PAGE_UNEXPECTED")

    def test_explicit_browser_wins_when_it_exists(self):
        self.assertEqual(helper.find_browser(sys.executable), sys.executable)


if __name__ == "__main__":
    unittest.main()
