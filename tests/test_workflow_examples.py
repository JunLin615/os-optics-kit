"""End-to-end checks of the three parameterized workflows and of the JSON specs in examples/specs.

The workflows are catalog-free (native ideal models) and run here with reduced studies. The catalog beam-expander
demonstration is skipped when no catalog release is installed.
Run from the repository root: python -m unittest discover -s tests -v
"""

import csv
from importlib.util import find_spec
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import unittest

from tests.workflow_fixtures import ROOT, TempDir, catalog_available, invoke, quiet_environment

quiet_environment()

from examples import alignment_analysis as aa  # noqa: E402
from examples import beam_expansion_focus as bef  # noqa: E402
from examples import build_report as br  # noqa: E402
from examples import catalog_candidates as cc  # noqa: E402
from examples import compare_runs as cr  # noqa: E402
from examples import design_workflow as dw  # noqa: E402
from examples import edit_project as ep  # noqa: E402
from examples import optical_designs as D  # noqa: E402
from examples import polarization_control as pc  # noqa: E402
from examples import sweep_parameters as sp  # noqa: E402
from examples import workflow_common as wc  # noqa: E402

HAS_MATPLOTLIB = find_spec("matplotlib") is not None
SPECS = ROOT / "examples" / "specs"
MAX_STDOUT_BYTES = 6000


def stdout_bytes(document):
    return len(json.dumps(document, separators=(",", ":")).encode("utf-8")) + 1


def assert_links_resolve(case, report_md):
    text = Path(report_md).read_text(encoding="utf-8")
    links = [link for link in re.findall(r"\]\(([^)]+)\)", text) if not link.startswith("http")]
    case.assertGreater(len(links), 0)
    for link in links:
        case.assertTrue((Path(report_md).parent / link).exists(), f"{report_md}: {link}")
    report = wc.read_json(Path(report_md).with_suffix(".json"))
    for artifact in report["artifacts"]:
        case.assertEqual(wc.sha256_file(Path(report_md).parent / artifact["path"]), artifact["sha256"])
    return text


def study(summary, name):
    return next(s for s in summary["studies"] if s["parameter"] == name)


def notable(entry, value):
    return next(n for n in entry["notable"] if n["value"] == value)


class StudySyntaxTests(unittest.TestCase):
    def test_values_ranges_and_units(self):
        parameters = D.DESIGN_INFO["expander_focus"]["parameters"]
        self.assertEqual(dw.parse_study("gap_error_mm=-500um,0,0.5mm", parameters)["values"], [-0.5, 0.0, 0.5])
        self.assertEqual(dw.parse_study("f3_mm=50..150:3", parameters)["values"], [50.0, 100.0, 150.0])
        self.assertEqual(dw.parse_study("wavelength_nm=0.78um", parameters)["values"], [780.0])
        for bad in ("gap_error_mm", "lens_band_nm=1,2", "nope_mm=1", "gap_error_mm=1mW", "gap_error_mm=1,1"):
            with self.assertRaises(wc.WorkflowError, msg=bad):
                dw.parse_study(bad, parameters)

    def test_default_studies_are_valid_and_within_budget(self):
        for module, design, samples in ((bef, "expander_focus", 21), (pc, "polarization_control", 13),
                                        (aa, "alignment", 12)):
            parameters = D.DESIGN_INFO[design]["parameters"]
            parsed = [dw.parse_study(text, parameters) for text in module.DEFAULT_STUDIES]
            self.assertEqual(sum(len(s["values"]) for s in parsed), samples, design)
            self.assertLessEqual(1 + samples + 1, module.config()["max_evaluations"])

    def test_help_is_english_on_stdout(self):
        for script in ("beam_expansion_focus.py", "polarization_control.py", "alignment_analysis.py"):
            done = subprocess.run([sys.executable, str(ROOT / "examples" / script), "--help"], cwd=ROOT,
                                  capture_output=True, text=True, timeout=120)
            self.assertEqual(done.returncode, 0, done.stderr)
            for text in ("Usage:", "--out", "--study", "Independent check", "Exit code"):
                self.assertIn(text, done.stdout, script)


class BeamExpansionFocusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = TempDir()
        cls.dir = cls._tmp.__enter__()
        cls.code, cls.out, cls.err = invoke(bef.main, [
            "--out", str(cls.dir / "wf"), "--study", "gap_error_mm=0,5", "--study", "f3_mm=150",
            "--study", "wavelength_nm=1300", "--study", "waist_radius_mm=3,4"])

    @classmethod
    def tearDownClass(cls):
        cls._tmp.__exit__(None, None, None)

    def test_exit_code_and_summary_fields(self):
        self.assertEqual(self.code, 0, self.err)
        out = self.out
        for key in ("status", "inputs", "nominal", "closed_form", "studies", "evaluations", "engine_build_id",
                    "paths"):
            self.assertIn(key, out)
        self.assertEqual((out["status"], out["evaluations"]), ("PASS", 7))
        self.assertEqual([t["id"] for t in out["nominal"]["targets"]],
                         ["expanded_radius", "spot_radius", "waist_location"])
        self.assertLess(stdout_bytes(out), MAX_STDOUT_BYTES)
        full = wc.read_json(self.dir / "wf" / "workflow.json")
        self.assertEqual(full["provenance"][0]["case_id"], "SEED-2-2")
        self.assertIsNone(out["catalog_version"])
        self.assertIsNone(full["catalog"]["catalog_version"])
        self.assertIn("installed_catalog", full)

    def test_unmet_and_diagnostic_outcomes_are_visible(self):
        gap = study(self.out, "gap_error_mm")
        self.assertEqual(gap["counts"], {"PASS": 1, "FAIL": 1})
        self.assertEqual(notable(gap, 5.0)["targets"]["spot_radius"], "FAIL")
        self.assertEqual(notable(study(self.out, "f3_mm"), 150.0)["targets"], {"spot_radius": "FAIL"})
        band = notable(study(self.out, "wavelength_nm"), 1300.0)
        self.assertEqual((band["outcome"], band["status"]), ("OUT_OF_MODEL", "FAIL"))
        self.assertIn("MODEL_WAVELENGTH_OUTSIDE_BAND", band["codes"])
        waist = study(self.out, "waist_radius_mm")
        truncated = notable(waist, 3.0)
        self.assertEqual((truncated["outcome"], truncated["status"]), ("PASS", "WARNING"))
        self.assertIn("GAUSSIAN_TRUNCATION", truncated["codes"])
        severe = notable(waist, 4.0)
        self.assertEqual(severe["outcome"], "OUT_OF_MODEL")
        self.assertIn("GAUSSIAN_SEVERE_TRUNCATION", severe["codes"])
        with open(self.dir / "wf" / "studies" / "waist_radius_mm" / "table.csv", encoding="utf-8") as handle:
            table = list(csv.DictReader(handle))
        self.assertEqual([row["outcome"] for row in table], ["PASS", "OUT_OF_MODEL"])
        self.assertEqual(table[1]["target:spot_radius"], "")

    def test_closed_form_agreement(self):
        cf = self.out["closed_form"]
        self.assertEqual(cf["compared"], cf["agreed"])
        self.assertEqual((cf["compared"], cf["not_compared"]), (18, 3))  # 7 runs x 3 targets; 4 mm has no values
        rows = wc.read_json(self.dir / "wf" / "studies" / "gap_error_mm" / "closed_form.json")["rows"]
        spot = next(r for r in rows if r["parameter_value"] == 5.0 and r["quantity"] == "spot_radius")
        self.assertAlmostEqual(spot["reference"], 0.03651066721345836, delta=1e-15)
        self.assertLessEqual(spot["rel_diff"], 1e-9)

    def test_reports_link_existing_artifacts(self):
        text = assert_links_resolve(self, self.dir / "wf" / "report" / "report.md")
        for heading in ("## Design inputs", "## Independent closed-form check (nominal)", "## Studies",
                        "## Freshness", "## Artifacts"):
            self.assertIn(heading, text)
        for name in ("gap_error_mm", "f3_mm", "wavelength_nm", "waist_radius_mm"):
            study_text = assert_links_resolve(self, self.dir / "wf" / "studies" / name / "report" / "report.md")
            self.assertIn("## Independent closed-form check", study_text)
        compare = wc.read_json(self.dir / "wf" / "studies" / "gap_error_mm" / "compare" / "compare.json")
        self.assertEqual(compare["reference"], "nominal")
        self.assertEqual([r["label"] for r in compare["runs"]], ["nominal", "s0001", "s0002"])

    @unittest.skipUnless(HAS_MATPLOTLIB, "matplotlib is not installed")
    def test_plots_of_changing_targets(self):
        plots = self.dir / "wf" / "studies" / "gap_error_mm" / "plots"
        self.assertEqual(study(self.out, "gap_error_mm")["notable"][0]["value"], 5.0)
        svg = (plots / "spot_radius.svg").read_text(encoding="utf-8")
        for text in ("gap_error_mm [mm]", "spot_radius [mm]", "FAIL (1)", "target maximum 0.02"):
            self.assertIn(text, svg)
        full = wc.read_json(self.dir / "wf" / "workflow.json")
        self.assertEqual(study(full, "f3_mm")["plots"], ["spot_radius", "waist_location"])
        self.assertTrue((self.dir / "wf" / "nominal" / "plots" / "power_distribution.png").is_file())

    def test_inputs_change_the_design(self):
        code, out, err = invoke(bef.main, ["--out", str(self.dir / "f2"), "--no-studies", "--f2-mm", "100",
                                           "--gap-error-mm", "250um"])
        self.assertEqual(code, dw.EXIT_CODES[out["status"]], err)
        self.assertEqual(out["inputs"]["gap_error_mm"], 0.25)
        targets = {t["id"]: t for t in wc.read_json(self.dir / "f2" / "workflow.json")["nominal"]["targets"]}
        reference = D.gaussian_expander_focus_reference(f2_mm=100.0, gap_error_mm=0.25)
        self.assertAlmostEqual(targets["expanded_radius"]["value"], reference["expanded_radius_mm"], delta=1e-9)
        self.assertNotAlmostEqual(targets["expanded_radius"]["value"],
                                  self.out["nominal"]["targets"][0]["value"], delta=0.1)
        self.assertEqual(out["closed_form"]["compared"], out["closed_form"]["agreed"])
        project = wc.read_json(self.dir / "f2" / "nominal" / "project.json")
        l2 = next(c for c in project["components"] if c["instance_id"] == "L2")
        self.assertAlmostEqual(l2["nominal_pose"]["position_mm"][0], 150.25, delta=1e-12)

    def test_exit_codes(self):
        code, out, _err = invoke(bef.main, ["--out", str(self.dir / "strict"), "--no-studies",
                                            "--max-spot-radius-mm", "10um"])
        self.assertEqual((code, out["status"]), (1, "FAIL"))
        code, out, _err = invoke(bef.main, ["--out", str(self.dir / "oom"), "--no-studies", "--waist-radius-mm", "4"])
        self.assertEqual((code, out["status"]), (3, "OUT_OF_MODEL"))
        code, out, err = invoke(bef.main, ["--out", str(self.dir / "budget"), "--max-evaluations", "5"])
        self.assertEqual((code, out), (4, None))
        self.assertEqual(json.loads(err.strip().splitlines()[-1])["error"]["code"], "BUDGET_EXCEEDED")
        self.assertFalse((self.dir / "budget").exists())
        code, out, err = invoke(bef.main, ["--out", str(self.dir / "bad"), "--study", "f3_mm=0"])
        self.assertEqual((code, json.loads(err.strip().splitlines()[-1])["error"]["code"]), (4, "INPUT_INVALID"))
        self.assertFalse((self.dir / "bad").exists())
        code, _out, err = invoke(bef.main, ["--out", str(self.dir / "wf"), "--no-studies"])
        self.assertEqual((code, json.loads(err.strip().splitlines()[-1])["error"]["code"]), (4, "OUTPUT_EXISTS"))
        code, out, _err = invoke(bef.main, ["--list-parameters"])
        self.assertEqual((code, out["parameters"]["gap_error_mm"]["unit"]), (0, "mm"))


class PolarizationControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = TempDir()
        cls.dir = cls._tmp.__enter__()
        cls.code, cls.out, cls.err = invoke(pc.main, [
            "--out", str(cls.dir / "wf"), "--study", "hwp_angle_deg=0,22.5,67.5", "--study", "wavelength_nm=1064"])

    @classmethod
    def tearDownClass(cls):
        cls._tmp.__exit__(None, None, None)

    def test_summary_and_unmet_angles(self):
        self.assertEqual(self.code, 0, self.err)
        full = wc.read_json(self.dir / "wf" / "workflow.json")
        self.assertEqual(full["nominal"]["targets"][0]["value"], 0.0008909999999999998)
        self.assertLess(stdout_bytes(self.out), MAX_STDOUT_BYTES)
        angle = study(self.out, "hwp_angle_deg")
        self.assertEqual(angle["counts"], {"PASS": 1, "FAIL": 2})
        self.assertEqual(notable(angle, 0.0)["targets"], {"transmitted_power": "FAIL", "output_azimuth": "FAIL"})
        rows = {r["param:hwp_angle_deg"]: r for r in wc.read_json(
            self.dir / "wf" / "studies" / "hwp_angle_deg" / "summary.json")["rows"]}
        self.assertAlmostEqual(rows[67.5]["target:transmitted_power"], 1e-3 * 0.99 * 0.9 / 1000.0, delta=1e-18)

    def test_out_of_band_wavelength_is_out_of_model_with_passing_targets(self):
        entry = notable(study(self.out, "wavelength_nm"), 1064.0)
        self.assertEqual((entry["outcome"], entry["status"], entry["targets"]),
                         ("OUT_OF_MODEL", "OUT_OF_MODEL_SCOPE", "all PASS"))
        self.assertEqual(entry["codes"], ["MODEL_WAVELENGTH_OUTSIDE_BAND"])

    def test_closed_form_includes_stokes_and_malus(self):
        cf = self.out["closed_form"]
        self.assertEqual(cf["compared"], cf["agreed"])
        self.assertEqual(set(cf["max_abs_diff"]), {"Jones calculus", "Malus law"})
        data = wc.read_json(self.dir / "wf" / "studies" / "hwp_angle_deg" / "closed_form.json")
        quantities = {r["quantity"] for r in data["rows"]}
        self.assertTrue({"after_hwp stokes_s1", "after_hwp stokes_s2", "after_hwp stokes_s3",
                         "output_azimuth", "transmitted_power"} <= quantities)
        self.assertAlmostEqual(data["analysis"]["s0001"]["after_hwp"]["stokes_s1"], 1.0, delta=1e-12)
        assert_links_resolve(self, self.dir / "wf" / "report" / "report.md")
        assert_links_resolve(self, self.dir / "wf" / "studies" / "wavelength_nm" / "report" / "report.md")

    def test_inputs_change_the_design(self):
        code, out, err = invoke(pc.main, ["--out", str(self.dir / "achromat"), "--no-studies", "--wavelength-nm",
                                          "1064", "--retarder-model", "ACHROMATIC", "--retarder-band-nm",
                                          "1000,1100", "--polarizer-angle-deg", "30", "--min-power-w", "0.5mW",
                                          "--azimuth-target-deg", "45"])
        self.assertEqual(code, 0, err)
        power = wc.read_json(self.dir / "achromat" / "workflow.json")["nominal"]["targets"][0]["value"]
        expected = D.malus_power_reference(hwp_angle_deg=22.5, polarizer_angle_deg=30.0)
        self.assertAlmostEqual(power, expected, delta=1e-15)
        self.assertEqual(out["inputs"]["min_power_w"], 0.0005)
        code, out, _err = invoke(pc.main, ["--out", str(self.dir / "oom"), "--no-studies", "--wavelength-nm", "1064"])
        self.assertEqual((code, out["status"]), (3, "OUT_OF_MODEL"))
        code, out, _err = invoke(pc.main, ["--out", str(self.dir / "qwp"), "--no-studies", "--retardance-waves",
                                           "0.25", "--hwp-angle-deg", "45"])
        self.assertEqual((code, out["status"]), (1, "FAIL"))  # azimuth indeterminate; another selected requirement is unmet


class AlignmentAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = TempDir()
        cls.dir = cls._tmp.__enter__()
        cls.code, cls.out, cls.err = invoke(aa.main, [
            "--out", str(cls.dir / "wf"), "--study", "mirror_tilt_local_y_mrad=0.25,60",
            "--study", "source_offset_z_mm=0.15", "--study", "mirror_tilt_local_x_mrad=0.5"])

    @classmethod
    def tearDownClass(cls):
        cls._tmp.__exit__(None, None, None)

    def test_position_miss_and_far_plane_not_reached(self):
        self.assertEqual(self.code, 0, self.err)
        self.assertLess(stdout_bytes(self.out), MAX_STDOUT_BYTES)
        tilt = study(self.out, "mirror_tilt_local_y_mrad")
        miss = notable(tilt, 0.25)
        self.assertEqual((miss["outcome"], miss["targets"]), ("FAIL", {"far_position_u": "FAIL"}))
        self.assertAlmostEqual(miss["analysis"]["pointing_u_mrad"], 0.5, delta=1e-9)
        lost = notable(tilt, 60.0)
        self.assertEqual(lost["outcome"], "FAIL")  # unmet captured-power requirement, position remains indeterminate
        self.assertIn("OBSERVATION_NOT_REACHED", lost["codes"])
        self.assertIsNone(lost["analysis"]["far_u_mm"])
        data = wc.read_json(self.dir / "wf" / "studies" / "mirror_tilt_local_y_mrad" / "closed_form.json")
        self.assertEqual(data["analysis"]["s0002"]["reached"], {"near": True, "far": False})
        offset = notable(study(self.out, "source_offset_z_mm"), 0.15)
        self.assertEqual(offset["targets"], {"near_position_v": "FAIL", "far_position_v": "FAIL"})
        self.assertAlmostEqual(offset["analysis"]["pointing_v_mrad"], 0.0, delta=1e-9)

    def test_closed_form_and_two_theta(self):
        cf = self.out["closed_form"]
        self.assertEqual(cf["compared"], cf["agreed"])
        self.assertEqual(cf["not_compared"], 8)  # 60 mrad: far positions, pointing and direction cosines
        rows = wc.read_json(self.dir / "wf" / "studies" / "mirror_tilt_local_x_mrad" / "closed_form.json")["rows"]
        small = next(r for r in rows if r["method"] == "small-angle 2 theta" and r["quantity"].endswith("_v"))
        self.assertAlmostEqual(small["reference"], -math.sqrt(2) / 2, delta=1e-15)
        self.assertLess(small["abs_diff"], 1e-6)
        assert_links_resolve(self, self.dir / "wf" / "report" / "report.md")

    def test_synthetic_measurement_variant(self):
        code, out, err = invoke(aa.main, ["--out", str(self.dir / "syn"), "--no-studies",
                                          "--mirror-tilt-local-y-mrad", "1", "--synthetic-measurements"])
        self.assertEqual((code, out["status"]), (1, "FAIL"), err)  # 1 mrad tilt misses the position tolerance
        synthetic = out["synthetic"]
        self.assertTrue(synthetic["synthetic"])
        self.assertTrue(synthetic["label"].startswith("SYNTHETIC"))
        self.assertEqual(synthetic["expected_small_angle_pointing_mrad"], 2.0)
        self.assertAlmostEqual(synthetic["fitted_pointing_mrad"], 1.997333, delta=1e-5)
        self.assertTrue(synthetic["within_3_standard_errors"])
        self.assertEqual(out["evaluations"], 2)
        with open(self.dir / "syn" / "synthetic" / "synthetic_measurements.csv", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(row["data_origin"].startswith("SYNTHETIC") for row in rows))
        text = assert_links_resolve(self, self.dir / "syn" / "report" / "report.md")
        self.assertIn("## Synthetic-measurement fit (SYNTHETIC data)", text)
        code, _out, err = invoke(aa.main, ["--out", str(self.dir / "syn2"), "--no-studies",
                                           "--synthetic-measurements", "--synthetic-residuals-mm", "0,0"])
        self.assertEqual((code, json.loads(err.strip().splitlines()[-1])["error"]["code"]), (4, "BAD_ARGUMENT"))

    def test_inputs_change_the_design(self):
        code, out, err = invoke(aa.main, ["--out", str(self.dir / "far"), "--no-studies", "--far-plane-mm", "800",
                                          "--source-tilt-local-x-mrad", "0.2"])
        self.assertEqual(code, 1, err)
        self.assertEqual(out["nominal"]["targets"][3]["status"], "FAIL")  # far_position_v
        analysis = wc.read_json(self.dir / "far" / "workflow.json")["nominal"]["analysis"]
        self.assertAlmostEqual(analysis["separation_mm"], 700.0, delta=1e-9)
        self.assertAlmostEqual(analysis["pointing_v_mrad"], -0.2, delta=1e-9)
        self.assertAlmostEqual(analysis["far_v_mm"], -0.18, delta=1e-6)  # 0.2 mrad over 100 + 800 mm


class SpecFileTests(unittest.TestCase):
    def test_every_spec_is_documented_and_valid(self):
        names = sorted(p.name for p in SPECS.glob("*.json"))
        edit_and_sweep = ["beam_expander_l2_sweep.json", "beam_expander_move_l2.json",
                          "beam_expander_swap_l2.json", "polarization_hwp_wavelength_sweep.json",
                          "polarization_threshold.json"]
        self.assertTrue(set(edit_and_sweep) <= set(names), names)
        for name in names:  # sensitivity_*/tolerance_* specs are validated by their own tests
            spec = wc.read_json(SPECS / name)
            if name.startswith("compute_budget_"):
                self.assertEqual(set(spec), {"ops"})
                self.assertEqual(spec["ops"][0]["op"], "set_compute_budget")
                continue
            self.assertIsInstance(spec.get("description"), str, name)
            self.assertGreater(len(spec["description"]), 40, name)
        base = D.polarization_control(azimuth_target_deg=45.0)
        for name in edit_and_sweep:
            spec = wc.read_json(SPECS / name)
            if "parameters" in spec:
                sp.normalize_spec(spec)
            else:
                ep.normalize_ops(base, spec)
        for path in SPECS.glob("*.json"):
            self.assertNotIn(b"\r\n", path.read_bytes(), path.name)


class PolarizationSpecDemoTests(unittest.TestCase):
    """#5 helpers reused on the polarization design: edit with units, two-parameter sweep, heatmap, report."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = TempDir()
        cls.dir = cls._tmp.__enter__()
        code, _out, err = invoke(pc.main, ["--out", str(cls.dir / "design"), "--no-studies"])
        assert code == 0, err
        cls.edit = invoke(ep.main, [str(cls.dir / "design" / "nominal" / "project.json"), "--edits",
                                    str(SPECS / "polarization_threshold.json"), "--out", str(cls.dir / "strict.json"),
                                    "--run", str(cls.dir / "strict-run")])
        cls.sweep = invoke(sp.main, [str(SPECS / "polarization_hwp_wavelength_sweep.json"), "--base",
                                     str(cls.dir / "strict.json"), "--out", str(cls.dir / "sweep")])

    @classmethod
    def tearDownClass(cls):
        cls._tmp.__exit__(None, None, None)

    def test_edit_converts_units(self):
        code, out, err = self.edit
        self.assertEqual((code, out["run"]["outcome"]), (0, "PASS"), err)
        target = next(t for t in wc.read_json(self.dir / "strict.json")["targets"]
                      if t["target_id"] == "transmitted_power")
        self.assertAlmostEqual(target["minimum"], 0.00085, delta=1e-18)

    def test_sweep_outcomes_and_rotation_semantics(self):
        code, out, err = self.sweep
        self.assertEqual(code, 0, err)
        self.assertEqual(out["counts"]["by_outcome"], {"ERROR": 0, "OUT_OF_MODEL": 9, "UNKNOWN": 0, "FAIL": 6,
                                                       "PASS": 3, "NOT_APPLICABLE": 0})
        self.assertEqual(out["best"]["parameters"]["hwp_turn"]["value"], 0)
        rows = wc.read_json(self.dir / "sweep" / "summary.json")["rows"]
        for row in rows:
            turn, wavelength = row["param:hwp_turn"], row["param:wavelength"]
            expected = D.jones_polarization_reference(hwp_angle_deg=22.5 + turn, wavelength_nm=wavelength)["power_w"]
            self.assertAlmostEqual(row["target:transmitted_power"] / expected, 1.0, delta=1e-12)
            if wavelength == 1064:
                self.assertEqual(row["outcome"], "OUT_OF_MODEL")
        self.assertEqual({r["target:transmitted_power:status"] for r in rows if r["param:wavelength"] == 1064
                          and abs(r["param:hwp_turn"]) <= 5}, {"PASS"})

    @unittest.skipUnless(HAS_MATPLOTLIB, "matplotlib is not installed")
    def test_heatmap_comparison_and_report(self):
        from examples import plot_results as pr

        plot = pr.plot_sweep(self.dir / "sweep", "target:transmitted_power")
        svg = Path(plot["out"]["svg"]).read_text(encoding="utf-8")
        for text in ("hwp_turn [deg]", "wavelength [nm]", "OUT_OF_MODEL (model scope issue) (9)"):
            self.assertIn(text, svg)
        document = cr.compare([self.dir / "design" / "nominal" / "run", self.dir / "strict-run"])
        self.assertEqual(document["input_differences"][0]["input"], "target:transmitted_power.minimum")
        report = br.build_report([self.dir / "sweep"], project=self.dir / "strict.json",
                                 plots=[self.dir / "sweep" / "plots"], changes=self.dir / "strict.changes.json")
        text = assert_links_resolve(self, report["paths"]["markdown"])
        self.assertIn("| 0 | set_target | target transmitted_power | minimum | 0.0008 | 0.00085 |", text)


@unittest.skipUnless(catalog_available(), "no catalog release is installed")
class CatalogBeamExpanderDemoTests(unittest.TestCase):
    """#5 primary demonstration on the catalog beam_expander example (catalog-gated)."""

    @classmethod
    def setUpClass(cls):
        from examples import beam_expander

        cls._tmp = TempDir()
        cls.dir = d = cls._tmp.__enter__()
        cls.base = invoke(beam_expander.main, ["--out", str(d / "base-run")])
        cls.candidates = invoke(cc.main, [
            "--category", "LENS", "--form", "PLANO_CONVEX_LENS", "--wavelength-nm", "780",
            "--range", "focal_length_mm=95..105", "--range", "outer_diameter_mm=12.7..12.7",
            "--near", "focal_length_mm=100", "--limit", "8", "--out", str(d / "l2-candidates.csv")])
        cls.swap = invoke(ep.main, [str(d / "base-run" / "project.json"), "--edits",
                                    str(SPECS / "beam_expander_swap_l2.json"), "--out", str(d / "swapped.json"),
                                    "--run", str(d / "swapped-run")])
        cls.sweep = invoke(sp.main, [str(SPECS / "beam_expander_l2_sweep.json"), "--base", str(d / "swapped.json"),
                                     "--out", str(d / "l2-sweep")])
        cls.move = invoke(ep.main, [str(d / "swapped.json"), "--edits", str(SPECS / "beam_expander_move_l2.json"),
                                    "--out", str(d / "final.json"), "--run", str(d / "final-run")])

    @classmethod
    def tearDownClass(cls):
        cls._tmp.__exit__(None, None, None)

    def test_bounded_candidate_query_lists_the_swapped_lens(self):
        code, out, err = self.candidates
        self.assertEqual(code, 0, err)
        self.assertLessEqual(out["shown"], 8)
        sku = wc.read_json(SPECS / "beam_expander_swap_l2.json")["ops"][1]["optical_sku"]
        self.assertIn(sku, [row["optical_sku"] for row in out["rows"]])

    def test_validated_edit_and_sweep_with_unmet_samples(self):
        code, out, err = self.swap
        self.assertEqual(code, 1, err)
        targets = {t["id"]: t for t in out["run"]["targets"]}
        self.assertEqual((targets["expanded_waist"]["status"], targets["output_beam_radius"]["status"]),
                         ("FAIL", "PASS"))
        code, out, err = self.sweep
        self.assertEqual(code, 0, err)
        self.assertEqual(out["best"]["parameters"]["l2_shift"]["value"], 0.54)
        by = out["counts"]["by_outcome"]
        self.assertGreater(by["FAIL"], 0)
        self.assertGreater(by["PASS"], 0)
        rows = wc.read_json(self.dir / "l2-sweep" / "summary.json")["rows"]
        waist = [r["target:expanded_waist"] for r in rows]
        radius = [r["target:output_beam_radius"] for r in rows]
        self.assertGreater(max(waist) / min(waist), 1.5)          # collimation measure: strong response
        self.assertLess(max(radius) / min(radius) - 1.0, 1e-3)   # radius at the plane: < 0.1 %
        code, out, err = self.move
        self.assertEqual((code, out["run"]["outcome"]), (0, "PASS"), err)
        self.assertEqual(out["run"]["targets"][0]["value"], rows[6]["target:expanded_waist"])

    def test_comparison_plots_and_report(self):
        d = self.dir
        document = cr.compare([d / "base-run", d / "swapped-run", d / "final-run"], project=d / "final.json")
        waist = next(t for t in document["targets"] if t["id"] == "output_beam_radius")
        self.assertEqual(waist["runs"]["base-run"], {"missing": True})
        self.assertEqual([r["outcome_label"] for r in document["runs"]], ["HISTORICAL PASS", "HISTORICAL FAIL",
                                                                          "PASS"])
        plots = []
        if HAS_MATPLOTLIB:
            from examples import plot_results as pr

            for column in ("target:expanded_waist", "target:output_beam_radius"):
                plots.append(pr.plot_sweep(d / "l2-sweep", column)["out"]["png"])
        report = br.build_report([d / "l2-sweep"], project=d / "swapped.json", plots=plots or None,
                                 changes=d / "swapped.changes.json")
        text = assert_links_resolve(self, report["paths"]["markdown"])
        self.assertIn("set_component_product", text)
        self.assertIn("Best sample: s0007", text)


if __name__ == "__main__":
    unittest.main()
