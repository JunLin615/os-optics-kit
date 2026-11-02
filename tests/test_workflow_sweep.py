"""Offline checks of examples/sweep_parameters.py on catalog-free Projects.

Run from the repository root: python -m unittest discover -s tests -v
"""

import csv
import json
from pathlib import Path
import unittest

from tests.workflow_fixtures import TempDir, by_id, expander, invoke, quiet_environment

quiet_environment()

from examples import sweep_parameters as sp  # noqa: E402
from examples import workflow_common as wc  # noqa: E402

# L2 x position (0.5 mm puts it inside L1: refused at run) x wavelength (L3 is modelled for 700-900 nm only)
MIXED_SPEC = {
    "parameters": [
        {"name": "l2_x", "unit": "mm", "values": [0.5, 200.0, 202.0],
         "ops": [{"op": "set_component_pose", "instance_id": "L2", "position_mm": ["$value", 0, 0]}]},
        {"name": "wavelength", "unit": "nm", "values": [780.0, 1064.0],
         "ops": [{"op": "set_source_model", "instance_id": "SRC", "changes": {"wavelength_nm": "$value"}}]},
    ],
    "max_evaluations": 6,
    "objective": {"column": "target:spot_radius", "sense": "minimize"},
    "derived": [{"name": "spot_um", "op": "mul", "a": "target:spot_radius", "b": 1000}],
}


class MixedOutcomeSweepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = TempDir()
        cls.dir = cls._tmp.__enter__()
        cls.base = expander(l3_band_nm=(700, 900))
        cls.summary = sp.run_sweep(MIXED_SPEC, cls.dir / "study", base=cls.base)
        cls.rows = {r["sample_id"]: r for r in cls.summary["rows"]}

    @classmethod
    def tearDownClass(cls):
        cls._tmp.__exit__(None, None, None)

    def test_every_outcome_class_stays_visible(self):
        outcomes = {sid: r["outcome"] for sid, r in self.rows.items()}
        self.assertEqual(outcomes, {"s0001": "ERROR", "s0002": "ERROR", "s0003": "PASS", "s0004": "OUT_OF_MODEL",
                                    "s0005": "FAIL", "s0006": "OUT_OF_MODEL"})
        counts = self.summary["counts"]
        self.assertEqual(counts["requested"], 6)
        self.assertEqual(counts["by_outcome"], {"ERROR": 2, "OUT_OF_MODEL": 2, "UNKNOWN": 0, "FAIL": 1, "PASS": 1, "NOT_APPLICABLE": 0})
        self.assertEqual((counts["pass_fraction"]["numerator"], counts["pass_fraction"]["denominator"]), (1, 6))
        self.assertEqual(counts["pass_fraction_among_evaluable"]["denominator"], 2)

    def test_error_and_out_of_model_records(self):
        error = self.rows["s0001"]
        self.assertEqual((error["error_stage"], error["error_code"]), ("run", "RUN_REFUSED"))
        self.assertIn("OPTICAL_BODIES_OVERLAP", error["error_problems"])
        self.assertIsNone(error["run_dir"])
        oom = self.rows["s0006"]  # L2 2 mm off: spot target FAIL, but the model-scope issue decides the class
        self.assertEqual(oom["status"], "FAIL")
        self.assertFalse(oom["in_model_scope"])
        self.assertIn("MODEL_WAVELENGTH_OUTSIDE_BAND", oom["model_scope_issues"])
        record = wc.read_json(self.dir / "study" / "samples" / "s0006" / "sample.json")
        self.assertEqual(record["parameters"]["wavelength"], {"value": 1064.0, "unit": "nm"})
        self.assertEqual(record["identity"]["engine_build_id"], wc.engine_identity()["engine_build_id"])
        self.assertIsNone(record["identity"]["catalog_version"])

    def test_rows_keep_parameters_identity_and_paths(self):
        row = self.rows["s0005"]
        self.assertEqual((row["param:l2_x"], row["param:l2_x:unit"], row["param:wavelength"]), (202.0, "mm", 780.0))
        self.assertEqual((row["target:spot_radius:status"], row["target:spot_radius:unit"]), ("FAIL", "mm"))
        self.assertAlmostEqual(row["derived:spot_um"], row["target:spot_radius"] * 1000)
        self.assertTrue((self.dir / "study" / row["run_dir"] / "manifest.json").is_file())
        for key in ("project_hash", "input_hash", "result_hash", "engine_build_id"):
            self.assertTrue(row[key])
        self.assertTrue((self.dir / "study" / "samples" / "s0001" / "project.json").is_file())

    def test_best_sample_among_feasible(self):
        best = self.summary["best"]
        self.assertEqual(best["sample_id"], "s0003")  # OUT_OF_MODEL samples are never feasible
        self.assertEqual(best["objective"]["unit"], "mm")
        self.assertEqual(self.summary["status"], "COMPLETED")

    def test_files_and_study_record(self):
        study = wc.read_json(self.dir / "study" / "study.json")
        self.assertEqual(study["base_project"]["project_hash"], wc.check(self.base)["project_hash"])
        self.assertEqual(study["budget"], {"max_evaluations": 6, "samples": 6})
        self.assertIn("finished", study)
        self.assertIsNone(study["catalog"]["catalog_version"])
        self.assertIsNone(self.summary["identity"]["catalog_version"])
        self.assertIn("installed_catalog", study)
        with open(self.dir / "study" / "table.csv", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual([r["outcome"] for r in rows], [self.rows[f"s000{i}"]["outcome"] for i in range(1, 7)])
        self.assertNotIn(b"\r\n", (self.dir / "study" / "table.csv").read_bytes())
        compact = sp.compact(self.summary)
        self.assertLessEqual(len(compact["top_rows"]), 5)
        self.assertEqual(compact["top_rows"][0]["sample_id"], "s0003")

    def test_rerun_is_deterministic(self):
        again = sp.run_sweep(MIXED_SPEC, self.dir / "study2", base=self.base)
        for first, second in zip(self.summary["rows"], again["rows"]):
            for key in ("outcome", "result_hash", "input_hash", "target:spot_radius"):
                self.assertEqual(first.get(key), second.get(key))


class SweepSpecTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TempDir()
        self.dir = self._tmp.__enter__()

    def tearDown(self):
        self._tmp.__exit__(None, None, None)

    def test_budget_is_refused_before_running(self):
        spec = {**MIXED_SPEC, "max_evaluations": 5}
        with self.assertRaises(wc.WorkflowError) as caught:
            sp.run_sweep(spec, self.dir / "study", base=expander())
        self.assertEqual(caught.exception.code, "BUDGET_EXCEEDED")
        self.assertFalse((self.dir / "study").exists())

    def test_unit_error_is_refused_before_running(self):
        spec = {"parameters": [{"name": "w", "unit": "deg", "values": [1.0],
                                "ops": [{"op": "set_source_model", "instance_id": "SRC",
                                         "changes": {"wavelength_nm": "$value"}}]}], "max_evaluations": 1}
        with self.assertRaises(Exception) as caught:
            sp.run_sweep(spec, self.dir / "study", base=expander())
        self.assertEqual(caught.exception.details["op_code"], "UNIT_INCOMPATIBLE")
        self.assertFalse((self.dir / "study").exists())

    def test_no_feasible_outcome_via_cli(self):
        base = wc.write_json(self.dir / "base.json", expander())
        spec = {"base_project": "base.json", "max_evaluations": 3,
                "parameters": [{"name": "shift", "unit": "um", "range": {"start": 2000, "stop": 4000, "count": 3},
                                "ops": [{"op": "transform", "selector": {"ids": ["L2"]},
                                         "translation_mm": [0, 0, "$value"], "frame": {"component": "L2"}}]}],
                "constraints": [{"column": "target:spot_radius", "max": {"value": 18, "unit": "um"}}],
                "objective": {"column": "target:spot_radius", "sense": "minimize"}}
        spec_path = wc.write_json(self.dir / "spec.json", spec)
        code, out, _err = invoke(sp.main, [str(spec_path), "--out", str(self.dir / "study")])
        self.assertEqual((code, out["status"]), (1, "NO_FEASIBLE"))
        self.assertIsNone(out["best"])
        self.assertEqual(out["no_feasible"]["failed_constraints"], {"target:spot_radius within [None, 0.018]": 3})
        self.assertEqual(out["units"]["parameters"], {"shift": "um"})
        rows = wc.read_json(self.dir / "study" / "summary.json")["rows"]
        moved = by_id(wc.read_json(self.dir / "study" / "samples" / "s0001" / "project.json")["components"],
                      "instance_id", "L2")["nominal_pose"]["position_mm"]
        self.assertEqual(moved[0], 202.0)  # 2000 um along L2's local +Z (design +X)
        self.assertEqual([r["param:shift"] for r in rows], [2000.0, 3000.0, 4000.0])
        self.assertEqual(base.read_bytes(), wc.json_text(expander()).encode())
        code, _out, err = invoke(sp.main, [str(spec_path), "--out", str(self.dir / "study")])
        self.assertEqual((code, json.loads(err)["error"]["code"]), (4, "OUTPUT_EXISTS"))

    def test_build_function_and_scaled_placeholder_and_stop_rule(self):
        spec = {"parameters": [{"name": "f3", "unit": "mm", "values": [100.0, 300.0, 50.0]}], "max_evaluations": 3,
                "targets": ["spot_radius"], "stop_on": ["FAIL"]}
        summary = sp.run_sweep(spec, self.dir / "built", build=lambda p: expander(f3=p["f3"]))
        self.assertEqual([r["outcome"] for r in summary["rows"]], ["PASS", "FAIL", "ERROR"])
        self.assertEqual((summary["rows"][2]["error_code"], summary["stopped_by"]), ("STOPPED", "s0002"))
        self.assertEqual(sp.substitute({"$value": {"scale": 2, "offset": 10}}, 3.0, "mm"),
                         {"value": 16.0, "unit": "mm"})
        self.assertEqual(sp.substitute(["$value", 0], 1.5, None), [1.5, 0])

    def test_unknown_target_in_selection_is_refused(self):
        spec = {"parameters": [{"name": "f3", "values": [100.0]}], "max_evaluations": 1, "targets": ["nope"]}
        with self.assertRaises(wc.WorkflowError) as raised:
            sp.run_sweep(spec, self.dir / "sel", build=lambda p: expander(f3=p["f3"]))
        self.assertEqual(raised.exception.code, "TARGET_UNKNOWN")


if __name__ == "__main__":
    unittest.main()
