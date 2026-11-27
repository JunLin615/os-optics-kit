"""field-points without the Engine: field-point geometry, the object conjugate rule, aiming, the first-order
reference, the sampled minima and the refusals that come before any Engine use."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import math
import sys
import unittest
from unittest import mock

from os_optics_kit import field_aberrations as fa
from os_optics_kit import field_geometry as fg
from os_optics_kit import field_points as fp
from os_optics_kit import geometry3d as g
from os_optics_kit import imaging_results as ir

from workflow_fixtures import BEAM_ALONG_X, invoke

U_AXIS, V_AXIS = (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)  # an image plane posed with BEAM_ALONG_X


def gaussian(waist_radius_mm=1.0, waist_position_mm=0.0, position=(-50.0, 0.0, 0.0), **model):
    return {"instance_id": "SRC", "optical_role": "source",
            "nominal_pose": {"position_mm": list(position), "quaternion_wxyz": list(BEAM_ALONG_X)},
            "source_model": {"model_kind": "GAUSSIAN_BEAM", "wavelength_nm": 587.56, "power_w": 0.001,
                             "waist_radius_mm": waist_radius_mm, "waist_position_mm": waist_position_mm, "m2": 1.0,
                             **model}}


def frame_of(source, azimuth_deg=0.0, centre=(0.0, 0.0, 0.0)):
    conj = fg.conjugate(source, 587.56)
    return fg.make_frame(source, conj, U_AXIS, V_AXIS, azimuth_deg, centre)


class GeometryTests(unittest.TestCase):
    def test_the_field_direction_follows_the_azimuth_in_the_planes_frame(self):
        axis = (1.0, 0.0, 0.0)
        for azimuth, expected in ((0.0, U_AXIS), (90.0, V_AXIS), (180.0, (0.0, -1.0, 0.0))):
            direction = fg.field_direction(axis, U_AXIS, V_AXIS, azimuth)
            for k in range(3):
                self.assertAlmostEqual(direction[k], expected[k], places=12)
        diagonal = fg.field_direction(axis, U_AXIS, V_AXIS, math.degrees(math.atan2(12, 18)))
        self.assertAlmostEqual(diagonal[2] / diagonal[1], 12 / 18, places=12)

    def test_an_angular_field_point_is_turned_across_the_beam_through_the_aim_centre(self):
        frame = frame_of(gaussian(), azimuth_deg=90.0, centre=(10.0, 0.0, 0.0))
        placed = fg.pose(frame, fg.LINE, 5.0, [0.0, 0.0])
        axis = placed["axis"]
        self.assertAlmostEqual(g.angle_deg(axis, frame.axis), 5.0, places=10)
        self.assertGreater(axis[2], 0.0)  # turned toward +v (azimuth 90)
        turn = g.qmul(placed["quaternion_wxyz"], [BEAM_ALONG_X[0], *(-c for c in BEAM_ALONG_X[1:])])
        self.assertAlmostEqual(g.dot(turn[1:], frame.axis), 0.0, places=12)  # the turn axis lies across the beam
        # the source origin stays in the reference's station plane, and its axis line passes the aim centre
        self.assertAlmostEqual(placed["position_mm"][0], -50.0, places=10)
        to_centre = g.sub(frame.centre, placed["position_mm"])
        self.assertAlmostEqual(g.norm(g.across(to_centre, axis)), 0.0, places=10)
        self.assertEqual(fg.verify_turned(placed["quaternion_wxyz"], frame, axis, 5.0), fg.verify_turned(
            placed["quaternion_wxyz"], frame, axis))

    def test_a_shifted_or_self_turned_source_is_refused(self):
        frame = frame_of(gaussian())
        intended = fg.angular_axis(frame, 5.0)
        with self.assertRaises(fg.FieldGeometryError) as shifted:
            fg.verify_turned(list(BEAM_ALONG_X), frame, intended, 5.0)  # moved, not turned
        self.assertEqual(shifted.exception.code, "FIELD_POINT_NOT_TURNED")
        own_axis = g.qmul(list(BEAM_ALONG_X), g.axis_angle((0.0, 0.0, 1.0), math.radians(5.0)))  # about local +Z
        with self.assertRaises(fg.FieldGeometryError):
            fg.verify_turned(own_axis, frame, intended, 5.0)

    def test_an_object_height_moves_the_object_point_and_aims_from_it(self):
        source = gaussian(waist_radius_mm=0.005, waist_position_mm=-200.0)
        frame = frame_of(source, centre=(20.0, 0.0, 0.0))
        self.assertEqual(frame.conjugate, fg.FINITE)
        self.assertEqual(frame.object_point, [-250.0, 0.0, 0.0])
        placed = fg.pose(frame, fg.POINT, 3.0, [0.0, 0.0])
        self.assertEqual(placed["object_point_mm"], [-250.0, 3.0, 0.0])
        waist = g.add(placed["position_mm"], g.scale(placed["axis"], -200.0))
        for k in range(3):
            self.assertAlmostEqual(waist[k], placed["object_point_mm"][k], places=9)
        self.assertAlmostEqual(g.norm(g.across(g.sub(frame.centre, placed["object_point_mm"]), placed["axis"])), 0.0,
                               places=10)
        expected = math.degrees(math.atan2(3.0, 270.0))
        self.assertAlmostEqual(fg.verify_turned(placed["quaternion_wxyz"], frame, placed["axis"]), expected, places=9)

    def test_an_angle_at_a_finite_object_puts_its_object_point_on_the_chief_ray(self):
        frame = frame_of(gaussian(waist_radius_mm=0.005, waist_position_mm=-200.0))
        placed = fg.pose(frame, fg.LINE, 10.0, [0.0, 0.0])
        obj = placed["object_point_mm"]
        self.assertAlmostEqual(obj[0], -250.0, places=9)
        self.assertAlmostEqual(obj[1], -250.0 * math.tan(math.radians(10.0)), places=9)

    def test_the_conjugate_rule_reads_the_reference_waist(self):
        self.assertEqual(fg.conjugate(gaussian(waist_position_mm=100.0), 587.56)["kind"], fg.INFINITE)
        finite = fg.conjugate(gaussian(waist_radius_mm=0.005, waist_position_mm=-100.0), 587.56)
        self.assertEqual(finite["kind"], fg.FINITE)
        self.assertAlmostEqual(finite["rayleigh_range_mm"], math.pi * 0.005 ** 2 / 587.56e-6, places=12)
        self.assertLess(finite["waist_position_rayleigh_ranges"], -fg.FINITE_RAYLEIGH_RANGES)
        z_r = math.pi / 587.56e-6
        with self.assertRaises(fg.FieldGeometryError) as between:
            fg.conjugate(gaussian(waist_position_mm=-3.0 * z_r), 587.56)
        self.assertEqual(between.exception.code, "CONJUGATE_UNDETERMINED")
        flat = gaussian()
        flat["source_model"] = {"model_kind": "EXTENDED_EMITTER"}
        with self.assertRaises(fg.FieldGeometryError) as other:
            fg.conjugate(flat, 587.56)
        self.assertEqual(other.exception.code, "SOURCE_UNSUPPORTED")

    def test_the_pupil_estimate_and_the_first_aim_pass_the_entrance_pupil(self):
        frame = frame_of(gaussian(), centre=(30.0, 0.0, 0.0))
        m, z = 0.8, -12.0  # a synthetic front group: pupil magnification and entrance pupil 12 mm before the stop
        tilt = math.radians(1.0)
        axis_crossing = [30.0, 0.0, 0.0]
        shift_crossing = [30.0, m * 0.5, 0.0]
        tilt_crossing = [30.0, m * z * math.tan(tilt), 0.0]
        estimate = fg.pupil_estimate(frame, axis_crossing, shift_crossing, tilt_crossing, 0.5, 1.0)
        self.assertAlmostEqual(estimate["pupil_magnification"], m, places=12)
        self.assertAlmostEqual(estimate["entrance_pupil_from_stop_mm"], z, places=9)
        frame.pupil = g.add(frame.centre, g.scale(frame.axis, z))
        placed = fg.pose(frame, fg.LINE, 7.0, fg.initial_offset(frame, fg.LINE, 7.0))
        self.assertAlmostEqual(g.norm(g.across(g.sub(frame.pupil, placed["position_mm"]), placed["axis"])), 0.0,
                               places=9)
        self.assertIn("NO_PROBE_CROSSING", fg.pupil_estimate(frame, None, shift_crossing, tilt_crossing, 0.5,
                                                             1.0)["basis"])

    def test_broyden_aiming_converges_on_a_linear_crossing_map(self):
        jacobian, bias = [[0.7, 0.05], [-0.02, 0.9]], [0.4, -0.3]

        def crossing(offset):
            return [jacobian[i][0] * offset[0] + jacobian[i][1] * offset[1] + bias[i] for i in range(2)]

        aim = fg.Aim(offset=[0.0, 0.0], gain=1.0, max_step_mm=5.0)
        for _ in range(12):
            error = crossing(aim.offset)
            if math.hypot(*error) < 1e-10:
                break
            aim.step(error)
        self.assertLess(math.hypot(*crossing(aim.offset)), 1e-9)

    def test_the_height_solve_steps_by_secant_within_bounds(self):
        self.assertAlmostEqual(fg.secant([(2.0, 4.0)], 6.0, 89.0), 3.0)
        self.assertAlmostEqual(fg.secant([(2.0, 4.0), (3.0, 5.0)], 6.0, 89.0), 4.0)
        self.assertEqual(fg.secant([(80.0, 4.0), (85.0, 4.1)], 100.0, 89.0), 89.0)
        self.assertEqual(fg.secant([(2.0, 4.0), (3.0, 5.0)], -10.0, 89.0), 0.0)

    def test_first_order_references(self):
        self.assertAlmostEqual(fg.efl_reference_height(100.0, 45.0), 100.0, places=12)
        thin = [[1.0, 0.0], [-1.0 / 100.0, 1.0]]  # f = 100 mm at its vertex
        matrix = fg.object_to_plane(thin, 200.0, 200.0)
        self.assertAlmostEqual(matrix[0][0], -1.0, places=12)  # 2f - 2f: unit magnification, inverted
        self.assertAlmostEqual(matrix[0][1], 0.0, places=9)  # the plane is the paraxial image
        self.assertNotAlmostEqual(fg.object_to_plane(thin, 200.0, 210.0)[0][1], 0.0)

    def test_the_plane_aperture_is_widened_only_when_a_field_point_falls_outside(self):
        circle = {"shape": "circle", "radius_mm": 10.0, "center_offset_mm": [0.0, 0.0], "obstruction_radius_mm": 0.0}
        self.assertTrue(fg.inside_aperture(circle, [6.0, 8.0]))
        self.assertFalse(fg.inside_aperture(circle, [6.0, 8.1]))
        self.assertIsNone(fg.widened_aperture(circle, [([3.0, 0.0], 0.1)]))
        widened = fg.widened_aperture(circle, [([0.0, 20.0], 0.5)])
        self.assertAlmostEqual(widened["radius_mm"], fg.WIDEN_MARGIN * (20.0 + fg.WIDEN_PUPIL_RADII * 0.5))
        self.assertEqual(circle["radius_mm"], 10.0)
        rectangle = {"shape": "rectangle", "half_width_mm": 18.0, "half_height_mm": 12.0, "center_offset_mm": [0, 0]}
        self.assertTrue(fg.inside_aperture(rectangle, [17.9, -11.9]))
        self.assertFalse(fg.inside_aperture(rectangle, [18.0, 12.01]))
        grown = fg.widened_aperture(rectangle, [([1.0, 13.0], 0.0)])
        self.assertEqual(grown["half_width_mm"], 18.0)
        self.assertAlmostEqual(grown["half_height_mm"], fg.WIDEN_MARGIN * 13.0)
        ring = dict(circle, obstruction_radius_mm=2.0)
        self.assertFalse(fg.inside_aperture(ring, [0.0, 0.0]))
        self.assertEqual(fg.widened_aperture(ring, [([0.0, 0.0], 0.0)])["obstruction_radius_mm"], 0.0)

    def test_stop_crossings_read_in_process_segments(self):
        stop = {"stop_id": "AS/STOP-stop", "label": "AS/STOP/stop", "centre_mm": [0.0, 0.0, 0.0],
                "normal": [1.0, 0.0, 0.0], "opening_center_mm": [0.0, 0.0], "clear_radius_mm": 5.0,
                "geometry_issue": None}
        segments = [{"end_surface_id": "AS/STOP-stop", "end_event": "aperture_stop", "end_x_mm": 0.0, "end_y_mm": 0.3,
                     "end_z_mm": -0.4}, {"end_surface_id": "AS/L1-S1", "end_event": "refract", "end_x_mm": 10.0,
                                         "end_y_mm": 0.0, "end_z_mm": 0.0}]
        found = fa.stop_crossings(segments, [stop])
        self.assertEqual(len(found), 1)
        self.assertIs(found[0]["stop"], stop)
        self.assertAlmostEqual(found[0]["offset_mm"], 0.5, places=12)
        self.assertEqual(fa.segment_end({"end_mm": [1, 2, 3]}), [1.0, 2.0, 3.0])


def sample(index, wavelength, direction, value, kind="EXACT", reason=None, frequency=40.0, requested=None):
    return {"point_index": index, "requested": index if requested is None else requested, "line_id": f"FP{index}#0",
            "wavelength_nm": wavelength, "direction": direction, "frequency_cyc_per_mm": frequency, "value": value,
            "status": "KNOWN" if kind == "EXACT" else reason, "value_kind": kind, "reason": reason, "message": None,
            "converged": kind == "EXACT", "model_basis": "x", "imaging_file": "imaging.json"}


class MinimaTests(unittest.TestCase):
    def test_every_exact_sample_confirms_the_minimum_and_names_the_limiting_sample(self):
        samples = [sample(i, w, d, v) for i, w, d, v in ((0, 486.0, "tangential", 0.8), (0, 486.0, "sagittal", 0.81),
                                                         (1, 486.0, "tangential", 0.6), (1, 486.0, "sagittal", 0.7),
                                                         (0, 656.0, "tangential", 0.75), (0, 656.0, "sagittal", 0.74),
                                                         (1, 656.0, "tangential", 0.65), (1, 656.0, "sagittal", 0.55))]
        row = fp.minima(samples, [40.0], [486.0, 656.0], [0.0, 0.8])[0]
        self.assertEqual(row["metric"], "per_line_minimum")
        self.assertEqual(row["field_grid"], [0.0, 0.8])
        self.assertEqual(row["all_lines"]["status"], "CONFIRMED")
        self.assertEqual(row["all_lines"]["value"], 0.55)
        self.assertIsNone(row["all_lines"]["bound"])
        self.assertEqual(row["all_lines"]["limiting_sample"], {"point_index": 1, "requested": 1, "direction": "sagittal",
                                                               "wavelength_nm": 656.0})
        self.assertEqual([line["value"] for line in row["per_line"]], [0.6, 0.55])
        self.assertIn("not a spectrally weighted", row["note"])

    def test_a_missing_or_inexact_sample_leaves_the_minimum_unconfirmed_with_its_reason(self):
        samples = [sample(0, 587.0, "tangential", 0.7), sample(0, 587.0, "sagittal", 0.72),
                   sample(1, 587.0, "tangential", 0.4, kind="QUANTITY_UNKNOWN", reason="NOT_CONVERGED"),
                   sample(1, 587.0, "sagittal", None, kind="NO_VALUE", reason="PUPIL_UNDERSAMPLED"),
                   sample(2, 587.0, "tangential", None, kind="NO_VALUE", reason="NO_ARRIVAL"),
                   sample(2, 587.0, "sagittal", None, kind="NO_VALUE", reason="NO_ARRIVAL")]
        row = fp.minima(samples, [40.0], [587.0], [0.0, 0.5, 1.0])[0]["all_lines"]
        self.assertEqual(row["status"], "UNCONFIRMED")
        self.assertEqual(row["bound"], "UPPER")
        self.assertEqual(row["value"], 0.7)  # the not-converged 0.4 is reported in missing, not used
        self.assertEqual(sorted(m["reason"] for m in row["missing"]),
                         ["NOT_CONVERGED", "NO_ARRIVAL", "NO_ARRIVAL", "PUPIL_UNDERSAMPLED"])
        none = fp.minima([sample(0, 587.0, "tangential", None, kind="NO_VALUE", reason="NO_ARRIVAL")], [40.0], [587.0],
                         [0.0])[0]["all_lines"]
        self.assertEqual((none["status"], none["value"], none["bound"]), ("UNCONFIRMED", None, None))

    def test_imaging_calls_take_at_most_32_fields_and_one_crossing_each(self):
        points = [fp.Point(index=i, requested=float(i), source_id=f"FP{i}", mode=fg.LINE, parameter=float(i),
                           measured={"crossing_index": 1 if i == 40 else 0}) for i in range(41)]
        calls = fp._imaging_calls(points)
        self.assertEqual([len(c) for c in calls], [32, 8, 1])
        self.assertEqual(calls[-1][0].source_id, "FP40")


class RequestTests(unittest.TestCase):
    def request(self, **changes):
        values = {"project": "p.json", "plane": "image", "kind": "ANGULAR", "values": [0.0, 5.0]}
        values.update(changes)
        return fp.Request(**values)

    def refused(self, request):
        with self.assertRaises(fp.FieldPointsError) as caught:
            fp.check_request(request)
        return caught.exception

    def test_a_request_of_another_field_kind_is_refused(self):
        error = self.refused(self.request(expect_field_kind="OBJECT_HEIGHT"))
        self.assertEqual((error.code, error.details["requested"]), ("FIELD_KIND_MISMATCH", "ANGULAR"))
        error = self.refused(self.request(kind="OBJECT_HEIGHT", expect_field_kind="ANGULAR"))
        self.assertEqual(error.code, "FIELD_KIND_MISMATCH")
        fp.check_request(self.request(expect_field_kind="ANGULAR"))

    def test_values_and_imaging_settings_are_checked_before_any_engine_use(self):
        for changes in ({"values": [-1.0]}, {"values": [90.0]}, {"values": []}, {"aim_tolerance_mm": 0.0},
                        {"wavelengths_nm": [500.0, 500.0]}, {"mtf_frequencies": [-1.0]}, {"pivot_mm": [1.0, 2.0]},
                        {"launch_grid": 64}, {"mtf_frequencies": [10.0], "imaging_request": {"fields": ["X"]}}):
            with self.subTest(changes=changes):
                self.assertEqual(self.refused(self.request(**changes)).code, "BAD_ARGUMENT")

    def test_the_cli_refuses_two_field_kinds_and_missing_ones(self):
        code, out, err = invoke(fp.main, ["p.json", "--plane", "image", "--out", "o", "--angles-deg", "1",
                                          "--image-heights-mm", "2"])
        self.assertEqual((code, out), (4, None))
        self.assertIn('"FIELD_KIND_MIXED"', err)
        code, _out, err = invoke(fp.main, ["p.json", "--plane", "image", "--out", "o"])
        self.assertEqual(code, 4)
        self.assertIn('"BAD_ARGUMENT"', err)
        code, _out, err = invoke(fp.main, ["p.json", "--plane", "image", "--out", "o", "--angles-deg", "1,x"])
        self.assertIn('"BAD_ARGUMENT"', err)

    def test_help_needs_no_engine(self):
        out = io.StringIO()
        with mock.patch.dict(sys.modules, {"os_optics": None}), redirect_stdout(out):
            code = fp.main(["--help"])
        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertTrue(text.startswith("usage: os-optics-kit field-points"))
        for needle in ("FIRST_ORDER_COMPARISON", "turned", "no polychromatic weighting", "--expect-field-kind"):
            self.assertIn(needle, text)

    def test_the_lines_keep_the_reference_powers_or_share_its_power(self):
        model = {"wavelength_nm": 587.0, "power_w": 0.003}
        self.assertEqual(fp.spectral_lines(model, None), [{"wavelength_nm": 587.0, "power_w": 0.003}])
        self.assertEqual(fp.spectral_lines(model, [486.0, 587.0, 656.0]),
                         [{"wavelength_nm": w, "power_w": 0.001} for w in (486.0, 587.0, 656.0)])
        lines = {"spectral_lines": [{"wavelength_nm": 486.0, "power_w": 1.0}, {"wavelength_nm": 656.0, "power_w": 3.0}]}
        self.assertEqual(fp.spectral_lines(lines, [486.0, 656.0])[1]["power_w"], 3.0)
        with self.assertRaises(fp.FieldPointsError) as density:
            fp.spectral_lines({"spectral_density": {"line_count": 5}, "power_w": 1.0}, None)
        self.assertEqual(density.exception.code, "WAVELENGTHS_REQUIRED")

    def test_sources_and_configurations_are_chosen_explicitly(self):
        two = {"components": [gaussian(), dict(gaussian(), instance_id="SRC2")]}
        with self.assertRaises(fp.FieldPointsError) as several:
            fp.reference_source(two, None)
        self.assertEqual((several.exception.code, several.exception.details["sources"]),
                         ("SOURCE_REQUIRED", ["SRC", "SRC2"]))
        self.assertEqual(fp.reference_source(two, "SRC2")["instance_id"], "SRC2")
        with self.assertRaises(fp.FieldPointsError) as unknown:
            fp.reference_source(two, "NOPE")
        self.assertEqual(unknown.exception.code, "SOURCE_NOT_FOUND")
        with self.assertRaises(fp.FieldPointsError) as required:
            fp.configured({"configurations": [{"configuration_id": "wide"}]}, None)
        self.assertEqual((required.exception.code, required.exception.details["configurations"]),
                         ("CONFIGURATION_REQUIRED", ["wide"]))
        with self.assertRaises(fp.FieldPointsError) as missing:
            fp.configured({"components": []}, "wide")
        self.assertEqual(missing.exception.code, "CONFIGURATION_UNKNOWN")


class FakeResults:
    """RunResults-shaped readings of two field points: FP0 arrives (two polarization paths), FP1 lands outside."""

    STOP = {"stop_id": "L/STOP-stop", "label": "L/STOP/stop", "centre_mm": [0.0, 0.0, 0.0], "normal": [1.0, 0.0, 0.0],
            "opening_center_mm": [0.0, 0.0], "clear_radius_mm": 5.0, "geometry_issue": None}

    def monitor(self, plane):
        beam = {"source_id": "FP0", "spectral_index": 0}
        return {"arrivals": [
            {"beam": {**beam, "path_id": "FP0#0:p1", "power_W": 2.0}, "uv_mm": [9.0, 9.0], "aoi_deg": 9.0,
             "crossing_index": 0, "pupil_ray_count": 96, "pupil_rays_arrived": 90, "pupil_rms_radius_mm": 0.1},
            {"beam": {**beam, "path_id": "FP0#0", "power_W": 1.0}, "uv_mm": [1.0, 2.0], "aoi_deg": 3.0,
             "crossing_index": 0, "pupil_ray_count": 96, "pupil_rays_arrived": 96, "pupil_rms_radius_mm": 0.02}],
            "outside_active_area": [{"path_id": "FP1#0", "uv_mm": [40.0, 0.0], "crossing_index": 0}]}

    def paths(self):
        return [{"path_id": "FP0#0", "parent_path_id": None, "lineage": ["L/STOP-stop:aperture_stop", "L/S1:refract"]},
                {"path_id": "FP1#0", "parent_path_id": None, "lineage": ["L/STOP-stop:aperture_stop"]}]

    def segments(self, path):
        y = 0.0004 if path == "FP0#0" else 0.2
        return [{"end_surface_id": "L/STOP-stop", "end_event": "aperture_stop", "end_x_mm": 0.0, "end_y_mm": y,
                 "end_z_mm": 0.0}]


class MeasureTests(unittest.TestCase):
    def test_the_chief_path_its_stop_crossing_and_an_outside_arrival_are_read(self):
        rows = fp.measure(FakeResults(), "image", ["FP0", "FP1"], 1, [FakeResults.STOP], FakeResults.STOP)
        first, second = rows["FP0"][0], rows["FP1"][0]
        self.assertEqual((first["path_id"], first["where"], first["uv_mm"]), ("FP0#0", "ARRIVED", [1.0, 2.0]))
        self.assertAlmostEqual(first["stop_offset_mm"], 0.0004, places=12)
        self.assertEqual((first["pupil_rays_launched"], first["pupil_rays_arrived"]), (96, 96))
        self.assertEqual((second["where"], second["uv_mm"]), ("OUTSIDE_PLANE", [40.0, 0.0]))
        self.assertEqual(fp._final_state(first, first["surfaces"]), (None, None))
        self.assertEqual(fp._final_state(second, first["surfaces"])[0], "OUTSIDE_PLANE")
        reason, message = fp._final_state(dict(first, surfaces=["L/STOP-stop:aperture_stop"]), first["surfaces"])
        self.assertEqual(reason, "CHIEF_RAY_PATH_DIFFERS")
        self.assertIn("L/S1:refract", message)


class ImagingObjectTests(unittest.TestCase):
    def test_the_engines_result_object_is_read_by_its_field_aliases(self):
        class Result:  # the Engine's AnalysisResultV1 names its schema field schema_ (alias schema)
            def model_dump(self, mode="python", by_alias=False):
                key = "schema" if by_alias else "schema_"
                return {key: ir.SCHEMA, "analysis": ir.ANALYSIS, "arrays": {}, "quantities": []}

        self.assertEqual(ir.load(Result())["schema"], ir.SCHEMA)


if __name__ == "__main__":
    unittest.main()
