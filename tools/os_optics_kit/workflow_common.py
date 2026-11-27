"""Shared internals of the edit, sweep, compare, plot, report, sensitivity and tolerance tools.

Usage (Python; the tools and case scripts import it):

    from os_optics_kit import workflow_common as wc
    record = wc.run_sample(project_document, "study/samples/s0001")
    record["outcome"]            # ERROR | OUT_OF_MODEL | UNKNOWN | FAIL | PASS | NOT_APPLICABLE

What it provides:

* identity and environment of the installed Engine and the active catalog release;
* explicit unit conversion at the helper boundary (``convert``, ``normalize_quantities``): a number is either a
  bare value in the field's schema unit (field-name suffix ``_mm``, ``_nm``, ``_w``/``_W``, ``_deg``, ``_rad``) or
  ``{"value": x, "unit": "u"}``; supported units are length nm/um/mm/m, angle deg/rad/mrad/urad and power
  W/mW/uW; anything else raises ``WorkflowError`` (``UNIT_UNKNOWN`` / ``UNIT_INCOMPATIBLE``) before any run;
* ``check`` (``json_api.check_project``) and ``run_sample``: one in-process run into a sample directory, returned as
  a sample record whatever happens (an Engine refusal at load or at run is recorded, never raised);
* the requirement class of a sample, from the Engine's assessments of the selected targets: ERROR (no result),
  FAIL (a selected requirement is unmet), OUT_OF_MODEL (a selected target reads a quantity outside its model),
  UNKNOWN (a selected requirement is indeterminate or does not apply), PASS (every selected requirement is met),
  NOT_APPLICABLE (no target is selected, including a target-free run). The execution outcome (COMPLETED or
  PARTIAL, with the exhausted budgets) is a separate fact of the record. A PARTIAL run did not complete: it is
  never PASS or NOT_APPLICABLE (UNKNOWN when its selected targets are met or none is selected), keeps FAIL /
  OUT_OF_MODEL, and exits 3 whatever its class (``requirement_exit_code``), as under the Engine's ``requirements`` exit
  contract;
* ``freshness`` delegates to the Engine's CURRENT / HISTORICAL / UNDETERMINED decision with its reasons;
* sha256 / JSON / CSV writers (LF line endings) and bounded-output helpers.

The Engine is imported only inside the functions that need it, so ``--help`` of every tool works without it.
"""

from __future__ import annotations

import csv
from decimal import Decimal
import hashlib
import io
import json
import math
import os
from pathlib import Path
import platform
import sys
import time

from .result_summary import ArgumentParser  # noqa: F401 - the tools' parser (usage errors exit 4)
from .result_summary import EXIT_CODES, ERROR_EXIT, error_document, exit_status, json_cli_stdout, summarize_results
from .result_summary import EXECUTION_INCOMPLETE_EXIT, requirement_exit_code  # noqa: F401 - used by the tools

OUTCOMES = ("ERROR", "OUT_OF_MODEL", "UNKNOWN", "FAIL", "PASS", "NOT_APPLICABLE")
#: Exit codes of the workflow tools: the run statuses of result_summary plus the study / edit outcomes.
WORKFLOW_EXIT_CODES = {**EXIT_CODES, "COMPLETED": 0, "EDITED": 0, "ALREADY_APPLIED": 0, "NO_FEASIBLE": 1}
#: Exit code of a command stopped by Ctrl-C.
INTERRUPTED_EXIT = 130


class WorkflowError(Exception):
    """A refused request with a stable ``code`` and JSON-safe ``details`` (printed by ``emit``)."""

    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.details = details

    def as_dict(self):
        return {"code": self.code, "message": str(self), **json_safe(self.details)}


def evaluation_cap(spec, count, what):
    """The optional ``max_evaluations`` of a study spec: ``None`` (absent or null: no cap) or a positive integer;
    a stated cap smaller than the ``count`` planned Engine runs refuses the request (``BUDGET_EXCEEDED``) before
    any run. ``what`` describes the count in the refusal."""
    budget = spec.get("max_evaluations")
    if budget is None:
        return None
    if isinstance(budget, bool) or not isinstance(budget, int) or budget < 1:
        raise WorkflowError("SPEC_INVALID", "max_evaluations must be a positive integer or null (no cap)")
    if count > budget:
        raise WorkflowError("BUDGET_EXCEEDED", f"{what}, more than max_evaluations {budget}; nothing was run",
                            samples=count, max_evaluations=budget)
    return budget


# --------------------------------------------------------------------------------------------- units

# factor to the dimension's base unit (mm, rad, W); Decimal strings keep decimal conversions exact
_UNITS = {
    "nm": ("length", "0.000001"), "um": ("length", "0.001"), "µm": ("length", "0.001"),
    "mm": ("length", "1"), "m": ("length", "1000"),
    "rad": ("angle", "1"), "mrad": ("angle", "0.001"), "urad": ("angle", "0.000001"), "deg": ("angle", None),
    "W": ("power", "1"), "mW": ("power", "0.001"), "uW": ("power", "0.000001"),
}
_SUFFIX_UNITS = (("_mm", "mm"), ("_nm", "nm"), ("_w", "W"), ("_W", "W"), ("_deg", "deg"), ("_rad", "rad"))
UNITLESS = ("1", "")


def field_unit(key):
    """Schema unit of a field from its name suffix (``position_mm`` -> ``mm``); None for other fields."""
    for suffix, unit in _SUFFIX_UNITS:
        if str(key).endswith(suffix):
            return unit
    return None


def is_quantity(value):
    return isinstance(value, dict) and set(value) == {"value", "unit"}


def convert(value, from_unit, to_unit, *, path="value"):
    """``value`` in ``from_unit`` expressed in ``to_unit``; refuses unknown or incompatible units."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise WorkflowError("BAD_ARGUMENT", f"{path}: expected a finite number, got {value!r}", path=path)
    if from_unit == to_unit:
        return value
    if from_unit in UNITLESS and to_unit in UNITLESS:
        return value
    for unit in (from_unit, to_unit):
        if unit not in _UNITS:
            raise WorkflowError("UNIT_UNKNOWN", f"{path}: unknown unit {unit!r}; known: {sorted(_UNITS)} "
                                "(and the target's own unit for target values)", path=path, unit=unit)
    (dim_from, f_from), (dim_to, f_to) = _UNITS[from_unit], _UNITS[to_unit]
    if dim_from != dim_to:
        raise WorkflowError("UNIT_INCOMPATIBLE", f"{path}: {from_unit!r} ({dim_from}) cannot be converted to "
                            f"{to_unit!r} ({dim_to})", path=path, unit=from_unit, expected_unit=to_unit)
    if f_from is not None and f_to is not None:
        return float(Decimal(repr(float(value))) * Decimal(f_from) / Decimal(f_to))
    radians = float(value) * (math.pi / 180.0 if f_from is None else float(f_from))
    return radians * 180.0 / math.pi if f_to is None else radians / float(f_to)


def to_unit(value, unit, *, path):
    """A bare number (already in ``unit``) or ``{"value", "unit"}`` (converted) -> number in ``unit``.

    A vector may be a list of numbers / quantities, or one quantity whose value is a list."""
    if is_quantity(value):
        given = value["unit"]
        inner = value["value"]
        if isinstance(inner, list):
            return [convert(v, given, unit, path=f"{path}[{i}]") for i, v in enumerate(inner)]
        return convert(inner, given, unit, path=path)
    if isinstance(value, list):
        return [to_unit(v, unit, path=f"{path}[{i}]") for i, v in enumerate(value)]
    return value


def normalize_quantities(obj, *, path="", unit_for=None):
    """Return a copy of ``obj`` with every ``{"value", "unit"}`` converted to its field's schema unit.

    ``unit_for(key)`` gives the unit of a field (default: ``field_unit``); a quantity on a field without a unit is
    accepted only with unit "1"; quaternions are unitless and reject quantity objects."""
    unit_for = unit_for or field_unit
    if isinstance(obj, dict) and not is_quantity(obj):
        out = {}
        for key, value in obj.items():
            here = f"{path}.{key}" if path else str(key)
            unit = unit_for(key)
            if key == "quaternion_wxyz":
                if is_quantity(value) or (isinstance(value, list) and any(is_quantity(v) for v in value)):
                    raise WorkflowError("UNIT_INCOMPATIBLE", f"{here}: a quaternion is unitless; give four numbers",
                                        path=here)
                out[key] = value
            elif unit is not None:
                out[key] = to_unit(value, unit, path=here) if (is_quantity(value) or isinstance(value, list)) \
                    else normalize_quantities(value, path=here, unit_for=unit_for)
            elif is_quantity(value):
                if value["unit"] not in UNITLESS:
                    raise WorkflowError("UNIT_INCOMPATIBLE", f"{here}: field has no unit; got unit {value['unit']!r}",
                                        path=here, unit=value["unit"])
                out[key] = value["value"]
            else:
                out[key] = normalize_quantities(value, path=here, unit_for=unit_for)
        return out
    if isinstance(obj, list):
        return [normalize_quantities(v, path=f"{path}[{i}]", unit_for=unit_for) for i, v in enumerate(obj)]
    return obj


# --------------------------------------------------------------------------------------------- files / hashes

def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    return sha256_bytes(Path(path).read_bytes())


def canonical_json(value):
    return json.dumps(json_safe(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def canonical_digest(value):
    """sha256 of the canonical JSON text (sorted keys, no whitespace) of ``value``."""
    return sha256_bytes(canonical_json(value).encode("ascii"))


def canonical_numbers(value):
    """Copy of ``value`` with every integer (not bool) as a float, so ``1`` and ``1.0`` compare and hash alike."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return float(value)
    if isinstance(value, dict):
        return {key: canonical_numbers(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [canonical_numbers(item) for item in value]
    return value


def json_safe(value):
    """Replace non-finite floats by None and tuples by lists, recursively."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, Path):
        return value.as_posix()
    return value


def json_text(document):
    return json.dumps(json_safe(document), indent=1, ensure_ascii=True, allow_nan=False) + "\n"


def write_json(path, document):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json_text(document))
    return path


def write_text_atomic(path, text):
    """Write ``text`` (LF) to a temporary file next to ``path`` and move it into place."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.replace(tmp, path)
    return path


def read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def run_catalog_identity(rows):
    """Aggregate saved-run catalog provenance, never the installed cache identity."""
    versions = sorted({row.get("catalog_version") for row in rows if row.get("result_hash")},
                      key=lambda version: version or "")
    return {"catalog_version": versions[0] if len(versions) == 1 else None,
            "catalog_versions": versions}


def csv_cell(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(value) if math.isfinite(value) else ""
    if isinstance(value, (list, tuple)):
        return ";".join(csv_cell(v) for v in value)
    if isinstance(value, dict):
        return canonical_json(value)
    return str(value)


def write_csv(path, rows, columns):
    """CSV with a header row and LF line endings; missing values are empty cells."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([csv_cell(row.get(column)) for column in columns])
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(buffer.getvalue())
    return path


def relative(path, start):
    """``path`` relative to ``start`` with forward slashes (absolute when on another drive)."""
    try:
        return Path(os.path.relpath(Path(path).resolve(), Path(start).resolve())).as_posix()
    except ValueError:
        return Path(path).resolve().as_posix()


def _produced(name, produced):
    """Whether the entry ``name`` is one of ``produced``: exact names, or compiled patterns matched in full."""
    return any(entry.fullmatch(name) if hasattr(entry, "fullmatch") else name == entry for entry in produced)


def check_output(out_dir, overwrite, produced=(), *, keep=(), writes=None):
    """Refuse an output directory before any work, without changing it (``prepare_output`` states the rule).

    An existing file, or a non-empty directory without ``overwrite``, is ``OUTPUT_EXISTS``. With ``overwrite``, an
    input (``keep``) that this run would replace (an entry of ``writes``, default ``produced``) or remove (inside a
    ``produced`` directory) is ``OUTPUT_IS_INPUT``: inputs are never modified."""
    out_dir = Path(out_dir)
    if out_dir.exists() and not out_dir.is_dir():
        raise WorkflowError("OUTPUT_EXISTS", f"{out_dir.as_posix()} is a file, not a directory", out=out_dir.as_posix())
    if not (out_dir.is_dir() and any(out_dir.iterdir())):
        return out_dir
    if not overwrite:
        raise WorkflowError("OUTPUT_EXISTS", f"{out_dir.as_posix()} is not empty; choose a new directory or use "
                            "--overwrite", out=out_dir.as_posix())
    root = out_dir.resolve()
    writes = produced if writes is None else writes
    for item in keep:
        if item is None:
            continue
        path = Path(item).resolve()
        replaced = path.parent == root and _produced(path.name, writes)
        removed = any(parent.parent == root and _produced(parent.name, produced) for parent in path.parents)
        if replaced or removed:
            raise WorkflowError("OUTPUT_IS_INPUT", f"--overwrite would {'replace' if replaced else 'remove'} the input "
                                f"{path.as_posix()}; choose another --out", out=out_dir.as_posix(),
                                input=path.as_posix())
    return out_dir


def prepare_output(out_dir, overwrite, produced=(), *, keep=(), writes=None):
    """A command's output directory, created when missing: the one output-directory rule of the kit's commands.

    A new or empty directory is used as is. A non-empty one is refused (``OUTPUT_EXISTS``) unless ``overwrite``, which
    first removes the entries this command writes, so no stale artifact survives: ``produced`` holds exact entry
    names or compiled patterns (``imaging-<n>.json`` as a regular expression) matched against whole names; a matching
    directory is removed with its contents, other entries are left as they are. ``keep``: the command's input paths,
    never removed or replaced (``check_output``); ``writes``: the entries this run writes, when not all of
    ``produced``."""
    import shutil

    out_dir = check_output(out_dir, overwrite, produced, keep=keep, writes=writes)
    if out_dir.is_dir() and overwrite:
        kept = {Path(item).resolve() for item in keep if item is not None}
        for path in out_dir.iterdir():
            if not _produced(path.name, produced) or path.resolve() in kept:
                continue
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def bounded(rows, limit):
    """The first ``limit`` rows and how many were left out."""
    rows = list(rows)
    return {"shown": rows[:limit], "omitted": max(0, len(rows) - limit), "total": len(rows)}


def warning_summary(rows, limit=5):
    """Samples whose run carries WARNING issues: ``{samples, pass_samples, by_code, sample_ids (first limit)}``."""
    flagged = [row for row in rows if row.get("warnings")]
    by_code = {}
    for row in flagged:
        for code in row["warnings"]:
            by_code[code] = by_code.get(code, 0) + 1
    return {"samples": len(flagged), "pass_samples": sum(1 for row in flagged if row.get("outcome") == "PASS"),
            "by_code": dict(sorted(by_code.items())), "sample_ids": [row["sample_id"] for row in flagged[:limit]]}


def utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --------------------------------------------------------------------------------------------- identity

def engine_identity():
    """Installed Engine version and build id."""
    from os_optics import __version__
    from os_optics.manifest import engine_build_id

    return {"engine_version": __version__, "engine_build_id": engine_build_id()}


def catalog_identity():
    """Active catalog release (never uses the network); ``available`` false when none is installed."""
    from os_optics.local import json_api

    status = json_api.catalog_status()
    active = status.get("active") or {}
    return {"available": bool(status.get("available")), "catalog_version": active.get("catalog_version"),
            "sha256": active.get("sha256"), "source": active.get("source")}


def environment():
    """Python, OS and package versions."""
    from importlib.metadata import PackageNotFoundError, version

    packages = {}
    for name in ("os-optics", "numpy", "matplotlib"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    return {"python": platform.python_version(), "implementation": platform.python_implementation(),
            "os": f"{platform.system()} {platform.release()}", "machine": platform.machine(), "packages": packages,
            "catalog_check_interval": os.environ.get("OPENSCI_CATALOG_CHECK_INTERVAL")}


def target_metric_units():
    """Metric name -> unit of the installed Engine (no catalog needed)."""
    from os_optics.local import target_metrics

    return dict(target_metrics())


# --------------------------------------------------------------------------------------------- check / run

def check(document):
    """``json_api.check_project``: ``{"ok": True, "project", "project_hash", "engine_input_hash", ...}`` or
    ``{"ok": False, "error": {"code", "problems"}}``. Never runs the Engine."""
    from os_optics.local import json_api

    return json_api.check_project(document)


def _problems(exc):
    return [problem.as_dict() for problem in getattr(exc, "problems", ())] or [
        {"code": type(exc).__name__, "message": str(exc)}]


def target_bounds(target):
    """``{minimum, maximum}`` of a target row (``bounds`` of result_summary), from target_value +- tolerance too."""
    bounds = target.get("bounds") or {}
    low, high = bounds.get("minimum"), bounds.get("maximum")
    if bounds.get("target_value") is not None and bounds.get("tolerance") is not None:
        low = bounds["target_value"] - bounds["tolerance"] if low is None else low
        high = bounds["target_value"] + bounds["tolerance"] if high is None else high
    return {"minimum": low, "maximum": high}


def classify(status, model_scope_issues, targets, selected=None, out_of_model_issues=()):
    """Requirement class of a run from the Engine's assessments of the selected targets.

    In this order: ``ERROR`` without a result; ``NOT_APPLICABLE`` when no target is selected; ``UNKNOWN`` when a
    selected id is not in the run; ``FAIL`` when a selected target is ``UNMET``; ``OUT_OF_MODEL`` when a selected
    target carries model-applicability codes or the status ``OUT_OF_MODEL_SCOPE``; ``UNKNOWN`` when one is
    ``INDETERMINATE`` or has no assessment; ``PASS`` when every selected target is ``MET``; else ``UNKNOWN``: a
    selected target assessed ``NOT_APPLICABLE`` is not met, alone or beside met ones, as under the Engine's
    ``requirements`` exit contract. Scope findings on quantities no selected target reads do not change the class;
    the per-target rows keep every assessment and code.
    """
    if status is None:
        return "ERROR"
    rows = {t["id"]: t for t in targets}
    chosen = list(rows) if selected is None else list(selected)
    if not chosen:
        return "NOT_APPLICABLE"
    selected_rows = [rows.get(target_id) for target_id in chosen]
    if any(row is None for row in selected_rows):
        return "UNKNOWN"
    assessments = [row.get("assessment") for row in selected_rows]
    if "UNMET" in assessments:
        return "FAIL"
    if any(row.get("applicability_issue_codes") or row.get("status") == "OUT_OF_MODEL_SCOPE"
           for row in selected_rows):
        return "OUT_OF_MODEL"
    if "INDETERMINATE" in assessments or any(value is None for value in assessments):
        return "UNKNOWN"
    return "PASS" if all(value == "MET" for value in assessments) else "UNKNOWN"


def requirement_outcome(status, execution, model_scope_issues, targets, selected=None, out_of_model_issues=()):
    """``classify`` for a run record: a ``FAILED`` or ``CANCELLED`` calculation has no result and is ``ERROR``; a
    ``PARTIAL`` one (a compute budget was reached) did not complete and is never ``PASS`` or ``NOT_APPLICABLE``
    but ``UNKNOWN``, as under the Engine's ``requirements`` exit contract, while an unmet or out-of-model selected
    target keeps ``FAIL`` / ``OUT_OF_MODEL``. What a reached budget left undecided is ``INDETERMINATE`` on the
    targets it affects; ``execution`` stays beside the class, and ``requirement_exit_code`` gives every ``PARTIAL``
    run exit 3."""
    state = (execution or {}).get("outcome")
    if state in ("FAILED", "CANCELLED"):
        return "ERROR"
    outcome = classify(status, model_scope_issues, targets, selected, out_of_model_issues)
    if state == "PARTIAL" and outcome in ("PASS", "NOT_APPLICABLE"):
        return "UNKNOWN"
    return outcome


def record_outcome(record):
    """The requirement class of a saved sample record, derived again from its targets, execution and selected
    targets (``requirement_outcome``), so a record written by an earlier kit release is classed by the same rule; a
    record with an error stays ``ERROR``."""
    if record.get("error") or record.get("status") is None:
        return "ERROR"
    return requirement_outcome(record.get("status"), record.get("execution"), record.get("model_scope_issues") or [],
                               record.get("targets") or [], record.get("selected_targets"),
                               record.get("out_of_model_issues") or [])


#: What a reported target value is, as a study operand (``target_value_use``).
VALUE_KINDS = ("EXACT", "BOUNDED", "QUANTITY_UNKNOWN", "OUTSIDE_MODEL", "UNDECIDED", "NO_VALUE")
#: Which values a study uses: ``exact`` (EXACT only, the default) or ``reported`` (every finite value).
VALUE_USES = ("exact", "reported")


def target_value_use(target):
    """``{usable, kind, codes}`` of a target row's value as a study operand under the default ``exact`` rule.

    ``kind``: ``NO_VALUE`` (no finite number), ``OUTSIDE_MODEL`` (model-applicability codes or status / quantity
    state ``OUT_OF_MODEL_SCOPE``), ``BOUNDED`` or ``QUANTITY_UNKNOWN`` (the quantity state), ``UNDECIDED`` (the
    target's status is not PASS / WARNING / FAIL: the metric could not be graded) or ``EXACT``. ``codes``: the
    target's applicability and issue codes. Bounds and applicability stay in the record whatever the kind."""
    value = target.get("value")
    codes = sorted({*(target.get("applicability_issue_codes") or []), *(target.get("issue_codes") or [])})
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        kind = "NO_VALUE"
    elif target.get("applicability_issue_codes") or "OUT_OF_MODEL_SCOPE" in (target.get("status"),
                                                                             target.get("quantity_state")):
        kind = "OUTSIDE_MODEL"
    elif target.get("quantity_state") == "BOUNDED":
        kind = "BOUNDED"
    elif target.get("quantity_state") == "UNKNOWN":
        kind = "QUANTITY_UNKNOWN"
    elif target.get("status") not in ("PASS", "WARNING", "FAIL"):
        kind = "UNDECIDED"
    else:
        kind = "EXACT"
    return {"usable": kind == "EXACT", "kind": kind, "codes": codes}


def value_usable(kind, value_use="exact"):
    """Whether a value of ``kind`` is used under ``value_use`` (``VALUE_USES``)."""
    return kind == "EXACT" if value_use == "exact" else kind not in (None, "NO_VALUE")


def value_use_setting(value):
    """The validated ``value_use`` of a spec or option (``None`` -> ``exact``)."""
    if value is None:
        return "exact"
    if value not in VALUE_USES:
        raise WorkflowError("SPEC_INVALID", f"value_use must be one of {list(VALUE_USES)}", value_use=value)
    return value


def values_used(rows, columns, value_use):
    """``{column: {kind: count}}`` of the rows whose value of each ``target:`` column is used under ``value_use``."""
    out = {}
    for column in columns:
        counts = {}
        for row in rows:
            kind = row.get(f"{column}:value_kind")
            if value_usable(kind, value_use):
                counts[kind] = counts.get(kind, 0) + 1
        out[column] = dict(sorted(counts.items()))
    return out


def target_value_is_exact(target):
    """Whether this target's numeric value can be used as an exact study operand (``target_value_use`` kind
    ``EXACT``)."""
    return target_value_use(target)["kind"] == "EXACT"


def error_record(stage, code, problems, *, sample_dir=None, project_file=None, seconds=0.0, selected=None):
    """A sample record for a refused or not-run sample (outcome ERROR)."""
    record = _empty_record(sample_dir, project_file, selected)
    record.update({"outcome": "ERROR", "error": {"stage": stage, "code": code, "problems": problems},
                   "seconds": seconds})
    return record


def _empty_record(sample_dir, project_file, selected):
    return {
        "sample_dir": None if sample_dir is None else Path(sample_dir).as_posix(),
        "project_file": None if project_file is None else Path(project_file).as_posix(),
        "project_file_sha256": None if project_file is None or not Path(project_file).exists()
        else sha256_file(project_file),
        "run_dir": None, "outcome": "ERROR", "execution": None, "requirements": None, "status": None, "engine_status": None, "in_model_scope": None,
        "model_scope_issues": [], "out_of_model_issues": [], "targets": [],
        "selected_targets": None if selected is None else list(selected),
        "issues": [], "warnings": [], "error": None,
        "identity": {"engine_version": None, "engine_build_id": None, "catalog_version": None, "project_hash": None,
                     "input_hash": None, "result_hash": None},
        "seconds": 0.0,
    }


def run_sample(document, sample_dir, *, targets=None, write_run=True, monitor=None, ray_data="full"):
    """Save ``document`` as ``sample_dir/project.json``, run it in-process into ``sample_dir/run`` and return the
    sample record. Never raises for an Engine refusal: a refusal at load (``stage: "load"``) or at run (``stage:
    "run"``) gives an ``ERROR`` record with the Engine's problem codes. ``targets``: the target ids that decide the
    outcome (default: all targets of the run). ``monitor``: an ``os_optics.run_monitor.RunMonitor`` passed to the
    run; a run it stops gives an ``ERROR`` record with code ``RUN_STOPPED`` (problem code = the stop cause:
    ``CALLER``, ``MEMORY_LIMIT``, ``ARTIFACT_LIMIT``). ``ray_data``: ``full`` or ``summary`` (chief rays only)."""
    from os_optics.local import LocalRunError, load_project, run_optical_project
    from os_optics.results import load_run
    from os_optics.run_control import RunStopped

    sample_dir = Path(sample_dir)
    sample_dir.mkdir(parents=True, exist_ok=True)
    project_file = write_json(sample_dir / "project.json", document)
    run_dir = sample_dir / "run"
    started = time.perf_counter()
    record = _empty_record(sample_dir, project_file, targets)
    try:
        project = load_project(json.loads(json.dumps(json_safe(document))))
    except LocalRunError as exc:
        return error_record("load", "PROJECT_REFUSED", _problems(exc), sample_dir=sample_dir,
                            project_file=project_file, seconds=time.perf_counter() - started, selected=targets)
    record["identity"]["project_hash"] = project.content_hash()
    try:
        run = run_optical_project(project, output_dir=run_dir if write_run else None, monitor=monitor,
                                  ray_data=ray_data)
        summary = summarize_results(load_run(run))
    except RunStopped as exc:
        problem = {"code": exc.cause, "message": exc.message, "phase": exc.phase}
        record.update({"error": {"stage": "run", "code": "RUN_STOPPED", "problems": [problem]},
                       "seconds": time.perf_counter() - started})
        return record
    except LocalRunError as exc:
        record.update({"error": {"stage": "run", "code": "RUN_REFUSED", "problems": _problems(exc)},
                       "seconds": time.perf_counter() - started})
        return record
    except Exception as exc:  # noqa: BLE001 - an unexpected Engine failure is a recorded ERROR sample, not a crash
        record.update({"error": {"stage": "run", "code": "ENGINE_EXCEPTION", "problems": _problems(exc)},
                       "seconds": time.perf_counter() - started})
        return record
    record.update(sample_from_summary(summary, targets))
    lv = run.local_validation
    record["identity"] = {
        "engine_version": lv.runtime.engine_version, "engine_build_id": lv.runtime.engine_build_id,
        "catalog_version": run.catalog_version, "project_hash": lv.project.project_hash.digest,
        "input_hash": lv.input_hash.digest, "result_hash": lv.result_hash.digest,
    }
    record["run_dir"] = run_dir.as_posix() if write_run else None
    record["seconds"] = time.perf_counter() - started
    return record


def sample_from_summary(summary, selected=None):
    """Outcome fields of a sample record from a result_summary summary (shared by run_sample and saved runs)."""
    scope = list(summary.get("model_scope_issues") or [])
    issues = [{key: issue.get(key) for key in ("code", "status", "location") if issue.get(key) is not None}
              for issue in summary.get("issues", [])]
    targets = []
    for row in summary["targets"]:
        kept = {key: row.get(key) for key in ("id", "metric", "value", "unit", "status", "bounds", "observation_id",
                                                         "assessment", "quantity_state", "observation_power_state", "power_complete", "power_upper_bound_W",
                                                         "applicability_issue_codes")}
        if row.get("issue_codes"):
            kept["issue_codes"] = list(row["issue_codes"])
        targets.append(kept)
    # issues the Engine reports with status OUT_OF_MODEL_SCOPE but not as model-scope issues (e.g. a tabulated thin
    # lens outside its rows): a FAIL target makes the run status FAIL, yet the run left a model's validity.
    # One entry per distinct code and location; a target row adds an entry only for a code no run issue reports.
    out_of_model, seen = [], set()
    for issue in issues:
        if issue.get("status") == "OUT_OF_MODEL_SCOPE" and (issue.get("code"), issue.get("location")) not in seen:
            seen.add((issue.get("code"), issue.get("location")))
            out_of_model.append(issue)
    issue_codes = {code for code, _ in seen}
    for t in targets:
        if t.get("status") != "OUT_OF_MODEL_SCOPE":
            continue
        for code in t.get("issue_codes") or ["OUT_OF_MODEL_SCOPE"]:
            key = (code, f"target {t['id']}")
            if code not in issue_codes and key not in seen:
                seen.add(key)
                out_of_model.append({"code": code, "status": "OUT_OF_MODEL_SCOPE", "location": key[1]})
    status = summary["status"]
    execution = (summary.get("outcome") or {}).get("execution")
    return {
        "outcome": requirement_outcome(status, execution, scope, targets, selected, out_of_model),
        "status": status, "engine_status": summary.get("engine_status"),
        "execution": execution,
        "requirements": (summary.get("outcome") or {}).get("requirements"),
        "in_model_scope": not scope and not out_of_model and status != "OUT_OF_MODEL_SCOPE",
        "model_scope_issues": scope, "out_of_model_issues": out_of_model, "targets": targets,
        "issues": issues, "warnings": sorted({i["code"] for i in issues if i.get("status") == "WARNING"}),
        "error": None,
    }


def read_saved_run(run_dir, selected=None, *, require_integrity=False):
    """Summary + sample fields of a saved run directory (never reruns). The summary's ``integrity`` states whether
    the run's files verify; with ``require_integrity`` a run that does not verify is refused
    (``RUN_INTEGRITY_FAILED``), for a reader that would adopt the run as a result. A run whose integrity was not
    checked is refused too (``RUN_INTEGRITY_NOT_CHECKED``): adoption needs a positive verification."""
    from .result_summary import summarize_run

    summary = summarize_run(run_dir, verify=True)
    integrity = summary.get("integrity") or {}
    if require_integrity and not integrity.get("checked"):
        raise WorkflowError("RUN_INTEGRITY_NOT_CHECKED", "the Engine did not check the run's files against their "
                            "recorded hashes", run_dir=Path(run_dir).as_posix())
    if require_integrity and not integrity.get("consistent"):
        raise WorkflowError("RUN_INTEGRITY_FAILED", "; ".join(integrity.get("problems") or []),
                            run_dir=Path(run_dir).as_posix(), problems=integrity.get("problems"))
    fields = sample_from_summary(summary, selected)
    identity = summary.get("identity") or {}
    fields["identity"] = {key: identity.get(key) for key in (
        "engine_version", "engine_build_id", "catalog_version", "project_hash", "input_hash", "result_hash")}
    fields["run_dir"] = Path(run_dir).as_posix()
    return summary, fields


# --------------------------------------------------------------------------------------------- freshness

def freshness(project, run_dir, *, verify=True):
    """Whether the saved run in ``run_dir`` is CURRENT for ``project`` (a document or a path), by the Engine's rule
    (``os_optics.local.freshness.run_freshness``). Different Engine builds alone do not make matching runs historical.

    With ``verify`` (the default) the Engine also checks the run's files against their recorded hashes
    (``integrity``). A run whose files do not verify is ``HISTORICAL`` with the reason ``RUN_UNVERIFIED`` and its
    ``problems``, whatever its hashes say: its stored outcome is not the one the run wrote, so no reader may take
    it as the current result of ``project``."""
    from os_optics.local.freshness import run_freshness

    document = read_json(project) if isinstance(project, (str, Path)) else project
    result = {"run_dir": Path(run_dir).as_posix(), **run_freshness(document, run_dir, verify=bool(verify))}
    integrity = result.get("integrity") or {}
    if integrity.get("checked") and not integrity.get("consistent"):
        result["freshness"] = "HISTORICAL"
        result["reasons"] = [*(r for r in result.get("reasons") or [] if r != "RUN_UNVERIFIED"), "RUN_UNVERIFIED"]
        result["problems"] = list(integrity.get("problems") or [])[:5]
    return result


# --------------------------------------------------------------------------------------------- CLI output

def emit(workflow, exit_codes=None, *, execution_of=None):
    """Run ``workflow``; one compact JSON document on stdout (exit code from its ``status``), diagnostics on
    stderr; any exception -> ``{"error": {...}}`` on stderr, empty stdout, exit 4. ``execution_of(summary)``: the
    ``execution`` of the run the status classes, if any; a PARTIAL one exits ``EXECUTION_INCOMPLETE_EXIT``
    (``requirement_exit_code``)."""
    exit_codes = exit_codes or WORKFLOW_EXIT_CODES
    try:
        with json_cli_stdout():
            summary = workflow()
        execution = execution_of(summary) if execution_of is not None else None
        code = requirement_exit_code(summary["status"], execution, exit_codes)
        output = json.dumps(json_safe(summary), ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    except SystemExit as exc:  # --help
        return exit_status(exc)
    except KeyboardInterrupt:  # Ctrl-C outside a study execution (which handles it itself)
        print(json.dumps({"error": {"code": "INTERRUPTED", "message": "interrupted (Ctrl-C)"}}), file=sys.stderr)
        return INTERRUPTED_EXIT
    except Exception as exc:  # noqa: BLE001 - every failure becomes a JSON error
        error = exc.as_dict() if isinstance(exc, WorkflowError) else error_document(exc)
        print(json.dumps({"error": json_safe(error)}, ensure_ascii=True), file=sys.stderr)
        return ERROR_EXIT
    print(output)
    return code
