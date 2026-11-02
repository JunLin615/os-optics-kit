"""Summarize a saved local run without rerunning the simulation.

Usage: python examples/result_summary.py run-example [--details]

Exit codes follow requirement_outcome: 0 all selected targets met or none declared,
1 an unmet target, 2 indeterminate, 3 selected target outside its model, 4 invalid
input/artifact or failed computation. Raw aggregate status is preserved separately.
"""

import argparse
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys

EXIT_CODES = {
    "PASS": 0, "WARNING": 0, "NOT_APPLICABLE": 0,
    "FAIL": 1, "UNKNOWN": 2, "OUT_OF_MODEL_SCOPE": 3, "OUT_OF_MODEL": 3, "ERROR": 4,
}
ERROR_EXIT = 4


class ArgumentParser(argparse.ArgumentParser):
    """Keep usage errors distinct from an UNKNOWN scientific result."""

    def error(self, message):
        raise ValueError(message)


def summarize_results(results, *, artifacts=None, details=False):
    """Return JSON-safe results using the public RunResults API; never simulate.

    All targets and diagnostics are retained. Only the power overview is reduced;
    complete power entries and rays remain in the supplied artifact files.
    """
    from os_optics.results import ResultsError

    description = results.describe()
    integrity = description["integrity"]
    if integrity["checked"] and not integrity["consistent"]:
        raise ResultsError("RUN_INTEGRITY_FAILED", "; ".join(integrity["problems"]))
    statuses = description["status"]
    status = statuses.get("validation_status")
    if status is None:
        status = statuses["engine_status"]
    if status not in EXIT_CODES:
        raise ValueError(f"Unsupported result status: {status}")
    compact = results.targets()
    quantities = {row["observation_id"]: row for row in results.monitors()}
    targets = []
    for target in compact["targets"]:
        row = {"id": target["target_id"], **{k: v for k, v in target.items() if k != "target_id"}}
        quantity = quantities.get(target.get("observation_id"), {})
        row["observation_power_state"] = quantity.get("quantity_state")
        row["quantity_state"] = quantity.get("quantity_state") if target.get("metric") == "power_W" else None
        row["power_complete"] = quantity.get("power_complete")
        if "power_upper_bound_W" in quantity:
            row["power_upper_bound_W"] = quantity["power_upper_bound_W"]
        targets.append(row)
    if __package__:
        from .workflow_common import classify
    else:
        from workflow_common import classify
    requirement_outcome = classify(status, compact["model_scope_issues"], targets)
    execution = (compact.get("outcome") or {}).get("execution") or {}
    if execution.get("outcome") in ("FAILED", "CANCELLED"):
        requirement_outcome = "ERROR"
    power = results.power_budget()
    summary = {
        "status": status,
        "outcome": compact["outcome"],
        "requirement_outcome": requirement_outcome,
        "observations": list(quantities.values()),
        "in_model_scope": compact["in_model_scope"],
        "out_of_model_issues": compact["out_of_model_issues"],
        "engine_status": statuses["engine_status"],
        "validation_status": statuses.get("validation_status"),
        "targets": targets,
        "power_budget": {key: power[key] for key in (
            "available", "total_input_W", "total_sinks", "residual_W", "tolerance_W", "conserved"
        ) if key in power},
        "issues": [issue.model_dump(mode="json") for issue in results.result.issues],
        "model_scope_issues": statuses.get("model_scope_issues", []),
        "element_models": description["element_models"],
        "identity": results.identity(),
        "integrity": integrity,
        "artifacts": {key: str(path) for key, path in (artifacts or {}).items()},
    }
    if details:
        summary["power_budget_details"] = power
    return summary


def summarize_run(directory, *, details=False):
    """Read and verify a native local run; do not modify its files."""
    from os_optics.results import load_run

    directory = Path(directory)
    results = load_run(directory)
    artifacts = {"run_directory": directory, "manifest": directory / "manifest.json"}
    artifacts.update({item["role"]: directory / item["name"]
                      for item in results.describe()["files"]})
    summary = summarize_results(results, artifacts=artifacts, details=details)
    summary["view_command"] = f'os-optics view "{directory}"'
    return summary


def emit_cli(workflow, *, exit_status_key="requirement_outcome"):
    """Emit JSON using the caller's declared outcome field for the exit code."""
    try:
        with redirect_stdout(sys.stderr):
            summary = workflow()
        code = EXIT_CODES[summary[exit_status_key]]
        output = json.dumps(summary, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    except Exception as exc:
        error = {"error": {"code": getattr(exc, "code", type(exc).__name__), "message": str(exc)}}
        print(json.dumps(error, ensure_ascii=True), file=sys.stderr)
        return ERROR_EXIT
    print(output)
    return code


def main(argv=None):
    def workflow():
        parser = ArgumentParser(description=__doc__)
        parser.add_argument("directory", type=Path, help="Saved local run directory")
        parser.add_argument("--details", action="store_true", help="Include the complete power ledger")
        args = parser.parse_args(argv)
        return summarize_run(args.directory, details=args.details)

    return emit_cli(workflow)


if __name__ == "__main__":
    raise SystemExit(main())
