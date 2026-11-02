"""Offline checks of examples/sensitivity_analysis.py and examples/tolerance_study.py on catalog-free designs.

Run from the repository root: python -m unittest discover -s tests -v
"""

import csv
import json
import math
from pathlib import Path
import unittest

from tests.workflow_fixtures import TempDir, invoke, quiet_environment

quiet_environment()

from examples import optical_designs as D  # noqa: E402
from examples import sensitivity_analysis as sa  # noqa: E402
from examples import tolerance_study as ts  # noqa: E402
from examples import workflow_common as wc  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SPECS = ROOT / "examples" / "specs"


def rotate(instance_id, axis):
    return [{"op": "transform", "selector": {"ids": [instance_id]}, "frame": {"component": instance_id},
             "pivot": {"component": instance_id}, "rotation": {"axis": axis, "angle_deg": "$value"}}]


def translate(instance_id, vector, frame=None):
    return [{"op": "transform", "selector": {"ids": [instance_id]}, "frame": frame or {"component": instance_id},
             "translation_mm": vector}]


WAVELENGTH_OPS = [{"op": "set_source_model", "instance_id": "SRC", "changes": {"wavelength_nm": "$value"}}]


def malus_slope_w_per_deg(theta_deg, alpha_deg=45.0, p0=1e-3, t_hwp=0.99, t_pol=0.9, er=1000.0):
    """Closed form dP/dtheta of P = K [cos^2 x + sin^2 x / ER], x = 2 theta - alpha (radians), per degree."""
    x = math.radians(2.0 * theta_deg - alpha_deg)
    return p0 * t_hwp * t_pol * (1.0 / er - 1.0) * math.sin(2.0 * x) * 2.0 * math.pi / 180.0


def malus_curvature_w_per_deg2(theta_deg, alpha_deg=45.0, p0=1e-3, t_hwp=0.99, t_pol=0.9, er=1000.0):
    x = math.radians(2.0 * theta_deg - alpha_deg)
    return p0 * t_hwp * t_pol * (1.0 / er - 1.0) * 2.0 * math.cos(2.0 * x) * (2.0 * math.pi / 180.0) ** 2


def pair(summary, parameter, target):
    return next(p for p in summary["sensitivities"] if p["parameter"] == parameter and p["target"] == target)


class FiniteDifferenceNumericsTests(unittest.TestCase):
    """Pure numerics on f(x) = 3 x^2 + 2 x + 1 (central difference and second difference are exact)."""

    @staticmethod
    def f(x):
        return 3.0 * x * x + 2.0 * x + 1.0

    def test_central_forward_backward_and_unavailable(self):
        h = 0.5
        central = sa.finite_difference(self.f(0.0), self.f(h), self.f(-h), h, h)
        self.assertEqual(central["method"], "central")
        self.assertAlmostEqual(central["derivative"], 2.0, places=14)
        self.assertAlmostEqual(central["second_difference"], 6.0, places=13)
        forward = sa.finite_difference(self.f(0.0), self.f(h), None, h, h)
        self.assertEqual((forward["method"], forward["second_difference"]), ("forward", None))
        self.assertAlmostEqual(forward["derivative"], 2.0 + 3.0 * h, places=14)  # O(h) bias, labelled
        backward = sa.finite_difference(self.f(0.0), None, self.f(-h), h, h)
        self.assertEqual(backward["method"], "backward")
        self.assertAlmostEqual(backward["derivative"], 2.0 - 3.0 * h, places=14)
        none = sa.finite_difference(None, self.f(h), None, h, h)
        self.assertIsNone(none["method"])
        self.assertIsNone(none["derivative"])

    def _estimates(self, func, steps):
        return [{"step": h, **sa.finite_difference(func(0.0), func(h), func(-h), h, h), "f_nominal": func(0.0),
                 "f_plus": func(h), "f_minus": func(-h)} for h in steps]

    def test_two_step_consistency_and_threshold(self):
        cubic = lambda x: math.exp(x)  # noqa: E731 - central difference error h^2/6
        result = sa.assess(self._estimates(cubic, [0.01, 0.1]), 0.01, None)
        self.assertEqual(result["method"], "central")
        self.assertEqual(result["primary_step"], 0.01)
        consistency = result["consistency"]
        self.assertAlmostEqual(consistency["ratio"], (math.sinh(0.01) / 0.01) / (math.sinh(0.1) / 0.1), places=12)
        self.assertLess(consistency["relative_difference"], 0.01)
        self.assertTrue(consistency["consistent"])
        self.assertEqual(result["flags"], [])
        strict = sa.assess(self._estimates(cubic, [0.01, 0.1]), 1e-4, None)
        self.assertIn("STEP_DEPENDENT", strict["flags"])
        three = sa.assess(self._estimates(cubic, [0.01, 0.1, 5.0]), 0.01, None)
        self.assertEqual(three["consistency"]["steps"], [0.01, 0.1])
        self.assertEqual(three["second_difference_step"], 0.1)  # the larger compared step, not the exploratory 5

    def test_extremum_no_change_and_unavailable(self):
        quad = lambda x: 1.0 + 4.0 * x * x  # noqa: E731 - minimum at 0
        result = sa.assess(self._estimates(quad, [0.01, 0.1]), 0.01, 0.5)
        self.assertEqual(result["derivative"], 0.0)
        self.assertIn("NEAR_EXTREMUM", result["flags"])
        self.assertNotIn("STEP_DEPENDENT", result["flags"])
        self.assertAlmostEqual(result["second_difference"], 8.0, places=8)
        self.assertAlmostEqual(result["taylor_change_at_scale"], 0.5 * 8.0 * 0.25, places=8)
        flat = sa.assess(self._estimates(lambda x: 2.5, [0.01, 0.1]), 0.01, None)
        self.assertIn("NO_CHANGE", flat["flags"])
        missing = sa.assess([{"step": 0.1, **sa.finite_difference(None, None, None, 0.1, 0.1)}], 0.01, None)
        self.assertEqual(missing["flags"], ["UNAVAILABLE"])

    def test_ranking_basis_and_ties(self):
        def entry(name, unit, derivative, scale, taylor):
            return {"parameter": name, "parameter_unit": unit, "derivative": derivative, "scale": scale,
                    "taylor_change_at_scale": taylor}

        scaled = [entry("a", "mm", 1.0, 0.1, 0.1), entry("b", "deg", 5.0, 0.1, 0.5), entry("c", "mm", 0.0, 1, 0.1)]
        basis, order, not_ranked = sa.rank(scaled)
        self.assertEqual((basis, order, not_ranked), ("taylor_change_at_scale", ["b", "a", "c"], []))
        self.assertEqual([e["rank"] for e in scaled], [2, 1, 2])  # equal keys share a rank
        mixed = [entry("a", "mm", 1.0, None, None), entry("b", "deg", 5.0, 0.1, 0.5)]
        basis, order, not_ranked = sa.rank(mixed)
        self.assertEqual(order, ["b"])
        self.assertEqual(not_ranked, [{"parameter": "a", "reason": "no scale and the parameter units differ"}])


class SensitivityAgainstClosedFormTests(unittest.TestCase):
    """Engine derivatives against Malus law and the 2 theta mirror law (independent closed forms)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = TempDir()
        cls.dir = cls._tmp.__enter__()
        cls.malus = sa.run_sensitivity({
            "targets": ["transmitted_power"], "max_evaluations": 9,
            "parameters": [{"name": "hwp_angle", "unit": "deg", "steps": [0.01, 0.1], "ops": rotate("HWP", [0, 0, 1])},
                           {"name": "hwp_angle_mrad", "unit": "mrad", "steps": [0.1, 1], "scale": 1,
                            "ops": rotate("HWP", [0, 0, 1])}]},
            cls.dir / "malus", base=D.polarization_control(hwp_angle_deg=10.0), plots=False, report=False)
        cls.peak = sa.run_sensitivity({
            "targets": ["transmitted_power"], "max_evaluations": 5,
            "parameters": [{"name": "hwp_angle", "unit": "deg", "steps": [0.1, 1], "scale": 1,
                            "ops": rotate("HWP", [0, 0, 1])}]},
            cls.dir / "peak", base=D.polarization_control(), plots=False, report=False)
        cls.mirror = sa.run_sensitivity({
            "targets": ["far_position_u", "far_position_v"], "max_evaluations": 9,
            "parameters": [{"name": "tilt_y", "unit": "mrad", "steps": [0.01, 0.1], "ops": rotate("M1", [0, 1, 0])},
                           {"name": "tilt_x", "unit": "mrad", "steps": [0.01, 0.1], "ops": rotate("M1", [1, 0, 0])}]},
            cls.dir / "mirror", base=D.alignment(), plots=False, report=False)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.__exit__(None, None, None)

    def test_malus_slope_within_1e_6(self):
        expected = malus_slope_w_per_deg(10.0)
        entry = pair(self.malus, "hwp_angle", "transmitted_power")
        self.assertEqual((entry["method"], entry["derivative_unit"]), ("central", "W/deg"))
        self.assertLess(abs(entry["derivative"] / expected - 1.0), 1e-6)
        self.assertLess(abs(entry["estimates"][1]["derivative"] / expected - 1.0), 1e-4)  # h = 0.1 deg: O(h^2)
        self.assertTrue(entry["consistency"]["consistent"])
        self.assertLess(abs(entry["consistency"]["ratio"] - 1.0), 1e-4)
        self.assertEqual(entry["flags"], [])
        mrad = pair(self.malus, "hwp_angle_mrad", "transmitted_power")  # same physics per mrad
        self.assertEqual(mrad["derivative_unit"], "W/mrad")
        self.assertLess(abs(mrad["derivative"] / (expected * 180.0 / math.pi / 1000.0) - 1.0), 1e-6)
        self.assertAlmostEqual(mrad["scaled"], mrad["derivative"] * 1.0, places=18)

    def test_extremum_reports_second_difference(self):
        entry = pair(self.peak, "hwp_angle", "transmitted_power")
        self.assertIn("NEAR_EXTREMUM", entry["flags"])
        self.assertNotIn("STEP_DEPENDENT", entry["flags"])
        self.assertLess(abs(entry["derivative"]), 1e-15)
        expected = malus_curvature_w_per_deg2(22.5)
        self.assertLess(abs(entry["estimates"][0]["second_difference"] / expected - 1.0), 1e-4)  # h = 0.1 deg
        self.assertEqual(entry["second_difference_step"], 1.0)  # reported: largest step, O(h^2) = 4e-4 here
        self.assertEqual(entry["second_difference"], entry["estimates"][1]["second_difference"])
        self.assertLess(abs(entry["second_difference"] / expected - 1.0), 1e-3)
        self.assertEqual(entry["second_difference_unit"], "W/deg^2")

    def test_mirror_two_theta_slopes(self):
        far_u = pair(self.mirror, "tilt_y", "far_position_u")
        self.assertEqual(far_u["derivative_unit"], "mm/mrad")
        self.assertLess(abs(far_u["derivative"] / (2.0 * 400.0 * 1e-3) - 1.0), 1e-6)  # 2 L theta
        far_v = pair(self.mirror, "tilt_x", "far_position_v")
        expected_v = -D.mirror_deviation_mrad(1.0, axis="out_of_plane") * 400.0 * 1e-3  # -2 cos(AOI) L theta
        self.assertLess(abs(far_v["derivative"] / expected_v - 1.0), 1e-6)
        cross = pair(self.mirror, "tilt_y", "far_position_v")
        self.assertLess(abs(cross["derivative"]), 1e-9)
        self.assertEqual(self.mirror["status"], "COMPLETED")
        self.assertEqual(self.mirror["counts"]["by_outcome"]["PASS"] + self.mirror["counts"]["by_outcome"]["FAIL"], 9)


class SensitivityVisibilityTests(unittest.TestCase):
    """Unmet nominal target, out-of-model and refused samples stay in every output."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = TempDir()
        cls.dir = cls._tmp.__enter__()
        spec = {"design": {"builder": "expander_focus", "arguments": {"wavelength_nm": 1099.8}},
                "targets": ["spot_radius"], "max_evaluations": 9,
                "parameters": [
                    {"name": "wavelength", "unit": "nm", "nominal": 1099.8, "steps": [0.05, 0.5], "scale": 1,
                     "ops": WAVELENGTH_OPS},
                    {"name": "L2_axial", "unit": "mm", "steps": [0.1, 199.5],
                     "scale": {"value": 50, "unit": "um"}, "ops": translate("L2", [0, 0, "$value"])}]}
        (cls.dir / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
        cls.code, cls.out, cls.err = invoke(sa.main, [str(cls.dir / "spec.json"), "--out", str(cls.dir / "study")])
        cls.summary = wc.read_json(cls.dir / "study" / "summary.json")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.__exit__(None, None, None)

    def test_cli_contract(self):
        self.assertEqual(self.code, 0, self.err)
        self.assertEqual(self.out["status"], "COMPLETED")
        self.assertEqual(self.out["nominal"]["outcome"], "FAIL")  # unmet nominal target stays visible
        self.assertEqual(self.out["counts"]["samples"], 9)
        outcomes = {s["sample_id"]: s["outcome"] for s in self.out["unusable_samples"]}
        self.assertIn("OUT_OF_MODEL", outcomes.values())
        self.assertIn("ERROR", outcomes.values())
        self.assertEqual(self.out["identity"]["engine_version"], wc.engine_identity()["engine_version"])

    def test_one_sided_labels(self):
        wavelength = pair(self.summary, "wavelength", "spot_radius")
        self.assertEqual([e["method"] for e in wavelength["estimates"]], ["central", "backward"])
        self.assertIn("MODEL_WAVELENGTH_OUTSIDE_BAND", wavelength["estimates"][1]["unusable"][0])
        self.assertEqual(wavelength["method"], "central")
        self.assertIn("METHOD_DIFFERS", wavelength["flags"])
        shift = pair(self.summary, "L2_axial", "spot_radius")
        self.assertEqual(shift["estimates"][1]["method"], "forward")
        self.assertIn("ERROR RUN_REFUSED", shift["estimates"][1]["unusable"][0])
        self.assertIn("OPTICAL_BODIES_OVERLAP", shift["estimates"][1]["unusable"][0])

    def test_files_keep_every_sample(self):
        study = self.dir / "study"
        with open(study / "table.csv", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual([r["sample_id"] for r in rows], [f"s{i:04d}" for i in range(9)])
        self.assertEqual(sorted({r["outcome"] for r in rows}), ["ERROR", "FAIL", "OUT_OF_MODEL"])
        for row in rows:
            self.assertTrue((study / "samples" / row["sample_id"] / "sample.json").is_file())
        with open(study / "sensitivity.csv", encoding="utf-8", newline="") as handle:
            estimates = list(csv.DictReader(handle))
        self.assertEqual(len(estimates), 4)
        self.assertEqual({e["derivative_unit"] for e in estimates}, {"mm/nm", "mm/mm"})
        for name in ("sensitivity_spot_radius", "tornado_spot_radius"):
            for suffix in (".png", ".svg"):
                self.assertTrue((study / "plots" / f"{name}{suffix}").is_file())
        svg = (study / "plots" / "tornado_spot_radius.svg").read_text(encoding="utf-8")
        self.assertIn("ERROR", svg)
        self.assertIn("change of spot_radius from nominal [mm]", svg)
        report = (study / "report" / "report.md").read_text(encoding="utf-8")
        self.assertIn("## Sensitivity", report)
        self.assertIn("backward", report)
        self.assertIn("OUT_OF_MODEL", report)
        study_json = wc.read_json(study / "study.json")
        self.assertEqual(study_json["engine"], wc.engine_identity())
        self.assertIn("catalog", study_json)
        self.assertIsNone(study_json["catalog"]["catalog_version"])
        self.assertIsNone(self.summary["identity"]["catalog_version"])
        self.assertIn("installed_catalog", study_json)


class SensitivityRefusalTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TempDir()
        self.dir = self._tmp.__enter__()
        self.base = D.expander_focus()

    def tearDown(self):
        self._tmp.__exit__(None, None, None)

    def refused(self, spec, code):
        with self.assertRaises(wc.WorkflowError) as caught:
            sa.run_sensitivity(spec, self.dir / "study", base=self.base, plots=False, report=False)
        self.assertEqual(caught.exception.code, code)
        self.assertFalse((self.dir / "study").exists())
        return caught.exception

    def spec(self, **parameter):
        base = {"name": "p", "unit": "mm", "steps": [0.01, 0.1], "ops": translate("L2", [0, 0, "$value"])}
        return {"max_evaluations": 50, "parameters": [{**base, **parameter}]}

    def test_unit_refusals(self):
        self.refused(self.spec(unit="furlong"), "UNIT_UNKNOWN")
        self.refused(self.spec(steps=[{"value": 1, "unit": "deg"}, 0.1]), "UNIT_INCOMPATIBLE")
        self.refused(self.spec(scale={"value": 1, "unit": "mW"}), "UNIT_INCOMPATIBLE")

    def test_spec_refusals(self):
        self.refused(self.spec(steps=[0.1]), "SPEC_INVALID")
        self.refused(self.spec(steps=[0.1, -0.2]), "SPEC_INVALID")
        self.refused({**self.spec(), "max_evaluations": 4}, "BUDGET_EXCEEDED")
        self.refused({**self.spec(), "targets": ["nope"]}, "TARGET_UNKNOWN")
        error = self.refused(self.spec(unit="nm", ops=WAVELENGTH_OPS, steps=[0.5, 5]), "NOMINAL_MISMATCH")
        self.assertEqual(error.details["parameters"], ["p"])
        self.refused(self.spec(unit="nm", ops=WAVELENGTH_OPS, steps=None), "STEP_REQUIRED")

    def test_default_steps(self):
        normalized = sa.normalize_spec({"max_evaluations": 20, "parameters": [
            {"name": "shift", "unit": "um", "ops": translate("L2", [0, 0, "$value"])},
            {"name": "tilt", "unit": "mrad", "ops": rotate("L3", [1, 0, 0])},
            {"name": "wl", "unit": "nm", "nominal": 780, "ops": WAVELENGTH_OPS}]})
        steps = {p["name"]: (p["steps"], p["steps_source"]) for p in normalized["parameters"]}
        self.assertEqual(steps["shift"], ([10.0, 100.0], "default translation"))
        self.assertEqual(steps["tilt"][1], "default rotation")
        self.assertAlmostEqual(steps["tilt"][0][0], math.radians(0.01) * 1000.0, places=12)
        self.assertEqual(steps["wl"], ([0.78, 7.8], "default relative"))

    def test_help_on_stdout(self):
        import subprocess
        import sys

        for name in ("sensitivity_analysis", "tolerance_study"):
            done = subprocess.run([sys.executable, str(ROOT / "examples" / f"{name}.py"), "--help"],
                                  capture_output=True, text=True, timeout=120)
            self.assertEqual(done.returncode, 0, name)
            self.assertIn("usage:", done.stdout, name)
            self.assertIn("Exit 0", done.stdout, name)


class ToleranceSamplingTests(unittest.TestCase):
    PARAMETERS = [{"name": "a", "unit": "mm", "nominal": 0.0,
                   "distribution": {"kind": "uniform", "low": -2.0, "high": 1.0, "bounds": [-2.0, 1.0]}},
                  {"name": "b", "unit": "deg", "nominal": 22.5,
                   "distribution": {"kind": "normal", "sigma": 4.0, "mean_offset": 0.5, "bounds": [-3.0, 6.0]}},
                  {"name": "c", "unit": "nm", "nominal": 0.0,
                   "distribution": {"kind": "normal", "sigma": 0.0, "mean_offset": 1.5, "bounds": [0.0, 2.0]}}]

    def test_same_seed_same_samples_and_prefix(self):
        first = ts.draw_samples(self.PARAMETERS, 50, 7)
        self.assertEqual(first, ts.draw_samples(self.PARAMETERS, 50, 7))
        self.assertEqual(first[:20], ts.draw_samples(self.PARAMETERS, 20, 7))
        self.assertNotEqual(first, ts.draw_samples(self.PARAMETERS, 50, 8))

    def test_truncation_bounds_and_fixed_offset(self):
        drawn = [offsets for _sid, offsets in ts.draw_samples(self.PARAMETERS, 4000, 1)]
        a = [o["a"] for o in drawn]
        b = [o["b"] for o in drawn]
        self.assertTrue(all(-2.0 <= v < 1.0 for v in a))
        self.assertTrue(all(-3.0 <= v <= 6.0 for v in b))
        self.assertLess(min(b), -2.8)  # the truncated tails are reached, not clipped earlier
        self.assertGreater(max(b), 5.8)
        self.assertTrue(all(o["c"] == 1.5 for o in drawn))
        normalized = ts.normalize_spec({"samples": 10, "seed": 1, "max_evaluations": 11, "parameters": [
            {"name": "t", "unit": "mrad", "ops": rotate("HWP", [0, 0, 1]),
             "distribution": {"kind": "normal", "sigma": {"value": 0.1, "unit": "deg"}, "truncate_sigma": 2}}]})
        bounds = normalized["parameters"][0]["distribution"]["bounds"]
        self.assertAlmostEqual(bounds[1], 2 * math.radians(0.1) * 1000.0, places=12)
        self.assertAlmostEqual(bounds[0], -bounds[1], places=15)

    def test_spec_refusals(self):
        base = {"samples": 10, "seed": 1, "max_evaluations": 11}

        def refused(parameter, code, **extra):
            with self.assertRaises(wc.WorkflowError) as caught:
                ts.normalize_spec({**base, **extra, "parameters": [
                    {"name": "p", "unit": "mm", "ops": translate("L2", [0, 0, "$value"]), **parameter}]})
            self.assertEqual(caught.exception.code, code)

        refused({"distribution": {"kind": "uniform", "low": {"value": 1, "unit": "deg"}, "high": 1}},
                "UNIT_INCOMPATIBLE")
        refused({"distribution": {"kind": "normal", "sigma": 1}}, "SPEC_INVALID")  # no truncation
        refused({"distribution": {"kind": "normal", "sigma": 1, "truncate_sigma": 3, "bounds": [-1, 1]}},
                "SPEC_INVALID")
        refused({"distribution": {"kind": "uniform", "low": 1, "high": -1}}, "SPEC_INVALID")
        refused({"distribution": {"kind": "uniform", "low": -1, "high": 1}}, "BUDGET_EXCEEDED", max_evaluations=10)
        refused({"distribution": {"kind": "uniform", "low": -1, "high": 1}}, "SPEC_INVALID", seed=-1)

    def test_draw_only_and_verify_seed(self):
        spec = {"design": {"builder": "polarization_control"}, "samples": 30, "seed": 11, "max_evaluations": 31,
                "parameters": [{"name": "hwp", "unit": "deg", "ops": rotate("HWP", [0, 0, 1]),
                                "distribution": {"kind": "normal", "sigma": 4, "truncate_sigma": 3}}]}
        with TempDir() as tmp:
            first = ts.run_tolerance(spec, tmp / "one", draw_only=True)
            second = ts.run_tolerance(spec, tmp / "two", draw_only=True)
            self.assertEqual(first["status"], "DRAWN")
            self.assertFalse((tmp / "one" / "samples").exists())  # nothing was run
            self.assertEqual(first["samples_csv_sha256"], second["samples_csv_sha256"])
            self.assertEqual((tmp / "one" / "samples.csv").read_bytes(), (tmp / "two" / "samples.csv").read_bytes())
            other = ts.run_tolerance(spec, tmp / "three", draw_only=True, seed=12)
            self.assertNotEqual(first["samples_csv_sha256"], other["samples_csv_sha256"])
            code, out, err = invoke(ts.main, ["--verify-seed", str(tmp / "one")])
            self.assertEqual((code, out["status"]), (0, "REPRODUCED"), err)
            self.assertEqual(ts.verify_samples(tmp / "three")["seed"], 12)
            path = tmp / "one" / "samples.csv"
            path.write_text(path.read_text(encoding="utf-8").replace("s0003,", "s0003,1", 1), encoding="utf-8")
            code, out, _err = invoke(ts.main, ["--verify-seed", str(tmp / "one")])
            self.assertEqual((code, out["status"]), (1, "MISMATCH"))


class ToleranceStudyTests(unittest.TestCase):
    """Polarization tolerance study whose wavelength range leaves the retarder band (out-of-model samples)."""

    SPEC = {"design": {"builder": "polarization_control", "arguments": {}}, "targets": ["transmitted_power"],
            "samples": 16, "seed": 20261018, "max_evaluations": 17,
            "parameters": [
                {"name": "hwp", "unit": "deg", "ops": rotate("HWP", [0, 0, 1]),
                 "distribution": {"kind": "normal", "sigma": 4, "truncate_sigma": 3}},
                {"name": "wavelength", "unit": "nm", "nominal": 1030, "ops": WAVELENGTH_OPS,
                 "distribution": {"kind": "uniform", "low": -40, "high": 40}}]}

    @classmethod
    def setUpClass(cls):
        cls._tmp = TempDir()
        cls.dir = cls._tmp.__enter__()
        cls.summary = ts.run_tolerance(cls.SPEC, cls.dir / "study", report=True)
        cls.zero = ts.run_tolerance(cls.SPEC, cls.dir / "zero", samples=3, zero_tolerance=True, plots=False,
                                    report=False)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.__exit__(None, None, None)

    def test_counts_and_denominators(self):
        counts = self.summary["counts"]
        self.assertEqual(counts["requested"], 16)
        self.assertEqual(sum(counts["by_outcome"].values()), 16)
        self.assertEqual(len(self.summary["rows"]), 16)
        self.assertEqual(counts["pass_fraction"]["denominator"], 16)
        self.assertEqual(counts["pass_fraction"]["numerator"], counts["by_outcome"]["PASS"])
        evaluable = counts["by_outcome"]["PASS"] + counts["by_outcome"]["FAIL"]
        self.assertEqual(counts["pass_fraction_among_evaluable"]["denominator"], evaluable)
        self.assertGreater(counts["by_outcome"]["OUT_OF_MODEL"], 0)
        self.assertIn("simulated samples", counts["pass_fraction"]["definition"])

    def test_out_of_model_never_counts_as_pass(self):
        rows = self.summary["rows"]
        oom = [r for r in rows if r["outcome"] == "OUT_OF_MODEL"]
        for row in oom:
            wavelength = 1030 + row["param:wavelength"]
            self.assertTrue(wavelength < 1000 or wavelength > 1060, row)
            self.assertIn("MODEL_WAVELENGTH_OUTSIDE_BAND", row["model_scope_issues"])
        self.assertTrue(any(r["target:transmitted_power:status"] == "PASS" for r in oom))
        distribution = self.summary["target_distributions"]["transmitted_power"]
        in_model = [r for r in rows if r["outcome"] not in ("ERROR", "OUT_OF_MODEL")]
        self.assertEqual(distribution["count"], len(in_model))
        self.assertEqual(distribution["denominator"], 16)
        values = sorted(r["target:transmitted_power"] for r in in_model)
        self.assertEqual((distribution["min"], distribution["max"]), (values[0], values[-1]))
        self.assertEqual(sum(distribution["status_counts"].values()), 16)

    def test_samples_saved_and_match_runs(self):
        study = self.dir / "study"
        with open(study / "samples.csv", encoding="utf-8", newline="") as handle:
            saved = list(csv.DictReader(handle))
        self.assertEqual(len(saved), 16)
        for row, sample in zip(self.summary["rows"], saved):
            self.assertEqual(row["sample_id"], sample["sample_id"])
            self.assertEqual(repr(row["param:hwp"]), sample["offset:hwp"])
            record = wc.read_json(study / "samples" / row["sample_id"] / "sample.json")
            self.assertEqual(record["parameters"]["wavelength"]["value"], float(sample["value:wavelength"]))
            low, high = -12.0, 12.0
            self.assertTrue(low <= float(sample["offset:hwp"]) <= high)
        sampling = wc.read_json(study / "sampling.json")
        self.assertEqual(sampling["samples_csv_sha256"], wc.sha256_file(study / "samples.csv"))
        self.assertIn("parameters are sampled independently", sampling["assumptions"])
        self.assertEqual(ts.verify_samples(study)["status"], "REPRODUCED")
        for name in ("histogram_transmitted_power", "histogram_param_hwp", "histogram_param_wavelength"):
            self.assertTrue((study / "plots" / f"{name}.svg").is_file())
        report = (study / "report" / "report.md").read_text(encoding="utf-8")
        self.assertIn("## Tolerance sampling", report)
        self.assertIn("of all simulated samples", report)

    def test_zero_tolerance_equals_nominal_exactly(self):
        check = self.zero["zero_offset_check"]
        self.assertEqual((check["samples"], check["same_result_hash"], check["same_project_hash"]), (3, 3, 3))
        self.assertTrue(check["agrees"])
        nominal = {t["id"]: t["value"] for t in self.zero["nominal"]["targets"]}
        for row in self.zero["rows"]:
            self.assertEqual(row["target:transmitted_power"], nominal["transmitted_power"])
        self.assertEqual(self.zero["counts"]["by_outcome"]["PASS"], 3)


if __name__ == "__main__":
    unittest.main()
