"""Summarize a saved local run without rerunning the simulation.

Usage: os-optics-kit summary RUN_DIR [--details]   (or: python -m os_optics_kit summary RUN_DIR)

A run whose files do not match their recorded hashes is summarized as stored: "integrity" holds consistent false
and the problems, and one JSON line {"warning": {"code": "RUN_INTEGRITY_FAILED", ...}} goes to stderr.
Exit codes follow the os-optics requirements exit contract: 0 the run completed and every target is met, or none
is declared; 1 an unmet target; 2 an indeterminate target or a target that does not apply; 3 a target reads a
quantity outside its model, or the run did not complete (execution PARTIAL: a compute budget was reached); 4
invalid input or artifact, or a FAILED or CANCELLED run. requirement_outcome is the class of the declared targets'
assessments; a PARTIAL run is never PASS or NOT_APPLICABLE (UNKNOWN when its targets are met or none is declared),
and outcome.execution names the budgets it reached. The aggregate status is in "status".
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stdout
import contextvars
import json
from pathlib import Path
import sys

from ._engine import dependency_error

PROG = "os-optics-kit summary"

EXIT_CODES = {
    "PASS": 0, "WARNING": 0, "NOT_APPLICABLE": 0,
    "FAIL": 1, "UNKNOWN": 2, "OUT_OF_MODEL_SCOPE": 3, "OUT_OF_MODEL": 3, "ERROR": 4,
}
ERROR_EXIT = 4
#: Exit code of a run whose calculation did not complete (execution ``PARTIAL``: a compute budget was reached),
#: whatever its class: the os-optics ``requirements`` exit contract's code for an incomplete calculation.
EXECUTION_INCOMPLETE_EXIT = 3


def requirement_exit_code(label, execution=None, exit_codes=None):
    """Exit code of a run's class or status ``label`` (a key of ``exit_codes``, default ``EXIT_CODES``) under the
    os-optics ``requirements`` exit contract: ``EXECUTION_INCOMPLETE_EXIT`` when ``execution`` (the run's
    ``outcome.execution``) is ``PARTIAL`` and the label is not an error, else the label's code."""
    code = (exit_codes or EXIT_CODES)[label]
    if code != ERROR_EXIT and isinstance(execution, dict) and execution.get("outcome") == "PARTIAL":
        return EXECUTION_INCOMPLETE_EXIT
    return code


#: where --help goes while a command's own prints are redirected to stderr (set by ``json_cli_stdout``)
_HELP_STREAM = contextvars.ContextVar("os_optics_kit_help_stream", default=None)


class ArgumentParser(argparse.ArgumentParser):
    """Keep usage errors distinct from an UNKNOWN scientific result (a usage error raises ValueError: exit 4);
    --help goes to the command's real stdout."""

    def error(self, message):
        raise ValueError(message)

    def print_help(self, file=None):
        super().print_help(file if file is not None else (_HELP_STREAM.get() or sys.stdout))


@contextmanager
def json_cli_stdout():
    """Send the workflow's own prints to stderr (stdout carries one JSON document); --help stays on stdout."""
    token = _HELP_STREAM.set(sys.stdout)
    try:
        with redirect_stdout(sys.stderr):
            yield
    finally:
        _HELP_STREAM.reset(token)


def exit_status(exc):
    """Exit code of a ``SystemExit`` raised inside a command (argparse --help: 0)."""
    if exc.code is None:
        return 0
    return exc.code if isinstance(exc.code, int) else ERROR_EXIT


def error_document(exc):
    """``{"code", "message", ...}`` of a failed command; missing dependencies get their own stable codes."""
    return dependency_error(exc) or {"code": getattr(exc, "code", None) or type(exc).__name__, "message": str(exc)}


def summarize_results(results, *, artifacts=None, details=False):
    """Return JSON-safe results using the public RunResults API; never simulate.

    All targets and diagnostics are retained. Only the power overview is reduced;
    complete power entries and rays remain in the supplied artifact files. ``integrity``
    (``checked``, ``consistent``, ``problems``) states whether the run's files match their recorded hashes; a run
    that does not verify is summarized as stored, with that record.
    """
    description = results.describe()
    integrity = description["integrity"]
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
    from .workflow_common import requirement_outcome as settle

    execution = (compact.get("outcome") or {}).get("execution") or {}
    requirement_outcome = settle(status, execution, compact["model_scope_issues"], targets)
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
    """Emit JSON using the caller's declared outcome field for the exit code; for ``requirement_outcome`` a run whose
    ``outcome.execution`` is PARTIAL exits ``EXECUTION_INCOMPLETE_EXIT`` (``requirement_exit_code``)."""
    try:
        with json_cli_stdout():
            summary = workflow()
        if exit_status_key == "requirement_outcome":
            outcome = summary.get("outcome")
            code = requirement_exit_code(summary[exit_status_key],
                                         outcome.get("execution") if isinstance(outcome, dict) else None)
        else:
            code = EXIT_CODES[summary[exit_status_key]]
        output = json.dumps(summary, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    except SystemExit as exc:  # --help
        return exit_status(exc)
    except Exception as exc:  # noqa: BLE001 - every failure becomes a JSON error
        print(json.dumps({"error": error_document(exc)}, ensure_ascii=True), file=sys.stderr)
        return ERROR_EXIT
    print(output)
    return code


def main(argv=None):
    def workflow():
        parser = ArgumentParser(prog=PROG, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
        parser.add_argument("directory", type=Path, help="Saved local run directory")
        parser.add_argument("--details", action="store_true", help="Include the complete power ledger")
        args = parser.parse_args(argv)
        summary = summarize_run(args.directory, details=args.details)
        integrity = summary["integrity"]
        if integrity["checked"] and not integrity["consistent"]:
            print(json.dumps({"warning": {"code": "RUN_INTEGRITY_FAILED", "problems": integrity["problems"],
                                          "check": f'os-optics project verify "{args.directory}"'}},
                             ensure_ascii=True, separators=(",", ":")), file=sys.stderr)
        return summary

    return emit_cli(workflow)


if __name__ == "__main__":
    raise SystemExit(main())
