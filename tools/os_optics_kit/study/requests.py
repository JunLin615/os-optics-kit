"""Evaluation requests: one JSON document per independent Engine evaluation, written before any run.

A request holds everything one evaluation needs, so any process can run it::

    {"schema": "os-optics-kit.study-request", "schema_version": 1,
     "sample_id": "s0001",
     "project": {...},                    # Project JSON with the sample's edits applied; null for a refusal
     "refusal": null,                     # or {"stage", "code", "problems"}: recorded, never run
     "selected_targets": ["spot_radius"], # target ids deciding the outcome; null = all targets
     "labels": {"role": "sample", "parameters": {"l2_x": {"value": 200.0, "unit": "mm"}}},
     "outputs": {"run_directory": true, "ray_data": "full"},
     "seed": null,
     "request_hash": "..."}

``labels`` are bookkeeping copied to the top level of the sample record (``role`` and ``parameters`` by the study
tools; a record key such as ``targets`` cannot be a label). ``outputs`` selects what is kept (the run directory, the
ray-data level ``full`` or ``summary``); numerical fidelity is the Project's own ``compute_budget``. ``seed`` is null
unless the evaluator uses randomness; ``sample_seed`` derives one from the study seed and the sample id, independent
of execution order. ``request_hash`` is the SHA-256 of the canonical JSON of ``project``, ``selected_targets``,
``outputs`` and ``seed`` (and of ``refusal`` for a refused request): a saved sample is reused only while it matches.
"""

from __future__ import annotations

import hashlib
import re

from .. import workflow_common as wc

REQUEST_SCHEMA = "os-optics-kit.study-request"
REQUEST_SCHEMA_VERSION = 1
RAY_DATA_LEVELS = ("full", "summary")
DEFAULT_OUTPUTS = {"run_directory": True, "ray_data": "full"}
#: Sample ids are lower-case ASCII so they can name directories and presentation sources.
SAMPLE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
#: Keys of a sample record that labels may not use (labels are copied into the record next to them).
RESERVED_LABELS = frozenset((
    "sample_dir", "project_file", "project_file_sha256", "run_dir", "outcome", "execution", "requirements", "status",
    "engine_status", "in_model_scope", "model_scope_issues", "out_of_model_issues", "targets", "selected_targets",
    "issues", "warnings", "error", "identity", "seconds", "sample_id", "request_hash", "state", "attempts", "timing",
    "peak_memory_bytes", "worker"))


def sample_ids(count, *, start=1, prefix="s"):
    """``count`` fixed-width ids ``s0001``... (wider when the largest index needs more than four digits)."""
    width = max(4, len(str(start + count - 1))) if count > 0 else 4
    return [f"{prefix}{index:0{width}d}" for index in range(start, start + count)]


def sample_seed(study_seed, sample_id):
    """A 63-bit seed for one sample from the study seed and the sample id (SHA-256), independent of run order."""
    digest = hashlib.sha256(f"{int(study_seed)}:{sample_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") >> 1


def normalize_outputs(outputs):
    """``{"run_directory": bool, "ray_data": "full" | "summary"}`` with defaults; refuses anything else."""
    merged = {**DEFAULT_OUTPUTS, **(outputs or {})}
    unknown = sorted(set(merged) - set(DEFAULT_OUTPUTS))
    if unknown:
        raise wc.WorkflowError("REQUEST_INVALID", f"outputs: unknown keys {unknown}; known: {sorted(DEFAULT_OUTPUTS)}")
    if not isinstance(merged["run_directory"], bool):
        raise wc.WorkflowError("REQUEST_INVALID", "outputs.run_directory must be true or false")
    if merged["ray_data"] not in RAY_DATA_LEVELS:
        raise wc.WorkflowError("REQUEST_INVALID", f"outputs.ray_data must be one of {list(RAY_DATA_LEVELS)}")
    return merged


def request_hash(project, selected_targets, outputs, seed, refusal=None):
    """SHA-256 of the canonical JSON of what decides an evaluation's result and saved outputs."""
    basis = {"project": project, "selected_targets": selected_targets, "outputs": outputs, "seed": seed}
    if refusal is not None:
        basis["refusal"] = refusal
    return wc.canonical_digest(basis)


def make_request(sample_id, project, *, labels=None, selected_targets=None, outputs=None, refusal=None, seed=None):
    """A validated request. ``project``: the Project document (None with a ``refusal``); ``labels``: JSON-safe
    bookkeeping; ``selected_targets``: target ids deciding the outcome (None = all); ``outputs``: see the module
    docstring; ``refusal``: ``{"stage", "code", "problems"}`` of a sample the planner could not build."""
    request = {
        "schema": REQUEST_SCHEMA, "schema_version": REQUEST_SCHEMA_VERSION, "sample_id": sample_id,
        "project": None if project is None else wc.json_safe(project),
        "refusal": None if refusal is None else wc.json_safe(refusal),
        "selected_targets": None if selected_targets is None else list(selected_targets),
        "labels": wc.json_safe(labels or {}), "outputs": normalize_outputs(outputs), "seed": seed,
    }
    request["request_hash"] = request_hash(request["project"], request["selected_targets"], request["outputs"],
                                           request["seed"], request["refusal"])
    return validate_request(request)


def validate_request(request):
    """Return ``request`` when it is a well-formed request whose ``request_hash`` matches its content."""
    if not isinstance(request, dict) or request.get("schema") != REQUEST_SCHEMA:
        raise wc.WorkflowError("REQUEST_INVALID", f"not a {REQUEST_SCHEMA} document")
    if request.get("schema_version") != REQUEST_SCHEMA_VERSION:
        raise wc.WorkflowError("REQUEST_INVALID", f"unsupported request schema_version "
                               f"{request.get('schema_version')!r}; this kit reads {REQUEST_SCHEMA_VERSION}")
    sample_id = request.get("sample_id")
    if not isinstance(sample_id, str) or not SAMPLE_ID.match(sample_id):
        raise wc.WorkflowError("REQUEST_INVALID", f"sample_id {sample_id!r} must match {SAMPLE_ID.pattern}")
    project, refusal = request.get("project"), request.get("refusal")
    if refusal is None and not isinstance(project, dict):
        raise wc.WorkflowError("REQUEST_INVALID", f"{sample_id}: a request needs a project or a refusal",
                               sample_id=sample_id)
    if refusal is not None and not (isinstance(refusal, dict) and {"stage", "code"} <= set(refusal)):
        raise wc.WorkflowError("REQUEST_INVALID", f"{sample_id}: refusal is {{stage, code, problems}}",
                               sample_id=sample_id)
    targets = request.get("selected_targets")
    if targets is not None and not (isinstance(targets, list) and all(isinstance(t, str) for t in targets)):
        raise wc.WorkflowError("REQUEST_INVALID", f"{sample_id}: selected_targets is a list of target ids or null",
                               sample_id=sample_id)
    if not isinstance(request.get("labels"), dict):
        raise wc.WorkflowError("REQUEST_INVALID", f"{sample_id}: labels must be an object", sample_id=sample_id)
    clashes = sorted(RESERVED_LABELS & set(request["labels"]))
    if clashes:
        raise wc.WorkflowError("REQUEST_INVALID", f"{sample_id}: labels may not use the record keys {clashes}",
                               sample_id=sample_id)
    seed = request.get("seed")
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int) or seed < 0):
        raise wc.WorkflowError("REQUEST_INVALID", f"{sample_id}: seed is a non-negative integer or null",
                               sample_id=sample_id)
    outputs = normalize_outputs(request.get("outputs"))
    expected = request_hash(project, targets, outputs, seed, refusal)
    if request.get("request_hash") != expected:
        raise wc.WorkflowError("REQUEST_INVALID", f"{sample_id}: request_hash does not match the request content",
                               sample_id=sample_id, expected=expected, found=request.get("request_hash"))
    return request
