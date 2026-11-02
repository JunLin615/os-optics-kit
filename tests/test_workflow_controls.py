"""Exercise public edit/check/result controls on synthetic, catalog-free optics."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

from examples import edit_project as ep, sweep_parameters as sweep, workflow_common as wc
from tests.workflow_fixtures import TempDir, expander


class EngineControlsTests(unittest.TestCase):
    def test_edit_record_cannot_replace_input_or_output(self):
        with TempDir() as directory:
            base = wc.write_json(directory / "base.json", expander())
            out = directory / "out.json"
            before = base.read_bytes()
            ops = [{"op": "set_compute_budget", "compute_budget": {"max_trace_steps": 2}}]
            for changes in (base, out):
                with self.assertRaises(ep.EditError):
                    ep.edit_file(base, ops, out, changes_path=changes)
                self.assertEqual(base.read_bytes(), before)
                self.assertFalse(out.exists())

    def test_overflow_and_bounded_objectives_stay_visible_without_ranking(self):
        row = {"sample_id": "s1", "outcome": "PASS", "target:power": 1.0,
               "target:power:exact": False}
        sweep.add_derived(row, [{"name": "overflow", "op": "mul", "a": 1e308, "b": 1e308}])
        self.assertIsNone(row["derived:overflow"])
        for column in ("derived:overflow", "target:power"):
            best, count = sweep.select_best([row], [], {"column": column, "sense": "maximize"})
            self.assertEqual((best, count), (None, 0))
            self.assertFalse(row["objective_valid"])
        self.assertEqual(row["target:power"], 1.0)

    def test_effective_assessment_controls_constraints(self):
        row = {"outcome": "UNKNOWN", "target:p:status": "PASS",
               "target:p:assessment": "INDETERMINATE"}
        self.assertTrue(sweep.constraint_failures(row, [{"target": "p", "assessment": ["MET"]}]))

    def cli(self, *args):
        result = subprocess.run([sys.executable, "-m", "os_optics", *map(str, args)],
                                capture_output=True, text=True, timeout=60)
        return result.returncode, json.loads(result.stdout)

    def test_atomic_refusal_hash_guard_and_interrupted_response_retry(self):
        with TempDir() as directory:
            project = wc.write_json(directory / "project.json", expander())
            original = project.read_bytes()
            code, checked = self.cli("project", "check", project)
            self.assertEqual(code, 0)
            invalid = wc.write_json(directory / "invalid.json", [
                {"op": "transform", "selector": {"ids": ["L2"]}, "translation_mm": [1, 0, 0]},
                {"op": "set_source_model", "instance_id": "SRC", "changes": {"waist_radius_mm": -1}},
            ])
            code, result = self.cli("project", "update", project, "--ops", invalid, "-o", project)
            self.assertEqual((code, result["error"]["code"]), (65, "PROJECT_REJECTED"))
            self.assertEqual(project.read_bytes(), original)
            ops = wc.write_json(directory / "move.json", [
                {"op": "transform", "selector": {"ids": ["L2"]}, "translation_mm": [1, 0, 0]},
            ])
            args = ("project", "update", project, "--ops", ops, "-o", project,
                    "--expect-hash", checked["project_hash"])
            code, changed = self.cli(*args)
            self.assertEqual(code, 0)
            once = project.read_bytes()
            # Simulate losing the first response and retrying the identical request.
            code, retry = self.cli(*args)
            self.assertEqual((code, retry["error"]["code"]), (65, "PROJECT_CHANGED"))
            self.assertEqual(retry["error"]["project_hash"], changed["project_hash"])
            self.assertEqual(project.read_bytes(), once)

    def test_physical_target_and_budget_freshness(self):
        with TempDir() as directory:
            base = expander()
            record = wc.run_sample(base, directory / "original")
            run_dir = record["run_dir"]
            self.assertEqual(wc.freshness(base, run_dir)["freshness"], "CURRENT")
            variants = [
                ({"op": "set_source_model", "instance_id": "SRC", "changes": {"power_w": 0.002}}, "PHYSICS_CHANGED"),
                ({"op": "set_target", "target_id": "spot_radius", "changes": {"maximum": 0.03}}, "TARGETS_CHANGED"),
                ({"op": "set_compute_budget", "compute_budget": {"max_trace_steps": 1}}, "PHYSICS_CHANGED"),
            ]
            for op, reason in variants:
                updated = ep.apply_edits(base, [op])["project"]
                fresh = wc.freshness(updated, run_dir)
                self.assertEqual(fresh["freshness"], "HISTORICAL")
                self.assertIn(reason, fresh["reasons"])
            # Reading freshness never changes a saved run's identity.
            self.assertEqual(wc.read_saved_run(run_dir)[1]["identity"], record["identity"])

    def test_target_free_and_budget_partial_are_distinct(self):
        with TempDir() as directory:
            free = expander()
            free["targets"] = []
            record = wc.run_sample(free, directory / "free")
            self.assertEqual(record["outcome"], "NOT_APPLICABLE")
            self.assertEqual(record["execution"]["outcome"], "COMPLETED")
            self.assertFalse(record["requirements"]["declared"])
            limited = ep.apply_edits(expander(), [{"op": "set_compute_budget",
                "compute_budget": {"max_trace_steps": 1}}])["project"]
            record = wc.run_sample(limited, directory / "limited")
            self.assertEqual(record["execution"]["outcome"], "PARTIAL")
            self.assertIn("max_trace_steps", record["execution"]["budgets"]["exhausted"])
            self.assertTrue(record["requirements"]["indeterminate"])
            self.assertTrue(Path(record["run_dir"]).is_dir())
            limited["targets"] = []
            record = wc.run_sample(limited, directory / "limited_free")
            self.assertEqual(record["execution"]["outcome"], "PARTIAL")
            self.assertEqual((record["status"], record["outcome"]), ("UNKNOWN", "NOT_APPLICABLE"))
            from examples import result_summary
            from tests.workflow_fixtures import invoke
            code, summary, error = invoke(result_summary.main, [record["run_dir"]])
            self.assertEqual((code, error), (0, ""))
            self.assertEqual(summary["outcome"]["execution"]["outcome"], "PARTIAL")
            self.assertEqual(summary["requirement_outcome"], "NOT_APPLICABLE")

    def test_objective_validity_and_requirement_feasibility_are_separate(self):
        rows = [
            {"sample_id": "s1", "outcome": "UNKNOWN", "target:radius": 1.0, "target:radius:exact": True},
            {"sample_id": "s2", "outcome": "PASS", "target:radius": 0.5, "target:radius:exact": False},
            {"sample_id": "s3", "outcome": "ERROR", "target:radius": None, "target:radius:exact": False},
        ]
        objective = {"column": "target:radius", "sense": "minimize"}
        best, feasible = sweep.select_best(rows, [], objective)
        self.assertEqual((best["sample_id"], feasible), ("s1", 1))
        self.assertFalse(rows[1]["objective_valid"])
        # Requiring all selected requirements to be met produces no feasible result.
        best, feasible = sweep.select_best(rows, [{"outcome": ["PASS"]}], objective)
        self.assertEqual((best, feasible), (None, 0))
        self.assertEqual(len(rows), 3)


if __name__ == "__main__":
    unittest.main()
