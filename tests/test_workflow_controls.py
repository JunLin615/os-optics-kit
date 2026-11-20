"""Exercise public edit/check/result controls on synthetic, catalog-free optics."""

import json
from pathlib import Path
import subprocess
import sys
import unittest

from os_optics_kit import edit_project as ep, sweep_parameters as sweep, workflow_common as wc
from tests.workflow_fixtures import TempDir, expander, requires_engine


class EngineControlsTests(unittest.TestCase):
    @requires_engine
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
        # value_use "reported" ranks the bounded value, and the failure text of the default names the kind.
        bounded = {"sample_id": "s1", "outcome": "PASS", "target:power": 1.0, "target:power:exact": False,
                   "target:power:value_kind": "BOUNDED"}
        best, count = sweep.select_best([bounded], [], {"column": "target:power", "sense": "maximize"})
        self.assertEqual((best, count, bounded["constraint_failures"]),
                         (None, 0, ["target:power has no usable value (BOUNDED)"]))
        best, count = sweep.select_best([bounded], [], {"column": "target:power", "sense": "maximize"}, "reported")
        self.assertEqual((best["sample_id"], count), ("s1", 1))
        self.assertEqual(wc.values_used([bounded], ["target:power"], "reported"), {"target:power": {"BOUNDED": 1}})

    def test_value_kinds(self):
        def use(**fields):
            return wc.target_value_use({"value": 1.0, "status": "PASS", "assessment": "MET", **fields})["kind"]

        self.assertEqual(use(), "EXACT")
        self.assertEqual(use(status="FAIL", assessment="UNMET"), "EXACT")
        self.assertEqual(use(value=None), "NO_VALUE")
        self.assertEqual(use(value=float("inf")), "NO_VALUE")
        self.assertEqual(use(applicability_issue_codes=["MODEL_WAVELENGTH_OUTSIDE_BAND"]), "OUTSIDE_MODEL")
        self.assertEqual(use(status="OUT_OF_MODEL_SCOPE", assessment="INDETERMINATE"), "OUTSIDE_MODEL")
        self.assertEqual(use(quantity_state="BOUNDED"), "BOUNDED")
        self.assertEqual(use(quantity_state="UNKNOWN"), "QUANTITY_UNKNOWN")
        self.assertEqual(use(status="UNKNOWN", assessment="INDETERMINATE"), "UNDECIDED")
        for kind in wc.VALUE_KINDS:
            self.assertEqual(wc.value_usable(kind), kind == "EXACT")
            self.assertEqual(wc.value_usable(kind, "reported"), kind != "NO_VALUE")
        with self.assertRaises(wc.WorkflowError):
            wc.value_use_setting("all")

    def test_effective_assessment_controls_constraints(self):
        row = {"outcome": "UNKNOWN", "target:p:status": "PASS",
               "target:p:assessment": "INDETERMINATE"}
        self.assertTrue(sweep.constraint_failures(row, [{"target": "p", "assessment": ["MET"]}]))

    def cli(self, *args):
        result = subprocess.run([sys.executable, "-m", "os_optics", *map(str, args)],
                                capture_output=True, text=True, timeout=60)
        return result.returncode, json.loads(result.stdout)

    @requires_engine
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

    @requires_engine
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

    @requires_engine
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
            self.assertFalse(record["requirements"]["declared"])
            # Without targets a PARTIAL run is UNKNOWN, not NOT_APPLICABLE, and exits 3 (the calculation did not
            # complete), as under the Engine's requirements exit contract; the execution is stated beside it.
            self.assertEqual((record["status"], record["outcome"]), ("UNKNOWN", "UNKNOWN"))
            from os_optics_kit import result_summary
            from tests.workflow_fixtures import invoke
            code, summary, error = invoke(result_summary.main, [record["run_dir"]])
            self.assertEqual((code, error), (3, ""))
            self.assertEqual(summary["outcome"]["execution"]["outcome"], "PARTIAL")
            self.assertEqual(summary["requirement_outcome"], "UNKNOWN")

    def test_partial_execution_is_never_a_pass_and_exits_3(self):
        """A PARTIAL run did not complete: met targets or none leave it UNKNOWN (not PASS / NOT_APPLICABLE), an
        unmet or out-of-model target keeps FAIL / OUT_OF_MODEL, and every PARTIAL run exits 3, the Engine's
        ``requirements`` code for an incomplete calculation."""
        met = [{"id": "t", "assessment": "MET", "status": "PASS"}]
        unmet = [{"id": "t", "assessment": "UNMET", "status": "FAIL"}]
        out = [{"id": "t", "assessment": "INDETERMINATE", "status": "OUT_OF_MODEL_SCOPE"}]
        for execution, targets, expected, code in (
            ({"outcome": "COMPLETED"}, met, "PASS", 0),
            ({"outcome": "COMPLETED"}, [], "NOT_APPLICABLE", 0),
            ({"outcome": "COMPLETED"}, unmet, "FAIL", 1),
            ({"outcome": "PARTIAL"}, met, "UNKNOWN", 3),
            ({"outcome": "PARTIAL"}, [], "UNKNOWN", 3),
            ({"outcome": "PARTIAL"}, [{"id": "t", "assessment": "INDETERMINATE", "status": "UNKNOWN"}], "UNKNOWN", 3),
            ({"outcome": "PARTIAL"}, unmet, "FAIL", 3),
            ({"outcome": "PARTIAL"}, out, "OUT_OF_MODEL", 3),
            ({"outcome": "FAILED"}, met, "ERROR", 4),
            ({"outcome": "CANCELLED"}, met, "ERROR", 4),
            (None, met, "PASS", 0),
        ):
            with self.subTest(execution=execution, targets=targets):
                self.assertEqual(wc.requirement_outcome("UNKNOWN", execution, [], targets), expected)
                self.assertEqual(wc.requirement_exit_code(expected, execution, wc.WORKFLOW_EXIT_CODES), code)

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
