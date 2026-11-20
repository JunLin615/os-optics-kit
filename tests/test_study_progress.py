"""Readers of a saved study (study aggregate, plot sweep, report, compare, present) on a study that was planned and not
executed, executed in part, cancelled, executed but not aggregated, or being executed: one refusal each, with the
command that moves the study on (os_optics_kit.study.progress). Fake evaluator, no Engine needed.
"""

import csv
import json
import unittest

from tests.workflow_fixtures import HAS_MATPLOTLIB, TempDir, invoke, quiet_environment

quiet_environment()

from os_optics_kit import build_report as br  # noqa: E402
from os_optics_kit import compare_runs as cr  # noqa: E402
from os_optics_kit import plot_results as pr  # noqa: E402
from os_optics_kit import present  # noqa: E402
from os_optics_kit import presentation as pres  # noqa: E402
from os_optics_kit import study_tool as st  # noqa: E402
from os_optics_kit.study import execute, progress, schedulers, store  # noqa: E402
from tests.test_study import FAKE, fake_requests, header  # noqa: E402


def plan(study_dir, count=3):
    store.plan_study(study_dir, header(parameters=[{"name": "x", "unit": "mm"}]),
                     fake_requests([float(i) for i in range(1, count + 1)]))
    return study_dir


def error_of(err):
    return json.loads(err)["error"]


class ReaderRefusalTests(unittest.TestCase):
    def readers(self, study_dir, tmp, aggregate=True):
        """``(name, exit code, stdout, stderr)`` of every CLI reader of ``study_dir`` (``study aggregate`` last)."""
        calls = [("report", br.main, [str(study_dir), "--out", str(tmp / "report")]),
                 ("compare", cr.main, [str(study_dir)]),
                 ("present init", present.main, ["init", str(study_dir), "--out", str(tmp / "present")])]
        if HAS_MATPLOTLIB:
            calls.append(("plot sweep", pr.main, ["sweep", str(study_dir), "--y", "target:t", "--out",
                                                  str(tmp / "plots")]))
        if aggregate:
            calls.append(("aggregate", st.main, ["aggregate", str(study_dir)]))
        return [(name, *invoke(main, argv)) for name, main, argv in calls]

    def assert_refused(self, study_dir, tmp, code, next_command, *, readers=None, skip=()):
        for name, exit_code, out, err in readers or self.readers(study_dir, tmp):
            if name in skip:
                continue
            self.assertEqual((exit_code, out), (4, None), name)
            error = error_of(err)
            self.assertEqual((error["code"], error["next"]), (code, next_command), name)
            self.assertIn(f"`{next_command}`", error["message"], name)
        for folder in ("report", "present", "plots"):
            self.assertFalse((tmp / folder).exists(), folder)

    def test_a_planned_study_is_refused_by_every_reader_with_study_run(self):
        with TempDir() as tmp:
            study_dir = plan(tmp / "planned")
            command = f"os-optics-kit study run {study_dir.as_posix()}"
            self.assert_refused(study_dir, tmp, "STUDY_NOT_RUN", command)
            self.assertFalse((study_dir / "table.csv").exists())  # aggregate wrote no table of NOT_RUN rows
            self.assertFalse((study_dir / "summary.json").exists())
            self.assertFalse((study_dir / "samples").exists())
            args = st._parser().parse_args(command.split()[2:])  # the printed command is runnable as it stands
            self.assertEqual((args.command, args.study_dir, args.resume), ("run", study_dir, False))
            code, out, _err = invoke(st.main, ["status", str(study_dir)])
            self.assertEqual((code, out["progress"], out["next"]), (0, "NOT_RUN", command))
            if HAS_MATPLOTLIB:  # present build reports it on the assets that use the study
                folder = tmp / "deck"
                spec = pres.default_spec()
                spec["sources"]["study"] = {"path": "../planned"}
                spec["assets"].append({"id": "sweep-study", "kind": "plot", "type": "sweep", "source": "study",
                                       "y": "target:t"})
                folder.mkdir()
                pres.save_spec(spec, folder / pres.SPEC_FILE)
                code, out, err = invoke(present.main, ["build", str(folder / pres.SPEC_FILE), "--no-gallery",
                                                       "--quiet"])
                self.assertEqual((code, out["status"]), (2, "INCOMPLETE"), err)
                self.assertEqual([(m["asset"], m["code"]) for m in out["missing"]], [("sweep-study", "STUDY_NOT_RUN")])
                self.assertTrue(out["missing"][0]["recovery"].startswith("os-optics-kit study run "))
            # executing it as printed makes it readable
            code, out, err = invoke(st.main, ["run", str(study_dir), "--evaluator", FAKE])
            self.assertEqual((code, out["status"]), (0, "COMPLETED"), err)
            code, out, err = invoke(cr.main, [str(study_dir)])
            self.assertEqual((code, len(out["runs"])), (0, 3), err)

    def test_a_study_executed_in_part_is_aggregated_incomplete_and_refused_until_then(self):
        with TempDir() as tmp:
            study_dir = plan(tmp / "partial", 4)
            execute.execute_study(study_dir, evaluator=FAKE)
            for sample_id in ("s0003", "s0004"):  # a killed execution: two samples never got a record
                store.sample_path(study_dir, sample_id).unlink()
            (study_dir / "summary.json").unlink(missing_ok=True)
            resume = f"os-optics-kit study run {study_dir.as_posix()} --resume --evaluator {FAKE}"
            readers = self.readers(study_dir, tmp)
            self.assert_refused(study_dir, tmp, "STUDY_INCOMPLETE", resume, readers=readers, skip=("aggregate",))
            error = error_of(next(err for name, _c, _o, err in readers if name == "compare"))
            self.assertEqual(error["without_result"]["shown"], ["s0003", "s0004"])
            self.assertEqual(error["aggregate"], f"os-optics-kit study aggregate {study_dir.as_posix()}")
            code, out, _err = invoke(st.main, ["status", str(study_dir)])
            self.assertEqual((out["progress"], out["next"]), ("PARTIAL", resume))
            # study aggregate tabulates what ran: exit 2, status INCOMPLETE, the missing samples and the next command
            name, code, out, err = next(item for item in readers if item[0] == "aggregate")
            self.assertEqual((code, out["status"], out["analysis_status"]), (2, "INCOMPLETE", "COMPLETED"), err)
            self.assertEqual((out["without_result"]["shown"], out["next"]), (["s0003", "s0004"], resume))
            with open(study_dir / "table.csv", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual([r["error_code"] for r in rows], ["", "", "NOT_RUN", "NOT_RUN"])
            # that table is read until the plan changes or another execution starts
            code, out, err = invoke(cr.main, [str(study_dir)])
            self.assertEqual(code, 0, err)
            store.append_journal(study_dir, "execution_started")
            code, _out, err = invoke(cr.main, [str(study_dir)])
            self.assertEqual((code, error_of(err)["code"]), (4, "STUDY_INCOMPLETE"))
            # the printed command continues it: only the two missing samples run
            code, out, err = invoke(st.main, resume.split()[2:])
            self.assertEqual((code, out["status"], out["execution"]["ran"], out["execution"]["reused"]),
                             (0, "COMPLETED", 2, 2), err)
            self.assertEqual(progress.study_progress(study_dir)["state"], progress.EXECUTED)
            code, out, err = invoke(st.main, ["aggregate", str(study_dir)])
            self.assertEqual((code, out["status"]), (0, "COMPLETED"), err)

    def test_a_study_cancelled_before_its_first_sample_names_run_resume(self):
        with TempDir() as tmp:
            study_dir = plan(tmp / "cancelled")
            scheduler = schedulers.SerialScheduler()
            scheduler.cancel()
            summary = execute.execute_study(study_dir, scheduler=scheduler, evaluator=FAKE)
            self.assertEqual((summary.state, summary.states), ("CANCELLED", {"NOT_RUN": 3}))
            resume = f"os-optics-kit study run {study_dir.as_posix()} --resume --evaluator {FAKE}"
            self.assert_refused(study_dir, tmp, "STUDY_NOT_RUN", resume)  # without --resume: STUDY_STARTED

    def test_an_executed_study_without_a_table_names_study_aggregate(self):
        with TempDir() as tmp:
            study_dir = plan(tmp / "executed")
            execute.execute_study(study_dir, evaluator=FAKE)
            command = f"os-optics-kit study aggregate {study_dir.as_posix()}"
            readers = self.readers(study_dir, tmp, aggregate=False)
            self.assert_refused(study_dir, tmp, "STUDY_NOT_AGGREGATED", command, readers=readers)
            code, out, err = invoke(st.main, command.split()[2:])
            self.assertEqual((code, out["status"]), (0, "COMPLETED"), err)
            code, _out, err = invoke(cr.main, [str(study_dir)])
            self.assertEqual(code, 0, err)

    def test_a_study_being_executed_is_refused_as_running(self):
        with TempDir() as tmp:
            study_dir = plan(tmp / "running")
            owner = store.acquire_execution(study_dir)  # this process "executes" it
            try:
                for name, code, _out, err in self.readers(study_dir, tmp):
                    self.assertEqual((code, error_of(err)["code"]), (4, "STUDY_RUNNING"), name)
            finally:
                store.release_execution(study_dir, owner)

    def test_other_directories_keep_their_own_errors(self):
        with TempDir() as tmp:
            (tmp / "empty").mkdir()
            self.assertIsNone(progress.study_progress(tmp / "empty"))
            code, _out, err = invoke(cr.main, [str(tmp / "empty")])
            self.assertEqual((code, error_of(err)["code"]), (4, "RUN_NOT_READABLE"))
            with self.assertRaises(Exception) as caught:
                pr._study(tmp / "empty")
            self.assertEqual(caught.exception.code, "STUDY_NOT_READABLE")


if __name__ == "__main__":
    unittest.main()
