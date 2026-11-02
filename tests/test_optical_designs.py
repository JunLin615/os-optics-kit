"""Checks of examples/optical_designs.py on the published os-optics (0.10.0).

The native designs need no catalog. The catalog variants are skipped when no catalog release is installed.
Run from the repository root: python -m unittest discover -s tests -v
"""

import inspect
import math
import unittest

from os_optics.local import LocalRunError
from os_optics.results import load_run

from examples import optical_designs as D
from tests.workflow_fixtures import catalog_available


def outcome(document):
    return D.design_outcome(D.run_design(document))


def values(result):
    return {k: t["value"] for k, t in result["targets"].items()}




def rel(a, b):
    return abs(a - b) / abs(b)


class MetadataTests(unittest.TestCase):
    def test_design_info_lists_every_builder_keyword(self):
        for name, info in D.DESIGN_INFO.items():
            builder = getattr(D, info["builder"])
            keywords = set(inspect.signature(builder).parameters)
            self.assertEqual(keywords, set(info["parameters"]), name)
            for parameter in info["parameters"].values():
                self.assertIn(parameter["role"], {"physical", "criterion", "applicability", "scale"})

    def test_native_targets_and_planes_match_the_built_project(self):
        for name in ("expander_focus", "polarization_control", "alignment"):
            info = D.DESIGN_INFO[name]
            document = getattr(D, name)(**({"azimuth_target_deg": 45.0} if name == "polarization_control" else {}))
            self.assertEqual({t["target_id"] for t in document["targets"]}, set(info["targets"]), name)
            self.assertEqual({o["observation_id"] for o in document["observations"]}, set(info["observations"]))

    def test_provenance_records_case_revision_and_commit(self):
        for name in ("expander_focus", "polarization_control", "alignment"):
            for case in D.DESIGN_INFO[name]["provenance"]:
                self.assertEqual(case["commit"], D.BENCHMARK["commit"])
                self.assertTrue(case["case_revision"])
                self.assertIn("CC BY 4.0", case["attribution"])
                self.assertIn(case["case_id"], case["attribution"])


class ExpanderFocusTests(unittest.TestCase):
    def test_check_project_is_catalog_free(self):
        checked = D.check_design(D.expander_focus())
        self.assertTrue(checked["ok"], checked.get("error"))
        self.assertIsNone(checked["catalog_version"])

    def test_nominal_passes_and_matches_q_propagation(self):
        result = outcome(D.expander_focus())
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["in_model_scope"])
        ref = D.gaussian_expander_focus_reference()
        v = values(result)
        self.assertLess(rel(v["expanded_radius"], ref["expanded_radius_mm"]), 1e-9)
        self.assertLess(rel(v["spot_radius"], ref["spot_radius_mm"]), 1e-9)
        self.assertLess(abs(v["waist_location"] - ref["waist_distance_mm"]), 1e-9)
        self.assertLess(v["waist_location"], 0.0)  # waist slightly after L3's focal plane: still converging there

    def test_reference_holds_across_parameters(self):
        for kwargs in ({"gap_error_mm": 5.0}, {"gap_error_mm": -2.0}, {"f3_mm": 150.0}, {"f1_mm": 40.0, "f2_mm": 200.0},
                       {"wavelength_nm": 532.0}, {"waist_radius_mm": 1.0}, {"focus_plane_shift_mm": -3.0}):
            v = values(outcome(D.expander_focus(**kwargs)))
            ref = D.gaussian_expander_focus_reference(**kwargs)
            self.assertLess(rel(v["spot_radius"], ref["spot_radius_mm"]), 1e-9, kwargs)
            self.assertLess(rel(v["expanded_radius"], ref["expanded_radius_mm"]), 1e-9, kwargs)
            self.assertLess(abs(v["waist_location"] - ref["waist_distance_mm"]), 1e-9, kwargs)

    def test_each_physical_parameter_changes_a_target_value(self):
        nominal = values(outcome(D.expander_focus()))
        changes = {"f1_mm": 55.0, "f2_mm": 160.0, "f3_mm": 120.0, "gap_error_mm": 1.0, "waist_radius_mm": 0.6,
                   "wavelength_nm": 633.0, "focus_plane_shift_mm": 0.5, "source_to_l1_mm": 150.0,
                   "l2_to_l3_mm": 150.0, "collimated_plane_after_l2_mm": 80.0}
        physical = {k for k, p in D.DESIGN_INFO["expander_focus"]["parameters"].items() if p["role"] == "physical"}
        self.assertEqual(physical, set(changes))
        for name, value in changes.items():
            self.assertNotEqual(values(outcome(D.expander_focus(**{name: value}))), nominal, name)

    def test_criteria_change_the_outcome(self):
        for kwargs, target in (({"max_spot_radius_mm": 0.01}, "spot_radius"),
                               ({"expanded_radius_target_mm": 2.0}, "expanded_radius"),
                               ({"expanded_radius_tolerance_rel": 1e-6}, "expanded_radius"),
                               ({"waist_window_mm": 0.01}, "waist_location")):
            result = outcome(D.expander_focus(**kwargs))
            self.assertEqual(result["targets"][target]["status"], "FAIL", kwargs)
            self.assertEqual(result["status"], "FAIL")

    def test_unmet_targets(self):
        result = outcome(D.expander_focus(gap_error_mm=5.0))
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["targets"]["spot_radius"]["status"], "FAIL")
        self.assertEqual(result["targets"]["expanded_radius"]["status"], "FAIL")
        self.assertEqual(outcome(D.expander_focus(f3_mm=150.0))["targets"]["spot_radius"]["status"], "FAIL")

    def test_diagnostic_outcomes(self):
        truncated = outcome(D.expander_focus(waist_radius_mm=3.0))
        self.assertEqual(truncated["status"], "WARNING")
        self.assertIn("GAUSSIAN_TRUNCATION", truncated["issues"])
        severe = outcome(D.expander_focus(waist_radius_mm=5.0))
        self.assertEqual(severe["status"], "OUT_OF_MODEL_SCOPE")
        self.assertFalse(severe["in_model_scope"])
        self.assertIsNone(severe["targets"]["spot_radius"]["value"])
        small_aperture = outcome(D.expander_focus(clear_aperture_mm=5.0))
        self.assertTrue({"GAUSSIAN_TRUNCATION", "GAUSSIAN_SEVERE_TRUNCATION"} & set(small_aperture["issues"]))
        out_of_band = outcome(D.expander_focus(wavelength_nm=1300.0))
        self.assertEqual(out_of_band["status"], "FAIL")  # a FAIL target masks the model-scope issue in the status
        self.assertFalse(out_of_band["in_model_scope"])
        self.assertEqual({i["code"] for i in out_of_band["model_scope_issues"]}, {"MODEL_WAVELENGTH_OUTSIDE_BAND"})
        self.assertTrue(outcome(D.expander_focus(wavelength_nm=1300.0, lens_band_nm=(400.0, 1700.0)))["in_model_scope"])

    def test_overlapping_lenses_are_refused(self):
        document = D.expander_focus(gap_error_mm=-199.5)
        checked = D.check_design(document)
        self.assertFalse(checked["ok"])
        self.assertEqual(checked["error"]["problems"][0]["code"], "OPTICAL_BODIES_OVERLAP")
        with self.assertRaises(LocalRunError):
            D.run_design(document)

    def test_reruns_are_identical(self):
        first, second = D.run_design(D.expander_focus(gap_error_mm=1.0)), D.run_design(D.expander_focus(gap_error_mm=1.0))
        self.assertEqual(values(D.design_outcome(first)), values(D.design_outcome(second)))
        self.assertEqual(load_run(first).identity()["result_hash"], load_run(second).identity()["result_hash"])


class PolarizationControlTests(unittest.TestCase):
    def test_check_project_is_catalog_free(self):
        checked = D.check_design(D.polarization_control(azimuth_target_deg=45.0))
        self.assertTrue(checked["ok"], checked.get("error"))
        self.assertIsNone(checked["catalog_version"])

    def test_nominal_passes(self):
        result = outcome(D.polarization_control(azimuth_target_deg=45.0))
        self.assertEqual(result["status"], "PASS")
        self.assertLess(rel(result["targets"]["transmitted_power"]["value"], 1e-3 * 0.99 * 0.9), 1e-12)
        self.assertLess(abs(result["targets"]["output_azimuth"]["value"] - 45.0), 1e-9)

    def test_power_follows_malus_law(self):
        for hwp, pol, src in ((0.0, 45.0, 0.0), (7.5, 45.0, 0.0), (30.0, 45.0, 0.0), (60.0, 45.0, 0.0),
                              (67.5, 45.0, 0.0), (10.0, -20.0, 30.0), (40.0, 0.0, 15.0), (22.5, 135.0, 0.0)):
            kwargs = {"hwp_angle_deg": hwp, "polarizer_angle_deg": pol, "source_polarization_angle_deg": src}
            power = outcome(D.polarization_control(**kwargs))["targets"]["transmitted_power"]["value"]
            self.assertLess(rel(power, D.malus_power_reference(**kwargs)), 1e-12, kwargs)

    def test_stokes_and_power_follow_jones_calculus(self):
        for kwargs in ({"retardance_waves": 0.25, "hwp_angle_deg": 10.0, "source_polarization_angle_deg": 30.0},
                       {"retardance_waves": 0.25, "hwp_angle_deg": -30.0, "polarizer_angle_deg": 0.0},
                       {"wavelength_nm": 1050.0, "hwp_angle_deg": 10.0},
                       {"wavelength_nm": 1050.0, "hwp_angle_deg": 10.0, "retarder_model": "ACHROMATIC"},
                       {"design_wavelength_nm": 1010.0, "hwp_angle_deg": 30.0, "extinction_ratio": 50.0}):
            run = D.run_design(D.polarization_control(**kwargs))
            power = D.design_outcome(run)["targets"]["transmitted_power"]["value"]
            arrival = load_run(run).arrivals("after_hwp")[0]
            ref = D.jones_polarization_reference(**kwargs)
            self.assertLess(rel(power, ref["power_w"]), 1e-12, kwargs)
            for key in ("stokes_s1", "stokes_s2", "stokes_s3"):
                self.assertLess(abs(arrival[key] - ref[key]), 1e-12, (kwargs, key))
            self.assertLess(abs(arrival["polarization_ellipticity_deg"] - ref["ellipticity_deg"]), 1e-9, kwargs)
            self.assertLess(abs(arrival["polarization_azimuth_deg"] - ref["azimuth_deg"]), 1e-9, kwargs)

    def test_handedness_convention(self):
        # input 20 deg from a quarter-wave fast axis: left-handed (s3 > 0, ellipticity +20 deg) in the descriptor sense
        run = D.run_design(D.polarization_control(retardance_waves=0.25, hwp_angle_deg=10.0,
                                                  source_polarization_angle_deg=30.0))
        arrival = load_run(run).arrivals("after_hwp")[0]
        self.assertAlmostEqual(arrival["polarization_ellipticity_deg"], 20.0, places=9)
        self.assertEqual(arrival["polarization_handedness"], "LEFT")

    def test_each_physical_parameter_changes_a_target_value(self):
        def power(**kwargs):
            return outcome(D.polarization_control(**kwargs))["targets"]["transmitted_power"]["value"]

        changes = [("hwp_angle_deg", 10.0, {}), ("polarizer_angle_deg", 30.0, {}),
                   ("source_polarization_angle_deg", 10.0, {}), ("wavelength_nm", 1050.0, {}),
                   ("retardance_waves", 0.25, {}), ("design_wavelength_nm", 1040.0, {}),
                   ("retarder_model", "ACHROMATIC", {"wavelength_nm": 1050.0}), ("hwp_transmittance", 0.95, {}),
                   ("polarizer_transmittance", 0.8, {}), ("extinction_ratio", 100.0, {"hwp_angle_deg": 0.0}),
                   ("power_w", 2e-3, {})]
        physical = {k for k, p in D.DESIGN_INFO["polarization_control"]["parameters"].items() if p["role"] == "physical"}
        self.assertEqual(physical, {name for name, _v, _b in changes})
        for name, value, base in changes:
            self.assertNotEqual(power(**base, **{name: value}), power(**base), name)

    def test_criteria_change_the_outcome(self):
        self.assertEqual(outcome(D.polarization_control(min_power_w=0.9e-3))["status"], "FAIL")
        result = outcome(D.polarization_control(azimuth_target_deg=40.0))
        self.assertEqual(result["targets"]["output_azimuth"]["status"], "FAIL")
        result = outcome(D.polarization_control(azimuth_target_deg=45.3, azimuth_tolerance_deg=0.1))
        self.assertEqual(result["targets"]["output_azimuth"]["status"], "FAIL")

    def test_unmet_targets(self):
        result = outcome(D.polarization_control(hwp_angle_deg=0.0))
        self.assertEqual(result["status"], "FAIL")
        self.assertLess(result["targets"]["transmitted_power"]["value"], 0.8e-3)
        crossed = outcome(D.polarization_control(polarizer_angle_deg=135.0))
        self.assertLess(rel(crossed["targets"]["transmitted_power"]["value"], 1e-3 * 0.99 * 0.9 / 1000.0), 1e-12)

    def test_out_of_band_wavelength_is_out_of_model_with_a_passing_target(self):
        result = outcome(D.polarization_control(wavelength_nm=1064.0))
        self.assertEqual(result["status"], "OUT_OF_MODEL_SCOPE")
        self.assertEqual(result["targets"]["transmitted_power"]["status"], "PASS")
        self.assertFalse(result["in_model_scope"])
        self.assertEqual([i["code"] for i in result["model_scope_issues"]], ["MODEL_WAVELENGTH_OUTSIDE_BAND"])
        self.assertTrue(outcome(D.polarization_control(wavelength_nm=1064.0, retarder_band_nm=(1000.0, 1100.0)))
                        ["in_model_scope"])

    def test_azimuth_of_circular_light_is_unknown(self):
        result = outcome(D.polarization_control(retardance_waves=0.25, hwp_angle_deg=45.0, azimuth_target_deg=45.0))
        self.assertEqual(result["targets"]["output_azimuth"]["status"], "UNKNOWN")
        self.assertIsNone(result["targets"]["output_azimuth"]["value"])
        self.assertIsNone(D.jones_polarization_reference(retardance_waves=0.25, hwp_angle_deg=45.0)["azimuth_deg"])

    def test_reruns_are_identical(self):
        first, second = (D.run_design(D.polarization_control(hwp_angle_deg=10.0)) for _ in range(2))
        self.assertEqual(values(D.design_outcome(first)), values(D.design_outcome(second)))
        self.assertEqual(load_run(first).identity()["result_hash"], load_run(second).identity()["result_hash"])


class AlignmentTests(unittest.TestCase):
    KEYS = ("near_u_mm", "near_v_mm", "far_u_mm", "far_v_mm", "pointing_u_mrad", "pointing_v_mrad")

    def errors(self, **kwargs):
        return D.alignment_errors(D.run_design(D.alignment(**kwargs)))

    def test_check_project_is_catalog_free(self):
        checked = D.check_design(D.alignment())
        self.assertTrue(checked["ok"], checked.get("error"))
        self.assertIsNone(checked["catalog_version"])

    def test_nominal_passes_on_axis(self):
        run = D.run_design(D.alignment())
        self.assertEqual(D.design_outcome(run)["status"], "PASS")
        errors = D.alignment_errors(run)
        for key in self.KEYS:
            self.assertLess(abs(errors[key]), 1e-9, key)
        self.assertAlmostEqual(errors["separation_mm"], 300.0, places=9)

    def test_mirror_tilt_deviation(self):
        for tilt in (1.0, -2.0, 0.3):
            errors = self.errors(mirror_tilt_local_y_mrad=tilt)
            self.assertLess(rel(errors["pointing_u_mrad"], D.mirror_deviation_mrad(tilt, axis="in_plane")), 1e-9)
            self.assertLess(abs(errors["pointing_v_mrad"]), 1e-9)
        for aoi in (45.0, 30.0, 60.0):
            errors = self.errors(mirror_tilt_local_x_mrad=1.0, mirror_aoi_deg=aoi)
            small_angle = -D.mirror_deviation_mrad(1.0, axis="out_of_plane", mirror_aoi_deg=aoi)
            self.assertLess(rel(errors["pointing_v_mrad"], small_angle), 1e-6, aoi)  # second-order terms ~ θ²

    def test_engine_matches_exact_reflection_geometry(self):
        for kwargs in ({"mirror_tilt_local_x_mrad": 1.0, "mirror_tilt_local_y_mrad": 1.0},
                       {"source_offset_y_mm": 0.2, "source_offset_z_mm": -0.1},
                       {"source_tilt_local_x_mrad": 1.0, "source_tilt_local_y_mrad": -0.5},
                       {"mirror_aoi_deg": 30.0, "mirror_tilt_local_x_mrad": 1.0, "mirror_tilt_local_y_mrad": 0.3,
                        "source_offset_z_mm": 0.1, "source_tilt_local_y_mrad": 0.4},
                       {"near_plane_mm": 150.0, "far_plane_mm": 600.0, "source_to_mirror_mm": 250.0,
                        "source_tilt_local_x_mrad": 0.7}):
            errors, ref = self.errors(**kwargs), D.mirror_alignment_reference(**kwargs)
            for key in self.KEYS:
                self.assertLess(abs(errors[key] - ref[key]), 1e-9, (kwargs, key))

    def test_source_tilt_and_offset(self):
        tilted = self.errors(source_tilt_local_x_mrad=1.0)
        self.assertLess(rel(tilted["pointing_v_mrad"], -1.0), 1e-9)
        self.assertLess(rel(tilted["far_v_mm"], -math.tan(1e-3) * 500.0), 1e-6)  # 500 mm source-to-far path
        shifted = self.errors(source_offset_z_mm=0.2)
        self.assertLess(abs(shifted["near_v_mm"] - 0.2), 1e-12)
        self.assertLess(abs(shifted["pointing_v_mrad"]), 1e-9)

    def test_two_plane_pointing_matches_direction_cosines(self):
        errors = self.errors(mirror_tilt_local_x_mrad=0.8, source_tilt_local_y_mrad=0.6)
        self.assertLess(abs(errors["pointing_u_mrad"] - errors["far_direction_pointing_u_mrad"]), 1e-5)
        self.assertLess(abs(errors["pointing_v_mrad"] - errors["far_direction_pointing_v_mrad"]), 1e-5)

    def test_each_physical_parameter_changes_a_target_value(self):
        def target_values(**kwargs):
            return values(outcome(D.alignment(**kwargs)))

        changes = [("mirror_tilt_local_x_mrad", 0.3, {}), ("mirror_tilt_local_y_mrad", 0.3, {}),
                   ("source_offset_y_mm", 0.05, {}), ("source_offset_z_mm", 0.05, {}),
                   ("source_tilt_local_x_mrad", 0.3, {}), ("source_tilt_local_y_mrad", 0.3, {}),
                   ("near_plane_mm", 150.0, {"mirror_tilt_local_y_mrad": 0.3}),
                   ("far_plane_mm", 600.0, {"mirror_tilt_local_y_mrad": 0.3}),
                   ("source_to_mirror_mm", 200.0, {"source_tilt_local_x_mrad": 0.3}),
                   ("mirror_aoi_deg", 30.0, {"mirror_tilt_local_x_mrad": 0.3})]
        physical = {k for k, p in D.DESIGN_INFO["alignment"]["parameters"].items() if p["role"] == "physical"}
        self.assertEqual(physical, {name for name, _v, _b in changes})
        for name, value, base in changes:
            self.assertNotEqual(target_values(**base, **{name: value}), target_values(**base), name)

    def test_criteria_change_the_outcome(self):
        base = outcome(D.alignment(mirror_tilt_local_y_mrad=0.1))  # far u 0.08 mm, pointing 0.2 mrad
        self.assertEqual(base["status"], "PASS")
        tight = outcome(D.alignment(mirror_tilt_local_y_mrad=0.1, position_tolerance_mm=0.05))
        self.assertEqual(tight["targets"]["far_position_u"]["status"], "FAIL")
        self.assertEqual(tight["targets"]["near_position_u"]["status"], "PASS")
        pointing = outcome(D.alignment(mirror_tilt_local_y_mrad=0.1, pointing_tolerance_mrad=0.1))
        self.assertEqual(pointing["targets"]["far_pointing_u"]["status"], "FAIL")

    def test_unmet_targets(self):
        result = outcome(D.alignment(mirror_tilt_local_x_mrad=0.5))
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["targets"]["far_position_v"]["status"], "FAIL")
        self.assertEqual(result["targets"]["near_position_v"]["status"], "PASS")
        self.assertLess(abs(result["targets"]["far_position_v"]["value"] + 0.4 * math.sqrt(2) * 0.5), 1e-6)

    def test_far_plane_not_reached(self):
        run = D.run_design(D.alignment(mirror_tilt_local_y_mrad=60.0))
        result = D.design_outcome(run)
        self.assertIn("OBSERVATION_NOT_REACHED", result["issues"])
        self.assertIsNone(result["targets"]["far_position_u"]["value"])
        errors = D.alignment_errors(run)
        self.assertEqual(errors["reached"], {"near": True, "far": False})
        self.assertIsNone(errors["pointing_u_mrad"])
        reached = D.alignment_errors(D.run_design(D.alignment(mirror_tilt_local_y_mrad=60.0, plane_radius_mm=60.0)))
        self.assertTrue(reached["reached"]["far"])
        self.assertLess(rel(reached["pointing_u_mrad"], 120.0), 1e-9)

    def test_out_of_band_wavelength(self):
        result = outcome(D.alignment(wavelength_nm=1550.0))
        self.assertFalse(result["in_model_scope"])
        self.assertIn("MODEL_WAVELENGTH_OUTSIDE_BAND", {i["code"] for i in result["model_scope_issues"]})
        self.assertTrue(outcome(D.alignment(wavelength_nm=1550.0, mirror_band_nm=(400.0, 1700.0)))["in_model_scope"])

    def test_invalid_inputs(self):
        with self.assertRaises(ValueError):
            D.alignment(near_plane_mm=400.0, far_plane_mm=100.0)
        with self.assertRaises(ValueError):
            D.two_plane_pointing(0.0, 0.0, 1.0, 1.0, 0.0)
        with self.assertRaises(ValueError):
            D.mirror_deviation_mrad(1.0, axis="diagonal")

    def test_reruns_are_identical(self):
        first, second = (D.run_design(D.alignment(mirror_tilt_local_x_mrad=0.4)) for _ in range(2))
        self.assertEqual(D.alignment_errors(first), D.alignment_errors(second))
        self.assertEqual(load_run(first).identity()["result_hash"], load_run(second).identity()["result_hash"])


@unittest.skipUnless(catalog_available(), "no catalog release is installed")
class CatalogVariantTests(unittest.TestCase):
    def test_beam_expander_spacing_moves_the_waist_not_the_radius(self):
        nominal = outcome(D.catalog_beam_expander())
        self.assertEqual(nominal["status"], "PASS")
        self.assertIsNotNone(nominal["catalog_version"])
        shifted = outcome(D.catalog_beam_expander(l2_shift_mm=1.0))
        self.assertEqual(shifted["targets"]["expanded_waist"]["status"], "FAIL")
        self.assertEqual(shifted["targets"]["output_beam_radius"]["status"], "PASS")

    def test_polarization_power_follows_malus_law(self):
        document = D.catalog_polarization_control()
        elements = D.catalog_element_values(document)
        for hwp in (22.5, 0.0, 10.0, 67.5):
            power = outcome(D.catalog_polarization_control(hwp_angle_deg=hwp))["targets"]["transmitted_power"]["value"]
            ref = D.malus_power_reference(hwp_angle_deg=hwp, hwp_transmittance=elements["HWP"]["transmittance"],
                                          polarizer_transmittance=elements["POL"]["transmittance"],
                                          extinction_ratio=elements["POL"]["extinction_ratio"])
            self.assertLess(rel(power, ref), 1e-12, hwp)

    def test_polarization_off_design_wavelength(self):
        result = outcome(D.catalog_polarization_control(wavelength_nm=1064.0))
        self.assertFalse(result["in_model_scope"])
        self.assertIn("MODEL_WAVELENGTH_OUTSIDE_BAND", {i["code"] for i in result["model_scope_issues"]})


if __name__ == "__main__":
    unittest.main()
