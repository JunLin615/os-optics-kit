"""``os-optics-kit``: one command for the tools, the case collection and the agent skills.

Every subcommand is a module of this package with ``main(argv) -> int``; it is imported only when it runs, so
``os-optics-kit --help``, ``skills`` and ``cases list`` work without importing the engine.
"""

from __future__ import annotations

import importlib
import sys

from ._version import __version__

# subcommand: (module, one-line summary). Keep in step with skills.json (each tool has a skill of the same name).
COMMANDS: dict[str, tuple[str, str]] = {
    "summary": ("result_summary", "compact JSON summary of a run directory, with an exit code per outcome"),
    "edit": ("edit_project", "describe a Project's objects and apply edits by ID, atomically"),
    "sweep": ("sweep_parameters", "run a parameter sweep from a JSON spec, one saved run per sample"),
    "compare": ("compare_runs", "compare saved runs by target and plane ID, with freshness"),
    "plot": ("plot_results", "plot sweep targets, power distribution, bar charts and histograms (needs the plot extra)"),
    "report": ("build_report", "assemble a Markdown/JSON report from a study directory"),
    "sensitivity": ("sensitivity_analysis", "one-at-a-time sensitivity of selected targets"),
    "tolerance": ("tolerance_study", "seeded tolerance sampling under declared distributions"),
    "candidates": ("catalog_candidates", "rank catalog products for a requirement"),
    "measurements": ("analyze_measurements", "clean, fit and reference-normalize detector CSV data"),
    "cases": ("cases", "list, show, copy and verify the engine-reproducible cases"),
    "skills": ("skills", "list, show and install the vendor-neutral agent skills"),
    "contrib": ("contrib", "prepare a case or tool contribution as a PR-ready patch or bundle"),
}


def _usage() -> str:
    width = max(len(name) for name in COMMANDS)
    lines = [
        f"usage: os-optics-kit <command> [args]   (os-optics-kit {__version__})",
        "",
        "Tools, cases and agent skills for OS Optics. 'os-optics-kit <command> --help' for details;",
        "'os-optics-kit skills list' for the skill index an agent loads first.",
        "",
        "commands:",
    ]
    lines += [f"  {name.ljust(width)}  {summary}" for name, (_, summary) in COMMANDS.items()]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        print(_usage())
        return 0
    if args[0] in ("-V", "--version"):
        print(f"os-optics-kit {__version__}")
        return 0
    name, rest = args[0], args[1:]
    if name not in COMMANDS:
        print(f"os-optics-kit: unknown command {name!r}\n\n{_usage()}", file=sys.stderr)
        return 4
    module = importlib.import_module(f".{COMMANDS[name][0]}", __package__)
    try:
        result = module.main(rest)
    except SystemExit as exc:  # a subcommand's argparse --help / usage exit
        if exc.code is None:
            return 0
        return exc.code if isinstance(exc.code, int) else 4
    return int(result or 0)


if __name__ == "__main__":
    raise SystemExit(main())
