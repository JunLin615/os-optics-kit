"""Catalog integration check and same-run output comparison.

Run from the repository root: python -m tests.check_beam_summary
Requires the published package and an available (or cached) optical catalog.
"""

import argparse
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import sys
from unittest.mock import patch

from os_optics.catalog import CatalogError
from os_optics.local import LocalRunError
from os_optics.results import load_run

from examples.beam_expander import run_example
from examples.result_summary import summarize_run


def snapshot(directory):
    return {path.name: (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
            for path in directory.iterdir() if path.is_file()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("run-summary-check"))
    directory = parser.parse_args(argv).out
    summary = run_example(directory)
    results = load_run(directory)
    # Reproduce the original example's JSON layout using this exact saved result.
    legacy = {
        "status": summary["status"],
        "targets": [{"id": t.target_id, "status": t.status.value, "value": t.value}
                    for t in results.result.targets],
        "power_budget": results.power_budget(),
        "run_directory": str(directory),
        "arrivals_csv": summary["exports"]["arrivals_csv"],
        "view_command": summary["view_command"],
    }
    before = snapshot(directory)
    forbidden = AssertionError("Saved-result inspection must not simulate")
    with patch("os_optics.validate", side_effect=forbidden), \
         patch("os_optics.validate_with_ray_paths", side_effect=forbidden), \
         patch("os_optics.local.run_optical_project", side_effect=forbidden):
        compact = summarize_run(directory)
        detailed = summarize_run(directory, details=True)
    assert before == snapshot(directory), "Inspection modified saved artifacts"
    assert compact["targets"] == summary["targets"]
    assert detailed["power_budget_details"] == results.power_budget()
    assert compact["integrity"]["consistent"] is True
    assert all(Path(p).exists() for p in summary["artifacts"].values())
    assert all(Path(p).exists() for p in summary["exports"].values())
    old_bytes = len((json.dumps(legacy, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    new_bytes = len((json.dumps(summary, separators=(",", ":"), ensure_ascii=True) + "\n").encode("utf-8"))
    # Historical byte counts remain descriptive: current summaries carry richer scientific semantics.
    old_compact_bytes = len((json.dumps(legacy, separators=(",", ":")) + "\n").encode("utf-8"))
    compact_bytes = len((json.dumps(compact, separators=(",", ":"), ensure_ascii=True) + "\n").encode("utf-8"))
    detailed_bytes = len((json.dumps(detailed, separators=(",", ":"), ensure_ascii=True) + "\n").encode("utf-8"))
    assert compact_bytes < detailed_bytes, "Default summary must omit the full power ledger"
    assert "power_budget_details" not in compact
    assert {key: value for key, value in detailed.items() if key != "power_budget_details"} == compact
    assert all(key in compact for key in ("outcome", "requirement_outcome", "observations", "targets"))
    report = {
        "engine_version": version("os-optics"),
        "catalog_version": summary["identity"]["catalog_version"],
        "result_hash": summary["identity"]["result_hash"],
        "status": summary["status"],
        "legacy_stdout_utf8_bytes_lf": old_bytes,
        "legacy_minified_utf8_bytes_lf": old_compact_bytes,
        "summary_stdout_utf8_bytes_lf": new_bytes,
        "saved_summary_utf8_bytes_lf": compact_bytes,
        "saved_summary_details_utf8_bytes_lf": detailed_bytes,
        "saved_inspection_unchanged": True,
        "details_match_full_power_budget": True,
    }
    (directory / "summary-check.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (CatalogError, LocalRunError) as exc:
        # Preserve structured catalog/model failures for the alignment report.
        print(json.dumps(exc.as_dict()), file=sys.stderr)
        raise SystemExit(4)
