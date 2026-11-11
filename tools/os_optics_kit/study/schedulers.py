"""Schedulers: who runs the jobs of a study, and how many at a time.

A scheduler is any object with::

    run(jobs, worker, *, on_result) -> None    # call worker(job) for every job; report each one
    cancel() -> None                           # stop handing out jobs; stop running ones at their next checkpoint

A cancellation stays in force: a cancelled scheduler starts no job until ``reset()`` withdraws the cancellation.
``jobs`` is an iterator of small JSON-able dicts, pulled one at a time as capacity frees up (the executor stops
yielding when a stop rule or a cancellation makes later jobs unnecessary). ``worker`` is a module-level function.
``on_result(job, result, error)`` is called in the calling process, in completion order, with the worker's return
value or with ``error = {"code", "message"}`` when the job produced none (``WORKER_EXCEPTION``, ``WORKER_LOST``,
``CANCELLED``, ``INTERRUPTED`` (Ctrl-C), ``TIME_LIMIT``). Optional attributes: ``name``, ``workers``,
``blas_threads``, ``in_process`` (True when ``worker`` runs in the calling process), ``cancelled``, ``reset()`` and
``close()``.

``SerialScheduler`` runs the jobs one after another in the calling process; Ctrl-C or ``cancel()`` during a job
stops the study after that job. ``ProcessScheduler`` keeps a pool of worker processes for its whole lifetime
(several ``run`` calls share it): each worker is started with the ``spawn`` method on every platform, sets the
numerical-library thread variables, imports the Engine once and then takes jobs until the scheduler is closed; what
a worker prints goes to stderr. A worker that dies fails only the job it was running and is replaced. After
``cancel()`` (or Ctrl-C in the calling process) running jobs get ``grace_s`` seconds to stop at an Engine
checkpoint; a worker that does not return in that time is terminated.

A script that uses ``ProcessScheduler`` must guard its entry point with ``if __name__ == "__main__":``.
"""

from __future__ import annotations

import importlib
import multiprocessing
import multiprocessing.connection
import os
import sys
import threading
import time

from .. import workflow_common as wc
from . import evaluators
from .resources import THREAD_VARIABLES
#: Imported by every worker before its first job, so the first job does not pay for it.
WARM_MODULES = ("os_optics", "os_optics.local", "os_optics.results", "os_optics.run_monitor",
                "os_optics_kit.workflow_common", "os_optics_kit.study.execute")
DEFAULT_GRACE_S = 10.0
START_METHOD = "spawn"
#: Connections per ``multiprocessing.connection.wait`` call (Windows waits on at most 63 handles before Python 3.13).
WAIT_SLICE = 60


def _has_message(worker):
    """Whether a worker's answer is waiting in its pipe."""
    try:
        return bool(worker.conn.poll())
    except (EOFError, OSError):
        return False


class SerialScheduler:
    """Run every job in the calling process, in order."""

    name = "serial"
    in_process = True
    workers = 1
    blas_threads = None  # the calling process keeps the thread settings it already has

    def __init__(self):
        self._event = threading.Event()
        self.cancelled = False

    def run(self, jobs, worker, *, on_result):
        previous = evaluators.set_cancel_event(self._event)
        try:
            for job in jobs:
                try:
                    result = worker(job)
                except KeyboardInterrupt:
                    self.cancel()
                    on_result(job, None, {"code": "INTERRUPTED", "message": "interrupted (Ctrl-C)"})
                    break
                except Exception as exc:  # noqa: BLE001 - a failing job is reported, the next one still runs
                    on_result(job, None, {"code": "WORKER_EXCEPTION", "message": f"{type(exc).__name__}: {exc}"})
                else:
                    on_result(job, result, None)
                if self._event.is_set():  # cancelled during this job (cancel(), or Ctrl-C seen by the evaluator)
                    self.cancelled = True
                    break
        finally:
            evaluators.set_cancel_event(previous)

    def cancel(self):
        self.cancelled = True
        self._event.set()

    def reset(self):
        """Withdraw a cancellation so the scheduler runs jobs again (call it between ``run`` calls only)."""
        self.cancelled = False
        self._event.clear()

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def worker_environment(blas_threads):
    """Environment variables of a worker process: one catalog check by the parent, a fixed thread count."""
    env = {"OPENSCI_CATALOG_CHECK_INTERVAL": "0"}
    if blas_threads is not None:
        env.update({name: str(int(blas_threads)) for name in THREAD_VARIABLES})
    return env


def _worker_main(conn, environment, cancel_event, warm):
    """Entry point of a worker process: warm up, then run jobs sent over ``conn`` until it closes."""
    import signal

    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)  # the parent decides how an interrupted study stops
    except (ValueError, OSError):
        pass
    sys.stdout = sys.stderr  # stdout carries only the calling tool's JSON document
    os.environ.update(environment)
    evaluators.set_cancel_event(cancel_event)
    started = time.perf_counter()
    problem = None
    for name in warm:
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - reported to the parent; the first job then shows the cause
            problem = f"{name}: {type(exc).__name__}: {exc}"
            break
    try:
        conn.send(("ready", os.getpid(), time.perf_counter() - started, problem))
        while True:
            message = conn.recv()
            if message is None:
                break
            function, job = message
            try:
                result = function(job)
            except Exception as exc:  # noqa: BLE001 - the job failed; the worker stays usable
                conn.send(("error", f"{type(exc).__name__}: {exc}"))
            else:
                conn.send(("result", result))
    except (EOFError, OSError, KeyboardInterrupt):
        pass
    finally:
        conn.close()


class _Worker:
    def __init__(self, index, process, conn):
        self.index, self.process, self.conn = index, process, conn
        self.pid = process.pid
        self.ready = False
        self.job = None
        self.job_started = None  # monotonic time the current job started (or the worker became ready)
        self.startup_s = None
        self.spawned = time.perf_counter()


class ProcessScheduler:
    """A persistent pool of ``workers`` processes (module docstring)."""

    name = "process"
    in_process = False

    def __init__(self, workers, *, blas_threads=1, grace_s=DEFAULT_GRACE_S, warm=WARM_MODULES, job_timeout_s=None):
        if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
            raise wc.WorkflowError("BAD_ARGUMENT", f"workers must be a positive integer, got {workers!r}")
        self.workers = workers
        self.blas_threads = blas_threads
        self.grace_s = float(grace_s)
        #: a job running longer than this (seconds after its worker was ready) is stopped by terminating the worker
        self.job_timeout_s = None if job_timeout_s is None else float(job_timeout_s)
        self.warm = tuple(warm)
        self.cancelled = False
        self.startup = []  # {"worker", "startup_s", "problem"} of every worker that became ready
        self._context = multiprocessing.get_context(START_METHOD)
        self._event = self._context.Event()
        self._pool = []
        self._spawned = 0

    # ---------------------------------------------------------------------------------------- pool
    def _spawn(self):
        parent_conn, child_conn = self._context.Pipe()
        environment = worker_environment(self.blas_threads)
        self._spawned += 1
        process = self._context.Process(target=_worker_main, name=f"os-optics-kit-study-{self._spawned}",
                                        args=(child_conn, environment, self._event, self.warm), daemon=True)
        saved = {name: os.environ.get(name) for name in environment}
        os.environ.update(environment)  # inherited by the child from its first instruction
        try:
            process.start()
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        child_conn.close()
        worker = _Worker(self._spawned, process, parent_conn)
        self._pool.append(worker)
        return worker

    def _retire(self, worker, *, terminate=False):
        if worker in self._pool:
            self._pool.remove(worker)
        if terminate and worker.process.is_alive():
            worker.process.terminate()
        try:
            worker.conn.close()
        except OSError:
            pass
        worker.process.join(timeout=5.0)

    def _free_worker(self):
        for worker in list(self._pool):
            if worker.job is None:
                if worker.process.is_alive():
                    return worker
                self._retire(worker)
        return self._spawn() if len(self._pool) < self.workers else None

    # ---------------------------------------------------------------------------------------- run
    def run(self, jobs, worker, *, on_result):
        jobs = iter(jobs)
        exhausted = False
        waiting = None  # a job taken from the iterator that no worker has accepted yet
        cancelled_at = None
        while True:
            try:
                if not self.cancelled and self._event.is_set():  # cancelled from inside a worker
                    self.cancelled = True
                if self.cancelled and cancelled_at is None:
                    cancelled_at = time.monotonic()
                while not self.cancelled and (waiting is not None or not exhausted):
                    free = self._free_worker()
                    if free is None:
                        break
                    if waiting is None:
                        waiting = next(jobs, None)
                        if waiting is None:
                            exhausted = True
                            break
                    try:
                        free.conn.send((worker, waiting))
                    except (OSError, ValueError):
                        self._retire(free, terminate=True)
                        continue
                    free.job, waiting = waiting, None
                    free.job_started = time.monotonic() if free.ready else None
                busy = [w for w in self._pool if w.job is not None]
                if not busy:
                    if self.cancelled or (exhausted and waiting is None):
                        return
                    continue
                if cancelled_at is not None and time.monotonic() - cancelled_at > self.grace_s:
                    for late in busy:
                        if _has_message(late):  # it answered in time: _collect takes the answer
                            continue
                        job, late.job = late.job, None
                        self._retire(late, terminate=True)
                        on_result(job, None, {"code": "CANCELLED", "message": "the worker was terminated: the "
                                              f"evaluation did not stop within {self.grace_s:g} s"})
                self._expire(busy, on_result)
                self._collect([w for w in busy if w.job is not None], on_result)
            except KeyboardInterrupt:
                self.cancel()

    def _collect(self, busy, on_result):
        """Take the answers of busy workers (waiting up to 0.25 s for one) and report workers that died."""
        conns = {w.conn: w for w in busy}
        ready = []
        handles = list(conns)
        for start in range(0, len(handles), WAIT_SLICE):  # Windows waits on at most 63 handles per call
            ready += multiprocessing.connection.wait(handles[start:start + WAIT_SLICE],
                                                     timeout=0.25 if start == 0 else 0)
        seen = [conns[conn] for conn in ready]
        seen += [w for w in busy if w not in seen and not w.process.is_alive()]
        for worker in seen:
            while worker.job is not None:
                try:
                    if not worker.conn.poll():
                        if not worker.process.is_alive() and not worker.conn.poll():
                            self._lost(worker, on_result)
                        break
                    message = worker.conn.recv()
                except (EOFError, OSError):
                    self._lost(worker, on_result)
                    break
                if message[0] == "ready":
                    worker.ready, worker.pid, worker.startup_s = True, message[1], message[2]
                    worker.job_started = time.monotonic()
                    self.startup.append({"worker": f"pid-{message[1]}", "startup_s": round(message[2], 3),
                                         "problem": message[3]})
                    continue
                job, worker.job = worker.job, None
                if message[0] == "result":
                    on_result(job, message[1], None)
                else:
                    on_result(job, None, {"code": "WORKER_EXCEPTION", "message": message[1]})

    def _expire(self, busy, on_result):
        """Terminate the workers whose job passed ``job_timeout_s`` (the hard stop behind the per-run time limit)."""
        if self.job_timeout_s is None:
            return
        now = time.monotonic()
        for late in busy:
            if (late.job is not None and late.job_started is not None and now - late.job_started > self.job_timeout_s
                    and not _has_message(late)):
                job, late.job = late.job, None
                self._retire(late, terminate=True)
                on_result(job, None, {"code": "TIME_LIMIT", "message": "the worker was terminated: the evaluation ran "
                                      f"longer than {self.job_timeout_s:g} s"})

    def _lost(self, worker, on_result):
        job, worker.job = worker.job, None
        self._retire(worker, terminate=True)
        code = worker.process.exitcode
        if job is not None and not worker.ready and (self.cancelled or self._event.is_set()):
            # Ctrl-C reaches a starting worker before it ignores it: the job was cancelled, not lost
            on_result(job, None, {"code": "CANCELLED", "message": "the study was cancelled while the worker process "
                                  f"was starting (exit code {code})"})
            return
        if job is not None:
            message = f"the worker process ended (exit code {code}) while it was evaluating this sample"
            if not worker.ready:
                message = (f"the worker process ended (exit code {code}) before it was ready; its stderr names the "
                           "cause. A script that runs a study with worker processes must make its calls under "
                           "if __name__ == \"__main__\": (each worker imports the script)")
            on_result(job, None, {"code": "WORKER_LOST", "message": message})

    # ---------------------------------------------------------------------------------------- control
    def cancel(self):
        self.cancelled = True
        self._event.set()

    def reset(self):
        """Withdraw a cancellation so the pool runs jobs again (call it between ``run`` calls only)."""
        self.cancelled = False
        self._event.clear()

    def close(self):
        """Stop the worker processes (idle ones exit; a busy one is terminated)."""
        for worker in list(self._pool):
            if worker.job is None:
                try:
                    worker.conn.send(None)
                except (OSError, ValueError):
                    pass
            self._retire(worker, terminate=worker.job is not None)
        for worker in list(self._pool):
            self._retire(worker, terminate=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def make_scheduler(scheduler=None, workers=1, *, blas_threads=None, job_timeout_s=None):
    """A scheduler from a name or an object.

    ``None``: serial for one worker, a process pool otherwise. ``"serial"`` / ``"process"``: that kind (a
    one-worker process pool runs the samples outside the calling process). An object with ``run`` and ``cancel``
    is returned as it is. ``job_timeout_s``: process pool only, a job still running that long after its worker was
    ready is stopped by terminating the worker."""
    if scheduler is not None and not isinstance(scheduler, str):
        if not callable(getattr(scheduler, "run", None)) or not callable(getattr(scheduler, "cancel", None)):
            raise wc.WorkflowError("BAD_ARGUMENT", "a scheduler object needs run(jobs, worker, *, on_result) and "
                                   "cancel()")
        return scheduler
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise wc.WorkflowError("BAD_ARGUMENT", f"workers must be a positive integer, got {workers!r}")
    name = scheduler or ("serial" if workers == 1 else "process")
    if name == "serial":
        if workers != 1:
            raise wc.WorkflowError("BAD_ARGUMENT", "the serial scheduler runs one sample at a time; use "
                                   "--scheduler process for more workers")
        return SerialScheduler()
    if name == "process":
        return ProcessScheduler(workers, blas_threads=1 if blas_threads is None else blas_threads,
                                job_timeout_s=job_timeout_s)
    raise wc.WorkflowError("BAD_ARGUMENT", f"unknown scheduler {name!r}; known: serial, process")
