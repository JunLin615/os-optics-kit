"""field-points with the Engine on the aspheric-lens case (catalog-free, a real aperture stop in front of five
elements): off-axis chief rays against the case's own stop-aimed sources and f tan(theta), an image-height solve, both
MTF directions against a direct imaging call, a field beyond the system, repeatability and the refusals."""

from __future__ import annotations

import hashlib
import json
import math
import unittest

from os_optics_kit import field_points as fp
from os_optics_kit import imaging_results as ir

from workflow_fixtures import HAS_ENGINE, ROOT, TempDir, invoke, requires_engine

CASE = ROOT / "cases" / "aspheric-lens" / "project.json"
PLANE = "image_plane"
REFERENCE = "SRC0_1"  # on axis, 587.5618 nm
#: the case's own imaging settings with a smaller launch grid (seconds, not minutes)
IMAGING = {"launch_radius_beam_radii": 1.0, "psf_samples": 128, "psf_half_width_um": 250}
#: the first-order comparison this five-element lens meets at 12.5 deg (relative to f tan(theta))
FIRST_ORDER_TOLERANCE = 0.01


def _request(**changes) -> fp.Request:
    values = {"project": CASE, "plane": PLANE, "kind": "ANGULAR", "values": [0.0, 12.5, 80.0], "source": REFERENCE,
              "mtf_frequencies": [20.0], "launch_grid": 48, "imaging_request": dict(IMAGING)}
    values.update(changes)
    return fp.Request(**values)


def _numbers(report: dict) -> list:
    return [(p["status"], p["reason"], p["chief_ray"]["image_point_uv_mm"], p["chief_ray"]["angle_deg"],
             p["chief_ray"]["stop_offset_mm"], [(s["direction"], s["value"], s["value_kind"]) for s in p["mtf"]])
            for p in report["points"]]


@requires_engine
class AngularFieldPoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from os_optics.local import EvaluationSession

        cls.tmp = TempDir()
        cls.base = cls.tmp.__enter__()
        cls.session = EvaluationSession()
        cls.report = fp.run(_request(), cls.base / "a", session=cls.session)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.__exit__(None, None, None)

    def point(self, index):
        return self.report["points"][index]

    def test_an_off_axis_chief_ray_lands_where_the_cases_stop_aimed_source_lands(self):
        # the case turns its 12.5 deg sources about the stop centre (its own field points): same chief ray
        case = json.loads(CASE.read_text(encoding="utf-8"))
        monitor = self.session.evaluate(case, outputs=("readings",)).results().monitor(PLANE)
        own = next(a for a in monitor["arrivals"] if a["beam"]["path_id"] == "SRC1_1#0")
        chief = self.point(1)["chief_ray"]
        self.assertEqual((self.point(1)["status"], chief["aim"]), ("EVALUATED", "AIMED"))
        self.assertLessEqual(chief["stop_offset_mm"], fp.DEFAULT_AIM_TOLERANCE_MM)
        self.assertAlmostEqual(chief["image_height_mm"], math.hypot(*own["uv_mm"]), delta=1e-6)
        self.assertAlmostEqual(chief["angle_deg"], own["aoi_deg"], delta=1e-6)
        self.assertAlmostEqual(self.point(1)["field"]["angle_deg"], 12.5, places=9)
        self.assertEqual(self.report["conditions"]["requested"], [0.0, 12.5, 80.0])

    def test_the_chief_ray_height_is_compared_with_f_tan_theta(self):
        first = self.point(1)["first_order"]
        self.assertEqual((first["kind"], first["label"]), ("EFL_TAN_THETA", "FIRST_ORDER_COMPARISON"))
        efl = self.report["conditions"]["first_order"]["efl_mm"]
        self.assertAlmostEqual(first["reference_height_mm"], efl * math.tan(math.radians(12.5)), places=9)
        self.assertLess(abs(first["relative_difference"]), FIRST_ORDER_TOLERANCE)
        self.assertEqual(self.point(0)["chief_ray"]["image_height_mm"], 0.0)

    def test_both_mtf_directions_equal_a_direct_imaging_call(self):
        entry = self.report["imaging"][0]
        evaluated = json.loads((self.base / "a" / "evaluated-project.json").read_text(encoding="utf-8"))
        direct = ir.load(self.session.evaluate(evaluated, outputs=("readings",)).results().imaging(PLANE,
                                                                                                  entry["request"]))
        for sample in self.point(1)["mtf"]:
            with self.subTest(direction=sample["direction"]):
                value, _status = ir.value_at(ir.curve(direct, "FP1#0", "mtf", sample["direction"]), 20.0)
                self.assertEqual(sample["value"], value)
                self.assertEqual(sample["value_kind"], "EXACT")
        self.assertEqual({s["direction"] for s in self.point(1)["mtf"]}, {"tangential", "sagittal"})

    def test_a_field_beyond_the_system_is_not_evaluated_and_leaves_the_minimum_unconfirmed(self):
        beyond = self.point(2)
        self.assertEqual((beyond["status"], beyond["reason"]), ("NOT_EVALUATED", "CHIEF_RAY_PATH_DIFFERS"))
        self.assertTrue(all(s["reason"] == "CHIEF_RAY_PATH_DIFFERS" for s in beyond["mtf"]))
        minimum = self.report["minima"][0]["all_lines"]
        self.assertEqual((minimum["status"], minimum["bound"]), ("UNCONFIRMED", "UPPER"))
        self.assertEqual({m["point_index"] for m in minimum["missing"]}, {2})
        exact = [s["value"] for p in self.report["points"][:2] for s in p["mtf"]]
        self.assertEqual(minimum["value"], min(exact))
        self.assertEqual(self.report["status"], "INCOMPLETE")
        self.assertFalse(self.report["plane"]["widened"])  # the 80 deg chief ray is not a field of the plane

    def test_a_repeated_call_gives_the_same_numbers(self):
        again = fp.run(_request(), self.base / "b")
        self.assertEqual(_numbers(again), _numbers(self.report))
        self.assertEqual(again["evaluated"]["sha256"], self.report["evaluated"]["sha256"])

    def test_the_report_binds_the_input_and_the_evaluated_project(self):
        data = CASE.read_bytes()
        self.assertEqual(self.report["input"]["sha256_lf"], hashlib.sha256(
            data.replace(b"\r\n", b"\n")).hexdigest())
        derivation = self.report["evaluated"]["derivation"]
        self.assertEqual(len(derivation["removed_sources"]), 9)
        self.assertEqual([s["source_id"] for s in derivation["added_sources"]], ["FP0", "FP1", "FP2"])
        self.assertEqual(json.loads(CASE.read_text(encoding="utf-8"))["components"][1]["instance_id"], "SRC0_0")


@requires_engine
class ImageHeightsAndConjugates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from os_optics.local import EvaluationSession

        cls.tmp = TempDir()
        cls.base = cls.tmp.__enter__()
        cls.session = EvaluationSession()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.__exit__(None, None, None)

    def test_an_image_height_is_solved_to_its_tolerance(self):
        report = fp.run(_request(kind="IMAGE_HEIGHT", values=[0.0, 8.0], mtf_frequencies=None, launch_grid=None,
                                 imaging_request=None), self.base / "h", session=self.session)
        point = report["points"][1]
        self.assertEqual(point["status"], "EVALUATED")
        self.assertLessEqual(abs(point["chief_ray"]["image_height_mm"] - 8.0), fp.DEFAULT_HEIGHT_TOLERANCE_MM)
        self.assertGreater(point["field"]["angle_deg"], 0.0)
        self.assertEqual(report["status"], "COMPLETED")

    def test_object_heights_need_a_finite_object(self):
        project = json.loads(CASE.read_text(encoding="utf-8"))
        reference = next(c for c in project["components"] if c["instance_id"] == REFERENCE)
        reference["source_model"].update(waist_radius_mm=0.002, waist_position_mm=-500.0)
        finite = self.base / "finite.json"
        finite.write_text(json.dumps(project), encoding="utf-8")
        report = fp.run(_request(project=finite, kind="OBJECT_HEIGHT", values=[0.0, 20.0], mtf_frequencies=None,
                                 launch_grid=None, imaging_request=None), self.base / "o", session=self.session)
        self.assertEqual(report["conditions"]["conjugate"]["kind"], "FINITE")
        point = report["points"][1]
        self.assertAlmostEqual(point["field"]["object_height_mm"], 20.0, places=9)
        self.assertEqual(point["chief_ray"]["aim"], "AIMED")
        self.assertEqual(point["first_order"]["kind"], "MAGNIFICATION_TIMES_HEIGHT")
        self.assertLess(point["first_order"]["magnification"], 0.0)  # a real, inverted image
        code, _out, err = invoke(fp.main, [str(CASE), "--plane", PLANE, "--source", REFERENCE, "--out",
                                           str(self.base / "x"), "--object-heights-mm", "1"])
        self.assertEqual(code, 4)
        self.assertIn('"OBJECT_AT_INFINITY"', err)

    def test_refusals_name_what_to_state(self):
        cases = {"SOURCE_REQUIRED": ["--angles-deg", "5"],
                 "PLANE_NOT_FOUND": ["--source", REFERENCE, "--angles-deg", "5", "--plane", "nope"],
                 "FIELD_KIND_MISMATCH": ["--source", REFERENCE, "--angles-deg", "5", "--expect-field-kind",
                                         "OBJECT_HEIGHT"]}
        for code_name, extra in cases.items():
            with self.subTest(code=code_name):
                argv = [str(CASE), "--plane", PLANE, "--out", str(self.base / code_name), *extra]
                code, out, err = invoke(fp.main, argv)
                self.assertEqual((code, out), (4, None))
                self.assertEqual(json.loads(err)["error"]["code"], code_name)

    def test_a_pivot_turns_unaimed_points_and_the_written_run_binds_the_imaging(self):
        # the ideal thin lens has no aperture stop: the points turn about its centre and are NOT_AIMED; its MTF is
        # outside the imaging model (no optical path length), which the report keeps as the reason
        from os_optics.results import load_run

        ideal = ROOT / "cases" / "ideal-lens-focus" / "project.json"
        report = fp.run(fp.Request(project=ideal, plane="focus", kind="ANGULAR", values=[0.0, 3.0],
                                   pivot_mm=[10.0, 0.0, 20.0], mtf_frequencies=[10.0], launch_grid=16,
                                   convergence=False, write_run=True), self.base / "r", session=self.session)
        point = report["points"][1]
        self.assertEqual((point["status"], point["chief_ray"]["aim"]), ("EVALUATED", "NOT_AIMED"))
        self.assertAlmostEqual(point["first_order"]["relative_difference"], 0.0, delta=1e-6)  # ideal: h = f tan(theta)
        self.assertEqual({s["reason"] for s in point["mtf"]}, {"RAY_OPL_UNDEFINED"})
        run = load_run(self.base / "r" / "run").identity()
        self.assertEqual(run["engine_input_hash"], report["evaluated"]["engine_input_hash"])
        self.assertEqual(report["imaging"][0]["identity"]["run_result_hash"], run["result_hash"])
        project = json.loads((self.base / "r" / "run" / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(c["instance_id"] for c in project["components"] if c["optical_role"] == "source"),
                         ["FP0", "FP1"])

if __name__ == "__main__" and HAS_ENGINE:
    unittest.main()
