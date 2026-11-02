"""Offline checks of compare_runs.py, plot_results.py, build_report.py and catalog_candidates.py.

Catalog-free by default; the catalog query test is skipped when no catalog release is installed.
Run from the repository root: python -m unittest discover -s tests -v
"""

import copy
from importlib.util import find_spec
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest

from tests.workflow_fixtures import ROOT, TempDir, by_id, catalog_available, expander, invoke, quiet_environment

quiet_environment()

from examples import build_report as br  # noqa: E402
from examples import catalog_candidates as cc  # noqa: E402
from examples import compare_runs as cr  # noqa: E402
from examples import edit_project as ep  # noqa: E402
from examples import sweep_parameters as sp  # noqa: E402
from examples import workflow_common as wc  # noqa: E402

HAS_MATPLOTLIB = find_spec("matplotlib") is not None


class CompareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = TempDir()
        cls.dir = cls._tmp.__enter__()
        base = expander()
        wc.write_json(cls.dir / "base.json", base)
        far = copy.deepcopy(by_id(base["observations"], "observation_id", "focus"))
        far.update({"observation_id": "far", "pose": {**far["pose"], "position_mm": [450.0, 0.0, 0.0]}})
        extended = ep.apply_edits(base, [
            {"op": "add_observation", "observation": far},
            {"op": "add_target", "target": {"target_id": "far_radius", "metric": "beam_radius_mm",
                                            "observation_id": "far", "unit": "mm", "maximum": 5,
                                            "parameters": []}},
            {"op": "set_source_model", "instance_id": "SRC", "changes": {"wavelength_nm": 785}}])["project"]
        cls.a = wc.run_sample(base, cls.dir / "a")
        cls.b = wc.run_sample(extended, cls.dir / "b")
        # the customer edits the base afterwards: run "a" is now historical for that Project
        cls.current = ep.apply_edits(base, [{"op": "set_target", "target_id": "spot_radius",
                                             "changes": {"maximum": 0.019}}])["project"]
        wc.write_json(cls.dir / "current.json", cls.current)
        cls.doc = cr.compare([cls.dir / "a" / "run", cls.dir / "b" / "run"], labels=["a", "b"],
                             project=cls.dir / "current.json")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.__exit__(None, None, None)

    def test_targets_aligned_by_id_with_missing_entries(self):
        targets = {t["id"]: t for t in self.doc["targets"]}
        self.assertEqual(targets["far_radius"]["runs"]["a"], {"missing": True})
        self.assertEqual(targets["far_radius"]["runs"]["b"]["unit"], "mm")
        spot = targets["spot_radius"]["runs"]
        self.assertAlmostEqual(spot["b"]["delta"], spot["b"]["value"] - spot["a"]["value"])
        observations = {o["id"]: o for o in self.doc["observations"]}
        self.assertEqual(observations["far"]["runs"]["a"], {"missing": True})
        self.assertGreater(observations["far"]["runs"]["b"]["total_power_W"], 0)

    def test_input_differences(self):
        inputs = {d["input"]: d for d in self.doc["input_differences"]}
        self.assertEqual(inputs["source:SRC.wavelength_nm"]["values"], {"a": 780.0, "b": 785.0})
        self.assertEqual(inputs["source:SRC.wavelength_nm"]["unit"], "nm")
        self.assertEqual(inputs["target:far_radius.metric"]["values"]["a"], "<missing>")
        self.assertNotIn("run.engine_build_id", inputs)

    def test_freshness_labels_historical_pass(self):
        runs = {r["label"]: r for r in self.doc["runs"]}
        self.assertEqual(runs["a"]["outcome"], "PASS")
        self.assertEqual(runs["a"]["freshness"]["freshness"], "HISTORICAL")
        self.assertEqual(runs["a"]["freshness"]["reasons"], ["TARGETS_CHANGED"])
        self.assertEqual(runs["a"]["outcome_label"], "HISTORICAL PASS")
        same = wc.freshness(self.dir / "base.json", self.dir / "a" / "run")
        self.assertEqual((same["freshness"], same["reasons"]), ("CURRENT", []))

    def test_tampered_run_is_unverified(self):
        target = self.dir / "tampered"
        import shutil

        shutil.copytree(self.dir / "a" / "run", target)
        path = target / "simulation_result.json"
        path.write_text(path.read_text(encoding="utf-8").replace("PASS", "FAIL", 1), encoding="utf-8")
        result = wc.freshness(self.dir / "base.json", target)
        self.assertIn("RUN_UNVERIFIED", result["reasons"])

    def test_cli_writes_json_and_csv(self):
        code, out, _err = invoke(cr.main, [str(self.dir / "a" / "run"), str(self.dir / "b" / "run"), "--labels",
                                           "a,b", "--project", str(self.dir / "current.json"), "--out",
                                           str(self.dir / "cmp")])
        self.assertEqual(code, 0)
        self.assertEqual(out["runs"][0]["outcome_label"], "HISTORICAL PASS")
        self.assertEqual(out["observations_W"][-1]["runs"]["a"], {"missing": True})
        text = (self.dir / "cmp" / "compare.csv").read_text(encoding="utf-8")
        self.assertIn("target,far_radius,beam_radius_mm,mm,a,,MISSING", text)
        self.assertEqual(wc.read_json(self.dir / "cmp" / "compare.json")["reference"], "a")


def _sweep(directory, two=False):
    parameters = [{"name": "f3", "unit": "mm", "values": [75.0, 100.0, 150.0]}]
    if two:
        parameters.append({"name": "wavelength", "unit": "nm", "values": [633.0, 1064.0]})
    spec = {"parameters": parameters, "max_evaluations": 6,
            "objective": {"column": "target:spot_radius", "sense": "minimize"}}
    return sp.run_sweep(spec, directory, build=lambda p: expander(
        f3=p["f3"], wavelength_nm=p.get("wavelength", 780.0), l3_band_nm=(600, 900)))


@unittest.skipUnless(HAS_MATPLOTLIB, "matplotlib is needed for plots (requirements-examples.txt)")
class PlotAndReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from examples import plot_results as pr

        cls.pr = pr
        cls._tmp = TempDir()
        cls.dir = cls._tmp.__enter__()
        cls.summary = _sweep(cls.dir / "study")
        cls.summary2 = _sweep(cls.dir / "study2", two=True)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.__exit__(None, None, None)

    def test_sweep_curve_has_labels_status_and_caption(self):
        result = self.pr.plot_sweep(self.dir / "study", "target:spot_radius")
        svg = Path(result["out"]["svg"]).read_text(encoding="utf-8")
        for text in ("spot_radius [mm]", "f3 [mm]", "PASS (2)", "FAIL (1)", "target maximum 0.02",
                     "engine " + wc.engine_identity()["engine_version"], "catalog none (catalog-free run)", "data: ../summary.json (sha256 "):
            self.assertIn(text, svg)
        self.assertTrue(Path(result["out"]["png"]).stat().st_size > 1000)
        self.assertEqual(result["by_outcome"], {"PASS": 2, "FAIL": 1})

    def test_heatmap_marks_out_of_model_cells(self):
        result = self.pr.plot_sweep(self.dir / "study2", "target:spot_radius")
        svg = Path(result["out"]["svg"]).read_text(encoding="utf-8")
        for text in ("wavelength [nm]", "f3 [mm]", "OUT_OF_MODEL (model scope issue) (3)", "spot_radius [mm]"):
            self.assertIn(text, svg)

    def test_primitives_and_power(self):
        bars = self.pr.plot_bars(["L2 Tx", "L2 Rz"], [0.0021, -0.0004], "mm/mm", self.dir / "plots", "sens",
                                 title="Sensitivity of spot radius", ylabel="d spot / d x [mm/mm]")
        self.assertIn("d spot / d x [mm/mm]", Path(bars["out"]["svg"]).read_text(encoding="utf-8"))
        hist = self.pr.plot_histogram([0.01, 0.02, None, 0.015], "mm", self.dir / "plots", "hist",
                                      title="spot radius", bounds={"minimum": None, "maximum": 0.018})
        svg = Path(hist["out"]["svg"]).read_text(encoding="utf-8")
        self.assertIn("plotted 3 of 4 values", svg)
        self.assertIn("target maximum 0.018", svg)
        power = self.pr.plot_power(self.dir / "study" / "samples" / "s0001" / "run", out_dir=self.dir / "plots")
        self.assertTrue(power["conserved"])
        self.assertIn("received power [W]", Path(power["out"]["svg"]).read_text(encoding="utf-8"))

    def test_power_plot_preserves_unknown_and_bound_states(self):
        from unittest.mock import Mock, patch
        monitors = [
            {"observation_id": "known", "total_power_W": 1.0, "quantity_state": "KNOWN", "power_complete": True},
            {"observation_id": "bounded", "total_power_W": 0.3, "quantity_state": "BOUNDED",
             "power_complete": False, "power_upper_bound_W": 0.8},
            {"observation_id": "unknown", "total_power_W": None, "quantity_state": "UNKNOWN",
             "power_complete": False},
        ]
        results = Mock()
        results.monitors.return_value = monitors
        results.identity.return_value = {}
        results.power_budget.return_value = {}
        source = self.dir / "study" / "samples" / "s0001" / "run"
        with patch("os_optics.results.load_run", return_value=results):
            power = self.pr.plot_power(source, out_dir=self.dir / "mixed_power")
        self.assertIsNone(power["observations"]["unknown"]["total_power_W"])
        self.assertEqual(power["observations"]["bounded"]["power_upper_bound_W"], 0.8)
        svg = Path(power["out"]["svg"]).read_text(encoding="utf-8")
        self.assertIn("UNKNOWN", svg)
        self.assertIn("hatched: lower bound", svg)
        self.assertIn("bounded", svg)

    def test_report_links_resolve_and_hashes_match(self):
        self.pr.plot_sweep(self.dir / "study", "target:spot_radius", out_dir=self.dir / "study" / "plots")
        current = wc.read_json(self.dir / "study" / "samples" / "s0002" / "project.json")
        wc.write_json(self.dir / "current.json", current)
        report = br.build_report([self.dir / "study"], project=self.dir / "current.json",
                                 plots=[self.dir / "study" / "plots"])
        out_dir = self.dir / "study" / "report"
        text = (out_dir / "report.md").read_text(encoding="utf-8")
        links = re.findall(r"\]\(([^)]+)\)", text)
        self.assertGreater(len(links), 10)
        for link in links:
            self.assertTrue((out_dir / link).exists(), link)
        for artifact in report["artifacts"]:
            self.assertEqual(wc.sha256_file(out_dir / artifact["path"]), artifact["sha256"])
        self.assertIn("HISTORICAL PASS", text)  # s0001 passes but is not the current Project
        self.assertIn("| s0002 | PASS |", text)
        self.assertIn("PASS fraction: 2/3 of all requested samples", text)
        self.assertEqual(wc.read_json(out_dir / "report.json")["section"]["counts"]["requested"], 3)

    def test_report_with_caller_sections_and_artifacts(self):
        extra = wc.write_json(self.dir / "extra" / "reference.json", {"spot_radius_mm": 0.0165})
        out_dir = self.dir / "extra" / "report"
        report = br.build_report([self.dir / "study"], out_dir=out_dir, artifacts=[(extra, "reference check")],
                                 sections=[{"title": "Independent check", "lines": [
                                     "| quantity | value |", "| --- | --- |", "| spot | 0.0165 |", "",
                                     "Data: [reference.json](../reference.json)"]}])
        text = (out_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("## Independent check\n\n| quantity | value |", text)
        self.assertLess(text.index("## Diagnostics"), text.index("## Independent check"))
        self.assertLess(text.index("## Independent check"), text.index("## Artifacts"))
        roles = {a["role"]: a for a in report["artifacts"]}
        self.assertEqual(roles["reference check"]["path"], "../reference.json")
        self.assertEqual(roles["reference check"]["sha256"], wc.sha256_file(extra))
        self.assertEqual(wc.read_json(out_dir / "report.json")["sections"][0]["title"], "Independent check")
        for link in re.findall(r"\]\(([^)]+)\)", text):
            self.assertTrue((out_dir / link).exists(), link)
        with self.assertRaises(wc.WorkflowError) as caught:
            br.build_report([self.dir / "study"], out_dir=out_dir, artifacts=[(self.dir / "missing.csv", "x")])
        self.assertEqual(caught.exception.code, "ARTIFACT_NOT_FOUND")

    def test_runs_report_with_change_record(self):
        base = wc.write_json(self.dir / "rb.json", expander())
        edits = wc.write_json(self.dir / "re.json", [{"op": "set_target", "target_id": "spot_radius",
                                                      "changes": {"maximum": {"value": 10, "unit": "um"}}}])
        code, out, _err = invoke(ep.main, [str(base), "--edits", str(edits), "--out", str(self.dir / "rn.json"),
                                           "--run", str(self.dir / "rrun")])
        self.assertEqual((code, out["run"]["outcome"]), (1, "FAIL"))
        code, out, _err = invoke(br.main, [str(self.dir / "rrun"), str(self.dir / "study" / "samples" / "s0002" /
                                                                       "run"), "--changes",
                                           str(self.dir / "rn.changes.json"), "--out", str(self.dir / "rrep")])
        self.assertEqual((code, out["kind"]), (0, "runs"))
        text = (self.dir / "rrep" / "report.md").read_text(encoding="utf-8")
        self.assertIn("| 0 | set_target | target spot_radius | maximum | 0.02 | 0.01 |", text)
        for link in re.findall(r"\]\(([^)]+)\)", text):
            self.assertTrue((self.dir / "rrep" / link).exists(), link)


class CandidateTests(unittest.TestCase):
    def test_range_parsing_and_units(self):
        self.assertEqual(cc.parse_range("focal_length_mm=90..110"), ("focal_length_mm", {"min": 90.0, "max": 110.0,
                                                                                         "unit": None}))
        self.assertEqual(cc.parse_range("outer_diameter_mm=12.7.."), ("outer_diameter_mm", {"min": 12.7,
                                                                                           "max": None, "unit": None}))
        self.assertEqual(cc.parse_range("focal_length_mm=0.09..0.11m")[1]["unit"], "m")
        self.assertEqual(str(cc._limit("focal_length_mm", 0.09, "m")), "90.0")
        with self.assertRaises(wc.WorkflowError):
            cc.parse_range("focal_length_mm>90")
        with self.assertRaises(wc.WorkflowError) as caught:
            cc._limit("focal_length_mm", 1, "deg")
        self.assertEqual(caught.exception.code, "UNIT_INCOMPATIBLE")
        with self.assertRaises(wc.WorkflowError) as caught:
            cc._field("outer_dimensions_mm")
        self.assertEqual(caught.exception.code, "FIELD_UNKNOWN")

    def test_missing_catalog_is_a_clear_error(self):
        with TempDir() as empty:
            env = {**os.environ, "OPENSCI_CATALOG_DIR": str(empty), "OPENSCI_CATALOG_CHECK_INTERVAL": "0"}
            done = subprocess.run([sys.executable, str(ROOT / "examples" / "catalog_candidates.py"), "--category",
                                   "LENS"], capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual((done.returncode, done.stdout), (4, ""))
        self.assertEqual(json.loads(done.stderr)["error"]["code"], "CATALOG_NOT_AVAILABLE")

    @unittest.skipUnless(catalog_available(), "no public catalog release installed (os-optics catalog sync)")
    def test_catalog_query_is_filtered_sorted_and_bounded(self):
        result = cc.candidates(category="LENS", form="PLANO_CONVEX_LENS", wavelength_nm="780",
                               ranges={"focal_length_mm": {"min": 90, "max": 110}}, near=("focal_length_mm", 100),
                               limit=3)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(len(result["rows"]), min(3, result["matched"]))
        self.assertEqual(result["omitted"], result["matched"] - len(result["rows"]))
        self.assertTrue(result["catalog_version"])
        values = [abs(float(r["focal_length_mm"]) - 100) for r in result["all_rows"]]
        self.assertEqual(values, sorted(values))
        self.assertTrue(all(90 <= float(r["focal_length_mm"]) <= 110 for r in result["all_rows"]))
        self.assertEqual(result["units"]["focal_length_mm"], "mm")
        none = cc.candidates(category="LENS", ranges={"focal_length_mm": {"min": 1e6}})
        self.assertEqual((none["status"], none["matched"], none["rows"]), ("NO_MATCH", 0, []))


if __name__ == "__main__":
    unittest.main()
