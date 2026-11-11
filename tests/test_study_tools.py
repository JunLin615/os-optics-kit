"""The study tools as compositions of the shared components: sweep, tolerance and candidate studies with one and two
workers, plan-only + ``study run`` + ``study aggregate``, and presentation from a saved study without the Engine.

One process pool of two workers serves every parallel study of a test class (start-up is paid once).
"""

import csv
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

from tests.workflow_fixtures import (HAS_MATPLOTLIB, TempDir, expander, invoke, quiet_environment,
                                     requires_engine)

quiet_environment()

from os_optics_kit import build_report as br  # noqa: E402
from os_optics_kit import sensitivity_analysis as sa  # noqa: E402
from os_optics_kit import study_tool as st  # noqa: E402
from os_optics_kit import sweep_parameters as sp  # noqa: E402
from os_optics_kit import tolerance_study as ts  # noqa: E402
from os_optics_kit import workflow_common as wc  # noqa: E402
from os_optics_kit.study import execute, requests as rq, schedulers, store  # noqa: E402
from tests import test_cli as cli_tests  # noqa: E402

# L2 x position (0.5 mm overlaps L1: refused at run) x wavelength (L3 is modelled for 700-900 nm only)
SWEEP_SPEC = {
    "parameters": [
        {"name": "l2_x", "unit": "mm", "values": [0.5, 200.0, 202.0],
         "ops": [{"op": "set_component_pose", "instance_id": "L2", "position_mm": ["$value", 0, 0]}]},
        {"name": "wavelength", "unit": "nm", "values": [780.0, 1064.0],
         "ops": [{"op": "set_source_model", "instance_id": "SRC", "changes": {"wavelength_nm": "$value"}}]},
    ],
    "max_evaluations": 6,
    "objective": {"column": "target:spot_radius", "sense": "minimize"},
}
TOLERANCE_SPEC = {
    "targets": ["spot_radius"], "samples": 4, "seed": 20261111, "max_evaluations": 5,
    "parameters": [{"name": "L2_axial", "unit": "mm",
                    "ops": [{"op": "transform", "selector": {"ids": ["L2"]}, "frame": {"component": "L2"},
                             "translation_mm": [0, 0, "$value"]}],
                    "distribution": {"kind": "uniform", "low": -1.0, "high": 1.0}}]}
#: fields that may differ between two executions of one plan (timing, workers, paths of two directories)
VOLATILE = ("seconds", "study_dir")


def stable(value):
    """``value`` without the fields that may differ between equivalent executions."""
    if isinstance(value, dict):
        return {k: stable(v) for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, list):
        return [stable(v) for v in value]
    return value


def table(path):
    with open(path, encoding="utf-8", newline="") as handle:
        return [{k: v for k, v in row.items() if k != "seconds"} for row in csv.DictReader(handle)]


def candidate_files(folder):
    files = []
    for name, f2 in (("short", 140.0), ("nominal", 150.0), ("long", 160.0)):
        files.append(wc.write_json(Path(folder) / f"{name}.json", expander(f2=f2)))
    return files


@requires_engine
class SerialParallelEquivalenceTests(unittest.TestCase):
    """Sweep, tolerance and candidate studies: serial and two workers give the same tables and summaries."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = TempDir()
        cls.dir = cls._tmp.__enter__()
        cls.pool = schedulers.ProcessScheduler(2)
        base = expander(l3_band_nm=(700, 900))
        files = candidate_files(cls.dir)
        cls.studies = {}
        for label, scheduler in (("serial", None), ("parallel", cls.pool)):
            cls.studies[("sweep", label)] = sp.run_sweep(SWEEP_SPEC, cls.dir / f"sweep-{label}", base=base,
                                                         scheduler=scheduler)
            cls.studies[("tolerance", label)] = ts.run_tolerance(TOLERANCE_SPEC, cls.dir / f"tol-{label}",
                                                                 base=expander(), plots=False, report=False,
                                                                 scheduler=scheduler)
            cls.studies[("candidates", label)] = st.run_candidates(files, cls.dir / f"cand-{label}",
                                                                   targets=["spot_radius"], scheduler=scheduler)

    @classmethod
    def tearDownClass(cls):
        cls.pool.close()
        cls._tmp.__exit__(None, None, None)

    def assert_equivalent(self, kind, folder):
        serial, parallel = self.studies[(kind, "serial")], self.studies[(kind, "parallel")]
        self.assertEqual(stable(serial), stable(parallel))
        self.assertEqual(table(self.dir / f"{folder}-serial" / "table.csv"),
                         table(self.dir / f"{folder}-parallel" / "table.csv"))
        self.assertEqual(stable(wc.read_json(self.dir / f"{folder}-serial" / "summary.json")),
                         stable(wc.read_json(self.dir / f"{folder}-parallel" / "summary.json")))
        execution = store.read_study(self.dir / f"{folder}-parallel")["execution"]
        self.assertEqual((execution["scheduler"], execution["workers"], execution["state"]),
                         ("process", 2, "COMPLETED"))
        self.assertEqual(store.read_study(self.dir / f"{folder}-serial")["execution"]["scheduler"], "serial")
        return serial

    def test_sweep(self):
        summary = self.assert_equivalent("sweep", "sweep")
        self.assertEqual([r["outcome"] for r in summary["rows"]],
                         ["ERROR", "ERROR", "PASS", "OUT_OF_MODEL", "FAIL", "OUT_OF_MODEL"])
        self.assertEqual(summary["best"]["sample_id"], "s0003")
        self.assertTrue(all(r["result_hash"] for r in summary["rows"][2:]))

    def test_tolerance(self):
        summary = self.assert_equivalent("tolerance", "tol")
        self.assertEqual([r["sample_id"] for r in summary["rows"]], ["s0001", "s0002", "s0003", "s0004"])
        drawn = ts.draw_samples(ts.normalize_spec(TOLERANCE_SPEC)["parameters"], 4, 20261111)
        self.assertEqual([r["param:L2_axial"] for r in summary["rows"]], [o["L2_axial"] for _s, o in drawn])
        self.assertEqual(ts.verify_samples(self.dir / "tol-parallel")["status"], "REPRODUCED")

    def test_candidates(self):
        summary = self.assert_equivalent("candidates", "cand")
        self.assertEqual([(r["sample_id"], r["candidate"], r["param:candidate"]) for r in summary["rows"]],
                         [("s0001", "short", 1), ("s0002", "nominal", 2), ("s0003", "long", 3)])
        self.assertEqual(summary["parameters"], [{"name": "candidate", "unit": "", "count": 3}])
        self.assertEqual(len({r["result_hash"] for r in summary["rows"]}), 3)

    def test_parallel_records_are_isolated_per_sample(self):
        folder = self.dir / "sweep-parallel"
        records = [wc.read_json(folder / "samples" / f"s000{i}" / "sample.json") for i in range(1, 7)]
        self.assertTrue(all(r["worker"].startswith("pid-") and r["worker"] != f"pid-{os.getpid()}"
                            for r in records if r["state"] == "COMPLETED" and r["run_dir"]))
        runs = [r["run_dir"] for r in records if r["run_dir"]]
        self.assertEqual(len(runs), len(set(runs)))
        for run in runs:
            self.assertTrue((folder / run / "manifest.json").is_file())
        self.assertEqual(records[0]["state"], "COMPLETED")  # an Engine refusal is a completed evaluation
        self.assertEqual(records[0]["error"]["code"], "RUN_REFUSED")
        events = [e["event"] for e in store.read_journal(folder)]
        self.assertEqual(events.count("finished"), 6)


@requires_engine
class CompositionTests(unittest.TestCase):
    """Plan only -> study run -> study aggregate equals the one-shot tool; presentation needs no Engine."""

    def test_plan_run_aggregate_equals_one_shot_and_presentation_reads_the_saved_study(self):
        with TempDir() as tmp:
            spec = {**SWEEP_SPEC, "base_project": "base.json"}
            wc.write_json(tmp / "base.json", expander(l3_band_nm=(700, 900)))
            wc.write_json(tmp / "spec.json", spec)
            code, out, err = invoke(sp.main, [str(tmp / "spec.json"), "--out", str(tmp / "one")])
            self.assertEqual(code, 0, err)
            code, out, err = invoke(sp.main, [str(tmp / "spec.json"), "--out", str(tmp / "planned"), "--plan-only"])
            self.assertEqual((code, out["status"], out["samples"]), (0, "PLANNED", 6))
            self.assertFalse((tmp / "planned" / "samples").exists())
            code, out, err = invoke(st.main, ["status", str(tmp / "planned")])
            self.assertEqual((code, out["execution"]["state"], out["to_run_on_resume"]["total"]), (0, "PLANNED", 6))
            code, out, err = invoke(st.main, ["run", str(tmp / "planned")])
            self.assertEqual((code, out["status"], out["best"]["sample_id"]), (0, "COMPLETED", "s0003"))
            self.assertEqual(stable(wc.read_json(tmp / "one" / "summary.json"))["rows"],
                             stable(wc.read_json(tmp / "planned" / "summary.json"))["rows"])
            (tmp / "planned" / "summary.json").unlink()
            (tmp / "planned" / "table.csv").unlink()
            done = cli_tests.run_blocked(["os_optics"], "study", "aggregate", str(tmp / "planned"))  # files only
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(json.loads(done.stdout)["best"]["sample_id"], "s0003")
            self.assertEqual(table(tmp / "one" / "table.csv"), table(tmp / "planned" / "table.csv"))
            saved = {p: p.stat().st_mtime_ns for p in (tmp / "planned" / "samples").rglob("*") if p.is_file()}
            code, out, err = invoke(br.main, [str(tmp / "planned"), "--out", str(tmp / "report")])
            self.assertEqual(code, 0, err)
            self.assertTrue((tmp / "report" / "report.md").is_file())
            if HAS_MATPLOTLIB:
                done = cli_tests.run_blocked(["os_optics"], "plot", "sweep", str(tmp / "planned"), "--y",
                                             "target:spot_radius", "--out", str(tmp / "plots"))
                self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(saved, {p: p.stat().st_mtime_ns for p in (tmp / "planned" / "samples").rglob("*")
                                     if p.is_file()})  # no sample file was written again
            journal = [e["event"] for e in store.read_journal(tmp / "planned")]
            self.assertEqual(journal.count("started"), 6)  # one evaluation per sample, in the one execution
            self.assertEqual(journal.count("execution_started"), 1)  # aggregation, report and plots ran nothing

    def test_sensitivity_plan_keeps_the_presentation_choice_and_resume_reuses_samples(self):
        with TempDir() as tmp:
            spec = {"design": {"builder": "expander_focus"}, "targets": ["spot_radius"], "max_evaluations": 5,
                    "parameters": [{"name": "L2_axial", "unit": "mm", "steps": [0.01, 0.1],
                                    "ops": [{"op": "transform", "selector": {"ids": ["L2"]},
                                             "frame": {"component": "L2"}, "translation_mm": [0, 0, "$value"]}]}]}
            wc.write_json(tmp / "spec.json", spec)
            code, out, _err = invoke(sa.main, [str(tmp / "spec.json"), "--out", str(tmp / "s"), "--plan-only",
                                               "--no-plots", "--no-report"])
            self.assertEqual((code, out["status"]), (0, "PLANNED"))
            code, out, err = invoke(st.main, ["run", str(tmp / "s")])
            self.assertEqual((code, out["status"]), (0, "COMPLETED"), err)
            summary = wc.read_json(tmp / "s" / "summary.json")
            self.assertEqual(summary["plots"]["skipped"], "plots disabled")
            self.assertFalse((tmp / "s" / "report").exists())
            code, out, err = invoke(sa.main, [str(tmp / "spec.json"), "--out", str(tmp / "s"), "--resume",
                                              "--no-plots", "--no-report"])
            self.assertEqual(code, 0, err)
            self.assertEqual(out["execution"]["reused"], 5)
            self.assertEqual(out["execution"]["ran"], 0)
            code, out, err = invoke(sa.main, [str(tmp / "spec.json"), "--out", str(tmp / "s")])
            self.assertEqual((code, json.loads(err)["error"]["code"]), (4, "OUTPUT_EXISTS"))

    def test_cli_workers_two_and_an_api_cancel_resumes(self):
        with TempDir() as tmp:
            files = candidate_files(tmp)
            code, out, err = invoke(st.main, ["candidates", *map(str, files), "--out", str(tmp / "c"),
                                              "--workers", "2", "--ray-data", "summary"])
            self.assertEqual((code, out["status"]), (0, "COMPLETED"), err)
            self.assertEqual((out["execution"]["workers"], out["execution"]["scheduler"]), (2, "process"))
            request = store.read_request(tmp / "c", "s0001")
            self.assertEqual(request["outputs"], {"run_directory": True, "ray_data": "summary"})
            for sample in (tmp / "c" / "samples").iterdir():
                (sample / "sample.json").unlink()
                shutil.rmtree(sample / "run")
            scheduler = schedulers.SerialScheduler()
            scheduler.cancel()  # cancelled before the first sample: every sample is recorded NOT_RUN
            summary = execute.execute_study(tmp / "c", scheduler=scheduler, resume=True)
            self.assertEqual((summary.state, summary.states), ("CANCELLED", {"NOT_RUN": 3}))
            self.assertEqual(store.read_study(tmp / "c")["execution"]["state"], "CANCELLED")
            code, out, err = invoke(st.main, ["run", str(tmp / "c"), "--resume"])
            self.assertEqual((code, out["counts"]["requested"], out["execution"]["ran"]), (0, 3, 3), err)


@requires_engine
class ToolResumeTests(unittest.TestCase):
    """--resume of the planning tools: a changed spec runs exactly the changed samples; a larger tolerance
    --samples with the same seed keeps the samples already run; --draw-only writes the sample set only."""

    def test_sweep_resume_with_a_changed_value_runs_only_that_sample(self):
        with TempDir() as tmp:
            wc.write_json(tmp / "base.json", expander())
            spec = {"base_project": "base.json", "max_evaluations": 3,
                    "parameters": [{"name": "l2_x", "unit": "mm", "values": [199.0, 200.0, 201.0],
                                    "ops": [{"op": "set_component_pose", "instance_id": "L2",
                                             "position_mm": ["$value", 0, 0]}]}]}
            wc.write_json(tmp / "spec.json", spec)
            code, out, err = invoke(sp.main, [str(tmp / "spec.json"), "--out", str(tmp / "s")])
            self.assertEqual(code, 0, err)
            spec["parameters"][0]["values"] = [199.0, 200.0, 201.5]
            wc.write_json(tmp / "spec.json", spec)
            code, out, err = invoke(sp.main, [str(tmp / "spec.json"), "--out", str(tmp / "s"), "--resume"])
            self.assertEqual(code, 0, err)
            self.assertEqual((out["execution"]["reused"], out["execution"]["ran"]), (2, 1))
            self.assertEqual([e["sample_id"] for e in store.read_journal(tmp / "s") if e["event"] == "started"][3:],
                             ["s0003"])

    def test_tolerance_resume_with_more_samples_keeps_the_samples_already_run(self):
        with TempDir() as tmp:
            wc.write_json(tmp / "base.json", expander())
            wc.write_json(tmp / "spec.json", {**TOLERANCE_SPEC, "base_project": "base.json", "max_evaluations": 7})
            common = [str(tmp / "spec.json"), "--out", str(tmp / "t"), "--no-plots", "--no-report"]
            code, out, err = invoke(ts.main, common)
            self.assertEqual(code, 0, err)
            code, out, err = invoke(ts.main, [*common, "--resume", "--samples", "6"])
            self.assertEqual(code, 0, err)
            self.assertEqual((out["execution"]["reused"], out["execution"]["ran"]), (5, 2))
            self.assertEqual(ts.verify_samples(tmp / "t")["status"], "REPRODUCED")

    def test_draw_only_writes_the_sample_set_and_no_requests(self):
        with TempDir() as tmp:
            wc.write_json(tmp / "base.json", expander())
            wc.write_json(tmp / "spec.json", {**TOLERANCE_SPEC, "base_project": "base.json"})
            code, out, err = invoke(ts.main, [str(tmp / "spec.json"), "--out", str(tmp / "d"), "--draw-only"])
            self.assertEqual((code, out["status"]), (0, "DRAWN"), err)
            self.assertEqual(sorted(p.name for p in (tmp / "d").iterdir()),
                             ["samples.csv", "sampling.json", "study.json"])
            code, out, err = invoke(ts.main, ["--verify-seed", str(tmp / "d")])
            self.assertEqual((code, out["status"]), (0, "REPRODUCED"), err)
            code, out, err = invoke(st.main, ["run", str(tmp / "d")])
            self.assertEqual((code, json.loads(err)["error"]["code"]), (4, "STUDY_NOT_PLANNED"))
            code, out, err = invoke(ts.main, [str(tmp / "spec.json"), "--out", str(tmp / "d"), "--draw-only",
                                              "--resume"])
            self.assertEqual((code, json.loads(err)["error"]["code"]), (4, "BAD_ARGUMENT"))

    def test_benchmark_runs_and_reports(self):
        from os_optics_kit.study import bench

        with TempDir() as tmp:
            result = bench.run_benchmark(tmp / "bench", workers=[1], samples=2, designs="expander")
            self.assertEqual(result["status"], "BENCHMARKED")
            self.assertEqual([(r["design"], r["workers"], r["equal_to_one_worker"]) for r in result["runs"]],
                             [("expander", 1, True)])
            self.assertIn("| design | workers |", (tmp / "bench" / "bench.md").read_text(encoding="utf-8"))
            self.assertEqual(wc.read_json(tmp / "bench" / "bench.json")["workers_skipped"], [])


class HelpTests(unittest.TestCase):
    def test_planned_next_runs_as_printed_and_workers_errors_are_readable(self):
        from os_optics_kit.study import options

        with TempDir() as tmp:
            store.plan_study(tmp / "p", {"kind": "test.study", "engine": {}, "installed_catalog": {}},
                             [rq.make_request("s0001", {"x": 1.0})])
            command = options.planned_summary(tmp / "p", "test")["next"].split()
            self.assertEqual(command[:3], ["os-optics-kit", "study", "run"])
            args = st._parser().parse_args(command[2:])
            self.assertEqual((args.command, args.study_dir, args.workers), ("run", tmp / "p", 1))
        for value, words in (("0", "give at least 1 worker"), ("many", "give a positive integer or auto")):
            code, _out, err = invoke(st.main, ["run", "nowhere", "--workers", value])
            message = json.loads(err)["error"]["message"]
            self.assertEqual(code, 4)
            self.assertIn(f"argument --workers: {words}", message)
            self.assertNotIn("workers_value", message)

    def test_help_lists_the_entries_and_resource_options(self):
        done = subprocess.run([sys.executable, "-m", "os_optics_kit", "study", "--help"], capture_output=True,
                              text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)
        for word in ("run", "status", "aggregate", "candidates", "bench", "--workers", "--resume", "--time-limit-s",
                     "--max-memory-mib", "--evaluator", "130"):
            self.assertIn(word, done.stdout)
        for tool in (sp, sa, ts):
            parser_help = subprocess.run([sys.executable, "-m", "os_optics_kit", tool.PROG.split()[-1], "--help"],
                                         capture_output=True, text=True, timeout=120).stdout
            for word in ("--workers", "--resume", "--plan-only", "--time-limit-s", "--max-memory-mib", "--ray-data"):
                self.assertIn(word, parser_help, tool.PROG)


if __name__ == "__main__":
    unittest.main()
