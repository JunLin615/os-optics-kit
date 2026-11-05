"""Tests of ``os-optics-kit render`` (tools/os_optics_kit/render_views.py).

The camera arithmetic, shot specs, framing and injection need no engine or browser.
Real capture and camera-state checks live in test_render_browser.py.
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
        self.assertEqual(up, [0.0, 0.0, 1.0])

    def test_azimuth_ninety_is_side_on(self):
        direction, _up = helper.camera_direction(self.AXIS, 90.0, 0.0)
        self.assertAlmostEqual(helper.dot(direction, self.AXIS), 0.0, places=9)
        self.assertAlmostEqual(math.sqrt(sum(c * c for c in direction)), 1.0, places=9)

    def test_elevation_lifts_towards_the_up_axis(self):
        direction, _up = helper.camera_direction(self.AXIS, 55.0, 22.0)
        self.assertAlmostEqual(math.degrees(math.asin(direction[2])), 22.0, places=9)

    def test_measured_angle_and_screen_up(self):
        for azimuth, elevation in ((0, 0), (35, 35), (55, 22), (86, 8), (90, -22), (125, 22), (180, 0)):
            with self.subTest(azimuth=azimuth, elevation=elevation):
                direction, up = helper.camera_direction(self.AXIS, azimuth, elevation)
                self.assertAlmostEqual(math.degrees(math.acos(helper.dot(direction, self.AXIS))), azimuth, places=9)
                self.assertAlmostEqual(helper.dot(direction, up), 0.0, places=9)
                self.assertAlmostEqual(math.hypot(*direction), 1.0, places=9)
                self.assertAlmostEqual(math.hypot(*up), 1.0, places=9)
                self.assertGreater(up[2], 0)

    def test_tilted_axis_uses_an_orthogonal_basis(self):
        axis = helper.unit([1.0, 0.0, 1.0])
        direction, up = helper.camera_direction(axis, 55.0, 22.0)
        self.assertAlmostEqual(math.degrees(math.acos(helper.dot(direction, axis))), 55.0, places=9)
        self.assertAlmostEqual(helper.dot(direction, up), 0.0, places=9)
        self.assertGreater(up[2], 0)

    def test_parallel_axis_requires_an_explicit_up(self):
        with self.assertRaisesRegex(helper.RenderError, "--up-axis"):
            helper.camera_direction([0.0, 0.0, 1.0], 55.0, 22.0)
        direction, up = helper.camera_direction([0.0, 0.0, 1.0], 0.0, 0.0, [0.0, 1.0, 0.0])
        self.assertEqual(direction, [0.0, 0.0, 1.0])
        self.assertEqual(up, [0.0, 1.0, 0.0])

    def test_impossible_angles_and_poles_are_refused(self):
        for azimuth, elevation in ((0, 12), (20, 30), (170, -20), (-55, 22), (181, 0), (90, 90), (90, -90)):
            with self.subTest(azimuth=azimuth, elevation=elevation):
                with self.assertRaises(helper.RenderError) as caught:
                    helper.camera_direction(self.AXIS, azimuth, elevation)
                self.assertEqual(caught.exception.code, "CAMERA_INVALID")

    def test_camera_vectors_and_angles_are_finite(self):
        for vector in ([1, 0], [0, 0, 0], [math.nan, 0, 0], [math.inf, 0, 0]):
            with self.assertRaises(helper.RenderError):
                helper.camera_direction(vector, 55, 22)
        for pose in ("nan,0", "55,inf", "55,22,nan"):
            with self.assertRaises(helper.RenderError) as caught:
                helper.parse_camera(pose)
            self.assertEqual(caught.exception.code, "CAMERA_INVALID")

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

    def test_name_requires_one_camera(self):
        args = self._args(camera=["86,8", "55,22"], name="same")
        with self.assertRaises(helper.RenderError):
            helper.collect_shots(args)
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
