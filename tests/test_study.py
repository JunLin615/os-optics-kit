"""The study execution components (os_optics_kit.study): requests, store, execution, resume, cancellation.

Most tests use a fake evaluator (no Engine needed) so the contracts are checked in the unit job too; the tests of the
default evaluator are marked ``@requires_engine``. Process-pool tests use at most two workers and a few samples.
"""

import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from tests.workflow_fixtures import HAS_ENGINE, TempDir, expander, invoke, quiet_environment, requires_engine

quiet_environment()

from os_optics_kit import study_tool  # noqa: E402
from os_optics_kit import workflow_common as wc  # noqa: E402
from os_optics_kit.study import (aggregate, bench, evaluators, execute, options, requests as rq,  # noqa: E402
                                 resources, schedulers, store)

FAKE = "tests.test_study:fake_evaluator"
OTHER = "tests.test_study:other_evaluator"
INTERRUPTING = "tests.test_study:interrupting_evaluator"
NOISY = "tests.test_study:noisy_evaluator"
UNRULY = "tests.test_study:unruly_evaluator"
REPO = Path(__file__).resolve().parents[1]


def fake_evaluator(request, sample_dir, limits=None):
    """A stand-in for the Engine: target t = 2 x, outcome from the label ``fake.outcome``; ``fake.fail`` raises;
    ``fake.sleep`` waits (in steps that honour a study cancellation)."""
    fake = request["labels"].get("fake") or {}
    deadline = time.monotonic() + float(fake.get("sleep") or 0.0)
    while time.monotonic() < deadline:
        if evaluators.cancel_requested():
            record = evaluators.empty_record(None, request["selected_targets"])
            record["error"] = {"stage": "run", "code": "RUN_STOPPED",
                               "problems": [{"code": "STUDY_CANCELLED", "message": "cancelled"}]}
            return record
        time.sleep(0.02)
    if fake.get("fail"):
        raise RuntimeError("the fake evaluation failed")
    value = 2.0 * float(fake.get("x", 0.0))
    Path(sample_dir, "output.txt").write_text(f"{value!r}\n", encoding="utf-8")
    record = evaluators.empty_record(None, request["selected_targets"])
    outcome = fake.get("outcome", "PASS")
    record.update({
        "outcome": outcome, "status": outcome, "in_model_scope": True,
        "targets": [{"id": "t", "metric": "fake", "value": value, "unit": "mm", "status": outcome,
                     "bounds": {"maximum": 10.0}, "assessment": "MET" if outcome == "PASS" else "UNMET",
                     "quantity_state": "EXACT", "applicability_issue_codes": []}],
        "identity": {"engine_version": None, "engine_build_id": None, "catalog_version": None,
                     "project_hash": wc.canonical_digest(request["project"]), "input_hash": None,
                     "result_hash": hashlib.sha256(repr(value).encode()).hexdigest()},
        "seconds": float(fake.get("seconds") or 0.001)})
    return record


def other_evaluator(request, sample_dir, limits=None):
    """A second evaluator: the fake one with ten times the target value."""
    record = fake_evaluator(request, sample_dir, limits)
    for target in record["targets"]:
        target["value"] *= 10.0
    return record


def interrupting_evaluator(request, sample_dir, limits=None):
    """Ctrl-C (KeyboardInterrupt) while evaluating the sample labelled ``fake.interrupt``, once per study (a marker
    file in the study directory remembers it)."""
    marker = Path(sample_dir).parent.parent / "interrupted-once"
    if (request["labels"].get("fake") or {}).get("interrupt") and not marker.exists():
        marker.write_text("1", encoding="utf-8")
        raise KeyboardInterrupt
    return fake_evaluator(request, sample_dir, limits)


def noisy_evaluator(request, sample_dir, limits=None):
    """The fake evaluator, printing a line to stdout first."""
    print(f"evaluating {request['sample_id']}")
    return fake_evaluator(request, sample_dir, limits)


def unruly_evaluator(request, sample_dir, limits=None):
    """``fake.stubborn``: sleeps that many seconds without looking at cancellation or limits; ``fake.die``: the
    process ends (exit code 3) before returning; otherwise the fake evaluator."""
    import os

    fake = request["labels"].get("fake") or {}
    if fake.get("die"):
        os._exit(3)
    if fake.get("stubborn"):
        time.sleep(float(fake["stubborn"]))
    return fake_evaluator(request, sample_dir, limits)


def header(kind="test.study", **keys):
    """A study header with the identity of this environment (empty without an Engine)."""
    if HAS_ENGINE:
        return {"kind": kind, **keys, "engine": wc.engine_identity(), "installed_catalog": wc.catalog_identity()}
    return {"kind": kind, **keys, "engine": {}, "installed_catalog": {}}


def fake_requests(values, *, fail=(), outcomes=None, sleep=0.0, refuse=(), unit="mm", extra=None):
    """Fake requests ``s0001``...; ``extra``: sample id -> more ``fake`` label keys (interrupt, stubborn, die,
    seconds)."""
    out = []
    for index, value in enumerate(values, start=1):
        sample_id = f"s{index:04d}"
        if sample_id in refuse:
            out.append(rq.make_request(sample_id, None, refusal={"stage": "edit", "code": "EDIT_REFUSED",
                                                                  "problems": [{"code": "EDIT_REFUSED"}]},
                                       labels={"parameters": {"x": {"value": value, "unit": unit}}}))
            continue
        fake = {"x": value, "fail": sample_id in fail, "sleep": sleep,
                "outcome": (outcomes or {}).get(sample_id, "PASS"), **(extra or {}).get(sample_id, {})}
        out.append(rq.make_request(sample_id, {"x": value}, selected_targets=["t"],
                                   labels={"role": "sample", "fake": fake,
                                           "parameters": {"x": {"value": value, "unit": unit}}}))
    return out


def basis(study_dir):
    """The serial/parallel comparison basis: per sample in plan order the outcome, state, target values, hashes."""
    return [(entry["sample_id"], record["outcome"], record["state"],
             [(t["id"], t["value"]) for t in record.get("targets") or []],
             {k: record["identity"].get(k) for k in ("project_hash", "input_hash", "result_hash")})
            for entry, record in aggregate.collect_records(study_dir)]


def events(study_dir, name):
    return [e["sample_id"] for e in store.read_journal(study_dir) if e["event"] == name]


class ReverseScheduler:
    """A replaceable scheduler: runs the jobs in this process in reverse order (proves output stays in plan order)."""

    name = "reverse"
    in_process = True
    workers = 1

    def __init__(self):
        self.cancelled = False
        self.seen = []

    def run(self, jobs, worker, *, on_result):
        for job in reversed(list(jobs)):
            self.seen.append(job["sample_id"])
            on_result(job, worker(job), None)

    def cancel(self):
        self.cancelled = True


class RequestTests(unittest.TestCase):
    def test_request_hash_is_stable_and_covers_the_inputs(self):
        a = rq.make_request("s0001", {"b": 1, "a": [1, 2]}, selected_targets=["t"])
        b = rq.make_request("s0001", {"a": [1, 2], "b": 1}, selected_targets=["t"], labels={"note": "labels"})
        self.assertEqual(a["request_hash"], b["request_hash"])  # key order and labels do not change the hash
        for changed in (rq.make_request("s0001", {"b": 2, "a": [1, 2]}, selected_targets=["t"]),
                        rq.make_request("s0001", {"b": 1, "a": [1, 2]}, selected_targets=None),
                        rq.make_request("s0001", {"b": 1, "a": [1, 2]}, selected_targets=["t"],
                                        outputs={"ray_data": "summary"}),
                        rq.make_request("s0001", {"b": 1, "a": [1, 2]}, selected_targets=["t"], seed=7)):
            self.assertNotEqual(changed["request_hash"], a["request_hash"])
        self.assertEqual(rq.make_request("s0009", {"b": 1, "a": [1, 2]}, selected_targets=["t"])["request_hash"],
                         a["request_hash"])  # the sample id is not part of the hash: invalidation follows content

    def test_validation_refuses_tampering_and_reserved_labels(self):
        request = rq.make_request("s0001", {"a": 1})
        tampered = {**request, "project": {"a": 2}}
        with self.assertRaises(wc.WorkflowError) as caught:
            rq.validate_request(tampered)
        self.assertEqual(caught.exception.code, "REQUEST_INVALID")
        for bad in ({"labels": {"targets": []}}, {"outputs": {"ray_data": "most"}}):
            with self.assertRaises(wc.WorkflowError):
                rq.make_request("s0001", {"a": 1}, **bad)
        with self.assertRaises(wc.WorkflowError):
            rq.make_request("S 1", {"a": 1})

    def test_sample_ids_and_seeds_do_not_depend_on_order(self):
        self.assertEqual(rq.sample_ids(3), ["s0001", "s0002", "s0003"])
        self.assertEqual(rq.sample_ids(2, start=9999), ["s09999", "s10000"])  # one width for the whole plan
        seeds = {sid: rq.sample_seed(20261018, sid) for sid in rq.sample_ids(5)}
        again = {sid: rq.sample_seed(20261018, sid) for sid in reversed(rq.sample_ids(5))}
        self.assertEqual(seeds, again)
        self.assertEqual(len(set(seeds.values())), 5)
        self.assertNotEqual(rq.sample_seed(1, "s0001"), rq.sample_seed(2, "s0001"))


class ResourceTests(unittest.TestCase):
    def test_budget_keeps_threads_within_the_cpus(self):
        budget = resources.ResourceBudget.resolve(2, cpus=4)
        self.assertEqual((budget.workers, budget.blas_threads), (2, 1))
        self.assertEqual(budget.thread_environment()["OPENBLAS_NUM_THREADS"], "1")
        self.assertIsNone(resources.ResourceBudget.resolve(1, cpus=4).blas_threads)
        with self.assertRaises(wc.WorkflowError) as caught:
            resources.ResourceBudget.resolve(3, blas_threads=2, cpus=4)
        self.assertEqual(caught.exception.code, "BUDGET_INVALID")

    def test_suggest_workers_stays_serial_below_the_break_even(self):
        workers, why = resources.suggest_workers(20, 0.2, 4.0, 150, cpus=12, available_mib=6000)
        self.assertEqual(workers, 1)
        self.assertEqual(why["expected_s"], why["expected_serial_s"])
        workers, why = resources.suggest_workers(400, 0.2, 4.0, 150, cpus=12, available_mib=6000)
        self.assertGreater(workers, 1)
        self.assertLess(why["expected_s"], why["expected_serial_s"])
        workers, _why = resources.suggest_workers(400, 2.0, 4.0, 1500, cpus=12, available_mib=3000)
        self.assertEqual(workers, 1)  # memory allows one worker only


class StoreTests(unittest.TestCase):
    def test_plan_round_trip_keeps_order_and_requests(self):
        with TempDir() as tmp:
            requests = fake_requests([3.0, 1.0, 2.0], refuse=("s0002",))
            study, entries = store.plan_study(tmp / "study", header(parameters=[{"name": "x", "unit": "mm"}]),
                                              requests)
            self.assertEqual([e["sample_id"] for e in store.read_plan(tmp / "study")], ["s0001", "s0002", "s0003"])
            self.assertEqual(entries, store.read_plan(tmp / "study"))
            for request in requests:
                self.assertEqual(store.read_request(tmp / "study", request["sample_id"]), request)
            saved = store.read_study(tmp / "study")
            self.assertEqual((saved["schema"], saved["schema_version"]), ("os-optics-kit.study", 1))
            self.assertEqual(saved["identity"]["plan_hash"], store.plan_hash(entries))
            self.assertEqual(saved["execution"]["state"], "PLANNED")
            self.assertEqual(store.read_journal(tmp / "study")[0]["event"], "planned")
            with self.assertRaises(wc.WorkflowError) as caught:
                store.write_plan(tmp / "other", requests + requests[:1])
            self.assertEqual(caught.exception.code, "PLAN_INVALID")


class ExecutionTests(unittest.TestCase):
    def plan(self, root, requests, **keys):
        store.plan_study(root, header(parameters=[{"name": "x", "unit": "mm"}], **keys), requests)
        return root

    def test_failure_and_refusal_are_explicit_and_the_rest_completes(self):
        with TempDir() as tmp:
            study_dir = self.plan(tmp / "study", fake_requests([1.0, 2.0, 3.0, 4.0], fail=("s0002",),
                                                               refuse=("s0004",)))
            summary = execute.execute_study(study_dir, evaluator=FAKE)
            self.assertEqual(summary.state, "COMPLETED")
            self.assertEqual(summary.states, {"COMPLETED": 2, "FAILED": 1, "REFUSED": 1})
            records = dict((e["sample_id"], r) for e, r in aggregate.collect_records(study_dir))
            self.assertEqual(records["s0002"]["error"]["code"], "WORKER_EXCEPTION")
            self.assertEqual(records["s0004"]["error"]["code"], "EDIT_REFUSED")
            self.assertEqual(records["s0003"]["targets"][0]["value"], 6.0)
            record = records["s0001"]
            self.assertEqual((record["sample_dir"], record["role"], record["state"], record["attempts"]),
                             ("samples/s0001", "sample", "COMPLETED", 1))
            self.assertEqual(record["parameters"], {"x": {"value": 1.0, "unit": "mm"}})
            for key in ("request_hash", "timing", "peak_memory_bytes", "worker", "identity", "targets", "outcome"):
                self.assertIn(key, record)
            self.assertEqual(store.read_study(study_dir)["execution"]["state"], "COMPLETED")
            summary = aggregate.aggregate_generic(study_dir)  # aggregation alone: no evaluator, no Engine
            self.assertEqual([r["sample_id"] for r in summary["rows"]], ["s0001", "s0002", "s0003", "s0004"])
            self.assertEqual(summary["counts"]["by_outcome"]["ERROR"], 2)
            self.assertTrue((study_dir / "table.csv").is_file())
            with self.assertRaises(wc.WorkflowError) as caught:  # a second plain execution would discard results
                execute.execute_study(study_dir, evaluator=FAKE)
            self.assertEqual(caught.exception.code, "STUDY_STARTED")
            execute.execute_study(study_dir, evaluator=FAKE, resume=True)
            self.assertEqual(events(study_dir, "started"), ["s0001", "s0002", "s0003", "s0002"])  # failed: again

    def test_cancelled_study_resumes_and_runs_only_what_is_missing(self):
        with TempDir() as tmp:
            study_dir = self.plan(tmp / "study", fake_requests([1.0, 2.0, 3.0, 4.0, 5.0]))
            scheduler = schedulers.SerialScheduler()

            def stop_after_two(event):
                if event["done"] == 2:
                    scheduler.cancel()

            summary = execute.execute_study(study_dir, evaluator=FAKE, scheduler=scheduler,
                                            on_progress=stop_after_two)
            self.assertEqual(summary.state, "CANCELLED")
            self.assertEqual(summary.states, {"COMPLETED": 2, "NOT_RUN": 3})
            self.assertEqual(store.read_study(study_dir)["execution"]["state"], "CANCELLED")
            self.assertEqual(store.read_record(study_dir, "s0004")["error"]["code"], "CANCELLED")
            summary = execute.execute_study(study_dir, evaluator=FAKE, resume=True)
            self.assertEqual(summary.state, "COMPLETED")
            self.assertEqual((summary.reused, summary.ran), (2, 3))
            self.assertEqual(events(study_dir, "reused"), ["s0001", "s0002"])
            self.assertEqual(events(study_dir, "started"), ["s0001", "s0002", "s0003", "s0004", "s0005"])
            fresh = self.plan(tmp / "fresh", fake_requests([1.0, 2.0, 3.0, 4.0, 5.0]))
            execute.execute_study(fresh, evaluator=FAKE)
            self.assertEqual(basis(study_dir), basis(fresh))

    def test_changed_input_invalidates_exactly_the_changed_samples(self):
        with TempDir() as tmp:
            study_dir = self.plan(tmp / "study", fake_requests([1.0, 2.0, 3.0]))
            execute.execute_study(study_dir, evaluator=FAKE)
            changed = fake_requests([1.0, 2.5, 3.0])
            store.plan_study(study_dir, header(parameters=[{"name": "x", "unit": "mm"}]), changed, resume=True)
            summary = execute.execute_study(study_dir, evaluator=FAKE, resume=True)
            self.assertEqual((summary.reused, summary.ran), (2, 1))
            self.assertEqual(events(study_dir, "started")[3:], ["s0002"])
            self.assertEqual(store.read_record(study_dir, "s0002")["targets"][0]["value"], 5.0)

    def test_changed_identity_refuses_resume_and_names_the_field(self):
        with TempDir() as tmp:
            study_dir = self.plan(tmp / "study", fake_requests([1.0, 2.0]))
            execute.execute_study(study_dir, evaluator=FAKE)
            study = store.read_study(study_dir)
            study["identity"]["engine"]["build_id"] = "0.0.0+another-build"
            study["identity"]["catalog"]["version"] = "1999-01-01.1"
            store.write_study(study_dir, study)
            with self.assertRaises(wc.WorkflowError) as caught:
                execute.execute_study(study_dir, evaluator=FAKE, resume=True)
            self.assertEqual(caught.exception.code, "STUDY_IDENTITY_CHANGED")
            self.assertEqual(caught.exception.details["changed"], ["engine.build_id", "catalog.version"])
            plan_path = study_dir / store.PLAN_FILE  # an edited plan no longer matches the planned identity
            store.write_study(study_dir, {**study, "identity": execute.current_identity(store.read_plan(study_dir))})
            plan_path.write_text(plan_path.read_text(encoding="utf-8").replace("s0002", "s0003"), encoding="utf-8")
            with self.assertRaises(wc.WorkflowError) as caught:
                execute.execute_study(study_dir, evaluator=FAKE, resume=True)
            self.assertEqual(caught.exception.details["changed"], ["plan_hash"])

    def test_scheduler_is_replaceable_and_output_keeps_plan_order(self):
        with TempDir() as tmp:
            serial = self.plan(tmp / "serial", fake_requests([1.0, 2.0, 3.0]))
            execute.execute_study(serial, evaluator=FAKE)
            reverse = self.plan(tmp / "reverse", fake_requests([1.0, 2.0, 3.0]))
            scheduler = ReverseScheduler()
            summary = execute.execute_study(reverse, evaluator=fake_evaluator, scheduler=scheduler)
            self.assertEqual(scheduler.seen, ["s0003", "s0002", "s0001"])
            self.assertEqual(summary.scheduler, "reverse")
            self.assertEqual(basis(serial), basis(reverse))
            self.assertEqual(aggregate.aggregate_generic(serial)["rows"][0]["sample_id"], "s0001")

    def test_stop_on_acts_in_plan_order_whatever_ran_first(self):
        with TempDir() as tmp:
            outcomes = {"s0002": "FAIL", "s0004": "FAIL"}
            serial = self.plan(tmp / "serial", fake_requests([1.0, 2.0, 3.0, 4.0], outcomes=outcomes))
            execute.execute_study(serial, evaluator=FAKE, stop_on=["FAIL"])
            reverse = self.plan(tmp / "reverse", fake_requests([1.0, 2.0, 3.0, 4.0], outcomes=outcomes))
            summary = execute.execute_study(reverse, evaluator=FAKE, scheduler=ReverseScheduler(), stop_on=["FAIL"])
            self.assertEqual(summary.stopped_by, "s0002")
            self.assertEqual(basis(serial), basis(reverse))
            stopped = store.read_record(reverse, "s0004")
            self.assertEqual((stopped["state"], stopped["error"]["code"]), ("NOT_RUN", "STOPPED"))
            self.assertFalse((reverse / "samples" / "s0004" / "output.txt").exists())  # its files were removed
            self.assertEqual(events(serial, "started"), ["s0001", "s0002"])  # serial never ran past the stop


class ProcessSchedulerTests(unittest.TestCase):
    """Two spawn workers (each imports the Engine once when it is installed: a few seconds)."""

    def test_two_workers_equal_serial_with_isolated_outputs(self):
        with TempDir() as tmp:
            values = [1.0, 2.0, 3.0, 4.0]
            serial = tmp / "serial"
            store.plan_study(serial, header(), fake_requests(values, fail=("s0003",), sleep=0.2))
            execute.execute_study(serial, evaluator=FAKE)
            parallel = tmp / "parallel"
            store.plan_study(parallel, header(), fake_requests(values, fail=("s0003",), sleep=0.2))
            summary = execute.execute_study(parallel, evaluator=FAKE, workers=2)
            self.assertEqual((summary.scheduler, summary.workers, summary.blas_threads), ("process", 2, 1))
            self.assertEqual(len(summary.startup), 2)
            self.assertEqual(basis(serial), basis(parallel))
            records = [r for _e, r in aggregate.collect_records(parallel)]
            self.assertEqual(len({r["sample_dir"] for r in records}), 4)
            self.assertTrue(all(r["worker"].startswith("pid-") for r in records if r["state"] == "COMPLETED"))
            self.assertEqual(records[2]["state"], "FAILED")
            for sample in ("s0001", "s0002", "s0004"):  # each worker wrote only its own sample directory
                self.assertEqual(sorted(p.name for p in (parallel / "samples" / sample).iterdir()),
                                 ["output.txt", "sample.json"])
            self.assertEqual(store.read_study(parallel)["execution"]["blas_threads"], 1)

    def test_cancel_through_the_api_leaves_a_resumable_study(self):
        with TempDir() as tmp:
            study_dir = tmp / "study"
            store.plan_study(study_dir, header(), fake_requests([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], sleep=1.0))
            scheduler = schedulers.ProcessScheduler(2)
            try:
                summary = execute.execute_study(study_dir, evaluator=FAKE, scheduler=scheduler,
                                                on_progress=lambda event: scheduler.cancel())
            finally:
                scheduler.close()
            self.assertEqual(summary.state, "CANCELLED")
            states = summary.states
            self.assertGreaterEqual(states.get("COMPLETED", 0), 1)
            self.assertGreaterEqual(states.get("NOT_RUN", 0) + states.get("CANCELLED", 0), 3)
            summary = execute.execute_study(study_dir, evaluator=FAKE, resume=True)
            self.assertEqual(summary.states, {"COMPLETED": 6})
            self.assertEqual(summary.reused + summary.ran, 6)


@requires_engine
class DefaultEvaluatorTests(unittest.TestCase):
    """``evaluators.run_project``: limits stop at Engine checkpoints; an interrupted sample is read back."""

    def test_limits_stop_the_run_and_the_record_says_why(self):
        with TempDir() as tmp:
            document = expander()
            requests = [rq.make_request("s0001", document, selected_targets=["spot_radius"]),
                        rq.make_request("s0002", expander(f2=160.0), selected_targets=["spot_radius"])]
            store.plan_study(tmp / "time", header(), requests[:1])
            summary = execute.execute_study(tmp / "time", limits={"time_limit_s": 1e-6})
            record = store.read_record(tmp / "time", "s0001")
            self.assertEqual((summary.state, record["state"]), ("COMPLETED", "CANCELLED"))
            self.assertEqual(record["error"]["code"], "RUN_STOPPED")
            self.assertEqual(record["error"]["problems"][0]["code"], "TIME_LIMIT")
            store.plan_study(tmp / "memory", header(), requests[1:])
            execute.execute_study(tmp / "memory", limits={"max_memory_mib": 1})
            record = store.read_record(tmp / "memory", "s0002")
            self.assertEqual(record["error"]["problems"][0]["code"], "MEMORY_LIMIT")
            summary = execute.execute_study(tmp / "memory", resume=True)  # CANCELLED runs again on resume
            record = store.read_record(tmp / "memory", "s0002")
            self.assertEqual((record["state"], record["attempts"]), ("COMPLETED", 2))
            self.assertEqual(record["run_dir"], "samples/s0002/run")
            self.assertGreater(len(record["timing"]["phase_seconds"]), 3)

    def test_complete_run_without_record_is_read_back_not_rerun(self):
        with TempDir() as tmp:
            study_dir = tmp / "study"
            request = rq.make_request("s0001", expander(), selected_targets=["spot_radius"])
            store.plan_study(study_dir, header(), [request])
            execute.execute_study(study_dir)
            first = store.read_record(study_dir, "s0001")
            store.sample_path(study_dir, "s0001").unlink()  # interrupted after the Engine wrote the run
            store.write_attempt(study_dir, "s0001", request["request_hash"], 1,
                                evaluator=evaluators.DEFAULT_EVALUATOR)  # the marker names its evaluator
            summary = execute.execute_study(study_dir, resume=True)
            self.assertEqual((summary.recovered, summary.ran), (1, 0))
            self.assertEqual(events(study_dir, "recovered"), ["s0001"])
            again = store.read_record(study_dir, "s0001")
            self.assertEqual(again["identity"]["result_hash"], first["identity"]["result_hash"])
            self.assertEqual(again["outcome"], first["outcome"])

    def test_ctrl_c_inside_an_engine_run_cancels_the_study_and_exits_130(self):
        import _thread

        import os_optics.local.run as engine_run

        with TempDir() as tmp:
            study_dir = tmp / "study"
            requests = [rq.make_request(f"s000{index}", expander(f2=f2), selected_targets=["spot_radius"])
                        for index, f2 in ((1, 150.0), (2, 155.0), (3, 160.0))]
            store.plan_study(study_dir, header(), requests)
            real, calls = engine_run._run_optical_project, []

            def interrupting(*args, **kwargs):  # Ctrl-C arrives while the Engine runs the first sample
                calls.append(1)
                if len(calls) == 1:
                    _thread.interrupt_main()
                return real(*args, **kwargs)

            with mock.patch.object(engine_run, "_run_optical_project", interrupting):
                code, out, err = invoke(study_tool.main, ["run", str(study_dir)])
            self.assertEqual((code, out["status"]), (130, "CANCELLED"), err)
            self.assertEqual(len(calls), 1)  # no other sample started
            self.assertEqual(out["execution"]["states"], {"CANCELLED": 1, "NOT_RUN": 2})
            record = store.read_record(study_dir, "s0001")
            self.assertEqual((record["state"], record["error"]["code"], record["error"]["problems"][0]["code"]),
                             ("CANCELLED", "RUN_STOPPED", "INTERRUPTED"))
            self.assertEqual(store.read_study(study_dir)["execution"]["state"], "CANCELLED")
            self.assertEqual(store.read_journal(study_dir)[-1]["event"], "execution_cancelled")
            code, out, err = invoke(study_tool.main, out["next"].split()[2:])
            self.assertEqual((code, out["execution"]["ran"], out["execution"]["state"]), (0, 3, "COMPLETED"), err)

    def test_the_parent_obtains_pinned_catalog_releases_before_workers_start(self):
        """Workers run with automatic catalog access off; the calling process lets the Engine fetch a missing
        pinned release once per set of pins (the fetch is replaced here: nothing leaves the machine)."""
        from os_optics.catalog import service

        project = wc.read_json(REPO / "cases" / "beam-expander" / "project.json")
        saved = {name: os.environ.get(name) for name in ("OPENSCI_CATALOG_DIR", "OPENSCI_CATALOG_URL",
                                                         "OPENSCI_CATALOG_CHECK_INTERVAL")}
        fetched = []

        def fetch_release(catalog_version, *, store, environ, timeout, automatic=False):
            fetched.append((catalog_version, os.getpid()))
            return store.read_state()

        with TempDir() as tmp:
            try:
                (tmp / "catalog").mkdir()
                os.environ["OPENSCI_CATALOG_DIR"] = str(tmp / "catalog")
                os.environ["OPENSCI_CATALOG_URL"] = "http://127.0.0.1:9/api/v1/catalog"
                os.environ.pop("OPENSCI_CATALOG_CHECK_INTERVAL", None)
                requests = [rq.make_request(sample_id, project, labels={"fake": {"x": 1.0}})
                            for sample_id in ("s0001", "s0002")]
                store.plan_study(tmp / "study", header(), requests)
                with mock.patch.object(service, "fetch_release", fetch_release):
                    summary = execute.execute_study(tmp / "study", evaluator=FAKE, scheduler=FakeProcessScheduler())
            finally:
                for name, value in saved.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value
        self.assertEqual(summary.states, {"COMPLETED": 2})
        self.assertTrue(fetched)  # the fake evaluator never calls the Engine: these are the parent's own requests
        self.assertEqual(set(fetched), {("2026-10-28.1", os.getpid())})

    def test_a_first_project_refused_before_the_catalog_does_not_skip_its_pins(self):
        """The first Project of a set of pins is refused when it is loaded (before the catalog binding): the next
        Project with the same pins obtains the release, once (the fetch is replaced: it stores nothing, so every
        later check would try again)."""
        from os_optics.catalog import service

        project = wc.read_json(REPO / "cases" / "beam-expander" / "project.json")
        refused = json.loads(json.dumps(project))
        refused["components"][0]["source_model"]["waist_radius_mm"] = -1.0
        saved = {name: os.environ.get(name) for name in ("OPENSCI_CATALOG_DIR", "OPENSCI_CATALOG_URL",
                                                         "OPENSCI_CATALOG_CHECK_INTERVAL")}
        fetched = []

        def fetch_release(catalog_version, *, store, environ, timeout, automatic=False):
            fetched.append((catalog_version, os.getpid()))
            return store.read_state()

        with TempDir() as tmp:
            try:
                (tmp / "catalog").mkdir()
                os.environ["OPENSCI_CATALOG_DIR"] = str(tmp / "catalog")
                os.environ["OPENSCI_CATALOG_URL"] = "http://127.0.0.1:9/api/v1/catalog"
                os.environ.pop("OPENSCI_CATALOG_CHECK_INTERVAL", None)
                requests = [rq.make_request(sample_id, document, labels={"fake": {"x": 1.0}})
                            for sample_id, document in (("s0001", refused), ("s0002", project), ("s0003", project))]
                with mock.patch.object(service, "fetch_release", fetch_release):
                    self.assertFalse(wc.check(refused)["ok"])
                    self.assertEqual(fetched, [])  # refused before the catalog binding
                    pins = execute.resolve_catalog(requests)
            finally:
                for name, value in saved.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value
        self.assertEqual(pins, [("2026-10-28.1",)])
        self.assertEqual(fetched, [("2026-10-28.1", os.getpid())])


class FakeProcessScheduler:
    """Runs the jobs in this process but presents itself as a worker pool (``in_process`` False); ``report(job)``
    gives the error to report instead of the result, ``run_worker`` False reports without running the job."""

    name = "fake-process"
    in_process = False
    workers = 1

    def __init__(self, report=None, run_worker=True):
        self.cancelled = False
        self.report, self.run_worker = report, run_worker

    def run(self, jobs, worker, *, on_result):
        for job in jobs:
            result = worker(job) if self.run_worker else None
            error = self.report(job) if self.report else None
            on_result(job, None if error else result, error)

    def cancel(self):
        self.cancelled = True


class SafetyTests(unittest.TestCase):
    """What a study directory and its plan may contain, and who may execute it."""

    def plan(self, root, requests):
        store.plan_study(root, header(parameters=[{"name": "x", "unit": "mm"}]), requests)
        return root

    def write_plan(self, study_dir, entries):
        (study_dir / store.PLAN_FILE).write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
        study = store.read_study(study_dir)
        study["identity"]["plan_hash"] = store.plan_hash(entries)
        store.write_study(study_dir, study)

    def test_crafted_plan_ids_are_refused_before_any_path_is_used(self):
        with TempDir() as tmp:
            study_dir = self.plan(tmp / "study", fake_requests([1.0]))
            (tmp / "victim").mkdir()
            (tmp / "victim" / "keep.txt").write_text("keep", encoding="utf-8")
            (tmp / "victim.json").write_text("{}", encoding="utf-8")
            entry = store.read_plan(study_dir)[0]
            for crafted in ("../../victim", "../..", "..", "s0001/../../victim", "C:/victim", "nul", "s0001\n"):
                self.write_plan(study_dir, [{**entry, "sample_id": crafted}])
                with self.assertRaises(wc.WorkflowError) as caught:
                    execute.execute_study(study_dir, evaluator=FAKE)
                self.assertEqual(caught.exception.code, "PLAN_INVALID", crafted)
            self.write_plan(study_dir, [entry, entry])
            with self.assertRaises(wc.WorkflowError) as caught:
                execute.execute_study(study_dir, evaluator=FAKE)
            self.assertEqual(caught.exception.code, "PLAN_INVALID")
            self.assertEqual((tmp / "victim" / "keep.txt").read_text(encoding="utf-8"), "keep")
            self.assertEqual(sorted(p.name for p in tmp.iterdir()), ["study", "victim", "victim.json"])
            self.assertFalse((study_dir / store.OWNER_FILE).exists())
            self.write_plan(study_dir, [entry])  # a request file holding another sample's request
            store.request_path(study_dir, "s0001").write_text(wc.json_text(fake_requests([1.0, 2.0])[1]),
                                                               encoding="utf-8")
            with self.assertRaises(wc.WorkflowError) as caught:
                execute.execute_study(study_dir, evaluator=FAKE)
            self.assertEqual(caught.exception.code, "REQUEST_INVALID")
            self.assertEqual(store.read_study(study_dir)["execution"]["state"], "FAILED")  # never left RUNNING

    def test_one_execution_owns_the_study_and_a_gone_owner_is_taken_over(self):
        with TempDir() as tmp:
            study_dir = self.plan(tmp / "study", fake_requests([1.0, 2.0]))
            refused, seen = [], {}

            def evaluator(request, sample_dir, limits=None):
                if request["sample_id"] == "s0001":
                    for attempt in (lambda: execute.execute_study(study_dir, evaluator=FAKE, resume=True),
                                    lambda: store.plan_study(study_dir, header(), fake_requests([1.0]), resume=True),
                                    lambda: store.prepare_study_dir(study_dir, overwrite=True)):
                        with self.assertRaises(wc.WorkflowError) as caught:
                            attempt()
                        refused.append(caught.exception.code)
                    seen["status"] = study_tool.status(study_dir)
                return fake_evaluator(request, sample_dir, limits)

            summary = execute.execute_study(study_dir, evaluator=evaluator)
            self.assertEqual(summary.states, {"COMPLETED": 2})
            self.assertEqual(refused, ["STUDY_RUNNING"] * 3)
            self.assertEqual(seen["status"]["execution"]["state"], "RUNNING")
            self.assertEqual(seen["status"]["owner"]["pid"], os.getpid())
            self.assertFalse((study_dir / store.OWNER_FILE).exists())
            self.assertEqual([e["event"] for e in store.read_journal(study_dir)].count("execution_started"), 1)

            done = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True,
                                  text=True, timeout=60)  # a process that has ended
            wc.write_json(study_dir / store.OWNER_FILE, {"pid": int(done.stdout), "host": socket.gethostname(),
                                                         "process_started": None, "started": "x", "id": "gone"})
            study = store.read_study(study_dir)
            study["execution"]["state"] = "RUNNING"  # as left by a killed execution
            store.write_study(study_dir, study)
            status = study_tool.status(study_dir)
            self.assertEqual((status["execution"]["state"], status["owner"]), ("INTERRUPTED", None))
            summary = execute.execute_study(study_dir, evaluator=FAKE, resume=True)
            self.assertEqual((summary.state, summary.reused, summary.ran), ("COMPLETED", 0, 2))  # another evaluator
            self.assertFalse((study_dir / store.OWNER_FILE).exists())
            with self.assertRaises(wc.WorkflowError):
                execute.execute_study(study_dir, evaluator=FAKE, resume=True, stop_on=["SOMETIMES"])
            self.assertFalse((study_dir / store.OWNER_FILE).exists())  # released after a refusal too

    def test_identity_refusal_names_a_recovery_that_exists(self):
        with TempDir() as tmp:
            study_dir = self.plan(tmp / "study", fake_requests([1.0, 2.0]))
            study = store.read_study(study_dir)
            study["identity"]["engine"]["build_id"] = "0.0.0+another-build"
            store.write_study(study_dir, study)
            code, _out, err = invoke(study_tool.main, ["run", str(study_dir), "--evaluator", FAKE])
            error = json.loads(err)["error"]
            self.assertEqual((code, error["code"]), (4, "STUDY_IDENTITY_CHANGED"))
            self.assertIn("cannot be run as planned", error["message"])  # a first run, not a resume
            self.assertIn("planning command and --overwrite", error["message"])
            self.assertNotIn("resumed", error["message"])
            with self.assertRaises(wc.WorkflowError) as caught:
                execute.execute_study(study_dir, evaluator=FAKE, resume=True)
            self.assertIn("cannot be resumed", str(caught.exception))


class EvaluatorIdentityTests(unittest.TestCase):
    def plan(self, root, requests, parameters=None):
        store.plan_study(root, header(parameters=parameters or [{"name": "x", "unit": "mm"}]), requests,
                         resume=store.has_study(root))
        return root

    def test_resume_reuses_only_records_of_the_same_evaluator(self):
        with TempDir() as tmp:
            study_dir = self.plan(tmp / "study", fake_requests([1.0, 2.0, 3.0], fail=("s0003",)))
            execute.execute_study(study_dir, evaluator=FAKE)
            self.assertEqual(store.read_record(study_dir, "s0001")["evaluator"], FAKE)
            summary = execute.execute_study(study_dir, evaluator=OTHER, resume=True)
            self.assertEqual((summary.reused, summary.ran), (0, 3))  # nothing evaluated by FAKE is kept
            self.assertEqual(store.read_record(study_dir, "s0001")["targets"][0]["value"], 20.0)
            summary = execute.execute_study(study_dir, evaluator=OTHER, resume=True)
            self.assertEqual((summary.reused, summary.ran), (2, 1))
            self.assertEqual(store.read_study(study_dir)["execution"]["evaluator"], OTHER)
            self.assertEqual(study_tool.status(study_dir)["to_run_on_resume"]["total"], 1)
            cancelled = options.cancelled_summary(study_dir, "test", summary)
            self.assertEqual(cancelled["next"], f"os-optics-kit study run {Path(study_dir).as_posix()} --resume "
                                                f"--evaluator {OTHER}")
            summary.evaluator = evaluators.DEFAULT_EVALUATOR
            self.assertTrue(options.cancelled_summary(study_dir, "test", summary)["next"].endswith("--resume"))

    def test_a_kept_record_takes_the_labels_of_the_new_plan(self):
        def requests(name, unit, values):
            return [rq.make_request(sample_id, {"x": x}, selected_targets=["t"],
                                    labels={"role": "sample", "fake": {"x": x},
                                            "parameters": {name: {"value": value, "unit": unit}}})
                    for sample_id, x, value in zip(("s0001", "s0002"), (1.0, 2.0), values)]

        with TempDir() as tmp:
            study_dir = self.plan(tmp / "study", requests("x", "m", [0.2, 0.3]), [{"name": "x", "unit": "m"}])
            execute.execute_study(study_dir, evaluator=FAKE)
            self.plan(study_dir, requests("x", "mm", [200.0, 300.0]))  # same Projects, labels in another unit
            summary = execute.execute_study(study_dir, evaluator=FAKE, resume=True)
            self.assertEqual((summary.reused, summary.ran), (2, 0))
            self.assertEqual(store.read_record(study_dir, "s0001")["parameters"],
                             {"x": {"value": 200.0, "unit": "mm"}})
            rows = aggregate.aggregate_generic(study_dir)["rows"]
            self.assertEqual([(r["param:x"], r["param:x:unit"]) for r in rows], [(200.0, "mm"), (300.0, "mm")])
            self.plan(study_dir, requests("lens_x", "mm", [200.0, 300.0]), [{"name": "lens_x", "unit": "mm"}])
            rows = aggregate.aggregate_generic(study_dir)["rows"]  # aggregation of the new plan, no execution
            self.assertEqual([r["param:lens_x"] for r in rows], [200.0, 300.0])
            self.assertNotIn("x", store.read_record(study_dir, "s0001")["parameters"])


class CancellationTests(unittest.TestCase):
    def plan(self, root, requests):
        store.plan_study(root, header(parameters=[{"name": "x", "unit": "mm"}]), requests)
        return root

    def test_ctrl_c_in_a_serial_study_exits_130_and_the_printed_command_resumes_it(self):
        with TempDir() as tmp:
            study_dir = self.plan(tmp / "study", fake_requests([1.0, 2.0, 3.0], extra={"s0002": {"interrupt": True}}))
            code, out, err = invoke(study_tool.main, ["run", str(study_dir), "--evaluator", INTERRUPTING])
            self.assertEqual((code, out["status"]), (130, "CANCELLED"), err)
            self.assertEqual(out["execution"]["states"], {"COMPLETED": 1, "CANCELLED": 1, "NOT_RUN": 1})
            self.assertTrue(out["next"].endswith(f"--resume --evaluator {INTERRUPTING}"))
            record = store.read_record(study_dir, "s0002")
            self.assertEqual((record["state"], record["error"]["code"], record["error"]["problems"][0]["code"]),
                             ("CANCELLED", "RUN_STOPPED", "INTERRUPTED"))
            self.assertEqual(store.read_record(study_dir, "s0003")["state"], "NOT_RUN")
            self.assertEqual(events(study_dir, "started"), ["s0001", "s0002"])  # s0003 never started
            self.assertEqual(store.read_study(study_dir)["execution"]["state"], "CANCELLED")
            code, out, err = invoke(study_tool.main, out["next"].split()[2:])
            self.assertEqual((code, out["execution"]["reused"], out["execution"]["ran"]), (0, 1, 2), err)

    def test_a_cancelled_serial_run_leaves_no_cancellation_behind(self):
        with TempDir() as tmp:
            scheduler = schedulers.SerialScheduler()
            scheduler.cancel()
            summary = execute.execute_study(self.plan(tmp / "first", fake_requests([1.0, 2.0])), evaluator=FAKE,
                                            scheduler=scheduler)
            self.assertEqual(summary.state, "CANCELLED")
            self.assertFalse(evaluators.cancel_requested())
            summary = execute.execute_study(self.plan(tmp / "second", fake_requests([1.0, 2.0], sleep=0.05)),
                                            evaluator=fake_evaluator, scheduler=ReverseScheduler())
            self.assertEqual((summary.state, summary.states), ("COMPLETED", {"COMPLETED": 2}))

    def test_a_stopped_job_has_the_record_shape_of_an_engine_stop(self):
        codes = {"s0001": "TIME_LIMIT", "s0002": "CANCELLED", "s0003": "INTERRUPTED", "s0004": "WORKER_EXCEPTION"}
        with TempDir() as tmp:
            study_dir = self.plan(tmp / "study", fake_requests([1.0, 2.0, 3.0, 4.0]))
            scheduler = FakeProcessScheduler(report=lambda job: {"code": codes[job["sample_id"]], "message": "x"},
                                             run_worker=False)
            execute.execute_study(study_dir, evaluator=FAKE, scheduler=scheduler)
            shapes = [(r["state"], r["error"]["code"], [p["code"] for p in r["error"]["problems"]])
                      for _e, r in aggregate.collect_records(study_dir)]
            self.assertEqual(shapes, [("CANCELLED", "RUN_STOPPED", ["TIME_LIMIT"]),
                                      ("CANCELLED", "RUN_STOPPED", ["STUDY_CANCELLED"]),
                                      ("CANCELLED", "RUN_STOPPED", ["INTERRUPTED"]),
                                      ("FAILED", "WORKER_EXCEPTION", ["WORKER_EXCEPTION"])])

    def test_a_record_written_before_the_scheduler_gave_up_is_kept(self):
        with TempDir() as tmp:
            study_dir = self.plan(tmp / "study", fake_requests([1.0]))
            scheduler = FakeProcessScheduler(report=lambda job: {"code": "WORKER_LOST", "message": "gone"})
            summary = execute.execute_study(study_dir, evaluator=FAKE, scheduler=scheduler)
            self.assertEqual(summary.states, {"COMPLETED": 1})
            self.assertEqual(store.read_record(study_dir, "s0001")["targets"][0]["value"], 2.0)
            self.assertEqual(events(study_dir, "finished"), ["s0001"])


class WorkerChoiceTests(unittest.TestCase):
    def test_auto_uses_the_measured_run_time_of_completed_samples(self):
        with TempDir() as tmp:
            study_dir = tmp / "study"
            store.plan_study(study_dir, header(), fake_requests(
                [1.0, 2.0, 3.0, 4.0, 5.0, 6.0], fail=("s0003", "s0004", "s0005", "s0006"),
                extra={"s0001": {"seconds": 30.0}, "s0002": {"seconds": 30.0}}))
            execute.execute_study(study_dir, evaluator=FAKE)
            calls = []

            def suggest(samples, seconds_per_sample=resources.DEFAULT_SECONDS_PER_SAMPLE, *args, **kwargs):
                calls.append((samples, seconds_per_sample))
                return 1, {"workers": 1}

            with mock.patch.object(execute, "suggest_workers", suggest):
                execute.execute_study(study_dir, evaluator=FAKE, resume=True, workers="auto")
            self.assertEqual(calls, [(4, 30.0)])
            choice = store.read_study(study_dir)["execution"]["workers_choice"]
            self.assertEqual(choice["seconds_per_sample_source"], "measured")
            fresh = tmp / "fresh"
            store.plan_study(fresh, header(), fake_requests([1.0, 2.0, 3.0]))
            summary = execute.execute_study(fresh, evaluator=FAKE, workers="auto")
            choice = store.read_study(fresh)["execution"]["workers_choice"]
            self.assertEqual((summary.scheduler, choice["seconds_per_sample"], choice["seconds_per_sample_source"]),
                             ("serial", resources.DEFAULT_SECONDS_PER_SAMPLE, "assumed"))

    def test_benchmark_counts_stop_at_the_cpus(self):
        self.assertEqual(bench.worker_counts(None, cpus=2),
                         ([1, 2], [{"workers": 4, "reason": "above the 2 logical CPUs"}]))
        self.assertEqual(bench.worker_counts([2, 1], cpus=2), ([1, 2], []))
        with self.assertRaises(wc.WorkflowError) as caught:
            bench.worker_counts([1, 4], cpus=2)
        self.assertEqual(caught.exception.code, "BUDGET_INVALID")
        with mock.patch.object(bench.subprocess, "run") as child, \
                mock.patch.object(resources, "cpu_count", return_value=2):
            with self.assertRaises(wc.WorkflowError):
                bench.run_benchmark(None, workers=[4], samples=2, designs="expander")
        child.assert_not_called()  # refused before any measured run

    def test_collect_takes_answers_from_more_workers_than_one_wait_handles(self):
        class Process:
            pid, exitcode = 0, None

            def __init__(self, alive=True, exitcode=None):
                self.alive, self.exitcode = alive, exitcode

            def is_alive(self):
                return self.alive

            def join(self, timeout=None):
                pass

            def terminate(self):
                pass

        pool = schedulers.ProcessScheduler(1)
        workers, ends = [], []
        for index in range(70):  # 70 connections: above the 63 handles one Windows wait takes
            parent, child = multiprocessing.Pipe()
            dead = index == 3
            worker = schedulers._Worker(index, Process(alive=not dead, exitcode=3 if dead else None), parent)
            worker.job = {"sample_id": f"s{index:04d}"}
            workers.append(worker)
            ends.append(child)
        ends[65].send(("result", {"ok": True}))
        got = []
        try:
            pool._collect(workers, lambda job, result, error: got.append((job["sample_id"], result, error)))
        finally:
            for end in ends:
                end.close()
            for worker in workers:
                worker.conn.close()
        self.assertEqual(sorted(g[0] for g in got), ["s0003", "s0065"])
        lost = next(g for g in got if g[0] == "s0003")
        self.assertEqual(lost[2]["code"], "WORKER_LOST")
        self.assertIn("exit code 3", lost[2]["message"])
        self.assertEqual(next(g for g in got if g[0] == "s0065")[1], {"ok": True})


class ProcessFailureTests(unittest.TestCase):
    """A one-worker pool: a hard time limit and a lost worker each fail only their sample (a few worker starts)."""

    def test_time_limit_termination_and_worker_loss_fail_only_their_sample(self):
        with TempDir() as tmp:
            study_dir = tmp / "study"
            store.plan_study(study_dir, header(), fake_requests([1.0, 2.0, 3.0],
                                                                 extra={"s0001": {"stubborn": 60.0},
                                                                        "s0002": {"die": True}}))
            scheduler = schedulers.ProcessScheduler(1, job_timeout_s=1.0)
            try:
                summary = execute.execute_study(study_dir, evaluator=UNRULY, scheduler=scheduler)
            finally:
                scheduler.close()
            records = {e["sample_id"]: r for e, r in aggregate.collect_records(study_dir)}
            stopped = records["s0001"]
            self.assertEqual((stopped["state"], stopped["error"]["code"], stopped["error"]["problems"][0]["code"]),
                             ("CANCELLED", "RUN_STOPPED", "TIME_LIMIT"))
            lost = records["s0002"]
            self.assertEqual((lost["state"], lost["error"]["code"]), ("FAILED", "WORKER_LOST"))
            self.assertIn("exit code 3", lost["error"]["problems"][0]["message"])
            self.assertEqual(records["s0003"]["state"], "COMPLETED")
            self.assertEqual(summary.state, "COMPLETED")

    def test_ctrl_c_in_the_calling_process_cancels_a_pool_execution(self):
        with TempDir() as tmp:
            study_dir = tmp / "study"
            store.plan_study(study_dir, header(), fake_requests([1.0, 2.0, 3.0, 4.0], sleep=0.5))

            def interrupt(event):  # Ctrl-C reaches the calling process while it collects results
                raise KeyboardInterrupt

            summary = execute.execute_study(study_dir, evaluator=FAKE, workers=2, on_progress=interrupt)
            self.assertEqual(summary.state, "CANCELLED")
            self.assertEqual(store.read_study(study_dir)["execution"]["state"], "CANCELLED")
            self.assertLess(summary.ran, 4)
            self.assertEqual(sum(summary.states.values()), 4)
            self.assertTrue({"CANCELLED", "NOT_RUN"} & set(summary.states))

    def test_a_worker_of_a_superseded_execution_writes_no_record(self):
        with TempDir() as tmp:
            study_dir = tmp / "study"
            store.plan_study(study_dir, header(), fake_requests([1.0]))
            request = store.read_request(study_dir, "s0001")

            def taken_over(request, sample_dir, limits=None):  # another execution marks the sample meanwhile
                store.write_attempt(study_dir, "s0001", request["request_hash"], 1, evaluator=FAKE,
                                    execution="another")
                return fake_evaluator(request, sample_dir, limits)

            job = {"study_dir": study_dir.as_posix(), "sample_id": "s0001", "request_hash": request["request_hash"],
                   "evaluator": FAKE, "limits": None, "attempt": 1, "execution": "mine"}
            with self.assertRaises(wc.WorkflowError) as caught:
                execute.run_job(job, evaluator=taken_over)
            self.assertEqual(caught.exception.code, "SAMPLE_TAKEN_OVER")
            self.assertIsNone(store.read_record(study_dir, "s0001"))

    def test_worker_prints_stay_off_the_json_stdout(self):
        with TempDir() as tmp:
            study_dir = tmp / "study"
            store.plan_study(study_dir, header(parameters=[{"name": "x", "unit": "mm"}]), fake_requests([1.0]))
            done = subprocess.run([sys.executable, "-m", "os_optics_kit", "study", "run", str(study_dir),
                                   "--scheduler", "process", "--evaluator", NOISY], capture_output=True, text=True,
                                  cwd=REPO, timeout=300)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(json.loads(done.stdout)["status"], "COMPLETED")
            self.assertIn("evaluating s0001", done.stderr)


class StubProcess:
    """A worker process stand-in for the scheduler's bookkeeping (nothing is started)."""

    pid = 0

    def __init__(self, alive=True, exitcode=None):
        self.alive, self.exitcode = alive, exitcode

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        pass

    def terminate(self):
        pass


class OutOfOrderCancelScheduler:
    """Runs every job but the first in this process, then is cancelled and reports the first job as stopped by the
    cancellation (a pool where an earlier plan-ordered sample was still running when the study was cancelled)."""

    name = "out-of-order"
    in_process = True
    workers = 1

    def __init__(self):
        self.cancelled = False

    def run(self, jobs, worker, *, on_result):
        jobs = list(jobs)
        for job in jobs[1:]:
            on_result(job, worker(job), None)
        self.cancel()
        on_result(jobs[0], None, {"code": "CANCELLED", "message": "cancelled while running"})

    def cancel(self):
        self.cancelled = True


def closure_evaluator(factor):
    """An evaluator without an importable name: the fake one with ``factor`` times the target value."""
    def evaluate(request, sample_dir, limits=None):
        record = fake_evaluator(request, sample_dir, limits)
        for target in record["targets"]:
            target["value"] *= factor
        return record
    return evaluate


class CancellationAndStopRuleTests(unittest.TestCase):
    """A cancellation is never taken for the stop rule, never recorded as a lost worker, and does not stay with a
    scheduler after ``reset()``."""

    def plan(self, root, requests):
        store.plan_study(root, header(parameters=[{"name": "x", "unit": "mm"}]), requests)
        return root

    def test_a_starting_worker_lost_after_a_cancel_records_a_cancellation(self):
        got = []
        for cancel in (True, False):
            pool = schedulers.ProcessScheduler(1)
            parent, child = multiprocessing.Pipe()
            worker = schedulers._Worker(1, StubProcess(alive=False, exitcode=3221225786), parent)  # Ctrl-C exit
            worker.job = {"sample_id": "s0001"}
            if cancel:
                pool.cancel()
            try:
                pool._lost(worker, lambda job, result, error: got.append(error))
            finally:
                child.close()
        cancelled, lost = got
        self.assertEqual(cancelled["code"], "CANCELLED")
        self.assertNotIn("__main__", cancelled["message"])
        self.assertEqual(lost["code"], "WORKER_LOST")
        self.assertIn('if __name__ == "__main__"', lost["message"])
        self.assertEqual(execute.STOP_CAUSES[cancelled["code"]], "STUDY_CANCELLED")  # recorded CANCELLED/RUN_STOPPED

    def test_ctrl_c_with_stop_on_error_keeps_the_completed_samples(self):
        with TempDir() as tmp:
            study_dir = self.plan(tmp / "study", fake_requests([1.0, 2.0, 3.0, 4.0],
                                                               extra={"s0002": {"interrupt": True}}))
            summary = execute.execute_study(study_dir, evaluator=INTERRUPTING, stop_on=["ERROR"])
            self.assertEqual((summary.state, summary.stopped_by), ("CANCELLED", None))
            self.assertIsNone(store.read_study(study_dir)["execution"]["stopped_by"])
            self.assertEqual(store.read_record(study_dir, "s0001")["state"], "COMPLETED")
            for sample_id in ("s0003", "s0004"):
                record = store.read_record(study_dir, sample_id)
                self.assertEqual((record["state"], record["error"]["code"]), ("NOT_RUN", "CANCELLED"))
            self.assertEqual(events(study_dir, "stopped"), [])
            summary = execute.execute_study(study_dir, evaluator=INTERRUPTING, stop_on=["ERROR"], resume=True)
            self.assertEqual((summary.state, summary.reused, summary.ran), ("COMPLETED", 1, 3))

    def test_a_cancelled_earlier_sample_does_not_clear_later_completed_ones(self):
        with TempDir() as tmp:
            study_dir = self.plan(tmp / "study", fake_requests([1.0, 2.0, 3.0, 4.0]))
            summary = execute.execute_study(study_dir, evaluator=FAKE, scheduler=OutOfOrderCancelScheduler(),
                                            stop_on=["ERROR"])
            self.assertEqual((summary.state, summary.stopped_by), ("CANCELLED", None))
            self.assertEqual(summary.states, {"CANCELLED": 1, "COMPLETED": 3})
            for sample_id in ("s0002", "s0003", "s0004"):
                self.assertEqual(store.read_record(study_dir, sample_id)["state"], "COMPLETED")
                self.assertTrue((study_dir / "samples" / sample_id / "output.txt").is_file())
            summary = execute.execute_study(study_dir, evaluator=FAKE, stop_on=["ERROR"], resume=True)
            self.assertEqual((summary.state, summary.reused, summary.ran), ("COMPLETED", 3, 1))

    def test_a_failed_sample_still_stops_on_error(self):
        with TempDir() as tmp:
            study_dir = self.plan(tmp / "study", fake_requests([1.0, 2.0, 3.0], fail=("s0002",)))
            summary = execute.execute_study(study_dir, evaluator=FAKE, stop_on=["ERROR"])
            self.assertEqual((summary.state, summary.stopped_by), ("COMPLETED", "s0002"))
            record = store.read_record(study_dir, "s0003")
            self.assertEqual((record["state"], record["error"]["code"]), ("NOT_RUN", "STOPPED"))

    @requires_engine
    def test_a_sweep_aggregated_after_a_cancel_names_no_stop_sample(self):
        from os_optics_kit import sweep_parameters as sp

        with TempDir() as tmp:
            spec = {"parameters": [{"name": "x", "unit": "mm", "values": [1.0, 2.0, 3.0]}], "max_evaluations": 3,
                    "stop_on": ["ERROR"]}
            scheduler = schedulers.SerialScheduler()
            summary = sp.run_sweep(spec, tmp / "sweep", documents=[{"x": 1.0}, {"x": 2.0}, {"x": 3.0}],
                                   evaluator=FAKE, scheduler=scheduler, on_progress=lambda event: scheduler.cancel())
            self.assertEqual(summary["status"], "CANCELLED")
            aggregated = sp.aggregate_sweep(tmp / "sweep")
            self.assertEqual([r["outcome"] for r in aggregated["rows"]], ["PASS", "ERROR", "ERROR"])
            self.assertIsNone(aggregated["stopped_by"])

    def test_a_cancelled_scheduler_runs_again_after_reset(self):
        with TempDir() as tmp:
            study_dir = self.plan(tmp / "study", fake_requests([1.0, 2.0, 3.0]))
            scheduler = schedulers.SerialScheduler()

            def stop_after_one(event):
                if event["done"] == 1:
                    scheduler.cancel()

            summary = execute.execute_study(study_dir, evaluator=FAKE, scheduler=scheduler,
                                            on_progress=stop_after_one)
            self.assertEqual(summary.states, {"COMPLETED": 1, "NOT_RUN": 2})
            summary = execute.execute_study(study_dir, evaluator=FAKE, scheduler=scheduler, resume=True)
            self.assertEqual((summary.state, summary.ran), ("CANCELLED", 0))  # the cancellation stays in force
            scheduler.reset()
            summary = execute.execute_study(study_dir, evaluator=FAKE, scheduler=scheduler, resume=True)
            self.assertEqual((summary.state, summary.reused, summary.ran), ("COMPLETED", 1, 2))
            pool = schedulers.ProcessScheduler(1)  # no worker is started by cancel() or reset()
            pool.cancel()
            pool.reset()
            self.assertFalse(pool.cancelled or pool._event.is_set())

    def test_auto_with_the_serial_scheduler_runs_serially(self):
        with TempDir() as tmp:
            study_dir = self.plan(tmp / "study", fake_requests([1.0, 2.0, 3.0]))
            with mock.patch.object(execute, "suggest_workers", return_value=(4, {"workers": 4})) as suggest:
                summary = execute.execute_study(study_dir, evaluator=FAKE, workers="auto", scheduler="serial")
            suggest.assert_not_called()
            self.assertEqual((summary.state, summary.scheduler, summary.workers), ("COMPLETED", "serial", 1))
            self.assertIsNone(store.read_study(study_dir)["execution"]["workers_choice"])


class OwnershipTests(unittest.TestCase):
    """Every writer of study-level files holds the ownership; taking it over from a gone process is atomic."""

    def plan(self, root, requests):
        store.plan_study(root, header(parameters=[{"name": "x", "unit": "mm"}]), requests)
        return root

    def stale_owner(self, study_dir):
        done = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True,
                              text=True, timeout=60)  # a process that has ended
        stale = {"pid": int(done.stdout), "host": socket.gethostname(), "process_started": None, "started": "x",
                 "id": "gone"}
        wc.write_json(study_dir / store.OWNER_FILE, stale)
        return store.read_owner(study_dir)

    def test_aggregation_during_an_execution_is_refused_and_writes_nothing(self):
        with TempDir() as tmp:
            study_dir = self.plan(tmp / "study", fake_requests([1.0, 2.0]))
            seen = {}

            def evaluator(request, sample_dir, limits=None):
                if request["sample_id"] == "s0001":
                    before = (study_dir / store.STUDY_FILE).read_bytes()
                    with self.assertRaises(wc.WorkflowError) as caught:
                        study_tool.aggregate_study(study_dir)
                    seen["code"] = caught.exception.code
                    seen["study_unchanged"] = (study_dir / store.STUDY_FILE).read_bytes() == before
                    seen["files"] = [(study_dir / name).exists() for name in ("table.csv", "summary.json")]
                return fake_evaluator(request, sample_dir, limits)

            summary = execute.execute_study(study_dir, evaluator=evaluator)
            self.assertEqual(seen, {"code": "STUDY_RUNNING", "study_unchanged": True, "files": [False, False]})
            self.assertEqual((summary.state, store.read_study(study_dir)["execution"]["state"]),
                             ("COMPLETED", "COMPLETED"))
            study_tool.aggregate_study(study_dir)  # after the execution: accepted, and the ownership is released
            self.assertTrue((study_dir / "summary.json").is_file())
            self.assertFalse((study_dir / store.OWNER_FILE).exists())
            self.assertEqual(store.read_study(study_dir)["execution"]["state"], "COMPLETED")

    def test_planning_holds_the_ownership_while_it_writes(self):
        with TempDir() as tmp:
            study_dir = self.plan(tmp / "study", fake_requests([1.0, 2.0]))
            real, refused = store.write_plan, []

            def write_plan(folder, requests):  # an execution starts while the plan is being written
                with self.assertRaises(wc.WorkflowError) as caught:
                    execute.execute_study(folder, evaluator=FAKE)
                refused.append(caught.exception.code)
                return real(folder, requests)

            with mock.patch.object(store, "write_plan", write_plan):
                store.plan_study(study_dir, header(), fake_requests([1.0]), resume=True)
            self.assertEqual(refused, ["STUDY_RUNNING"])
            self.assertFalse((study_dir / store.OWNER_FILE).exists())
            self.assertEqual(len(store.read_plan(study_dir)), 1)
            self.assertFalse(store.has_results(study_dir))

    def test_two_callers_taking_over_a_gone_owner_give_one_owner(self):
        """The interleaving that gave two owners when the take-over was check-then-remove: caller B has found the
        gone owner's record still in place and is about to remove it when caller A takes ownership."""
        with TempDir() as tmp:
            study_dir = self.plan(tmp / "study", fake_requests([1.0]))
            stale = self.stale_owner(study_dir)
            real_read, b_checked, a_done, reads = store.read_owner, threading.Event(), threading.Event(), []
            results = {}

            def read_owner(folder):
                found = real_read(folder)
                if threading.current_thread().name == "B" and found == stale:
                    reads.append(found)
                    if len(reads) == 2:  # B's second look at the gone owner, right before it would remove it
                        b_checked.set()
                        a_done.wait(1.0)
                return found

            def caller(name):
                if name == "A":
                    b_checked.wait(1.0)
                try:
                    results[name] = store.acquire_execution(study_dir)
                except wc.WorkflowError as exc:
                    results[name] = exc.code
                except Exception as exc:  # noqa: BLE001 - reported by the assertion below
                    results[name] = repr(exc)
                finally:
                    if name == "A":
                        a_done.set()

            with mock.patch.object(store, "read_owner", read_owner):
                threads = [threading.Thread(target=caller, args=(name,), name=name) for name in ("B", "A")]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(30)
            owners = [value for value in results.values() if isinstance(value, dict)]
            self.assertEqual(len(owners), 1, results)
            self.assertEqual([value for value in results.values() if not isinstance(value, dict)], ["STUDY_RUNNING"])
            self.assertEqual(store.read_owner(study_dir)["id"], owners[0]["id"])
            store.release_execution(study_dir, owners[0])
            self.assertFalse((study_dir / store.OWNER_FILE).exists())

    def test_take_over_and_release_with_concurrent_callers_and_readers(self):
        with TempDir() as tmp:
            study_dir = self.plan(tmp / "study", fake_requests([1.0]))
            problems, stop = [], threading.Event()

            def reader():  # what study status and the planners' checks do
                while not stop.is_set():
                    try:
                        store.read_owner(study_dir)
                    except Exception as exc:  # noqa: BLE001
                        problems.append(repr(exc))
                    time.sleep(0.001)

            readers = [threading.Thread(target=reader) for _ in range(2)]
            for thread in readers:
                thread.start()
            try:
                for round_number in range(40):
                    if round_number % 4 == 0:
                        self.stale_owner(study_dir)
                    results = []

                    def caller():
                        try:
                            results.append(store.acquire_execution(study_dir))
                        except wc.WorkflowError as exc:
                            results.append(exc.code)
                        except Exception as exc:  # noqa: BLE001
                            results.append(repr(exc))

                    callers = [threading.Thread(target=caller) for _ in range(4)]
                    for thread in callers:
                        thread.start()
                    for thread in callers:
                        thread.join(30)
                    owners = [r for r in results if isinstance(r, dict)]
                    self.assertEqual(len(owners), 1, results)
                    self.assertEqual(sorted(r for r in results if not isinstance(r, dict)), ["STUDY_RUNNING"] * 3)
                    store.release_execution(study_dir, owners[0])
                    self.assertEqual(problems, [], f"round {round_number}")
            finally:
                stop.set()
                for thread in readers:
                    thread.join(30)
            self.assertEqual(problems, [])
            self.assertFalse((study_dir / store.OWNER_FILE).exists())

    def test_a_file_held_open_longer_than_a_second_is_retried_until_the_budget_ends(self):
        calls = []

        def action():  # refused 30 times (the fixed 20 attempts gave up), then accepted
            calls.append(None)
            if len(calls) <= 30:
                raise PermissionError(13, "The process cannot access the file because it is being used")
            return "done"

        with mock.patch.object(store.time, "sleep") as sleep:
            self.assertEqual(store._sharing_retry(action), "done")
        self.assertEqual((len(calls), sleep.call_count), (31, 30))
        calls.clear()
        started = time.monotonic()
        with self.assertRaises(PermissionError):
            store._sharing_retry(action, budget_s=0.1)
        self.assertGreaterEqual(time.monotonic() - started, 0.1)
        self.assertLess(len(calls), 30)


class EvaluatorRecordTests(unittest.TestCase):
    """What a resume makes of an evaluator without a name, an evaluator that empties its directory, and damaged
    study files."""

    def plan(self, root, requests):
        store.plan_study(root, header(parameters=[{"name": "x", "unit": "mm"}]), requests)
        return root

    def test_an_evaluator_without_a_name_has_no_records_to_reuse(self):
        with TempDir() as tmp:
            study_dir = self.plan(tmp / "study", fake_requests([1.0, 2.0]))
            execute.execute_study(study_dir, evaluator=closure_evaluator(1.0))
            self.assertIsNone(store.read_study(study_dir)["execution"]["evaluator"])
            status = study_tool.status(study_dir)
            self.assertEqual(status["to_run_on_resume"]["shown"], ["s0001", "s0002"])
            self.assertIn("no importable module:function name", status["resume_note"])
            summary = execute.execute_study(study_dir, evaluator=closure_evaluator(10.0), resume=True)
            self.assertEqual((summary.reused, summary.ran), (0, 2))
            self.assertEqual(store.read_record(study_dir, "s0001")["targets"][0]["value"], 20.0)
            summary = execute.execute_study(study_dir, evaluator=FAKE, resume=True)
            self.assertEqual((summary.reused, summary.ran), (0, 2))
            self.assertNotIn("resume_note", study_tool.status(study_dir))
            self.assertEqual(study_tool.status(study_dir)["to_run_on_resume"]["total"], 0)

    def test_an_evaluator_that_empties_its_directory_completes(self):
        def clearing(request, sample_dir, limits=None):
            shutil.rmtree(sample_dir)
            Path(sample_dir).mkdir()
            return fake_evaluator(request, sample_dir, limits)

        with TempDir() as tmp:
            study_dir = self.plan(tmp / "study", fake_requests([1.0, 2.0]))
            summary = execute.execute_study(study_dir, evaluator=clearing)
            self.assertEqual(summary.states, {"COMPLETED": 2})
            self.assertTrue((study_dir / "samples" / "s0001" / "output.txt").is_file())

    def test_a_sample_another_execution_finished_keeps_its_record(self):
        with TempDir() as tmp:
            study_dir = self.plan(tmp / "study", fake_requests([1.0]))
            request = store.read_request(study_dir, "s0001")
            other = {"request_hash": request["request_hash"], "state": "COMPLETED", "outcome": "PASS",
                     "worker": "another", "evaluator": FAKE}

            def finished_by_another(request, sample_dir, limits=None):  # its record written, its marker removed
                wc.write_json(Path(sample_dir) / store.SAMPLE_FILE, other)
                (Path(sample_dir) / store.ATTEMPT_FILE).unlink()
                return fake_evaluator(request, sample_dir, limits)

            job = {"study_dir": study_dir.as_posix(), "sample_id": "s0001", "request_hash": request["request_hash"],
                   "evaluator": FAKE, "limits": None, "attempt": 1, "execution": "mine"}
            with self.assertRaises(wc.WorkflowError) as caught:
                execute.run_job(job, evaluator=finished_by_another)
            self.assertEqual(caught.exception.code, "SAMPLE_TAKEN_OVER")
            self.assertEqual(store.read_record(study_dir, "s0001"), other)

    def test_damaged_plan_request_and_records_are_refused_or_run_again(self):
        with TempDir() as tmp:
            study_dir = self.plan(tmp / "study", fake_requests([1.0, 2.0, 3.0]))
            plan_path = study_dir / store.PLAN_FILE
            good_plan = plan_path.read_bytes()
            for damaged in (b"\xff\xfe\x00garbage\n", good_plan.decode("utf-8").encode("utf-16")):
                plan_path.write_bytes(damaged)
                for attempt in (lambda: execute.execute_study(study_dir, evaluator=FAKE),
                                lambda: study_tool.status(study_dir)):
                    with self.assertRaises(wc.WorkflowError) as caught:
                        attempt()
                    self.assertEqual(caught.exception.code, "PLAN_INVALID")
                    self.assertIn("--overwrite", str(caught.exception))
            plan_path.write_bytes(good_plan)
            request_path = store.request_path(study_dir, "s0003")
            good_request = request_path.read_bytes()
            request_path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(wc.WorkflowError) as caught:
                execute.execute_study(study_dir, evaluator=FAKE)
            self.assertEqual(caught.exception.code, "REQUEST_INVALID")
            self.assertIn("--overwrite", str(caught.exception))
            request_path.write_bytes(good_request)
            execute.execute_study(study_dir, evaluator=FAKE)
            for sample_id, outcome in (("s0001", None), ("s0002", "weird")):
                record = store.read_record(study_dir, sample_id)
                damaged = {"request_hash": record["request_hash"], "state": "COMPLETED", "evaluator": FAKE}
                if outcome is not None:
                    damaged["outcome"] = outcome
                wc.write_json(store.sample_path(study_dir, sample_id), damaged)
            rows = aggregate.aggregate_generic(study_dir)["rows"]  # a damaged record aggregates as not run
            self.assertEqual([r["error_code"] for r in rows[:2]], ["NOT_RUN", "NOT_RUN"])
            self.assertEqual(study_tool.status(study_dir)["to_run_on_resume"]["shown"], ["s0001", "s0002"])
            summary = execute.execute_study(study_dir, evaluator=FAKE, resume=True)
            self.assertEqual((summary.state, summary.reused, summary.ran), ("COMPLETED", 1, 2))
            self.assertEqual(summary.states, {"COMPLETED": 3})


class BenchmarkSlowParallelTests(unittest.TestCase):
    """The benchmark records the parallel runs that were not faster than one worker (measurements replaced)."""

    def run_benchmark(self, execute_s, designs="expander", repeat=1):
        def child(command, **kwargs):
            workers = int(command[-2])
            result = {"design": command[-4], "samples": 2, "workers": workers, "scheduler": "serial"
                      if workers == 1 else "process", "blas_threads": None, "build_s": 0.0,
                      "execute_s": execute_s[workers], "study_s": execute_s[workers],
                      "throughput": round(2 / execute_s[workers], 3), "startup_s": [0.5] * workers if workers > 1 else [],
                      "first_result_s": [0.1], "peak_memory_mib": 100.0, "parent_peak_mib": 50.0,
                      "states": {"COMPLETED": 2}, "basis_sha256": "same"}
            return SimpleNamespace(returncode=0, stdout=json.dumps(result) + "\n", stderr="")

        with TempDir() as tmp, mock.patch.object(bench.subprocess, "run", child), \
                mock.patch.object(resources, "cpu_count", return_value=2):
            result = bench.run_benchmark(tmp / "bench", workers=[1, 2], samples=2, designs=designs, repeat=repeat)
            return result, (tmp / "bench" / "bench.md").read_text(encoding="utf-8")

    def test_the_reproduce_line_names_the_installed_command_and_the_options_used(self):
        for designs, repeat, command in (
                ("expander", 1, "os-optics-kit study bench --workers 1,2 --samples 2 --design expander`."),
                ("relay", 3, "os-optics-kit study bench --workers 1,2 --samples 2 --design relay --repeat 3`."),
                ("both", 1, "os-optics-kit study bench --workers 1,2 --samples 2`.")):
            with self.subTest(designs=designs, repeat=repeat):
                _, text = self.run_benchmark({1: 1.0, 2: 0.5}, designs=designs, repeat=repeat)
                self.assertIn("Reproduce: `" + command, text)
                self.assertNotIn("scripts/bench_study.py", text)

    def test_a_slower_parallel_run_is_recorded(self):
        result, text = self.run_benchmark({1: 1.0, 2: 2.0})
        self.assertEqual(result["parallel_not_faster"], [{"design": "expander", "workers": 2, "speedup": 0.5}])
        self.assertIn("expander with 2 workers (speed-up 0.5)", text)
        self.assertTrue(all(r["equal_to_one_worker"] for r in result["runs"]))
        result, text = self.run_benchmark({1: 1.0, 2: 0.5})
        self.assertEqual(result["parallel_not_faster"], [])
        self.assertIn("not faster than one worker: none.", text)


if __name__ == "__main__":
    unittest.main()
