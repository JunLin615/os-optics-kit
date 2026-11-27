"""os-optics-kit optimize: spec validation, the solvers on a fake session, history/result shape, the adapter's paraxial,
solids, ray-loss and narrowed-MTF sources, the set_optical_model_value edit op, and the beam-expander case."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from os_optics_kit import optimize
from os_optics_kit import workflow_common as wc
from os_optics_kit.edit_project import EditError, _normalize_model_value, engine_op, model_value
from os_optics_kit.study import evaluation_adapter as ea
from os_optics_kit.study import solvers

ROOT = Path(__file__).resolve().parents[1]
HAS_ENGINE = importlib.util.find_spec("os_optics") is not None
HAS_SCIPY = importlib.util.find_spec("scipy") is not None
engine = pytest.mark.skipif(not HAS_ENGINE, reason="os_optics is not importable")
scipy_needed = pytest.mark.skipif(not HAS_SCIPY, reason="scipy (installed with os-optics) is not importable")


# --------------------------------------------------------------------------------------------- fakes

def _scope():
    return SimpleNamespace(model_dump=lambda mode=None: {"requested": ["requirements"], "executed": ["requirements"],
                                                         "not_computed": [], "targets_not_evaluated": []})


def _target(tid, value):
    return SimpleNamespace(target_id=tid, value=value, unit="mm", status=SimpleNamespace(value="PASS"), metric="m",
                           assessment=None, value_basis=None, validity=(), issue_codes=(), message="")


class QuadraticSession:
    """Targets ``a`` and ``b`` read the candidate's variables (``doc["x"]``); ``sum`` = a + b. Counts evaluations."""

    def __init__(self):
        self.calls = 0

    def evaluate(self, document, outputs=(), targets=None):
        self.calls += 1
        x = document.get("x", {})
        a, b = float(x.get("a", 0.0)), float(x.get("b", 0.0))
        values = {"a": a, "b": b, "sum": a + b}
        return SimpleNamespace(result=SimpleNamespace(targets=[_target(k, v) for k, v in values.items()],
                                                      evaluation=_scope()),
                               identity={"engine_version": "fake", "engine_build_id": "fake", "project_hash": "h",
                                         "engine_input_hash": json.dumps(x, sort_keys=True), "result_hash": "r"},
                               model_scope_issues=())


def _set(document, ops):
    out = copy.deepcopy(document)
    for op in ops:
        if op.get("op") != "set_x":
            raise EditError("PROJECT_OP_INVALID", f"unknown op {op.get('op')!r}", op_code="UNKNOWN_OP")
        out.setdefault("x", {})[op["name"]] = op["value"]
    return out


PROJECT = {"targets": [{"target_id": "a"}, {"target_id": "b"}, {"target_id": "sum"}], "x": {"a": 0.0, "b": 0.0}}


def _spec(**changes):
    spec = {"description": "a quadratic bowl with its minimum at a = 1, b = -2, for the solver tests",
            "project": copy.deepcopy(PROJECT),
            "variables": [{"name": "a", "ops": [{"op": "set_x", "name": "a", "value": "$value"}], "bounds": [-5, 5],
                           "nominal": 0.0, "scale": 1.0},
                          {"name": "b", "ops": [{"op": "set_x", "name": "b", "value": "$value"}], "bounds": [-5, 5],
                           "nominal": 0.0, "scale": 1.0}],
            "objectives": [{"name": "a_to_1", "source": {"target": "a"}, "goal": 1.0},
                           {"name": "b_to_m2", "source": {"target": "b"}, "goal": -2.0}],
            "solver": {"method": "trf", "max_evaluations": 80, "tolerances": {"ftol": 1e-12, "xtol": 1e-12,
                                                                             "gtol": 1e-12}}}
    spec.update(changes)
    return spec


@pytest.fixture
def fake(monkeypatch):
    """``optimize.run`` on the fake session: no Engine check of the base Project."""
    monkeypatch.setattr(optimize, "_base_identity", lambda document: {"project_hash": "h", "engine_input_hash": "i",
                                                                      "catalog_version": None})
    sessions = []

    def factory():
        sessions.append(QuadraticSession())
        return sessions[-1]

    def run(spec, out, **kw):
        return optimize.run(spec, out, session_factory=factory, adapter_options={"edit": _set}, **kw)

    run.sessions = sessions
    return run


def _history(out):
    return [json.loads(line) for line in (out / "history.jsonl").read_text(encoding="utf-8").splitlines()]


# --------------------------------------------------------------------------------------------- solvers

@scipy_needed
@pytest.mark.parametrize("method", ["trf", "slsqp"])
def test_solvers_find_the_known_optimum_and_converge(fake, tmp_path, method):
    result = fake(_spec(), tmp_path / "out", method=method)
    assert result["status"] == "CONVERGED" and result["termination"]["reason"] == "CONVERGED"
    best = result["best"]["candidate"]
    assert best["a"] == pytest.approx(1.0, abs=1e-6) and best["b"] == pytest.approx(-2.0, abs=1e-6)
    assert result["best"]["objective"] == pytest.approx(0.0, abs=1e-10)
    assert (tmp_path / "out" / "best-project.json").is_file()
    assert result["verification"]["reproduction"]["status"] == "REPRODUCED"


@scipy_needed
def test_slsqp_holds_an_active_constraint():
    spec = _spec(constraints=[{"name": "a_floor", "source": {"target": "a"}, "minimum": 1.5}])
    loaded = optimize.load_spec(spec, edit=_set)
    adapter = ea.Adapter(loaded["problem"], session=QuadraticSession(), edit=_set)
    outcome = solvers.solve(adapter, solvers.Settings(method="slsqp", max_evaluations=80))
    chosen = solvers.best(outcome.evaluations)
    assert outcome.converged and chosen.x["a"] == pytest.approx(1.5, abs=1e-6)
    summary = optimize.candidate_summary(chosen, loaded["problem"], 1e-3)
    assert summary["active_constraints"] == ["a_floor"] and summary["constraints"][0]["active"] is True
    assert summary["worst_residual"]["name"] == "objective:a_to_1"
    assert summary["worst_residual"]["conditions"]["target"] == "a"


@scipy_needed
def test_trf_converging_outside_an_active_constraint_is_not_converged(fake, tmp_path):
    """trf treats a constraint as a quadratic penalty: where it binds, the solver's last point lies just outside it.
    That is CONVERGED_INFEASIBLE / STOPPED, never CONVERGED with a feasible best taken from elsewhere."""
    spec = _spec(constraints=[{"name": "a_floor", "source": {"target": "a"}, "minimum": 1.5}])
    spec["variables"][0]["nominal"] = 2.0  # a feasible start; the penalized optimum is a = 1.25
    result = fake(spec, tmp_path / "out", method="trf")
    assert result["termination"]["reason"] == "CONVERGED_INFEASIBLE"
    assert result["status"] == "STOPPED" and optimize.EXIT_CODES[result["status"]] == 2
    assert result["termination"]["solver_x"]["a"] < 1.5  # just outside the floor
    assert result["termination"]["solver_index"] is not None
    assert result["best"]["candidate"]["a"] >= 1.5  # the best is feasible
    loaded = optimize.load_spec(spec, edit=_set)
    adapter = ea.Adapter(loaded["problem"], session=QuadraticSession(), edit=_set)
    outcome = solvers.solve(adapter, solvers.Settings(method="slsqp", max_evaluations=80))
    assert outcome.reason == "CONVERGED" and outcome.solver_record.result.feasible is True


def test_overwrite_never_replaces_the_input_spec_or_project(fake, tmp_path):
    out = tmp_path / "run"
    out.mkdir()
    (out / "spec.json").write_text(json.dumps(_spec()), encoding="utf-8")
    before = (out / "spec.json").read_bytes()
    with pytest.raises(wc.WorkflowError) as refused:
        fake(str(out / "spec.json"), out, overwrite=True)
    assert refused.value.code == "OUTPUT_IS_INPUT" and (out / "spec.json").read_bytes() == before
    (tmp_path / "best-project.json").write_text(json.dumps(PROJECT), encoding="utf-8")
    (tmp_path / "s.json").write_text(json.dumps(_spec(project="best-project.json")), encoding="utf-8")
    with pytest.raises(wc.WorkflowError) as project:
        fake(str(tmp_path / "s.json"), tmp_path, overwrite=True)
    assert project.value.code == "OUTPUT_IS_INPUT" and (tmp_path / "best-project.json").is_file()


@scipy_needed
def test_infeasible_problem_has_no_best_and_keeps_every_margin(fake, tmp_path):
    out = tmp_path / "out"
    result = fake(_spec(constraints=[{"name": "a_above_10", "source": {"target": "a"}, "minimum": 10.0}]), out)
    assert result["status"] == "NO_FEASIBLE" and result["best"] is None and result["verification"] is None
    assert not (out / "best-project.json").exists() and result["files"]["best_project"] is None
    lines = _history(out)
    assert len(lines) == result["counts"]["evaluations"] and result["feasible_count"] == 0
    assert all(line["constraints"]["a_above_10"]["margin"] < 0 for line in lines)
    assert result["infeasible_reasons"] == {"CONSTRAINT_VIOLATED": len(lines)}
    # the least violating candidate is named for the next decision: a at its upper bound
    assert result["closest"]["candidate"]["a"] == pytest.approx(5.0)
    assert optimize.EXIT_CODES[result["status"]] == 1


@scipy_needed
def test_the_budget_is_a_hard_cap_and_a_feasible_best_is_stopped(fake, tmp_path):
    result = fake(_spec(), tmp_path / "out", max_evaluations=4)
    assert result["status"] == "STOPPED" and result["termination"]["reason"] == "BUDGET_EXHAUSTED"
    assert result["counts"]["evaluations"] == 4 == len(_history(tmp_path / "out"))
    assert result["best"] is not None and optimize.EXIT_CODES["STOPPED"] == 2


@scipy_needed
def test_ctrl_c_keeps_the_files_and_exits_130(monkeypatch, tmp_path):
    monkeypatch.setattr(optimize, "_base_identity", lambda document: {"project_hash": "h"})

    class Interrupted(QuadraticSession):
        def evaluate(self, document, outputs=(), targets=None):
            if self.calls == 3:
                raise KeyboardInterrupt
            return super().evaluate(document, outputs, targets)

    out = tmp_path / "out"
    result = optimize.run(_spec(), out, session=Interrupted(), session_factory=QuadraticSession,
                          adapter_options={"edit": _set})
    assert result["status"] == "INTERRUPTED" and result["termination"]["reason"] == "INTERRUPTED"
    assert len(_history(out)) == 3 and (out / "result.json").is_file() and result["verification"] is None
    assert optimize.EXIT_CODES["INTERRUPTED"] == 130


def test_cached_candidates_cost_nothing_and_are_counted_once():
    problem = optimize.load_spec(_spec(), edit=_set)["problem"]
    session = QuadraticSession()
    seen = []
    evaluator = solvers.Evaluator(ea.Adapter(problem, session=session, edit=_set), 2, seen.append)
    first = evaluator([0.5, 0.5], "start")
    assert evaluator([0.5, 0.5], "step") is first and evaluator.cache_hits == 1 and session.calls == 1
    evaluator([0.25, 0.5], "jacobian")
    with pytest.raises(solvers.BudgetExhausted):
        evaluator([0.0, 0.0], "step")
    assert [r.index for r in seen] == [0, 1] and [r.role for r in seen] == ["start", "jacobian"]
    assert session.calls == 2


def test_finite_difference_probes_stay_inside_the_bounds():
    space = solvers._Space([0.0, 4.9995], [1.0, 1.0], [(-5.0, 5.0), (-5.0, 5.0)])
    probes = solvers._probes([0.0, 4.9995], space, 1e-3, "central")
    assert probes[0][2] == pytest.approx(2e-3)  # central where both sides fit
    up, down, h = probes[1]
    assert h == pytest.approx(1e-3) and up[1] == 4.9995 and down[1] == pytest.approx(4.9985)  # backward at the bound
    assert all(-5.0 <= v <= 5.0 for v in space.x([10.0, -10.0]))


def test_solvers_are_deterministic(fake, tmp_path):
    pytest.importorskip("scipy")
    one = fake(_spec(), tmp_path / "one")
    two = fake(_spec(), tmp_path / "two")
    strip = [{k: v for k, v in line.items() if k != "seconds"} for line in _history(tmp_path / "one")]
    assert strip == [{k: v for k, v in line.items() if k != "seconds"} for line in _history(tmp_path / "two")]
    assert one["best"]["candidate"] == two["best"]["candidate"]


def test_settings_are_validated():
    for bad in ({"method": "nelder"}, {"max_evaluations": 0}, {"diff_step": -1.0}, {"difference": "complex"},
                {"x_scale": "unit"}, {"ftol": float("nan")}):
        with pytest.raises(solvers.SettingsError):
            solvers.Settings(**bad)


# --------------------------------------------------------------------------------------------- files

@scipy_needed
def test_history_result_and_spec_shapes(fake, tmp_path):
    out = tmp_path / "out"
    result = fake(_spec(verify={"ops": [{"op": "set_x", "name": "b", "value": -2.5}]}), out)
    assert result["schema"] == "os-optics-kit.optimize" and result["schema_version"] == 1
    for key in ("status", "termination", "counts", "best", "start", "closest", "feasible_count", "infeasible_reasons",
                "verification", "identity", "files", "settings"):
        assert key in result
    assert set(result["counts"]) >= {"evaluations", "cache_hits", "iterations", "seconds", "by_role"}
    assert result["counts"]["by_role"]["start"] == 1 and result["counts"]["by_role"]["jacobian"] > 0
    assert result["start"]["candidate"] == {"a": 0.0, "b": 0.0} and result["start"]["role"] == "start"
    line = _history(out)[0]
    assert set(line) >= {"index", "role", "candidate", "objective", "residual_objective", "feasible", "constraints",
                         "reason", "seconds", "cache_hit"}
    assert line["cache_hit"] is False
    dense = result["verification"]["dense"]
    assert dense["feasibility_changed"] is False and dense["valid"] is True
    change = next(c for c in dense["changes"] if c["quantity"] == "objective:b_to_m2")
    assert change["after"] == pytest.approx(-2.5) and change["difference"] == pytest.approx(-0.5, abs=1e-6)
    # spec.json is the spec as run, with the Project inlined: it runs again by itself
    again = optimize.load_spec(out / "spec.json", edit=_set)
    assert again["spec"]["variables"] == json.loads((out / "spec.json").read_text())["variables"]
    assert json.loads((out / "result.json").read_text()) == json.loads(json.dumps(wc.json_safe(result)))


@scipy_needed
def test_the_output_directory_is_new_or_empty(fake, tmp_path):
    out = tmp_path / "out"
    fake(_spec(), out)
    with pytest.raises(wc.WorkflowError) as caught:
        fake(_spec(), out)
    assert caught.value.code == "OUTPUT_EXISTS"
    # a rerun with --overwrite that finds nothing feasible leaves no stale best Project behind
    fake(_spec(constraints=[{"name": "a_above_10", "source": {"target": "a"}, "minimum": 10.0}]), out,
         overwrite=True)
    assert not (out / "best-project.json").exists()


# --------------------------------------------------------------------------------------------- spec refusals

@pytest.mark.parametrize("change, path", [
    ({"unknown_key": 1}, "spec"),
    ({"variables": []}, "variables"),
    ({"variables": [{"name": "a", "ops": [{"op": "set_x", "name": "a", "value": "$value"}], "bounds": [1, 1]}]},
     "variables[0].bounds"),
    ({"variables": [{"name": "a", "ops": [{"op": "set_x", "name": "a", "value": "$value"}], "bounds": [1, 2],
                     "nominal": 0.0}]}, "variables[0].nominal"),
    ({"variables": [{"name": "a", "ops": [{"op": "set_x", "name": "a", "value": "$value"}], "bounds": [0, 2],
                     "scale": 0}]}, "variables[0].scale"),
    ({"objectives": [{"name": "m", "source": {"mtf": {"observation": "p", "frequency_cyc_per_mm": 10}}}]},
     "problem"),
    ({"objectives": [{"name": "x", "source": {"paraxial": {"components": ["L1"], "wavelength_nm": 500,
                                                           "quantity": "power"}}}]}, "problem"),
    ({"aggregation": "max_abs"}, "solver.method"),
    ({"solver": {"method": "trf", "max_evaluations": 0}}, "solver"),
    ({"solver": {"step": 1}}, "solver"),
    ({"verify": {"ops": [{"op": "nope"}]}}, "verify.ops"),
    ({"verify": {"tolerances": {"relative": -1}}}, "verify.tolerances.relative"),
    ({"schema_version": 2}, "schema"),
    ({"project": 3}, "project"),
])
def test_invalid_specs_are_refused_before_any_evaluation(change, path):
    with pytest.raises(wc.WorkflowError) as caught:
        optimize.load_spec(_spec(**change), edit=_set)
    assert caught.value.code == "SPEC_INVALID" and caught.value.details["path"] == path


def test_a_configurations_project_needs_a_configuration():
    project = {**copy.deepcopy(PROJECT), "configurations": [{"configuration_id": "wide"}, {"configuration_id": "tele"}]}
    with pytest.raises(wc.WorkflowError) as caught:
        optimize.load_spec(_spec(project=project), edit=_set)
    assert caught.value.code == "CONFIGURATION_REQUIRED" and caught.value.details["configurations"] == ["wide", "tele"]


def test_help_runs_without_the_engine(capsys):
    assert optimize.main(["--help"]) == 0
    text = capsys.readouterr().out
    assert text.startswith("usage: os-optics-kit optimize") and "NO_FEASIBLE 1" in text and "slsqp" in text


# --------------------------------------------------------------------------------------------- adapter sources

class SourcesSession:
    """One evaluation with a solid report, arrivals with pupil counts and imaging analyses by request."""

    def __init__(self, solids=None, arrivals=(), paths=(), analysis=None):
        self.solids, self.arrivals, self.paths, self.analysis = solids, arrivals, paths, analysis
        self.requests, self.outputs = [], []

    def evaluate(self, document, outputs=(), targets=None):
        self.outputs.append(tuple(outputs))
        observations = [SimpleNamespace(observation_id="image", arrivals=list(self.arrivals))]

        def imaging(observation, request):
            self.requests.append(copy.deepcopy(request))
            return self.analysis

        return SimpleNamespace(
            result=SimpleNamespace(targets=[_spot(a) for a in self.arrivals], evaluation=_scope(),
                                   observations=observations, paths=list(self.paths)),
            identity={}, model_scope_issues=(), optical_solids=self.solids,
            results=lambda: SimpleNamespace(imaging=imaging))


def _spot(arrival):
    t = _target(f"spot_{arrival.beam.source_id}", 0.01)
    t.metric = "spot_rms_mm"
    return t


def _arrival(source_id, launched, arrived, wavelength=587.5618):
    return SimpleNamespace(beam=SimpleNamespace(source_id=source_id, wavelength_nm=wavelength, path_id=f"{source_id}#0"),
                           crossing_index=0, pupil_ray_count=launched, pupil_rays_arrived=arrived)


class _Report:
    def __init__(self, document):
        self.document = document

    def as_dict(self):
        return self.document


SOLIDS = _Report({"status": "VALID", "elements": [
    {"instance_id": "AS", "member_id": "STOP"},
    {"instance_id": "AS", "member_id": "L1", "gaps": [{"from_surface": "S1", "to_surface": "S2", "medium": "N-BK7",
                                                        "min_thickness_mm": 9.77, "edge_thickness_mm": 10.16}]},
    {"instance_id": "AS", "member_id": "L4", "gaps": [{"from_surface": "S1", "to_surface": "S2", "medium": "N-BK7",
                                                        "min_thickness_mm": 3.28, "edge_thickness_mm": 3.28}]},
    {"instance_id": "W", "member_id": None, "gaps": [{"from_surface": "S1", "to_surface": "S2", "medium": "F2",
                                                       "min_thickness_mm": 1.0}]}]})


def _sources_problem(*constraints, objectives=(), **kw):
    return ea.Problem(project={"targets": kw.pop("targets", [])}, variables=[], objectives=list(objectives),
                      constraints=list(constraints), **kw)


def test_solids_source_takes_the_smallest_selected_gap_and_names_it():
    problem = _sources_problem(ea.Constraint("thick", {"solids": {"component": "AS", "quantity": "min_thickness_mm"}},
                                             minimum=3.0, scale=0.5),
                               ea.Constraint("l1", {"solids": {"component": "AS", "member": "L1",
                                                               "quantity": "edge_thickness_mm"}}, minimum=3.0))
    session = SourcesSession(solids=SOLIDS)
    r = ea.Adapter(problem, session=session, edit=lambda d, ops: d).evaluate({})
    assert r.valid and r.feasible is True and session.outputs == [("solid_check",)]
    thick, l1 = r.constraints
    assert thick["value"] == 3.28 and thick["margin"] == pytest.approx(0.56)
    assert thick["binding"]["member_id"] == "L4" and thick["binding"]["from_surface"] == "S1"
    assert l1["value"] == 10.16 and l1["value_kind"] == "EXACT"


def test_solids_without_a_matching_gap_or_report_is_unavailable():
    problem = _sources_problem(ea.Constraint("t", {"solids": {"component": "NONE", "quantity": "min_thickness_mm"}},
                                             minimum=1.0))
    for solids in (SOLIDS, None):
        r = ea.Adapter(problem, session=SourcesSession(solids=solids), edit=lambda d, ops: d).evaluate({})
        assert not r.valid and r.reason["code"] == "QUANTITY_UNAVAILABLE"


def test_paraxial_source_reads_one_analysis_per_request():
    calls = []

    def paraxial(document, request):
        calls.append(request)
        return {"system": {"efl_mm": 50.1, "bfl_mm": 13.0, "input_plane": "in", "output_plane": "out"}}

    spec = {"components": ["AS"], "wavelength_nm": 587.5618}
    problem = _sources_problem(ea.Constraint("efl", {"paraxial": {**spec, "quantity": "efl_mm"}},
                                             minimum=49.75, maximum=50.25, scale=0.25),
                               objectives=[ea.Objective("bfl", {"paraxial": {**spec, "quantity": "bfl_mm"}}, goal=13.5)])
    session = SourcesSession()
    r = ea.Adapter(problem, session=session, edit=lambda d, ops: d, paraxial=paraxial).evaluate({})
    assert r.valid and len(calls) == 1 and calls[0] == spec and session.outputs == [()]
    assert r.constraints[0]["value"] == 50.1 and r.constraints[0]["margin"] == pytest.approx(0.6)
    assert r.objectives[0]["scaled_error"] == pytest.approx(-0.5) and "paraxial_analysis" in r.objectives[0]["basis_note"]


def test_a_refused_or_afocal_paraxial_analysis_is_a_reason():
    def refuse(document, request):
        raise ValueError("no component 'AS' in the Project")

    spec = {"components": ["AS"], "wavelength_nm": 587.5618, "quantity": "efl_mm"}
    problem = _sources_problem(ea.Constraint("efl", {"paraxial": spec}, minimum=49.0))
    r = ea.Adapter(problem, session=SourcesSession(), edit=lambda d, ops: d, paraxial=refuse).evaluate({})
    assert not r.valid and r.reason["code"] == "ANALYSIS_REFUSED" and r.reason["stage"] == "paraxial"
    afocal = ea.Adapter(problem, session=SourcesSession(), edit=lambda d, ops: d,
                        paraxial=lambda d, q: {"system": {"efl_mm": None}}).evaluate({})
    assert afocal.reason["code"] == "QUANTITY_UNAVAILABLE"


@pytest.mark.parametrize("policy, feasible, infeasible", [("infeasible", False, ["RAYS_LOST"]), ("report", True, [])])
def test_lost_pupil_rays_follow_the_ray_loss_policy(policy, feasible, infeasible):
    targets = [{"target_id": f"spot_{s}", "metric": "spot_rms_mm", "observation_id": "image", "source_id": s,
                "wavelength_nm": 587.5618, "parameters": []} for s in ("ON", "OFF")]
    problem = _sources_problem(ea.Constraint("off", {"target": "spot_OFF"}, maximum=0.05),
                               objectives=[ea.Objective("on", {"target": "spot_ON"}, scale=0.01)],
                               targets=targets, ray_loss=policy)
    session = SourcesSession(arrivals=[_arrival("ON", 96, 96), _arrival("OFF", 96, 89)])
    r = ea.Adapter(problem, session=session, edit=lambda d, ops: d).evaluate({})
    assert r.valid and r.feasible is feasible and r.infeasible == infeasible
    assert r.ray_loss == [{"name": "off", "target": "spot_OFF", "pupil_rays_launched": 96, "pupil_rays_arrived": 89,
                           "pupil_rays_lost": 7}]
    assert r.residuals == [1.0, 0.0]  # the residuals are not changed by the policy
    assert r.constraints[0]["pupil_rays_arrived"] == 89 and r.objectives[0]["pupil_rays_launched"] == 96


def test_rays_source_bounds_the_share_of_arriving_pupil_rays():
    targets = [{"target_id": "spot_OFF", "metric": "spot_rms_mm", "observation_id": "image", "source_id": "OFF",
                "parameters": []}, {"target_id": "power", "metric": "power_W", "observation_id": "image"}]
    problem = _sources_problem(ea.Constraint("kept", {"rays": {"target": "spot_OFF"}}, minimum=0.9, scale=0.01),
                               targets=targets)
    session = SourcesSession(arrivals=[_arrival("OFF", 96, 86)])
    r = ea.Adapter(problem, session=session, edit=lambda d, ops: d).evaluate({})
    kept = r.constraints[0]
    assert kept["value"] == pytest.approx(86 / 96) and kept["margin"] == pytest.approx((86 / 96 - 0.9) / 0.01)
    assert r.feasible is False and r.infeasible == ["CONSTRAINT_VIOLATED"] and r.ray_loss == []
    assert session.outputs == [("plane_readings",)]
    with pytest.raises(ea.ProblemError, match="pupil-ray metric"):
        _sources_problem(ea.Constraint("p", {"rays": {"target": "power"}}, minimum=0.9), targets=targets)


FREQS = [0.0, 20.0]


def _analysis(lines):
    quantities, arrays = [], {}
    for source, wavelength, value in lines:
        qid = f"{source}#0.mtf_tangential"
        quantities.append(SimpleNamespace(
            quantity_id=qid, state=SimpleNamespace(value="KNOWN"), value_basis=None, values_ref=qid, convergence=None,
            reason=None, message="", scope=SimpleNamespace(observation_id="image", source_id=source, field_id=None,
                                                           wavelength_nm=wavelength)))
        arrays[f"{source}#0.frequencies_cyc_per_mm"] = FREQS
        arrays[qid] = [1.0, value]
    return SimpleNamespace(quantities=quantities, arrays=arrays)


def _mtf_constraint(name, **spec):
    return ea.Constraint(name, {"mtf": {"observation": "image", "frequency_cyc_per_mm": 20, **spec}}, minimum=0.3)


PATHS = [SimpleNamespace(source_id=s, wavelength_nm=w) for s in ("F0", "F1", "F2") for w in (486.1327, 587.5618)]


def test_mtf_requests_name_only_the_fields_and_lines_every_constraint_selects():
    analysis = _analysis([("F1", 587.5618, 0.6), ("F2", 587.5618, 0.4)])
    problem = _sources_problem(_mtf_constraint("f1", source_id="F1", wavelength_nm=587.56),
                               _mtf_constraint("f2", source_id="F2", wavelength_nm=587.5618))
    session = SourcesSession(paths=PATHS, analysis=analysis)
    r = ea.Adapter(problem, session=session, edit=lambda d, ops: d).evaluate({})
    assert r.valid and [c["value"] for c in r.constraints] == [0.6, 0.4]
    request = session.requests[0]
    # the Engine's own line wavelength (it matches within 1e-6 nm), not the selector's 587.56
    assert request["fields"] == ["F1", "F2"] and request["wavelengths_nm"] == [587.5618]
    assert request["frequencies_cyc_per_mm"] == FREQS and r.constraints[0]["imaging_request"] == request


def test_mtf_requests_stay_whole_when_one_constraint_selects_everything():
    analysis = _analysis([("F1", 587.5618, 0.6), ("F2", 587.5618, 0.4)])
    problem = _sources_problem(_mtf_constraint("f1", source_id="F1"), _mtf_constraint("all"))
    session = SourcesSession(paths=PATHS, analysis=analysis)
    ea.Adapter(problem, session=session, edit=lambda d, ops: d).evaluate({})
    assert "fields" not in session.requests[0] and "wavelengths_nm" not in session.requests[0]
    stated = _sources_problem(_mtf_constraint("f1", source_id="F1"), imaging={"fields": ["F0", "F1"]})
    session = SourcesSession(paths=PATHS, analysis=analysis)
    ea.Adapter(stated, session=session, edit=lambda d, ops: d).evaluate({})
    assert session.requests[0]["fields"] == ["F0", "F1"]  # a request that states its fields is kept


def test_new_sources_are_validated_when_the_problem_is_built():
    for source, match in (({"paraxial": {"components": [], "wavelength_nm": 500, "quantity": "efl_mm"}}, "components"),
                          ({"paraxial": {"components": ["L"], "wavelength_nm": 0, "quantity": "efl_mm"}}, "wavelength"),
                          ({"paraxial": {"components": ["L"], "wavelength_nm": 500, "quantity": "efl_mm", "x": 1}},
                           "keys"),
                          ({"solids": {"quantity": "volume_mm3"}}, "quantity"),
                          ({"solids": {}}, "needs quantity"),
                          ({"power": {}}, "source is")):
        with pytest.raises(ea.ProblemError, match=match):
            _sources_problem(ea.Constraint("c", source, minimum=0.0))
    with pytest.raises(ea.ProblemError, match="ray_loss"):
        _sources_problem(ea.Constraint("c", {"solids": {"quantity": "min_thickness_mm"}}, minimum=0.0),
                         ray_loss="ignore")


# --------------------------------------------------------------------------------------------- set_optical_model_value

MODELS = {"optical_models": [{"model_id": "L", "model": {"surfaces": [
    {"radius_mm": "31.3825470238", "conic": -1.5, "aspheric_coefficients": ["0.00000206374318835"], "traced": "T"}]}}]}


def test_set_optical_model_value_replaces_one_member_and_keeps_strings_strings():
    op = engine_op({"op": "set_optical_model_value", "model_id": "L", "path": "/surfaces/0/radius_mm", "value": 32.0},
                   MODELS, 0)
    assert op["op"] == "update_optical_model" and op["model_id"] == "L"
    surface = op["changes"]["model"]["surfaces"][0]
    assert surface["radius_mm"] == "32.0" and surface["conic"] == -1.5 and surface["traced"] == "T"
    assert MODELS["optical_models"][0]["model"]["surfaces"][0]["radius_mm"] == "31.3825470238"  # not modified
    conic = engine_op({"op": "set_optical_model_value", "model_id": "L", "path": "/surfaces/0/conic", "value": -2},
                      MODELS, 0)["changes"]["model"]["surfaces"][0]["conic"]
    assert conic == -2.0 and isinstance(conic, float)
    assert model_value(MODELS, "L", "/surfaces/0/aspheric_coefficients/0") == pytest.approx(2.06374318835e-6)


@pytest.mark.parametrize("op, code", [
    ({"model_id": "M", "path": "/surfaces/0/radius_mm", "value": 1.0}, "NOT_FOUND"),
    ({"model_id": "L", "path": "/surfaces/3/radius_mm", "value": 1.0}, "NOT_FOUND"),
    ({"model_id": "L", "path": "/surfaces/0/traced", "value": 1.0}, "BAD_ARGUMENT"),
])
def test_set_optical_model_value_refusals(op, code):
    with pytest.raises(EditError) as caught:
        engine_op({"op": "set_optical_model_value", **op}, MODELS, 2)
    assert caught.value.code == "PROJECT_OP_INVALID" and caught.value.details["op_code"] == code
    assert caught.value.details["op_index"] == 2


@pytest.mark.parametrize("op, code", [
    ({"model_id": "L", "path": "surfaces/0", "value": 1.0}, "BAD_ARGUMENT"),
    ({"model_id": "L", "path": "/surfaces/0/radius_mm"}, "BAD_ARGUMENT"),
    ({"model_id": "L", "path": "/surfaces/0/radius_mm", "value": "1"}, "BAD_ARGUMENT"),
    ({"model_id": "L", "path": "/surfaces/0/radius_mm", "value": {"value": 1, "unit": "deg"}}, "UNIT_INCOMPATIBLE"),
    ({"model_id": "L", "path": "/surfaces/0/conic", "value": {"value": 1, "unit": "mm"}}, "UNIT_INCOMPATIBLE"),
    ({"model_id": "L", "path": "/surfaces/0/radius_mm", "value": 1.0, "extra": 1}, "BAD_ARGUMENT"),
])
def test_set_optical_model_value_arguments_are_checked(op, code):
    with pytest.raises(EditError) as caught:
        _normalize_model_value({"op": "set_optical_model_value", **op}, 0)
    assert caught.value.details["op_code"] == code


def test_set_optical_model_value_converts_a_stated_unit():
    op = _normalize_model_value({"op": "set_optical_model_value", "model_id": "L", "path": "/surfaces/0/radius_mm",
                                 "value": {"value": 3.2, "unit": "m"}}, 0)
    assert op["value"] == pytest.approx(3200.0)


# --------------------------------------------------------------------------------------------- Engine

def _case(name):
    return json.loads((ROOT / "cases" / name / "project.json").read_text(encoding="utf-8"))


EXPANDER = ROOT / "cases" / "interop-optimization" / "studies"


def _reference_separation():
    """The independent ABCD optimum of the case script (numpy only, no Engine)."""
    spec = importlib.util.spec_from_file_location("optimize_expander",
                                                  ROOT / "cases" / "interop-optimization" / "optimize_expander.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from scipy.optimize import minimize_scalar

    return minimize_scalar(module.reference_divergence, bounds=module.BOUNDS_MM, method="bounded",
                           options={"xatol": 1e-10}).x


@engine
@pytest.mark.parametrize("method", ["trf", "slsqp"])
def test_engine_expander_reproduces_the_abcd_optimum(tmp_path, method):
    from tests.workflow_fixtures import invoke

    out = tmp_path / method
    code, stdout, stderr = invoke(optimize.main, [str(EXPANDER / "optimize-expander.json"), "--out", str(out),
                                                  "--method", method])
    assert code == 0 and stdout["status"] == "CONVERGED", stderr
    result = json.loads((out / "result.json").read_text(encoding="utf-8"))
    assert result["best"]["candidate"]["separation"] == pytest.approx(_reference_separation(), abs=1e-5)
    assert result["identity"]["start_reproduces_base"] is True
    assert result["verification"]["reproduction"]["status"] == "REPRODUCED"
    assert result["verification"]["reproduction"]["max_abs_difference"] == 0.0
    best = json.loads((out / "best-project.json").read_text(encoding="utf-8"))
    l2 = next(c for c in best["components"] if c["instance_id"] == "L2")
    assert l2["nominal_pose"]["position_mm"][0] == pytest.approx(100.0 + result["best"]["candidate"]["separation"])


@engine
def test_engine_expander_infeasible_divergence_is_no_feasible(tmp_path):
    from tests.workflow_fixtures import invoke

    out = tmp_path / "strict"
    code, stdout, _ = invoke(optimize.main, [str(EXPANDER / "optimize-expander-infeasible.json"), "--out", str(out)])
    assert code == 1 and stdout["status"] == "NO_FEASIBLE" and stdout["best"] is None
    assert not (out / "best-project.json").exists()
    lines = _history(out)
    assert lines and all(line["valid"] and line["feasible"] is False for line in lines)
    assert all(line["constraints"]["collimated"]["margin"] < 0 for line in lines)
    assert stdout["closest"]["candidate"]["separation"] == pytest.approx(_reference_separation(), abs=1e-3)


@engine
def test_engine_set_optical_model_value_edits_and_reads_the_nominal():
    from os_optics_kit.edit_project import apply_edits
    from os_optics_kit.sensitivity_analysis import base_nominal

    project = _case("aspheric-lens")
    ops = [{"op": "set_optical_model_value", "model_id": "AS-L1", "path": "/surfaces/0/radius_mm", "value": "$value"}]
    assert base_nominal(project, {"name": "r", "unit": None, "ops": ops}) == pytest.approx(31.3825470238)
    assert base_nominal(project, {"name": "r", "unit": "um", "ops": ops}) == pytest.approx(31382.5470238)
    edited = apply_edits(project, [{**ops[0], "value": 31.5},
                                   {**ops[0], "path": "/surfaces/1/radius_mm", "value": 48.0}])
    assert edited["record"]["normalized_ops"][0]["op"] == "set_optical_model_value"
    surfaces = next(m for m in edited["project"]["optical_models"] if m["model_id"] == "AS-L1")["model"]["surfaces"]
    assert float(surfaces[0]["radius_mm"]) == 31.5 and float(surfaces[1]["radius_mm"]) == 48.0
    assert edited["record"]["result"]["project_hash"] != edited["record"]["base"]["project_hash"]


@engine
def test_engine_paraxial_and_solids_sources_on_the_aspheric_lens():
    problem = ea.Problem(project=_case("aspheric-lens"), variables=[], constraints=[
        ea.Constraint("efl", {"paraxial": {"components": ["AS"], "wavelength_nm": 587.5618, "quantity": "efl_mm"}},
                      minimum=49.75, maximum=50.25, scale=0.25),
        ea.Constraint("thickness", {"solids": {"component": "AS", "quantity": "min_thickness_mm"}}, minimum=3.0)])
    r = ea.Adapter(problem).evaluate({})
    assert r.valid and r.feasible is True
    efl, thickness = r.constraints
    assert efl["value"] == pytest.approx(49.996743, abs=1e-5)
    assert thickness["value"] == pytest.approx(3.277361, abs=1e-5) and thickness["binding"]["member_id"] == "L4"


@engine
def test_engine_vignetted_field_loses_pupil_rays():
    """aspheric-lens at 25 deg: 89 of 96 pupil rays reach the image plane at 587.56 nm (the RMS is over those)."""
    problem = ea.Problem(project=_case("aspheric-lens"), variables=[],
                         objectives=[ea.Objective("spot", {"target": "spot_rms_f25deg_587.562nm"}, scale=0.01)])
    r = ea.Adapter(problem).evaluate({})
    assert r.valid and r.feasible is False and r.infeasible == ["RAYS_LOST"]
    assert r.ray_loss[0]["pupil_rays_launched"] == 96 and r.ray_loss[0]["pupil_rays_arrived"] == 89


@engine
def test_engine_narrowed_mtf_request_gives_the_full_requests_numbers():
    """aspheric-lens at 587.56 nm, on axis and at 25 deg: the request the adapter narrows to the 25 deg field and line
    gives that line exactly the curves the whole request gives it."""
    from os_optics.local import EvaluationSession

    from os_optics_kit import imaging_results

    project = _case("aspheric-lens")
    project["targets"] = []
    project["components"] = [c for c in project["components"] if c["instance_id"] in ("AS", "SRC0_1", "SRC2_1")]
    session = EvaluationSession()
    ev = session.evaluate(project, outputs=("plane_readings",))
    spec = {"observation": "image_plane", "frequency_cyc_per_mm": 20, "source_id": "SRC2_1", "wavelength_nm": 587.56}
    problem = ea.Problem(project=project, variables=[], constraints=[ea.Constraint("mtf", {"mtf": spec}, minimum=0.0)],
                         imaging={"launch_grid": 16, "convergence": False, "include_psf_map": False})
    narrowed = ea.Adapter(problem, session=session)._imaging_request(ev, "image_plane")
    assert narrowed["fields"] == ["SRC2_1"] and narrowed["wavelengths_nm"] == [587.5618]
    whole = {k: v for k, v in narrowed.items() if k not in ("fields", "wavelengths_nm")}
    small = imaging_results.load(ev.results().imaging("image_plane", narrowed))
    full = imaging_results.load(ev.results().imaging("image_plane", whole))
    assert [line["line_id"] for line in imaging_results.lines(small)] == ["SRC2_1#0"]
    assert len(imaging_results.lines(full)) == 2
    assert imaging_results.curves(small, line_ids=["SRC2_1#0"]) == imaging_results.curves(full, line_ids=["SRC2_1#0"])
    for name in ("geometric_spot_rms_radius", "strehl_ratio"):
        assert imaging_results.scalar(small, "SRC2_1#0", name) == imaging_results.scalar(full, "SRC2_1#0", name)
