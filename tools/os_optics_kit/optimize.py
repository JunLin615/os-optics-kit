"""Bounded local optimization of a Project from a JSON spec: the best feasible Project, its measured performance and the
history that reproduces the search.

Usage:
  os-optics-kit optimize SPEC.json --out DIR [--method trf|slsqp] [--max-evaluations N] [--overwrite]

SPEC.json (schema ``os-optics-kit.optimize-spec`` v1; every field but ``project``, ``variables`` and an objective or
constraint is optional):

  description       what is optimized and why (text)
  project           the base Project: a path relative to SPEC.json, or the Project object itself
  configuration     the configuration to optimize, for a Project that declares configurations (flattened first)
  variables[]       {name, unit?, ops, bounds: [lo, hi], nominal?, scale?}: ops are edit-op templates of
                    ``os-optics-kit edit`` (including set_optical_model_value) with "$value" = the variable's value in
                    ``unit``; nominal omitted: read from the field the ops write in the base Project (0 for transform
                    ops); scale (default 1, in ``unit``): the variable's step unit for the solver
  objectives[]      {name, source, goal?, scale?, weight?}: drive a quantity toward ``goal`` (scaled, weighted)
  constraints[]     {name, source, minimum?, maximum?, scale?}: a quantity's limits; ``margin`` > 0 inside
                    sources: {"target": ID}, {"rays": {"target": ID}} (share of a spot target's pupil rays that
                    arrive), {"paraxial": {components, wavelength_nm, quantity}}, {"solids": {quantity, component?,
                    member?}}, {"mtf": {observation, frequency_cyc_per_mm, ...}} (constraints only)
  aggregation       sum_squares (default) | sum_abs | max_abs: how objective errors combine
  value_use         exact (default) | reported;  imaging: the imaging request of MTF constraints
  invalid_residual  residual of an invalid candidate (default 1000);  ray_loss: infeasible (default) | report
  active_margin     a constraint is active when |margin| <= this (default 1e-3, in constraint scales)
  solver            {method: trf | slsqp, max_evaluations (default 200), max_iterations?, tolerances: {ftol, xtol,
                    gtol}, diff_step (default 1e-3 variable scales), difference: central | forward,
                    x_scale: variables | jac}
  verify            {ops?, imaging?, tolerances?: {relative, absolute}}: re-evaluate the best candidate in a fresh
                    session (reproduction within the tolerances, default 1e-9 relative and 1e-12 absolute), then with
                    ``ops`` (e.g. set_compute_budget with denser pupil sampling) and ``imaging`` overrides applied

The whole spec is checked before any evaluation: an unknown key, a source the adapter does not read, MTF as an
objective, trf with an aggregation other than sum_squares, a nominal outside its bounds or ambiguous
(NOMINAL_AMBIGUOUS) is refused (SPEC_INVALID with ``path``; exit 4). Each candidate is evaluated by
``os_optics_kit.study.evaluation_adapter`` on one Engine session; the solvers are ``os_optics_kit.study.solvers``
(trf: SciPy least_squares on the residual vector; slsqp: SciPy SLSQP with every constraint margin >= 0).

DIR (new or empty, else OUTPUT_EXISTS unless --overwrite):
  spec.json          the spec as run: defaults filled in, nominals resolved, the base Project inlined (it runs again
                     with this command)
  history.jsonl      one line per physical evaluation (finite-difference probes included), written as it runs
  result.json        schema os-optics-kit.optimize v1: status, termination, counts, variables (nominal and where it
                     was read), best, start, closest (without a
                     feasible candidate: the valid one nearest to feasible), feasible_count, infeasible_reasons,
                     verification, identity
  best-project.json  the best feasible Project (only when one exists)

Status / exit: CONVERGED 0 (the solver converged; best feasible Project written), NO_FEASIBLE 1 (no feasible
candidate; no best-project.json, every candidate's margins in history.jsonl), STOPPED 2 (budget, iteration cap or a
solver stop with a feasible best), INTERRUPTED 130 (Ctrl-C; files written so far kept), errors 4 (JSON on stderr:
SPEC_INVALID, NOMINAL_AMBIGUOUS, CONFIGURATION_REQUIRED, PROJECT_REJECTED, OUTPUT_EXISTS, ...).
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
from pathlib import Path
import time
from typing import Any, Callable

from . import workflow_common as wc
from .study import solvers
from .study.solvers import Settings, SettingsError, solve

PROG = "os-optics-kit optimize"
SPEC_SCHEMA = "os-optics-kit.optimize-spec"
RESULT_SCHEMA = "os-optics-kit.optimize"
SCHEMA_VERSION = 1
SPEC_KEYS = ("schema", "schema_version", "description", "project", "configuration", "variables", "objectives",
             "constraints", "aggregation", "value_use", "imaging", "invalid_residual", "ray_loss", "active_margin",
             "solver", "verify")
VARIABLE_KEYS = ("name", "unit", "ops", "bounds", "nominal", "scale")
OBJECTIVE_KEYS = ("name", "source", "goal", "scale", "weight")
CONSTRAINT_KEYS = ("name", "source", "minimum", "maximum", "scale")
SOLVER_KEYS = ("method", "max_evaluations", "max_iterations", "tolerances", "diff_step", "difference", "x_scale")
TOLERANCE_KEYS = ("ftol", "xtol", "gtol")
VERIFY_KEYS = ("ops", "imaging", "tolerances")
DEFAULT_ACTIVE_MARGIN = 1e-3
DEFAULT_REPRODUCTION = {"relative": 1e-9, "absolute": 1e-12}
FILES = ("spec.json", "history.jsonl", "result.json", "best-project.json")
STATUSES = ("CONVERGED", "STOPPED", "NO_FEASIBLE", "INTERRUPTED")
EXIT_CODES = {**wc.WORKFLOW_EXIT_CODES, "CONVERGED": 0, "STOPPED": 2, "NO_FEASIBLE": 1,
              "INTERRUPTED": wc.INTERRUPTED_EXIT}


def _invalid(message: str, path: str, **details: Any) -> wc.WorkflowError:
    return wc.WorkflowError("SPEC_INVALID", f"{path}: {message}", path=path, **details)


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _object(value: Any, path: str, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _invalid("must be an object", path)
    unknown = sorted(set(value) - set(keys))
    if unknown:
        raise _invalid(f"unknown key(s) {', '.join(unknown)}; known: {', '.join(keys)}", path, unknown=unknown)
    return value


def _numbers(item: dict[str, Any], path: str, names: tuple[str, ...]) -> None:
    for name in names:
        if item.get(name) is not None and not _number(item[name]):
            raise _invalid("must be a finite number", f"{path}.{name}")


# --------------------------------------------------------------------------------------------- spec

def _base_identity(document: dict[str, Any]) -> dict[str, Any]:
    """The Engine's check of the base Project: ``project_hash``, ``engine_input_hash``, ``catalog_version``; refuses a
    Project the Engine does not accept (PROJECT_REJECTED)."""
    checked = wc.check(document)
    if not checked.get("ok"):
        raise wc.WorkflowError("PROJECT_REJECTED", "os-optics refused the base Project; nothing was evaluated",
                               problems=(checked.get("error") or {}).get("problems", []))
    return {key: checked.get(key) for key in ("project_hash", "engine_input_hash", "catalog_version")}


def _configured(document: dict[str, Any], configuration_id: Any) -> dict[str, Any]:
    configurations = [c.get("configuration_id") for c in document.get("configurations") or [] if isinstance(c, dict)]
    if not configurations:
        if configuration_id is not None:
            raise _invalid("the Project declares no configurations", "configuration")
        return document
    if configuration_id is None:
        raise wc.WorkflowError("CONFIGURATION_REQUIRED", "the Project declares configurations: name one with "
                               "\"configuration\" (it is flattened into a Project of that state)",
                               configurations=configurations)
    if configuration_id not in configurations:
        raise _invalid(f"unknown configuration {configuration_id!r}", "configuration", configurations=configurations)
    from os_optics.local.configurations import configured_project

    return configured_project(document, configuration_id).payload()


def _project(spec: dict[str, Any], spec_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """The base Project document and where it came from."""
    given = spec.get("project")
    if isinstance(given, dict):
        return copy.deepcopy(given), {"source": "inline", "file": None}
    if not isinstance(given, str) or not given:
        raise _invalid("give the base Project: a path relative to the spec file, or the Project object", "project")
    path = Path(os.path.normpath(spec_dir / given))
    try:
        document = wc.read_json(path)
    except (OSError, ValueError) as exc:
        raise _invalid(f"cannot read {path.as_posix()}: {exc}", "project") from None
    if not isinstance(document, dict):
        raise _invalid(f"{path.as_posix()} is not a Project object", "project")
    return document, {"source": "file", "file": path.as_posix(), "sha256": wc.sha256_file(path)}


def _variable(item: Any, index: int, base: dict[str, Any] | None) -> dict[str, Any]:
    from .sensitivity_analysis import common_parameter, quantity

    path = f"variables[{index}]"
    _object(item, path, VARIABLE_KEYS)
    try:  # the parameter rules of sensitivity and tolerance: name, unit, ops with "$value", nominal from the base
        resolved = common_parameter(item, index, base=base)
    except wc.WorkflowError as exc:
        raise wc.WorkflowError(exc.code, str(exc).replace(f"parameters[{index}]", path), **{**exc.details,
                                                                                            "path": path}) from None
    bounds = item.get("bounds")
    if not isinstance(bounds, list) or len(bounds) != 2:
        raise _invalid("must be [low, high]", f"{path}.bounds")
    low, high = (quantity(b, resolved["unit"], f"{path}.bounds") for b in bounds)
    if not low < high:
        raise _invalid("low must be below high", f"{path}.bounds")
    scale = item.get("scale", 1.0)
    if not (_number(scale) and scale > 0):
        raise _invalid("must be a positive finite number", f"{path}.scale")
    if not low <= resolved["nominal"] <= high:
        raise _invalid(f"the nominal {resolved['nominal']:.12g} ({resolved['nominal_source']}) lies outside the bounds "
                       f"[{low:g}, {high:g}]", f"{path}.nominal", nominal=resolved["nominal"],
                       nominal_source=resolved["nominal_source"])
    return {**resolved, "bounds": [low, high], "scale": float(scale)}


def _items(spec: dict[str, Any], key: str, keys: tuple[str, ...], numbers: tuple[str, ...]) -> list[dict[str, Any]]:
    items = spec.get(key) or []
    if not isinstance(items, list):
        raise _invalid("must be a list", key)
    for index, item in enumerate(items):
        _object(item, f"{key}[{index}]", keys)
        if not isinstance(item.get("name"), str) or not item["name"]:
            raise _invalid("needs a name", f"{key}[{index}]")
        if "source" not in item:
            raise _invalid("needs a source", f"{key}[{index}]")
        _numbers(item, f"{key}[{index}]", numbers)
    return items


def _settings(spec: dict[str, Any], method: str | None, max_evaluations: int | None) -> Settings:
    solver = _object(spec.get("solver") or {}, "solver", SOLVER_KEYS)
    tolerances = _object(solver.get("tolerances") or {}, "solver.tolerances", TOLERANCE_KEYS)
    values = {k: solver[k] for k in ("method", "max_evaluations", "max_iterations", "diff_step", "difference",
                                      "x_scale") if solver.get(k) is not None}
    values.update({k: tolerances[k] for k in TOLERANCE_KEYS if tolerances.get(k) is not None})
    if method is not None:
        values["method"] = method
    if max_evaluations is not None:
        values["max_evaluations"] = max_evaluations
    try:
        return Settings(**values)
    except SettingsError as exc:
        raise _invalid(str(exc), "solver") from None


def _verify(spec: dict[str, Any], base: dict[str, Any], edit: Callable[..., Any]) -> dict[str, Any]:
    verify = _object(spec.get("verify") or {}, "verify", VERIFY_KEYS)
    ops = verify.get("ops") or []
    if not isinstance(ops, list):
        raise _invalid("must be a list of edit ops", "verify.ops")
    if ops:
        try:
            edit(base, copy.deepcopy(ops))
        except Exception as exc:  # noqa: BLE001 - an op the edit refuses on the base Project
            raise _invalid(f"the ops cannot be applied: {exc}", "verify.ops",
                           error=getattr(exc, "as_dict", lambda: None)()) from None
    imaging = verify.get("imaging") or {}
    if not isinstance(imaging, dict):
        raise _invalid("must be an imaging request object", "verify.imaging")
    tolerances = _object(verify.get("tolerances") or {}, "verify.tolerances", ("relative", "absolute"))
    for key in ("relative", "absolute"):
        if key in tolerances and not (_number(tolerances[key]) and tolerances[key] >= 0):
            raise _invalid("must be a finite number >= 0", f"verify.tolerances.{key}")
    return {"ops": ops, "imaging": imaging, "tolerances": {**DEFAULT_REPRODUCTION, **tolerances}}


def load_spec(spec: Any, *, spec_dir: str | Path | None = None, method: str | None = None,
              max_evaluations: int | None = None, edit: Callable[..., Any] | None = None) -> dict[str, Any]:
    """The validated spec, resolved: ``{"spec": <spec as run>, "problem": Problem, "settings": Settings, "verify",
    "base": <base Project>, "project": <its origin>, "spec_sha256"}``. ``spec``: a path or the parsed object
    (relative paths then resolve against ``spec_dir``, default the working directory). Raises ``WorkflowError``
    before any evaluation (module docstring)."""
    from .study import evaluation_adapter as ea

    if isinstance(spec, (str, Path)):
        path = Path(spec)
        try:
            raw = path.read_bytes()
            document = json.loads(raw.decode("utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise wc.WorkflowError("SPEC_INVALID", f"cannot read {path.as_posix()}: {exc}", path="") from None
        spec_dir, digest = path.parent, wc.sha256_bytes(raw)
    else:
        document, digest = copy.deepcopy(spec), None
        spec_dir = Path(spec_dir or ".")
    document = _object(document, "spec", SPEC_KEYS)
    if document.get("schema", SPEC_SCHEMA) != SPEC_SCHEMA or document.get("schema_version", 1) != SCHEMA_VERSION:
        raise _invalid(f"this command reads {SPEC_SCHEMA} v{SCHEMA_VERSION}", "schema")
    if "description" in document and not isinstance(document["description"], str):
        raise _invalid("must be text", "description")
    base, origin = _project(document, Path(spec_dir))
    base = _configured(base, document.get("configuration"))
    variables = document.get("variables")
    if not isinstance(variables, list) or not variables:
        raise _invalid("give at least one variable", "variables")
    resolved = [_variable(item, index, base if edit is None else None) for index, item in enumerate(variables)]
    objectives = _items(document, "objectives", OBJECTIVE_KEYS, ("goal", "scale", "weight"))
    constraints = _items(document, "constraints", CONSTRAINT_KEYS, ("minimum", "maximum", "scale"))
    for key in ("invalid_residual", "active_margin"):
        if key in document and not (_number(document[key]) and document[key] > 0):
            raise _invalid("must be a positive finite number", key)
    imaging = document.get("imaging") or {}
    if not isinstance(imaging, dict):
        raise _invalid("must be an imaging request object", "imaging")
    settings = _settings(document, method, max_evaluations)
    try:
        problem = ea.Problem(
            project=base,
            variables=[ea.Variable(v["name"], v["ops"], tuple(v["bounds"]), nominal=v["nominal"], scale=v["scale"],
                                   unit=v["unit"]) for v in resolved],
            objectives=[ea.Objective(o["name"], o["source"], **{k: o[k] for k in ("goal", "scale", "weight") if k in o})
                        for o in objectives],
            constraints=[ea.Constraint(c["name"], c["source"], **{k: c[k] for k in ("minimum", "maximum", "scale")
                                                                   if k in c}) for c in constraints],
            aggregation=document.get("aggregation", "sum_squares"), imaging=imaging,
            invalid_residual=document.get("invalid_residual", ea.DEFAULT_INVALID_RESIDUAL),
            value_use=wc.value_use_setting(document.get("value_use")), ray_loss=document.get("ray_loss", "infeasible"))
    except ea.ProblemError as exc:
        raise wc.WorkflowError("SPEC_INVALID", str(exc), path="problem") from None
    if settings.method == "trf" and problem.aggregation != "sum_squares":
        raise _invalid("trf solves the residuals' sum of squares (dls_compatible): use aggregation sum_squares, or "
                       "method slsqp", "solver.method", method="trf", aggregation=problem.aggregation)
    verify = _verify(document, base, edit or _apply_ops)
    run_spec = {"schema": SPEC_SCHEMA, "schema_version": SCHEMA_VERSION, "description": document.get("description", ""),
                "project": base, "configuration": None,  # the Project inlined is the configured one
                "variables": [{"name": v["name"], "unit": v["unit"], "ops": v["ops"], "bounds": v["bounds"],
                               "nominal": v["nominal"], "scale": v["scale"]} for v in resolved],
                "objectives": objectives, "constraints": constraints, "aggregation": problem.aggregation,
                "value_use": problem.value_use, "imaging": imaging, "invalid_residual": problem.invalid_residual,
                "ray_loss": problem.ray_loss, "active_margin": document.get("active_margin", DEFAULT_ACTIVE_MARGIN),
                "solver": settings.as_dict(), "verify": verify}
    run_spec["solver"]["tolerances"] = {k: run_spec["solver"].pop(k) for k in TOLERANCE_KEYS}
    variables_used = [{"name": v["name"], "unit": v["unit"], "bounds": v["bounds"], "scale": v["scale"],
                       "nominal": v["nominal"], "nominal_source": v["nominal_source"]} for v in resolved]
    return {"spec": run_spec, "problem": problem, "settings": settings, "verify": verify, "base": base,
            "project": {**origin, "configuration": document.get("configuration")}, "spec_sha256": digest,
            "active_margin": run_spec["active_margin"], "variables": variables_used}


def _apply_ops(document: dict[str, Any], ops: list[dict[str, Any]]) -> dict[str, Any]:
    from .edit_project import apply_edits

    return apply_edits(document, ops, validate=False)["project"]


# --------------------------------------------------------------------------------------------- result

def _conditions(entry: dict[str, Any], targets: dict[Any, dict[str, Any]]) -> dict[str, Any]:
    """Where an objective's or constraint's number was measured: target, plane, source, wavelength, quantity."""
    source = entry.get("source") or {}
    out: dict[str, Any] = {"source_kind": next(iter(source), None)}
    target = source.get("target") or (source.get("rays") or {}).get("target")
    if target is not None:
        declared = targets.get(target) or {}
        out.update(target=target, metric=declared.get("metric"),
                   observation_id=declared.get("observation_id"), source_id=declared.get("source_id"),
                   wavelength_nm=declared.get("wavelength_nm"))
    for key in ("quantity_id", "source_id", "field_id", "wavelength_nm", "binding", "pupil_rays_launched",
                "pupil_rays_arrived"):
        if entry.get(key) is not None:
            out[key] = entry[key]
    if "paraxial" in source:
        out.update(components=source["paraxial"]["components"], wavelength_nm=source["paraxial"]["wavelength_nm"])
    return out


def candidate_summary(record: solvers.EvaluationRecord, problem: Any, active_margin: float) -> dict[str, Any]:
    """The facts of one evaluated candidate for ``result.json``: values, margins, active constraints, the worst
    residual with its conditions, ray loss, the reason of an invalid candidate and the identity of its numbers."""
    result = record.result
    targets = {t.get("target_id"): t for t in problem.project.get("targets") or [] if isinstance(t, dict)}
    entries = [*result.objectives, *result.constraints]
    worst = None
    if result.valid and result.residuals:
        at = max(range(len(result.residuals)), key=lambda i: (abs(result.residuals[i]), -i))
        worst = {"name": result.residual_names[at], "residual": result.residuals[at],
                 "value": entries[at].get("value") if at < len(entries) else None,
                 "conditions": _conditions(entries[at], targets) if at < len(entries) else None}
    identity = result.identity or {}
    return {
        "index": record.index, "role": record.role, "candidate": record.x, "valid": result.valid,
        "feasible": result.feasible, "objective": result.objective, "residual_objective": result.residual_objective,
        "objectives": [{"name": o["name"], "value": o["value"], "goal": o["goal"], "scaled_error": o["scaled_error"],
                        "value_kind": o.get("value_kind"), "conditions": _conditions(o, targets)}
                       for o in result.objectives],
        "constraints": [{"name": c["name"], "value": c["value"], "minimum": c["minimum"], "maximum": c["maximum"],
                         "margin": c["margin"], "satisfied": c["satisfied"],
                         "active": abs(c["margin"]) <= active_margin, "value_kind": c.get("value_kind"),
                         "conditions": _conditions(c, targets)} for c in result.constraints],
        "active_constraints": [c["name"] for c in result.constraints if abs(c["margin"]) <= active_margin],
        "worst_residual": worst, "infeasible": list(result.infeasible), "ray_loss": result.ray_loss,
        "reason": result.reason,
        "identity": {k: identity.get(k) for k in ("project_hash", "engine_input_hash", "result_hash")}}


def history_line(record: solvers.EvaluationRecord) -> dict[str, Any]:
    """One ``history.jsonl`` line: a physical evaluation (cached repeats are not lines)."""
    result = record.result
    reason = result.reason or {}
    return {"index": record.index, "role": record.role, "candidate": record.x, "valid": result.valid,
            "feasible": result.feasible, "objective": result.objective,
            "residual_objective": result.residual_objective,
            "objectives": {o["name"]: o["value"] for o in result.objectives},
            "constraints": {c["name"]: {"value": c["value"], "margin": c["margin"], "satisfied": c["satisfied"]}
                            for c in result.constraints},
            "residuals": dict(zip(result.residual_names, result.residuals)),
            "infeasible": list(result.infeasible),
            "rays_lost": sum(e["pupil_rays_lost"] for e in result.ray_loss),
            "reason": reason.get("code"), "reason_message": reason.get("message"),
            "seconds": round(record.seconds, 6), "cache_hit": False,
            "engine_input_hash": (result.identity or {}).get("engine_input_hash"),
            "result_hash": (result.identity or {}).get("result_hash")}


def infeasible_reasons(records: list[solvers.EvaluationRecord]) -> dict[str, int]:
    """Counts of why evaluated candidates are not feasible: an invalid candidate's reason code, a valid one's
    ``infeasible`` codes, ``FEASIBILITY_UNDETERMINED`` when a constraint is not established."""
    counts: dict[str, int] = {}
    for record in records:
        result = record.result
        if not result.valid:
            codes = [(result.reason or {}).get("code") or "INVALID"]
        elif result.feasible is True:
            continue
        else:
            codes = list(result.infeasible) or ["FEASIBILITY_UNDETERMINED"]
        for code in codes:
            counts[code] = counts.get(code, 0) + 1
    return dict(sorted(counts.items()))


def _readings(result: Any) -> dict[str, float | None]:
    """Every number of a candidate a reproduction compares: values, margins, residuals, objective."""
    out: dict[str, float | None] = {"objective": result.objective, "residual_objective": result.residual_objective}
    for o in result.objectives:
        out[f"objective:{o['name']}"] = o["value"]
    for c in result.constraints:
        out[f"constraint:{c['name']}"] = c["value"]
        out[f"margin:{c['name']}"] = c["margin"]
    out.update({f"residual:{n}": r for n, r in zip(result.residual_names, result.residuals)})
    return out


def _compare(before: dict[str, float | None], after: dict[str, float | None], tolerances: dict[str, float]) -> dict:
    worst_abs, worst_rel, outside = 0.0, 0.0, []
    for key in before:
        a, b = before[key], after.get(key)
        if a is None or b is None:
            if a != b:
                outside.append(key)
            continue
        difference = abs(b - a)
        worst_abs = max(worst_abs, difference)
        worst_rel = max(worst_rel, difference / abs(a) if a else (0.0 if difference == 0 else math.inf))
        if difference > tolerances["absolute"] + tolerances["relative"] * abs(a):
            outside.append(key)
    return {"max_abs_difference": worst_abs, "max_rel_difference": worst_rel, "outside_tolerance": outside,
            "within_tolerance": not outside}


def _same_problem(problem: Any, project: dict[str, Any], imaging: dict[str, Any] | None = None) -> Any:
    """``problem`` on a fixed ``project``: no variables, the same objectives, constraints and rules."""
    from .study import evaluation_adapter as ea

    return ea.Problem(project=project, variables=[], objectives=problem.objectives, constraints=problem.constraints,
                      aggregation=problem.aggregation, imaging=problem.imaging if imaging is None else imaging,
                      invalid_residual=problem.invalid_residual, value_use=problem.value_use,
                      ray_loss=problem.ray_loss)


def _verification(loaded: dict[str, Any], project: dict[str, Any], best: solvers.EvaluationRecord,
                  session_factory: Callable[[], Any], adapter_options: dict[str, Any]) -> dict[str, Any]:
    """Evaluate the saved best Project (``project``, read back from best-project.json) in a fresh session and compare
    every number with the search's; then evaluate it again with the ``verify`` ops and imaging applied."""
    from .study import evaluation_adapter as ea

    problem, verify = loaded["problem"], loaded["verify"]
    started = time.perf_counter()
    again = ea.Adapter(_same_problem(problem, project), session=session_factory(), **adapter_options).evaluate({})
    before = _readings(best.result)
    comparison = _compare(before, _readings(again), verify["tolerances"])
    same = {k: (best.result.identity or {}).get(k) == (again.identity or {}).get(k)
            for k in ("engine_input_hash", "result_hash")}
    reproduction = {"project_file": "best-project.json", "fresh_session": True, "tolerances": verify["tolerances"],
                    "valid": again.valid, "feasible": again.feasible, "identity_equal": same, **comparison,
                    "status": "REPRODUCED" if comparison["within_tolerance"] and again.feasible is True else "MISMATCH",
                    "seconds": round(time.perf_counter() - started, 3)}
    out: dict[str, Any] = {"reproduction": reproduction, "dense": None, "evaluations": 1}
    if not verify["ops"] and not verify["imaging"]:
        return out
    started = time.perf_counter()
    edit = adapter_options.get("edit") or _apply_ops
    dense_project = edit(project, copy.deepcopy(verify["ops"])) if verify["ops"] else project
    dense = ea.Adapter(_same_problem(problem, dense_project, {**problem.imaging, **verify["imaging"]}),
                       session=session_factory(), **adapter_options).evaluate({})
    after = _readings(dense)
    changes = []
    for key, value in before.items():
        if key.startswith(("objective:", "constraint:", "margin:")) or key == "objective":
            new = after.get(key)
            changes.append({"quantity": key, "before": value, "after": new,
                            "difference": None if value is None or new is None else new - value})
    out["dense"] = {"ops": verify["ops"], "imaging": verify["imaging"], "valid": dense.valid,
                    "reason": dense.reason, "feasible_before": best.result.feasible, "feasible_after": dense.feasible,
                    "feasibility_changed": dense.feasible != best.result.feasible, "changes": changes,
                    "infeasible": list(dense.infeasible), "ray_loss": dense.ray_loss,
                    "identity": {k: (dense.identity or {}).get(k) for k in ("project_hash", "engine_input_hash",
                                                                          "result_hash")},
                    "seconds": round(time.perf_counter() - started, 3)}
    out["evaluations"] = 2
    return out


def _status(outcome: solvers.SolveOutcome, chosen: solvers.EvaluationRecord | None) -> str:
    if outcome.reason == "INTERRUPTED":
        return "INTERRUPTED"
    if chosen is None:
        return "NO_FEASIBLE"
    # CONVERGED only where the solver converged on a feasible point; a feasible best found elsewhere is STOPPED
    return "CONVERGED" if outcome.converged else "STOPPED"


def run(spec: Any, out_dir: str | Path, *, spec_dir: str | Path | None = None, method: str | None = None,
        max_evaluations: int | None = None, overwrite: bool = False, session: Any = None,
        session_factory: Callable[[], Any] | None = None,
        adapter_options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate ``spec``, search, verify the best candidate and write DIR (module docstring); returns ``result.json``.

    ``session``: the Engine session of the search (default: a new ``EvaluationSession``); ``session_factory``: makes
    the fresh sessions of the verification (default: ``EvaluationSession``); ``adapter_options``: ``edit`` /
    ``paraxial`` functions passed to every :class:`~os_optics_kit.study.evaluation_adapter.Adapter`."""
    from .study import evaluation_adapter as ea

    adapter_options = dict(adapter_options or {})
    out = Path(out_dir)
    loaded = load_spec(spec, spec_dir=spec_dir, method=method, max_evaluations=max_evaluations,
                       edit=adapter_options.get("edit"))
    base_identity = _base_identity(loaded["base"])
    inputs = (Path(spec) if isinstance(spec, (str, Path)) else None, loaded["project"].get("file"))
    wc.prepare_output(out, overwrite, FILES, keep=inputs)  # a stale best-project.json must not outlive a rerun
    if session_factory is None:
        def session_factory() -> Any:
            from os_optics.local import EvaluationSession

            return EvaluationSession()
    problem, settings = loaded["problem"], loaded["settings"]
    adapter = ea.Adapter(problem, session=session if session is not None else session_factory(), **adapter_options)
    wc.write_json(out / "spec.json", loaded["spec"])
    history = out / "history.jsonl"
    with open(history, "w", encoding="utf-8", newline="\n") as handle:
        def record(entry: solvers.EvaluationRecord) -> None:
            handle.write(json.dumps(wc.json_safe(history_line(entry)), ensure_ascii=True, allow_nan=False,
                                    separators=(",", ":")) + "\n")
            handle.flush()

        outcome = solve(adapter, settings, record)
    records = outcome.evaluations
    chosen = solvers.best(records)
    status = _status(outcome, chosen)
    verification, best = None, None
    if chosen is not None:
        best = candidate_summary(chosen, problem, loaded["active_margin"])
        best["project_file"] = "best-project.json"
        wc.write_json(out / "best-project.json", adapter.candidate(chosen.x))
        if status != "INTERRUPTED":
            try:
                verification = _verification(loaded, wc.read_json(out / "best-project.json"), chosen,
                                             session_factory, adapter_options)
            except KeyboardInterrupt:
                status = "INTERRUPTED"
    start = candidate_summary(records[0], problem, loaded["active_margin"]) if records else None
    nearest = None if chosen is not None else solvers.closest(records)
    first = (records[0].result.identity or {}) if records else {}
    roles: dict[str, int] = {}
    for r in records:
        roles[r.role] = roles.get(r.role, 0) + 1
    result = {
        "schema": RESULT_SCHEMA, "schema_version": SCHEMA_VERSION, "status": status,
        "description": loaded["spec"]["description"],
        "termination": {"reason": outcome.reason, "method": outcome.method, "solver_message": outcome.message,
                        "solver_status": outcome.status, "solver_x": outcome.solver_x,
                        "solver_index": outcome.solver_record.index if outcome.solver_record is not None else None},
        "counts": {"evaluations": len(records), "by_role": dict(sorted(roles.items())),
                   "cache_hits": outcome.cache_hits, "iterations": outcome.iterations,
                   "solver_calls": outcome.solver_calls, "max_evaluations": settings.max_evaluations,
                   "seconds": round(outcome.seconds, 3),
                   "verification_evaluations": 0 if verification is None else verification["evaluations"]},
        "variables": loaded["variables"],
        "best": best, "start": start,
        "closest": None if nearest is None else candidate_summary(nearest, problem, loaded["active_margin"]),
        "feasible_count": sum(1 for r in records if r.result.valid and r.result.feasible is True),
        "infeasible_reasons": infeasible_reasons(records),
        "verification": verification,
        "settings": loaded["spec"]["solver"],
        "identity": {"engine_version": first.get("engine_version"), "engine_build_id": first.get("engine_build_id"),
                     "catalog_version": base_identity.get("catalog_version"),
                     "base_project_hash": base_identity.get("project_hash"),
                     "base_engine_input_hash": base_identity.get("engine_input_hash"),
                     "start_reproduces_base": first.get("project_hash") == base_identity.get("project_hash")
                     if first else None,
                     "spec_sha256": loaded["spec_sha256"], "spec_as_run_sha256": wc.sha256_file(out / "spec.json"),
                     "project": loaded["project"]},
        "files": {"spec": "spec.json", "history": "history.jsonl", "best_project": None if best is None
                  else "best-project.json"},
    }
    wc.write_json(out / "result.json", result)
    return result


def summary(result: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    """The compact stdout document of the CLI: status, why it stopped, counts, the best candidate's key facts."""
    best = result.get("best")
    verification = result.get("verification") or {}
    reproduction = verification.get("reproduction") or {}
    dense = verification.get("dense") or {}
    return {
        "status": result["status"], "out": out_dir.as_posix(), "termination": result["termination"]["reason"],
        "solver_message": result["termination"]["solver_message"],
        "evaluations": result["counts"]["evaluations"], "cache_hits": result["counts"]["cache_hits"],
        "seconds": result["counts"]["seconds"], "feasible_count": result["feasible_count"],
        "infeasible_reasons": result["infeasible_reasons"],
        "best": None if best is None else {
            "candidate": best["candidate"], "objective": best["objective"],
            "active_constraints": best["active_constraints"],
            "worst_residual": None if best["worst_residual"] is None else {
                k: best["worst_residual"][k] for k in ("name", "residual", "value")},
            "reproduction": reproduction.get("status"),
            "dense_feasible": dense.get("feasible_after") if dense else None},
        "closest": None if not result.get("closest") else {
            "candidate": result["closest"]["candidate"],
            "margins": {c["name"]: c["margin"] for c in result["closest"]["constraints"]},
            "infeasible": result["closest"]["infeasible"]},
        "files": {k: None if v is None else (out_dir / v).as_posix() for k, v in result["files"].items()}}


# --------------------------------------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    parser = wc.ArgumentParser(prog=PROG, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("spec", type=Path, help="optimize spec (os-optics-kit.optimize-spec v1)")
    parser.add_argument("--out", type=Path, required=True, help="output directory (new or empty)")
    parser.add_argument("--method", choices=solvers.METHODS, help="solver (overrides the spec's solver.method)")
    parser.add_argument("--max-evaluations", type=int, help="evaluation budget (overrides solver.max_evaluations)")
    parser.add_argument("--overwrite", action="store_true", help="replace the files of an earlier run in --out")

    def workflow() -> dict[str, Any]:
        args = parser.parse_args(argv)
        result = run(args.spec, args.out, method=args.method, max_evaluations=args.max_evaluations,
                     overwrite=args.overwrite)
        return summary(result, args.out)

    return wc.emit(workflow, EXIT_CODES)


__all__ = ["EXIT_CODES", "RESULT_SCHEMA", "SPEC_SCHEMA", "STATUSES", "candidate_summary", "history_line",
           "infeasible_reasons", "load_spec", "main", "run", "solve", "summary"]
