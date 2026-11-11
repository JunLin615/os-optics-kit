"""A design workflow and the study benchmark with two worker processes (catalog-free, at most 2 workers).

The expansion-focus workflow runs two studies once serially and once with ``--workers 2`` (one pool serves both
studies): every study's table.csv (without ``seconds``) and closed-form comparison are equal, and the parallel
studies record the process scheduler. The benchmark times one and two workers on a small expander study and finds
equal results.
"""

import csv
from importlib.util import module_from_spec, spec_from_file_location
import unittest

from tests.workflow_fixtures import ROOT, TempDir, invoke, quiet_environment, requires_engine

quiet_environment()

STUDIES = ("gap_error_mm", "f3_mm")


def case_workflow(case):
    spec = spec_from_file_location(f"case_{case.replace('-', '_')}_workers", ROOT / "cases" / case / "workflow.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rows(path, drop=("seconds",)):
    with open(path, newline="", encoding="utf-8") as handle:
        return [{k: v for k, v in row.items() if k not in drop} for row in csv.DictReader(handle)]


@requires_engine
class DesignWorkflowWorkerTests(unittest.TestCase):
    def test_two_workers_give_the_serial_studies(self):
        from os_optics_kit import workflow_common as wc

        workflow = case_workflow("expansion-focus")
        with TempDir() as tmp:
            outputs = {}
            for workers in ("1", "2"):
                out = tmp / f"w{workers}"
                code, document, err = invoke(workflow.main, [
                    "--out", str(out), "--study", "gap_error_mm=0,5", "--study", "f3_mm=150", "--max-plots", "0",
                    "--workers", workers])
                self.assertEqual(code, 0, err)
                self.assertEqual(document["status"], "PASS")
                outputs[workers] = out
            for name in STUDIES:
                with self.subTest(study=name):
                    serial, parallel = (outputs[w] / "studies" / name for w in ("1", "2"))
                    self.assertEqual(rows(serial / "table.csv"), rows(parallel / "table.csv"))
                    self.assertEqual(rows(serial / "closed_form.csv", drop=()),
                                     rows(parallel / "closed_form.csv", drop=()))
                    execution = wc.read_json(parallel / "study.json")["execution"]
                    self.assertEqual((execution["scheduler"], execution["workers"], execution["state"]),
                                     ("process", 2, "COMPLETED"))
                    self.assertEqual(wc.read_json(serial / "study.json")["execution"]["scheduler"], "serial")


@requires_engine
class BenchmarkWorkerTests(unittest.TestCase):
    def test_benchmark_with_one_and_two_workers(self):
        from os_optics_kit.study import bench

        with TempDir() as tmp:
            result = bench.run_benchmark(tmp / "bench", workers=[1, 2], samples=4, designs="expander")
            self.assertEqual(result["status"], "BENCHMARKED")
            runs = {r["workers"]: r for r in result["runs"]}
            self.assertEqual(sorted(runs), [1, 2])
            self.assertTrue(runs[2]["equal_to_one_worker"])
            self.assertEqual(runs[2]["states"], {"COMPLETED": 4})
            self.assertEqual(len(runs[2]["startup_s"]), 2)
            self.assertIn("| expander | 2 |", (tmp / "bench" / "bench.md").read_text(encoding="utf-8"))
            self.assertEqual(result["parallel_not_faster"],  # whatever the timing, the record lists exactly these
                             [{"design": r["design"], "workers": r["workers"], "speedup": r["speedup"]}
                              for r in result["runs"] if r["workers"] > 1 and r["speedup"] <= 1.0])
            for run in result["runs"]:
                self.assertTrue(run["elapsed_s"] > 0 and run["throughput"] > 0 and run["peak_memory_mib"] > 0, run)


if __name__ == "__main__":
    unittest.main()
