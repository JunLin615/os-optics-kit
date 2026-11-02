"""Seeded tolerance sampling of a Project: sampled offsets saved before any run, every sample run and counted.

Usage:
  python examples/tolerance_study.py SPEC.json --out STUDY_DIR [--base BASE.json] [--seed N] [--samples N]
                                     [--zero-tolerance] [--draw-only] [--overwrite] [--no-plots] [--no-report]
  python examples/tolerance_study.py --verify-seed STUDY_DIR

SPEC.json (keys; quantities are a bare number in the parameter's unit or {"value": x, "unit": "u"}):
  {
    "base_project": "base.json",                 # or "design": {"builder": ..., "arguments": {...}} as in
                                                 # sensitivity_analysis.py; --base overrides
    "targets": ["spot_radius"],                  # targets that must all PASS for a sample to PASS (default: all)
    "samples": 200,                              # number of sampled designs (the nominal run is extra)
    "seed": 20261018,                            # integer >= 0; --seed overrides
    "max_evaluations": 201,                      # required; refused before any run when samples + 1 exceeds it
    "parameters": [                              # sampled independently, in this order
      {"name": "L2_axial", "unit": "mm",
       "ops": [{"op": "transform", "selector": {"ids": ["L2"]}, "frame": {"component": "L2"},
                "translation_mm": [0, 0, "$value"]}],
       "nominal": 0,                             # as in sensitivity_analysis.py ("$value" = nominal + offset)
       "distribution": {"kind": "uniform", "low": -0.5, "high": 0.5}},
      {"name": "hwp_angle", "unit": "deg", "ops": [...],
       "distribution": {"kind": "normal", "sigma": 2, "mean_offset": 0,
                        "truncate_sigma": 3}}    # or "bounds": [low, high] (offsets, parameter unit)
    ]
  }

Distributions of the offset from nominal (parameter unit): uniform(low, high) (low <= high; low == high is a fixed
offset) and normal(mean_offset, sigma) truncated to [mean_offset - k sigma, mean_offset + k sigma] (truncate_sigma k)
or to explicit bounds [low, high] (sigma 0 gives the fixed offset mean_offset, which must lie within the bounds).
All parameters are independent. Sampling: numpy Generator(PCG64(seed)) draws one uniform number u in [0, 1) per
sample and parameter, row by row (sample 1 parameters 1..P, then sample 2, ...); uniform: low + (high - low) u;
truncated normal: inverse CDF, mean_offset + sigma * Phi^-1(Phi(a) + (Phi(b) - Phi(a)) u) with a, b the standardized
bounds (statistics.NormalDist), clipped to the bounds against round-off; bounds holding less than 1e-9 of the normal
distribution (more than about 6 sigma into one tail) are refused (SPEC_INVALID). The first n samples of a larger
run with the same seed are identical. Same seed + same spec give the same samples.csv bytes (--verify-seed redraws
and compares).
--zero-tolerance replaces every distribution by the fixed offset 0: every sample then reproduces the nominal Project
hash and result exactly (the Engine run is deterministic; numerical tolerance 0).

Before any run: the spec, units and every sample's ops are validated, the base Project must pass
json_api.check_project, the ops at zero offset must reproduce the base Project hash (NOMINAL_MISMATCH otherwise), and
STUDY_DIR/sampling.json (normalized distributions, bounds, seed, generator, numpy version, samples.csv sha256) and
STUDY_DIR/samples.csv (sample_id, offset and applied value of every parameter) are written. --draw-only stops there.

Each sample retains execution, quantity evidence and target assessments. Its shorthand is ERROR (not run),
OUT_OF_MODEL (selected target applicability), UNKNOWN (indeterminate), FAIL (unmet), PASS (all selected targets met),
or NOT_APPLICABLE (no selected requirements). Counts use every requested sample as denominator; the PASS fraction
among evaluable samples (PASS + FAIL) is listed separately. These are simulated sample fractions for the stated
independent distributions. Per-target statistics use finite exact values with applicable dependencies; bounded
or unavailable values and unrelated diagnostics remain visible in the sample records.
Stdout: one compact JSON document (counts with denominators, target distributions, samples whose runs carry
warnings, unsuccessful samples). Exit 0 completed with at least one PASS sample, 1 no PASS sample (or a
--verify-seed mismatch), 4 refused spec or invalid input.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import time

if __package__:
    from . import workflow_common as wc
    from . import sweep_parameters as sweep
    from . import sensitivity_analysis as sa
    from .edit_project import normalize_ops
else:
    import workflow_common as wc
    import sweep_parameters as sweep
    import sensitivity_analysis as sa
    from edit_project import normalize_ops

KIND = "opensci-example.tolerance-study"
EXIT_CODES = {**wc.WORKFLOW_EXIT_CODES, "DRAWN": 0, "REPRODUCED": 0, "MISMATCH": 1}
GENERATOR = "numpy.random.Generator(numpy.random.PCG64(seed))"
PERCENTILES = (5, 25, 50, 75, 95)
ASSUMPTIONS = ("parameters are sampled independently", "each distribution describes the offset from the "
               "parameter's nominal value in the parameter's unit", "every sample is one deterministic Engine run "
               "of the base Project with all offsets applied in parameter order")


# --------------------------------------------------------------------------------------------- spec

def _distribution(parameter, raw, zero_tolerance):
    name, unit = parameter["name"], parameter["unit"]
    if zero_tolerance:
        return {"kind": "uniform", "low": 0.0, "high": 0.0, "bounds": [0.0, 0.0], "zero_tolerance": True}
    if not isinstance(raw, dict) or raw.get("kind") not in ("uniform", "normal"):
        raise wc.WorkflowError("SPEC_INVALID", f"parameter {name!r}: distribution.kind must be uniform or normal")

    def q(key, default=None):
        if key not in raw:
            if default is None:
                raise wc.WorkflowError("SPEC_INVALID", f"parameter {name!r}: distribution needs {key}")
            return default
        return sa.quantity(raw[key], unit, f"parameter {name} distribution.{key}")

    if raw["kind"] == "uniform":
        low, high = q("low"), q("high")
        if low > high:
            raise wc.WorkflowError("SPEC_INVALID", f"parameter {name!r}: uniform needs low <= high")
        return {"kind": "uniform", "low": low, "high": high, "bounds": [low, high]}
    sigma, mean = q("sigma"), q("mean_offset", 0.0)
    if sigma < 0:
        raise wc.WorkflowError("SPEC_INVALID", f"parameter {name!r}: sigma must be >= 0")
    if ("truncate_sigma" in raw) == ("bounds" in raw):
        raise wc.WorkflowError("SPEC_INVALID", f"parameter {name!r}: a normal distribution needs exactly one of "
                               "truncate_sigma or bounds [low, high]")
    if "truncate_sigma" in raw:
        k = raw["truncate_sigma"]
        if isinstance(k, bool) or not isinstance(k, (int, float)) or not k > 0 or not math.isfinite(k):
            raise wc.WorkflowError("SPEC_INVALID", f"parameter {name!r}: truncate_sigma must be a positive number")
        low, high = mean - float(k) * sigma, mean + float(k) * sigma
    else:
        bounds = raw["bounds"]
        if not isinstance(bounds, list) or len(bounds) != 2:
            raise wc.WorkflowError("SPEC_INVALID", f"parameter {name!r}: bounds must be [low, high]")
        low = sa.quantity(bounds[0], unit, f"parameter {name} distribution.bounds[0]")
        high = sa.quantity(bounds[1], unit, f"parameter {name} distribution.bounds[1]")
        if not low < high:
            raise wc.WorkflowError("SPEC_INVALID", f"parameter {name!r}: bounds need low < high")
    if sigma == 0 and not low <= mean <= high:
        raise wc.WorkflowError("SPEC_INVALID", f"parameter {name!r}: mean_offset lies outside the bounds")
    if sigma > 0 and not low < high:
        raise wc.WorkflowError("SPEC_INVALID", f"parameter {name!r}: the truncation interval is empty")
    if sigma > 0:
        from statistics import NormalDist

        standard = NormalDist()
        mass = standard.cdf((high - mean) / sigma) - standard.cdf((low - mean) / sigma)
        if not mass >= MIN_TRUNCATED_MASS:
            raise wc.WorkflowError("SPEC_INVALID", f"parameter {name!r}: the bounds [{low:.6g}, {high:.6g}] hold "
                                   f"{mass:.3g} of the normal distribution (mean_offset {mean:.6g}, sigma {sigma:.6g}), "
                                   f"less than {MIN_TRUNCATED_MASS:g}: they lie more than about 6 sigma into one "
                                   "tail, where the inverse CDF cannot be sampled in double precision; use bounds "
                                   "nearer the mean, a larger sigma or a uniform distribution",
                                   probability=mass, minimum_probability=MIN_TRUNCATED_MASS)
    out = {"kind": "normal", "sigma": sigma, "mean_offset": mean, "bounds": [low, high]}
    if "truncate_sigma" in raw:
        out["truncate_sigma"] = float(raw["truncate_sigma"])
    return out


def describe_distribution(parameter):
    d, unit = parameter["distribution"], parameter["unit"] or ""
    low, high = d["bounds"]
    if d["kind"] == "uniform":
        text = f"uniform({low:.6g}, {high:.6g}) {unit}"
    else:
        text = (f"normal(mean_offset {d['mean_offset']:.6g}, sigma {d['sigma']:.6g}) {unit}, "
                f"bounds [{low:.6g}, {high:.6g}]")
        if "truncate_sigma" in d:
            text += f" (+-{d['truncate_sigma']:.6g} sigma)"
    return text.replace("  ", " ").strip()


def normalize_spec(spec, *, build=None, seed=None, samples=None, zero_tolerance=False):
    """Validated parameters with distributions, sample count, seed and budget (WorkflowError before any run)."""
    parameters = spec.get("parameters")
    if not isinstance(parameters, list) or not parameters:
        raise wc.WorkflowError("SPEC_INVALID", "parameters must list at least one parameter")
    out, names = [], set()
    for index, parameter in enumerate(parameters):
        common = sa.common_parameter(parameter, index, build=build)
        if common["name"] in names:
            raise wc.WorkflowError("SPEC_INVALID", f"duplicate parameter name {common['name']!r}")
        names.add(common["name"])
        common["distribution"] = _distribution(common, parameter.get("distribution"), zero_tolerance)
        common["description"] = describe_distribution(common)
        out.append(common)
    count = spec.get("samples") if samples is None else samples
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise wc.WorkflowError("SPEC_INVALID", "samples must be a positive integer")
    seed = spec.get("seed") if seed is None else seed
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise wc.WorkflowError("SPEC_INVALID", "seed must be an integer >= 0 (spec seed or --seed)")
    budget = spec.get("max_evaluations")
    if isinstance(budget, bool) or not isinstance(budget, int) or budget < 1:
        raise wc.WorkflowError("SPEC_INVALID", "max_evaluations must be a positive integer")
    if count + 1 > budget:
        raise wc.WorkflowError("BUDGET_EXCEEDED", f"the study needs {count + 1} runs ({count} samples + nominal), "
                               f"more than max_evaluations {budget}; nothing was run", samples=count + 1,
                               max_evaluations=budget)
    return {"parameters": out, "samples": count, "seed": seed, "max_evaluations": budget,
            "targets": spec.get("targets"), "zero_tolerance": bool(zero_tolerance)}


# --------------------------------------------------------------------------------------------- sampling

MIN_TRUNCATED_MASS = 1e-9  # smallest probability of a truncated normal's bounds that is sampled
_P_LOW, _P_HIGH = math.nextafter(0.0, 1.0), math.nextafter(1.0, 0.0)


def draw_samples(parameters, count, seed):
    """``[(sample_id, {name: offset})]`` for samples s0001.. (module docstring: generator, order, inverse CDF)."""
    import numpy as np
    from statistics import NormalDist

    rng = np.random.Generator(np.random.PCG64(seed))
    uniforms = rng.random((count, len(parameters)))
    standard = NormalDist()
    rows = []
    for i in range(count):
        offsets = {}
        for j, parameter in enumerate(parameters):
            d = parameter["distribution"]
            u = float(uniforms[i, j])
            low, high = d["bounds"]
            if d["kind"] == "uniform":
                value = low + (high - low) * u
            elif d["sigma"] == 0.0:
                value = d["mean_offset"]
            else:
                a = standard.cdf((low - d["mean_offset"]) / d["sigma"])
                b = standard.cdf((high - d["mean_offset"]) / d["sigma"])
                p = min(max(a + (b - a) * u, _P_LOW), _P_HIGH)  # stays inside (0, 1) against round-off
                value = d["mean_offset"] + d["sigma"] * standard.inv_cdf(p)
            offsets[parameter["name"]] = min(max(value, low), high)
        rows.append((f"s{i + 1:04d}", offsets))
    return rows


def samples_columns(parameters):
    columns = ["sample_id"]
    for p in parameters:
        columns += [f"offset:{p['name']}", f"value:{p['name']}", f"unit:{p['name']}"]
    return columns


def samples_rows(parameters, drawn):
    rows = []
    for sample_id, offsets in drawn:
        row = {"sample_id": sample_id}
        for p in parameters:
            row[f"offset:{p['name']}"] = offsets[p["name"]]
            row[f"value:{p['name']}"] = p["nominal"] + offsets[p["name"]]
            row[f"unit:{p['name']}"] = p["unit"]
        rows.append(row)
    return rows


def write_samples(study_dir, normalized, drawn):
    """samples.csv and sampling.json (written before any run); returns the samples.csv sha256."""
    parameters = normalized["parameters"]
    path = wc.write_csv(Path(study_dir) / "samples.csv", samples_rows(parameters, drawn), samples_columns(parameters))
    digest = wc.sha256_file(path)
    wc.write_json(Path(study_dir) / "sampling.json", sampling_record(normalized, digest))
    return digest


def sampling_record(normalized, digest):
    import numpy as np

    return {"kind": f"{KIND}-sampling", "generator": GENERATOR, "numpy_version": np.__version__,
            "seed": normalized["seed"], "samples": normalized["samples"],
            "zero_tolerance": normalized["zero_tolerance"],
            "draw_order": "one uniform number per sample and parameter, sample by sample, parameters in spec order",
            "assumptions": list(ASSUMPTIONS),
            "parameters": [{"name": p["name"], "unit": p["unit"], "nominal": p["nominal"],
                            "distribution": p["distribution"], "description": p["description"]}
                           for p in normalized["parameters"]],
            "samples_csv_sha256": digest}


def verify_samples(study_dir):
    """Redraw the samples of a study from its saved spec, seed and options and compare with samples.csv."""
    study_dir = Path(study_dir)
    study = wc.read_json(study_dir / "study.json")
    options = study["options"]
    normalized = normalize_spec(study["spec"], seed=options["seed"], samples=options["samples"],
                                zero_tolerance=options["zero_tolerance"],
                                build=(lambda values: None) if options.get("build_function") else None)
    drawn = draw_samples(normalized["parameters"], normalized["samples"], normalized["seed"])
    import io
    import csv

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    columns = samples_columns(normalized["parameters"])
    writer.writerow(columns)
    for row in samples_rows(normalized["parameters"], drawn):
        writer.writerow([wc.csv_cell(row.get(c)) for c in columns])
    redrawn = wc.sha256_bytes(buffer.getvalue().encode("utf-8"))
    saved = wc.sha256_file(study_dir / "samples.csv")
    recorded = wc.read_json(study_dir / "sampling.json").get("samples_csv_sha256")
    same = redrawn == saved == recorded
    return {"status": "REPRODUCED" if same else "MISMATCH", "seed": normalized["seed"],
            "samples": normalized["samples"], "generator": GENERATOR, "redrawn_sha256": redrawn,
            "samples_csv_sha256": saved, "recorded_sha256": recorded, "study_dir": study_dir.as_posix()}


# --------------------------------------------------------------------------------------------- statistics

def target_distributions(rows, targets, units):
    """Statistics over each target's exact applicable values, with count and requested denominator."""
    import numpy as np

    out = {}
    total = len(rows)
    for target_id in targets:
        values = [r.get(f"target:{target_id}") for r in rows if r.get(f"target:{target_id}:exact")]
        values = [v for v in values if isinstance(v, (int, float))]
        statuses = {}
        for r in rows:
            key = r.get(f"target:{target_id}:status") if r.get(f"target:{target_id}:exact") else \
                r["outcome"]
            statuses[key or "NO_VALUE"] = statuses.get(key or "NO_VALUE", 0) + 1
        entry = {"unit": units.get(target_id), "count": len(values), "denominator": total,
                 "population": "samples with a finite exact target value and applicable dependencies", "status_counts": statuses}
        if values:
            array = np.asarray(values, dtype=float)
            entry.update({"min": float(array.min()), "max": float(array.max()), "mean": float(array.mean()),
                          "std": float(array.std()),
                          **{f"p{q:02d}": float(np.percentile(array, q)) for q in PERCENTILES}})
        out[target_id] = entry
    return out


def zero_offset_check(rows, nominal_row, targets):
    """Samples whose offsets are all exactly 0 compared with the nominal run (exact equality expected)."""
    zero = [r for r in rows if all(r.get(k) == 0.0 for k in r if k.startswith("param:") and k.count(":") == 1)]
    if not zero:
        return None
    differences = {}
    for target_id in targets:
        nominal = nominal_row.get(f"target:{target_id}")
        diffs = [abs(r[f"target:{target_id}"] - nominal) for r in zero
                 if isinstance(r.get(f"target:{target_id}"), (int, float)) and isinstance(nominal, (int, float))]
        differences[target_id] = max(diffs) if diffs else None
    same_hash = sum(1 for r in zero if r.get("result_hash") == nominal_row.get("result_hash"))
    same_project = sum(1 for r in zero if r.get("project_hash") == nominal_row.get("project_hash"))
    return {"samples": len(zero), "same_project_hash": same_project, "same_result_hash": same_hash,
            "max_abs_difference": differences, "numerical_tolerance": 0.0,
            "agrees": same_hash == len(zero) and all(v == 0.0 for v in differences.values() if v is not None)}


# --------------------------------------------------------------------------------------------- study

def run_tolerance(spec, study_dir, *, base=None, build=None, base_path=None, spec_dir=None, overwrite=False,
                  seed=None, samples=None, zero_tolerance=False, draw_only=False, plots=True, report=True):
    """Draw, save and run the tolerance samples of ``spec`` (module docstring); returns the summary.

    ``base`` / ``build`` as in sensitivity_analysis.run_sensitivity (``build(values)`` gets nominal + offset of
    every parameter). ``seed`` / ``samples`` override the spec; ``draw_only`` writes the sample set and stops."""
    t_start = time.perf_counter()
    normalized = normalize_spec(spec, build=build, seed=seed, samples=samples, zero_tolerance=zero_tolerance)
    parameters = normalized["parameters"]
    document, base_info = sa.resolve_base(spec, spec_dir=spec_dir, base=base, base_path=base_path, build=build)
    if build is not None:
        document = build({p["name"]: p["nominal"] for p in parameters})
    checked = sa.check_base(document, parameters, build=build)
    targets = sa.selected_targets(document, normalized["targets"])
    units = sa.target_units(document)
    drawn = draw_samples(parameters, normalized["samples"], normalized["seed"])
    if build is None:  # unit and op errors in any sample are spec errors: refuse before running anything
        for _sid, offsets in drawn:
            normalize_ops(document, [op for p in parameters for op in sa.parameter_ops(p, offsets[p["name"]])])
    study_dir = sa.prepare_dir(study_dir, overwrite)
    study = {
        "kind": KIND, "spec": spec,
        "options": {"seed": normalized["seed"], "samples": normalized["samples"],
                    "zero_tolerance": normalized["zero_tolerance"], "build_function": base_info.get("build_function")},
        "normalized": {**normalized, "targets": targets,
                       "parameters": [{**p, "values": [p["description"]]} for p in parameters]},
        "base_project": {**base_info, "project_hash": checked["project_hash"],
                         "engine_input_hash": checked["engine_input_hash"],
                         "document_sha256": wc.canonical_digest(document)},
        "engine": wc.engine_identity(), "installed_catalog": wc.catalog_identity(),
        "catalog": wc.run_catalog_identity([]), "environment": wc.environment(),
        "seed": normalized["seed"], "sampling": GENERATOR, "assumptions": list(ASSUMPTIONS),
        "budget": {"max_evaluations": normalized["max_evaluations"], "samples": normalized["samples"] + 1},
        "stop_on": [], "started": wc.utc_now(),
    }
    wc.write_json(study_dir / "study.json", study)
    digest = write_samples(study_dir, normalized, drawn)
    if draw_only:
        return {"status": "DRAWN", "kind": f"{KIND}-summary", "seed": normalized["seed"],
                "samples": normalized["samples"], "samples_csv_sha256": digest,
                "parameters": [{"name": p["name"], "unit": p["unit"], "distribution": p["description"]}
                               for p in parameters],
                "paths": {"study_dir": study_dir.as_posix(), "samples": "samples.csv", "sampling": "sampling.json",
                          "study": "study.json"}}
    zero = {p["name"]: 0.0 for p in parameters}
    nominal = sa.run_one(document, parameters, {}, study_dir / "samples" / "s0000", build=None, targets=targets)
    nominal.update({"sample_id": "s0000", "role": "nominal"})
    wc.write_json(study_dir / "samples" / "s0000" / "sample.json", nominal)
    nominal_row = sweep.row_from_record("s0000", zero, parameters, nominal, study_dir)
    rows = []
    for sample_id, offsets in drawn:
        sample_dir = study_dir / "samples" / sample_id
        record = sa.run_one(document, parameters, offsets, sample_dir, build=build, targets=targets)
        record.update({"sample_id": sample_id, "role": "sample"})
        wc.write_json(sample_dir / "sample.json", record)
        row = sweep.row_from_record(sample_id, offsets, parameters, record, study_dir)
        for p in parameters:
            row[f"param:{p['name']}:value"] = p["nominal"] + offsets[p["name"]]
        rows.append(row)
    sweep.select_best(rows, [{"outcome": ["PASS"]}], None)
    counts = sweep.counts(rows)
    counts["pass_fraction"]["definition"] = (
        "PASS samples / all requested samples: fraction of the simulated samples, drawn from the stated independent "
        "distributions, in which every selected target passed")
    status = "COMPLETED" if counts["by_outcome"]["PASS"] else "NO_FEASIBLE"
    all_rows = [{**nominal_row, "role": "nominal", "feasible": None, "constraint_failures": []}] + \
        [{**r, "role": "sample"} for r in rows]
    columns = sweep._columns(all_rows)
    wc.write_csv(study_dir / "table.csv", all_rows, columns)
    study["catalog"] = wc.run_catalog_identity([nominal_row, *rows])
    study["finished"] = wc.utc_now()
    wc.write_json(study_dir / "study.json", study)
    summary = {
        "status": status, "kind": f"{KIND}-summary",
        "sampling": {"generator": GENERATOR, "seed": normalized["seed"], "samples": normalized["samples"],
                     "zero_tolerance": normalized["zero_tolerance"], "samples_csv_sha256": digest,
                     "assumptions": list(ASSUMPTIONS)},
        "parameters": [{"name": p["name"], "unit": p["unit"], "nominal": p["nominal"],
                        "distribution": p["distribution"], "description": p["description"],
                        "sampled": _sampled_range([o[p["name"]] for _s, o in drawn])} for p in parameters],
        "selected_targets": targets,
        "nominal": {"sample_id": "s0000", "outcome": nominal["outcome"], "status": nominal["status"],
                    "model_scope_issues": nominal["model_scope_issues"],
                    "targets": [{k: t.get(k) for k in ("id", "value", "unit", "status", "assessment", "quantity_state", "power_upper_bound_W")} for t in nominal["targets"]
                                if t["id"] in targets], "run_dir": nominal_row["run_dir"]},
        "counts": counts,
        "target_distributions": target_distributions(rows, targets, units),
        "zero_offset_check": zero_offset_check(rows, nominal_row, targets),
        "feasible": counts["by_outcome"]["PASS"], "constraints": [{"outcome": ["PASS"]}], "objective": None,
        "best": None, "no_feasible": None if status == "COMPLETED" else {
            "message": "no sample passed every selected target",
            "failed_constraints": {f"outcome {k}": v for k, v in counts["by_outcome"].items() if k != "PASS" and v}},
        "stopped_by": None, "targets": sweep._target_units(rows), "columns": columns, "rows": rows,
        "identity": {"engine": study["engine"], **study["catalog"],
                     "base_project_hash": checked["project_hash"]},
        "paths": {"study_dir": study_dir.as_posix(), "study": "study.json", "sampling": "sampling.json",
                  "samples_table": "samples.csv", "table": "table.csv", "summary": "summary.json",
                  "samples": "samples"},
        "seconds": None,
    }
    wc.write_json(study_dir / "summary.json", summary)
    skipped = sa.matplotlib_missing() if plots else "plots disabled"
    if skipped is None:
        summary["plots"] = plot_tolerance(study_dir, summary)
        summary["paths"]["plots"] = "plots"
    else:
        summary["plots"] = {"written": [], "skipped": skipped}
    if report:
        summary["report"] = sa.append_report(
            study_dir, plots=[study_dir / "plots"] if skipped is None else None, title="Tolerance sampling",
            lines=report_lines(summary), extra={"tolerance": {k: summary[k] for k in (
                "sampling", "parameters", "nominal", "counts", "target_distributions", "zero_offset_check")}},
            artifacts=[(study_dir / "samples.csv", "sampled offsets"),
                       (study_dir / "sampling.json", "sampling record")])
        summary["paths"]["report"] = "report/report.md"
    summary["seconds"] = round(time.perf_counter() - t_start, 3)
    wc.write_json(study_dir / "summary.json", summary)
    return summary


def _sampled_range(values):
    return {"min": min(values), "max": max(values), "count": len(values)}


# --------------------------------------------------------------------------------------------- outputs

def plot_tolerance(study_dir, summary):
    """Histogram per selected target (values of in-model samples, target bounds) and per parameter (sampled
    offsets, distribution bounds)."""
    if __package__:
        from . import plot_results as pr
    else:
        import plot_results as pr

    study_dir = Path(study_dir)
    out_dir = study_dir / "plots"
    common = {"engine_build": summary["identity"]["engine"]["engine_build_id"],
              "catalog_version": summary["identity"]["catalog_version"]}
    written = []
    total = len(summary["rows"])
    for target_id in summary["selected_targets"]:
        unit = summary["target_distributions"][target_id]["unit"]
        values = [r.get(f"target:{target_id}") if r.get(f"target:{target_id}:exact") else None
                  for r in summary["rows"]]
        bounds = None
        for sample_dir in sorted((study_dir / "samples").iterdir()):
            record = wc.read_json(sample_dir / "sample.json") if (sample_dir / "sample.json").is_file() else {}
            target = next((t for t in record.get("targets") or [] if t["id"] == target_id), None)
            if target and target.get("bounds"):
                bounds = wc.target_bounds(target)
                break
        result = pr.plot_histogram(values, unit, out_dir, f"histogram_{target_id}",
                                   title=f"{target_id}, in-model samples", xlabel=f"{target_id} [{unit}]",
                                   bounds=bounds, source=study_dir / "table.csv", **common)
        written.append({"kind": "target", "id": target_id, "plotted": result["plotted"], "total": total,
                        **result["out"]})
    for parameter in summary["parameters"]:
        low, high = parameter["distribution"]["bounds"]
        values = [r.get(f"param:{parameter['name']}") for r in summary["rows"]]
        result = pr.plot_histogram(values, parameter["unit"], out_dir, f"histogram_param_{parameter['name']}",
                                   title=f"{parameter['name']} offsets ({parameter['distribution']['kind']})",
                                   xlabel=f"{parameter['name']} offset from nominal [{parameter['unit'] or '1'}]",
                                   bounds={"minimum": low, "maximum": high}, source=study_dir / "samples.csv",
                                   **common)
        written.append({"kind": "parameter", "id": parameter["name"], "plotted": result["plotted"],
                        "total": total, **result["out"]})
    return {"written": written}


def report_lines(summary):
    sampling, counts = summary["sampling"], summary["counts"]
    pf, pe = counts["pass_fraction"], counts["pass_fraction_among_evaluable"]
    lines = [f"Sampling: {sampling['samples']} samples, seed {sampling['seed']}, {GENERATOR}; "
             + "; ".join(sampling["assumptions"]) + ". Sampled offsets: [samples.csv](../samples.csv) (sha256 "
             f"`{sampling['samples_csv_sha256'][:16]}`), record [sampling.json](../sampling.json).", ""]
    lines += sa.sweep_table(["parameter", "unit", "nominal", "distribution of the offset", "sampled min",
                             "sampled max"],
                            [[p["name"], p["unit"] or "-", sa.fmt(p["nominal"]), p["description"],
                              sa.fmt(p["sampled"]["min"]), sa.fmt(p["sampled"]["max"])] for p in summary["parameters"]])
    lines += ["", "Nominal (s0000): outcome " + summary["nominal"]["outcome"] + "; " + "; ".join(
        f"{t['id']} = {sa.fmt(t['value'])} {t['unit']} {t['status']}" for t in summary["nominal"]["targets"]), ""]
    lines += sa.sweep_table(["outcome", "samples", "of requested"], [
        [k, v, counts["requested"]] for k, v in counts["by_outcome"].items()])
    lines += ["", f"PASS fraction (every selected target passed): {pf['numerator']}/{pf['denominator']} of all "
                  f"simulated samples = {sa.fmt(pf['value'], 6)}; among evaluable samples (PASS + FAIL): "
                  f"{pe['numerator']}/{pe['denominator']}"
                  + (f" = {sa.fmt(pe['value'], 6)}" if pe["value"] is not None else "") + ". This is the "
                  "fraction of simulated samples under the stated distributions and independence.", ""]
    rows = []
    for target_id, d in summary["target_distributions"].items():
        rows.append([target_id, d["unit"], f"{d['count']}/{d['denominator']}", sa.fmt(d.get("min")),
                     sa.fmt(d.get("p05")), sa.fmt(d.get("p50")), sa.fmt(d.get("p95")), sa.fmt(d.get("max")),
                     sa.fmt(d.get("mean")), sa.fmt(d.get("std")),
                     ", ".join(f"{k} {v}" for k, v in d["status_counts"].items())])
    lines += ["Target values over samples with a finite exact target value and applicable dependencies (numpy linear percentiles):", ""]
    lines += sa.sweep_table(["target", "unit", "count", "min", "p05", "median", "p95", "max", "mean", "std",
                             "per-target status"], rows) + [""]
    check = summary["zero_offset_check"]
    if check is not None:
        lines.append(f"Zero-offset samples: {check['samples']}; same result hash as nominal: "
                     f"{check['same_result_hash']}; maximum absolute target difference: "
                     f"{wc.canonical_json(check['max_abs_difference'])} (tolerance 0).")
        lines.append("")
    lines.append("Reproduce the sample set: `python examples/tolerance_study.py --verify-seed <study dir>`.")
    return lines


def compact(summary, limit=5):
    """The bounded stdout document of a tolerance summary."""
    problems = [{"sample_id": r["sample_id"], "outcome": r["outcome"], "error_code": r.get("error_code"),
                 "model_scope_issues": r.get("model_scope_issues"),
                 "offsets": {p["name"]: r.get(f"param:{p['name']}") for p in summary["parameters"]}}
                for r in summary["rows"] if r["outcome"] in ("ERROR", "OUT_OF_MODEL", "UNKNOWN")]
    shown = wc.bounded(problems, limit)
    return {"status": summary["status"], "sampling": summary["sampling"],
            "samples_with_warnings": wc.warning_summary(summary["rows"]),
            "parameters": [{"name": p["name"], "unit": p["unit"], "nominal": p["nominal"],
                            "distribution": p["description"]} for p in summary["parameters"]],
            "nominal": {k: summary["nominal"][k] for k in ("outcome", "targets")},
            "counts": summary["counts"], "target_distributions": summary["target_distributions"],
            "zero_offset_check": summary["zero_offset_check"],
            "unsuccessful_samples": shown["shown"], "unsuccessful_omitted": shown["omitted"],
            "identity": {"engine_version": summary["identity"]["engine"]["engine_version"],
                         "engine_build_id": summary["identity"]["engine"]["engine_build_id"],
                         "catalog_version": summary["identity"]["catalog_version"]},
            "seconds": summary["seconds"], "paths": summary["paths"]}


def main(argv=None):
    def workflow():
        parser = wc.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
        parser.add_argument("spec", type=Path, nargs="?", help="Tolerance spec JSON")
        parser.add_argument("--out", type=Path, help="Study directory to create")
        parser.add_argument("--base", type=Path, help="Base Project JSON (overrides base_project / design)")
        parser.add_argument("--seed", type=int, help="Random seed (overrides the spec)")
        parser.add_argument("--samples", type=int, help="Number of samples (overrides the spec)")
        parser.add_argument("--zero-tolerance", action="store_true", help="Sample every parameter at offset 0")
        parser.add_argument("--draw-only", action="store_true", help="Write the sample set and stop")
        parser.add_argument("--verify-seed", type=Path, metavar="STUDY_DIR",
                            help="Redraw a study's samples from its spec and seed and compare with samples.csv")
        parser.add_argument("--overwrite", action="store_true", help="Replace this script's files in --out")
        parser.add_argument("--no-plots", action="store_true", help="Do not write plots")
        parser.add_argument("--no-report", action="store_true", help="Do not write report/")
        args = parser.parse_args(argv)
        if args.verify_seed is not None:
            return verify_samples(args.verify_seed)
        if args.spec is None or args.out is None:
            raise wc.WorkflowError("BAD_ARGUMENT", "give SPEC.json and --out STUDY_DIR, or --verify-seed STUDY_DIR")
        spec = wc.read_json(args.spec)
        base = wc.read_json(args.base) if args.base else None
        summary = run_tolerance(spec, args.out, base=base, base_path=args.base, spec_dir=args.spec.parent,
                                overwrite=args.overwrite, seed=args.seed, samples=args.samples,
                                zero_tolerance=args.zero_tolerance, draw_only=args.draw_only,
                                plots=not args.no_plots, report=not args.no_report)
        return summary if summary["status"] == "DRAWN" else compact(summary)

    return wc.emit(workflow, EXIT_CODES)


if __name__ == "__main__":
    raise SystemExit(main())
