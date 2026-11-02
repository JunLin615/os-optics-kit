"""Offline regression checks against the published os-optics results API.

Run from the repository root: python -m unittest discover -s tests -v
"""

from contextlib import redirect_stderr, redirect_stdout
import csv
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from os_optics.contracts import (
    PowerBudget, TargetResult, ValidationIssue, ValidationResult, ValidationSummary,
)
from os_optics.results import ResultsError, RunResults, results_from_engine

from examples.result_summary import emit_cli, summarize_results


ROOT = Path(__file__).resolve().parents[1]
IDEAL = ROOT / "examples" / "ideal_lens_focus.py"


def engine_result(status="PASS", **kwargs):
    return ValidationResult(status=status, summary=ValidationSummary(), **kwargs)


def invoke(workflow):
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = emit_cli(workflow)
    return code, stdout.getvalue(), stderr.getvalue()


class ResultSummaryTests(unittest.TestCase):
    def test_failed_and_cancelled_execution_override_unassessed_requirements(self):
        from examples import workflow_common as wc
        results = results_from_engine(engine_result())
        for state in ("FAILED", "CANCELLED"):
            with self.subTest(state=state):
                native = results.targets()
                native["outcome"] = {"execution": {"outcome": state}, "requirements": {}}
                with patch.object(RunResults, "targets", return_value=native):
                    summary = summarize_results(results)
                self.assertEqual(summary["requirement_outcome"], "ERROR")
                self.assertEqual(wc.sample_from_summary(summary)["outcome"], "ERROR")
                code, stdout, stderr = invoke(lambda: summary)
                self.assertEqual((code, stderr), (4, ""))
                self.assertEqual(json.loads(stdout)["outcome"]["execution"]["outcome"], state)

    def test_all_scientific_statuses_produce_json_and_matching_exit_codes(self):
        for status, expected in (
            ("PASS", 0), ("WARNING", 0), ("NOT_APPLICABLE", 0),
            ("FAIL", 1), ("UNKNOWN", 2), ("OUT_OF_MODEL_SCOPE", 3),
        ):
            with self.subTest(status=status):
                results = results_from_engine(engine_result(status))
                code, stdout, stderr = invoke(lambda: summarize_results(results))
                summary = json.loads(stdout)
                self.assertEqual((code, stderr), (0, ""))  # no requirements in these synthetic records
                self.assertEqual(summary["requirement_outcome"], "NOT_APPLICABLE")
                self.assertEqual(summary["status"], status)
                self.assertEqual(summary["engine_status"], status)
                self.assertIsNone(summary["validation_status"])

    def test_missing_power_and_empty_targets_are_explicit(self):
        summary = summarize_results(results_from_engine(engine_result()))
        self.assertEqual(summary["targets"], [])
        self.assertEqual(summary["power_budget"], {"available": False})
        self.assertNotIn("power_budget_details", summary)
        self.assertEqual(summary["integrity"]["checked"], False)

    def test_targets_preserve_units_bounds_zero_values_and_diagnostics(self):
        targets = (
            TargetResult(target_id="upper", metric="beam_radius_mm", value=0,
                         unit="mm", maximum=0, observation_id="focus", status="PASS"),
            TargetResult(target_id="lower", metric="power_W", value=1,
                         unit="W", minimum=0, device_id="detector", status="PASS"),
            TargetResult(target_id="interval", metric="power_W", value=0.5,
                         unit="W", minimum=0, maximum=1, status="PASS"),
            TargetResult(target_id="exact", metric="power_W", value=None,
                         unit="W", target_value=0, tolerance=0, status="UNKNOWN",
                         message="Supply the missing observation", issue_codes=("MONITOR_MISSED",)),
        )
        summary = summarize_results(results_from_engine(engine_result("UNKNOWN", targets=targets)))
        rows = {row["id"]: row for row in summary["targets"]}
        self.assertEqual(rows["upper"]["bounds"], {"maximum": 0})
        self.assertEqual(rows["upper"]["value"], 0)
        self.assertEqual(rows["upper"]["unit"], "mm")
        self.assertEqual(rows["upper"]["observation_id"], "focus")
        self.assertEqual(rows["lower"]["bounds"], {"minimum": 0})
        self.assertEqual(rows["lower"]["device_id"], "detector")
        self.assertEqual(rows["interval"]["bounds"], {"minimum": 0, "maximum": 1})
        self.assertEqual(rows["exact"]["bounds"], {"target_value": 0, "tolerance": 0})
        self.assertIsNone(rows["exact"]["value"])
        self.assertEqual(rows["exact"]["status"], "UNKNOWN")
        self.assertEqual(rows["exact"]["message"], "Supply the missing observation")
        self.assertEqual(rows["exact"]["issue_codes"], ["MONITOR_MISSED"])
        json.dumps(summary, allow_nan=False)

    def test_actionable_issue_context_is_not_truncated(self):
        issue = ValidationIssue(
            code="MONITOR_MISSED", status="WARNING", message="Beam missed the detector",
            location="observation:focus", affected_targets=("spot_radius",),
            numerical_context={"offset_mm": 5.0}, suggested_action="Move the detector",
        )
        summary = summarize_results(results_from_engine(engine_result("WARNING", issues=(issue,))))
        self.assertEqual(summary["issues"], [issue.model_dump(mode="json")])

    def test_full_power_ledger_is_only_in_explicit_details(self):
        budget = PowerBudget(
            total_input_W=1, total_sinks={"ESCAPED": 1},
            entries=({"kind": "ESCAPED", "power_W": 1, "source_id": "L",
                      "spectral_index": 0, "wavelength_nm": 532, "detail": "End of path"},),
        )
        results = results_from_engine(engine_result(power_budget=budget))
        compact = summarize_results(results)
        detail = summarize_results(results, details=True)
        self.assertEqual(detail["power_budget_details"], results.power_budget())
        self.assertTrue(detail["power_budget_details"]["entries"])
        self.assertTrue(detail["power_budget_details"]["rows"])
        self.assertEqual(compact["power_budget"]["total_sinks"], {"ESCAPED": 1})
        self.assertEqual(compact["power_budget"]["total_input_W"], 1)
        for field in ("entries", "rows", "lines", "by_element"):
            self.assertNotIn(field, compact["power_budget"])
        detail.pop("power_budget_details")
        self.assertEqual(compact, detail)

    def test_local_validation_scope_and_element_models_survive(self):
        scope = [{"instance_id": "Lens", "code": "CATALOG_SCOPE", "message": "Select a supported model"}]
        models = [{"instance_id": "Lens", "fidelity": "IDEAL_THIN_ELEMENT",
                   "approximations": ["SUBSTRATE_NOT_MODELLED"]}]
        results = RunResults(engine_result(), manifest={
            "status": {"engine_status": "PASS", "validation_status": "OUT_OF_MODEL_SCOPE",
                       "model_scope_issues": scope}, "element_models": models,
        })
        code, stdout, stderr = invoke(lambda: summarize_results(results))
        summary = json.loads(stdout)
        self.assertEqual((code, stderr), (0, ""))  # unrelated scope, no target requirements
        self.assertEqual(summary["status"], "OUT_OF_MODEL_SCOPE")
        self.assertEqual(summary["engine_status"], "PASS")
        self.assertEqual(summary["validation_status"], "OUT_OF_MODEL_SCOPE")
        self.assertEqual(summary["model_scope_issues"], scope)
        self.assertEqual(summary["element_models"], models)

    def test_null_validation_status_falls_back_to_engine(self):
        results = RunResults(engine_result(), manifest={"status": {"validation_status": None}})
        summary = summarize_results(results)
        self.assertEqual(summary["status"], "PASS")
        self.assertIsNone(summary["validation_status"])

    def test_integrity_failure_never_emits_a_scientific_summary(self):
        results = RunResults(engine_result(), integrity_problems=("simulation_result hash mismatch",))
        with self.assertRaises(ResultsError):
            summarize_results(results)
        code, stdout, stderr = invoke(lambda: summarize_results(results))
        self.assertEqual((code, stdout), (4, ""))
        self.assertEqual(json.loads(stderr)["error"]["code"], "RUN_INTEGRITY_FAILED")

    def test_unknown_future_status_fails_closed(self):
        results = RunResults(engine_result(), manifest={"status": {"validation_status": "FUTURE_STATUS"}})
        code, stdout, stderr = invoke(lambda: summarize_results(results))
        self.assertEqual((code, stdout), (4, ""))
        self.assertIn("FUTURE_STATUS", json.loads(stderr)["error"]["message"])

    def test_in_memory_summary_never_calls_the_simulator(self):
        results = results_from_engine(engine_result())
        with patch("os_optics.validate", side_effect=AssertionError("simulation called")), \
             patch("os_optics.validate_with_ray_paths", side_effect=AssertionError("simulation called")), \
             patch("os_optics.local.run_optical_project", side_effect=AssertionError("simulation called")):
            summary = summarize_results(results, artifacts={"simulation_result": Path("result.json")})
        self.assertEqual(summary["artifacts"], {"simulation_result": "result.json"})

    def test_workflow_logging_is_separate_from_summary(self):
        def workflow():
            print("Simulation diagnostic")
            return {"status": "PASS", "requirement_outcome": "PASS", "message": "光束"}

        code, stdout, stderr = invoke(workflow)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["message"], "光束")
        self.assertEqual(stderr, "Simulation diagnostic\n")

    def test_runtime_and_nonfinite_serialization_errors_leave_stdout_empty(self):
        def broken_workflow():
            raise ValueError("Invalid project")

        for workflow in (broken_workflow, lambda: {"status": "PASS", "value": float("nan")}):
            with self.subTest(workflow=workflow):
                code, stdout, stderr = invoke(workflow)
                self.assertEqual((code, stdout), (4, ""))
                self.assertIn("message", json.loads(stderr)["error"])


class IdealLensCliTests(unittest.TestCase):
    def run_example(self, *args):
        return subprocess.run([sys.executable, str(IDEAL), *map(str, args)],
                              cwd=ROOT, capture_output=True, text=True, timeout=60)

    def test_passing_and_failing_runs_preserve_complete_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            for name, bound, expected_status, expected_exit in (
                ("passing", "0.05", "PASS", 0), ("failing", "0.001", "FAIL", 1),
            ):
                with self.subTest(name=name):
                    output = Path(directory) / name
                    completed = self.run_example("--out", output, "--maximum-radius-mm", bound)
                    self.assertEqual(completed.returncode, expected_exit, completed.stderr)
                    summary = json.loads(completed.stdout)
                    self.assertEqual(summary["status"], expected_status)
                    target = summary["targets"][0]
                    self.assertEqual(target["unit"], "mm")
                    self.assertEqual(target["bounds"], {"maximum": float(bound)})
                    self.assertEqual(target["status"], expected_status)
                    self.assertNotIn("power_budget_details", summary)
                    self.assertNotIn("view_command", summary)
                    self.assertFalse((output / "manifest.json").exists())
                    files = {role: json.loads(Path(summary["artifacts"][role]).read_text(encoding="utf-8"))
                             for role in ("engine_request", "simulation_result", "ray_data")}
                    self.assertEqual(files["simulation_result"]["status"], expected_status)
                    self.assertTrue(files["simulation_result"]["paths"])
                    self.assertTrue(files["simulation_result"]["power_budget"]["entries"])
                    self.assertTrue(files["ray_data"])
                    self.assertEqual(files["engine_request"]["project"]["targets"][0]["maximum"], float(bound))
                    with Path(summary["exports"]["arrivals_csv"]).open(encoding="utf-8", newline="") as stream:
                        self.assertTrue(list(csv.DictReader(stream)))

    def test_details_includes_complete_power_entries_and_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            completed = self.run_example("--out", directory, "--details")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            summary = json.loads(completed.stdout)
            self.assertTrue(summary["power_budget_details"]["entries"])
            self.assertTrue(summary["power_budget_details"]["rows"])

    def test_invalid_input_and_usage_produce_json_errors_without_artifacts(self):
        for args in (("--maximum-radius-mm", "-1"), ("--maximum-radius-mm", "nan"),
                     ("--maximum-radius-mm", "inf"), ("--maximum-radius-mm", "invalid"),
                     ("--unknown-option",)):
            with self.subTest(args=args), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "must-not-exist"
                completed = self.run_example("--out", output, *args)
                self.assertEqual((completed.returncode, completed.stdout), (4, ""))
                self.assertTrue(json.loads(completed.stderr)["error"]["message"])
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
