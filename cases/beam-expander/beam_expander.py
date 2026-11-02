"""Run a catalog-based beam expander and export its results.

Usage: python examples/beam_expander.py --out run-example [--details]
"""

from pathlib import Path

from os_optics.local import example_project_of_kind, run_optical_project
from os_optics.results import load_run

if __package__:
    from .result_summary import ArgumentParser, emit_cli, summarize_run
else:
    from result_summary import ArgumentParser, emit_cli, summarize_run


def run_example(output_dir=Path("run-example"), *, details=False):
    """Run once, preserve the complete native artifacts, and summarize them."""
    output_dir = Path(output_dir)
    project = example_project_of_kind("beam_expander")
    run_optical_project(project, output_dir=output_dir)
    summary = summarize_run(output_dir, details=details)
    results = load_run(output_dir)
    csv_path = output_dir.parent / f"{output_dir.name}-arrivals.csv"
    csv_path.write_bytes(results.export("arrivals", "csv"))
    summary["exports"] = {"arrivals_csv": str(csv_path)}
    return summary


def main(argv=None):
    def workflow():
        parser = ArgumentParser(description=__doc__)
        parser.add_argument("--out", type=Path, default=Path("run-example"), help="Run artifact directory")
        parser.add_argument("--details", action="store_true", help="Include the complete power ledger")
        args = parser.parse_args(argv)
        return run_example(args.out, details=args.details)

    return emit_cli(workflow)


if __name__ == "__main__":
    raise SystemExit(main())
