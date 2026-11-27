"""``os-optics-kit``: one command for the tools, the case collection and the agent skills.

Every subcommand is a module of this package with ``main(argv) -> int``; it is imported only when it runs, so
``os-optics-kit --help``, ``skills`` and ``cases list`` work without importing the engine.
"""

from __future__ import annotations

import importlib
import json
import sys

from ._version import __version__


def _engine_refusal(exc: BaseException) -> dict | None:
    """A structured refusal when ``exc`` comes from a missing or unsupported engine, else None.

    An engine older than the kit's requirement lacks APIs the kit calls; whatever that raises (``ImportError``,
    ``AttributeError``, ...) becomes ENGINE_UNSUPPORTED naming the required range, the installed version and the
    upgrade command, so no traceback reaches the user."""
    from . import _engine

    known = _engine.dependency_error(exc)
    installed = _engine.engine_version()
    if known is not None and known.get("code") != "ENGINE_UNSUPPORTED":
        return known
    if installed is None or _engine.satisfies(installed):
        return known
    requirement = f"{_engine.ENGINE_DISTRIBUTION}{_engine.ENGINE_REQUIREMENT}"
    upgrade = f'python -m pip install --upgrade "{requirement}"'
    return {"code": "ENGINE_UNSUPPORTED",
            "message": f"this kit requires {requirement}; installed: {_engine.ENGINE_DISTRIBUTION} {installed}. "
                       f"Upgrade with: {upgrade}",
            "requirement": requirement, "installed": installed, "upgrade": upgrade}

# subcommand: (module, one-line summary). Keep in step with skills.json (each tool has a skill of the same name).
COMMANDS: dict[str, tuple[str, str]] = {
    "summary": ("result_summary", "compact JSON summary of a run directory, with an exit code per outcome"),
    "edit": ("edit_project", "describe a Project's objects and apply edits by ID, atomically"),
    "sweep": ("sweep_parameters", "run a parameter sweep from a JSON spec, one saved run per sample"),
    "compare": ("compare_runs", "compare saved runs by target and plane ID, with freshness"),
    "plot": ("plot_results", "plot sweep targets, power distribution, bars and histograms (needs the plot extra)"),
    "report": ("build_report", "assemble a Markdown/JSON report from a study directory"),
    "sensitivity": ("sensitivity_analysis", "one-at-a-time sensitivity of selected targets"),
    "tolerance": ("tolerance_study", "seeded tolerance sampling under declared distributions"),
    "candidates": ("catalog_candidates", "rank catalog products for a requirement"),
    "measurements": ("analyze_measurements", "clean, fit and reference-normalize detector CSV data"),
    "fans": ("ray_fans", "ray fans: transverse/longitudinal aberration and OPD per field and wavelength"),
    "field": ("field_aberrations", "field curvature and distortion per field and wavelength"),
    "sfr": ("sfr", "slanted-edge SFR (ISO 12233 e-SFR) of an image, optionally beside a design MTF"),
    "study": ("study_tool", "run, resume, inspect and aggregate planned studies, in parallel worker processes"),
    "mtf": ("mtf", "optical MTF curves of a run's imaging analysis: CSV, JSON and one figure per line"),
    "field-points": (
        "field_points",
        "turned-source field points of a Project: chief-ray hit, angle and MTF at a fixed plane",
    ),
    "optimize": (
        "optimize",
        "bounded optimization of a Project from a JSON spec, with history and the best feasible Project",
    ),
    "zoom": ("zoom_study", "two moving groups' positions per focal length with the focus held on a fixed image plane"),
    "render": (
        "render_views",
        "render reproducible 3D figures with camera angles, roll and framing",
    ),
    "present": (
        "present",
        "presentation material from saved results: themed figures, comparisons, animations, gallery, bundle",
    ),
    "view": (
        "viewer_tool",
        "local read-only live viewer for a native Project JSON or saved run directory",
    ),
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
    program = sys.argv[0] if sys.argv else None
    # argparse's default prog: a parser without an explicit prog shows "os-optics-kit NAME" in its usage
    sys.argv[:1] = [f"os-optics-kit {name}"]
    try:
        result = module.main(rest)
    except SystemExit as exc:  # a subcommand's argparse --help / usage exit
        if exc.code is None:
            return 0
        return exc.code if isinstance(exc.code, int) else 4
    except Exception as exc:
        refusal = _engine_refusal(exc)
        if refusal is None:
            raise
        print(json.dumps({"error": refusal}), file=sys.stderr)
        return 4
    finally:
        sys.argv[:1] = [program] if program is not None else []
    return int(result or 0)


if __name__ == "__main__":
    raise SystemExit(main())
