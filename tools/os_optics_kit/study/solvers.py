"""Bounded local solvers on the evaluation adapter: SciPy's trust-region reflective least squares (``trf``) and SLSQP.

:func:`solve` drives an :class:`~os_optics_kit.study.evaluation_adapter.Adapter` from its nominal point within the
variables' bounds and returns every physical evaluation it made with the solver's stopping reason. The rules:

* **Scaled variables.** The solvers work in ``z = (x - x_start) / scale`` per variable, so a unit step is one variable
  scale; ``x_scale: "variables"`` keeps that metric for ``trf`` (``"jac"`` lets SciPy rescale by the Jacobian).
* **Finite differences are evaluations.** Derivatives are central differences (``difference: "forward"`` for one-sided)
  with the step ``diff_step`` in variable scales, one-sided where a central probe would leave the bounds. Every probe is
  an adapter evaluation: it is counted, cached and recorded like any other candidate, and it never leaves the bounds.
* **Cache.** Evaluations are cached by the exact candidate vector: a repeated point costs nothing and is counted once
  (``cache_hits`` counts the repeats).
* **Budget.** ``max_evaluations`` caps the physical evaluations (the start included). The evaluation that would exceed
  it is not run: the search stops with ``BUDGET_EXHAUSTED``.
* ``trf``: ``scipy.optimize.least_squares(method="trf")`` on the adapter's residual vector (objective errors, then
  constraint violations as quadratic penalties); only for a problem whose residuals are its objective
  (``aggregation: "sum_squares"``, ``dls_compatible``). An invalid candidate's residuals are the adapter's
  ``invalid_residual`` penalties.
* ``slsqp``: ``scipy.optimize.minimize(method="SLSQP")`` on the adapter's ``objective`` with every constraint margin
  ``>= 0`` as an inequality constraint. An invalid candidate scores the sum of its squared residuals and margins of
  ``-invalid_residual``. ``xtol`` and ``gtol`` are not used by SLSQP.
* Feasibility is the adapter's (``CandidateResult.feasible``, ray loss included); a solver step does not decide it.

:func:`best` picks the feasible evaluation with the smallest objective (the earliest on a tie). The solvers are
deterministic: the same problem, start, settings and Engine give the same evaluations in the same order.

SciPy and numpy come with os-optics (its core dependencies) and are imported when a solver runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import time
from typing import Any, Callable, Sequence

METHODS = ("trf", "slsqp")
DIFFERENCES = ("central", "forward")
X_SCALES = ("variables", "jac")
#: Why a search stopped (``SolveOutcome.reason``).
TERMINATIONS = ("CONVERGED", "BUDGET_EXHAUSTED", "ITERATION_LIMIT", "SOLVER_STOPPED", "INTERRUPTED")
#: Roles of an evaluation in the history.
ROLES = ("start", "step", "jacobian")


class SettingsError(ValueError):
    """Solver settings that cannot run (raised when the settings are built)."""


def _positive(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


@dataclass(frozen=True)
class Settings:
    """Solver settings (module docstring). ``max_iterations``: SciPy's own cap (``trf`` ``max_nfev``, ``slsqp``
    ``maxiter``; ``None``: SciPy's default)."""

    method: str = "trf"
    max_evaluations: int = 200
    max_iterations: int | None = None
    ftol: float = 1e-8
    xtol: float = 1e-8
    gtol: float = 1e-8
    diff_step: float = 1e-3
    difference: str = "central"
    x_scale: str = "variables"

    def __post_init__(self) -> None:
        if self.method not in METHODS:
            raise SettingsError(f"method is one of {', '.join(METHODS)}")
        for name in ("max_evaluations", "max_iterations"):
            value = getattr(self, name)
            if value is None and name == "max_iterations":
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise SettingsError(f"{name} must be a positive integer")
        for name in ("ftol", "xtol", "gtol", "diff_step"):
            if not _positive(getattr(self, name)):
                raise SettingsError(f"{name} must be a positive finite number")
        if self.difference not in DIFFERENCES:
            raise SettingsError(f"difference is one of {', '.join(DIFFERENCES)}")
        if self.x_scale not in X_SCALES:
            raise SettingsError(f"x_scale is one of {', '.join(X_SCALES)}")

    def as_dict(self) -> dict[str, Any]:
        return {"method": self.method, "max_evaluations": self.max_evaluations, "max_iterations": self.max_iterations,
                "ftol": self.ftol, "xtol": self.xtol, "gtol": self.gtol, "diff_step": self.diff_step,
                "difference": self.difference, "x_scale": self.x_scale}


class BudgetExhausted(Exception):
    """The next physical evaluation would exceed ``max_evaluations``."""


@dataclass
class EvaluationRecord:
    """One physical evaluation: ``index`` (0-based, in order), ``x`` (by name), ``role``, the adapter's result and the
    wall time of the call."""

    index: int
    x: dict[str, float]
    role: str
    result: Any
    seconds: float


@dataclass
class SolveOutcome:
    """What :func:`solve` did: ``reason`` (``TERMINATIONS``), the solver's ``message`` and ``status``, its last point
    (``solver_x``, by name; ``None`` when it stopped before returning one), ``iterations`` (``trf``: Jacobian
    evaluations, one per iteration; ``slsqp``: ``nit``; ``None`` when it stopped before returning), ``solver_calls`` (function calls the solver made, cached
    ones included), the physical ``evaluations``, ``cache_hits`` and wall ``seconds``."""

    method: str
    reason: str
    message: str
    status: int | None
    solver_x: dict[str, float] | None
    iterations: int | None
    solver_calls: int | None
    evaluations: list[EvaluationRecord] = field(default_factory=list)
    cache_hits: int = 0
    seconds: float = 0.0

    @property
    def converged(self) -> bool:
        return self.reason == "CONVERGED"


Recorder = Callable[[EvaluationRecord], None]


class Evaluator:
    """Adapter evaluations by exact candidate vector: cached, counted, recorded and capped (module docstring)."""

    def __init__(self, adapter: Any, max_evaluations: int, recorder: Recorder | None = None,
                 clock: Callable[[], float] = time.perf_counter) -> None:
        self.adapter = adapter
        self.max_evaluations = max_evaluations
        self.recorder = recorder
        self.clock = clock
        self.records: list[EvaluationRecord] = []
        self.cache: dict[tuple[float, ...], EvaluationRecord] = {}
        self.cache_hits = 0

    def __call__(self, x: Sequence[float], role: str) -> Any:
        key = tuple(float(v) for v in x)
        if key in self.cache:
            self.cache_hits += 1
            return self.cache[key].result
        if len(self.records) >= self.max_evaluations:
            raise BudgetExhausted(f"max_evaluations {self.max_evaluations} reached")
        started = self.clock()
        result = self.adapter.evaluate(list(key))
        record = EvaluationRecord(index=len(self.records), x=self.adapter.mapping(key), role=role, result=result,
                                  seconds=self.clock() - started)
        self.records.append(record)
        self.cache[key] = record
        if self.recorder is not None:
            self.recorder(record)
        return result


def objective_value(result: Any) -> float:
    """The score SLSQP minimizes: the adapter's ``objective``, or for an invalid candidate the sum of its squared
    residuals (finite penalties)."""
    if result.valid and result.objective is not None:
        return float(result.objective)
    return float(sum(r * r for r in result.residuals))


def margins(result: Any, count: int, invalid_residual: float) -> list[float]:
    """The constraint margins of a candidate in declaration order (``-invalid_residual`` each when invalid)."""
    if not result.valid or len(result.constraints) != count:
        return [-float(invalid_residual)] * count
    return [float(c["margin"]) for c in result.constraints]


def best(records: Sequence[EvaluationRecord]) -> EvaluationRecord | None:
    """The feasible evaluation with the smallest objective (earliest on a tie); ``None`` when none is feasible."""
    feasible = [r for r in records if r.result.valid and r.result.feasible is True]
    if not feasible:
        return None
    return min(feasible, key=lambda r: (objective_value(r.result), r.index))


def closest(records: Sequence[EvaluationRecord]) -> EvaluationRecord | None:
    """The valid evaluation nearest to feasible: the smallest sum of squared constraint violations (``residuals`` past
    the objectives), then the smallest objective, then the earliest; ``None`` when none is valid."""
    valid = [r for r in records if r.result.valid]
    if not valid:
        return None

    def violation(record: EvaluationRecord) -> float:
        count = len(record.result.constraints)
        tail = record.result.residuals[len(record.result.residuals) - count:] if count else []
        return float(sum(v * v for v in tail))

    return min(valid, key=lambda r: (violation(r), objective_value(r.result), r.index))


class _Space:
    """Scaled coordinates ``z = (x - start) / scale`` within the bounds."""

    def __init__(self, start: Sequence[float], scales: Sequence[float], bounds: Sequence[tuple[float, float]]):
        self.start, self.scales = [float(v) for v in start], [float(s) for s in scales]
        self.low = [(lo - x0) / s for (lo, _), x0, s in zip(bounds, self.start, self.scales)]
        self.high = [(hi - x0) / s for (_, hi), x0, s in zip(bounds, self.start, self.scales)]
        self.bounds = [tuple(b) for b in bounds]

    def x(self, z: Sequence[float]) -> list[float]:
        """The candidate of ``z``, clipped to the bounds (a solver may round past them)."""
        out = []
        for zi, x0, s, (lo, hi) in zip(z, self.start, self.scales, self.bounds):
            out.append(min(max(x0 + s * float(zi), lo), hi))
        return out


def _probes(z: Sequence[float], space: _Space, step: float, difference: str) -> list[tuple[list[float], list[float], float]]:
    """Per variable: ``(z_plus, z_minus, h)`` of a difference quotient, one-sided where a probe leaves the bounds."""
    out = []
    for i, zi in enumerate(z):
        up, down = list(map(float, z)), list(map(float, z))
        if difference == "central" and zi - step >= space.low[i] and zi + step <= space.high[i]:
            up[i], down[i] = zi + step, zi - step
            out.append((up, down, 2.0 * step))
        elif zi + step <= space.high[i]:
            up[i] = zi + step
            out.append((up, down, step))
        else:
            down[i] = zi - step
            out.append((up, down, step))
    return out


def _jacobian(values: Callable[[Sequence[float], str], list[float]], z: Sequence[float], space: _Space,
              settings: Settings) -> list[list[float]]:
    """Finite-difference Jacobian ``d values / d z`` (rows: values, columns: variables)."""
    columns = []
    for up, down, h in _probes(z, space, settings.diff_step, settings.difference):
        plus, minus = values(up, "jacobian"), values(down, "jacobian")
        columns.append([(p - m) / h for p, m in zip(plus, minus)])
    rows = len(columns[0]) if columns else 0
    return [[columns[j][i] for j in range(len(columns))] for i in range(rows)]


def solve(adapter: Any, settings: Settings, recorder: Recorder | None = None) -> SolveOutcome:
    """Run ``settings.method`` from the adapter's nominal point (module docstring). Never raises for a candidate; a
    budget stop, an iteration cap, a solver failure and Ctrl-C are stopping reasons of the outcome."""
    problem = adapter.problem
    if settings.method == "trf" and problem.aggregation != "sum_squares":
        raise SettingsError("trf solves the residuals' sum of squares: it needs aggregation sum_squares "
                            "(dls_compatible); use slsqp for sum_abs or max_abs")
    if not problem.variables:
        raise SettingsError("a search needs at least one variable")
    nominal = adapter.vector(adapter.nominal())
    space = _Space(nominal, adapter.scales(), adapter.bounds())
    evaluator = Evaluator(adapter, settings.max_evaluations, recorder)
    started = time.perf_counter()
    z0 = [0.0] * len(nominal)
    outcome = SolveOutcome(method=settings.method, reason="SOLVER_STOPPED", message="", status=None, solver_x=None,
                           iterations=None, solver_calls=None)
    try:
        evaluator(space.x(z0), "start")
        if settings.method == "trf":
            _trf(evaluator, space, settings, outcome)
        else:
            _slsqp(evaluator, space, settings, outcome, len(problem.constraints), problem.invalid_residual)
    except BudgetExhausted as exc:
        outcome.reason, outcome.message = "BUDGET_EXHAUSTED", str(exc)
    except KeyboardInterrupt:
        outcome.reason, outcome.message = "INTERRUPTED", "interrupted (Ctrl-C)"
    outcome.evaluations = evaluator.records
    outcome.cache_hits = evaluator.cache_hits
    outcome.seconds = time.perf_counter() - started
    return outcome


def _trf(evaluator: Evaluator, space: _Space, settings: Settings, outcome: SolveOutcome) -> None:
    import numpy as np
    from scipy.optimize import least_squares

    def residuals(z: Sequence[float], role: str = "step") -> list[float]:
        return [float(r) for r in evaluator(space.x(z), role).residuals]

    def jacobian(z: Sequence[float]) -> Any:
        return np.asarray(_jacobian(residuals, z, space, settings), dtype=float)

    res = least_squares(lambda z: np.asarray(residuals(z), dtype=float), np.zeros(len(space.start)), jac=jacobian,
                        bounds=(np.asarray(space.low), np.asarray(space.high)), method="trf",
                        x_scale=1.0 if settings.x_scale == "variables" else "jac", ftol=settings.ftol,
                        xtol=settings.xtol, gtol=settings.gtol, max_nfev=settings.max_iterations)
    outcome.status, outcome.message = int(res.status), str(res.message)
    outcome.solver_x = evaluator.adapter.mapping(space.x(res.x))
    outcome.iterations, outcome.solver_calls = int(res.njev or 0), int(res.nfev)
    outcome.reason = "CONVERGED" if res.status > 0 else ("ITERATION_LIMIT" if res.status == 0 else "SOLVER_STOPPED")


def _slsqp(evaluator: Evaluator, space: _Space, settings: Settings, outcome: SolveOutcome, count: int,
           invalid_residual: float) -> None:
    import numpy as np
    from scipy.optimize import Bounds, minimize

    calls = [0]

    def objective(z: Sequence[float], role: str = "step") -> list[float]:
        calls[0] += role == "step"
        return [objective_value(evaluator(space.x(z), role))]

    def constraint(z: Sequence[float], role: str = "step") -> list[float]:
        return margins(evaluator(space.x(z), role), count, invalid_residual)

    constraints = []
    if count:
        constraints.append({"type": "ineq", "fun": lambda z: np.asarray(constraint(z), dtype=float),
                            "jac": lambda z: np.asarray(_jacobian(constraint, z, space, settings), dtype=float)})
    options: dict[str, Any] = {"ftol": settings.ftol}
    if settings.max_iterations is not None:
        options["maxiter"] = settings.max_iterations
    res = minimize(lambda z: objective(z)[0], np.zeros(len(space.start)), method="SLSQP",
                   jac=lambda z: np.asarray(_jacobian(objective, z, space, settings)[0], dtype=float),
                   bounds=Bounds(np.asarray(space.low), np.asarray(space.high)), constraints=constraints,
                   options=options)
    outcome.status, outcome.message = int(res.status), str(res.message)
    outcome.solver_x = evaluator.adapter.mapping(space.x(res.x))
    outcome.iterations, outcome.solver_calls = int(getattr(res, "nit", 0) or 0), calls[0]
    outcome.reason = "CONVERGED" if res.status == 0 else ("ITERATION_LIMIT" if res.status == 9 else "SOLVER_STOPPED")


__all__ = ["BudgetExhausted", "DIFFERENCES", "EvaluationRecord", "Evaluator", "METHODS", "ROLES", "Settings",
           "SettingsError", "SolveOutcome", "TERMINATIONS", "X_SCALES", "best", "closest", "margins", "objective_value",
           "solve"]
