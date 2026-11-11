"""The study directory: plan, requests, per-sample records, journal and study identity.

Layout (``samples/``, ``table.csv`` and ``summary.json`` are what the plot, report, compare and present tools read)::

    STUDY/
      study.json           the planning tool's keys + "schema", "schema_version", "identity", "execution"
      plan.jsonl           one line per request in plan order: {sample_id, request_hash, labels, role}
      requests/<id>.json   the full request (requests.py)
      samples/<id>/        project.json, run/ (Engine run directory), sample.json (the sample record)
      journal.jsonl        append-only events: planned, started, finished, failed, cancelled, reused, ...
      table.csv, summary.json   written by the aggregation step, rows in plan order

Writers: the process that evaluates a sample is the only writer of that sample's directory; the process that owns the
study (the parent) is the only writer of every other file. Files are written to a uniquely named temporary file and
moved into place, so a reader never sees half a file and ``samples/<id>/sample.json`` marks a finished sample.

``identity`` = ``{plan_hash, engine {version, build_id}, catalog {version, sha256}, kit_version}``. A sample is
reused on resume when its ``sample.json`` carries the plan's ``request_hash`` and its ``state`` is ``COMPLETED`` or
``REFUSED``; a changed Engine build or catalog release refuses the resume.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import time
import uuid

from .. import workflow_common as wc
from .._version import __version__
from .requests import validate_request

STUDY_SCHEMA = "os-optics-kit.study"
STUDY_SCHEMA_VERSION = 1
STATES = ("COMPLETED", "REFUSED", "FAILED", "CANCELLED", "NOT_RUN")
#: States whose record is final for its request: reused on resume, never run again.
REUSABLE_STATES = ("COMPLETED", "REFUSED")
STUDY_FILE, PLAN_FILE, JOURNAL_FILE = "study.json", "plan.jsonl", "journal.jsonl"
REQUESTS_DIR, SAMPLES_DIR = "requests", "samples"
SAMPLE_FILE, ATTEMPT_FILE = "sample.json", "attempt.json"
#: What a plan owns in a study directory (removed by an overwrite, next to the planning tool's own files).
PLAN_FILES = (PLAN_FILE, JOURNAL_FILE)
PLAN_DIRS = (REQUESTS_DIR,)


# --------------------------------------------------------------------------------------------- paths

def sample_dir(study_dir, sample_id):
    return Path(study_dir) / SAMPLES_DIR / sample_id


def request_path(study_dir, sample_id):
    return Path(study_dir) / REQUESTS_DIR / f"{sample_id}.json"


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
            write_text_atomic(path, text)
    lines = [json.dumps(wc.json_safe(entry), ensure_ascii=True, allow_nan=False, separators=(",", ":"))
             for entry in entries]
    write_text_atomic(study_dir / PLAN_FILE, "\n".join(lines) + "\n")
    return entries


def read_plan(study_dir):
    """The plan entries in plan order; refuses a directory without a plan."""
    path = Path(study_dir) / PLAN_FILE
    if not path.is_file():
        raise wc.WorkflowError("STUDY_NOT_PLANNED", f"{Path(study_dir).as_posix()} holds no plan.jsonl; plan the "
                               "study first (sweep / sensitivity / tolerance --plan-only, study candidates, or "
                               "os_optics_kit.study.plan_study)", study_dir=Path(study_dir).as_posix())
    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not entries:
        raise wc.WorkflowError("PLAN_INVALID", "plan.jsonl lists no request")
    return entries


def read_request(study_dir, sample_id):
    """The saved request of ``sample_id``, validated (its hash must match its content)."""
    path = request_path(study_dir, sample_id)
    if not path.is_file():
        raise wc.WorkflowError("REQUEST_MISSING", f"requests/{sample_id}.json is missing", sample_id=sample_id)
    return validate_request(wc.read_json(path))


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


def require_same_identity(saved, current, *, plan=True, study_dir=None):
    """Refuse a resume (``STUDY_IDENTITY_CHANGED``) when the saved identity differs from the current one."""
    fields = identity_differences(saved, current, plan=plan)
    if fields:
        raise wc.WorkflowError(
            "STUDY_IDENTITY_CHANGED", f"the study cannot be resumed: {', '.join(fields)} changed since it was "
            "planned; --overwrite starts it again with the installed Engine and catalog",
            changed=fields, saved=saved, current=current,
            study_dir=None if study_dir is None else Path(study_dir).as_posix())


def read_study(study_dir):
    """``study.json`` of a planned study (``STUDY_NOT_PLANNED`` when the directory holds none with an identity)."""
    path = Path(study_dir) / STUDY_FILE
    study = wc.read_json(path) if path.is_file() else None
    if not isinstance(study, dict) or not isinstance(study.get("identity"), dict):
        raise wc.WorkflowError("STUDY_NOT_PLANNED", f"{Path(study_dir).as_posix()} is not a planned study directory "
                               "(study.json with an identity block)", study_dir=Path(study_dir).as_posix())
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
    if study_dir.exists() and any(study_dir.iterdir()):
        if resume and not overwrite and has_study(study_dir):
            return study_dir, True
        if not overwrite:
            hint = "it holds no planned study to resume; " if resume else ""
            raise wc.WorkflowError("OUTPUT_EXISTS", f"{study_dir} is not empty; {hint}choose a new directory, "
                                   "--resume a planned study or use --overwrite", out=study_dir.as_posix())
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
    reused by the next execution. Returns ``(study, entries)``."""
    study_dir = Path(study_dir)
    entries = [plan_entry(validate_request(request)) for request in requests]
    identity = study_identity(entries, header.get("engine") or {}, header.get("installed_catalog") or {})
    previous = None
    if resume and has_study(study_dir):
        previous = read_study(study_dir)
        if previous.get("kind") != header.get("kind"):
            raise wc.WorkflowError("STUDY_KIND_CHANGED", f"{study_dir.as_posix()} holds a study of kind "
                                   f"{previous.get('kind')!r}, not {header.get('kind')!r}; choose another directory "
                                   "or --overwrite", study_dir=study_dir.as_posix())
        require_same_identity(previous["identity"], identity, plan=False, study_dir=study_dir)
    study = {**header, "schema": STUDY_SCHEMA, "schema_version": STUDY_SCHEMA_VERSION, "identity": identity,
             "execution": {"state": "PLANNED"}}
    if previous is not None:
        study["started"] = previous.get("started", study.get("started"))
        if isinstance(previous.get("execution"), dict):
            study["execution"] = {**previous["execution"], "state": "PLANNED"}
    study_dir.mkdir(parents=True, exist_ok=True)
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
    """The saved record of a sample, or None when it has none (or an unreadable one)."""
    path = sample_path(study_dir, sample_id)
    if not path.is_file():
        return None
    try:
        record = wc.read_json(path)
    except ValueError:
        return None
    return record if isinstance(record, dict) else None


def reusable_record(study_dir, entry):
    """The record of a finished sample of this plan entry (same ``request_hash``, final state), else None."""
    record = read_record(study_dir, entry["sample_id"])
    if record is None or record.get("request_hash") != entry["request_hash"]:
        return None
    return record if record.get("state") in REUSABLE_STATES else None


def write_attempt(study_dir, sample_id, request_hash, attempt):
    """Mark that an evaluation of this request starts (written by the evaluating process)."""
    return write_json_atomic(sample_dir(study_dir, sample_id) / ATTEMPT_FILE,
                             {"request_hash": request_hash, "attempt": attempt, "started": timestamp()})


def read_attempt(study_dir, sample_id):
    path = sample_dir(study_dir, sample_id) / ATTEMPT_FILE
    if not path.is_file():
        return None
    try:
        return wc.read_json(path)
    except ValueError:
        return None


def recoverable_run(study_dir, entry):
    """True when the sample has no record but a complete run directory written for this request (an evaluation
    that was interrupted after the Engine finished)."""
    if read_record(study_dir, entry["sample_id"]) is not None:
        return False
    attempt = read_attempt(study_dir, entry["sample_id"])
    if not attempt or attempt.get("request_hash") != entry["request_hash"]:
        return False
    return (sample_dir(study_dir, entry["sample_id"]) / "run" / "manifest.json").is_file()


def resume_selection(study_dir, entries):
    """Split a plan into ``(reused, recoverable, pending)``: entries with a final record of the same request,
    entries whose complete run directory can be read back, and entries that have to run."""
    reused, recoverable, pending = [], [], []
    for entry in entries:
        if reusable_record(study_dir, entry) is not None:
            reused.append(entry)
        elif recoverable_run(study_dir, entry):
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
