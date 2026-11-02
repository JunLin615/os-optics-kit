"""Offline checks that the workflow helpers surface every diagnostic and refuse spec mistakes before running.

Covers: report diagnostics of saved runs, complete input comparison, sweep column validation, study-target
classification in comparisons, candidate exclusion counts, study overwrite, op argument checks, unit-equivalent edit
retries, valueless plot markers, impossible truncation, derived-column units, warning flags in compact output and
distinct out-of-model issue entries.
Catalog-free; run from the repository root: python -m unittest discover -s tests -v
"""

import copy
from importlib.util import find_spec
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from tests.workflow_fixtures import TempDir, by_id, expander, invoke, quiet_environment

quiet_environment()

from examples import build_report as br  # noqa: E402
from examples import catalog_candidates as cc  # noqa: E402
from examples import compare_runs as cr  # noqa: E402
from examples import edit_project as ep  # noqa: E402
from examples import optical_designs as designs  # noqa: E402
from examples import sweep_parameters as sp  # noqa: E402
from examples import tolerance_study as ts  # noqa: E402
from examples import workflow_common as wc  # noqa: E402

HAS_MATPLOTLIB = find_spec("matplotlib") is not None
SHIFT_FOCUS = [{"op": "transform", "selector": {"ids": ["focus"]}, "frame": "design",
                "translation_mm": ["$value", 0, 0]}]


def _diagnostic_lines(report_dir):
    return [line for line in (Path(report_dir) / "report.md").read_text(encoding="utf-8").splitlines()
            if line.startswith("- ")]


class RunReportDiagnosticsTests(unittest.TestCase):
    """build_report on run directories lists the runs' warnings, target issue codes and model-scope issues."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = TempDir()
        cls.dir = cls._tmp.__enter__()
        cls.warn = wc.run_sample(designs.expander_focus(waist_radius_mm=3.0), cls.dir / "warn")
        cls.failing = wc.run_sample(designs.expander_focus(gap_error_mm=5.0), cls.dir / "fail")
        cls.oom = wc.run_sample(designs.expander_focus(wavelength_nm=1300.0), cls.dir / "oom")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.__exit__(None, None, None)

    def test_preconditions(self):
        self.assertIn("GAUSSIAN_TRUNCATION", self.warn["warnings"])
        self.assertEqual(self.failing["outcome"], "FAIL")
        self.assertTrue(any(t.get("issue_codes") for t in self.failing["targets"]))
        self.assertEqual(self.oom["outcome"], "OUT_OF_MODEL")

    def test_runs_mode_carries_warnings_issue_codes_and_scope(self):
        report = br.build_report([self.dir / name / "run" for name in ("warn", "fail", "oom")],
                                 out_dir=self.dir / "report")
        diagnostics = report["diagnostics"]
        self.assertEqual(diagnostics["warnings"].get("GAUSSIAN_TRUNCATION"), 1)
        expected_codes = {}
        for record in (self.warn, self.failing, self.oom):
            for target in record["targets"]:
                for code in target.get("issue_codes") or []:
                    expected_codes[code] = expected_codes.get(code, 0) + 1
        self.assertTrue(expected_codes)
        self.assertEqual(diagnostics["target_issue_codes"], expected_codes)
        scope = {(i.get("code") if isinstance(i, dict) else i) for i in self.oom["model_scope_issues"]}
        self.assertEqual(set(diagnostics["model_scope_issues"]), scope)
        lines = _diagnostic_lines(self.dir / "report")
        self.assertNotIn("- Warnings: none", lines)
        self.assertNotIn("- Target issue codes: none", lines)
        self.assertIn("GAUSSIAN_TRUNCATION", (self.dir / "report" / "report.md").read_text(encoding="utf-8"))

    def test_compare_runs_keep_warnings_per_run(self):
        document = cr.compare([self.dir / "warn" / "run", self.dir / "fail" / "run"])
        runs = {r["label"]: r for r in document["runs"]}
        self.assertEqual(runs["warn"]["warnings"], self.warn["warnings"])
        self.assertTrue(runs["fail"]["target_issue_codes"])

    def test_workflow_report_names_the_nominal_warning(self):
        from examples import beam_expansion_focus as bef

        code, out, err = invoke(bef.main, ["--out", str(self.dir / "wf"), "--waist-radius-mm", "3", "--no-studies"])
        self.assertIn(code, (0, 1, 2, 3), err)
        self.assertIn("GAUSSIAN_TRUNCATION", out["nominal"]["warnings"])
        text = (self.dir / "wf" / "report" / "report.md").read_text(encoding="utf-8")
        self.assertIn("GAUSSIAN_TRUNCATION", text)
        self.assertNotIn("- Warnings: none", text.splitlines())


class CompleteInputComparisonTests(unittest.TestCase):
    """compare_runs lists every differing input of the run Projects and states when the Projects are identical."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = TempDir()
        cls.dir = cls._tmp.__enter__()
        base = designs.alignment(mirror_tilt_local_y_mrad=60.0)
        wide = ep.apply_edits(base, [{"op": "set_observation", "observation_id": "far", "changes": {
            "aperture": {"shape": "circle", "radius_mm": 60.0, "center_offset_mm": [0.0, 0.0],
                         "obstruction_radius_mm": 0.0}}}])["project"]
        cls.narrow = wc.run_sample(base, cls.dir / "narrow")
        cls.wide = wc.run_sample(wide, cls.dir / "wide")
        cls.again = wc.run_sample(base, cls.dir / "again")
        elliptic = ep.apply_edits(designs.expander_focus(), [
            {"op": "set_source_model", "instance_id": "SRC", "changes": {"waist_radius_y_mm": 0.8}}])["project"]
        wc.run_sample(designs.expander_focus(), cls.dir / "round")
        wc.run_sample(elliptic, cls.dir / "elliptic")
        wc.run_sample(designs.expander_focus(max_spot_radius_mm=0.03), cls.dir / "loose")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.__exit__(None, None, None)

    def test_observation_aperture_difference_is_listed_with_unit(self):
        self.assertNotEqual(self.narrow["identity"]["project_hash"], self.wide["identity"]["project_hash"])
        document = cr.compare([self.dir / "narrow" / "run", self.dir / "wide" / "run"])
        inputs = {d["input"]: d for d in document["input_differences"]}
        self.assertIn("observation:far.aperture.radius_mm", inputs)
        self.assertEqual(inputs["observation:far.aperture.radius_mm"]["unit"], "mm")
        self.assertEqual(inputs["observation:far.aperture.radius_mm"]["values"], {"narrow": 12.5, "wide": 60.0})
        self.assertFalse(document["inputs_compared"]["project_hashes_equal"])
        br.build_report([self.dir / "narrow" / "run", self.dir / "wide" / "run"], out_dir=self.dir / "rep-a")
        text = (self.dir / "rep-a" / "report.md").read_text(encoding="utf-8")
        self.assertNotIn("No input differences", text)
        self.assertIn("observation:far.aperture.radius_mm", text)

    def test_elliptical_source_field_is_listed(self):
        document = cr.compare([self.dir / "round" / "run", self.dir / "elliptic" / "run"])
        inputs = {d["input"]: d for d in document["input_differences"]}
        self.assertEqual(inputs["source:SRC.waist_radius_y_mm"]["values"], {"round": "<missing>", "elliptic": 0.8})
        self.assertEqual(inputs["source:SRC.waist_radius_y_mm"]["unit"], "mm")

    def test_target_bounds_carry_the_target_unit(self):
        document = cr.compare([self.dir / "round" / "run", self.dir / "loose" / "run"])
        inputs = {d["input"]: d for d in document["input_differences"]}
        self.assertEqual(inputs["target:spot_radius.maximum"]["values"], {"round": 0.02, "loose": 0.03})
        self.assertEqual(inputs["target:spot_radius.maximum"]["unit"], "mm")

    def test_identical_projects_are_stated_with_equal_hashes(self):
        document = cr.compare([self.dir / "narrow" / "run", self.dir / "again" / "run"])
        self.assertEqual(document["input_differences"], [])
        self.assertTrue(document["inputs_compared"]["project_hashes_equal"])
        br.build_report([self.dir / "narrow" / "run", self.dir / "again" / "run"], out_dir=self.dir / "rep-b")
        text = (self.dir / "rep-b" / "report.md").read_text(encoding="utf-8")
        self.assertIn("identical", text)

    def test_hash_difference_without_field_difference_is_stated(self):
        # e.g. runs of one Project by Engine builds that hash it differently: equal documents, different hashes
        load = cr.load_entry

        def other_hash(label, run_dir, record, selected=None):
            entry = load(label, run_dir, record, selected)
            if label == "again":
                entry["identity"] = {**entry["identity"], "project_hash": "0" * 64}
            return entry

        paths = [self.dir / "narrow" / "run", self.dir / "again" / "run"]
        with patch.object(cr, "load_entry", other_hash):
            document = cr.compare(paths)
            self.assertEqual(document["input_differences"], [])
            self.assertFalse(document["inputs_compared"]["project_hashes_equal"])
            br.build_report(paths, out_dir=self.dir / "rep-c")
        text = (self.dir / "rep-c" / "report.md").read_text(encoding="utf-8")
        self.assertNotIn("No input differences", text)
        self.assertIn("project hashes differ", text)


class SweepColumnValidationTests(unittest.TestCase):
    """A typo in an objective, constraint or derived column is a refused spec (exit 4), not NO_FEASIBLE."""

    PARAMETER = {"name": "gap", "unit": "mm", "values": [-1, 0, 1],
                 "ops": [{"op": "transform", "selector": {"ids": ["L2"]}, "frame": "design",
                          "translation_mm": ["$value", 0, 0]}]}

    def refused(self, extra, code):
        with TempDir() as tmp:
            with self.assertRaises(wc.WorkflowError) as caught:
                sp.run_sweep({"parameters": [self.PARAMETER], "max_evaluations": 5, **extra}, tmp / "study",
                             base=designs.expander_focus())
            self.assertFalse((tmp / "study").exists(), "nothing may run before the spec is accepted")
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_objective_column_typo(self):
        exc = self.refused({"objective": {"column": "target:spot_radus", "sense": "minimize"}}, "COLUMN_UNKNOWN")
        self.assertIn("target:spot_radius", exc.details["known"])
        self.assertIn("param:gap", exc.details["known"])

    def test_constraint_target_typo(self):
        exc = self.refused({"constraints": [{"target": "spot_radus", "assessment": ["MET"]}]}, "TARGET_UNKNOWN")
        self.assertIn("spot_radius", exc.details["known"])

    def test_constraint_column_typo(self):
        self.refused({"constraints": [{"column": "target:spot_radus", "max": 0.02}]}, "COLUMN_UNKNOWN")

    def test_derived_operand_typo_and_unknown_prefix(self):
        self.refused({"derived": [{"name": "d", "op": "abs", "a": "target:spot_radus"}]}, "COLUMN_UNKNOWN")
        self.refused({"objective": {"column": "seconds", "sense": "minimize"}}, "COLUMN_UNKNOWN")
        # a derived column may use an earlier derived column, not a later one
        self.refused({"derived": [{"name": "b", "op": "abs", "a": "derived:a"},
                                  {"name": "a", "op": "abs", "a": "target:spot_radius"}]}, "COLUMN_UNKNOWN")

    def test_cli_exit_4_and_valid_columns_run(self):
        with TempDir() as tmp:
            spec = {"parameters": [self.PARAMETER], "max_evaluations": 5,
                    "objective": {"column": "target:spot_radus", "sense": "minimize"}}
            wc.write_json(tmp / "spec.json", spec)
            wc.write_json(tmp / "base.json", designs.expander_focus())
            code, out, err = invoke(sp.main, [str(tmp / "spec.json"), "--base", str(tmp / "base.json"),
                                              "--out", str(tmp / "study")])
            self.assertEqual((code, out), (4, None))
            self.assertEqual(json.loads(err.strip().splitlines()[-1])["error"]["code"], "COLUMN_UNKNOWN")
            good = {**spec, "objective": {"column": "derived:spot_um", "sense": "minimize"},
                    "derived": [{"name": "spot_um", "op": "mul", "a": "target:spot_radius", "b": 1000, "unit": "um"}],
                    "constraints": [{"column": "derived:spot_um", "max": {"value": 0.05, "unit": "mm"}}]}
            summary = sp.run_sweep(good, tmp / "ok", base=designs.expander_focus())
            self.assertEqual(summary["status"], "COMPLETED")
            self.assertEqual(summary["best"]["objective"]["unit"], "um")
            self.assertEqual(summary["rows"][0]["derived:spot_um:unit"], "um")

    def test_derived_unit_must_be_known(self):
        self.refused({"derived": [{"name": "d", "op": "abs", "a": "target:spot_radius", "unit": "furlong"}]},
                     "UNIT_UNKNOWN")


class StudyComparisonTargetsTests(unittest.TestCase):
    """compare_runs on a study classifies samples with the study's selected targets and refuses unknown ids."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = TempDir()
        cls.dir = cls._tmp.__enter__()
        spec = {"parameters": [{"name": "focus_shift", "unit": "mm", "values": [0, 5], "ops": SHIFT_FOCUS}],
                "max_evaluations": 5, "targets": ["expanded_radius"]}
        cls.summary = sp.run_sweep(spec, cls.dir / "study", base=designs.expander_focus())

    @classmethod
    def tearDownClass(cls):
        cls._tmp.__exit__(None, None, None)

    def test_same_outcomes_as_the_study(self):
        study = [(r["sample_id"], r["outcome"]) for r in self.summary["rows"]]
        self.assertEqual(study, [("s0001", "PASS"), ("s0002", "PASS")])
        document = cr.compare([self.dir / "study"])
        self.assertEqual([(r["label"], r["outcome"]) for r in document["runs"]], study)
        self.assertEqual(document["selected_targets"], ["expanded_radius"])

    def test_explicit_targets_override_and_unknown_ids_are_refused(self):
        document = cr.compare([self.dir / "study"], targets=["spot_radius"])
        self.assertEqual([r["outcome"] for r in document["runs"]], ["PASS", "FAIL"])
        with self.assertRaises(wc.WorkflowError) as caught:
            cr.compare([self.dir / "study"], targets=["spot_radus"])
        self.assertEqual(caught.exception.code, "TARGET_UNKNOWN")
        code, out, _err = invoke(cr.main, [str(self.dir / "study"), "--targets", "spot_radus"])
        self.assertEqual((code, out), (4, None))


class CandidateExclusionTests(unittest.TestCase):
    """--near / --sort count the products without the field instead of dropping them silently."""

    PRODUCTS = [{"optical_sku": "A", "focal_length_mm": "100"}, {"optical_sku": "B"},
                {"optical_sku": "C", "focal_length_mm": "90"}, {"optical_sku": "D", "focal_length_mm": None}]

    def query(self, **options):
        reply = {"ok": True, "catalog_version": "test", "total": len(self.PRODUCTS),
                 "products": copy.deepcopy(self.PRODUCTS)}
        with patch("os_optics.local.json_api.catalog_products", return_value=reply):
            return cc.candidates(**options)

    def test_near_and_sort_count_missing_fields(self):
        near = self.query(near=("focal_length_mm", 95))
        self.assertEqual(near["matched"], 2)
        self.assertEqual(near["excluded_missing_field"], {"focal_length_mm": 2})
        self.assertEqual(near["engine_matches"], near["matched"] + 2)
        ordered = self.query(sort="focal_length_mm")
        self.assertEqual([r["optical_sku"] for r in ordered["rows"]], ["C", "A"])
        self.assertEqual(ordered["excluded_missing_field"], {"focal_length_mm": 2})
        plain = self.query()
        self.assertEqual((plain["matched"], plain["excluded_missing_field"]), (4, {}))


class StudyOverwriteTests(unittest.TestCase):
    def test_overwrite_removes_previous_plots_and_report(self):
        spec = {"parameters": [{"name": "shift", "unit": "mm", "values": [-1, 1], "ops": SHIFT_FOCUS}],
                "max_evaluations": 5}
        with TempDir() as tmp:
            sp.run_sweep(spec, tmp / "study", base=designs.expander_focus())
            (tmp / "study" / "plots").mkdir()
            (tmp / "study" / "plots" / "sweep_target_spot_radius.png").write_bytes(b"old")
            (tmp / "study" / "report").mkdir()
            (tmp / "study" / "report" / "report.md").write_text("old", encoding="utf-8")
            (tmp / "study" / "notes.txt").write_text("customer file", encoding="utf-8")
            sp.run_sweep(spec, tmp / "study", base=designs.expander_focus(), overwrite=True)
            self.assertFalse((tmp / "study" / "plots").exists())
            self.assertFalse((tmp / "study" / "report").exists())
            self.assertTrue((tmp / "study" / "notes.txt").is_file(), "files of other tools stay")


class OpArgumentTests(unittest.TestCase):
    """An absent argument is refused like the Designer does; an explicit null removes."""

    def apply(self, ops):
        return ep.apply_edits(expander(), ops, validate=False)["project"]

    def test_set_retained_dof_needs_control(self):
        with self.assertRaises(ep.EditError) as caught:
            self.apply([{"op": "set_retained_dof", "instance_id": "L2", "axis": "Tx"}])
        self.assertEqual(caught.exception.details["op_code"], "BAD_ARGUMENT")
        kept = self.apply([{"op": "set_retained_dof", "instance_id": "L2", "axis": "Tx", "control": "MANUAL"}])
        self.assertEqual(by_id(kept["components"], "instance_id", "L2")["retained_dofs"],
                         [{"axis": "Tx", "control": "MANUAL"}])
        released = self.apply([{"op": "set_retained_dof", "instance_id": "L2", "axis": "Tx", "control": "MANUAL"},
                               {"op": "set_retained_dof", "instance_id": "L2", "axis": "Tx", "control": None}])
        self.assertEqual(by_id(released["components"], "instance_id", "L2")["retained_dofs"], [])

    def test_set_design_intent_needs_the_key(self):
        with self.assertRaises(ep.EditError) as caught:
            self.apply([{"op": "set_design_intent", "design_intent": "OPTICAL_SYSTEM"}, {"op": "set_design_intent"}])
        self.assertEqual((caught.exception.details["op_code"], caught.exception.details["op_index"]),
                         ("BAD_ARGUMENT", 1))
        removed = self.apply([{"op": "set_design_intent", "design_intent": "OPTICAL_SYSTEM"},
                              {"op": "set_design_intent", "design_intent": None}])
        self.assertNotIn("design_intent", removed)

    def test_workspace_only_frames_are_refused_on_a_project(self):
        with self.assertRaises(ep.EditError) as caught:
            self.apply([{"op": "transform", "selector": {"ids": ["L2"]}, "frame": {"group": "optics"},
                         "translation_mm": [1, 0, 0]}])
        self.assertEqual(caught.exception.code, "PROJECT_OP_INVALID")
        self.assertEqual(caught.exception.details["op_code"], "TRANSFORM_REFUSED")
        self.assertEqual(caught.exception.details["problems"][0]["code"], "FRAME_INVALID")
        self.assertIn("FRAME_INVALID", ep.__doc__)


class UnitEquivalentRetryTests(unittest.TestCase):
    def test_same_edit_in_other_units_is_already_applied_and_other_edit_refused(self):
        with TempDir() as tmp:
            base = wc.write_json(tmp / "base.json", expander())
            edits = {
                "mm": [{"op": "transform", "selector": {"ids": ["L2"]}, "frame": {"component": "L2"},
                        "translation_mm": [0, 0, 1]}],
                "um": [{"op": "transform", "selector": {"ids": ["L2"]}, "frame": {"component": "L2"},
                        "translation_mm": [0, 0, {"value": 1000, "unit": "um"}]}],
                "float": [{"op": "transform", "selector": {"ids": ["L2"]}, "frame": {"component": "L2"},
                           "translation_mm": [0.0, 0.0, 1.0]}],
                "other": [{"op": "transform", "selector": {"ids": ["L2"]}, "frame": {"component": "L2"},
                           "translation_mm": [0, 0, {"value": 1001, "unit": "um"}]}],
            }
            paths = {name: wc.write_json(tmp / f"{name}.json", ops) for name, ops in edits.items()}

            def run(name):
                return invoke(ep.main, [str(base), "--edits", str(paths[name]), "--out", str(tmp / "new.json")])

            self.assertEqual(run("mm")[1]["status"], "EDITED")
            for name in ("um", "float"):
                code, out, err = run(name)
                self.assertEqual((code, out["status"], out["already_applied"]), (0, "ALREADY_APPLIED", True), err)
            code, out, err = run("other")
            self.assertEqual((code, out), (4, None))
            error = json.loads(err.strip().splitlines()[-1])["error"]
            self.assertEqual((error["code"], error["mismatch"]), ("OUTPUT_EXISTS", ["OPS_CHANGED"]))


@unittest.skipUnless(HAS_MATPLOTLIB, "matplotlib is needed for plots (requirements-examples.txt)")
class ValuelessPlotMarkerTests(unittest.TestCase):
    def test_valueless_samples_have_their_own_position_and_legend(self):
        from examples import plot_results as pr

        spec = {"parameters": [{"name": "l2_x", "unit": "mm", "values": [0.5, 200.0, 202.0], "ops": [
            {"op": "set_component_pose", "instance_id": "L2", "position_mm": ["$value", 0, 0]}]}],
            "max_evaluations": 5}
        with TempDir() as tmp:
            sp.run_sweep(spec, tmp / "study", base=expander())
            result = pr.plot_sweep(tmp / "study", "target:spot_radius")
            self.assertEqual(result["without_value"], 1)
            valued = [r["target:spot_radius"] for r in wc.read_json(tmp / "study" / "summary.json")["rows"]
                      if isinstance(r.get("target:spot_radius"), float)]
            self.assertLess(result["no_value_position"], min(valued))
            svg = Path(result["out"]["svg"]).read_text(encoding="utf-8")
            self.assertIn("no exact value (1)", svg)
            self.assertIn("samples without an exact value", svg)

    def test_heatmap_draws_target_bounds(self):
        from examples import plot_results as pr

        spec = {"parameters": [{"name": "shift", "unit": "mm", "values": [-1, 1], "ops": SHIFT_FOCUS},
                               {"name": "wl", "unit": "nm", "values": [780, 790], "ops": [
                                   {"op": "set_source_model", "instance_id": "SRC",
                                    "changes": {"wavelength_nm": "$value"}}]}], "max_evaluations": 5}
        with TempDir() as tmp:
            sp.run_sweep(spec, tmp / "study", base=designs.expander_focus())
            result = pr.plot_sweep(tmp / "study", "target:spot_radius")
            self.assertEqual(result["bounds"]["maximum"], 0.02)
            self.assertIn("target maximum 0.02", Path(result["out"]["svg"]).read_text(encoding="utf-8"))


class ImpossibleTruncationTests(unittest.TestCase):
    def test_bounds_far_in_one_tail_are_refused(self):
        spec = {"samples": 10, "seed": 1, "max_evaluations": 11, "parameters": [
            {"name": "p", "unit": "mm", "ops": [{"op": "transform", "selector": {"ids": ["L2"]},
                                                 "translation_mm": [0, 0, "$value"]}],
             "distribution": {"kind": "normal", "sigma": 1, "bounds": [9, 10]}}]}
        with self.assertRaises(wc.WorkflowError) as caught:
            ts.normalize_spec(spec)
        self.assertEqual(caught.exception.code, "SPEC_INVALID")
        self.assertIn("sigma", str(caught.exception))
        spec["parameters"][0]["distribution"]["bounds"] = [2, 4]  # far but representable: still sampled
        normalized = ts.normalize_spec(spec)
        drawn = [o["p"] for _sid, o in ts.draw_samples(normalized["parameters"], 200, 3)]
        self.assertTrue(all(2 <= v <= 4 for v in drawn))


class CompactWarningFlagTests(unittest.TestCase):
    def test_sweep_and_tolerance_flag_pass_samples_with_warnings(self):
        spec = {"parameters": [{"name": "w", "unit": "mm", "values": [0.5, 3.0]}], "max_evaluations": 5}
        with TempDir() as tmp:
            summary = sp.run_sweep(spec, tmp / "study", build=lambda p: designs.expander_focus(
                waist_radius_mm=p["w"], max_spot_radius_mm=1.0, expanded_radius_tolerance_rel=1.0,
                waist_window_mm=1e6))
            rows = {r["sample_id"]: r for r in summary["rows"]}
            self.assertEqual(rows["s0002"]["outcome"], "PASS")
            self.assertIn("GAUSSIAN_TRUNCATION", rows["s0002"]["warnings"])
            compact = sp.compact(summary)
            flagged = compact["samples_with_warnings"]
            self.assertEqual((flagged["samples"], flagged["pass_samples"]), (1, 1))
            self.assertEqual(flagged["by_code"], {"GAUSSIAN_TRUNCATION": 1})
            top = {r["sample_id"]: r for r in compact["top_rows"]}
            self.assertEqual(top["s0002"]["warnings"], ["GAUSSIAN_TRUNCATION"])
            self.assertNotIn("warnings", top["s0001"])
            fake = {"rows": summary["rows"], "parameters": [], "status": "COMPLETED", "sampling": {},
                    "nominal": {"outcome": "PASS", "targets": []}, "counts": {}, "target_distributions": [],
                    "zero_offset_check": None, "identity": {"engine": {"engine_version": "x",
                                                                       "engine_build_id": "y"},
                                                            "catalog_version": None},
                    "seconds": 0.0, "paths": {}}
            self.assertEqual(ts.compact(fake)["samples_with_warnings"], flagged)


class HistoricalBestSampleTests(unittest.TestCase):
    def test_best_sample_line_carries_the_historical_label(self):
        spec = {"parameters": [{"name": "shift", "unit": "mm", "values": [-1, 1], "ops": SHIFT_FOCUS}],
                "max_evaluations": 5, "objective": {"column": "target:spot_radius", "sense": "minimize"}}
        with TempDir() as tmp:
            summary = sp.run_sweep(spec, tmp / "study", base=designs.expander_focus())
            best = summary["best"]["sample_id"]
            current = wc.write_json(tmp / "current.json", designs.expander_focus(max_spot_radius_mm=0.03))
            report = br.build_report([tmp / "study"], project=current)
            lines = (tmp / "study" / "report" / "report.md").read_text(encoding="utf-8").splitlines()
            line = next(item for item in lines if item.startswith("Best sample:"))
            self.assertIn(f"{best} (HISTORICAL {summary['best']['outcome']})", line)
            self.assertEqual(report["section"]["best"]["outcome_label"], f"HISTORICAL {summary['best']['outcome']}")


TABULATED_LENS = {"model_id": "metalens", "model": {
    "model_kind": "TABULATED_THIN_LENS", "model_version": "1",
    "rows": [{"wavelength_nm": "500", "focal_length_mm": "0.2", "transmittance": "0.8"},
             {"wavelength_nm": "560", "focal_length_mm": "0.18", "transmittance": "0.7"}],
    "focal_interpolation": "LINEAR_IN_OPTICAL_POWER", "transmittance_interpolation": "LINEAR",
    "clear_radius_mm": "0.05", "edge_radius_mm": "0.06", "max_field_angle_deg": "2", "max_beam_radius_mm": "0.03",
    "validated_polarizations": ["LINEAR_X"],
    "supported_metrics": [{"metric": "waist_radius_x_mm", "rel_error": "0.1", "basis": "test fixture"}],
    "evidence": {"method": "test fixture", "tools": [{"name": "none", "version": "0"}], "source_data_sha256": "0" * 64,
                 "fit_model": "test fixture", "validation_summary": "test fixture", "date": "2026-10-15"},
    "status": "DECLARED"}}


def tabulated_lens_project(wavelengths_nm):
    """Catalog-free tabulated thin lens (rows 500-560 nm) with one failing target at 530 nm."""
    from examples.interop.metasurface import step4_engine_check

    document = step4_engine_check.project(TABULATED_LENS, "tabulated", wavelengths_nm=wavelengths_nm, waist_um=20.0)
    document["targets"] = [{"target_id": "spot_530nm", "metric": "waist_radius_x_mm", "observation_id": "after",
                            "wavelength_nm": "530", "maximum": 1e-6, "unit": "mm"}]
    return document


class EngineOutOfModelIssueTests(unittest.TestCase):
    """A tabulated thin lens outside its rows is a model-scope issue (MODEL_WAVELENGTH_OUTSIDE_BAND). The target that
    depends on the lens keeps its value and bounds but is assessed INDETERMINATE with the scope code in
    applicability_issue_codes, so the sample outcome is OUT_OF_MODEL while the run status stays FAIL for the unmet
    numeric bound. The 530 nm reference run stays inside the model."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = TempDir()
        cls.dir = cls._tmp.__enter__()
        cls.inside = wc.run_sample(tabulated_lens_project([530.0]), cls.dir / "inside")
        cls.outside = wc.run_sample(tabulated_lens_project([450.0, 530.0]), cls.dir / "outside")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.__exit__(None, None, None)

    def test_run_sample_classifies_out_of_model(self):
        self.assertEqual((self.inside["outcome"], self.inside["status"]), ("FAIL", "FAIL"))
        self.assertEqual(self.outside["status"], "FAIL")
        self.assertEqual([issue["code"] for issue in self.outside["model_scope_issues"]],
                         ["MODEL_WAVELENGTH_OUTSIDE_BAND"])
        self.assertEqual(self.outside["outcome"], "OUT_OF_MODEL")
        self.assertFalse(self.outside["in_model_scope"])
        self.assertIn("MODEL_WAVELENGTH_OUTSIDE_BAND", [i["code"] for i in self.outside["out_of_model_issues"]])
        target = self.outside["targets"][0]
        self.assertEqual((target["assessment"], target["applicability_issue_codes"]),
                         ("INDETERMINATE", ["MODEL_WAVELENGTH_OUTSIDE_BAND"]))
        self.assertEqual(target["value"], self.inside["targets"][0]["value"])

    def test_edit_run_exits_3(self):
        wc.write_json(self.dir / "base.json", tabulated_lens_project([450.0, 530.0]))
        wc.write_json(self.dir / "edit.json", [{"op": "set_target", "target_id": "spot_530nm",
                                                "changes": {"maximum": 2e-6}}])
        code, out, err = invoke(ep.main, [str(self.dir / "base.json"), "--edits", str(self.dir / "edit.json"),
                                          "--out", str(self.dir / "edited.json"), "--run", str(self.dir / "edited")])
        self.assertEqual(code, 3, err)
        self.assertEqual((out["status"], out["run"]["status"], out["run"]["outcome"]),
                         ("OUT_OF_MODEL_SCOPE", "FAIL", "OUT_OF_MODEL"))
        self.assertEqual([issue["code"] for issue in out["run"]["model_scope_issues"]],
                         ["MODEL_WAVELENGTH_OUTSIDE_BAND"])
        self.assertTrue(out["run"]["out_of_model_issues"])

    def test_sweep_compare_and_report_use_the_same_rule(self):
        spec = {"parameters": [{"name": "short", "unit": "nm", "values": [530.0, 450.0]}], "max_evaluations": 3}
        summary = sp.run_sweep(spec, self.dir / "study",
                               build=lambda p: tabulated_lens_project(sorted({p["short"], 530.0})))
        rows = {r["sample_id"]: r for r in summary["rows"]}
        self.assertEqual((rows["s0001"]["outcome"], rows["s0002"]["outcome"]), ("FAIL", "OUT_OF_MODEL"))
        self.assertEqual(rows["s0002"]["out_of_model_issues"], ["MODEL_WAVELENGTH_OUTSIDE_BAND"])
        self.assertEqual(summary["counts"]["by_outcome"]["FAIL"], 1)
        self.assertEqual(summary["counts"]["by_outcome"]["OUT_OF_MODEL"], 1)
        document = cr.compare([self.dir / "inside" / "run", self.dir / "outside" / "run"])
        runs = {r["label"]: r for r in document["runs"]}
        self.assertEqual(runs["outside"]["outcome"], "OUT_OF_MODEL")
        self.assertEqual(runs["outside"]["out_of_model_issues"], ["MODEL_WAVELENGTH_OUTSIDE_BAND"])
        report = br.build_report([self.dir / "inside" / "run", self.dir / "outside" / "run"],
                                 out_dir=self.dir / "report")
        self.assertEqual(report["diagnostics"]["model_scope_issues"], {"MODEL_WAVELENGTH_OUTSIDE_BAND": 2})

    def test_design_outcome_agrees(self):
        result = designs.design_outcome(designs.run_design(tabulated_lens_project([450.0, 530.0])))
        self.assertFalse(result["in_model_scope"])


class OutOfModelIssueListTests(unittest.TestCase):
    """out_of_model_issues holds one entry per distinct code and location; a target row adds an entry only for a code
    that no run issue reports, and the report counts the fact once per sample."""

    SUMMARY = {
        "status": "OUT_OF_MODEL_SCOPE", "engine_status": "OUT_OF_MODEL_SCOPE", "model_scope_issues": [],
        "issues": [
            {"code": "GAUSSIAN_SEVERE_TRUNCATION", "status": "OUT_OF_MODEL_SCOPE", "location": "surface:L2/lens"},
            {"code": "GAUSSIAN_SEVERE_TRUNCATION", "status": "OUT_OF_MODEL_SCOPE", "location": "surface:L2/lens"},
            {"code": "GAUSSIAN_SEVERE_TRUNCATION", "status": "OUT_OF_MODEL_SCOPE", "location": "surface:L3/lens"},
            {"code": "GAUSSIAN_TRUNCATION", "status": "WARNING", "location": "surface:L1/lens"},
        ],
        "targets": [
            {"id": "waist", "metric": "waist_radius", "value": None, "unit": "mm", "status": "OUT_OF_MODEL_SCOPE",
             "issue_codes": ["GAUSSIAN_SEVERE_TRUNCATION"]},
            {"id": "spot", "metric": "spot_radius", "value": None, "unit": "mm", "status": "OUT_OF_MODEL_SCOPE",
             "issue_codes": ["GAUSSIAN_SEVERE_TRUNCATION", "OBSERVATION_NOT_REACHED", "OBSERVATION_NOT_REACHED"]},
            {"id": "power", "metric": "power", "value": None, "unit": "W", "status": "OUT_OF_MODEL_SCOPE"},
            {"id": "ok", "metric": "power", "value": 1.0, "unit": "W", "status": "PASS"},
        ],
    }

    def test_entries_are_distinct(self):
        fields = wc.sample_from_summary(copy.deepcopy(self.SUMMARY))
        self.assertEqual([(i["code"], i["location"]) for i in fields["out_of_model_issues"]], [
            ("GAUSSIAN_SEVERE_TRUNCATION", "surface:L2/lens"), ("GAUSSIAN_SEVERE_TRUNCATION", "surface:L3/lens"),
            ("OBSERVATION_NOT_REACHED", "target spot"), ("OUT_OF_MODEL_SCOPE", "target power")])
        self.assertEqual(fields["outcome"], "OUT_OF_MODEL")
        self.assertFalse(fields["in_model_scope"])
        self.assertEqual(fields["warnings"], ["GAUSSIAN_TRUNCATION"])

    def test_target_rows_alone_still_mark_the_sample(self):
        summary = copy.deepcopy(self.SUMMARY)
        summary["issues"] = []
        fields = wc.sample_from_summary(summary)
        self.assertEqual([(i["code"], i["location"]) for i in fields["out_of_model_issues"]], [
            ("GAUSSIAN_SEVERE_TRUNCATION", "target waist"), ("GAUSSIAN_SEVERE_TRUNCATION", "target spot"),
            ("OBSERVATION_NOT_REACHED", "target spot"), ("OUT_OF_MODEL_SCOPE", "target power")])
        self.assertEqual(fields["outcome"], "OUT_OF_MODEL")

    def test_report_counts_each_fact_once(self):
        fields = wc.sample_from_summary(copy.deepcopy(self.SUMMARY))
        scope = br._diagnostics({"kind": "runs", "runs": [fields]})["model_scope_issues"]
        self.assertEqual(scope, {"GAUSSIAN_SEVERE_TRUNCATION": 2, "OBSERVATION_NOT_REACHED": 1, "OUT_OF_MODEL_SCOPE": 1})


if __name__ == "__main__":
    unittest.main()
