"""Finite-difference sensitivity of selected targets to explicit design parameters; every sample is saved.

Usage:
  python examples/sensitivity_analysis.py SPEC.json --out STUDY_DIR [--base BASE.json] [--overwrite]
                                          [--no-plots] [--no-report]

SPEC.json (keys; quantities are a bare number in the stated unit or {"value": x, "unit": "u"}):
  {
    "base_project": "base.json",                 # relative to SPEC.json; --base overrides. Or, instead:
    "design": {"builder": "expander_focus",      # a builder of optical_designs.py (catalog-free designs need no
               "arguments": {"f3_mm": 100}},     # catalog); arguments in the units of their names
    "targets": ["spot_radius"],                  # target ids to differentiate (default: all targets)
    "parameters": [
      {"name": "L2_axial", "unit": "mm",         # unit of nominal, steps, scale and the "$value" placeholder
       "ops": [{"op": "transform", "selector": {"ids": ["L2"]}, "frame": {"component": "L2"},
                "translation_mm": [0, 0, "$value"]}],
       "nominal": 0,                             # parameter value of the base Project (default 0: the ops move by
                                                 # the perturbation; give the field's value for set_* ops)
       "steps": [0.01, 0.1],                     # >= 2 distinct step sizes h (or {"relative": r}: r * |nominal|)
       "scale": {"value": 50, "unit": "um"}}     # optional normalization scale, e.g. the parameter's tolerance
    ],
    "consistency_threshold": 0.01,               # relative difference of the two step estimates that is flagged
    "resolution": {"spot_radius": {"value": 1e-9, "unit": "mm"}},   # optional: target change treated as none
    "max_evaluations": 20                        # required; refused before any run when 1 + 2 * steps exceeds it
  }

Parameters use the edit vocabulary of edit_project.py and the placeholder syntax of sweep_parameters.py ("$value"
or {"$value": {"scale", "offset"}}), substituted with nominal + perturbation. Position: op "transform" with
"translation_mm" and an explicit "frame" (e.g. {"component": "L2"}: along the element's own axes). Angle: op
"transform" with "rotation": {"axis": [...], "angle_deg": "$value"} and explicit "frame" and "pivot" (angle units
deg/rad/mrad/urad). Scalar fields: set_* ops with "nominal" equal to the base value. Before any run the ops of every
parameter at zero perturbation must reproduce the base Project hash (else NOMINAL_MISMATCH), every sample's ops must
normalize (unit errors refused) and the base Project must pass json_api.check_project.
Default steps when "steps" is omitted: 0.01 and 0.1 mm for translations, 0.01 and 0.1 deg for rotations, relative
1e-3 and 1e-2 of |nominal| for other fields.

Method, per parameter p, target t and step h (f0: nominal value; f+/f-: value at nominal +h / -h):
  central  df/dp = (f+ - f-) / (2h), truncation error O(h^2); second difference (f+ - 2 f0 + f-) / h^2
  forward  (f+ - f0) / h, or backward (f0 - f-) / h, O(h), when only one side is usable (labelled)
A side is usable when the target has a finite exact value with applicable dependencies; the nominal must be usable for the one-sided forms and the second difference. The primary estimate is the
smallest step with a central difference (else the smallest usable step); the reported second difference is the one
of the larger of the two compared steps (round-off grows as 1/h^2). Step consistency compares the primary estimate
with the next usable step: ratio = D(primary) / D(other), relative difference |D1 - D2| / max(|D1|, |D2|), flagged
STEP_DEPENDENT above consistency_threshold. Change floor per target: the larger of the target's "resolution" and
64 machine epsilons (1.42e-14) times its largest magnitude in the pair; two estimates whose changes |D| h are both
within the floor count as consistent. NEAR_EXTREMUM: |f''| h^2 / 2 exceeds the floor and |D| h <= |f''| h^2 / 2 at
the second-difference step (the first derivative is not a useful sensitivity there; use the second difference).
Other flags: ONE_SIDED, METHOD_DIFFERS, NO_CHANGE (every target change within the floor), STEP_CHECK_UNAVAILABLE
(one usable step), UNAVAILABLE.
Units: derivative in target unit per parameter unit, second difference per parameter unit squared.
Normalized sensitivity (with "scale" s): scaled = D * s (target unit: first-order change for one scale of the
parameter) and second_order_at_scale = f'' * s^2 / 2. Ranking per target by taylor_change_at_scale =
|D| s + |f''| s^2 / 2 when every parameter has a scale; otherwise by |D| when all parameters share one unit;
otherwise only parameters with a scale are ranked and the others are listed as not ranked.

Writes STUDY_DIR/study.json (spec as given and normalized, base project hash, engine / catalog identity,
environment, budget, timestamps), samples/<id>/{project.json, run/, sample.json} (s0000 = nominal), table.csv
(one row per sample, param:<name> = perturbation from nominal), sensitivity.csv (one row per parameter, target and
step), summary.json, plots/ (sensitivity bars and tornado per target, PNG + SVG; needs matplotlib, see
requirements-examples.txt, otherwise summary.json records why no plot was written) and report/ (report.md,
report.json via build_report.py).

Stdout: one compact JSON document (nominal values, per-target ranking with derivatives, units, methods, flags,
outcome counts, unusable samples, paths). Exit 0 completed (every derivative has a value), 2 some derivative
unavailable, 4 refused spec or invalid input.
"""

from __future__ import annotations

import argparse
import copy
import math
from pathlib import Path
import shutil
import time

if __package__:
    from . import workflow_common as wc
    from . import sweep_parameters as sweep
    from .edit_project import EditError, apply_edits, normalize_ops
else:
    import workflow_common as wc
    import sweep_parameters as sweep
    from edit_project import EditError, apply_edits, normalize_ops

KIND = "opensci-example.sensitivity"
EXIT_CODES = {**wc.WORKFLOW_EXIT_CODES, "INCOMPLETE": 2}
DEFAULT_THRESHOLD = 0.01
#: target changes at or below this fraction of the target's magnitude are treated as floating-point round-off
ROUNDOFF_RELATIVE = 64 * 2.220446049250313e-16
KNOWN_UNITS = ("nm", "um", "µm", "mm", "m", "deg", "rad", "mrad", "urad", "W", "mW", "uW")
STUDY_FILES = ("study.json", "table.csv", "summary.json", "sensitivity.csv", "samples.csv", "sampling.json")
STUDY_DIRS = ("samples", "plots", "report")
TOP_UNUSABLE = 5
METHOD_TEXT = {
    "central": "(f(+h) - f(-h)) / (2h), O(h^2)",
    "forward": "(f(+h) - f(0)) / h, O(h); the -h side is not usable",
    "backward": "(f(0) - f(-h)) / h, O(h); the +h side is not usable",
}


# --------------------------------------------------------------------------------------------- shared study parts

def resolve_base(spec, *, spec_dir=None, base=None, base_path=None, build=None):
    """The base Project and its description: ``base`` (a document), ``build`` (a function of the parameter values),
    the spec's ``design`` (an optical_designs builder) or its ``base_project`` file (relative to ``spec_dir``)."""
    if build is not None:
        return None, {"source": "build_function", "build_function": getattr(build, "__qualname__", repr(build))}
    if base is not None:
        return copy.deepcopy(base), {"source": "document", "file": None if base_path is None
                                     else Path(base_path).as_posix()}
    design = spec.get("design")
    if design is not None:
        if __package__:
            from . import optical_designs
        else:
            import optical_designs
        name = design.get("builder") if isinstance(design, dict) else None
        if name not in optical_designs.DESIGN_INFO:
            raise wc.WorkflowError("SPEC_INVALID",
                                   f"design.builder must be one of {sorted(optical_designs.DESIGN_INFO)}",
                                   builder=name)
        arguments = design.get("arguments") or {}
        try:
            document = getattr(optical_designs, name)(**arguments)
        except TypeError as exc:
            raise wc.WorkflowError("SPEC_INVALID", f"design.arguments: {exc}", builder=name) from None
        except Exception as exc:  # noqa: BLE001 - e.g. a catalog design without a catalog
            error = getattr(exc, "error", None)
            raise wc.WorkflowError(getattr(exc, "code", None) or "DESIGN_UNAVAILABLE", str(exc), builder=name,
                                   error=error) from None
        return document, {"source": "optical_designs", "builder": name, "arguments": arguments}
    if spec.get("base_project"):
        path = Path(spec_dir or ".") / spec["base_project"] if base_path is None else Path(base_path)
        return wc.read_json(path), {"source": "file", "file": path.as_posix()}
    if base_path is not None:
        return wc.read_json(base_path), {"source": "file", "file": Path(base_path).as_posix()}
    raise wc.WorkflowError("SPEC_INVALID", "give base_project or design in the spec, or --base")


def common_parameter(parameter, index, *, build=None):
    """Name, unit, nominal (in the parameter's unit) and op templates of one parameter."""
    if not isinstance(parameter, dict):
        raise wc.WorkflowError("SPEC_INVALID", f"parameters[{index}] must be an object")
    name = parameter.get("name")
    if not isinstance(name, str) or not name:
        raise wc.WorkflowError("SPEC_INVALID", f"parameters[{index}] needs a name")
    unit = parameter.get("unit")
    if unit is not None and unit not in wc.UNITLESS and unit not in KNOWN_UNITS:
        raise wc.WorkflowError("UNIT_UNKNOWN", f"parameter {name!r}: unknown unit {unit!r}; known: "
                               f"{list(KNOWN_UNITS)} or omitted for a plain number", unit=unit, parameter=name)
    nominal = quantity(parameter.get("nominal", 0.0), unit, f"parameter {name} nominal")
    ops = parameter.get("ops")
    if build is None:
        if not isinstance(ops, list) or not ops:
            raise wc.WorkflowError("SPEC_INVALID", f"parameter {name!r}: ops must be a non-empty list of op templates")
        if sweep.substitute(ops, 0.0, unit) == sweep.substitute(ops, 1.0, unit):
            raise wc.WorkflowError("SPEC_INVALID", f"parameter {name!r}: the ops contain no \"$value\" placeholder")
    return {"name": name, "unit": unit, "nominal": nominal, "ops": ops or []}


def quantity(value, unit, path):
    """A bare number in ``unit`` or {"value", "unit"} converted to ``unit``."""
    if wc.is_quantity(value):
        if unit is None or unit in wc.UNITLESS:
            if value["unit"] not in wc.UNITLESS:
                raise wc.WorkflowError("UNIT_INCOMPATIBLE", f"{path}: the parameter has no unit; got "
                                       f"{value['unit']!r}", path=path, unit=value["unit"])
            return float(value["value"])
        return float(wc.convert(value["value"], value["unit"], unit, path=path))
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise wc.WorkflowError("SPEC_INVALID", f"{path}: expected a finite number or a quantity, got {value!r}",
                               path=path)
    return float(value)


def parameter_ops(parameter, offset):
    """Op templates of ``parameter`` with "$value" = nominal + offset (in the parameter's unit)."""
    return sweep.substitute(copy.deepcopy(parameter["ops"]), parameter["nominal"] + offset, parameter["unit"])


def offset_document(base, parameters, offsets, *, build=None):
    """The Project at the given offsets from nominal: ``build`` of the applied values, or the ops of every parameter
    with an offset applied in parameter order to a copy of ``base``. Raises EditError for a refused op."""
    if build is not None:
        return build({p["name"]: p["nominal"] + offsets.get(p["name"], 0.0) for p in parameters})
    ops = []
    for parameter in parameters:
        if parameter["name"] in offsets:
            ops.extend(parameter_ops(parameter, offsets[parameter["name"]]))
    if not ops:
        return copy.deepcopy(base)
    return apply_edits(base, ops, validate=False)["project"]


def check_base(document, parameters, *, build=None):
    """Refuse an invalid base Project, unknown units in any op, and ops that change the base at zero offset."""
    checked = wc.check(document)
    if not checked.get("ok"):
        problems = checked["error"].get("problems", [])
        raise wc.WorkflowError("PROJECT_REJECTED", "os-optics refused the base Project; nothing was run",
                               problems=problems)
    if build is not None:
        return checked
    mismatched = []
    for parameter in parameters:
        try:
            zero = apply_edits(document, parameter_ops(parameter, 0.0), validate=False)["project"]
        except EditError as exc:
            raise wc.WorkflowError("SPEC_INVALID", f"parameter {parameter['name']!r}: {exc}",
                                   parameter=parameter["name"], error=exc.as_dict()) from None
        if wc.check(zero).get("project_hash") != checked["project_hash"]:
            mismatched.append(parameter["name"])
    if mismatched:
        raise wc.WorkflowError("NOMINAL_MISMATCH", "at zero perturbation the ops of these parameters change the base "
                               "Project; set each parameter's nominal to the base value of the field it edits",
                               parameters=mismatched)
    return checked


def selected_targets(document, targets):
    known = [t.get("target_id") for t in document.get("targets") or []]
    if targets is None:
        return known
    if not isinstance(targets, list) or not targets:
        raise wc.WorkflowError("SPEC_INVALID", "targets must be a non-empty list of target ids")
    unknown = [t for t in targets if t not in known]
    if unknown:
        raise wc.WorkflowError("TARGET_UNKNOWN", f"targets {unknown} are not in the base Project; known: {known}",
                               unknown=unknown, known=known)
    return list(targets)


def target_units(document):
    return {t.get("target_id"): t.get("unit") for t in document.get("targets") or []}


def prepare_dir(study_dir, overwrite):
    """A new or empty study directory; with ``overwrite`` this script family's files are removed first."""
    study_dir = Path(study_dir)
    if study_dir.exists() and any(study_dir.iterdir()):
        if not overwrite:
            raise wc.WorkflowError("OUTPUT_EXISTS", f"{study_dir} is not empty; choose a new directory or use "
                                   "--overwrite", out=study_dir.as_posix())
        for name in STUDY_DIRS:
            shutil.rmtree(study_dir / name, ignore_errors=True)
        for name in STUDY_FILES:
            (study_dir / name).unlink(missing_ok=True)
    study_dir.mkdir(parents=True, exist_ok=True)
    return study_dir


def run_one(base, parameters, offsets, sample_dir, *, build, targets):
    """Edit, run and record one sample (parameters missing from ``offsets`` stay at nominal); never raises for a
    refused edit or run."""
    started = time.perf_counter()
    try:
        document = offset_document(base, parameters, offsets, build=build)
    except EditError as exc:
        Path(sample_dir).mkdir(parents=True, exist_ok=True)
        record = wc.error_record("edit", exc.code, [exc.as_dict()], sample_dir=sample_dir,
                                 seconds=time.perf_counter() - started, selected=targets)
    else:
        record = wc.run_sample(document, sample_dir, targets=targets)
    record["parameters"] = {p["name"]: {"value": p["nominal"] + offsets.get(p["name"], 0.0),
                                        "offset": offsets.get(p["name"], 0.0), "unit": p["unit"]}
                            for p in parameters}
    return record


def problem_codes(problems):
    """Every problem code in a refusal, including nested ``problems`` / ``details.problems`` lists."""
    codes = []
    for problem in problems or []:
        if isinstance(problem, dict):
            codes.append(problem.get("code"))
            codes += problem_codes(problem.get("problems"))
            codes += problem_codes((problem.get("details") or {}).get("problems"))
    return codes


def target_value(record, target_id):
    """``(value, None)`` when ``record`` gives a usable value of ``target_id``, else ``(None, reason)``."""
    outcome = record["outcome"]
    if outcome == "ERROR":
        error = record.get("error") or {}
        codes = sorted(set(problem_codes(error.get("problems"))) - {None, error.get("code")})
        return None, f"ERROR {error.get('code')}" + (f" ({', '.join(codes)})" if codes else "")
    row = next((t for t in record.get("targets") or [] if t["id"] == target_id), None)
    if row is None:
        return None, "NO_VALUE (target not in the run)"
    if not wc.target_value_is_exact(row):
        codes = ", ".join(row.get("issue_codes") or []) or "no issue code"
        return None, f"NO_VALUE (status {row.get('status')}, {codes})"
    return row["value"], None


def append_report(study_dir, *, plots, title, lines, extra, artifacts=()):
    """build_report.py report of the study with a study-specific section, extra artifacts and a JSON key."""
    import inspect

    if __package__:
        from .build_report import build_report
    else:
        from build_report import build_report

    out_dir = Path(study_dir) / "report"
    if "sections" in inspect.signature(build_report).parameters:
        report = build_report([study_dir], out_dir=out_dir, plots=plots or None,
                              sections=[{"title": title, "lines": lines}], artifacts=list(artifacts))
    else:  # build_report without caller sections: insert the section before Diagnostics
        report = build_report([study_dir], out_dir=out_dir, plots=plots or None)
        text = (out_dir / "report.md").read_text(encoding="utf-8")
        section = "\n".join([f"## {title}", ""] + lines + [""]) + "\n"
        marker = "## Diagnostics"
        text = text.replace(marker, section + marker, 1) if marker in text else text + "\n" + section
        wc.write_text_atomic(out_dir / "report.md", text)
    document = wc.read_json(out_dir / "report.json")
    document.update(extra)
    wc.write_json(out_dir / "report.json", document)
    return {"markdown": (out_dir / "report.md").as_posix(), "json": (out_dir / "report.json").as_posix(),
            "artifacts": len(report["artifacts"])}


def matplotlib_missing():
    """None when matplotlib can be imported, else why no plots are written."""
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        return "matplotlib is not installed (python -m pip install -r requirements-examples.txt)"
    return None


def fmt(value, digits=9):
    if isinstance(value, float):
        return f"{value:.{digits}g}"
    return "-" if value is None else str(value)


def derivative_unit(target_unit, parameter_unit, power=1):
    numerator = target_unit if target_unit not in (None, "") else "1"
    if parameter_unit in (None, "", "1"):
        return numerator
    return f"{numerator}/{parameter_unit}" + (f"^{power}" if power != 1 else "")


# --------------------------------------------------------------------------------------------- spec

def _translation_or_rotation(ops):
    """"translation" / "rotation" when every op is a transform whose placeholder sits only in that part."""
    kinds = set()
    for op in ops:
        if op.get("op") != "transform":
            return None
        for key in ("translation_mm", "rotation"):
            if key in op and sweep.substitute(op[key], 0.0, None) != sweep.substitute(op[key], 1.0, None):
                kinds.add("translation" if key == "translation_mm" else "rotation")
    return kinds.pop() if len(kinds) == 1 else None


def _steps(parameter, raw, common):
    name, unit, nominal = common["name"], common["unit"], common["nominal"]
    source = "given"
    if raw is None:
        kind = _translation_or_rotation(common["ops"]) if common["ops"] else None
        if kind == "translation":
            raw, source = [{"value": 0.01, "unit": "mm"}, {"value": 0.1, "unit": "mm"}], "default translation"
        elif kind == "rotation":
            raw, source = [{"value": 0.01, "unit": "deg"}, {"value": 0.1, "unit": "deg"}], "default rotation"
        else:
            raw, source = [{"relative": 1e-3}, {"relative": 1e-2}], "default relative"
    if not isinstance(raw, list) or len(raw) < 2:
        raise wc.WorkflowError("SPEC_INVALID", f"parameter {name!r}: steps must list at least two step sizes")
    steps = []
    for i, item in enumerate(raw):
        if isinstance(item, dict) and set(item) == {"relative"}:
            if nominal == 0.0:
                raise wc.WorkflowError("STEP_REQUIRED", f"parameter {name!r}: a relative step needs a non-zero "
                                       "nominal; give steps with units", parameter=name)
            step = abs(float(item["relative"])) * abs(nominal)
        else:
            step = quantity(item, unit, f"parameter {name} steps[{i}]")
        if not step > 0.0 or not math.isfinite(step):
            raise wc.WorkflowError("SPEC_INVALID", f"parameter {name!r}: steps must be positive", parameter=name)
        steps.append(step)
    if len(set(steps)) != len(steps):
        raise wc.WorkflowError("SPEC_INVALID", f"parameter {name!r}: step sizes must be distinct", parameter=name)
    return sorted(steps), source


def normalize_spec(spec, *, build=None):
    """Validated parameters, steps, scales, budget; raises WorkflowError (SPEC_INVALID, UNIT_*, BUDGET_EXCEEDED,
    STEP_REQUIRED) before anything runs."""
    parameters = spec.get("parameters")
    if not isinstance(parameters, list) or not parameters:
        raise wc.WorkflowError("SPEC_INVALID", "parameters must list at least one parameter")
    out, names = [], set()
    for index, parameter in enumerate(parameters):
        common = common_parameter(parameter, index, build=build)
        if common["name"] in names:
            raise wc.WorkflowError("SPEC_INVALID", f"duplicate parameter name {common['name']!r}")
        names.add(common["name"])
        steps, source = _steps(parameter, parameter.get("steps"), common)
        scale = parameter.get("scale")
        scale = None if scale is None else abs(quantity(scale, common["unit"], f"parameter {common['name']} scale"))
        out.append({**common, "steps": steps, "steps_source": source, "scale": scale})
    threshold = spec.get("consistency_threshold", DEFAULT_THRESHOLD)
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not threshold > 0:
        raise wc.WorkflowError("SPEC_INVALID", "consistency_threshold must be a positive number")
    samples = 1 + 2 * sum(len(p["steps"]) for p in out)
    budget = spec.get("max_evaluations")
    if isinstance(budget, bool) or not isinstance(budget, int) or budget < 1:
        raise wc.WorkflowError("SPEC_INVALID", "max_evaluations must be a positive integer")
    if samples > budget:
        raise wc.WorkflowError("BUDGET_EXCEEDED", f"the study needs {samples} runs (1 nominal + 2 per step), more "
                               f"than max_evaluations {budget}; nothing was run", samples=samples,
                               max_evaluations=budget)
    return {"parameters": out, "samples": samples, "max_evaluations": budget, "targets": spec.get("targets"),
            "consistency_threshold": float(threshold)}


def target_resolution(raw, targets, units):
    """``{target_id: change in the target unit}`` from the spec's ``resolution`` (quantities converted)."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise wc.WorkflowError("SPEC_INVALID", "resolution maps target ids to a change in the target's unit")
    out = {}
    for target_id, value in raw.items():
        if target_id not in targets:
            raise wc.WorkflowError("TARGET_UNKNOWN", f"resolution names {target_id!r}, which is not a selected target",
                                   unknown=[target_id], known=targets)
        unit = units.get(target_id)
        out[target_id] = abs(quantity(value, unit if unit not in ("1", "") else None, f"resolution {target_id}"))
    return out


def plan(parameters):
    """``[(sample_id, parameter name or None, step or None, side, offset)]``: s0000 nominal, then for each parameter
    (spec order) and step (ascending) the -h and +h samples."""
    rows = [("s0000", None, None, "nominal", 0.0)]
    for parameter in parameters:
        for step in parameter["steps"]:
            for side, sign in (("minus", -1.0), ("plus", 1.0)):
                rows.append((f"s{len(rows):04d}", parameter["name"], step, side, sign * step))
    return rows


# --------------------------------------------------------------------------------------------- numerics

def finite_difference(f0, f_plus, f_minus, h_plus, h_minus):
    """Derivative estimate from the usable values (None = unusable); ``h_plus`` / ``h_minus`` are the positive
    distances of the +h / -h samples from nominal. Returns method, derivative, second difference and deltas."""
    out = {"method": None, "derivative": None, "second_difference": None,
           "delta_plus": None if f_plus is None or f0 is None else f_plus - f0,
           "delta_minus": None if f_minus is None or f0 is None else f_minus - f0}
    if f_plus is not None and f_minus is not None:
        out["method"] = "central"
        out["derivative"] = (f_plus - f_minus) / (h_plus + h_minus)
        if f0 is not None:
            h = 0.5 * (h_plus + h_minus)
            out["second_difference"] = (f_plus - 2.0 * f0 + f_minus) / (h * h)
    elif f0 is not None and f_plus is not None:
        out["method"], out["derivative"] = "forward", (f_plus - f0) / h_plus
    elif f0 is not None and f_minus is not None:
        out["method"], out["derivative"] = "backward", (f0 - f_minus) / h_minus
    return out


def assess(estimates, threshold, scale, resolution=None):
    """Primary estimate, step consistency, flags and normalized values of one (parameter, target) pair.

    ``estimates``: per step (ascending) dicts with ``step``, ``method``, ``derivative``, ``second_difference``,
    ``delta_plus``, ``delta_minus`` (and ``f_nominal`` / ``f_plus`` / ``f_minus``). ``resolution``: the target
    change (target unit) at or below which the target counts as unchanged; the round-off floor applies as well."""
    valued = [e for e in estimates if e["derivative"] is not None]
    result = {"primary_step": None, "method": None, "derivative": None, "second_difference": None,
              "consistency": None, "flags": [], "scaled": None, "second_order_at_scale": None,
              "taylor_change_at_scale": None}
    if not valued:
        result["flags"] = ["UNAVAILABLE"]
        return result
    primary = next((e for e in valued if e["method"] == "central"), valued[0])
    result.update({"primary_step": primary["step"], "method": primary["method"], "derivative": primary["derivative"]})
    others = [e for e in valued if e is not primary]
    compared = sorted([primary] + others[:1], key=lambda e: e["step"])
    curved = ([e for e in compared if e["second_difference"] is not None]
              or [e for e in valued if e["second_difference"] is not None])
    second = curved[-1]["second_difference"] if curved else None  # larger step: less round-off amplification
    result["second_difference"] = second
    result["second_difference_step"] = curved[-1]["step"] if curved else None
    flags = []
    if primary["method"] != "central":
        flags.append("ONE_SIDED")
    magnitudes = [abs(e.get(k)) for e in valued for k in ("f_nominal", "f_plus", "f_minus")
                  if isinstance(e.get(k), (int, float))]
    floor = max(ROUNDOFF_RELATIVE * max(magnitudes, default=0.0), resolution or 0.0)
    result["change_floor"] = floor
    if others:
        other = others[0]
        d1, d2 = primary["derivative"], other["derivative"]
        largest = max(abs(d1), abs(d2))
        rel = 0.0 if largest == 0.0 else abs(d1 - d2) / largest
        negligible = abs(d1) * primary["step"] <= floor and abs(d2) * other["step"] <= floor
        result["consistency"] = {"steps": [primary["step"], other["step"]],
                                 "methods": [primary["method"], other["method"]],
                                 "ratio": None if d2 == 0.0 else d1 / d2, "relative_difference": rel,
                                 "threshold": threshold, "below_change_floor": negligible,
                                 "consistent": rel <= threshold or negligible}
        if not result["consistency"]["consistent"]:
            flags.append("STEP_DEPENDENT")
        if primary["method"] != other["method"]:
            flags.append("METHOD_DIFFERS")
    else:
        flags.append("STEP_CHECK_UNAVAILABLE")
    deltas = [d for e in valued for d in (e["delta_plus"], e["delta_minus"]) if d is not None]
    if deltas and all(abs(d) <= floor for d in deltas):
        flags.append("NO_CHANGE")
    elif second is not None:
        h_ref = result["second_difference_step"]
        curvature = 0.5 * abs(second) * h_ref * h_ref
        if curvature > floor and abs(primary["derivative"]) * h_ref <= curvature:
            flags.append("NEAR_EXTREMUM")
    result["flags"] = flags
    if scale is not None:
        result["scaled"] = primary["derivative"] * scale
        result["second_order_at_scale"] = None if second is None else 0.5 * second * scale * scale
        result["taylor_change_at_scale"] = abs(primary["derivative"]) * scale + (
            0.0 if second is None else 0.5 * abs(second) * scale * scale)
    return result


def rank(entries):
    """Ranking of one target's parameter entries (see module docstring); returns (basis, ranked, not_ranked)."""
    valued = [e for e in entries if e["derivative"] is not None]
    if valued and all(e["scale"] is not None for e in valued):
        basis, key = "taylor_change_at_scale", (lambda e: e["taylor_change_at_scale"])
        pool, rest = valued, []
    elif valued and len({e["parameter_unit"] for e in valued}) == 1:
        basis, key = "abs_derivative_per_unit", (lambda e: abs(e["derivative"]))
        pool, rest = valued, []
    else:
        basis, key = "taylor_change_at_scale (parameters with a scale)", (lambda e: e["taylor_change_at_scale"])
        pool = [e for e in valued if e["scale"] is not None]
        rest = [e for e in valued if e["scale"] is None]
    ordered = sorted(pool, key=lambda e: (-key(e), e["parameter"]))
    previous, position = None, 0
    for index, entry in enumerate(ordered, start=1):
        if key(entry) != previous:
            position, previous = index, key(entry)
        entry["rank"] = position  # equal keys share a rank
    not_ranked = [{"parameter": e["parameter"], "reason": "no scale and the parameter units differ"} for e in rest]
    not_ranked += [{"parameter": e["parameter"], "reason": "derivative unavailable"} for e in entries
                   if e["derivative"] is None]
    return basis, [e["parameter"] for e in ordered], not_ranked


# --------------------------------------------------------------------------------------------- study

def run_sensitivity(spec, study_dir, *, base=None, build=None, base_path=None, spec_dir=None, overwrite=False,
                    plots=True, report=True):
    """Run the study described by ``spec`` (module docstring) and write the study directory; returns the summary.

    ``base``: the base Project document; ``build(values) -> ProjectDocumentV2``: Python alternative to op templates
    (``values``: parameter name -> nominal + perturbation, in the parameter's unit; nominal = build of the
    nominals). Without either, the spec's ``design`` or ``base_project`` is used."""
    t_start = time.perf_counter()
    normalized = normalize_spec(spec, build=build)
    parameters = normalized["parameters"]
    document, base_info = resolve_base(spec, spec_dir=spec_dir, base=base, base_path=base_path, build=build)
    if build is not None:
        document = build({p["name"]: p["nominal"] for p in parameters})
    checked = check_base(document, parameters, build=build)
    targets = selected_targets(document, normalized["targets"])
    units = target_units(document)
    resolution = target_resolution(spec.get("resolution"), targets, units)
    samples = plan(parameters)
    if build is None:  # unit and op errors in any sample are spec errors: refuse before running anything
        by_name = {p["name"]: p for p in parameters}
        for _sid, name, _step, _side, offset in samples[1:]:
            normalize_ops(document, parameter_ops(by_name[name], offset))
    started = wc.utc_now()
    study_dir = prepare_dir(study_dir, overwrite)
    study = {
        "kind": KIND, "spec": spec,
        "normalized": {**normalized, "targets": targets,
                       "parameters": [{**p, "values": sorted({s * k for s in p["steps"] for k in (-1.0, 1.0)})}
                                      for p in parameters]},
        "base_project": {**base_info, "project_hash": checked["project_hash"],
                         "engine_input_hash": checked["engine_input_hash"],
                         "document_sha256": wc.canonical_digest(document)},
        "engine": wc.engine_identity(), "installed_catalog": wc.catalog_identity(),
        "catalog": wc.run_catalog_identity([]), "environment": wc.environment(),
        "seed": None, "sampling": "deterministic finite-difference plan",
        "budget": {"max_evaluations": normalized["max_evaluations"], "samples": normalized["samples"]},
        "stop_on": [], "started": started,
    }
    wc.write_json(study_dir / "study.json", study)
    records, rows = {}, []
    by_name = {p["name"]: p for p in parameters}
    for sample_id, name, step, side, offset in samples:
        sample_dir = study_dir / "samples" / sample_id
        if name is None:
            record = run_one(document, parameters, {}, sample_dir, build=None, targets=targets)
        else:
            record = run_one(document, parameters, {name: offset}, sample_dir, build=build, targets=targets)
        record.update({"sample_id": sample_id, "role": side, "perturbed_parameter": name, "step": step})
        wc.write_json(sample_dir / "sample.json", record)
        records[sample_id] = record
        row = sweep.row_from_record(sample_id, {p["name"]: offset if p["name"] == name else 0.0 for p in parameters},
                                    parameters, record, study_dir)
        row.update({"role": side, "perturbed_parameter": name, "step": step,
                    "step_unit": None if name is None else by_name[name]["unit"]})
        rows.append(row)
    sweep.select_best(rows, [], None)  # no requirement constraints; per-target validity is assessed separately
    nominal = records["s0000"]
    pairs, estimate_rows = [], []
    for parameter in parameters:
        ids = {(s[2], s[3]): s[0] for s in samples if s[1] == parameter["name"]}
        for target_id in targets:
            f0, nominal_reason = target_value(nominal, target_id)
            estimates = []
            for step in parameter["steps"]:
                plus, minus = records[ids[(step, "plus")]], records[ids[(step, "minus")]]
                f_plus, reason_plus = target_value(plus, target_id)
                f_minus, reason_minus = target_value(minus, target_id)
                applied_plus = plus["parameters"][parameter["name"]]["value"]
                applied_minus = minus["parameters"][parameter["name"]]["value"]
                if nominal_reason is not None:
                    fd = finite_difference(None, None, None, step, step)
                else:
                    fd = finite_difference(f0, f_plus, f_minus, applied_plus - parameter["nominal"],
                                           parameter["nominal"] - applied_minus)
                reasons = [r for r in (f"nominal: {nominal_reason}" if nominal_reason else None,
                                       f"-h: {reason_minus}" if reason_minus else None,
                                       f"+h: {reason_plus}" if reason_plus else None) if r]
                estimate = {"step": step, **fd, "f_nominal": f0, "f_plus": f_plus, "f_minus": f_minus,
                            "plus_sample": plus["sample_id"], "minus_sample": minus["sample_id"],
                            "plus_outcome": plus["outcome"], "minus_outcome": minus["outcome"],
                            "unusable": reasons}
                estimates.append(estimate)
            assessed = assess(estimates, normalized["consistency_threshold"], parameter["scale"],
                              resolution.get(target_id))
            pair = {"parameter": parameter["name"], "parameter_unit": parameter["unit"],
                    "nominal": parameter["nominal"], "target": target_id, "target_unit": units.get(target_id),
                    "nominal_value": f0, "scale": parameter["scale"], **assessed,
                    "derivative_unit": derivative_unit(units.get(target_id), parameter["unit"]),
                    "second_difference_unit": derivative_unit(units.get(target_id), parameter["unit"], 2),
                    "estimates": estimates}
            pairs.append(pair)
            for estimate in estimates:
                estimate_rows.append(_estimate_row(pair, estimate))
    ranking = {}
    for target_id in targets:
        entries = [p for p in pairs if p["target"] == target_id]
        basis, ordered, not_ranked = rank(entries)
        ranking[target_id] = {"basis": basis, "order": ordered, "not_ranked": not_ranked,
                              "unit": units.get(target_id)}
    for row in estimate_rows:
        pair = next(p for p in pairs if p["parameter"] == row["parameter"] and p["target"] == row["target"])
        row["rank"] = pair.get("rank")
    columns = sweep._columns(rows)
    wc.write_csv(study_dir / "table.csv", rows, columns)
    wc.write_csv(study_dir / "sensitivity.csv", estimate_rows, list(estimate_rows[0]) if estimate_rows else [])
    unavailable = [{"parameter": p["parameter"], "target": p["target"]} for p in pairs if p["derivative"] is None]
    status = "INCOMPLETE" if unavailable else "COMPLETED"
    study["catalog"] = wc.run_catalog_identity(rows)
    study["finished"] = wc.utc_now()
    wc.write_json(study_dir / "study.json", study)
    summary = {
        "status": status, "kind": f"{KIND}-summary",
        "method": {"default": "central finite differences", "formulas": METHOD_TEXT,
                   "second_difference": "(f(+h) - 2 f(0) + f(-h)) / h^2 at the larger of the two compared steps",
                   "consistency": f"ratio D(primary) / D(next usable step); relative difference "
                                  f"|D1 - D2| / max(|D1|, |D2|) flagged above {normalized['consistency_threshold']}",
                   "change_floor": "max(resolution of the target, 64 machine epsilons x largest |target value|)",
                   "near_extremum": "|f''| h^2 / 2 above the change floor and |D| h <= |f''| h^2 / 2 at the "
                                    "second-difference step",
                   "resolution": resolution,
                   "normalization": {
                       "per_unit": "derivative in target unit per parameter unit (primary estimate)",
                       "scaled": "derivative * scale: first-order target change for one declared scale of the "
                                 "parameter, in the target unit",
                       "second_order_at_scale": "f'' * scale^2 / 2, in the target unit",
                       "taylor_change_at_scale": "|derivative| * scale + |f''| * scale^2 / 2, in the target unit"},
                   "usable_side": "a finite exact target value with applicable dependencies"},
        "nominal": {"sample_id": "s0000", "outcome": nominal["outcome"], "status": nominal["status"],
                    "model_scope_issues": nominal["model_scope_issues"],
                    "parameters": {p["name"]: {"value": p["nominal"], "unit": p["unit"]} for p in parameters},
                    "targets": [{k: t.get(k) for k in ("id", "value", "unit", "status", "assessment", "quantity_state", "power_upper_bound_W")} for t in nominal["targets"]
                                if t["id"] in targets],
                    "run_dir": rows[0]["run_dir"]},
        "parameters": [{"name": p["name"], "unit": p["unit"], "nominal": p["nominal"], "steps": p["steps"],
                        "steps_source": p["steps_source"], "scale": p["scale"]} for p in parameters],
        "consistency_threshold": normalized["consistency_threshold"],
        "selected_targets": targets, "sensitivities": pairs, "ranking": ranking, "unavailable": unavailable,
        "counts": sweep.counts(rows), "feasible": sum(1 for r in rows if r["feasible"]),
        "constraints": [], "objective": None, "best": None, "no_feasible": None, "stopped_by": None,
        "targets": sweep._target_units(rows), "columns": columns, "rows": rows,
        "identity": {"engine": study["engine"], **study["catalog"],
                     "base_project_hash": checked["project_hash"]},
        "paths": {"study_dir": study_dir.as_posix(), "study": "study.json", "table": "table.csv",
                  "sensitivity_table": "sensitivity.csv", "summary": "summary.json", "samples": "samples"},
        "seconds": None,
    }
    wc.write_json(study_dir / "summary.json", summary)
    skipped = matplotlib_missing() if plots else "plots disabled"
    if skipped is None:
        summary["plots"] = plot_sensitivity(study_dir, summary)
        summary["paths"]["plots"] = "plots"
    else:
        summary["plots"] = {"written": [], "skipped": skipped}
    if report:
        summary["report"] = append_report(study_dir, plots=[study_dir / "plots"] if skipped is None else None,
                                          title="Sensitivity", lines=report_lines(summary),
                                          extra={"sensitivity": _report_extra(summary)},
                                          artifacts=[(study_dir / "sensitivity.csv", "sensitivity table")])
        summary["paths"]["report"] = "report/report.md"
    summary["seconds"] = round(time.perf_counter() - t_start, 3)
    wc.write_json(study_dir / "summary.json", summary)
    return summary


def _estimate_row(pair, estimate):
    consistency = pair["consistency"] or {}
    return {"parameter": pair["parameter"], "parameter_unit": pair["parameter_unit"], "nominal": pair["nominal"],
            "target": pair["target"], "target_unit": pair["target_unit"], "step": estimate["step"],
            "method": estimate["method"], "f_minus": estimate["f_minus"], "f_nominal": estimate["f_nominal"],
            "f_plus": estimate["f_plus"], "delta_minus": estimate["delta_minus"], "delta_plus": estimate["delta_plus"],
            "derivative": estimate["derivative"], "derivative_unit": pair["derivative_unit"],
            "second_difference": estimate["second_difference"],
            "second_difference_unit": pair["second_difference_unit"],
            "minus_sample": estimate["minus_sample"], "minus_outcome": estimate["minus_outcome"],
            "plus_sample": estimate["plus_sample"], "plus_outcome": estimate["plus_outcome"],
            "unusable": "; ".join(estimate["unusable"]), "primary": estimate["step"] == pair["primary_step"],
            "consistency_ratio": consistency.get("ratio"),
            "consistency_relative_difference": consistency.get("relative_difference"),
            "flags": pair["flags"], "scale": pair["scale"], "scaled": pair["scaled"],
            "taylor_change_at_scale": pair["taylor_change_at_scale"], "rank": None}


# --------------------------------------------------------------------------------------------- outputs

def plot_sensitivity(study_dir, summary):
    """Per target: bars of the normalized sensitivity (plot_results.plot_bars) and a tornado of the measured target
    change at -h / +h of each parameter's largest usable step. PNG + SVG in STUDY_DIR/plots."""
    if __package__:
        from . import plot_results as pr
    else:
        import plot_results as pr

    study_dir = Path(study_dir)
    out_dir = study_dir / "plots"
    source = study_dir / "summary.json"
    engine = summary["identity"]["engine"]["engine_build_id"]
    catalog = summary["identity"]["catalog_version"]
    written, skipped = [], []
    for target_id in summary["selected_targets"]:
        entries = [p for p in summary["sensitivities"] if p["target"] == target_id]
        unit = entries[0]["target_unit"] if entries else None
        order = summary["ranking"][target_id]["order"]
        ordered = sorted(entries, key=lambda e: (order.index(e["parameter"]) if e["parameter"] in order
                                                 else len(order), e["parameter"]))
        if all(e["scale"] is not None for e in entries):
            labels = [f"{e['parameter']}\nscale {e['scale']:.6g} {e['parameter_unit'] or ''}".strip()
                      for e in ordered]
            values = [e["scaled"] for e in ordered]
            xlabel = f"d({target_id})/dp x scale [{unit}]"
        elif len({e["parameter_unit"] for e in entries}) == 1:
            labels = [e["parameter"] for e in ordered]
            values = [e["derivative"] for e in ordered]
            xlabel = f"d({target_id})/dp [{entries[0]['derivative_unit']}]"
        else:
            labels = values = None
            skipped.append({"target": target_id, "plot": "bars", "reason": "parameters without a scale have "
                            "different units"})
        if labels is not None:
            labels = [f"{label}\n{', '.join(e['flags'])}" if e["flags"] else label for label, e in zip(labels, ordered)]
            result = pr.plot_bars(labels, values, unit, out_dir, f"sensitivity_{target_id}",
                                  title=f"Sensitivity of {target_id} (primary estimate)", ylabel=xlabel,
                                  source=source, engine_build=engine, catalog_version=catalog)
            written.append({"target": target_id, "plot": "bars", **result["out"]})
        written.append({"target": target_id, "plot": "tornado",
                        **_tornado(pr, ordered, target_id, unit, out_dir, source, engine, catalog)})
    return {"written": written, "skipped": skipped}


def _tornado(pr, entries, target_id, unit, out_dir, source, engine, catalog):
    plt = pr._pyplot()
    fig, ax = plt.subplots(figsize=(6.6, max(2.6, 0.55 * len(entries) + 1.6)))
    labels, legend_done = [], set()
    for i, entry in enumerate(entries):
        usable = [e for e in entry["estimates"] if e["delta_plus"] is not None or e["delta_minus"] is not None]
        chosen = usable[-1] if usable else entry["estimates"][-1]
        labels.append(f"{entry['parameter']}\nstep {chosen['step']:.6g} {entry['parameter_unit'] or ''}".strip())
        for side, value, outcome, color, offset in (
                ("-h", chosen["delta_minus"], chosen["minus_outcome"], "#e8743b", 0.17),
                ("+h", chosen["delta_plus"], chosen["plus_outcome"], "#2a78d6", -0.17)):
            if value is not None:
                label = f"change at {side}" if side not in legend_done else None
                legend_done.add(side)
                ax.barh(i + offset, value, height=0.32, color=color, edgecolor="white", label=label)
                ax.text(value, i + offset, f" {value:.4g}", va="center", ha="left" if value >= 0 else "right",
                        fontsize=6.5, color="#333333")
            else:
                marker, mcolor, mlabel = pr.STATUS_STYLE.get(outcome if outcome != "PASS" else "UNKNOWN",
                                                             pr.STATUS_STYLE["UNKNOWN"])
                key = f"unusable {outcome}"
                ax.scatter([0.0], [i + offset], marker=marker, color=mcolor, s=40, zorder=3,
                           label=f"{side} not usable: {mlabel}" if key not in legend_done else None)
                legend_done.add(key)
                ax.text(0.0, i + offset, f"  {side}: {outcome}", va="center", ha="left", fontsize=6.5,
                        color=mcolor)
    ax.axvline(0.0, color="#555555", linewidth=0.8)
    ax.set_yticks(range(len(entries)), labels)
    ax.invert_yaxis()
    ax.set_xlabel(f"change of {target_id} from nominal [{unit}]")
    ax.set_title(f"{target_id}: target change at the largest usable step")
    ax.legend(fontsize=6.5, frameon=False, loc="best")
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    text = pr.caption(source, out_dir, engine_build=engine, catalog_version=catalog)
    return pr.save(fig, out_dir, f"tornado_{target_id}", text)


def report_lines(summary):
    lines = ["Method: central finite differences (f(+h) - f(-h)) / (2h); forward or backward differences where "
             "one side is not usable (labelled); second difference (f(+h) - 2 f(0) + f(-h)) / h^2. Step "
             f"consistency is flagged above a relative difference of {fmt(summary['consistency_threshold'])}.",
             "", "Nominal (s0000): outcome " + summary["nominal"]["outcome"] + "; " + "; ".join(
                 f"{t['id']} = {fmt(t['value'])} {t['unit']} {t['status']}" for t in summary["nominal"]["targets"]),
             ""]
    lines += sweep_table(["parameter", "unit", "nominal", "steps", "scale"], [
        [p["name"], p["unit"] or "-", fmt(p["nominal"]), ", ".join(fmt(s) for s in p["steps"]), fmt(p["scale"])]
        for p in summary["parameters"]]) + [""]
    for target_id in summary["selected_targets"]:
        ranking = summary["ranking"][target_id]
        entries = sorted((p for p in summary["sensitivities"] if p["target"] == target_id),
                         key=lambda e: (e.get("rank") or 10 ** 6, e["parameter"]))
        lines += [f"### {target_id} [{ranking['unit']}]", "", f"Ranking basis: {ranking['basis']}.", ""]
        lines += sweep_table(["rank", "parameter", "derivative", "unit", "method (step)", "step ratio",
                              "second difference", "scaled", "Taylor at scale", "flags"], [
            [e.get("rank") or "-", e["parameter"], fmt(e["derivative"]), e["derivative_unit"],
             f"{e['method'] or '-'} ({fmt(e['primary_step'])})", fmt((e["consistency"] or {}).get("ratio"), 6),
             f"{fmt(e['second_difference'])} {e['second_difference_unit']}", fmt(e["scaled"]),
             fmt(e["taylor_change_at_scale"]), ", ".join(e["flags"]) or "-"] for e in entries]) + [""]
        lines += sweep_table(["parameter", "step", "method", "f(-h)", "f(+h)", "derivative", "second difference",
                              "not usable"], [
            [e["parameter"], fmt(x["step"]), x["method"] or "-", fmt(x["f_minus"]), fmt(x["f_plus"]),
             fmt(x["derivative"]), fmt(x["second_difference"]), "; ".join(x["unusable"]) or "-"]
            for e in entries for x in e["estimates"]]) + [""]
    lines.append("Full numbers: [sensitivity.csv](../sensitivity.csv).")
    return lines


def sweep_table(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "|".join(" --- " for _ in header) + "|"]
    for row in rows:
        out.append("| " + " | ".join(fmt(v).replace("|", "/") for v in row) + " |")
    return out


def _report_extra(summary):
    return {key: summary[key] for key in ("method", "nominal", "parameters", "ranking", "unavailable")} | {
        "pairs": [{k: p[k] for k in ("parameter", "target", "derivative", "derivative_unit", "method",
                                     "primary_step", "second_difference", "consistency", "flags", "scaled",
                                     "taylor_change_at_scale")} for p in summary["sensitivities"]]}


def compact(summary, limit=TOP_UNUSABLE):
    """The bounded stdout document of a sensitivity summary."""
    by_target = {}
    for target_id in summary["selected_targets"]:
        entries = [p for p in summary["sensitivities"] if p["target"] == target_id]
        ranking = summary["ranking"][target_id]
        order = ranking["order"] + [e["parameter"] for e in entries if e["parameter"] not in ranking["order"]]
        rows = []
        for name in order:
            e = next(p for p in entries if p["parameter"] == name)
            item = {"parameter": name, "rank": e.get("rank"), "derivative": e["derivative"],
                    "unit": e["derivative_unit"], "method": e["method"], "step": e["primary_step"],
                    "step_unit": e["parameter_unit"],
                    "step_ratio": (e["consistency"] or {}).get("ratio"),
                    "second_difference": e["second_difference"], "flags": e["flags"]}
            if e["scale"] is not None:
                item.update({"scale": e["scale"], "scaled": e["scaled"],
                             "taylor_change_at_scale": e["taylor_change_at_scale"]})
            rows.append(item)
        by_target[target_id] = {"unit": ranking["unit"], "ranking_basis": ranking["basis"], "parameters": rows}
    unusable = [{"sample_id": r["sample_id"], "outcome": r["outcome"], "parameter": r["perturbed_parameter"],
                 "offset": r.get(f"param:{r['perturbed_parameter']}") if r["perturbed_parameter"] else 0.0,
                 "error_code": r.get("error_code"), "model_scope_issues": r.get("model_scope_issues")}
                for r in summary["rows"] if r["outcome"] in ("ERROR", "OUT_OF_MODEL", "UNKNOWN")]
    shown = wc.bounded(unusable, limit)
    return {"status": summary["status"], "method": "central finite differences (one-sided where labelled)",
            "nominal": {k: summary["nominal"][k] for k in ("outcome", "status", "targets", "parameters")},
            "sensitivities": by_target, "unavailable": summary["unavailable"],
            "counts": {"samples": summary["counts"]["requested"], "by_outcome": summary["counts"]["by_outcome"]},
            "unusable_samples": shown["shown"], "unusable_omitted": shown["omitted"],
            "identity": {"engine_version": summary["identity"]["engine"]["engine_version"],
                         "engine_build_id": summary["identity"]["engine"]["engine_build_id"],
                         "catalog_version": summary["identity"]["catalog_version"]},
            "seconds": summary["seconds"], "paths": summary["paths"]}


def main(argv=None):
    def workflow():
        parser = wc.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
        parser.add_argument("spec", type=Path, help="Sensitivity spec JSON")
        parser.add_argument("--out", type=Path, required=True, help="Study directory to create")
        parser.add_argument("--base", type=Path, help="Base Project JSON (overrides base_project / design)")
        parser.add_argument("--overwrite", action="store_true", help="Replace this script's files in --out")
        parser.add_argument("--no-plots", action="store_true", help="Do not write plots")
        parser.add_argument("--no-report", action="store_true", help="Do not write report/")
        args = parser.parse_args(argv)
        spec = wc.read_json(args.spec)
        base = wc.read_json(args.base) if args.base else None
        summary = run_sensitivity(spec, args.out, base=base, base_path=args.base, spec_dir=args.spec.parent,
                                  overwrite=args.overwrite, plots=not args.no_plots, report=not args.no_report)
        return compact(summary)

    return wc.emit(workflow, EXIT_CODES)


if __name__ == "__main__":
    raise SystemExit(main())
