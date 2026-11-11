"""Execute a planned study: run every request of ``plan.jsonl`` that still needs a result, write one record each.

``execute_study(study_dir, workers=1)`` is the one execution step of the sweep, sensitivity, tolerance and candidate
studies (and of any caller that planned requests with ``store.plan_study``):

* it checks the study identity first (plan hash, Engine build, catalog release; ``STUDY_IDENTITY_CHANGED``);
* a planner refusal (``request.refusal``) is recorded ``REFUSED`` without running;
* the other requests go to a scheduler (``schedulers.py``: serial in this process, or a pool of worker processes)
  which calls the evaluator (``evaluators.py``) once per request; the evaluating process writes
  ``samples/<id>/sample.json``, the record of 3.2: the sample record keys + the request labels + ``request_hash``,
  ``state``, ``attempts``, ``timing``, ``peak_memory_bytes``, ``worker``; paths in it are study-relative;
* ``stop_on`` (outcome classes) acts in plan order: every sample after the first plan-ordered sample with such an
  outcome is recorded ``ERROR`` / ``STOPPED`` (``state`` ``NOT_RUN``), whether or not it ran; samples not started
  once a stop is known are skipped;
* ``resume=True`` keeps the samples whose record has the plan's ``request_hash`` and state ``COMPLETED`` or
  ``REFUSED``, reads back a complete run directory left without a record, and runs the rest; without it a study
  with results is refused (``STUDY_STARTED``);
* a cancellation (``scheduler.cancel()``, Ctrl-C) stops handing out work, stops running evaluations at their next
  Engine checkpoint and records unfinished samples ``CANCELLED`` / ``NOT_RUN``; ``study.json`` ``execution.state``
  is then ``CANCELLED`` and the study can be resumed.

Every step is appended to ``journal.jsonl`` (``execution_started``, ``reused``, ``recovered``, ``refused``,
``started``, ``finished``, ``failed``, ``cancelled``, ``not_run``, ``stopped``, ``execution_finished``).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import functools
import os
from pathlib import Path
import shutil
import time

from .. import workflow_common as wc
from . import evaluators, schedulers, store
from .resources import ResourceBudget, suggest_workers

FINAL_STATES = ("COMPLETED", "REFUSED")
#: How a job without a result is recorded: scheduler error code -> state.
ERROR_STATES = {"CANCELLED": "CANCELLED", "TIME_LIMIT": "CANCELLED"}
#: Seconds a cancelled worker gets beyond the time limit before it is terminated.
TIME_LIMIT_GRACE_S = 5.0


@dataclass
class ExecutionSummary:
    """What one ``execute_study`` call did."""

    state: str  # COMPLETED (every planned sample has a final record or was stopped) or CANCELLED
    samples: int
    states: dict = field(default_factory=dict)  # final record state -> count, over the whole plan
    outcomes: dict = field(default_factory=dict)  # requirement outcome -> count, over the whole plan
    ran: int = 0  # evaluations started in this call
    reused: int = 0
    recovered: int = 0
    refused: int = 0
    stopped_by: str | None = None
    scheduler: str = "serial"
    workers: int = 1
    blas_threads: int | None = None
    evaluator: str | None = None
    limits: dict = field(default_factory=dict)
    seconds: float = 0.0
    startup: list = field(default_factory=list)  # worker start-up seconds (process scheduler)

    def as_dict(self):
        return asdict(self)


# --------------------------------------------------------------------------------------------- identity

def current_identity(entries):
    """The identity of a plan with the installed Engine and catalog (None fields where no Engine is installed)."""
    try:
        engine, catalog = wc.engine_identity(), wc.catalog_identity()
    except ImportError:
        engine, catalog = {}, {}
    return store.study_identity(entries, engine, catalog)


# --------------------------------------------------------------------------------------------- records

def complete_record(record, request, study_dir, *, state, attempt=1, queued_s=None, worker=None):
    """The record of 3.2 from an evaluator's record: every sample-record key, the request labels, the job fields;
    paths study-relative."""
    sample_id = request["sample_id"]
    out = {**evaluators.empty_record(None, request["selected_targets"]), **record}
    out["sample_id"] = sample_id
    out.update(request["labels"])
    folder = store.sample_dir(study_dir, sample_id)
    relative = f"{store.SAMPLES_DIR}/{sample_id}"
    out["sample_dir"] = relative
    out["project_file"] = f"{relative}/project.json" if (folder / "project.json").is_file() else None
    out["run_dir"] = f"{relative}/run" if out.get("run_dir") else None
    timing = dict(out.get("timing") or {})
    out["request_hash"] = request["request_hash"]
    out["state"] = state
    out["attempts"] = attempt
    out["timing"] = {"queued_s": None if queued_s is None else round(queued_s, 4),
                     "run_s": round(float(out.get("seconds") or 0.0), 4),
                     "phase_seconds": timing.get("phase_seconds")}
    out.setdefault("peak_memory_bytes", None)
    out["worker"] = worker
    return out


def job_state(record):
    """The job state of an evaluator's record: a stopped run is CANCELLED, an unexpected Engine failure FAILED."""
    if record.get("state") in store.STATES:
        return record["state"]
    code = (record.get("error") or {}).get("code")
    if code == "RUN_STOPPED":
        return "CANCELLED"
    if code == "ENGINE_EXCEPTION":
        return "FAILED"
    return "COMPLETED"


def worker_name():
    return f"pid-{os.getpid()}"


def run_job(job, evaluator=None):
    """Evaluate one request in this process and write its record (the worker entry of every scheduler).

    ``job``: ``{study_dir, sample_id, request_hash, evaluator ("module:function"), limits, attempt, queued_at}``;
    ``evaluator``: a function used instead of ``job["evaluator"]`` (in-process schedulers only)."""
    queued_s = max(0.0, time.time() - job["queued_at"]) if job.get("queued_at") else None
    study_dir = Path(job["study_dir"])
    sample_id = job["sample_id"]
    request = store.read_request(study_dir, sample_id)
    if request["request_hash"] != job["request_hash"]:
        raise wc.WorkflowError("REQUEST_CHANGED", f"{sample_id}: requests/{sample_id}.json changed after the job "
                               "was queued", sample_id=sample_id)
    folder = store.sample_dir(study_dir, sample_id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / store.SAMPLE_FILE).unlink(missing_ok=True)
    store.write_attempt(study_dir, sample_id, request["request_hash"], job.get("attempt", 1))
    function = evaluator if evaluator is not None else evaluators.load_evaluator(job["evaluator"])
    record = function(request, folder, job.get("limits"))
    if not isinstance(record, dict) or record.get("outcome") not in wc.OUTCOMES:
        raise wc.WorkflowError("EVALUATOR_INVALID", f"{sample_id}: the evaluator returned no sample record with an "
                               f"outcome in {wc.OUTCOMES}", sample_id=sample_id)
    record = complete_record(record, request, study_dir, state=job_state(record), attempt=job.get("attempt", 1),
                             queued_s=queued_s, worker=worker_name())
    store.write_json_atomic(folder / store.SAMPLE_FILE, record)
    (folder / store.ATTEMPT_FILE).unlink(missing_ok=True)
    return {"sample_id": sample_id, "state": record["state"], "outcome": record["outcome"],
            "seconds": record["timing"]["run_s"]}


def _not_run_record(request, study_dir, state, code, message, *, stage="not_run", attempt=0):
    record = wc.error_record(stage, code, [{"code": code, "message": message}], selected=request["selected_targets"])
    return complete_record(record, request, study_dir, state=state, attempt=attempt)


def _clear_sample(study_dir, sample_id):
    """Remove every file of a sample directory (an evaluation's outputs, record, attempt marker); keep the
    directory."""
    folder = store.sample_dir(study_dir, sample_id)
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True, exist_ok=True)


def recover_record(study_dir, request, attempt=1):
    """The record of a sample whose complete run directory has no ``sample.json`` (read back, not rerun)."""
    folder = store.sample_dir(study_dir, request["sample_id"])
    _summary, fields = wc.read_saved_run(folder / "run", request["selected_targets"])
    project = folder / "project.json"
    record = {**wc._empty_record(None, project if project.is_file() else None, request["selected_targets"]),
              **fields}
    return complete_record(record, request, study_dir, state="COMPLETED", attempt=attempt, worker="recovered")


# --------------------------------------------------------------------------------------------- execution

def _evaluator_choice(evaluator):
    """``(name or None, function or None)``: a ``module:function`` text, or a function (named when importable)."""
    if evaluator is None or isinstance(evaluator, str):
        name = evaluator or evaluators.DEFAULT_EVALUATOR
        if ":" not in name:
            raise wc.WorkflowError("EVALUATOR_INVALID", f"evaluator {name!r}: use module:function", evaluator=name)
        return name, None
    if not callable(evaluator):
        raise wc.WorkflowError("EVALUATOR_INVALID", "the evaluator must be a function or module:function")
    return evaluators.evaluator_name(evaluator), evaluator


def execute_study(study_dir, *, workers=1, scheduler=None, evaluator=None, limits=None, resume=False, stop_on=None,
                  on_progress=None, blas_threads=None):
    """Run the planned study in ``study_dir`` (module docstring) and return an ``ExecutionSummary``.

    ``workers``: worker count (1 = serial in this process unless ``scheduler`` says otherwise; ``"auto"``:
    ``resources.suggest_workers`` for the samples still to run); ``scheduler``:
    ``"serial"``, ``"process"`` or a scheduler object (not closed here, so one pool can serve several studies);
    ``evaluator``: ``"module:function"`` or a function (default ``evaluators.run_project``; a worker process needs an
    importable one); ``limits``: ``{"time_limit_s", "max_memory_mib"}`` per evaluation; ``stop_on``: outcome classes
    (default: the study's ``stop_on``); ``on_progress(event)``: called in this process after every sample with
    ``{sample_id, state, outcome, done, total}``; ``blas_threads``: threads per worker (default 1 with several
    workers)."""
    t0 = time.perf_counter()
    study_dir = Path(study_dir).resolve()
    study = store.read_study(study_dir)
    entries = store.read_plan(study_dir)
    store.require_same_identity(study["identity"], current_identity(entries), study_dir=study_dir)
    stop_on = list(study.get("stop_on") or []) if stop_on is None else list(stop_on)
    unknown = [item for item in stop_on if item not in wc.OUTCOMES]
    if unknown:
        raise wc.WorkflowError("BAD_ARGUMENT", f"stop_on lists outcome classes {wc.OUTCOMES}, not {unknown}")
    limits = evaluators.normalize_limits(limits)
    if not resume and store.has_results(study_dir):
        raise wc.WorkflowError("STUDY_STARTED", f"{study_dir.as_posix()} already holds sample results; resume it "
                               "(--resume) or plan it again (--overwrite)", study_dir=study_dir.as_posix())
    evaluator_name, evaluator_function = _evaluator_choice(evaluator)
    choice = None
    if workers == "auto":
        to_run = len(store.resume_selection(study_dir, entries)[2]) if resume else len(entries)
        workers, choice = suggest_workers(to_run)
    owned = scheduler is None or isinstance(scheduler, str)
    if owned:
        budget = ResourceBudget.resolve(workers, blas_threads=blas_threads, max_memory_mib=limits["max_memory_mib"],
                                        time_limit_s=limits["time_limit_s"])
        timeout = None if limits["time_limit_s"] is None else limits["time_limit_s"] + TIME_LIMIT_GRACE_S
        sched = schedulers.make_scheduler(scheduler, budget.workers, blas_threads=budget.blas_threads,
                                          job_timeout_s=timeout)
    else:
        sched = schedulers.make_scheduler(scheduler)
    in_process = bool(getattr(sched, "in_process", False))
    if not in_process and evaluator_name is None:
        raise wc.WorkflowError("EVALUATOR_INVALID", "worker processes import the evaluator by name: pass "
                               "\"module:function\" or a module-level function")
    if in_process and evaluator_function is None and evaluator_name != evaluators.DEFAULT_EVALUATOR:
        evaluator_function = evaluators.load_evaluator(evaluator_name)
    summary = ExecutionSummary(state="RUNNING", samples=len(entries), scheduler=getattr(sched, "name", "custom"),
                               workers=int(getattr(sched, "workers", workers) or 1),
                               blas_threads=getattr(sched, "blas_threads", None), evaluator=evaluator_name,
                               limits=limits)
    started = wc.utc_now()
    study["execution"] = {"scheduler": summary.scheduler, "workers": summary.workers,
                          "blas_threads": summary.blas_threads, "limits": limits, "evaluator": evaluator_name,
                          "workers_choice": choice, "resumed": bool(resume), "started": started, "finished": None,
                          "state": "RUNNING"}
    store.write_study(study_dir, study)
    store.append_journal(study_dir, "execution_started", scheduler=summary.scheduler, workers=summary.workers,
                         resume=bool(resume))

    order = {entry["sample_id"]: index for index, entry in enumerate(entries)}
    records = {}
    pending = entries
    if resume:
        reused, recoverable, pending = store.resume_selection(study_dir, entries)
        for entry in reused:
            records[entry["sample_id"]] = store.read_record(study_dir, entry["sample_id"])
            store.append_journal(study_dir, "reused", entry["sample_id"], request_hash=entry["request_hash"])
        for entry in recoverable:
            request = store.read_request(study_dir, entry["sample_id"])
            try:
                record = recover_record(study_dir, request)
            except Exception as exc:  # noqa: BLE001 - an unreadable run directory is run again
                store.append_journal(study_dir, "recovery_failed", entry["sample_id"],
                                     message=f"{type(exc).__name__}: {exc}")
                pending = [*pending, entry]
                continue
            store.write_json_atomic(store.sample_path(study_dir, entry["sample_id"]), record)
            records[entry["sample_id"]] = record
            store.append_journal(study_dir, "recovered", entry["sample_id"])
        summary.reused, summary.recovered = len(reused), len(records) - len(reused)
        pending = sorted(pending, key=lambda e: order[e["sample_id"]])

    runnable = []
    for entry in pending:
        request = store.read_request(study_dir, entry["sample_id"])
        if request["refusal"] is not None:
            _clear_sample(study_dir, entry["sample_id"])
            refusal = request["refusal"]
            record = wc.error_record(refusal["stage"], refusal["code"], refusal.get("problems") or [],
                                     selected=request["selected_targets"])
            record = complete_record(record, request, study_dir, state="REFUSED", worker="planner")
            store.write_json_atomic(store.sample_path(study_dir, entry["sample_id"]), record)
            records[entry["sample_id"]] = record
            summary.refused += 1
            store.append_journal(study_dir, "refused", entry["sample_id"], code=refusal["code"])
        else:
            previous = store.read_record(study_dir, entry["sample_id"])
            attempt = 1
            if previous is not None and previous.get("request_hash") == entry["request_hash"]:
                attempt = int(previous.get("attempts") or 0) + 1
            runnable.append((entry, request, attempt))

    def first_stop():
        hits = [order[sid] for sid, record in records.items() if record.get("outcome") in stop_on]
        return min(hits) if hits else None

    state = {"cancelled": False}
    requests = {entry["sample_id"]: request for entry, request, _attempt in runnable}

    def jobs():
        for entry, _request, attempt in runnable:
            if state["cancelled"] or getattr(sched, "cancelled", False):
                return
            stop = first_stop() if stop_on else None
            if stop is not None and order[entry["sample_id"]] > stop:
                continue
            summary.ran += 1
            store.append_journal(study_dir, "started", entry["sample_id"], attempt=attempt)
            yield {"study_dir": study_dir.as_posix(), "sample_id": entry["sample_id"],
                   "request_hash": entry["request_hash"], "evaluator": evaluator_name, "limits": limits,
                   "attempt": attempt, "queued_at": time.time()}

    def on_result(job, result, error):
        sample_id = job["sample_id"]
        request = requests[sample_id]
        record = None
        if error is None:
            record = store.read_record(study_dir, sample_id)
            if record is None or record.get("request_hash") != request["request_hash"]:
                error = {"code": "RECORD_MISSING", "message": "the evaluation returned but wrote no record"}
        if error is not None:
            existing = store.read_record(study_dir, sample_id)
            if (existing is not None and existing.get("request_hash") == request["request_hash"]
                    and existing.get("attempts") == job["attempt"] and existing.get("state") == "CANCELLED"):
                record = existing  # the evaluation recorded its own stop before the scheduler gave up on it
            else:
                record = _not_run_record(request, study_dir, ERROR_STATES.get(error["code"], "FAILED"),
                                         error["code"], error.get("message") or error["code"], stage="run",
                                         attempt=job["attempt"])
                store.write_json_atomic(store.sample_path(study_dir, sample_id), record)
        records[sample_id] = record
        event = {"COMPLETED": "finished", "CANCELLED": "cancelled"}.get(record["state"], "failed")
        store.append_journal(study_dir, event, sample_id, state=record["state"], outcome=record["outcome"],
                             code=(record.get("error") or {}).get("code"))
        if on_progress is not None:
            on_progress({"sample_id": sample_id, "state": record["state"], "outcome": record["outcome"],
                         "done": len(records), "total": len(entries)})

    worker = functools.partial(run_job, evaluator=evaluator_function) if in_process else run_job
    try:
        if runnable:
            sched.run(jobs(), worker, on_result=on_result)
    except KeyboardInterrupt:
        state["cancelled"] = True
        sched.cancel()
    finally:
        if owned and callable(getattr(sched, "close", None)):
            sched.close()
    cancelled = state["cancelled"] or bool(getattr(sched, "cancelled", False))
    summary.startup = list(getattr(sched, "startup", []) or [])

    stop = first_stop() if stop_on else None
    for entry in entries:
        sample_id = entry["sample_id"]
        if stop is not None and order[sample_id] > stop:
            current = records.get(sample_id) or store.read_record(study_dir, sample_id) or {}
            if (current.get("request_hash") != entry["request_hash"]
                    or (current.get("error") or {}).get("code") != "STOPPED"):
                _clear_sample(study_dir, sample_id)
                request = store.read_request(study_dir, sample_id)
                stopper = entries[stop]["sample_id"]
                record = _not_run_record(request, study_dir, "NOT_RUN", "STOPPED",
                                         f"stop_on rule after sample {stopper}")
                store.write_json_atomic(store.sample_path(study_dir, sample_id), record)
                store.append_journal(study_dir, "stopped", sample_id, after=stopper)
                current = record
            records[sample_id] = current
        elif sample_id not in records:
            request = store.read_request(study_dir, sample_id)
            if cancelled:
                record = _not_run_record(request, study_dir, "NOT_RUN", "CANCELLED",
                                         "the study was cancelled before this sample ran")
            else:
                record = _not_run_record(request, study_dir, "FAILED", "NOT_REPORTED",
                                         "the scheduler reported no result for this sample")
            existing = store.read_record(study_dir, sample_id)
            if existing is None or existing.get("request_hash") != entry["request_hash"] \
                    or existing.get("state") not in FINAL_STATES:
                store.write_json_atomic(store.sample_path(study_dir, sample_id), record)
                store.append_journal(study_dir, "not_run", sample_id, code=record["error"]["code"])
            else:
                record = existing
            records[sample_id] = record
    summary.stopped_by = None if stop is None else entries[stop]["sample_id"]
    for record in records.values():
        summary.states[record["state"]] = summary.states.get(record["state"], 0) + 1
        summary.outcomes[record["outcome"]] = summary.outcomes.get(record["outcome"], 0) + 1
    summary.state = "CANCELLED" if cancelled else "COMPLETED"
    summary.seconds = round(time.perf_counter() - t0, 3)
    study = store.read_study(study_dir)
    study["execution"].update({"finished": wc.utc_now(), "state": summary.state, "stopped_by": summary.stopped_by,
                               "startup": summary.startup, "seconds": summary.seconds,
                               "counts": {"ran": summary.ran, "reused": summary.reused,
                                          "recovered": summary.recovered, "refused": summary.refused,
                                          "states": summary.states}})
    store.write_study(study_dir, study)
    store.append_journal(study_dir, "execution_cancelled" if cancelled else "execution_finished",
                         states=summary.states, seconds=summary.seconds)
    return summary
