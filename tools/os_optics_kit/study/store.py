"""The study directory: plan, requests, per-sample records, journal and study identity.

Layout (``samples/``, ``table.csv`` and ``summary.json`` are what the plot, report, compare and present tools read)::

    STUDY/
      study.json           the planning tool's keys + "schema", "schema_version", "identity", "execution"
      plan.jsonl           one line per request in plan order: {sample_id, request_hash, labels, role}
      requests/<id>.json   the full request (requests.py)
      samples/<id>/        project.json, run/ (Engine run directory), sample.json (the sample record)
      journal.jsonl        append-only events: planned, started, finished, failed, cancelled, reused, ...
      owner.json           while an execution runs: its process (pid, host, process start), start time and id
      owner.lock           taken for a moment by whoever takes ownership (never removed)
      table.csv, summary.json   written by the aggregation step, rows in plan order

Writers: one execution at a time owns a study (``acquire_execution``: a second one is refused with
``STUDY_RUNNING`` while the owner's process lives, and takes over a record whose process is gone; ``owner.lock``
makes taking ownership atomic); planning (``plan_study``, the removals of ``prepare_study_dir``) and aggregation
(``aggregate.finish_study``) hold the ownership while they write, so they are refused while an execution runs. The
process that evaluates a sample is the only writer of that sample's directory; the owning process (the parent) is
the only writer of every other file. Files are written to a uniquely named temporary file and moved into place, so a reader never
sees half a file and ``samples/<id>/sample.json`` marks a finished sample. Every sample id read from ``plan.jsonl``
or a request is checked before it names a path.

``identity`` = ``{plan_hash, engine {version, build_id}, catalog {version, sha256}, kit_version}``. A sample is
reused on resume when its ``sample.json`` carries the plan's ``request_hash``, its ``state`` is ``COMPLETED`` or
``REFUSED`` and the same evaluator produced it; a changed Engine build or catalog release refuses the run or the
resume. A record kept for an unchanged request takes the labels of the current plan.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import time
import uuid

from .. import workflow_common as wc
from .._version import __version__
from .requests import valid_sample_id, validate_request

STUDY_SCHEMA = "os-optics-kit.study"
STUDY_SCHEMA_VERSION = 1
STATES = ("COMPLETED", "REFUSED", "FAILED", "CANCELLED", "NOT_RUN")
#: States whose record is final for its request: reused on resume, never run again.
REUSABLE_STATES = ("COMPLETED", "REFUSED")
STUDY_FILE, PLAN_FILE, JOURNAL_FILE = "study.json", "plan.jsonl", "journal.jsonl"
REQUESTS_DIR, SAMPLES_DIR = "requests", "samples"
SAMPLE_FILE, ATTEMPT_FILE = "sample.json", "attempt.json"
OWNER_FILE, OWNER_LOCK = "owner.json", "owner.lock"
#: What a plan owns in a study directory (removed by an overwrite, next to the planning tool's own files).
PLAN_FILES = (PLAN_FILE, JOURNAL_FILE)
PLAN_DIRS = (REQUESTS_DIR,)


# --------------------------------------------------------------------------------------------- paths

def _checked(sample_id):
    if not valid_sample_id(sample_id):
        raise wc.WorkflowError("PLAN_INVALID", f"sample id {sample_id!r} cannot name a sample directory")
    return sample_id


def sample_dir(study_dir, sample_id):
    return Path(study_dir) / SAMPLES_DIR / _checked(sample_id)


def request_path(study_dir, sample_id):
    return Path(study_dir) / REQUESTS_DIR / f"{_checked(sample_id)}.json"


def sample_path(study_dir, sample_id):
    return sample_dir(study_dir, sample_id) / SAMPLE_FILE


# --------------------------------------------------------------------------------------------- atomic writers

def write_text_atomic(path, text):
    """Write ``text`` (LF) through a uniquely named temporary file next to ``path``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}-{uuid.uuid4().hex[:12]}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return path
        except PermissionError:  # the target is open in another process for a moment (Windows)
            if attempt == 19:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.05)
    return path


def write_json_atomic(path, document):
    return write_text_atomic(path, wc.json_text(document))


def remove_tree(path):
    """Remove a directory this study owns; missing is fine."""
    shutil.rmtree(path, ignore_errors=True)


# --------------------------------------------------------------------------------------------- journal

def timestamp():
    now = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)) + f".{int((now % 1) * 1000):03d}Z"


def append_journal(study_dir, event, sample_id=None, **fields):
    """Append one event line to ``journal.jsonl`` (called by the study's owning process only)."""
    entry = {"t": timestamp(), "event": event}
    if sample_id is not None:
        entry["sample_id"] = sample_id
    entry.update({key: value for key, value in fields.items() if value is not None})
    path = Path(study_dir) / JOURNAL_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(wc.json_safe(entry), ensure_ascii=True, allow_nan=False,
                                separators=(",", ":")) + "\n")
    return entry


def read_journal(study_dir):
    """Every journal event in order (an unfinished last line is ignored)."""
    path = Path(study_dir) / JOURNAL_FILE
    if not path.is_file():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events


# --------------------------------------------------------------------------------------------- plan

def plan_entry(request):
    labels = request.get("labels") or {}
    return {"sample_id": request["sample_id"], "request_hash": request["request_hash"], "labels": labels,
            "role": labels.get("role", "sample")}


def plan_hash(entries):
    """SHA-256 over the ordered (sample_id, request_hash) pairs of a plan."""
    return wc.canonical_digest([[entry["sample_id"], entry["request_hash"]] for entry in entries])


def write_plan(study_dir, requests):
    """Write ``requests/<id>.json`` and ``plan.jsonl`` (plan order = list order); return the plan entries.

    Refuses duplicate sample ids. Sample directories and request files of ids that are no longer planned are
    removed, so the directory describes exactly this plan."""
    study_dir = Path(study_dir)
    entries, seen = [], set()
    for request in requests:
        validate_request(request)
        if request["sample_id"] in seen:
            raise wc.WorkflowError("PLAN_INVALID", f"duplicate sample id {request['sample_id']!r}")
        seen.add(request["sample_id"])
        entries.append(plan_entry(request))
    if not entries:
        raise wc.WorkflowError("PLAN_INVALID", "a plan needs at least one request")
    for folder, wanted in ((study_dir / REQUESTS_DIR, {f"{sid}.json" for sid in seen}),
                           (study_dir / SAMPLES_DIR, seen)):
        if folder.is_dir():
            for stale in sorted(folder.iterdir()):
                if stale.name not in wanted and not stale.name.startswith("."):
                    if stale.is_dir():
                        remove_tree(stale)
                    else:
                        stale.unlink(missing_ok=True)
    for request in requests:
        path = request_path(study_dir, request["sample_id"])
        text = wc.json_text(request)
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            if path.is_file():
                _relabel_record(study_dir, path, request)
            write_text_atomic(path, text)
    lines = [json.dumps(wc.json_safe(entry), ensure_ascii=True, allow_nan=False, separators=(",", ":"))
             for entry in entries]
    write_text_atomic(study_dir / PLAN_FILE, "\n".join(lines) + "\n")
    return entries


def _relabel_record(study_dir, old_path, request):
    """Give the saved record of an unchanged request the labels of the new plan (a reused sample then reads like
    one evaluated for this plan: labels are bookkeeping, not part of the request hash)."""
    try:
        old = wc.read_json(old_path)
    except (OSError, ValueError):
        return
    if not isinstance(old, dict) or old.get("request_hash") != request["request_hash"]:
        return
    old_labels, labels = old.get("labels") or {}, request["labels"]
    record = read_record(study_dir, request["sample_id"])
    if old_labels == labels or record is None or record.get("request_hash") != request["request_hash"]:
        return
    record = {key: value for key, value in record.items() if key not in old_labels}
    record.update(labels)
    write_json_atomic(sample_path(study_dir, request["sample_id"]), record)


def read_plan(study_dir):
    """The plan entries in plan order; refuses a directory without a plan."""
    path = Path(study_dir) / PLAN_FILE
    if not path.is_file():
        raise wc.WorkflowError("STUDY_NOT_PLANNED", f"{Path(study_dir).as_posix()} holds no plan.jsonl; plan the "
                               "study first (sweep / sensitivity / tolerance --plan-only, study candidates, or "
                               "os_optics_kit.study.plan_study)", study_dir=Path(study_dir).as_posix())
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise wc.WorkflowError("PLAN_INVALID", "plan.jsonl is not UTF-8 text; plan the study again with "
                               "--overwrite") from None
    entries, seen = [], set()
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            entry = None
        if (not isinstance(entry, dict) or not valid_sample_id(entry.get("sample_id"))
                or not isinstance(entry.get("request_hash"), str) or not isinstance(entry.get("labels", {}), dict)):
            raise wc.WorkflowError("PLAN_INVALID", f"plan.jsonl line {number} is not a plan entry with a valid "
                                   "sample_id and request_hash; plan the study again with --overwrite", line=number)
        if entry["sample_id"] in seen:
            raise wc.WorkflowError("PLAN_INVALID", f"plan.jsonl line {number} repeats sample id "
                                   f"{entry['sample_id']!r}; plan the study again with --overwrite", line=number)
        seen.add(entry["sample_id"])
        entries.append(entry)
    if not entries:
        raise wc.WorkflowError("PLAN_INVALID", "plan.jsonl lists no request")
    return entries


def read_request(study_dir, sample_id):
    """The saved request of ``sample_id``, validated (its hash must match its content, its id must be
    ``sample_id``)."""
    path = request_path(study_dir, sample_id)
    if not path.is_file():
        raise wc.WorkflowError("REQUEST_MISSING", f"requests/{sample_id}.json is missing", sample_id=sample_id)
    try:
        saved = wc.read_json(path)
    except ValueError:
        raise wc.WorkflowError("REQUEST_INVALID", f"requests/{sample_id}.json is not a JSON request; {REPLAN_HINT}",
                               sample_id=sample_id) from None
    request = validate_request(saved)
    if request["sample_id"] != sample_id:
        raise wc.WorkflowError("REQUEST_INVALID", f"requests/{sample_id}.json holds the request of "
                               f"{request['sample_id']!r}", sample_id=sample_id)
    return request


# --------------------------------------------------------------------------------------------- identity

def study_identity(entries, engine, catalog):
    """``identity`` block of ``study.json`` from the plan, ``wc.engine_identity()`` and ``wc.catalog_identity()``."""
    return {"plan_hash": plan_hash(entries),
            "engine": {"version": engine.get("engine_version"), "build_id": engine.get("engine_build_id")},
            "catalog": {"version": catalog.get("catalog_version"), "sha256": catalog.get("sha256")},
            "kit_version": __version__}


def identity_differences(saved, current, *, plan=True):
    """Names of the identity fields that differ (``plan_hash``, ``engine.build_id``, ``catalog.version``, ...)."""
    saved, current = saved or {}, current or {}
    fields = []
    if plan and saved.get("plan_hash") != current.get("plan_hash"):
        fields.append("plan_hash")
    for group, keys in (("engine", ("version", "build_id")), ("catalog", ("version", "sha256"))):
        for key in keys:
            if (saved.get(group) or {}).get(key) != (current.get(group) or {}).get(key):
                fields.append(f"{group}.{key}")
    return fields


#: How a study is planned again from scratch (the planning commands take --overwrite; ``study run`` does not).
REPLAN_HINT = ("plan it again with its planning command and --overwrite (sweep, sensitivity, tolerance or "
               "study candidates)")


def require_same_identity(saved, current, *, plan=True, study_dir=None, resuming=True):
    """Refuse a run or a resume (``STUDY_IDENTITY_CHANGED``) when the saved identity differs from the current one."""
    fields = identity_differences(saved, current, plan=plan)
    if fields:
        what = "resumed" if resuming else "run as planned"
        raise wc.WorkflowError(
            "STUDY_IDENTITY_CHANGED", f"the study cannot be {what}: {', '.join(fields)} of study.json differ from "
            f"the plan and the installed Engine and catalog; {REPLAN_HINT} to start it with the installed Engine "
            "and catalog", changed=fields, saved=saved, current=current,
            study_dir=None if study_dir is None else Path(study_dir).as_posix())


def read_study(study_dir):
    """``study.json`` of a planned study (``STUDY_NOT_PLANNED`` when the directory holds none with an identity)."""
    path = Path(study_dir) / STUDY_FILE
    study = wc.read_json(path) if path.is_file() else None
    if not isinstance(study, dict) or not isinstance(study.get("identity"), dict):
        raise wc.WorkflowError("STUDY_NOT_PLANNED", f"{Path(study_dir).as_posix()} is not a planned study directory "
                               "(study.json with an identity block); plan it with sweep / sensitivity / tolerance "
                               "--plan-only or study candidates --plan-only", study_dir=Path(study_dir).as_posix())
    if study.get("schema") != STUDY_SCHEMA or study.get("schema_version") != STUDY_SCHEMA_VERSION:
        raise wc.WorkflowError("STUDY_SCHEMA_UNSUPPORTED", f"study.json schema {study.get('schema')!r} version "
                               f"{study.get('schema_version')!r}; this kit reads {STUDY_SCHEMA} "
                               f"{STUDY_SCHEMA_VERSION}")
    return study


def write_study(study_dir, study):
    return write_json_atomic(Path(study_dir) / STUDY_FILE, study)


def has_study(study_dir):
    """Whether ``study_dir`` already holds a planned study (``study.json`` with an identity and a plan)."""
    study_dir = Path(study_dir)
    if not (study_dir / STUDY_FILE).is_file() or not (study_dir / PLAN_FILE).is_file():
        return False
    try:
        return isinstance(wc.read_json(study_dir / STUDY_FILE).get("identity"), dict)
    except (ValueError, AttributeError):
        return False


def study_header(kind, **keys):
    """``study.json`` keys every study carries: ``kind``, the caller's ``keys``, the installed Engine and catalog
    identity, the environment and the start time (pass the result to ``plan_study``)."""
    return {"kind": kind, **keys, "engine": wc.engine_identity(), "installed_catalog": wc.catalog_identity(),
            "catalog": wc.run_catalog_identity([]), "environment": wc.environment(),
            "started": keys.get("started") or wc.utc_now()}


def prepare_study_dir(study_dir, *, overwrite=False, resume=False, files=(), dirs=()):
    """A study directory ready for planning: new or empty; with ``resume`` an existing planned study is kept; with
    ``overwrite`` ``files`` / ``dirs`` (the planning tool's outputs) and the plan files are removed first.
    Returns ``(path, resuming)``."""
    study_dir = Path(study_dir)
    if study_dir.exists() and any(item.name != OWNER_LOCK for item in study_dir.iterdir()):
        require_not_running(study_dir)
        if resume and not overwrite and has_study(study_dir):
            return study_dir, True
        if not overwrite:
            hint = "it holds no planned study to resume; " if resume else ""
            raise wc.WorkflowError("OUTPUT_EXISTS", f"{study_dir} is not empty; {hint}choose a new directory, "
                                   "--resume a planned study or use --overwrite", out=study_dir.as_posix())
        with owned(study_dir):  # an execution that starts meanwhile is refused, and this removal is refused by one
            for name in (*dirs, *PLAN_DIRS, SAMPLES_DIR):
                remove_tree(study_dir / name)
            for name in (*files, *PLAN_FILES, STUDY_FILE):
                (study_dir / name).unlink(missing_ok=True)
    study_dir.mkdir(parents=True, exist_ok=True)
    return study_dir, False


def plan_study(study_dir, header, requests, *, resume=False):
    """Write a planned study: ``study.json`` (``header`` + schema, identity, execution), requests and plan.

    ``header``: the planning tool's own ``study.json`` keys; it must hold ``engine`` (``wc.engine_identity()``) and
    ``installed_catalog`` (``wc.catalog_identity()``). With ``resume`` and an existing study in ``study_dir``, the
    saved Engine build and catalog release must equal the current ones (``STUDY_IDENTITY_CHANGED`` otherwise) and
    the saved ``kind`` must equal the header's; the plan is replaced, and samples whose request is unchanged are
    reused by the next execution. Refused (``STUDY_RUNNING``) while an execution runs: it holds the study's
    ownership while it writes. Returns ``(study, entries)``."""
    study_dir = Path(study_dir)
    entries = [plan_entry(validate_request(request)) for request in requests]
    identity = study_identity(entries, header.get("engine") or {}, header.get("installed_catalog") or {})
    study_dir.mkdir(parents=True, exist_ok=True)
    with owned(study_dir):
        previous = None
        if resume and has_study(study_dir):
            previous = read_study(study_dir)
            if previous.get("kind") != header.get("kind"):
                raise wc.WorkflowError("STUDY_KIND_CHANGED", f"{study_dir.as_posix()} holds a study of kind "
                                       f"{previous.get('kind')!r}, not {header.get('kind')!r}; choose another "
                                       "directory or --overwrite", study_dir=study_dir.as_posix())
            require_same_identity(previous["identity"], identity, plan=False, study_dir=study_dir)
        study = {**header, "schema": STUDY_SCHEMA, "schema_version": STUDY_SCHEMA_VERSION, "identity": identity,
                 "execution": {"state": "PLANNED"}}
        if previous is not None:
            study["started"] = previous.get("started", study.get("started"))
            if isinstance(previous.get("execution"), dict):
                study["execution"] = {**previous["execution"], "state": "PLANNED"}
        write_study(study_dir, study)
        entries = write_plan(study_dir, requests)
        append_journal(study_dir, "planned", samples=len(entries), plan_hash=identity["plan_hash"],
                       replaced_plan=bool(previous is not None
                                          and previous["identity"].get("plan_hash") != identity["plan_hash"]) or None)
    return study, entries


def clear_plan(study_dir):
    """Remove the plan files of a study directory (part of an overwrite)."""
    study_dir = Path(study_dir)
    for name in PLAN_DIRS:
        remove_tree(study_dir / name)
    for name in PLAN_FILES:
        (study_dir / name).unlink(missing_ok=True)


# --------------------------------------------------------------------------------------------- sample records

def read_record(study_dir, sample_id):
    """The saved record of a sample, or None when it has none or an unreadable one (not JSON, or without a job
    ``state`` and a requirement ``outcome`` of the known values: a damaged record is run again)."""
    path = sample_path(study_dir, sample_id)
    if not path.is_file():
        return None
    try:
        record = wc.read_json(path)
    except ValueError:
        return None
    if not isinstance(record, dict) or record.get("state") not in STATES or record.get("outcome") not in wc.OUTCOMES:
        return None
    return record


def reusable_record(study_dir, entry, evaluator):
    """The record of a finished sample of this plan entry (same ``request_hash``, final state, produced by
    ``evaluator``; a planner refusal involves no evaluator), else None. An evaluator without a name (None: a
    function that cannot be imported by ``module:function``) has no identity, so none of its records is reused."""
    record = read_record(study_dir, entry["sample_id"])
    if record is None or record.get("request_hash") != entry["request_hash"]:
        return None
    if record.get("state") not in REUSABLE_STATES:
        return None
    if record.get("worker") != "planner" and (evaluator is None or record.get("evaluator") != evaluator):
        return None
    return record


def write_attempt(study_dir, sample_id, request_hash, attempt, *, evaluator=None, execution=None):
    """Mark that an evaluation of this request starts (written by the evaluating process): request hash, attempt,
    the evaluator's name and the id of the execution that owns the study."""
    return write_json_atomic(sample_dir(study_dir, sample_id) / ATTEMPT_FILE,
                             {"request_hash": request_hash, "attempt": attempt, "evaluator": evaluator,
                              "execution": execution, "started": timestamp()})


def read_attempt(study_dir, sample_id):
    path = sample_dir(study_dir, sample_id) / ATTEMPT_FILE
    if not path.is_file():
        return None
    try:
        return wc.read_json(path)
    except ValueError:
        return None


def recoverable_run(study_dir, entry, evaluator):
    """True when the sample has no record but a complete run directory written for this request by ``evaluator``
    (an evaluation that was interrupted after the Engine finished; never for an evaluator without a name)."""
    if evaluator is None or read_record(study_dir, entry["sample_id"]) is not None:
        return False
    attempt = read_attempt(study_dir, entry["sample_id"])
    if (not isinstance(attempt, dict) or attempt.get("request_hash") != entry["request_hash"]
            or attempt.get("evaluator") != evaluator):
        return False
    return (sample_dir(study_dir, entry["sample_id"]) / "run" / "manifest.json").is_file()


def resume_selection(study_dir, entries, evaluator):
    """Split a plan into ``(reused, recoverable, pending)`` for an execution with ``evaluator`` (its
    ``module:function`` name): entries with a final record of the same request by the same evaluator, entries whose
    complete run directory can be read back, and entries that have to run."""
    reused, recoverable, pending = [], [], []
    for entry in entries:
        if reusable_record(study_dir, entry, evaluator) is not None:
            reused.append(entry)
        elif recoverable_run(study_dir, entry, evaluator):
            recoverable.append(entry)
        else:
            pending.append(entry)
    return reused, recoverable, pending


def has_results(study_dir):
    """Whether any sample of the study directory has a record or an attempt marker."""
    folder = Path(study_dir) / SAMPLES_DIR
    if not folder.is_dir():
        return False
    return any((item / SAMPLE_FILE).is_file() or (item / ATTEMPT_FILE).is_file() for item in folder.iterdir())


# --------------------------------------------------------------------------------------------- ownership

#: Seconds an unreadable owner file is taken to be one another process is writing right now.
OWNER_WRITE_GRACE_S = 10.0


def _windows_process(pid):
    """``(alive, creation time)`` of a Windows process (``(False, None)`` when there is none)."""
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ctypes.get_last_error() == 5, None  # ERROR_ACCESS_DENIED: it exists, owned by someone else
    try:
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:  # STILL_ACTIVE
            return False, None
        times = [wintypes.FILETIME() for _ in range(4)]
        if kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return True, (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        return True, None
    finally:
        kernel.CloseHandle(handle)


def process_state(pid):
    """``(alive, start token)`` of a process on this machine; the token (the process's start time where the system
    tells it, else None) tells a process from a later one that reuses its id."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False, None
    if sys.platform == "win32":
        try:
            return _windows_process(pid)
        except (AttributeError, OSError):
            return True, None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False, None
    except PermissionError:
        pass
    except OSError:
        return True, None
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii", errors="replace") as handle:
            return True, int(handle.read().rsplit(")", 1)[1].split()[19])  # field 22: start time in clock ticks
    except (OSError, ValueError, IndexError):
        return True, None


def owner_path(study_dir):
    return Path(study_dir) / OWNER_FILE


def read_owner(study_dir):
    """The ownership record of the execution in ``study_dir``, or None."""
    path = owner_path(study_dir)
    if not path.is_file():
        return None
    try:
        owner = wc.read_json(path)
    except (OSError, ValueError):
        return {}
    return owner if isinstance(owner, dict) else {}


def owner_alive(owner):
    """Whether the process named by an ownership record still runs (True for a record of another host or one
    being written: it cannot be checked from here)."""
    if not owner or owner.get("host") != socket.gethostname():
        return True
    alive, token = process_state(owner.get("pid"))
    saved = owner.get("process_started")
    return alive and (saved is None or token is None or saved == token)


def _running_error(study_dir, owner):
    study_dir = Path(study_dir)
    who = (f"process {owner.get('pid')} on {owner.get('host')} since {owner.get('started')}" if owner
           else "another process")
    hint = "wait for it to finish or stop it, then continue with --resume"
    if owner and owner.get("host") != socket.gethostname():
        hint += f"; if that process no longer runs, delete {OWNER_FILE}"
    return wc.WorkflowError("STUDY_RUNNING", f"{study_dir.as_posix()} is being executed by {who}; {hint}",
                            study_dir=study_dir.as_posix(), owner=owner or None)


def _owner_stale(study_dir, owner):
    if owner == {}:  # unreadable: another process may be writing it right now
        try:
            age = time.time() - owner_path(study_dir).stat().st_mtime
        except OSError:
            return True
        return age > OWNER_WRITE_GRACE_S
    return owner is None or not owner_alive(owner)


def require_not_running(study_dir):
    """Refuse (``STUDY_RUNNING``) while a live process executes the study in ``study_dir``."""
    owner = read_owner(study_dir)
    if owner is not None and not _owner_stale(study_dir, owner):
        raise _running_error(study_dir, owner)


#: Seconds a caller waits for ``owner.lock`` (held only while another caller takes ownership).
TAKEOVER_WAIT_S = 10.0


def _sharing_retry(action, budget_s=5.0):
    """``action()``, retried for up to ``budget_s`` seconds while Windows refuses it because another process has
    the file open for a moment (a reader of ``owner.json``, or a removal not finished yet)."""
    deadline = time.monotonic() + budget_s
    while True:
        try:
            return action()
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def _try_lock(fd):
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fd):
    if sys.platform == "win32":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


@contextlib.contextmanager
def _takeover_lock(study_dir):
    """Hold ``owner.lock`` (an operating-system lock that ends with its process): one caller at a time reads,
    replaces and writes ``owner.json``. The file is never removed (removing it would let two callers lock two
    files)."""
    fd = _sharing_retry(lambda: os.open(Path(study_dir) / OWNER_LOCK, os.O_CREAT | os.O_RDWR))
    try:
        deadline = time.monotonic() + TAKEOVER_WAIT_S
        while True:
            try:
                _try_lock(fd)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise _running_error(study_dir, read_owner(study_dir)) from None
                time.sleep(0.01)
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


def acquire_execution(study_dir):
    """Take ownership of ``study_dir`` for one execution: write ``owner.json`` (pid, host, process start, time, id).
    A record whose process is gone is taken over; a live one refuses with ``STUDY_RUNNING``. Callers take turns
    under ``owner.lock``, so of two callers that find the same gone owner exactly one takes over. Returns the
    record."""
    path = owner_path(study_dir)
    _alive, token = process_state(os.getpid())
    owner = {"pid": os.getpid(), "host": socket.gethostname(), "process_started": token, "started": timestamp(),
             "id": uuid.uuid4().hex}
    with _takeover_lock(study_dir):
        current = read_owner(study_dir)
        if current is not None:
            if not _owner_stale(study_dir, current):
                raise _running_error(study_dir, current)
            _sharing_retry(lambda: path.unlink(missing_ok=True))  # the record of a process that is gone
        try:
            fd = _sharing_retry(lambda: os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        except FileExistsError:  # written by a caller that does not take the lock (another kit version)
            raise _running_error(study_dir, read_owner(study_dir)) from None
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(wc.json_text(owner))
    return owner


def release_execution(study_dir, owner):
    """Remove the ownership record of this execution (one taken over by another process is left alone)."""
    current = read_owner(study_dir)
    if current and owner and current.get("id") == owner.get("id"):
        _sharing_retry(lambda: owner_path(study_dir).unlink(missing_ok=True))


@contextlib.contextmanager
def owned(study_dir):
    """Hold the ownership of ``study_dir`` for a block of writes (``STUDY_RUNNING`` while an execution runs)."""
    owner = acquire_execution(study_dir)
    try:
        yield owner
    finally:
        release_execution(study_dir, owner)
