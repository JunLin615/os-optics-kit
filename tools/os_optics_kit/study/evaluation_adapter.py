"""Evaluation adapter: parameters -> requested analyses -> numerical results, on the Engine's ``EvaluationSession``.

Guide: ``docs/en/evaluation-adapter.md``. A :class:`Problem` declares

* ``variables``: named parameters with ``bounds`` and a ``scale``, each applied to the base Project by op templates
  (``"$value"`` = the variable's value, in its ``unit``; the edit ops of ``os-optics-kit edit``);
* ``objectives``: quantities to drive toward a ``goal``, each scaled, weighted and combined by the problem's
  ``aggregation`` (``sum_squares``, ``sum_abs`` or ``max_abs`` of the scaled errors);
* ``constraints``: quantities with a ``minimum`` and/or ``maximum``; each reports its ``margin`` (scaled distance to
  the nearest bound, positive when satisfied). MTF is read as a constraint only (``{"mtf": ...}`` sources are refused
  in an objective): an MTF floor at a stated frequency, not a value to optimize.

Objective and constraint names are unique together (they name the residuals). Goals, bounds, scales and weights are
finite; ``minimum <= maximum``; scales are positive, weights non-negative.

A quantity ``source`` is ``{"target": TARGET_ID}`` (a declared target's value, evaluated through the Engine's
``requirements``), ``{"paraxial": {...}}``, ``{"solids": {...}}`` (both below) or ``{"mtf": {"observation": ID,
"frequency_cyc_per_mm": F, "direction": "tangential" |
"sagittal", "source_id": S, "wavelength_nm": W, "quantity_id": Q}}``: the MTF curves of the imaging analysis of plane
``ID``, read at ``F``. The analysis states one curve per source and wavelength (for example ``SRC#0`` .. ``SRC#2``
for one source at three wavelengths, ``SRC0_0#0`` .. ``SRC2_2#0`` for three field points at three wavelengths), and
each curve's scope names its ``source_id`` and ``wavelength_nm``. ``source_id``, ``wavelength_nm`` (matched within
``WAVELENGTH_MATCH_NM``) and ``quantity_id`` narrow the selection; without them it is every curve of that direction.
The constraint holds when it holds on every selected curve: its ``margin`` is the smallest member margin, its
``value``, ``quantity_id``, ``source_id``, ``field_id`` and ``wavelength_nm`` are those of that binding curve, and
``members`` lists every selected curve with its own value and margin. A selection that matches no curve is
``MTF_SOURCE_NOT_FOUND``, with the curves the analysis states. When every MTF constraint of a plane names its
``source_id`` (at most ``MAX_IMAGING_FIELDS`` sources), the imaging request asks for those fields only, and when every
one names ``wavelength_nm``, for the Engine's spectral lines within ``WAVELENGTH_MATCH_NM`` of them only (a request
that already states ``fields`` / ``wavelengths_nm`` is kept): each line is analysed on its own, so the values are the
same and fewer lines are traced. Each MTF reading records the request it was read from (``imaging_request``).

``{"paraxial": {"components": [ID, ...], "wavelength_nm": W, "quantity": Q, "axis"?: "X" | "Y", "source_id"?: S}}``:
the Engine's paraxial analysis (``os_optics.local.paraxial_analysis``) of the candidate's components at ``W``,
``system.<Q>`` with ``Q`` in ``PARAXIAL_QUANTITIES`` (``efl_mm``, ``bfl_mm``, ...). Conic and polynomial terms do not
change paraxial power. A value the analysis states as null (``efl_mm`` of an afocal system) is ``NO_VALUE``; an
analysis it refuses makes the candidate invalid (``ANALYSIS_REFUSED``, stage ``paraxial``). One analysis serves every
quantity of one request per candidate.

``{"solids": {"quantity": Q, "component"?: ID, "member"?: ID}}``: the optical-solid check of the candidate
(``solid_check``, ``Evaluation.optical_solids``): the smallest ``Q`` (``SOLIDS_QUANTITIES``: ``min_thickness_mm``,
``edge_thickness_mm``, ``centre_thickness_mm``, ``clear_aperture_min_thickness_mm``) over every glass gap of the
selected component (and COMPOUND member; every element when none is named). The reading names the binding gap
(``binding``: instance, member, surfaces, medium) and the report's ``status``; no matching gap is ``NO_VALUE``.

Lost rays. A target whose metric is computed from the pupil rays that reach the plane (``PUPIL_METRICS``:
``spot_rms_mm``, ``spot_centroid_*``) carries ``pupil_rays_launched`` and ``pupil_rays_arrived`` of the arrivals it
selects (its plane, ``source_id``, ``wavelength_nm``, ``path_id``, ``crossing_index``), and the source
``{"rays": {"target": ID}}`` reads their ratio ``arrived / launched`` (unit 1) for such a target, so a constraint can
bound the vignetting (for example keep at least the start's share). The Engine computes such a
value over the arriving rays only: a ray clipped on the way is not part of the RMS, so vignetting can make a spot
smaller. ``Problem.ray_loss`` decides what a loss means: ``"infeasible"`` (default) makes a candidate whose objectives
or constraints read a target that lost pupil rays ``feasible: false`` (``infeasible`` lists ``RAYS_LOST``); ``"report"``
only records it. Either way the residuals are unchanged and ``ray_loss`` lists every such reading.

The adapter asks the session only for what the sources need (selective evaluation) and carries the Engine's scope in
every result: ``requested``, ``executed``, ``not_computed[]`` and ``targets_not_evaluated[]`` of
``ValidationResult.evaluation``.

Every reading states what its number is. A target reading carries the Engine's ``status`` and ``assessment`` and a
``value_kind`` (``workflow_common.target_value_use``): ``OUTSIDE_MODEL`` for status ``OUT_OF_MODEL_SCOPE``, a
model-scope finding that names the target, a failed validity check or an estimate outside a validity limit;
``BOUNDED`` for a lower or upper bound; ``QUANTITY_UNKNOWN`` for another estimate; ``UNDECIDED`` when the status is
not PASS / WARNING / FAIL; ``NO_VALUE`` without a finite number; else ``EXACT``. An MTF curve carries its
``QuantityState`` as ``state`` and a ``value_kind`` by the same rules (``OUT_OF_MODEL_SCOPE`` -> ``OUTSIDE_MODEL``,
``BOUNDED`` -> ``BOUNDED``, ``UNKNOWN`` or an unconverged curve -> ``QUANTITY_UNKNOWN``, ``KNOWN`` / ``KNOWN_ZERO``
with no qualifying ``value_basis`` -> ``EXACT``); a curve's ``value_basis`` or ``basis_note`` says where its number
was read. Under ``Problem.value_use = "exact"`` (the default) only ``EXACT`` values are used and any other reading is
``QUANTITY_UNAVAILABLE``. Under ``"reported"`` every finite value is used for the residuals and its kind recorded; a
constraint read from a value that is not ``EXACT`` has ``satisfied`` null unless an exact member violates it, and
``feasible`` is false when a constraint is violated, else null when any constraint is not established.

MTF is refused as an objective in both forms: an ``{"mtf": ...}`` source, and a ``{"target": ID}`` whose declared
metric is of the MTF family: detected by the metric name containing ``mtf`` (case-insensitive), nothing else.

:meth:`Adapter.evaluate` never raises for a candidate and never drops one. A candidate is a mapping with exactly the
declared variable names (others are ``UNKNOWN_VARIABLE``, absent ones ``MISSING_VARIABLE``) or a sequence of values in
declaration order of the same length; values are real numbers (``numbers.Real``: ``int``, ``float``, numpy scalars).
It returns a :class:`CandidateResult` whose ``residuals`` always has ``len(objectives) + len(constraints)`` entries:
``sqrt(weight) * (value - goal) / scale`` per objective, then the violation ``max(0, -margin)`` per constraint.
Relation to the objective, exactly: ``objective`` is the aggregation of the objective errors only (constraint
violations do not count in it); ``residual_objective = sum(r ** 2 for r in residuals)``, which counts the constraint
violations as quadratic penalties. For ``sum_squares`` ``residual_objective == objective + sum(violation ** 2)``
(equal to ``objective`` when every constraint holds), and ``dls_compatible`` is true. For ``sum_abs`` and ``max_abs``
the residuals are the same vector but ``sum(r ** 2)`` is not their objective: ``dls_compatible`` is false and a
least-squares solver must not use them as that objective's residuals.

An invalid candidate carries ``reason`` = ``{"code", "stage", "message", ...}``, no objective or constraint entries,
and residuals filled with ``invalid_residual`` (finite, so a least-squares solver can still step away from it); for
``OUT_OF_BOUNDS`` each entry is ``invalid_residual * (1 + d)`` with ``d`` the bound violation in variable scales
(reason ``violation``; null when it overflows), so a farther candidate is worse; the growth stops at
``sqrt(sys.float_info.max / (2 * len(residuals)))`` (the sum of the squared residuals stays within half the float range) or at
``invalid_residual`` when that is larger. Choose ``invalid_residual`` above the largest residual a valid candidate can
reach (objective scales near the expected errors keep valid residuals near 1). Its ``evaluation`` is the empty scope
when nothing ran. ``DEFAULT_IMAGING`` (``launch_grid`` 24) is the sampling of MTF readings unless ``Problem.imaging``
sets it: the grid changes MTF values, and so the margins; an undersampled curve reads ``OUT_OF_MODEL_SCOPE``. Codes:
``OUT_OF_BOUNDS``, ``UNKNOWN_VARIABLE``, ``MISSING_VARIABLE``, ``EDIT_REFUSED``, ``PROJECT_REFUSED``,
``PLAN_REFUSED``, ``RUN_STOPPED``, ``ANALYSIS_REFUSED``, ``MTF_SOURCE_NOT_FOUND``, ``QUANTITY_UNAVAILABLE`` (a
quantity was not computed or its value is not used under ``value_use``), ``RESIDUAL_NOT_FINITE`` (a residual or
margin overflowed), ``AGGREGATE_NOT_FINITE`` (every residual is finite, but ``objective`` or ``residual_objective``
exceeds the float range: the reason names them in ``overflowed`` and keeps the finite ``residuals`` and
``largest_residual``), ``ENGINE_ERROR``. ``residuals``, ``objective`` and ``residual_objective`` are finite numbers
or null in every result, and so are the residuals these two reasons carry (``RESIDUAL_NOT_FINITE`` names the
non-finite ones in ``not_finite`` and keeps them as null), so such a result serializes as strict JSON
(``json.dumps(result.as_dict(), allow_nan=False)``).

A session evaluates one candidate at a time: an :class:`Adapter` and its session are not for use from several threads
at once. Evaluate in parallel with one Adapter per process.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
import json
import math
import numbers
import sys
from typing import Any, Callable, Mapping, Sequence

from ..imaging_results import basis_facts as _basis_facts, quantity_kind as _quantity_kind
from ..workflow_common import VALUE_USES, target_value_use, value_usable

AGGREGATIONS = ("sum_squares", "sum_abs", "max_abs")
REASON_CODES = ("OUT_OF_BOUNDS", "UNKNOWN_VARIABLE", "MISSING_VARIABLE", "EDIT_REFUSED", "PROJECT_REFUSED",
                "PLAN_REFUSED", "RUN_STOPPED", "ANALYSIS_REFUSED", "MTF_SOURCE_NOT_FOUND", "QUANTITY_UNAVAILABLE",
                "RESIDUAL_NOT_FINITE", "AGGREGATE_NOT_FINITE", "ENGINE_ERROR")
USABLE_STATES = ("KNOWN", "KNOWN_ZERO")
DEFAULT_INVALID_RESIDUAL = 1.0e3
DEFAULT_IMAGING = {"launch_grid": 24, "include_psf_map": False}
#: Largest difference (nm) between an MTF selector's ``wavelength_nm`` and a curve's wavelength that still matches.
WAVELENGTH_MATCH_NM = 0.05
MTF_KEYS = ("observation", "frequency_cyc_per_mm", "direction", "source_id", "wavelength_nm", "quantity_id")
SOURCE_KINDS = ("target", "mtf", "paraxial", "solids", "rays")
#: ``system`` numbers of the Engine's paraxial analysis a ``paraxial`` source reads (mm).
PARAXIAL_QUANTITIES = ("efl_mm", "bfl_mm", "ffd_mm", "front_principal_plane_mm", "rear_principal_plane_mm")
PARAXIAL_KEYS = ("components", "wavelength_nm", "quantity", "axis", "source_id")
#: Per-gap numbers of the Engine's optical-solid report a ``solids`` source reads (mm).
SOLIDS_QUANTITIES = ("min_thickness_mm", "edge_thickness_mm", "centre_thickness_mm", "clear_aperture_min_thickness_mm")
SOLIDS_KEYS = ("quantity", "component", "member")
#: What lost pupil rays mean for a candidate (``Problem.ray_loss``).
RAY_LOSS_POLICIES = ("infeasible", "report")
#: Target metrics the Engine computes over the pupil rays that arrive (lost rays are not part of them).
PUPIL_METRICS = ("spot_rms_mm", "spot_centroid_u_mm", "spot_centroid_v_mm")
#: Most fields one imaging request names (the Engine's ``ImagingRequestV1.fields`` limit).
MAX_IMAGING_FIELDS = 32
#: Why a valid candidate is not feasible (``CandidateResult.infeasible``).
INFEASIBLE_CODES = ("CONSTRAINT_VIOLATED", "RAYS_LOST")
#: The reading kinds that are not EXACT, most severe first (the kind a multi-curve reading reports).
_KIND_SEVERITY = ("NO_VALUE", "OUTSIDE_MODEL", "UNDECIDED", "QUANTITY_UNKNOWN", "BOUNDED")


EMPTY_SCOPE = {"complete": False, "requested": [], "executed": [], "not_computed": [], "targets_not_evaluated": []}


def is_mtf_metric(metric: str) -> bool:
    """Whether a target metric is of the MTF family (``mtf``, ``mtf_*``, ``*_mtf_*``)."""
    return "mtf" in metric.lower()


class ProblemError(ValueError):
    """A problem declaration that cannot be evaluated (raised when the problem is built, never per candidate)."""


def _real(value: Any) -> bool:
    return isinstance(value, numbers.Real) and not isinstance(value, bool)


def _finite(value: Any) -> bool:
    return _real(value) and math.isfinite(float(value))


@dataclass(frozen=True)
class Variable:
    name: str
    ops: Sequence[dict[str, Any]]
    bounds: tuple[float, float]
    nominal: float = 0.0
    scale: float = 1.0
    unit: str | None = None

    def __post_init__(self) -> None:
        if len(self.bounds) != 2 or not all(_finite(b) for b in self.bounds) or not self.bounds[0] < self.bounds[1]:
            raise ProblemError(f"variable {self.name!r}: bounds must be finite with low < high")
        low, high = self.bounds
        if not (_finite(self.scale) and self.scale > 0):
            raise ProblemError(f"variable {self.name!r}: scale must be a positive finite number")
        if not (_finite(self.nominal) and low <= self.nominal <= high):
            raise ProblemError(f"variable {self.name!r}: nominal lies outside its bounds")
        if isinstance(self.ops, (str, bytes)) or not all(isinstance(op, dict) for op in self.ops):
            raise ProblemError(f"variable {self.name!r}: ops is a list of edit op objects")
        try:
            _substitute(copy.deepcopy(list(self.ops)), float(self.nominal), self.unit)
        except Exception as exc:
            raise ProblemError(f"variable {self.name!r}: the op template cannot take a value ({exc})") from None


@dataclass(frozen=True)
class Objective:
    name: str
    source: dict[str, Any]
    goal: float = 0.0
    scale: float = 1.0
    weight: float = 1.0


@dataclass(frozen=True)
class Constraint:
    name: str
    source: dict[str, Any]
    minimum: float | None = None
    maximum: float | None = None
    scale: float = 1.0


def _check_keys(owner: str, kind: str, spec: Any, allowed: Sequence[str], required: Sequence[str]) -> None:
    if not isinstance(spec, dict):
        raise ProblemError(f"{owner}: {kind} is an object")
    unknown = sorted(set(spec) - set(allowed))
    if unknown:
        raise ProblemError(f"{owner}: {kind} keys are {', '.join(allowed)}, not {', '.join(unknown)}")
    missing = [key for key in required if key not in spec]
    if missing:
        raise ProblemError(f"{owner}: {kind} needs {', '.join(missing)}")


def _check_paraxial(owner: str, spec: Any) -> None:
    _check_keys(owner, "paraxial", spec, PARAXIAL_KEYS, ("components", "wavelength_nm", "quantity"))
    components = spec["components"]
    if not isinstance(components, list) or not components or not all(isinstance(c, str) and c for c in components):
        raise ProblemError(f"{owner}: paraxial components is a non-empty list of component ids")
    if not (_finite(spec["wavelength_nm"]) and spec["wavelength_nm"] > 0):
        raise ProblemError(f"{owner}: paraxial wavelength_nm is a positive finite number")
    if spec["quantity"] not in PARAXIAL_QUANTITIES:
        raise ProblemError(f"{owner}: paraxial quantity is one of {', '.join(PARAXIAL_QUANTITIES)}")
    if "axis" in spec and spec["axis"] not in ("X", "Y"):
        raise ProblemError(f"{owner}: paraxial axis is X or Y")
    if "source_id" in spec and (not isinstance(spec["source_id"], str) or not spec["source_id"]):
        raise ProblemError(f"{owner}: paraxial source_id is a source id")


def _check_solids(owner: str, spec: Any) -> None:
    _check_keys(owner, "solids", spec, SOLIDS_KEYS, ("quantity",))
    if spec["quantity"] not in SOLIDS_QUANTITIES:
        raise ProblemError(f"{owner}: solids quantity is one of {', '.join(SOLIDS_QUANTITIES)}")
    for key in ("component", "member"):
        if key in spec and (not isinstance(spec[key], str) or not spec[key]):
            raise ProblemError(f"{owner}: solids {key} is an id")


def _check_source(owner: str, source: dict[str, Any], *, allow_mtf: bool) -> None:
    if not isinstance(source, dict) or len(source) != 1 or next(iter(source)) not in SOURCE_KINDS:
        raise ProblemError(f"{owner}: source is {{'target': ID}}, {{'rays': {{'target': ID}}}}, {{'paraxial': {{...}}}}, "
                           "{'solids': {...}} or {'mtf': {...}}")
    if "target" in source and not isinstance(source["target"], str):
        raise ProblemError(f"{owner}: target is a target id")
    if "rays" in source:
        _check_keys(owner, "rays", source["rays"], ("target",), ("target",))
        if not isinstance(source["rays"]["target"], str) or not source["rays"]["target"]:
            raise ProblemError(f"{owner}: rays target is a target id")
    if "paraxial" in source:
        _check_paraxial(owner, source["paraxial"])
    if "solids" in source:
        _check_solids(owner, source["solids"])
    if "mtf" in source:
        if not allow_mtf:
            raise ProblemError(f"{owner}: MTF is a constraint, not an objective; declare it under constraints")
        spec = source["mtf"]
        if not isinstance(spec, dict) or "observation" not in spec or "frequency_cyc_per_mm" not in spec:
            raise ProblemError(f"{owner}: mtf needs observation and frequency_cyc_per_mm")
        unknown = sorted(set(spec) - set(MTF_KEYS))
        if unknown:
            raise ProblemError(f"{owner}: mtf keys are {', '.join(MTF_KEYS)}, not {', '.join(unknown)} (a curve is "
                               "selected by source_id, wavelength_nm or quantity_id)")
        if not isinstance(spec["observation"], str) or not spec["observation"]:
            raise ProblemError(f"{owner}: mtf observation is an observation id")
        if not (_finite(spec["frequency_cyc_per_mm"]) and spec["frequency_cyc_per_mm"] >= 0):
            raise ProblemError(f"{owner}: mtf frequency_cyc_per_mm is a finite number >= 0")
        if spec.get("direction", "tangential") not in ("tangential", "sagittal"):
            raise ProblemError(f"{owner}: mtf direction is tangential or sagittal")
        for key in ("source_id", "quantity_id"):
            if key in spec and (not isinstance(spec[key], str) or not spec[key]):
                raise ProblemError(f"{owner}: mtf {key} is an id")
        if "wavelength_nm" in spec and not (_finite(spec["wavelength_nm"]) and spec["wavelength_nm"] > 0):
            raise ProblemError(f"{owner}: mtf wavelength_nm is a positive finite number")


def _bound(owner: str, label: str, value: Any) -> None:
    if value is not None and not _finite(value):
        raise ProblemError(f"{owner}: {label} must be a finite number")


@dataclass(frozen=True)
class Problem:
    project: dict[str, Any]
    variables: Sequence[Variable]
    objectives: Sequence[Objective] = ()
    constraints: Sequence[Constraint] = ()
    aggregation: str = "sum_squares"
    imaging: dict[str, Any] = field(default_factory=dict)
    invalid_residual: float = DEFAULT_INVALID_RESIDUAL
    value_use: str = "exact"
    ray_loss: str = "infeasible"

    def __post_init__(self) -> None:
        if self.aggregation not in AGGREGATIONS:
            raise ProblemError(f"aggregation is one of {', '.join(AGGREGATIONS)}")
        if self.value_use not in VALUE_USES:
            raise ProblemError(f"value_use is one of {', '.join(VALUE_USES)}")
        if self.ray_loss not in RAY_LOSS_POLICIES:
            raise ProblemError(f"ray_loss is one of {', '.join(RAY_LOSS_POLICIES)}")
        if not self.objectives and not self.constraints:
            raise ProblemError("a problem needs at least one objective or constraint")
        names = [v.name for v in self.variables]
        if len(set(names)) != len(names):
            raise ProblemError("variable names must be unique")
        sourced = [item.name for item in self.sourced()]
        if len(set(sourced)) != len(sourced):
            duplicated = sorted({n for n in sourced if sourced.count(n) > 1})
            raise ProblemError(f"objective and constraint names must be unique together: {', '.join(duplicated)}")
        for o in self.objectives:
            owner = f"objective {o.name!r}"
            _check_source(owner, o.source, allow_mtf=False)
            _bound(owner, "goal", o.goal)
            if not (_finite(o.scale) and o.scale > 0):
                raise ProblemError(f"{owner}: scale must be a positive finite number")
            if not (_finite(o.weight) and o.weight >= 0):
                raise ProblemError(f"{owner}: weight must be a finite number >= 0")
        for c in self.constraints:
            owner = f"constraint {c.name!r}"
            _check_source(owner, c.source, allow_mtf=True)
            if c.minimum is None and c.maximum is None:
                raise ProblemError(f"{owner}: give minimum and/or maximum")
            _bound(owner, "minimum", c.minimum)
            _bound(owner, "maximum", c.maximum)
            if c.minimum is not None and c.maximum is not None and c.minimum > c.maximum:
                raise ProblemError(f"{owner}: minimum {c.minimum:g} exceeds maximum {c.maximum:g}")
            if not (_finite(c.scale) and c.scale > 0):
                raise ProblemError(f"{owner}: scale must be a positive finite number")
        if not self.constraints and not any(o.weight > 0 for o in self.objectives):
            raise ProblemError("a problem without constraints needs an objective with weight > 0")
        metrics = {t.get("target_id"): str(t.get("metric") or "") for t in self.project.get("targets") or []}
        for o in self.objectives:
            tid = o.source.get("target")
            if tid is not None and is_mtf_metric(metrics.get(tid, "")):
                raise ProblemError(f"objective {o.name!r}: target {tid!r} is an MTF metric ({metrics[tid]}); MTF is a "
                                   "constraint, not an objective; declare it under constraints")
        declared = set(metrics)
        for item in self.sourced():
            if "target" in item.source and item.source["target"] not in declared:
                raise ProblemError(f"{item.name!r}: target {item.source['target']!r} is not declared in the Project")
            if "rays" in item.source and metrics.get(item.source["rays"]["target"]) not in PUPIL_METRICS:
                raise ProblemError(f"{item.name!r}: rays reads a declared target of a pupil-ray metric "
                                   f"({', '.join(PUPIL_METRICS)}), not {item.source['rays']['target']!r}")
        if not (_finite(self.invalid_residual) and self.invalid_residual > 0):
            raise ProblemError("invalid_residual must be a positive finite number")

    def sourced(self) -> list[Objective | Constraint]:
        return [*self.objectives, *self.constraints]

    @property
    def residual_names(self) -> list[str]:
        return [f"objective:{o.name}" for o in self.objectives] + [f"constraint:{c.name}" for c in self.constraints]

    def target_ids(self) -> list[str]:
        out: list[str] = []
        for item in self.sourced():
            tid = item.source.get("target")
            if tid is not None and tid not in out:
                out.append(tid)
        return out

    def mtf_observations(self) -> list[str]:
        out: list[str] = []
        for c in self.constraints:
            if "mtf" in c.source and c.source["mtf"]["observation"] not in out:
                out.append(c.source["mtf"]["observation"])
        return out

    def uses(self, kind: str) -> bool:
        """Whether an objective or constraint reads a source of ``kind`` (``SOURCE_KINDS``)."""
        return any(kind in item.source for item in self.sourced())

    def outputs(self) -> tuple[str, ...]:
        """What the session is asked for: ``requirements`` for target sources, ``plane_readings`` for MTF and rays
        sources, ``solid_check`` for solids sources (nothing for paraxial sources alone: the evaluation then only lowers the
        Project, which gives the identity)."""
        out = ["requirements"] if self.target_ids() else []
        if self.mtf_observations() or (self.uses("rays") and not out):
            out.append("plane_readings")
        if self.uses("solids"):
            out.append("solid_check")
        return tuple(out)


@dataclass
class CandidateResult:
    x: dict[str, Any]
    valid: bool
    residuals: list[float]
    residual_names: list[str]
    objective: float | None = None
    residual_objective: float | None = None
    dls_compatible: bool = False
    objectives: list[dict[str, Any]] = field(default_factory=list)
    constraints: list[dict[str, Any]] = field(default_factory=list)
    feasible: bool | None = None
    reason: dict[str, Any] | None = None
    evaluation: dict[str, Any] | None = None
    identity: dict[str, Any] | None = None
    value_use: str = "exact"
    #: Why a valid candidate is not feasible: ``CONSTRAINT_VIOLATED``, ``RAYS_LOST`` (``INFEASIBLE_CODES``).
    infeasible: list[str] = field(default_factory=list)
    #: Every objective or constraint reading a target that lost pupil rays: ``name``, ``target``, launched, arrived.
    ray_loss: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """The result as plain data that serializes as strict JSON: a non-finite number anywhere (an echoed candidate
        value or Engine reading) is written as ``None``."""
        return _json_safe({"x": dict(self.x), "valid": self.valid, "feasible": self.feasible, "objective": self.objective,
                "residual_objective": self.residual_objective, "dls_compatible": self.dls_compatible,
                "residuals": list(self.residuals), "residual_names": list(self.residual_names),
                "value_use": self.value_use, "objectives": self.objectives, "constraints": self.constraints,
                "infeasible": list(self.infeasible), "ray_loss": self.ray_loss,
                "reason": self.reason, "evaluation": self.evaluation, "identity": self.identity})


def _json_safe(value: Any) -> Any:
    """``value`` with every non-finite float replaced by ``None`` (dicts, lists and tuples walked)."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


class _Refused(Exception):
    def __init__(self, code: str, stage: str, message: str, *, grow: float = 1.0, **details: Any) -> None:
        super().__init__(message)
        self.reason = {"code": code, "stage": stage, "message": message, **details}
        self.grow = grow  # the invalid residual's growth factor (OUT_OF_BOUNDS: 1 + the violation)


def aggregate(errors: Sequence[float], weights: Sequence[float], how: str) -> float:
    if not errors:
        return 0.0
    if how == "sum_squares":
        return float(sum(w * e * e for e, w in zip(errors, weights)))
    if how == "sum_abs":
        return float(sum(w * abs(e) for e, w in zip(errors, weights)))
    return float(max(w * abs(e) for e, w in zip(errors, weights)))


def margin_of(value: float, constraint: Constraint) -> float:
    """Scaled distance to the nearest bound: positive inside, negative outside; NaN when any side is not a number."""
    sides = []
    if constraint.minimum is not None:
        sides.append((value - constraint.minimum) / constraint.scale)
    if constraint.maximum is not None:
        sides.append((constraint.maximum - value) / constraint.scale)
    if not sides or any(math.isnan(s) for s in sides):
        return math.nan
    return min(sides)


def _scope_of(ev: Any) -> dict[str, Any]:
    """The Engine's selective-evaluation scope (``ValidationResult.evaluation``) as plain JSON; a complete evaluation
    has none and reports ``complete: true``."""
    scope = ev.result.evaluation
    if scope is None:
        return {"complete": True, "requested": [], "executed": [], "not_computed": [], "targets_not_evaluated": []}
    doc = scope.model_dump(mode="json")
    return {"complete": False, "requested": doc["requested"], "executed": doc["executed"],
            "not_computed": doc["not_computed"], "targets_not_evaluated": doc["targets_not_evaluated"]}


class Adapter:
    """Evaluates candidates of one :class:`Problem` through one ``EvaluationSession`` (reused across candidates).

    ``session``: an ``os_optics.local.EvaluationSession`` (default: a new one). ``edit``: the function applying ops to a
    Project document (default: ``os_optics_kit.edit_project.apply_edits`` without the final check; the Engine
    evaluation checks the edited Project). ``paraxial``: the function ``(project, request) -> reply`` of
    ``paraxial`` sources (default: ``os_optics.local.paraxial_analysis``). The session runs one evaluation at a time:
    do not call one Adapter (or adapters sharing a session) from several threads at once."""

    def __init__(self, problem: Problem, session: Any = None,
                 edit: Callable[[dict[str, Any], list[dict[str, Any]]], dict[str, Any]] | None = None,
                 paraxial: Callable[[Any, dict[str, Any]], dict[str, Any]] | None = None) -> None:
        self.problem = problem
        if session is None:
            from os_optics.local import EvaluationSession

            session = EvaluationSession()
        self.session = session
        self._edit = edit or _apply_ops
        self._paraxial = paraxial

    @property
    def size(self) -> int:
        return len(self.problem.objectives) + len(self.problem.constraints)

    def nominal(self) -> dict[str, float]:
        return {v.name: v.nominal for v in self.problem.variables}

    def bounds(self) -> list[tuple[float, float]]:
        return [v.bounds for v in self.problem.variables]

    def scales(self) -> list[float]:
        return [v.scale for v in self.problem.variables]

    def vector(self, x: dict[str, float]) -> list[float]:
        return [float(x[v.name]) for v in self.problem.variables]

    def mapping(self, values: Sequence[float]) -> dict[str, float]:
        if len(values) != len(self.problem.variables):
            raise ProblemError(f"expected {len(self.problem.variables)} values, got {len(values)}")
        return {v.name: float(value) for v, value in zip(self.problem.variables, values)}

    def values_of(self, x: Mapping[str, Any] | Sequence[Any]) -> dict[str, Any]:
        """``{name: value}`` of a candidate given by name or in declaration order (raises ``_Refused``
        ``UNKNOWN_VARIABLE`` / ``MISSING_VARIABLE`` when it does not name exactly the declared variables)."""
        names = [v.name for v in self.problem.variables]
        if isinstance(x, Mapping):
            unknown = sorted(str(k) for k in x if k not in names)
            if unknown:
                raise _Refused("UNKNOWN_VARIABLE", "parameters", f"not declared: {', '.join(unknown)}",
                               unknown=unknown, declared=names)
            missing = [n for n in names if n not in x]
            if missing:
                raise _Refused("MISSING_VARIABLE", "parameters", f"no value for {', '.join(missing)}",
                               missing=missing, declared=names)
            return {n: x[n] for n in names}
        if isinstance(x, (str, bytes)):
            raise _Refused("MISSING_VARIABLE", "parameters", "a candidate is a mapping by name or a sequence of values",
                           declared=names)
        try:
            items = list(x)
        except TypeError:
            raise _Refused("MISSING_VARIABLE", "parameters", "a candidate is a mapping by name or a sequence of values",
                           declared=names) from None
        if len(items) > len(names):
            raise _Refused("UNKNOWN_VARIABLE", "parameters", f"{len(items)} values for {len(names)} variables",
                           expected=len(names), got=len(items), declared=names)
        if len(items) < len(names):
            missing = names[len(items):]
            raise _Refused("MISSING_VARIABLE", "parameters", f"{len(items)} values for {len(names)} variables",
                           missing=missing, expected=len(names), got=len(items), declared=names)
        return dict(zip(names, items))

    def candidate(self, x: Mapping[str, Any] | Sequence[Any]) -> dict[str, Any]:
        """The Project of ``x`` (raises ``_Refused`` for an unknown or missing variable, an out-of-bounds value or a
        refused edit)."""
        values = self.values_of(x)
        ops: list[dict[str, Any]] = []
        for v in self.problem.variables:
            raw = values[v.name]
            if not _finite(raw):
                raise _Refused("OUT_OF_BOUNDS", "parameters", f"{v.name}: value must be a finite real number",
                               variable=v.name)
            value = float(raw)
            low, high = v.bounds
            if not low <= value <= high:
                distance = float((low - value if value < low else value - high) / v.scale)
                raise _Refused("OUT_OF_BOUNDS", "parameters", f"{v.name}={value:g} lies outside [{low:g}, {high:g}]",
                               grow=1.0 + distance, variable=v.name, value=value, bounds=[low, high],
                               violation=distance if math.isfinite(distance) else None)
            try:
                ops.extend(_substitute(copy.deepcopy(list(v.ops)), value, v.unit))
            except Exception as exc:
                raise _Refused("EDIT_REFUSED", "parameters", f"{v.name}: {exc}",
                               error_code=type(exc).__name__) from None
        if not ops:
            return copy.deepcopy(self.problem.project)
        try:
            return self._edit(self.problem.project, ops)
        except Exception as exc:  # an edit refused for this candidate is a structured reason
            raise _Refused("EDIT_REFUSED", "parameters", str(exc), error_code=getattr(exc, "code", type(exc).__name__),
                           details=_details(exc)) from None

    def evaluate(self, x: Mapping[str, Any] | Sequence[Any]) -> CandidateResult:
        """One candidate: never raises, never drops it (module docstring)."""
        problem = self.problem
        result = CandidateResult(x={}, valid=False, residuals=[problem.invalid_residual] * self.size,
                                 residual_names=problem.residual_names, value_use=problem.value_use)
        try:
            values = self.values_of(x)
            result.x = {k: float(v) if _real(v) else v for k, v in values.items()}
            document = self.candidate(values)
            ev = self._run(document)
            result.evaluation = _scope_of(ev)
            result.identity = ev.identity
            readings = self._readings(ev, document)
            self._score(result, readings)
        except _Refused as refused:
            if not result.x and isinstance(x, Mapping):
                result.x = {str(k): v for k, v in x.items()}  # the candidate as given, for a refused variable set
            self._invalid(result, refused.reason, grow=refused.grow)
        except Exception as exc:  # anything else the Engine raises is still a reason, never an escape
            self._invalid(result, {"code": "ENGINE_ERROR", "stage": "evaluation", "message": str(exc),
                                   "error_code": getattr(exc, "code", type(exc).__name__)})
        assert len(result.residuals) == self.size
        return result

    # ------------------------------------------------------------------------------------------------ internals
    def _invalid(self, result: CandidateResult, reason: dict[str, Any], grow: float = 1.0) -> None:
        result.reason = reason
        result.valid = False
        base = self.problem.invalid_residual
        # grown (OUT_OF_BOUNDS), never below invalid_residual; the sum of the squares stays within half the float range
        ceiling = max(base, math.sqrt(sys.float_info.max / (2 * max(1, self.size))))
        result.residuals = [min(base * grow, ceiling)] * self.size
        if result.evaluation is None:
            result.evaluation = copy.deepcopy(EMPTY_SCOPE)
        result.objective = result.residual_objective = None
        result.dls_compatible = False
        result.feasible = None
        result.objectives, result.constraints = [], []
        result.infeasible, result.ray_loss = [], []

    def _run(self, document: dict[str, Any]) -> Any:
        targets = self.problem.target_ids()
        try:
            return self.session.evaluate(document, outputs=self.problem.outputs(), targets=targets or None)
        except Exception as exc:  # classified by the Engine's exception names (the session may be any object)
            names = {cls.__name__ for cls in type(exc).__mro__}
            if "LocalRunError" in names:
                raise _Refused("PROJECT_REFUSED", "evaluation", str(exc), details=_details(exc)) from None
            if "EvaluationPlanError" in names:
                raise _Refused("PLAN_REFUSED", "evaluation", str(exc), error_code=getattr(exc, "code", None)) from None
            if "RunStopped" in names:
                raise _Refused("RUN_STOPPED", "evaluation", str(exc)) from None
            raise

    def _readings(self, ev: Any, document: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Every source's reading: ``value``, ``value_kind``, the Engine's facts about it and where it came from;
        target readings under ``target:ID``, MTF readings under ``mtf:INDEX`` (the constraint's position), paraxial
        and solids readings under ``_reading_key``."""
        out: dict[str, dict[str, Any]] = {}
        by_target = {t.target_id: t for t in ev.result.targets}
        not_evaluated = {t["target_id"] for t in _scope_of(ev)["targets_not_evaluated"]}
        findings = _scope_findings(getattr(ev, "model_scope_issues", None))
        declared = {t.get("target_id"): t for t in document.get("targets") or [] if isinstance(t, dict)}
        for tid in self.problem.target_ids():
            t = by_target.get(tid)
            if t is None:
                out[f"target:{tid}"] = {"value": None, "state": "NOT_COMPUTED", "value_kind": "NO_VALUE",
                                        "value_basis": None, "not_evaluated": tid in not_evaluated,
                                        "message": "the target was not evaluated"}
                continue
            out[f"target:{tid}"] = _target_reading(t, findings)
            if t.metric in PUPIL_METRICS and tid in declared:
                out[f"target:{tid}"].update(_pupil_counts(ev, declared[tid]))
        for observation in self.problem.mtf_observations():
            out.update(self._mtf(ev, observation))
        analyses: dict[str, dict[str, Any]] = {}
        for item in self.problem.sourced():
            key = _reading_key(item.source)
            if key in out:
                continue
            if "paraxial" in item.source:
                out[key] = self._paraxial_reading(document, item.source["paraxial"], analyses)
            elif "solids" in item.source:
                out[key] = _solids_reading(ev, item.source["solids"])
            elif "rays" in item.source:
                out[key] = _rays_reading(ev, item.source["rays"]["target"], declared)
        return out

    def _paraxial_reading(self, document: dict[str, Any], spec: dict[str, Any],
                          analyses: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """``system.<quantity>`` of the paraxial analysis of ``spec``'s request (one analysis per request)."""
        request: dict[str, Any] = {k: spec[k] for k in ("components", "wavelength_nm", "axis", "source_id") if k in spec}
        cache_key = json.dumps(request, sort_keys=True)
        if cache_key not in analyses:
            analyse = self._paraxial
            if analyse is None:
                from os_optics.local import paraxial_analysis

                analyse = paraxial_analysis
            try:
                analyses[cache_key] = analyse(document, dict(request))
            except Exception as exc:  # ParaxialError / LocalRunError: the candidate's components have no analysis
                raise _Refused("ANALYSIS_REFUSED", "paraxial", str(exc), request=request,
                               error_code=getattr(exc, "code", None) or type(exc).__name__,
                               details=_details(exc)) from None
        system = analyses[cache_key].get("system") or {}
        quantity = spec["quantity"]
        raw: Any = system.get(quantity)
        value = float(raw) if _finite(raw) else None
        components = ", ".join(request["components"])
        return {"value": value, "unit": "mm", "state": "KNOWN" if value is not None else "NO_VALUE",
                "value_kind": "EXACT" if value is not None else "NO_VALUE", "quantity_id": f"paraxial.{quantity}",
                "wavelength_nm": request["wavelength_nm"], "value_basis": None,
                "basis_note": f"os_optics.local.paraxial_analysis of {components} at {request['wavelength_nm']} nm: "
                              f"system.{quantity} ({system.get('input_plane')} to {system.get('output_plane')})"}

    def _imaging_request(self, ev: Any, observation: str) -> dict[str, Any]:
        """The imaging request of ``observation``'s MTF constraints: the problem's, its frequencies extended by 0 and
        every constraint frequency, its fields and lines narrowed to the ones every constraint names."""
        request = {**DEFAULT_IMAGING, **self.problem.imaging}
        specs = [c.source["mtf"] for c in self.problem.constraints
                 if "mtf" in c.source and c.source["mtf"]["observation"] == observation]
        wanted = sorted({float(spec["frequency_cyc_per_mm"]) for spec in specs})
        request["frequencies_cyc_per_mm"] = sorted({0.0, *[float(f) for f in request.get("frequencies_cyc_per_mm") or []],
                                                    *wanted})
        sources = sorted({spec["source_id"] for spec in specs if "source_id" in spec})
        if "fields" not in request and all("source_id" in spec for spec in specs) and \
                0 < len(sources) <= MAX_IMAGING_FIELDS:
            request["fields"] = sources
        if "wavelengths_nm" not in request and all("wavelength_nm" in spec for spec in specs):
            # the Engine matches a requested line within 1e-6 nm: ask for its own line wavelengths
            lines = sorted({float(p.wavelength_nm) for p in getattr(ev.result, "paths", ()) or ()})
            chosen = sorted({w for w in lines for spec in specs
                             if abs(w - float(spec["wavelength_nm"])) <= WAVELENGTH_MATCH_NM})
            if chosen and all(any(abs(w - float(spec["wavelength_nm"])) <= WAVELENGTH_MATCH_NM for w in chosen)
                              for spec in specs):
                request["wavelengths_nm"] = chosen
        return request

    def _mtf(self, ev: Any, observation: str) -> dict[str, dict[str, Any]]:
        request = self._imaging_request(ev, observation)
        try:
            analysis = ev.results().imaging(observation, request)
        except Exception as exc:
            raise _Refused("ANALYSIS_REFUSED", "imaging", str(exc), observation=observation,
                           error_code=getattr(exc, "code", type(exc).__name__)) from None
        out: dict[str, dict[str, Any]] = {}
        for index, c in enumerate(self.problem.constraints):
            spec = c.source.get("mtf")
            if spec is None or spec["observation"] != observation:
                continue
            name = f"mtf_{spec.get('direction', 'tangential')}"
            curves = [q for q in analysis.quantities if q.quantity_id.rsplit(".", 1)[-1] == name
                      and getattr(q.scope, "observation_id", None) in (None, observation)]
            chosen = [q for q in curves if _selected(q, spec)]
            selector = {k: spec[k] for k in ("source_id", "wavelength_nm", "quantity_id") if k in spec}
            if not chosen:
                raise _Refused("MTF_SOURCE_NOT_FOUND", "results",
                               f"{c.name}: no {name} curve of {observation} matches {selector or 'the selection'}",
                               constraint=c.name, selector=selector,
                               available=[_curve_scope(q) for q in curves])
            members = [_mtf_member(analysis, q, float(spec["frequency_cyc_per_mm"])) for q in chosen]
            kinds = [m["value_kind"] for m in members]
            out[f"mtf:{index}"] = {"members": members, "selector": selector, "value_kind": _worst_kind(kinds),
                                   "state": None if len(members) != 1 else members[0]["state"],
                                   "imaging_request": copy.deepcopy(request)}
        return out

    def _reading(self, readings: dict[str, dict[str, Any]], key: str, name: str) -> dict[str, Any]:
        reading = readings[key]
        members = reading.get("members") or [reading]
        unusable = [m for m in members
                    if not value_usable(m.get("value_kind"), self.problem.value_use) or not _finite(m.get("value"))]
        if unusable:
            first = unusable[0]
            raise _Refused("QUANTITY_UNAVAILABLE", "results",
                           f"{name}: {first.get('quantity_id') or key} is not used under value_use "
                           f"{self.problem.value_use} (value_kind {first.get('value_kind')}, "
                           f"state {first.get('state') or first.get('status')})", quantity=key, reading=reading)
        return reading

    def _score(self, result: CandidateResult, readings: dict[str, dict[str, Any]]) -> None:
        problem = self.problem
        errors, weights, residuals = [], [], []
        ray_loss: list[dict[str, Any]] = []
        for o in problem.objectives:
            reading = self._reading(readings, _reading_key(o.source), o.name)
            ray_loss.extend(_lost(o.name, o.source, reading))
            error = (reading["value"] - o.goal) / o.scale
            errors.append(error)
            weights.append(o.weight)
            residuals.append(math.sqrt(o.weight) * error)
            result.objectives.append({"name": o.name, "source": o.source, "value": reading["value"], "goal": o.goal,
                                      "scaled_error": error, **_facts(reading)})
        satisfied_all: list[bool | None] = []
        for index, c in enumerate(problem.constraints):
            key = _reading_key(c.source, index)
            reading = self._reading(readings, key, c.name)
            ray_loss.extend(_lost(c.name, c.source, reading))
            members = reading.get("members") or [reading]
            margins = [margin_of(m["value"], c) for m in members]
            if not all(math.isfinite(m) for m in margins):
                raise _Refused("RESIDUAL_NOT_FINITE", "results", f"{c.name}: the margin is not a finite number",
                               quantity=key)
            binding = min(range(len(members)), key=margins.__getitem__)
            satisfied = _satisfied(members, margins)
            satisfied_all.append(satisfied)
            residuals.append(max(0.0, -margins[binding]))
            entry = {"name": c.name, "source": c.source, "value": members[binding]["value"], "minimum": c.minimum,
                     "maximum": c.maximum, "margin": margins[binding], "satisfied": satisfied,
                     **_facts(members[binding])}
            if "members" in reading:
                entry["value_kind"] = reading["value_kind"]
                entry["selector"] = reading["selector"]
                entry["members"] = [{**m, "margin": margin} for m, margin in zip(members, margins)]
                entry["imaging_request"] = reading["imaging_request"]
            result.constraints.append(entry)
        if not all(math.isfinite(r) for r in residuals):
            not_finite = [name for name, r in zip(problem.residual_names, residuals) if not math.isfinite(r)]
            raise _Refused("RESIDUAL_NOT_FINITE", "results",
                           f"a residual is not a finite number: {', '.join(not_finite)}", not_finite=not_finite,
                           residuals=[float(r) if math.isfinite(r) else None for r in residuals])
        objective = aggregate(errors, weights, problem.aggregation)
        residual_objective = float(sum(r * r for r in residuals))
        overflowed = [name for name, total in (("objective", objective), ("residual_objective", residual_objective))
                      if not math.isfinite(total)]
        if overflowed:
            largest = max(abs(r) for r in residuals)
            verb = "exceeds" if len(overflowed) == 1 else "exceed"
            advice = ("larger scales or smaller weights bring the residuals into range" if "residual_objective" in overflowed
                      else "smaller weights bring the objective into range")
            raise _Refused("AGGREGATE_NOT_FINITE", "results",
                           f"{' and '.join(overflowed)} {verb} the float range (largest residual {largest:.6g}); {advice}",
                           overflowed=overflowed, aggregation=problem.aggregation,
                           residuals=[float(r) for r in residuals], largest_residual=float(largest))
        result.objective = objective
        result.residuals = residuals
        result.residual_objective = residual_objective
        result.dls_compatible = problem.aggregation == "sum_squares"
        result.feasible = False if False in satisfied_all else (None if None in satisfied_all else True)
        result.ray_loss = ray_loss
        result.infeasible = ["CONSTRAINT_VIOLATED"] if False in satisfied_all else []
        if ray_loss and problem.ray_loss == "infeasible":
            result.infeasible.append("RAYS_LOST")
            result.feasible = False
        result.valid = True


def _satisfied(members: list[dict[str, Any]], margins: list[float]) -> bool | None:
    """True when every member is exact and inside; False when an exact member is outside; else not established."""
    if any(m["value_kind"] == "EXACT" and margin < 0 for m, margin in zip(members, margins)):
        return False
    if all(m["value_kind"] == "EXACT" for m in members):
        return all(margin >= 0 for margin in margins)
    return None


_FACT_KEYS = ("value_kind", "codes", "status", "assessment", "state", "quantity_id", "source_id", "field_id",
              "wavelength_nm", "value_basis", "basis_note", "pupil_rays_launched", "pupil_rays_arrived", "binding")


def _reading_key(source: dict[str, Any], index: int | None = None) -> str:
    """Where a source's reading is kept: ``target:ID``, ``mtf:INDEX`` (the constraint's position), or
    ``paraxial:`` / ``solids:`` and the canonical JSON of the spec (equal specs share one reading)."""
    kind = next(iter(source))
    if kind == "target":
        return f"target:{source['target']}"
    if kind == "mtf":
        return f"mtf:{index}"
    return f"{kind}:{json.dumps(source[kind], sort_keys=True, separators=(',', ':'))}"


def _lost(name: str, source: dict[str, Any], reading: dict[str, Any]) -> list[dict[str, Any]]:
    """The ray-loss entry of an objective or constraint whose target reading lost pupil rays (none otherwise; a
    ``rays`` source states its loss as its value)."""
    launched, arrived = reading.get("pupil_rays_launched"), reading.get("pupil_rays_arrived")
    if "target" not in source or launched is None or arrived is None or arrived >= launched:
        return []
    return [{"name": name, "target": source.get("target"), "pupil_rays_launched": launched,
             "pupil_rays_arrived": arrived, "pupil_rays_lost": launched - arrived}]


def _parameters(target: dict[str, Any]) -> dict[str, Any]:
    """A declared target's metric parameters as ``{key: value}`` (a Project lists ``[{key, value}]``)."""
    raw = target.get("parameters")
    if isinstance(raw, dict):
        return dict(raw)
    return {str(p.get("key")): p.get("value") for p in raw or () if isinstance(p, dict)}


def _pupil_counts(ev: Any, target: dict[str, Any]) -> dict[str, Any]:
    """``pupil_rays_launched`` / ``pupil_rays_arrived`` summed over the arrivals a pupil-ray target selects (its
    plane, ``source_id``, ``wavelength_nm`` within ``WAVELENGTH_MATCH_NM``, ``path_id``, ``crossing_index``)."""
    parameters = _parameters(target)
    arrivals = []
    for observation in getattr(ev.result, "observations", ()) or ():
        if observation.observation_id != target.get("observation_id"):
            continue
        for a in observation.arrivals:
            if target.get("source_id") is not None and a.beam.source_id != target["source_id"]:
                continue
            if target.get("wavelength_nm") is not None and \
                    abs(float(a.beam.wavelength_nm) - float(target["wavelength_nm"])) > WAVELENGTH_MATCH_NM:
                continue
            if parameters.get("path_id") is not None and a.beam.path_id != parameters["path_id"]:
                continue
            if parameters.get("crossing_index") is not None and a.crossing_index != parameters["crossing_index"]:
                continue
            arrivals.append(a)
    if not arrivals:
        return {}
    return {"pupil_rays_launched": sum(int(a.pupil_ray_count) for a in arrivals),
            "pupil_rays_arrived": sum(int(a.pupil_rays_arrived) for a in arrivals)}


def _rays_reading(ev: Any, target_id: str, declared: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """``pupil_rays_arrived / pupil_rays_launched`` of the arrivals a pupil-ray target selects (unit 1)."""
    counts = _pupil_counts(ev, declared.get(target_id) or {})
    reading: dict[str, Any] = {"value": None, "unit": "1", "state": "NO_VALUE", "value_kind": "NO_VALUE",
                               "quantity_id": f"rays.{target_id}", "value_basis": None, **counts}
    if not counts.get("pupil_rays_launched"):
        reading["message"] = f"no arrival of target {target_id!r} reaches its plane"
        return reading
    reading.update(value=counts["pupil_rays_arrived"] / counts["pupil_rays_launched"], state="KNOWN",
                   value_kind="EXACT", basis_note=f"pupil rays of target {target_id} that reach its plane, over those "
                                                  "launched (ArrivalResult.pupil_rays_arrived / pupil_ray_count)")
    return reading


def _solids_reading(ev: Any, spec: dict[str, Any]) -> dict[str, Any]:
    """The smallest ``spec['quantity']`` over the selected glass gaps of the optical-solid report."""
    quantity = spec["quantity"]
    reading: dict[str, Any] = {"value": None, "unit": "mm", "state": "NO_VALUE", "value_kind": "NO_VALUE",
                               "quantity_id": f"solids.{quantity}", "value_basis": None, "binding": None}
    report = getattr(ev, "optical_solids", None)
    document = report.as_dict() if report is not None and callable(getattr(report, "as_dict", None)) else report
    if not isinstance(document, dict):
        reading["message"] = "the optical-solid check was not computed"
        return reading
    gaps = []
    for element in document.get("elements") or ():
        if spec.get("component") is not None and element.get("instance_id") != spec["component"]:
            continue
        if spec.get("member") is not None and element.get("member_id") != spec["member"]:
            continue
        for gap in element.get("gaps") or ():
            if _finite(gap.get(quantity)):
                gaps.append({"instance_id": element.get("instance_id"), "member_id": element.get("member_id"),
                             "from_surface": gap.get("from_surface"), "to_surface": gap.get("to_surface"),
                             "medium": gap.get("medium"), "value": float(gap[quantity])})
    reading["status"] = document.get("status")
    selection = {k: spec[k] for k in ("component", "member") if k in spec}
    if not gaps:
        reading["message"] = f"no glass gap of {selection or 'the Project'} states {quantity}"
        return reading
    binding = min(gaps, key=lambda g: g["value"])
    reading.update(value=binding["value"], state="KNOWN", value_kind="EXACT", binding=binding, gaps=len(gaps),
                   basis_note=f"optical-solid check (solid_check): smallest {quantity} over {len(gaps)} gap(s) of "
                              f"{selection or 'every element'}")
    return reading


def _facts(reading: dict[str, Any]) -> dict[str, Any]:
    return {k: reading.get(k) for k in _FACT_KEYS if k in reading}


def _worst_kind(kinds: Sequence[str]) -> str:
    for kind in _KIND_SEVERITY:
        if kind in kinds:
            return kind
    return "EXACT"


def _selected(q: Any, spec: dict[str, Any]) -> bool:
    if "quantity_id" in spec and q.quantity_id != spec["quantity_id"]:
        return False
    if "source_id" in spec and getattr(q.scope, "source_id", None) != spec["source_id"]:
        return False
    if "wavelength_nm" in spec:
        wavelength = getattr(q.scope, "wavelength_nm", None)
        if wavelength is None or abs(float(wavelength) - float(spec["wavelength_nm"])) > WAVELENGTH_MATCH_NM:
            return False
    return True


def _curve_scope(q: Any) -> dict[str, Any]:
    scope = q.scope
    return {"quantity_id": q.quantity_id, "source_id": getattr(scope, "source_id", None),
            "field_id": getattr(scope, "field_id", None), "wavelength_nm": getattr(scope, "wavelength_nm", None)}


def _mtf_member(analysis: Any, q: Any, frequency: float) -> dict[str, Any]:
    prefix = q.quantity_id.rsplit(".", 1)[0]
    state = str(q.state.value)
    converged = None if q.convergence is None else q.convergence.converged
    reading: dict[str, Any] = {**_curve_scope(q), "state": state,
                               "value_basis": None if q.value_basis is None else _jsonable(q.value_basis),
                               "reason": q.reason, "message": q.message, "converged": converged,
                               "convergence": None if q.convergence is None else _jsonable(q.convergence), "value": None}
    if q.value_basis is None:
        reading["basis_note"] = (f"read from the imaging curve {q.values_ref} at the frequency axis "
                                 f"{prefix}.frequencies_cyc_per_mm (state {state}, no engine value_basis)")
    curve = analysis.arrays.get(q.values_ref) if q.values_ref else None
    axis = analysis.arrays.get(f"{prefix}.frequencies_cyc_per_mm")
    if isinstance(curve, list) and isinstance(axis, list):
        index = next((i for i, f in enumerate(axis) if abs(float(f) - frequency) <= 1e-9 * max(1.0, frequency)), None)
        if index is not None and curve[index] is not None:
            reading["value"] = float(curve[index])
    kind, failed = _basis_facts(q.value_basis)
    reading["value_kind"] = _quantity_kind(state, reading["value"], kind, failed, converged)
    return reading


def _target_reading(t: Any, findings: list[tuple[str, tuple[str, ...]]]) -> dict[str, Any]:
    """A target's reading with the kind of its value (``workflow_common.target_value_use`` on the Engine's facts)."""
    applicability = sorted({code for code, ids in findings if t.target_id in ids})
    failed_checks = [c for c in getattr(t, "validity", None) or () if _get(c, "within") is False]
    entry = _basis_entry(t.value_basis)
    kind, basis_failed = _basis_facts(entry)
    quantity_state = None
    if failed_checks or (kind == "ESTIMATE" and basis_failed):
        quantity_state = "OUT_OF_MODEL_SCOPE"
    elif kind in ("LOWER_BOUND", "UPPER_BOUND"):
        quantity_state = "BOUNDED"
    elif kind == "ESTIMATE":
        quantity_state = "UNKNOWN"
    status = str(t.status.value)
    assessment = getattr(t, "assessment", None)
    use = target_value_use({"value": t.value, "status": status, "quantity_state": quantity_state,
                            "applicability_issue_codes": applicability, "issue_codes": list(t.issue_codes)})
    return {"value": t.value, "unit": t.unit, "metric": t.metric, "status": status,
            "assessment": None if assessment is None else str(getattr(assessment, "value", assessment)),
            "value_kind": use["kind"], "codes": use["codes"], "quantity_state": quantity_state,
            "value_basis": None if t.value_basis is None else _jsonable(t.value_basis),
            "validity_failed": [_jsonable(c) for c in failed_checks], "issue_codes": list(t.issue_codes),
            "message": t.message}


def _scope_findings(issues: Any) -> list[tuple[str, tuple[str, ...]]]:
    """``(code, affected_target_ids)`` of each model-scope finding of an evaluation (none when not computed)."""
    out = []
    for issue in issues or ():
        code = _get(issue, "code")
        if code:
            out.append((str(code), tuple(_get(issue, "affected_target_ids") or ())))
    return out


def _get(item: Any, key: str) -> Any:
    return item.get(key) if isinstance(item, dict) else getattr(item, key, None)


def _basis_entry(basis: Any) -> Any:
    """The value basis entry of a record's ``value`` (a target's ``value_basis`` is keyed by field)."""
    if isinstance(basis, dict) and "kind" not in basis:
        return basis.get("value") or basis.get("*")
    return basis


def _apply_ops(document: dict[str, Any], ops: list[dict[str, Any]]) -> dict[str, Any]:
    from ..edit_project import apply_edits

    return apply_edits(document, ops, validate=False)["project"]


def _substitute(template: Any, value: float, unit: str | None) -> Any:
    from ..sweep_parameters import substitute

    return substitute(template, value, unit)


def _details(exc: Exception) -> Any:
    as_dict = getattr(exc, "as_dict", None)
    if callable(as_dict):
        try:
            return _jsonable(as_dict())
        except Exception:
            pass
    problems = getattr(exc, "problems", None)
    return _jsonable(problems) if problems is not None else None


def _jsonable(value: Any) -> Any:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


__all__ = ["AGGREGATIONS", "Adapter", "CandidateResult", "Constraint", "INFEASIBLE_CODES", "MAX_IMAGING_FIELDS",
           "MTF_KEYS", "Objective", "PARAXIAL_QUANTITIES", "PUPIL_METRICS", "Problem", "RAY_LOSS_POLICIES",
           "SOLIDS_QUANTITIES", "SOURCE_KINDS",
           "ProblemError", "REASON_CODES", "Variable", "WAVELENGTH_MATCH_NM", "aggregate", "margin_of"]
