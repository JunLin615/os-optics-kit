"""Presentation animations (os_optics_kit.animate): camera paths, playback order and labels, saved sequences, media.

Run from the repository root: python -m unittest tests.test_present_animate -v

The geometry, ordering, label and sequence tests need nothing beyond the kit; the media tests need matplotlib, Pillow
and imageio-ffmpeg (``os-optics-kit[plot,media]``); the end-to-end tests also need the engine, Playwright
(``os-optics-kit[render]``) and Chrome/Chromium. Those skip when a dependency is missing, unless
OS_OPTICS_KIT_REQUIRE_PRESENTATION=1, which turns a missing dependency into a failure.
"""

from __future__ import annotations

import copy
from importlib.util import find_spec
import json
import math
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

from tests.workflow_fixtures import HAS_ENGINE, TempDir, expander, quiet_environment

quiet_environment()

from os_optics_kit import animate as an  # noqa: E402
from os_optics_kit import presentation as pr  # noqa: E402
from os_optics_kit import render_views as rv  # noqa: E402

REQUIRE = os.environ.get("OS_OPTICS_KIT_REQUIRE_PRESENTATION") == "1"
HAS_MEDIA = all(find_spec(name) is not None for name in ("matplotlib", "PIL", "imageio_ffmpeg"))
HAS_PLAYWRIGHT = find_spec("playwright") is not None
AXIS = [1.0, 0.0, 0.0]
UP = [0.0, 0.0, 1.0]


def require(test, available, what):
    """Skip without ``what`` (fail instead under OS_OPTICS_KIT_REQUIRE_PRESENTATION=1)."""
    if available:
        return
    if REQUIRE:
        test.fail(f"{what} is required (OS_OPTICS_KIT_REQUIRE_PRESENTATION=1)")
    test.skipTest(f"{what} is not available")


def angle_between(a, b):
    return math.degrees(math.acos(max(-1.0, min(1.0, rv.dot(rv.unit(a), rv.unit(b))))))


class Context:
    """Stand-in for present's build context: theme, sources, asset and cache directories, progress, browser."""

    def __init__(self, out_dir, sources, theme=None, browser=None):
        self.out_dir = Path(out_dir)
        self.spec_dir = self.out_dir
        self.spec = {"theme": theme or {}}
        self._theme = theme or {}
        self.theme = pr.resolve_theme(self._theme)
        self._sources = sources
        self.browser = browser
        self.progress_calls = []

    def asset_theme(self, asset):
        return pr.resolve_theme(self._theme, asset.get("theme"))

    def source(self, source_id):
        entry = self._sources[source_id]
        return pr.Source(id=source_id, kind=pr.source_kind(entry["path"]), path=Path(entry["path"]),
                         label=entry.get("label"), identity=entry.get("identity", {"id": source_id}))

    def asset_dir(self, asset_id):
        path = self.out_dir / "assets" / asset_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def cache_dir(self, key):
        path = self.out_dir / ".cache" / key
        path.mkdir(parents=True, exist_ok=True)
        return path

    def progress(self, asset_id, done, total):
        self.progress_calls.append((asset_id, done, total))


def fake_study(root, rows_spec, *, unit="mm", wavelengths=None):
    """A saved study directory (summary.json, study.json, sample.json) without runs: values only.

    ``wavelengths``: one value per row adds a second parameter ``wavelength`` [nm]."""
    root = Path(root)
    rows = []
    for index, (x, y, outcome) in enumerate(rows_spec):
        sample_id = f"s{index + 1:04d}"
        rows.append({"sample_id": sample_id, "outcome": outcome, "param:gap": x, "param:gap:unit": "mm",
                     "target:waist": y, "target:waist:unit": unit, "target:waist:exact": y is not None,
                     "run_dir": None, "result_hash": f"hash{index}"})
        if wavelengths:
            rows[-1].update({"param:wavelength": wavelengths[index], "param:wavelength:unit": "nm"})
        sample_dir = root / "samples" / sample_id
        sample_dir.mkdir(parents=True)
        (sample_dir / "sample.json").write_text(json.dumps({"targets": [
            {"id": "waist", "bounds": {"minimum": 1.0, "maximum": 2.0}}]}), encoding="utf-8")
    parameters = [{"name": "gap", "unit": "mm", "count": len(rows)}]
    if wavelengths:
        parameters.append({"name": "wavelength", "unit": "nm", "count": len(set(wavelengths))})
    summary = {"parameters": parameters,
               "columns": ["sample_id", "outcome", "param:gap", "param:gap:unit", "target:waist", "target:waist:unit",
                           "target:waist:exact", "run_dir"],
               "rows": rows, "identity": {"engine": {"engine_build_id": "test"}}}
    (root / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (root / "study.json").write_text(json.dumps({"engine": {}, "catalog": {}}), encoding="utf-8")
    return root


# --------------------------------------------------------------------------------------------- pure functions

class CameraPathTests(unittest.TestCase):
    def test_orbit_about_up_keeps_elevation_and_steps_evenly(self):
        orbit = {"about": "up", "start_deg": 55.0, "sweep_deg": 360.0, "elevation_deg": 20.0, "angle_deg": 70.0}
        fractions = an.path_fractions(24, loop=True, easing="auto")
        self.assertEqual(fractions[0], 0.0)
        self.assertLess(fractions[-1], 1.0)  # a loop leaves out the end: it equals the start
        poses = an.orbit_poses(AXIS, UP, orbit, fractions)
        steps = [angle_between(a[0], b[0]) for a, b in zip(poses, poses[1:])]
        self.assertLess(max(steps) - min(steps), 1e-6)  # constant speed
        for direction, up, _angle in poses:
            self.assertAlmostEqual(math.degrees(math.asin(rv.dot(direction, UP))), 20.0, places=9)
            self.assertAlmostEqual(rv.dot(direction, up), 0.0, places=9)
            self.assertAlmostEqual(math.hypot(*up), 1.0, places=9)
        # with zero elevation the start is the render pose AZ=start_deg
        level = an.orbit_poses(AXIS, UP, {**orbit, "elevation_deg": 0.0}, [0.0])[0]
        expected, _up = rv.camera_direction(AXIS, 55.0, 0.0, UP)
        self.assertLess(angle_between(level[0], expected), 1e-5)

    def test_orbit_starts_with_light_left_to_right_like_present_views(self):
        box = ([0.0, -6.0, -6.0], [150.0, 6.0, 6.0])
        view = SimpleNamespace(axis=AXIS, document={"camera_fov_deg": 14.0}, box=lambda focus: box)
        cfg = an.normalize({"id": "o", "kind": "animation", "mode": "orbit", "source": "run", "duration_s": 1,
                            "fps": 8, "orbit": {"start_deg": 55, "elevation_deg": 0}})
        shots, _segments, _focus = an.plan_orbit(cfg, view, "run", [0, 0, 320, 180])
        expected, _up = an.pose_vectors(AXIS, [55.0, 0.0, 0.0], UP)
        self.assertLess(angle_between(shots[0]["direction"], expected), 1e-5)
        first = shots[0]
        self.assertEqual(rv.left_to_right(first["direction"], first["up"], AXIS)[0], first["direction"])
        self.assertTrue(all(shot.get("mirrored") for shot in shots))
        steps = [angle_between(a["direction"], b["direction"]) for a, b in zip(shots, shots[1:])]
        self.assertLess(max(steps) - min(steps), 1e-6)  # the mirrored path still turns evenly

    def test_orbit_about_optical_axis_keeps_angle_and_turns_rigidly(self):
        orbit = {"about": "optical_axis", "start_deg": 0.0, "sweep_deg": 90.0, "elevation_deg": 0.0,
                 "angle_deg": 65.0}
        poses = an.orbit_poses(AXIS, UP, orbit, an.path_fractions(10, loop=False, easing="ease"))
        for direction, up, _angle in poses:
            self.assertAlmostEqual(angle_between(direction, AXIS), 65.0, places=9)
            self.assertAlmostEqual(rv.dot(up, AXIS), 0.0, places=9)  # the axis stays put on screen
            self.assertAlmostEqual(rv.dot(direction, up), 0.0, places=9)
        self.assertAlmostEqual(poses[-1][2] - poses[0][2], 90.0)
        steps = [angle_between(a[0], b[0]) for a, b in zip(poses, poses[1:])]
        self.assertLess(steps[0], steps[len(steps) // 2])  # eased: slow start
        self.assertLess(steps[-1], steps[len(steps) // 2])

    def test_orbit_refuses_vertical_axis(self):
        orbit = {"about": "up", "start_deg": 0.0, "sweep_deg": 360.0, "elevation_deg": 10.0, "angle_deg": 70.0}
        with self.assertRaises(pr.PresentationError) as caught:
            an.orbit_poses([0.0, 0.0, 1.0], UP, orbit, [0.0])
        self.assertEqual(caught.exception.code, "CAMERA_UNDEFINED")

    def test_transition_hits_both_ends_and_moves_smoothly(self):
        box0 = ([0.0, -6.0, -6.0], [150.0, 6.0, 6.0])
        box1 = ([148.0, -6.0, -6.0], [152.0, 6.0, 6.0])
        d0, u0 = an.pose_vectors(AXIS, [55.0, 22.0, 0.0], UP)
        d1, u1 = an.pose_vectors(AXIS, [35.0, 12.0, 0.0], UP)
        start = (d0, u0, box0, an.fit_distance(box0, d0, u0, 14.0, 16 / 9))
        end = (d1, u1, box1, an.fit_distance(box1, d1, u1, 14.0, 16 / 9))
        self.assertGreater(start[3], end[3])
        fractions = an.path_fractions(30, loop=False, easing="auto")
        poses = an.transition_poses(start, end, fractions)
        for got, want in ((poses[0], start), (poses[-1], end)):
            self.assertLess(angle_between(got[0], want[0]), 1e-5)
            self.assertLess(angle_between(got[1], want[1]), 1e-5)
            self.assertAlmostEqual(got[3], want[3], places=6)
            for index in range(3):
                self.assertAlmostEqual(got[2][0][index], want[2][0][index], places=6)
                self.assertAlmostEqual(got[2][1][index], want[2][1][index], places=6)
        distances = [pose[3] for pose in poses]
        self.assertTrue(all(a >= b for a, b in zip(distances, distances[1:])))  # monotonic zoom
        steps = [angle_between(a[0], b[0]) for a, b in zip(poses, poses[1:])]
        self.assertLess(max(steps), 3.0)
        self.assertLess(steps[0], max(steps) / 4)  # eased in and out
        self.assertLess(steps[-1], max(steps) / 4)
        ratios = [math.log(a / b) for a, b in zip(distances, distances[1:])]
        self.assertLess(ratios[0], max(ratios) / 4)

    def test_fit_distance_keeps_every_corner_in_view(self):
        box = ([0.0, -6.0, -6.0], [150.0, 6.0, 6.0])
        direction, up = an.pose_vectors(AXIS, [55.0, 22.0, 0.0], UP)
        fov, aspect = 14.0, 16 / 9
        distance = an.fit_distance(box, direction, up, fov, aspect)
        center = [(a + b) / 2 for a, b in zip(*box)]
        eye = [c + d * distance for c, d in zip(center, direction)]
        right = rv.unit(rv.cross(up, direction))
        tan_v = math.tan(math.radians(fov) / 2)
        for corner in range(8):
            point = [box[1][i] if corner & (1 << i) else box[0][i] for i in range(3)]
            offset = [p - e for p, e in zip(point, eye)]
            depth = -rv.dot(offset, direction)
            self.assertGreater(depth, 0)
            self.assertLessEqual(abs(rv.dot(offset, up)) / depth, tan_v / an.FIT_MARGIN + 1e-9)
            self.assertLessEqual(abs(rv.dot(offset, right)) / depth, tan_v * aspect / an.FIT_MARGIN + 1e-9)


class TimelineTests(unittest.TestCase):
    def test_video_frames_follow_the_cumulative_time(self):
        segments = [{"duration_s": 0.8} for _ in range(12)]
        self.assertAlmostEqual(an.assign_timeline(segments, 24), 9.6)
        self.assertEqual(sum(s["frames"] for s in segments), round(9.6 * 24))
        self.assertEqual([s["start_s"] for s in segments[:3]], [0.0, 0.8, 1.6])
        self.assertTrue(all(s["frames"] in (19, 20) for s in segments))

    def test_every_segment_of_a_frame_or_longer_gets_a_frame(self):
        segments = [{"duration_s": d} for d in (0.375, 0.25, 0.25, 0.375, 0.25)]  # 1.5, 1, 1, 1.5, 1 frames at 4 fps
        an.assign_timeline(segments, 4)
        self.assertTrue(all(s["frames"] >= 1 for s in segments), [s["frames"] for s in segments])
        short = [{"index": i, "duration_s": 0.1} for i in range(5)]
        self.assertAlmostEqual(an.sample_hold(short, 4), 0.25)
        self.assertEqual([s["duration_s"] for s in short], [0.25] * 5)
        an.assign_timeline(short, 4)
        self.assertEqual([s["frames"] for s in short], [1] * 5)
        plan = an.segment_gif_timeline(short)
        self.assertEqual([i for i, _ms in plan], [0, 1, 2, 3, 4])  # one GIF frame per sample
        self.assertEqual(sum(ms for _i, ms in plan), 1250)

    def test_gif_timeline_merges_one_image_and_keeps_the_total(self):
        segments = [{"shot": "a", "duration_s": 0.5}] + [{"shot": f"m{i}", "duration_s": 0.25} for i in range(4)] + [
            {"shot": "m3", "duration_s": 0.5}]
        plan = an.gif_timeline(segments, 4, key=lambda s: s["shot"])
        self.assertEqual(sum(ms for _i, ms in plan), 2000)
        self.assertEqual([segments[i]["shot"] for i, _ms in plan], ["a", "m0", "m1", "m2", "m3"])
        self.assertEqual(plan[0][1], 500)
        self.assertEqual(plan[-1][1], 750)  # the last motion frame and the end hold are one image


class PlaybackTextTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TempDir()
        self.dir = self._tmp.__enter__()
        self.addCleanup(self._tmp.__exit__, None, None, None)

    def test_order_by_parameter_keeps_recorded_order_and_missing_samples(self):
        study_dir = fake_study(self.dir / "study", [(2.0, 1.5, "PASS"), (0.5, None, "ERROR"), (1.0, 1.2, "FAIL"),
                                                    (0.5, 1.1, "FAIL")])
        study = an.study_samples(study_dir, "target:waist")
        order = an.playback_order(study["samples"], "gap")
        self.assertEqual([s["sample_id"] for s in order], ["s0002", "s0004", "s0003", "s0001"])
        self.assertEqual([s["recorded_index"] for s in order], [1, 3, 2, 0])
        self.assertEqual(study["bounds"], {"minimum": 1.0, "maximum": 2.0})
        missing = order[0]
        self.assertFalse(missing["has_run"])
        self.assertEqual(missing["y"], {"value": None, "unit": "mm", "exact": False})

    def test_labels_name_values_units_and_outcome(self):
        sample = {"parameters": {"l2_shift": {"value": 0.54, "unit": "mm"}},
                  "y": {"value": 1.9964496422827251, "unit": "mm", "exact": True}, "outcome": "PASS"}
        parameters = [{"name": "l2_shift", "unit": "mm"}]
        theme = pr.resolve_theme({})
        self.assertEqual(an.sample_label(theme, sample, parameters, "target:expanded_waist"),
                         "l2_shift = 0.54 mm · expanded_waist = 1.99645 mm · PASS")
        microns = pr.resolve_theme({"units": {"mm": "um"}, "labels": {"param:l2_shift": "L2 shift"}})
        self.assertEqual(an.sample_label(microns, sample, parameters, "target:expanded_waist"),
                         "L2 shift = 540 um · expanded_waist = 1996.45 um · PASS")
        empty = {**sample, "y": {"value": None, "unit": "mm", "exact": False}, "outcome": "UNKNOWN"}
        label = an.sample_label(theme, empty, parameters, "target:expanded_waist")
        self.assertIn("expanded_waist: no exact value", label)
        self.assertNotIn("= 0 ", label)
        chinese = pr.resolve_theme({"language": "zh-CN"})
        self.assertIn("expanded_waist：无精确值", an.sample_label(chinese, empty, parameters, "target:expanded_waist"))

    def test_unknown_column_and_missing_study_are_refused_with_the_columns(self):
        study_dir = fake_study(self.dir / "study", [(1.0, 1.2, "PASS")])
        with self.assertRaises(pr.PresentationError) as caught:
            an.study_samples(study_dir, "target:spot")
        self.assertEqual(caught.exception.code, "COLUMN_UNKNOWN")
        self.assertIn("target:waist", str(caught.exception))
        with self.assertRaises(pr.PresentationError) as caught:
            an.study_samples(self.dir / "nowhere", "target:waist")
        self.assertEqual(caught.exception.code, "SOURCE_MISSING")

    def test_every_text_key_has_both_languages(self):
        self.assertEqual(set(an.TEXT["en"]), set(an.TEXT["zh-CN"]))


class SettingsTests(unittest.TestCase):
    def test_defaults_and_refusals(self):
        cfg = an.normalize({"id": "o", "kind": "animation", "mode": "orbit", "source": "run"})
        self.assertEqual((cfg["formats"], cfg["fps"], cfg["gif_fps"], cfg["duration_s"]), (["mp4", "gif"], 24, 15, 6))
        self.assertEqual(cfg["orbit"]["about"], "up")
        with self.assertRaises(pr.PresentationError) as caught:
            an.normalize({"id": "o", "kind": "animation", "mode": "orbit", "source": "run", "speed": 2})
        self.assertEqual(caught.exception.code, "SETTING_UNKNOWN")
        with self.assertRaises(pr.PresentationError) as caught:
            an.normalize({"id": "o", "kind": "animation", "mode": "orbit", "source": "run", "y": "target:x"})
        self.assertEqual(caught.exception.code, "SETTING_INVALID")
        with self.assertRaises(pr.PresentationError):
            an.normalize({"id": "t", "kind": "animation", "mode": "transition", "source": "run"})  # no end pose
        self.assertIn("orbit", an.settings()["modes"])
        self.assertEqual(an.settings()["fields"]["formats"]["default"], ["mp4", "gif"])

    def test_odd_size_is_refused_for_mp4_only(self):
        self.assertEqual(an.check_size((320, 180), ["mp4", "gif"]), (320, 180))
        self.assertEqual(an.check_size((321, 181), ["gif"]), (321, 181))
        with self.assertRaises(pr.PresentationError) as caught:
            an.check_size((321, 180), ["mp4"])
        self.assertEqual(caught.exception.code, "SETTING_INVALID")
        self.assertIn("322x180", caught.exception.details["recovery"])

    def test_missing_media_extra_names_the_install_step(self):
        with mock.patch.dict(sys.modules, {"imageio_ffmpeg": None}):
            with self.assertRaises(pr.PresentationError) as caught:
                an.media_modules(["mp4", "gif"])
        self.assertEqual(caught.exception.code, "MEDIA_EXTRA_MISSING")
        self.assertEqual(caught.exception.details["recovery"], an.MEDIA_RECOVERY)


# --------------------------------------------------------------------------------------------- media without the engine

class PlotPlaybackMediaTests(unittest.TestCase):
    """A plot-only playback needs no engine or browser: frames, media, sequence and replay."""

    def setUp(self):
        require(self, HAS_MEDIA, "matplotlib, Pillow and imageio-ffmpeg")
        self._tmp = TempDir()
        self.dir = self._tmp.__enter__()
        self.addCleanup(self._tmp.__exit__, None, None, None)
        self.study = fake_study(self.dir / "study", [(2.0, 1.5, "PASS"), (0.5, None, "ERROR"), (1.0, 1.2, "FAIL")])
        self.ctx = Context(self.dir / "out", {"gap": {"path": self.study}}, theme={"size": "320x180"})
        self.asset = {"id": "gap-playback", "kind": "animation", "mode": "playback", "source": "gap",
                      "y": "target:waist", "panels": "plot", "hold_s": 0.5, "fps": 8}

    def test_frames_media_and_saved_sequence(self):
        import imageio
        from PIL import Image

        result = an.build_animation(self.asset, self.ctx)
        files = result["files"]
        self.assertEqual(set(files), {"image", "mp4", "gif", "sequence"})
        sequence = json.loads(files["sequence"].read_text(encoding="utf-8"))
        self.assertEqual([s["sample_id"] for s in sequence["segments"]], ["s0002", "s0003", "s0001"])
        self.assertEqual(sequence["frame_count"], 12)
        self.assertIn("gap = 0.5 mm · waist: no exact value · ERROR", sequence["segments"][0]["label"])
        self.assertIn("gap = 1 mm · waist = 1.2 mm · FAIL", sequence["segments"][1]["label"])
        self.assertFalse(any(Path(v).is_absolute() for v in sequence["outputs"].values()))
        self.assertNotIn(str(self.dir), files["sequence"].read_text(encoding="utf-8"))
        data = json.loads(result["data_files"][0].read_text(encoding="utf-8"))
        self.assertEqual([s["y"]["value"] for s in data["samples"]], [None, 1.2, 1.5])
        reader = imageio.get_reader(files["mp4"], "ffmpeg")
        frames = [frame.shape for frame in reader]
        reader.close()
        self.assertEqual(len(frames), 12)
        self.assertEqual(frames[0][:2], (180, 320))
        with Image.open(files["gif"]) as gif:
            self.assertEqual((gif.size, gif.n_frames), ((320, 180), 3))
        with Image.open(files["image"]) as still:
            self.assertEqual(still.size, (320, 180))
        details = result["details"]
        self.assertEqual((details["samples"], details["missing_samples"], details["no_value_samples"]),
                         (3, ["s0002", "s0003", "s0001"], ["s0002"]))  # no saved runs
        for language in pr.LANGUAGES:  # stated once in the caption and once on each frame, not in the title
            note = pr.text("caption.recorded_samples", language)
            self.assertEqual(result["caption"][language].count(note), 1)
            self.assertNotIn(note, result["title"][language])
        self.assertTrue(result["caption"]["en"][0].isupper() or result["caption"]["en"][0].isdigit())
        self.assertTrue(self.ctx.progress_calls)

        # replay the saved sequence in another language with GIF only: same order, timing and values
        if pr.missing_glyphs(["输出束腰"], pr.resolve_theme({"language": "zh-CN"}).font_family):
            self.skipTest("no installed font draws Chinese")
        saved = self.dir / "saved-sequence.json"
        saved.write_bytes(files["sequence"].read_bytes())
        replay = {"id": "replayed", "kind": "animation", "mode": "playback", "source": "gap", "y": "target:waist",
                  "sequence": os.path.relpath(saved, self.ctx.spec_dir), "formats": ["gif"],
                  "theme": {"language": "zh-CN", "background": "dark"}}
        again = an.build_animation(replay, self.ctx)
        replayed = json.loads(again["files"]["sequence"].read_text(encoding="utf-8"))
        for key in ("sample_id", "duration_s", "frames", "values"):
            self.assertEqual([s.get(key) for s in replayed["segments"]], [s.get(key) for s in sequence["segments"]])
        self.assertIn("waist：无精确值", replayed["segments"][0]["label"])
        self.assertEqual(set(again["files"]), {"image", "gif", "sequence"})

    def test_short_holds_keep_every_sample_in_every_format(self):
        import imageio
        from PIL import Image

        asset = {**self.asset, "hold_s": 0.1, "fps": 4, "gif_fps": 2}  # 0.1 s is shorter than one frame at 4 fps
        result = an.build_animation(asset, self.ctx)
        sequence = json.loads(result["files"]["sequence"].read_text(encoding="utf-8"))
        self.assertEqual([s["frames"] for s in sequence["segments"]], [1, 1, 1])
        self.assertEqual([s["duration_s"] for s in sequence["segments"]], [0.25, 0.25, 0.25])
        self.assertEqual(sequence["hold_s_applied"], 0.25)
        self.assertEqual((result["details"]["hold_s"], result["details"]["hold_s_applied"]), (0.1, 0.25))
        self.assertIn("0.25 s", result["caption"]["en"])
        reader = imageio.get_reader(result["files"]["mp4"], "ffmpeg")
        frames = [frame for frame in reader]
        reader.close()
        self.assertEqual(len(frames), 3)
        self.assertEqual(len({frame.tobytes() for frame in frames}), 3)  # three different samples
        with Image.open(result["files"]["gif"]) as gif:
            self.assertEqual(gif.n_frames, 3)
        self.assertEqual(sequence["gif"]["segments"], [0, 1, 2])

    def test_plot_joins_samples_only_within_one_combination_of_the_other_parameters(self):
        study_dir = fake_study(self.dir / "two", [(0.5, 1.1, "FAIL"), (1.0, 1.4, "PASS"), (0.5, 1.8, "PASS"),
                                                  (1.0, 1.9, "PASS"), (1.5, 1.3, "FAIL")],
                               wavelengths=[780.0, 780.0, 1064.0, 1064.0, 1064.0])
        theme = pr.resolve_theme({"size": "640x360"})
        study = an.study_samples(study_dir, "target:waist")
        order = an.playback_order(study["samples"], "gap")
        model = an.plot_model(theme, study, order)
        self.assertEqual(model["groups"], ["wavelength = 780 nm", "wavelength = 1064 nm"])
        figure = an._figure(theme, (640, 360))
        plan_layout = an.layout(theme, (640, 360), "playback", "plot", True, False)
        an._draw_plot(figure, theme, plan_layout, model, 0)
        lines = [line for line in figure.axes[0].lines if not line.get_label().startswith("_")]
        drawn = {line.get_label(): (list(line.get_xdata()), list(line.get_ydata())) for line in lines}
        self.assertEqual(drawn, {"wavelength = 780 nm": ([0.5, 1.0], [1.1, 1.4]),
                                 "wavelength = 1064 nm": ([0.5, 1.0, 1.5], [1.8, 1.9, 1.3])})
        self.assertIn("wavelength = 780 nm", an.legend_labels(theme, model, ["PASS", "FAIL"]))
        single = an.plot_model(theme, *(lambda st: (st, an.playback_order(st["samples"], "gap")))(
            an.study_samples(self.study, "target:waist")))
        self.assertEqual(single["groups"], [])

    def test_frame_text_stays_inside_the_canvas_at_large_font_scales(self):
        import matplotlib
        from matplotlib.text import Text

        study_dir = fake_study(self.dir / "wide", [(0.5, 1.1, "FAIL"), (1.0, 1.4, "PASS"), (0.5, None, "ERROR"),
                                                   (1.0, 1.9, "UNKNOWN"), (1.5, 1.3, "OUT_OF_MODEL")],
                               wavelengths=[780.0, 780.0, 1064.0, 1064.0, 1064.0])
        labels = {"param:gap": {"en": "Lens gap after the collimator", "zh-CN": "准直器后的透镜间距"},
                  "target:waist": {"en": "Output waist radius at the far plane", "zh-CN": "远场面的输出束腰半径"}}
        cases = [(language, scale, size) for language in pr.LANGUAGES for scale in (1.4, 2.0)
                 for size in ("1920x1080", "3840x2160", "1080x1080")]
        checked = 0
        for language, scale, size in cases:
            theme = pr.resolve_theme({"language": language, "font_scale": scale, "size": size, "labels": labels})
            if pr.missing_glyphs(["输出束腰"], theme.font_family) and language == "zh-CN":
                continue
            study = an.study_samples(study_dir, "target:waist")
            order = an.playback_order(study["samples"], "gap")
            model = an.plot_model(theme, study, order)
            plan_layout = an.layout(theme, theme.size, "playback", "view+plot", True, False)
            lines = [an.sample_label(theme, order[1], study["parameters"], "target:waist"),
                     an.SEPARATOR.join([an.text("label.sample", language, index=2, count=5),
                                        theme.text("caption.recorded_samples")])]
            with matplotlib.rc_context(theme.rc()):
                figure = an.overlay_figure(theme, plan_layout, "Output waist radius at the far plane vs lens gap "
                                           "after the collimator", lines, plot=model, current=1,
                                           note=an.text("note.no_run", language))
                figure.canvas.draw()
                renderer = figure.canvas.get_renderer()
                width, height = theme.size
                texts = [t for t in figure.findobj(Text) if t.get_visible() and t.get_text().strip()]
                pr.check_glyphs([t.get_text() for t in texts], theme)
                # the extent of everything drawn (ticks outside the axis range are not drawn)
                box = figure.get_tightbbox(renderer).transformed(figure.dpi_scale_trans)
                with self.subTest(language=language, scale=scale, size=size):
                    self.assertGreaterEqual(box.x0, -1)
                    self.assertLessEqual(box.x1, width + 1)
                    self.assertGreaterEqual(box.y0, -1)
                    self.assertLessEqual(box.y1, height + 1)
                axes = figure.axes[0]
                self.assertGreater(axes.get_window_extent(renderer).height, 0.12 * plan_layout["plot"][3])
                checked += 1
        self.assertGreaterEqual(checked, 6)

    def test_unknown_column_lists_the_columns(self):
        with self.assertRaises(pr.PresentationError) as caught:
            an.build_animation({**self.asset, "y": "target:spot"}, self.ctx)
        self.assertEqual(caught.exception.code, "COLUMN_UNKNOWN")
        self.assertEqual(caught.exception.details["columns"], ["param:gap", "target:waist"])

    def test_run_mode_on_a_study_is_refused(self):
        orbit = {"id": "orbit", "kind": "animation", "mode": "orbit", "source": "gap"}
        with self.assertRaises(pr.PresentationError) as caught:
            an.build_animation(orbit, self.ctx)
        self.assertEqual(caught.exception.code, "SOURCE_MISSING")


# --------------------------------------------------------------------------------------------- engine and browser

class SavedRunAnimationTests(unittest.TestCase):
    """Orbit, transition into element:L2 and playback from small saved runs, captured by the engine's viewer."""

    @classmethod
    def setUpClass(cls):
        cls.browser = rv.find_browser(None)
        cls.ready = HAS_ENGINE and HAS_MEDIA and HAS_PLAYWRIGHT and bool(cls.browser)
        if not cls.ready:
            return
        from os_optics_kit import sweep_parameters as sp

        cls._tmp = TempDir()
        cls.dir = cls._tmp.__enter__()
        spec = {"parameters": [{"name": "l2_x", "unit": "mm", "values": [201.0, 0.5, 200.0],
                                "ops": [{"op": "set_component_pose", "instance_id": "L2",
                                         "position_mm": ["$value", 0, 0]}]}],
                "max_evaluations": 3}
        sp.run_sweep(spec, cls.dir / "study", base=expander())
        cls.run_dir = cls.dir / "study" / "samples" / "s0003" / "run"
        sources = {"run": {"path": cls.run_dir, "label": {"en": "Expander", "zh-CN": "扩束器"},
                           "identity": pr.source_identity("run", cls.run_dir)},
                   "study": {"path": cls.dir / "study"}}
        cls.ctx = Context(cls.dir / "out", sources, theme={"size": "320x180"}, browser=cls.browser)

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "_tmp", None):
            cls._tmp.__exit__(None, None, None)

    def setUp(self):
        require(self, self.ready, "the engine, Playwright, Chrome/Chromium, matplotlib and imageio-ffmpeg")

    def read_media(self, files):
        import imageio
        from PIL import Image

        reader = imageio.get_reader(files["mp4"], "ffmpeg")
        shapes = [frame.shape for frame in reader]
        reader.close()
        with Image.open(files["gif"]) as gif:
            gif_facts = (gif.size, gif.n_frames)
        with Image.open(files["image"]) as still:
            still_size = still.size
        return shapes, gif_facts, still_size

    def test_orbit(self):
        asset = {"id": "orbit", "kind": "animation", "mode": "orbit", "source": "run", "duration_s": 1.0, "fps": 6,
                 "orbit": {"sweep_deg": 360, "elevation_deg": 15}}
        result = an.build_animation(asset, self.ctx)
        shapes, (gif_size, gif_frames), still_size = self.read_media(result["files"])
        self.assertEqual(len(shapes), 6)
        self.assertEqual(shapes[0][:2], (180, 320))
        self.assertEqual((gif_size, still_size), ((320, 180), (320, 180)))
        self.assertLessEqual(gif_frames, 6)
        sequence = json.loads(result["files"]["sequence"].read_text(encoding="utf-8"))
        angles = [shot["angle_deg"] for shot in sequence["shots"]]
        self.assertEqual(angles, [55.0 + 60.0 * i for i in range(6)])  # frame order follows the orbit
        self.assertEqual(len({shot["distance_mm"] for shot in sequence["shots"]}), 1)  # one scale
        self.assertEqual(len({json.dumps(shot["box_min"]) for shot in sequence["shots"]}), 1)
        self.assertEqual(result["details"]["views_captured"], 6)
        self.assertEqual(result["title"]["en"], "Expander: orbit view")
        self.assertTrue(result["caption"]["en"].startswith("Camera turning 360° about the vertical around Expander"))

    def test_transition_into_l2(self):
        asset = {"id": "zoom-l2", "kind": "animation", "mode": "transition", "source": "run",
                 "from": {"camera": [55, 22], "focus": "optics"}, "to": {"camera": [35, 12], "focus": "element:L2"},
                 "duration_s": 1.0, "hold_s": 0.5, "fps": 6}
        result = an.build_animation(asset, self.ctx)
        shapes, (gif_size, _gif_frames), _still = self.read_media(result["files"])
        self.assertEqual(len(shapes), 12)
        self.assertEqual(gif_size, (320, 180))
        sequence = json.loads(result["files"]["sequence"].read_text(encoding="utf-8"))
        view = rv.ViewScene(self.run_dir)
        low, high = view.box("element:L2")
        last = sequence["shots"][-1]
        self.assertEqual(last["box_min"], [round(v, 6) for v in low])
        self.assertEqual(last["box_max"], [round(v, 6) for v in high])
        self.assertEqual([s["kind"] for s in sequence["segments"]], ["hold"] + ["motion"] * 6 + ["hold"])
        self.assertEqual([s["frames"] for s in sequence["segments"]], [3] + [1] * 6 + [3])
        distances = [shot["distance_mm"] for shot in sequence["shots"]]
        self.assertEqual(distances, sorted(distances, reverse=True))
        self.assertIn("L2", result["title"]["en"])
        self.assertIn("L2", result["caption"]["zh-CN"])
        self.assertTrue(result["caption"]["en"].startswith("Camera move on Expander"))
        self.assertIn("in 1 s, held 0.5 s at each end; 2 s at 6 fps", result["caption"]["en"])

    def test_playback_with_missing_sample_and_cached_restyle(self):
        asset = {"id": "l2-playback", "kind": "animation", "mode": "playback", "source": "study",
                 "y": "target:expanded_radius", "panels": "view+plot", "hold_s": 0.5, "fps": 4}
        result = an.build_animation(asset, self.ctx)
        details = result["details"]
        self.assertEqual(details["order"], ["s0002", "s0003", "s0001"])  # 0.5, 200, 201 mm
        self.assertEqual(details["missing_samples"], ["s0002"])
        self.assertEqual(details["views_captured"], 2)
        sequence = json.loads(result["files"]["sequence"].read_text(encoding="utf-8"))
        self.assertIsNone(sequence["segments"][0]["shot"])
        self.assertIn("l2_x = 0.5 mm", sequence["segments"][0]["label"])
        shots = sequence["shots"]
        for key in ("direction", "up", "box_min", "box_max", "distance_mm"):
            self.assertEqual(len({json.dumps(shot[key]) for shot in shots}), 1, key)  # one camera, one scale
        shapes, (gif_size, gif_frames), _still = self.read_media(result["files"])
        self.assertEqual((len(shapes), gif_size, gif_frames), (6, (320, 180), 3))

        # a restyle of the text (title, labels) re-encodes from the cached views without opening a browser
        theme = {"labels": {"param:l2_x": {"en": "L2 position", "zh-CN": "L2 位置"}}}
        restyled = copy.deepcopy(asset)
        restyled["title"] = {"en": "L2 position", "zh-CN": "L2 位置"}
        restyled["theme"] = theme
        with mock.patch.object(an.rv, "BrowserSession", side_effect=AssertionError("browser opened")):
            again = an.build_animation(restyled, self.ctx)
        self.assertEqual((again["details"]["views_captured"], again["details"]["views_reused"]), (0, 2))
        replayed = json.loads(again["files"]["sequence"].read_text(encoding="utf-8"))
        self.assertIn("L2 position = 200 mm", replayed["segments"][1]["label"])


if __name__ == "__main__":
    unittest.main()
