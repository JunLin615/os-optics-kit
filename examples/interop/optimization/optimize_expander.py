"""External optimization and tolerance loop over an OpenSci Project (requires os-optics 0.10.0).

A catalog-free Keplerian beam expander (two declared ideal thin lenses, f = 50 mm
and 150 mm, 532 nm, 0.5 mm source waist) is collimated by moving lens L2. SciPy
and Optuna choose L2's position; each evaluation edits the Project by
``instance_id``, runs the Engine in memory and reads the target metric
``divergence_half_angle_x_rad`` from the run's results. SALib then estimates
first-order and total Sobol indices of the divergence for mounting errors of L1,
L2 and the source waist. An independent ABCD Gaussian-beam calculation (numpy
only, no Engine data) gives the reference optimum and the reference value of every
evaluated design.

Every evaluation is logged with its run identity, execution outcome, selected quantity
and target assessment in ``evaluations.jsonl``. Searches penalize unavailable objectives
and unmet requirements; Sobol analysis requires a complete set of valid objective values.
The optimizer chooses the
values; OpenSci computes each design. Project ``retained_dofs`` are design intent,
not optimizer variables.

Usage: python examples/interop/optimization/optimize_expander.py --out run-interop-optimization
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import sys
import time

if __package__:
    from ..common import ArgumentParser, comparison_status, decimal_text, emit, environment, require_engine, run_identity, write_json
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from common import ArgumentParser, comparison_status, decimal_text, emit, environment, require_engine, run_identity, write_json

WAVELENGTH_NM = 532.0
WAIST_MM = 0.5
F1_MM, F2_MM = 50.0, 150.0
L1_X_MM = 100.0
OUTPUT_X_MM = 700.0
BOUNDS_MM = (150.0, 250.0)  # L1-L2 separation searched by the optimizers
PLUS_X = [0.7071067811865476, 0.0, 0.7071067811865476, 0.0]  # local +Z along design +X
METRIC = "divergence_half_angle_x_rad"
# A finite penalty guides the search; the returned optimum is selected only from
# recorded evaluations with a valid objective and a met requirement.
INFEASIBLE_PENALTY_RAD = 1.0
# Tolerance study: uniform mounting errors about the optimum (mm) and a relative waist error.
TOLERANCES = {"L1_x_mm": 0.5, "L2_x_mm": 0.5, "waist_rel": 0.05}


def ideal_lens(model_id, focal_length_mm):
    return {"model_id": model_id, "model": {
        "model_kind": "IDEAL_THIN_LENS", "model_version": "1", "focal_length_mm": decimal_text(focal_length_mm),
        "clear_radius_mm": "12", "edge_radius_mm": "12.7", "status": "SPECIFIED",
        "valid_band_nm": {"min_nm": "500", "max_nm": "600", "status": "SPECIFIED"},
    }}


def lens_component(instance_id, model_id, x_mm):
    return {"instance_id": instance_id, "optical_role": "lens", "optical_model_id": model_id,
            "custom_part": {"custom_part_key": f"own-{instance_id.lower()}", "description": "customer lens"},
            "nominal_pose": {"position_mm": [x_mm, 0.0, 50.0], "quaternion_wxyz": PLUS_X}}


def base_project():
    """The baseline Project document (catalog-free; no download needed)."""
    return {
        "schema": "opensci.project", "project_schema_version": "2.0", "design_intent": "OPTICAL_SYSTEM",
        "optical_models": [ideal_lens("f50", F1_MM), ideal_lens("f150", F2_MM)],
        "components": [
            {"instance_id": "SRC", "optical_role": "source",
             "custom_part": {"custom_part_key": "user-laser", "description": "532 nm laser"},
             "nominal_pose": {"position_mm": [0.0, 0.0, 50.0], "quaternion_wxyz": PLUS_X},
             "source_model": {"model_kind": "GAUSSIAN_BEAM", "wavelength_nm": WAVELENGTH_NM, "power_w": 0.001,
                              "waist_radius_mm": WAIST_MM, "waist_position_mm": 0.0, "m2": 1.0,
                              "polarization": {"kind": "LINEAR", "angle_deg": 0.0}}},
            lens_component("L1", "f50", L1_X_MM),
            lens_component("L2", "f150", L1_X_MM + 200.0),
        ],
        "observations": [{"observation_id": "output", "aperture": {"shape": "circle", "radius_mm": 20},
                          "pose": {"position_mm": [OUTPUT_X_MM, 0.0, 50.0], "quaternion_wxyz": PLUS_X}}],
        "targets": [{"target_id": "collimated", "metric": METRIC, "observation_id": "output",
                     "maximum": 1e-3, "unit": "rad"}],
    }


def by_id(rows, field, identifier):
    matches = [row for row in rows if row[field] == identifier]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one {field}={identifier}")
    return matches[0]


def edited_project(separation_mm, *, l1_shift_mm=0.0, waist_mm=WAIST_MM):
    """Copy the baseline and edit fields by id: L1 x, L2 x (L1 + separation), source waist."""
    document = copy.deepcopy(base_project())
    by_id(document["components"], "instance_id", "L1")["nominal_pose"]["position_mm"][0] = L1_X_MM + l1_shift_mm
    by_id(document["components"], "instance_id", "L2")["nominal_pose"]["position_mm"][0] = L1_X_MM + separation_mm
    by_id(document["components"], "instance_id", "SRC")["source_model"]["waist_radius_mm"] = waist_mm
    return document


def reference_divergence(separation_mm, *, l1_shift_mm=0.0, waist_mm=WAIST_MM):
    """Independent ABCD: q at L1 from the source waist, thin lens f1, gap, thin lens f2; far-field half angle w0'/zR'."""
    lam_mm = WAVELENGTH_NM * 1e-6
    q = complex(L1_X_MM + l1_shift_mm, math.pi * waist_mm ** 2 / lam_mm)
    gap = separation_mm - l1_shift_mm
    for a, b, c, d in ((1.0, 0.0, -1.0 / F1_MM, 1.0), (1.0, gap, 0.0, 1.0), (1.0, 0.0, -1.0 / F2_MM, 1.0)):
        q = (a * q + b) / (c * q + d)
    z_r = q.imag
    return math.sqrt(lam_mm * z_r / math.pi) / z_r


class EngineObjective:
    """Edit, run in memory, read the target metric; log every evaluation with its run identity."""

    def __init__(self, log_path, *, max_divergence_rad=1e-3, compute_budget=None, max_evaluations=1000):
        from os_optics.local import load_project, run_optical_project
        from os_optics.results import load_run

        self._load_project, self._run, self._load_run = load_project, run_optical_project, load_run
        self.log_path = Path(log_path)
        self.log_path.open("x", encoding="utf-8").close()
        self.count = 0
        self.max_divergence_rad = max_divergence_rad
        self.compute_budget = compute_budget
        self.max_evaluations = max_evaluations

    def __call__(self, separation_mm, *, l1_shift_mm=0.0, waist_mm=WAIST_MM, stage="search"):
        if self.count >= self.max_evaluations:
            raise ValueError("evaluation budget exhausted; evaluations.jsonl retains completed evaluations")
        started = time.perf_counter()
        document = edited_project(separation_mm, l1_shift_mm=l1_shift_mm, waist_mm=waist_mm)
        document["targets"][0]["maximum"] = self.max_divergence_rad
        if self.compute_budget:
            document["compute_budget"] = self.compute_budget
        self.count += 1
        inputs = {"L1_x_mm": L1_X_MM + l1_shift_mm, "L2_x_mm": L1_X_MM + separation_mm, "waist_radius_mm": waist_mm}
        try:
            results = self._load_run(self._run(self._load_project(document)))
            compact = results.targets()
            target = next(t for t in compact["targets"] if t["target_id"] == "collimated")
        except Exception as exc:
            record = {"evaluation": self.count, "stage": stage, "inputs": inputs,
                      "metric": METRIC, "value": None, "unit": "rad", "target_status": None,
                      "assessment": None, "objective_valid": False, "requirement_feasible": False,
                      "feasible": False, "execution": None, "run_status": "ERROR", "identity": None,
                      "error": {"code": getattr(exc, "code", type(exc).__name__), "message": str(exc)},
                      "seconds": round(time.perf_counter() - started, 4)}
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=True, allow_nan=False) + "\n")
            return INFEASIBLE_PENALTY_RAD
        statuses = results.describe()["status"]
        run_status = statuses.get("validation_status") or statuses["engine_status"]
        objective_valid = (target["status"] in ("PASS", "WARNING", "FAIL")
                           and not target["applicability_issue_codes"]
                           and target["value"] is not None and math.isfinite(target["value"]))
        feasible = objective_valid and target["assessment"] == "MET"
        value = target["value"] if objective_valid and (feasible or stage == "tolerance") else INFEASIBLE_PENALTY_RAD
        record = {
            "evaluation": self.count, "stage": stage,
            "inputs": inputs,
            "metric": METRIC, "value": target["value"], "unit": target["unit"], "target_status": target["status"],
            "assessment": target["assessment"], "objective_valid": objective_valid,
            "applicability_issue_codes": target["applicability_issue_codes"],
            "execution": compact["outcome"]["execution"], "requirement_feasible": feasible,
            "run_status": run_status, "feasible": feasible,
            "reference_value": reference_divergence(separation_mm, l1_shift_mm=l1_shift_mm, waist_mm=waist_mm),
            "seconds": round(time.perf_counter() - started, 4),
            "identity": run_identity(results),
        }
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True, allow_nan=False) + "\n")
        return value


def run_example(out_dir, *, optuna_trials=60, seed=1, sobol_n=128,
                max_divergence_rad=1e-3, compute_budget=None, max_evaluations=1000):
    installed = require_engine()
    import numpy as np
    import optuna
    from SALib.analyze import sobol
    from SALib.sample import sobol as sobol_sample
    from scipy.optimize import minimize_scalar

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if any((out_dir / name).exists() for name in ("project-baseline.json", "evaluations.jsonl", "report.json")):
        raise ValueError("output already contains a study; choose a new --out directory to preserve it")
    if optuna_trials < 1 or sobol_n < 1 or sobol_n & (sobol_n - 1):
        raise ValueError("optuna-trials must be positive; sobol-n must be a positive power of two")
    if not math.isfinite(max_divergence_rad) or max_divergence_rad < 0:
        raise ValueError("max-divergence-rad must be finite and nonnegative")
    if max_evaluations < optuna_trials + 5 * sobol_n + 50:
        raise ValueError("max-evaluations must cover optuna-trials + 5*sobol-n + 50 SciPy evaluations")
    baseline = base_project()
    baseline["targets"][0]["maximum"] = max_divergence_rad
    if compute_budget:
        baseline["compute_budget"] = compute_budget
    write_json(out_dir / "project-baseline.json", baseline)
    objective = EngineObjective(out_dir / "evaluations.jsonl", max_divergence_rad=max_divergence_rad,
                                compute_budget=compute_budget, max_evaluations=max_evaluations)

    reference = minimize_scalar(reference_divergence, bounds=BOUNDS_MM, method="bounded", options={"xatol": 1e-9})

    started = time.perf_counter()
    scipy_result = minimize_scalar(lambda d: objective(d, stage="scipy"), bounds=BOUNDS_MM, method="bounded",
                                   options={"xatol": 1e-6, "maxiter": 50})
    scipy_seconds, scipy_evaluations = time.perf_counter() - started, objective.count

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(sampler=optuna.samplers.TPESampler(seed=seed))
    started = time.perf_counter()
    study.optimize(lambda trial: objective(trial.suggest_float("separation_mm", *BOUNDS_MM), stage="optuna"),
                   n_trials=optuna_trials)
    optuna_seconds = time.perf_counter() - started

    search_log = [json.loads(line) for line in objective.log_path.read_text(encoding="utf-8").splitlines()]
    feasible_search = [row for row in search_log if row["feasible"]]
    if not feasible_search:
        report = {"example": "interop/optimization", "outcome": "NO_FEASIBLE_RESULT", "best": None,
                  "evaluations": len(search_log), "feasible_evaluations": 0,
                  "environment": environment("scipy", "optuna", "SALib", "numpy"),
                  "max_divergence_rad": max_divergence_rad,
                  "files": {"evaluations": str(objective.log_path)},
                  "message": "No sampled design met the selected requirement with a valid objective."}
        write_json(out_dir / "report.json", report)
        return report

    # Tolerance (variance-based sensitivity) about the best feasible sampled design.
    best = min(feasible_search, key=lambda row: row["value"])
    optimum = best["inputs"]["L2_x_mm"] - L1_X_MM
    problem = {"num_vars": 3, "names": ["L1_x_mm", "L2_x_mm", "waist_rel"],
               "bounds": [[-TOLERANCES["L1_x_mm"], TOLERANCES["L1_x_mm"]],
                          [-TOLERANCES["L2_x_mm"], TOLERANCES["L2_x_mm"]],
                          [-TOLERANCES["waist_rel"], TOLERANCES["waist_rel"]]]}
    samples = sobol_sample.sample(problem, sobol_n, calc_second_order=False, seed=seed)
    started = time.perf_counter()
    engine_values, reference_values = [], []
    for dl1, dl2, dw in samples:
        kwargs = {"l1_shift_mm": float(dl1), "waist_mm": WAIST_MM * (1.0 + float(dw))}
        engine_values.append(objective(optimum + float(dl2), stage="tolerance", **kwargs))
        reference_values.append(reference_divergence(optimum + float(dl2), **kwargs))
    tolerance_seconds = time.perf_counter() - started
    engine_values, reference_values = np.array(engine_values), np.array(reference_values)
    log = [json.loads(line) for line in (out_dir / "evaluations.jsonl").read_text(encoding="utf-8").splitlines()]
    infeasible = [r["evaluation"] for r in log if not r["feasible"]]
    if any(not r["objective_valid"] for r in log if r["stage"] == "tolerance"):
        report = {"example": "interop/optimization", "outcome": "INCOMPLETE_SAMPLE_SET",
                  "sobol_indices": None, "requested_samples": len(samples),
                  "valid_samples": sum(r["objective_valid"] for r in log if r["stage"] == "tolerance"),
                  "files": {"evaluations": str(objective.log_path)}}
        write_json(out_dir / "report.json", report)
        return report
    engine_si = sobol.analyze(problem, engine_values, calc_second_order=False, seed=seed)
    reference_si = sobol.analyze(problem, reference_values, calc_second_order=False, seed=seed)
    rel = [abs(r["value"] / r["reference_value"] - 1.0) for r in log if r["objective_valid"]]
    engine_vs_reference = max(rel)
    scipy_best = min((r for r in feasible_search if r["stage"] == "scipy"), key=lambda r: r["value"], default=None)
    optuna_best = min((r for r in feasible_search if r["stage"] == "optuna"), key=lambda r: r["value"], default=None)
    optuna_theta_excess = optuna_best["value"] / reference.fun - 1.0 if optuna_best else None
    checks = {
        "scipy_optimum_abs_err_mm": {"value": abs(scipy_best["inputs"]["L2_x_mm"] - L1_X_MM - reference.x) if scipy_best else None, "tolerance": 1e-4},
        "engine_vs_reference_max_rel_err": {"value": engine_vs_reference, "tolerance": 1e-9},
        "optuna_best_divergence_excess_rel": {"value": optuna_theta_excess, "tolerance": 0.01},
        "sobol_ST_max_abs_diff": {"value": float(np.max(np.abs(engine_si["ST"] - reference_si["ST"]))), "tolerance": 1e-6},
    }
    status = comparison_status(all(c["value"] is not None and c["value"] <= c["tolerance"] for c in checks.values()))
    identities = {r["identity"]["engine_build_id"] for r in log if r["identity"]}
    report = {
        "example": "interop/optimization", "outcome": status, "requires": "os-optics>=0.10.0",
        "best_feasible_evaluation": best["evaluation"], "max_divergence_rad": max_divergence_rad,
        "compute_budget": compute_budget, "max_evaluations": max_evaluations,
        "environment": environment("scipy", "optuna", "SALib", "numpy"),
        "reference": {"method": "ABCD Gaussian beam, numpy only", "optimum_separation_mm": reference.x,
                      "optimum_divergence_rad": reference.fun},
        "scipy": {"method": "minimize_scalar bounded", "separation_mm": scipy_best["inputs"]["L2_x_mm"] - L1_X_MM if scipy_best else None,
                  "divergence_rad": scipy_best["value"] if scipy_best else None, "evaluations": scipy_evaluations,
                  "requirement_feasible": scipy_best is not None, "optimizer_converged": bool(scipy_result.success),
                  "seconds": round(scipy_seconds, 2)},
        "optuna": {"sampler": f"TPESampler(seed={seed})", "trials": optuna_trials,
                   "separation_mm": optuna_best["inputs"]["L2_x_mm"] - L1_X_MM if optuna_best else None,
                   "divergence_rad": optuna_best["value"] if optuna_best else None,
                   "requirement_feasible": optuna_best is not None,
                   "seconds": round(optuna_seconds, 2)},
        "tolerance": {"method": "SALib Sobol (Saltelli sampling, first-order and total indices)",
                      "about_separation_mm": optimum, "uniform_half_ranges": TOLERANCES, "samples": len(samples),
                      "seconds": round(tolerance_seconds, 2),
                      "divergence_rad": {"engine_mean": float(engine_values.mean()), "engine_max": float(engine_values.max()),
                                         "reference_mean": float(reference_values.mean())},
                      "engine": {name: {key: float(engine_si[key][i]) for key in ("S1", "S1_conf", "ST", "ST_conf")}
                                 for i, name in enumerate(problem["names"])},
                      "reference": {name: {key: float(reference_si[key][i]) for key in ("S1", "S1_conf", "ST", "ST_conf")}
                                    for i, name in enumerate(problem["names"])}},
        "checks": checks,
        "evaluations": len(log), "run_statuses": {st: sum(1 for r in log if r["run_status"] == st)
                                                  for st in sorted({r["run_status"] for r in log})},
        "infeasible_evaluations": infeasible, "mean_seconds_per_evaluation": round(sum(r["seconds"] for r in log) / len(log), 4),
        "engine_build_ids": sorted(identities), "engine_version": installed,
        "first_evaluation_identity": log[0]["identity"],
        "files": {"evaluations": str(out_dir / "evaluations.jsonl"), "baseline_project": str(out_dir / "project-baseline.json")},
    }
    write_json(out_dir / "report.json", report)
    return {key: report[key] for key in ("example", "outcome", "reference", "scipy", "optuna", "checks",
                                         "evaluations", "run_statuses", "engine_build_ids", "files")} | {
        "report": str(out_dir / "report.json")}


def main(argv=None):
    def workflow():
        parser = ArgumentParser(description=__doc__.split("\n\n")[0])
        parser.add_argument("--out", type=Path, default=Path("run-interop-optimization"), help="output directory")
        parser.add_argument("--optuna-trials", type=int, default=60, help="Optuna trials (default 60)")
        parser.add_argument("--sobol-n", type=int, default=128, help="SALib base sample size (power of 2, default 128)")
        parser.add_argument("--seed", type=int, default=1, help="random seed for Optuna and SALib (default 1)")
        parser.add_argument("--max-divergence-rad", type=float, default=1e-3, help="maximum acceptable divergence half-angle (rad)")
        parser.add_argument("--max-evaluations", type=int, default=1000, help="total evaluation cap (default 1000)")
        parser.add_argument("--max-trace-steps", type=int, default=2048, help="Engine trace steps per evaluation")
        parser.add_argument("--max-interactions-per-path", type=int, default=64, help="Engine surface interactions per path")
        args = parser.parse_args(argv)
        return run_example(args.out, optuna_trials=args.optuna_trials, seed=args.seed, sobol_n=args.sobol_n,
                           max_divergence_rad=args.max_divergence_rad, max_evaluations=args.max_evaluations,
                           compute_budget={"max_trace_steps": args.max_trace_steps,
                                           "max_interactions_per_path": args.max_interactions_per_path})

    return emit(workflow)


if __name__ == "__main__":
    raise SystemExit(main())
