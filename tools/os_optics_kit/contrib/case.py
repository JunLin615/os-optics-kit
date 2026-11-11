"""``os-optics-kit contrib case``: turn a finished design into a PR-ready case of the kit.

PROJECT.json is normalized to a ProjectDocumentV2 by the engine, checked, run and read back
(``project check`` -> ``project run`` -> ``results targets``). The case is classified with --class, --domain and
--tags (values: ``os-optics-kit cases vocabulary``); --provenance, --publication and --source state where it comes
from. The case directory (project.json, CASE.md, README.md, README.zh-CN.md, expected.json, images.json, images/)
goes into a bundle with a ``git format-patch`` patch against the kit checkout, PR_BODY.md and bundle.json. Edit
the prose in BUNDLE/case, then ``--refresh BUNDLE`` re-verifies, re-renders the system views and rebuilds the patch.
``--open-pr`` opens the pull request with your own ``gh`` login; ``--dry-run`` stays on this device.

Case images need no browser and no ``render`` extra: the system views are the engine's own snapshots,
``os-optics view RUN --no-browser --snapshot DIR --views iso`` (3D), plus ``--section ALL --frame=scene`` (2D
section) for a lens, recorded in images.json with the options used. ``--refresh`` renders the recorded views again,
plus any view that was not rendered before and the views the class needs (a snapshot view first, the section of a
lens), and updates the images of both READMEs. In a kit checkout the bare ``os-optics-kit cases images NAME``
re-renders the views recorded in images.json (the same defaults when none is recorded); the READMEs are then edited
by hand.
The kit's ``render`` and ``present`` (presentation-grade figures) need ``os-optics-kit[render]`` and Chrome or
Chromium; a case never needs them.

Exit codes: 0 bundle ready (or PR opened), 1 verification failed (the engine run did not complete) or a system view
was not rendered (status INCOMPLETE: the bundle holds the case files, no patch), 4 usage error or refusal (JSON with
status ERROR on stdout; invalid classification values: BAD_ARGUMENT with the allowed values).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from .. import _frontmatter as fm
from .. import case_taxonomy as taxonomy
from .. import cases as kit_cases
from . import _common as C
from . import _templates as T

REQUIRED_SECTIONS = list(kit_cases.SECTIONS)
#: run outputs never packaged, beyond the ones ``cases.check_case`` already refuses (manifest.json,
#: engine_request.json, simulation_result.json and every .glb)
RUN_FILES = {"ray_data.json", "local_validation.json", "optical_scene.json"}
MAX_TABLE_ROWS = 12
#: the front matter codes of ``cases.check_front_matter`` that judge the classification options
CLASSIFICATION_CODES = {"FRONT_MATTER_CLASS": "class", "FRONT_MATTER_DOMAIN": "domain",
                        "FRONT_MATTER_PROVENANCE": "provenance", "FRONT_MATTER_PUBLICATION": "publication",
                        "FRONT_MATTER_SOURCE": "source", "FRONT_MATTER_TAGS": "tags"}
#: the language line under the title of each README: the pair links each other (``cases index --check``
#: README_PAIR)
LANGUAGE_LINKS = {"en": "English · [简体中文](README.zh-CN.md)", "zh-CN": "[English](README.md) · 简体中文"}
#: README captions of the system views (English, Simplified Chinese)
CAPTIONS = {
    "iso": ("3D view of the design: the components and the traced beam", "设计的三维视图：元件与追迹光束"),
    "section": ("Section through the optical axis: the components and the traced rays", "沿光轴的截面：元件与追迹光线"),
    "camera": ("3D view of the design from the chosen camera", "设计在所选视角下的三维视图"),
}


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise C.ContribError("USAGE", message, hint="os-optics-kit contrib case --help")


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="os-optics-kit contrib case", description=__doc__,
                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project", nargs="?", help="the finished design: a ProjectDocumentV2 JSON file with targets")
    parser.add_argument("--name", help="case name, kebab-case (becomes cases/NAME)")
    parser.add_argument("--title", help="one-line title (default: from the name)")
    parser.add_argument("--summary", help="what the case shows, at most 300 characters (default: generated)")
    parser.add_argument("--class", dest="case_class", metavar="CLASS",
                        help="required: " + ", ".join(taxonomy.values(taxonomy.CLASSES)))
    parser.add_argument("--domain", help=f"required: {taxonomy.DOMAIN_MIN}-{taxonomy.DOMAIN_MAX} comma-separated "
                                         "domains, primary first ('os-optics-kit cases vocabulary' lists them)")
    parser.add_argument("--tags", help=f"required: {taxonomy.TAG_MIN}-{taxonomy.TAG_MAX} comma-separated tags from "
                                       "the tag vocabulary")
    parser.add_argument("--provenance", help="where the design comes from: "
                                             + ", ".join(taxonomy.values(taxonomy.PROVENANCE))
                                             + f" (default {taxonomy.SELF_BUILT})")
    parser.add_argument("--publication", help="publication basis: " + ", ".join(taxonomy.values(taxonomy.PUBLICATION))
                                              + f" (default {taxonomy.OWN_WORK}; self-built goes with own-work)")
    parser.add_argument("--source", help="citation of the third-party origin, at most "
                                         f"{taxonomy.SOURCE_MAX_CHARS} characters (required unless self-built)")
    parser.add_argument("--tools", help="comma-separated os-optics-kit commands the case uses (default: none)")
    parser.add_argument("--repo", help="os-optics-kit Git checkout to patch (default: the checkout around the "
                                       "current directory; without one, only the case files are built)")
    parser.add_argument("--base", help="commit of the checkout the patch applies to (default: HEAD)")
    parser.add_argument("--out", help="bundle directory (default: ./contrib-case-NAME)")
    parser.add_argument("--catalog", help="catalog release file to run with (imported into a private store; your "
                                          "catalog store is not changed)")
    parser.add_argument("--refresh", metavar="BUNDLE", help="re-verify an edited bundle and rebuild its patch")
    parser.add_argument("--dry-run", action="store_true", help="stay on this device: no catalog download, no PR")
    parser.add_argument("--open-pr", action="store_true", help="fork, push and open the PR with your own gh login")
    parser.add_argument("--force", action="store_true", help="regenerate an existing bundle from scratch")
    parser.add_argument("--upstream", default=C.UPSTREAM, help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        result = contribute_case(args)
    except C.ContribError as exc:
        C.emit(exc.as_dict())
        return exc.exit_code
    C.emit(result)
    return C.EXIT_VERIFICATION if result["status"] == "INCOMPLETE" else C.EXIT_OK


# ---------------------------------------------------------------------------------------------- classification

def classification(args) -> dict[str, str]:
    """The classification front matter values of the options (``class`` .. ``tags`` in CASE.md order, ``source``
    only when given), checked by the same rules as ``cases index --check``; refuses BAD_ARGUMENT naming the
    allowed values. Runs before any engine work."""
    case_class = (args.case_class or "").strip().lower()
    domains = [value.lower() for value in C.split_list(args.domain)]
    tags = [value.lower() for value in C.split_list(args.tags)]
    values = {"class": case_class, "domain": ", ".join(domains),
              "provenance": (args.provenance or taxonomy.SELF_BUILT).strip().lower(),
              "publication": (args.publication or taxonomy.OWN_WORK).strip().lower()}
    source = one_line(args.source or "")
    if source:
        values["source"] = source
    values["tags"] = ", ".join(tags)
    problems, fields = [], []
    for field, given in (("class", case_class), ("domain", domains), ("tags", tags)):
        if not given:
            problems.append(f"--{field} is required")
            fields.append(field)
    for code, message in kit_cases.check_front_matter({k: v for k, v in values.items() if v}):
        if code in CLASSIFICATION_CODES:
            problems.append(f"--{CLASSIFICATION_CODES[code]}: {message}")
            fields.append(CLASSIFICATION_CODES[code])
    if problems:
        allowed = {field: list(_allowed_values(field)) for field in dict.fromkeys(fields)}
        raise C.ContribError("BAD_ARGUMENT", "the case classification options are missing or not in the vocabulary",
                             problems=problems, hint="os-optics-kit cases vocabulary lists every value",
                             details={"allowed": allowed})
    return values


def _allowed_values(field: str) -> tuple[str, ...]:
    if field == "source":
        return (f"a citation of at most {taxonomy.SOURCE_MAX_CHARS} characters, only when provenance is not "
                f"{taxonomy.SELF_BUILT}",)
    terms = {"class": taxonomy.CLASSES, "domain": taxonomy.DOMAINS, "provenance": taxonomy.PROVENANCE,
             "publication": taxonomy.PUBLICATION, "tags": taxonomy.TAGS}[field]
    return taxonomy.values(terms)


# ------------------------------------------------------------------------------------------------------ engine

def _engine():
    try:
        import os_optics.local as local
        from importlib.metadata import version
        return local, version("os-optics")
    except ImportError as exc:
        raise C.ContribError("ENGINE_NOT_INSTALLED", f"os-optics is not importable ({exc})",
                             hint="pip install os-optics-kit (it installs os-optics)") from None


def load_and_normalize(data: bytes) -> tuple[object, str]:
    """``(ProjectDocumentV2, normalized text)``; refuses anything that is not a v2 Project with targets."""
    local, _ = _engine()
    try:
        project = local.load_project(data)
    except local.LocalRunError as exc:
        raise C.ContribError("PROJECT_INVALID", "not a valid ProjectDocumentV2", problems=[
            {"code": str(getattr(p.code, "value", p.code)), "message": p.message, "location": p.location}
            for p in exc.problems]) from None
    if not project.targets:
        raise C.ContribError("PROJECT_HAS_NO_TARGETS", "a case states what it verifies: the Project declares no targets",
                             hint="add targets (os-optics results metrics lists the metrics) and run again")
    text = json.dumps(project.payload(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    return project, text


def catalog_pins(project) -> list[str]:
    pins = set()
    for component in project.components:
        product = getattr(component, "product", None)
        if product is not None and getattr(product, "catalog_version", None):
            pins.add(product.catalog_version)
    return sorted(pins)


def _engine_cmd(*args: str) -> list[str]:
    return [sys.executable, "-m", "os_optics", *args]


def _json_out(result, step: str) -> dict:
    try:
        return json.loads(result.stdout.decode("utf-8"))
    except ValueError:
        raise C.ContribError("ENGINE_OUTPUT_UNREADABLE", f"{step} printed no JSON (exit {result.returncode})",
                             problems=[C._tail(result.stderr or result.stdout)], exit_code=C.EXIT_VERIFICATION) from None


def engine_env(args, workdir: Path, pins: list[str]) -> dict:
    env = dict(os.environ)
    if args.dry_run or args.catalog:
        env["OPENSCI_CATALOG_CHECK_INTERVAL"] = "0"
    if args.catalog:
        catalog_file = Path(args.catalog).resolve()
        if not catalog_file.is_file():
            raise C.ContribError("CATALOG_FILE_MISSING", f"--catalog {args.catalog} is not a file")
        env["OPENSCI_CATALOG_DIR"] = str(workdir / "catalog")
        imported = C.run(_engine_cmd("catalog", "import", str(catalog_file)), env=env)
        if imported.returncode != 0:
            raise C.ContribError("CATALOG_IMPORT_FAILED", f"os-optics catalog import {args.catalog} failed",
                                 problems=[C._tail(imported.stdout or imported.stderr)])
    if pins and (args.dry_run or args.catalog):
        status = _json_out(C.run(_engine_cmd("catalog", "status"), env=env), "os-optics catalog status")
        stored = {release.get("catalog_version") for release in status.get("releases", [])}
        missing = [pin for pin in pins if pin not in stored]
        if missing:
            raise C.ContribError(
                "CATALOG_RELEASE_NOT_STORED", f"catalog release {missing[0]} pinned by the Project is not available "
                "offline", hint=f"os-optics catalog sync --release {missing[0]}, or pass --catalog FILE")
    return env


def verify_project(case_dir: Path, run_dir: Path, env: dict) -> dict:
    """``project check`` -> ``project run`` -> ``results targets`` on ``case_dir/project.json``."""
    check_result = C.run(_engine_cmd("project", "check", "project.json"), cwd=case_dir, env=env)
    check = _json_out(check_result, "os-optics project check")
    if check_result.returncode != 0 or check.get("status") != "ACCEPTED":
        raise C.ContribError("PROJECT_REFUSED", "os-optics project check did not accept the Project",
                             problems=check.get("problems") or [check.get("status")])
    run_result = C.run(_engine_cmd("project", "run", "project.json", "--out-dir", str(run_dir)), cwd=case_dir, env=env)
    if run_result.returncode not in (0, 1, 2, 3):
        raise C.ContribError("RUN_FAILED", f"os-optics project run exited with {run_result.returncode}",
                             problems=[C._tail(run_result.stdout or run_result.stderr)], exit_code=C.EXIT_VERIFICATION)
    targets_result = C.run(_engine_cmd("results", "targets", str(run_dir)), env=env)
    targets = _json_out(targets_result, "os-optics results targets")
    if targets_result.returncode != 0:
        raise C.ContribError("RESULTS_UNREADABLE", "os-optics results targets could not read the run",
                             problems=[targets.get("error") or targets], exit_code=C.EXIT_VERIFICATION)
    execution = (targets.get("outcome") or {}).get("execution") or {}
    if not execution.get("complete") or execution.get("outcome") != "COMPLETED":
        raise C.ContribError("RUN_INCOMPLETE", "the engine run did not complete; nothing was packaged",
                             problems=[{k: execution.get(k) for k in ("outcome", "termination", "reason")}],
                             exit_code=C.EXIT_VERIFICATION)
    run_info = targets.get("run") or {}
    return {
        "check": check,
        "targets": targets,
        "engine": run_info.get("engine_version"),
        "engine_build_id": run_info.get("engine_build_id"),
        "catalog": run_info.get("catalog_version") or check.get("catalog_version"),
        "status": targets.get("validation_status") or targets.get("engine_status"),
        "execution": execution.get("outcome"),
    }


# ------------------------------------------------------------------------------------------------------ images
# The system views go through the same engine path as ``os-optics-kit cases images``: one browser-free
# ``os-optics view RUN --no-browser --snapshot DIR <options>`` call per view, redrawn smaller when the PNG is over
# the budget, recorded in images.json with exactly the options used and the run identity of the verified run.

#: a planned system view: (view, argument, size, frame)
View = tuple[str, str, str, str | None]


def default_views(case_class: str | None) -> list[View]:
    """``[(view, argument, size, frame)]`` of a new case: ``iso``, plus ``section ALL --frame=scene`` for a lens."""
    plan = kit_cases.plan_views({"class": case_class})
    return [(view, argument, kit_cases.SECTION_SIZE if view == taxonomy.SECTION_VIEW else kit_cases.SNAPSHOT_SIZE,
             "scene" if view == taxonomy.SECTION_VIEW else None) for view, argument in plan]


def _option(options: list, name: str) -> str | None:
    """The value of option ``name`` in recorded ``options``: the one token ``NAME=VALUE`` (how a frame is recorded,
    so that a negative box frame stays one argument) or ``NAME VALUE``."""
    for number, option in enumerate(options):
        if not isinstance(option, str):
            continue
        if option.startswith(name + "="):
            return option[len(name) + 1:]
        if option == name and number + 1 < len(options):
            return str(options[number + 1])
    return None


def _view_file(entry: View) -> str:
    return kit_cases._image_file(entry[0], entry[1])


def recorded_views(document: dict | None) -> list[View] | None:
    """The system views recorded in a bundle's images.json, as ``[(view, argument, size, frame)]`` (None when it
    records none)."""
    plan = []
    for record in (document or {}).get("images") or []:
        if not isinstance(record, dict) or record.get("kind") != taxonomy.SYSTEM_VIEW_KIND:
            continue
        options = record.get("options")
        view = record.get("view")
        if not isinstance(view, str) or not isinstance(options, list):
            continue
        flag = {taxonomy.SECTION_VIEW: "--section", taxonomy.CAMERA_VIEW: "--camera"}.get(view, "--views")
        argument = _option(options, flag)
        if argument is None:
            continue
        default_size = kit_cases.SECTION_SIZE if view == taxonomy.SECTION_VIEW else kit_cases.SNAPSHOT_SIZE
        plan.append((view, argument, _option(options, "--size") or default_size, _option(options, "--frame")))
    return plan or None


def unrendered_entries(missing: list[tuple[int, View]]) -> list[dict]:
    """bundle.json ``unrendered_views``: the planned views that were not rendered, with their place in the plan."""
    return [{"index": index, "view": view, "argument": argument, "size": size, "frame": frame}
            for index, (view, argument, size, frame) in missing]


def _unrendered_views(entries) -> list[tuple[int, View]]:
    views: list[tuple[int, View]] = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or not isinstance(entry.get("index"), int):
            continue
        fields = [entry.get(key) for key in ("view", "argument", "size")]
        frame = entry.get("frame")
        if all(isinstance(value, str) and value for value in fields) and (frame is None or isinstance(frame, str)):
            views.append((entry["index"], (str(fields[0]), str(fields[1]), str(fields[2]), frame)))
    return sorted(views, key=lambda item: item[0])


def refresh_plan(recorded: list[View] | None, unrendered, case_class: str | None) -> list[View]:
    """The views a --refresh renders: the ones images.json records, the ones the previous run could not render
    (bundle.json ``unrendered_views``, back at their place in the plan), and what the case class needs: a snapshot
    view first (``iso`` when there is none) and, for a lens, the default section ``section ALL --frame=scene``."""
    plan = list(recorded or [])
    for index, entry in _unrendered_views(unrendered):
        if _view_file(entry) not in [_view_file(view) for view in plan]:
            plan.insert(min(index, len(plan)), entry)
    defaults = default_views(case_class)
    snapshots = [view for view in plan if kit_cases.is_snapshot_view(view[0])]
    if not snapshots:
        plan.insert(0, defaults[0])
    elif not kit_cases.is_snapshot_view(plan[0][0]):
        plan.remove(snapshots[0])
        plan.insert(0, snapshots[0])
    for default in defaults:
        if default[0] == taxonomy.SECTION_VIEW and not any(view[0] == taxonomy.SECTION_VIEW for view in plan):
            plan.append(default)
    unique: dict[str, View] = {}
    for view in plan:
        unique.setdefault(_view_file(view), view)
    return list(unique.values())


def images_command(name: str, plan: list[View]) -> str:
    """The ``os-optics-kit cases images NAME`` command of a kit checkout that renders and records ``plan``."""
    parts = ["os-optics-kit", "cases", "images", name]
    sizes: dict[str, str] = {}
    for view, argument, size, frame in plan:
        if view == taxonomy.SECTION_VIEW:
            parts += ["--section", argument] + ([f"--frame={frame}"] if frame else [])
            if size != kit_cases.SECTION_SIZE:
                sizes.setdefault("--section-size", size)
        else:
            parts += ["--camera", argument] if view == taxonomy.CAMERA_VIEW else ["--view", argument]
            if size != kit_cases.SNAPSHOT_SIZE:
                sizes.setdefault("--size", size)
    for option, size in sizes.items():
        parts += [option, size]
    return " ".join(parts)


def _recovery(view: str, argument: str) -> str:
    flag = {taxonomy.SECTION_VIEW: "--section", taxonomy.CAMERA_VIEW: "--camera"}.get(view, "--views")
    return (f"render it without a browser or the render extra: 'os-optics view RUN --no-browser --snapshot DIR "
            f"{flag} {argument}' on a run of case/project.json shows the engine's error; fix the cause, then "
            "'os-optics-kit contrib case --refresh BUNDLE' renders this view again and puts it into both READMEs")


def render_system_views(name: str, plan: list[View], run_dir: Path, workdir: Path, case_dir: Path, env: dict,
                        warnings: list[str]) -> tuple[list[dict], list[tuple[int, View]]]:
    """Render ``plan`` from the verified run into ``case_dir/images`` -> (system-view records, the views not
    rendered with their place in ``plan``). A view the engine cannot draw within the budget is left out with an
    IMAGE_NOT_RENDERED warning."""
    work = workdir / "views"
    work.mkdir(exist_ok=True)
    records: list[dict] = []
    missing: list[tuple[int, View]] = []
    for index, (view, argument, size, frame) in enumerate(plan):
        file = kit_cases._image_file(view, argument)
        try:
            sizes = kit_cases._sizes(size, "--size")
            png, options = kit_cases._render_view(run_dir, work, view, argument, sizes, frame, env)
        except kit_cases.CaseError as exc:
            missing.append((index, (view, argument, size, frame)))
            warnings.append(f"IMAGE_NOT_RENDERED: {file} ({exc.code}: {exc}); {_recovery(view, argument)}")
            continue
        (case_dir / "images").mkdir(exist_ok=True)
        shutil.copyfile(png, case_dir / file)
        records.append({"file": file, "kind": taxonomy.SYSTEM_VIEW_KIND, "options": options,
                        "sha256": C.sha256_file(case_dir / file), "view": view})
    return records, missing


def record_images(name: str, case_dir: Path, run_dir: Path, workdir: Path, env: dict, warnings: list[str],
                  *, case_class: str | None, previous: dict | None,
                  unrendered=None) -> tuple[dict, list[tuple[int, View]]]:
    """Render the system views (a new case: the defaults of ``case_class``; a refresh: :func:`refresh_plan` of
    ``previous`` images.json and the ``unrendered`` views of bundle.json), keep the recorded figures after them and
    write images.json with the run identity of ``run_dir`` -> (document, the views not rendered)."""
    if previous is None and unrendered is None:
        plan = default_views(case_class)
    else:
        plan = refresh_plan(recorded_views(previous), unrendered, case_class)
    old_views = [r.get("file") for r in (previous or {}).get("images") or []
                 if isinstance(r, dict) and r.get("kind") == taxonomy.SYSTEM_VIEW_KIND]
    for old in old_views:
        if isinstance(old, str) and kit_cases._case_image(case_dir, old) and (case_dir / old).is_file():
            (case_dir / old).unlink()
    records, missing = render_system_views(name, plan, run_dir, workdir, case_dir, env, warnings)
    for figure in kit_cases._figures(previous):
        file = figure.get("file")
        if isinstance(file, str) and kit_cases._case_image(case_dir, file) and (case_dir / file).is_file():
            records.append({**figure, "sha256": C.sha256_file(case_dir / file)})
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    document = kit_cases.write_images_record(case_dir, records, kit_cases.run_identity(manifest))
    return document, missing


# ------------------------------------------------------------------------------------------------------- prose

def expected_document(verified: dict) -> dict:
    return {
        "schema": kit_cases.EXPECTED_SCHEMA,
        "schema_version": 1,
        "engine": verified["engine"],
        "catalog": verified["catalog"],
        "status": verified["status"],
        "targets": {t["target_id"]: t["assessment"] for t in verified["targets"].get("targets", [])},
    }


def _number(value) -> str:
    return f"{value:.6g}" if isinstance(value, (int, float)) else str(value)


def _bounds(bounds: dict, unit: str) -> str:
    unit = f" {unit}" if unit else ""
    if bounds.get("target_value") is not None:
        tolerance = bounds.get("tolerance")
        return f"{_number(bounds['target_value'])} ± {_number(tolerance)}{unit}" if tolerance is not None else \
            f"{_number(bounds['target_value'])}{unit}"
    low, high = bounds.get("minimum"), bounds.get("maximum")
    if low is not None and high is not None:
        return f"{_number(low)} to {_number(high)}{unit}"
    if low is not None:
        return f">= {_number(low)}{unit}"
    if high is not None:
        return f"<= {_number(high)}{unit}"
    return "-"


def _value(target: dict) -> str:
    value, unit = target.get("value"), target.get("unit") or ""
    if not isinstance(value, (int, float)):
        return "-"
    return f"{value:.6g} {unit}".strip()


def _rows(targets: list[dict], row) -> str:
    rows = [row(target) for target in targets[:MAX_TABLE_ROWS]]
    if len(targets) > MAX_TABLE_ROWS:
        rows.append(f"\n{len(targets) - MAX_TABLE_ROWS} more targets in `expected.json`.")
    return "\n".join(rows)


def targets_rows(targets: list[dict]) -> str:
    """Rows of the CASE.md targets table: Target | Metric | Plane | Bound | Verified."""
    return _rows(targets, lambda t: f"| `{t['target_id']}` | `{t.get('metric')}` | `{t.get('observation_id')}` | "
                                    f"{_bounds(t.get('bounds') or {}, t.get('unit') or '')} | "
                                    f"{_value(t)}, {t.get('assessment')} |")


def results_rows(targets: list[dict]) -> str:
    """Rows of the README.md results table: Target | Value | Bound | Assessment."""
    return _rows(targets, lambda t: f"| `{t['target_id']}` | {_value(t)} | "
                                    f"{_bounds(t.get('bounds') or {}, t.get('unit') or '')} | {t.get('assessment')} |")


def targets_table(targets: list[dict]) -> str:
    """The targets table of the pull-request description."""
    return ("| Target | Metric | Plane | Bound | Verified |\n| --- | --- | --- | --- | --- |\n"
            + targets_rows(targets))


def catalog_text(catalog: str | None, language: str = "en") -> str:
    if language == "zh-CN":
        return f"目录 {catalog}" if catalog else "不使用目录产品"
    return f"catalog {catalog}" if catalog else "no catalog products"


STANDARD_FILES = ("project.json", "expected.json", "CASE.md", "README.md", kit_cases.IMAGES_FILE)


def extra_files(case_dir: Path) -> str:
    """Bullets for the case files beyond the standard ones (project, expected outcome, pages, images)."""
    extra = [rel for rel in C.relative_listing(case_dir) if rel not in STANDARD_FILES
             and not rel.startswith("images/") and not re.fullmatch(r"README\..+\.md", rel)]
    return "\n".join(f"- `{rel}`" for rel in extra)


def caption(record: dict, language: str = "en") -> str:
    """README caption of one recorded image (``language`` en or zh-CN)."""
    zh = language == "zh-CN"
    if record.get("kind") == taxonomy.FIGURE_KIND:
        script = record.get("script")
        return f"由 `{script}` 绘制的结果图" if zh else f"Figure drawn by `{script}`"
    view = str(record.get("view"))
    if view in CAPTIONS:
        return CAPTIONS[view][1 if zh else 0]
    return f"设计的 {view} 视图（引擎快照）" if zh else f"{view.replace('_', ' ').capitalize()} view of the design"


def images_block(name: str, records: list[dict], language: str = "en") -> str:
    """The README images: every recorded image, in images.json order, with its caption."""
    if records:
        return "\n\n".join(f"![{caption(record, language)}]({record['file']})" for record in records)
    if language == "zh-CN":
        return ("尚未记录系统视图：引擎能够绘制后，运行 `os-optics-kit contrib case --refresh BUNDLE` 生成视图并放在"
                "此处（无需浏览器）。")
    return ("No system view is recorded yet: once the engine draws it, `os-optics-kit contrib case --refresh BUNDLE` "
            "renders it and puts it here (no browser needed).")


#: the line :func:`images_block` writes when no image is recorded (this version's text and earlier ones)
PLACEHOLDER_LINE = re.compile(r"^(?:No system view is recorded yet|尚未记录系统视图)[^\n]*$", re.M)
#: an image a README line shows: Markdown ``![caption](images/X.png)`` or HTML ``<img src="images/X.png">``
SHOWN = re.compile(r"!\[[^\]\n]*\]\((images/[^)\s]+)\)|<img\b[^>\n]*\bsrc=[\"'](images/[^\"'\s]+)[\"'][^>\n]*>")


def default_summary(project, title: str, verified: dict) -> str:
    targets = verified["targets"].get("targets", [])
    met = sum(1 for t in targets if t.get("assessment") == "MET")
    parts = len([c for c in project.components if getattr(c, "optical_role", None) != "source"])
    catalog = f"catalog release {verified['catalog']}" if verified["catalog"] else "declared models only"
    return (f"{title}: {parts} optical elements ({catalog}); {met} of {len(targets)} targets met, "
            f"run status {verified['status']}.")[:300]


def one_line(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def replace_images(text: str, name: str, records: list[dict], language: str = "en") -> str | None:
    """``text`` (a README) showing exactly ``records``, in order, or None when its images cannot be found.

    The images are one block of lines that each show one image (blank lines between them): the block is rebuilt in
    images.json order, keeping the line (and so the caption) of every image it already showed and writing the
    generated line for a new one. Without images, the generated "no system view is recorded yet" line is replaced.
    Any other README text is left as it is."""
    lines = text.split("\n")
    rows, fenced = [], False
    for number, line in enumerate(lines):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        elif not fenced and SHOWN.search(line):
            rows.append(number)
    if not rows:
        if not records:
            return text
        match = PLACEHOLDER_LINE.search(text)
        if match is None:
            return None
        return text[:match.start()] + images_block(name, records, language) + text[match.end():]
    first, last = rows[0], rows[-1]
    kept: dict[str, str] = {}
    for number in range(first, last + 1):
        line = lines[number]
        if number not in rows:
            if line.strip():
                return None  # prose between the images: the README is edited by hand
            continue
        match = SHOWN.fullmatch(line.strip())
        if match is None:
            return None  # text around the image, or several images on one line
        kept.setdefault(match.group(1) or match.group(2), line)
    block = "\n\n".join(kept.get(record["file"]) or images_block(name, [record], language) for record in records) \
        if records else images_block(name, [], language)
    return "\n".join(lines[:first] + block.split("\n") + lines[last + 1:])


def update_readme_images(name: str, case_dir: Path, records: list[dict]) -> list[str]:
    """On --refresh: make both READMEs show exactly ``records`` (the images.json images rendered now) with
    :func:`replace_images` -> the READMEs it could not update (their images are then edited by hand)."""
    files = [record["file"] for record in records]
    stuck = []
    for readme, language in zip(kit_cases.README_PAIR, ("en", "zh-CN")):
        path = case_dir / readme
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if kit_cases.shown_images(text) == files:
            continue
        updated = replace_images(text, name, records, language)
        if updated is None:
            stuck.append(readme)
        elif updated != text:
            C.write_text(path, updated)
    return stuck


def prose_values(name: str, title: str, summary: str, tools: list[str], classified: dict[str, str], verified: dict,
                 case_dir: Path, images: list[dict], language: str = "en") -> dict[str, str]:
    """Values of the ``{{key}}`` placeholders of templates/CASE.md and templates/README.case.md (``language``
    zh-CN: of templates/README.case.zh-CN.md). ``classified`` comes from :func:`classification`; ``source`` is
    empty when not given, and the template line ``source: {{source}}`` is then dropped."""
    targets = verified["targets"].get("targets", [])
    return {
        "name": name,
        "title": title,
        "summary": summary,
        "what_it_shows": summary,
        "class": classified["class"],
        "domain": classified["domain"],
        "provenance": classified["provenance"],
        "publication": classified["publication"],
        "source": classified.get("source", ""),
        "engine": verified["engine"],
        "catalog": verified["catalog"] or "none",
        "catalog_text": catalog_text(verified["catalog"], language),
        "tools": ", ".join(tools) if tools else "none",
        "tags": classified["tags"],
        "status": verified["status"],
        "targets_rows": targets_rows(targets),
        "results_rows": results_rows(targets),
        "images": images_block(name, images, language),
        "language_links": LANGUAGE_LINKS[language],
        "extra_files": extra_files(case_dir),
    }


# ------------------------------------------------------------------------------------------------- validation

IMAGE_CODES = ("IMAGES", "IMAGES_RECORD")


def validate_case_dir(case_dir: Path, name: str, verified: dict, commands: set[str]) -> list[str]:
    """Problems (``CODE: message``) of a case directory: every rule of ``cases index --check`` (``cases.check_case``,
    so a bundle that passes here also passes the index check), plus what contrib knows besides: the case name, the
    engine and catalog of the verified run, the tools of the patched checkout, run outputs and unfilled
    placeholders."""
    problems = [f"{p['code']}: {p['message']}" for p in kit_cases.check_case(case_dir)]
    files = C.relative_listing(case_dir)
    for rel in files:
        if Path(rel).name in RUN_FILES:
            problems.append(f"GENERATED_FILE: {rel}: run outputs are not part of a case")
    try:
        front, _body = fm.split((case_dir / "CASE.md").read_text(encoding="utf-8"))
    except (OSError, fm.FrontMatterError):
        return problems
    expect = {"name": name, "engine": verified["engine"], "catalog": verified["catalog"] or "none"}
    for key, value in expect.items():
        if front.get(key) and front[key] != value:
            problems.append(f"VERIFIED_RUN: CASE.md {key} is {front[key]!r}, expected {value!r}")
    unknown = [tool for tool in kit_cases._items(front.get("tools", "none"))
               if tool.removeprefix("os-optics-kit ") not in commands]
    if unknown:
        problems.append(f"FRONT_MATTER_TOOLS: tools {', '.join(unknown)} are not os-optics-kit commands of the "
                        "checkout")
    for label in ("CASE.md", *kit_cases.README_PAIR):
        path = case_dir / label
        if path.is_file() and C.PLACEHOLDER_RE.search(path.read_text(encoding="utf-8")):
            problems.append(f"PLACEHOLDER: {label} has an unfilled {{{{...}}}} placeholder")
    return problems


def rewrite_front_matter(case_md: str, updates: dict[str, str]) -> str:
    front, body = fm.split(case_md)
    front.update({k: v for k, v in updates.items() if k in front})
    return fm.render(front) + body


# ------------------------------------------------------------------------------------------------------- flow

def contribute_case(args) -> dict:
    warnings: list[str] = []
    classified: dict[str, str] = {}
    if args.refresh:
        bundle = Path(args.refresh).resolve()
        previous = C.load_bundle(bundle, "case")
        name = previous["name"]
        out = bundle
        source = bundle / "case" / "project.json"
        if any(getattr(args, key) for key in ("project", "name", "title", "summary", "tools", "tags", "case_class",
                                              "domain", "provenance", "publication", "source", "out")):
            raise C.ContribError("USAGE", "--refresh takes its name, classification, prose and files from the bundle",
                                 hint="edit BUNDLE/case/CASE.md (front matter included) and run --refresh again")
    else:
        if not args.project or not args.name:
            raise C.ContribError("USAGE", "PROJECT.json and --name are required (or --refresh BUNDLE)",
                                 hint="os-optics-kit contrib case PROJECT.json --name my-case --class free-space "
                                      "--domain gaussian-beam --tags beam-expander --dry-run")
        name = C.check_name(args.name, what="case name")
        classified = classification(args)
        out = Path(args.out or f"contrib-case-{name}").resolve()
        source = Path(args.project)
        C.check_bundle_target(out, "case", name, force=args.force)
    if not source.is_file():
        raise C.ContribError("PROJECT_MISSING", f"{source} is not a file")
    project, normalized = load_and_normalize(source.read_bytes())
    pins = catalog_pins(project)
    if len(pins) > 1:
        raise C.ContribError("CATALOG_PINS_MIXED", f"components pin several catalog releases ({', '.join(pins)})",
                             hint="os-optics project update-catalog PROJECT.json moves them to one release")

    repo = C.resolve_repo(args.repo)
    base = C.resolve_base(repo, args.base or (previous.get("base_commit") if args.refresh else None)) if repo else None
    if repo is not None and C.run(["git", "-C", str(repo), "cat-file", "-e", f"{base}:cases/{name}"]).returncode == 0:
        raise C.ContribError("CASE_EXISTS", f"cases/{name} already exists in the kit", hint="choose another --name")
    commands = C.kit_commands(repo)
    if not args.refresh:
        tools = C.split_list(args.tools)
        unknown = [tool for tool in tools if tool not in commands]
        if unknown:
            raise C.ContribError("TOOLS_UNKNOWN", f"not os-optics-kit commands: {', '.join(unknown)}",
                                 hint="os-optics-kit --help lists them")

    workdir = Path(tempfile.mkdtemp(prefix="os-optics-kit-contrib-"))
    try:
        # staged as cases/NAME so that cases.check_case judges it exactly as the kit's index check will
        case_dir = workdir / "cases" / name
        if args.refresh:
            shutil.copytree(bundle / "case", case_dir)
            for path in case_dir.rglob("*"):
                if path.is_file() and path.suffix in kit_cases.TEXT_SUFFIXES:
                    C.write_text(path, path.read_bytes().decode("utf-8"))
        else:
            case_dir.mkdir(parents=True)
        C.write_text(case_dir / "project.json", normalized)
        env = engine_env(args, workdir, pins)
        run_dir = workdir / "run"
        verified = verify_project(case_dir, run_dir, env)
        if pins and verified["catalog"] != pins[0]:
            raise C.ContribError("CATALOG_MISMATCH", f"the run used catalog {verified['catalog']}, the Project pins "
                                 f"{pins[0]}", exit_code=C.EXIT_VERIFICATION)
        if verified["status"] not in ("PASS", "WARNING"):
            warnings.append(f"RUN_STATUS_{verified['status']}: the case records this outcome; say why in CASE.md")
        C.write_text(case_dir / "expected.json", C.dump_json(expected_document(verified)))

        if args.refresh:
            case_md = (case_dir / "CASE.md").read_text(encoding="utf-8") if (case_dir / "CASE.md").is_file() else ""
            try:
                case_md = rewrite_front_matter(case_md, {"engine": verified["engine"],
                                                         "catalog": verified["catalog"] or "none"})
            except fm.FrontMatterError as exc:
                raise C.ContribError("CASE_FILES_INVALID", "CASE.md front matter unreadable", problems=[str(exc)],
                                     hint="edit BUNDLE/case/CASE.md and run --refresh again") from None
            C.write_text(case_dir / "CASE.md", case_md)
            # the front matter keeps what the bundle has; the system views are rendered again from this run: the
            # recorded ones, the ones not rendered before and the ones the (possibly edited) class needs
            case_class = fm.split(case_md)[0].get("class")
            images, missing = record_images(name, case_dir, run_dir, workdir, env, warnings, case_class=case_class,
                                            previous=kit_cases.read_images(case_dir),
                                            unrendered=previous.get("unrendered_views") or [])
            files = [record["file"] for record in images["images"]]
            for readme in update_readme_images(name, case_dir, images["images"]):
                warnings.append(f"README_IMAGES_NOT_UPDATED: case/{readme}: its images could not be found as one "
                                f"block of image lines; edit it to show exactly {files}, in this order")
        else:
            case_class = classified["class"]
            images, missing = record_images(name, case_dir, run_dir, workdir, env, warnings, case_class=case_class,
                                            previous=None)
        complete = not missing
        if not args.refresh:
            title = one_line(args.title) or name.replace("-", " ").capitalize()
            summary = one_line(args.summary) or default_summary(project, title, verified)
            if len(summary) > kit_cases.SUMMARY_MAX_CHARS:
                raise C.ContribError("SUMMARY_TOO_LONG", f"--summary has {len(summary)} characters; at most "
                                     f"{kit_cases.SUMMARY_MAX_CHARS}")
            for template, target, language in (("CASE.md", "CASE.md", "en"), ("README.case.md", "README.md", "en"),
                                               ("README.case.zh-CN.md", "README.zh-CN.md", "zh-CN")):
                values = prose_values(name, title, summary, tools, classified, verified, case_dir, images["images"],
                                      language)
                C.write_text(case_dir / target, C.render_template(repo, template, values, warnings))

        problems = validate_case_dir(case_dir, name, verified, commands)
        # a view the engine could not draw is reported as IMAGE_NOT_RENDERED: the bundle is still written
        # (status INCOMPLETE, no patch) so that the case can be completed and refreshed
        blocking = [p for p in problems if complete or p.split(":", 1)[0] not in IMAGE_CODES]
        if blocking:
            raise C.ContribError("CASE_FILES_INVALID", "the case files do not meet the case format", problems=problems,
                                 hint="edit BUNDLE/case and run 'os-optics-kit contrib case --refresh BUNDLE'",
                                 details={"warnings": warnings} if warnings else None)
        front, _ = fm.split((case_dir / "CASE.md").read_text(encoding="utf-8"))
        entry = {**kit_cases.index_entry(case_dir), "path": f"cases/{name}"}

        patch_dir = workdir / "patch"
        patch_dir.mkdir()
        patch, clone, committed, index_note = None, None, [], None
        branch = f"contrib/case-{name}"
        subject = f"feat(cases): add {name}"
        if complete and repo is not None and base is not None:
            clone = C.make_clone(repo, base, branch, workdir)
            target = clone.path / "cases" / name
            shutil.copytree(case_dir, target)
            index_note = update_case_indexes(clone)
            catalog_note = f"catalog release {verified['catalog']}" if verified["catalog"] else "no catalog products"
            message = (f"{subject}\n\n{front['summary']}\n\nVerified with os-optics {verified['engine']} and "
                       f"{catalog_note}: run {verified['status']}, {_counts(verified)}.")
            patch = C.commit_and_format_patch(clone, index_paths(clone, name), message, patch_dir)
            committed = C.committed_files(clone)
        elif repo is None:
            warnings.append("NO_KIT_CHECKOUT: no os-optics-kit checkout found; the bundle holds the case files only")

        baseline = C.read_baseline(repo)
        pr_body = case_pr_body(front, verified, baseline, entry, committed or [f"cases/{name}/{f}" for f in entry["files"]],
                               patch is not None, index_note, images["images"])

        # write the bundle
        if args.refresh:
            C.clear_bundle_outputs(out, "case")
        else:
            C.prepare_bundle_dir(out, "case", name, force=args.force)
        shutil.copytree(case_dir, out / "case")
        C.write_text(out / "PR_BODY.md", pr_body)
        bundle_patch = None
        if patch is not None:
            bundle_patch = out / patch.name
            shutil.copyfile(patch, bundle_patch)

        summary_doc: dict[str, Any] = {
            "baseline": {"engine": verified["engine"], "engine_build_id": verified["engine_build_id"],
                         "catalog": verified["catalog"]},
            "kit_baseline": _kit_baseline(baseline),
            "verification": {"check": verified["check"].get("status"), "run_status": verified["status"],
                             "execution": verified["execution"],
                             "in_model_scope": verified["targets"].get("in_model_scope"),
                             "counts": verified["targets"].get("counts"),
                             "targets": expected_document(verified)["targets"]},
        }
        warnings += _baseline_warnings(summary_doc["baseline"], baseline)
        manifest = {
            "schema": C.BUNDLE_SCHEMA, "schema_version": 1, "kind": "case", "name": name,
            "kit_version": C.kit_version_of(repo) if repo else _installed_kit_version(),
            "base_commit": base, "branch": branch, "patch": bundle_patch.name if bundle_patch else None,
            "pr": {"title": subject, "body": "PR_BODY.md", "upstream": args.upstream},
            "files": [{"path": f"cases/{name}/{rel}", "sha256": C.sha256_file(case_dir / rel)}
                      for rel in C.relative_listing(case_dir)],
            "cases_index": index_note or ("not regenerated (no patch): the entry is below; in a kit checkout "
                                          "'os-optics-kit cases index --write' and 'python scripts/case_tables.py "
                                          "--write' add it to cases.json and the README case tables"),
            "cases_index_entry": entry,
            # the planned views the engine did not render: the next --refresh tries them again at their place
            "unrendered_views": unrendered_entries(missing),
            **summary_doc,
            "warnings": warnings,
        }
        pr_info = None
        if args.open_pr:
            plan = C.PullRequestPlan(branch=branch, title=subject, body_file=out / "PR_BODY.md", upstream=args.upstream)
            if not complete:
                pr_info = {"opened": False, "reason": "INCOMPLETE"}
            elif args.dry_run:
                pr_info = {"opened": False, "reason": "DRY_RUN", "planned": plan.planned_commands()}
            elif clone is None:
                pr_info = {"opened": False, "reason": "NO_PATCH"}
            else:
                pr_info = C.open_pull_request(plan, clone)
            manifest["pr"]["result"] = pr_info
        C.write_text(out / "bundle.json", C.dump_json(manifest))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    opened = bool(pr_info and pr_info.get("opened"))
    return {
        "status": "INCOMPLETE" if not complete else "PR_OPENED" if opened else "READY",
        "kind": "case", "name": name, "dry_run": bool(args.dry_run),
        "bundle": str(out), "case_dir": str(out / "case"),
        "patch": str(bundle_patch) if bundle_patch else None, "pr_body": str(out / "PR_BODY.md"),
        "files": committed or [f["path"] for f in manifest["files"]],
        **summary_doc,
        "images": [record["file"] for record in images["images"]],
        "warnings": warnings,
        **({"pr": pr_info} if pr_info else {}),
        "next_step": _next_step(out, bundle_patch, args, opened, pr_info, complete, name, images=images_command(
            name, refresh_plan(recorded_views(images), unrendered_entries(missing), case_class))),
    }


CASE_TABLES_SCRIPT = "scripts/case_tables.py"


def update_case_indexes(clone: C.Clone) -> str:
    """cases.json regenerated by the checkout's own ``os-optics-kit cases index --write``, then the case tables of
    README.md and README.zh-CN.md by its ``python scripts/case_tables.py --write`` (CI checks both) -> the note of
    the pull-request checklist."""
    result = clone.kit("cases", "index", "--write")
    if result.returncode != 0:
        raise C.ContribError("CASES_INDEX_FAILED", "os-optics-kit cases index --write refused the case",
                             problems=[C._tail(result.stdout or result.stderr)])
    if not (clone.path / CASE_TABLES_SCRIPT).is_file():
        return ("`cases.json` regenerated by `os-optics-kit cases index --write` (this checkout has no "
                f"`{CASE_TABLES_SCRIPT}`: no README case tables)")
    tables = C.run([sys.executable, CASE_TABLES_SCRIPT, "--write"], cwd=clone.path, env=clone.env())
    if tables.returncode != 0:
        raise C.ContribError("CASES_INDEX_FAILED", f"python {CASE_TABLES_SCRIPT} --write could not regenerate the "
                             "README case tables", problems=[C._tail(tables.stderr or tables.stdout)])
    return ("`cases.json` and the README case tables regenerated by `os-optics-kit cases index --write` and "
            f"`python {CASE_TABLES_SCRIPT} --write`")


def index_paths(clone: C.Clone, name: str) -> list[str]:
    """What the case commit adds: the case, cases.json and the READMEs that hold the case tables."""
    return [f"cases/{name}", "cases.json", *[p for p in kit_cases.README_PAIR if (clone.path / p).is_file()]]


def _counts(verified: dict) -> str:
    counts: dict[str, int] = {}
    for target in verified["targets"].get("targets", []):
        counts[target.get("assessment")] = counts.get(target.get("assessment"), 0) + 1
    return ", ".join(f"{n} {a}" for a, n in sorted(counts.items())) or "no targets"


def _kit_baseline(baseline: dict | None) -> dict | None:
    engine = (baseline or {}).get("engine")
    catalog = (baseline or {}).get("catalog")
    if not isinstance(engine, dict) or not isinstance(catalog, dict):
        return None
    return {"engine": engine.get("verified_version"), "engine_requirement": engine.get("requirement"),
            "catalog": catalog.get("version")}


def _baseline_warnings(actual: dict[str, Any], baseline: dict | None) -> list[str]:
    kit = _kit_baseline(baseline)
    if not kit:
        return []
    warnings = []
    if kit["engine"] and actual["engine"] and actual["engine"].split(".")[:2] != kit["engine"].split(".")[:2]:
        warnings.append(f"ENGINE_DIFFERS_FROM_KIT_BASELINE: verified with os-optics {actual['engine']}, the kit "
                        f"baseline is {kit['engine']}")
    if actual["catalog"] and kit["catalog"] and actual["catalog"] != kit["catalog"]:
        warnings.append(f"CATALOG_DIFFERS_FROM_KIT_BASELINE: the case pins {actual['catalog']}, the kit baseline is "
                        f"{kit['catalog']}; CI verifies cases against the baseline release")
    return warnings


def _installed_kit_version() -> str:
    from .._version import __version__
    return __version__


def classification_rows(front: dict) -> str:
    """Rows of the pull-request classification table: Field | Value."""
    rows = [("Class", f"`{front.get('class')}`"),
            ("Domain", ", ".join(f"`{d}`" for d in kit_cases.parse_list(front.get("domain")))),
            ("Tags", ", ".join(f"`{t}`" for t in kit_cases.parse_list(front.get("tags")))),
            ("Provenance", f"`{front.get('provenance')}`"), ("Publication", f"`{front.get('publication')}`")]
    if front.get("source"):
        rows.append(("Source", str(front["source"])))
    return "\n".join(f"| {field} | {value} |" for field, value in rows)


def image_list(records: list[dict]) -> str:
    """Bullets of the recorded images, in images.json (README) order."""
    lines = []
    for record in records:
        if record.get("kind") == taxonomy.FIGURE_KIND:
            lines.append(f"- `{record.get('file')}`: figure drawn by `{record.get('script')}`")
        else:
            lines.append(f"- `{record.get('file')}`: system view `{record.get('view')}` "
                         f"(`os-optics view RUN --no-browser --snapshot DIR {' '.join(record.get('options') or [])}`)")
    return "\n".join(lines) or "- none recorded: a system view could not be rendered (see the warnings)"


def case_pr_body(front: dict, verified: dict, baseline: dict | None, entry: dict, files: list[str],
                 patched: bool, index_note: str | None, images: list[dict]) -> str:
    kit = _kit_baseline(baseline) or {}
    views = [r for r in images if r.get("kind") == taxonomy.SYSTEM_VIEW_KIND]
    checklist = [
        "[x] `project.json` is the engine-normalized ProjectDocumentV2 with its targets",
        "[x] `expected.json` is the status and target assessments of the verified run",
        "[x] `CASE.md` front matter and sections complete, classified from the vocabulary, within 700 tokens",
        f"[{'x' if views else ' '}] {len(views)} system views rendered by `os-optics view --no-browser --snapshot` "
        f"and {len(images) - len(views)} figures, recorded in `images.json`, each within 400 KiB",
        "[x] `README.md` and `README.zh-CN.md` link each other and show the recorded images in order",
        "[x] no run directories, GLB files or bulk outputs",
        "[x] commit signed off (DCO)" if patched else "[ ] commit signed off (DCO): no patch in this bundle",
        f"[x] {index_note}" if index_note else
        "[ ] `cases.json` and the README case tables: the entry is in bundle.json; in a kit checkout run "
        "`os-optics-kit cases index --write` and `python scripts/case_tables.py --write`",
    ]
    return T.PR_BODY_CASE.format(
        name=front["name"], title=front["title"], summary=front["summary"],
        classification_rows=classification_rows(front), images_list=image_list(images),
        engine=verified["engine"], kit_engine=kit.get("engine") or "-",
        catalog=verified["catalog"] or "none", kit_catalog=kit.get("catalog") or "-",
        check_status=verified["check"].get("status"), run_status=verified["status"],
        execution=verified["execution"], target_counts=_counts(verified),
        targets_table=targets_table(verified["targets"].get("targets", [])),
        files_list="\n".join(f"- `{f}`" for f in files),
        checklist="\n".join(f"- {item}" for item in checklist),
        kit_version=_installed_kit_version(),
    )


def _next_step(out: Path, patch: Path | None, args, opened: bool, pr_info: dict | None, complete: bool = True,
               name: str = "NAME", images: str = "") -> str:
    if not complete:
        command = images or f"os-optics-kit cases images {name}"
        return (f"a system view was not rendered (IMAGE_NOT_RENDERED in warnings): fix the cause and run "
                f"'os-optics-kit contrib case --refresh {out}'; it renders the missing views again and puts them "
                f"into both READMEs. By hand instead: copy {out / 'case'} to cases/{name} of a kit checkout, run "
                f"'{command}', edit README.md and README.zh-CN.md to show exactly the "
                "images of images.json in that order, then 'os-optics-kit cases index --write', 'python "
                "scripts/case_tables.py --write' and 'os-optics-kit cases index --check'")
    if pr_info and opened:
        return f"pull request opened: {pr_info.get('url')}; follow the review there"
    if pr_info and pr_info.get("reason") in ("GH_NOT_AUTHENTICATED", "GH_NOT_INSTALLED"):
        return ("gh is not logged in ('gh auth login'); " + C.submission_hint(out, patch))
    if not args.refresh:
        return (f"edit {out / 'case' / 'CASE.md'} ('What it shows'), README.md and README.zh-CN.md, then run "
                f"'os-optics-kit contrib case --refresh {out}'; " + C.submission_hint(out, patch))
    return C.submission_hint(out, patch)
