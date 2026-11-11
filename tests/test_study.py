"""The study execution components (os_optics_kit.study): requests, store, execution, resume, cancellation.

Most tests use a fake evaluator (no Engine needed) so the contracts are checked in the unit job too; the tests of the
default evaluator are marked ``@requires_engine``. Process-pool tests use at most two workers and a few samples.
"""

import hashlib
from pathlib import Path
import time
import unittest

from tests.workflow_fixtures import HAS_ENGINE, TempDir, expander, quiet_environment, requires_engine

quiet_environment()

from os_optics_kit import workflow_common as wc  # noqa: E402
from os_optics_kit.study import (aggregate, evaluators, execute, requests as rq, resources,  # noqa: E402
                                 schedulers, store)

FAKE = "tests.test_study:fake_evaluator"


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
        "seconds": 0.001})
    return record


def header(kind="test.study", **keys):
    """A study header with the identity of this environment (empty without an Engine)."""
    if HAS_ENGINE:
        return {"kind": kind, **keys, "engine": wc.engine_identity(), "installed_catalog": wc.catalog_identity()}
    return {"kind": kind, **keys, "engine": {}, "installed_catalog": {}}


def fake_requests(values, *, fail=(), outcomes=None, sleep=0.0, refuse=()):
    out = []
    for index, value in enumerate(values, start=1):
        sample_id = f"s{index:04d}"
        if sample_id in refuse:
            out.append(rq.make_request(sample_id, None, refusal={"stage": "edit", "code": "EDIT_REFUSED",
                                                                  "problems": [{"code": "EDIT_REFUSED"}]},
                                       labels={"parameters": {"x": {"value": value, "unit": "mm"}}}))
            continue
        fake = {"x": value, "fail": sample_id in fail, "sleep": sleep,
                "outcome": (outcomes or {}).get(sample_id, "PASS")}
        out.append(rq.make_request(sample_id, {"x": value}, selected_targets=["t"],
                                   labels={"role": "sample", "fake": fake,
                                           "parameters": {"x": {"value": value, "unit": "mm"}}}))
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


if __name__ == "__main__":
    unittest.main()
