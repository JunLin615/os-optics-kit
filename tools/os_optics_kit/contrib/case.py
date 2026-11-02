"""``os-optics-kit contrib case``: turn a finished design into a PR-ready case of the kit.

PROJECT.json is normalized to a ProjectDocumentV2 by the engine, checked, run and read back
(``project check`` -> ``project run`` -> ``results targets``); images come from ``os-optics view --snapshot``.
The case directory (project.json, CASE.md, README.md, expected.json, images/) goes into a bundle with a
``git format-patch`` patch against the kit checkout, PR_BODY.md and bundle.json. Edit the prose in BUNDLE/case,
then ``--refresh BUNDLE`` re-verifies and rebuilds the patch. ``--open-pr`` opens the pull request with your own
``gh`` login; ``--dry-run`` stays on this device.

Exit codes: 0 bundle ready (or PR opened), 1 verification failed (the engine run did not complete), 4 usage error
or refusal (JSON with status ERROR on stdout).
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

from . import _common as C
from . import _templates as T

REQUIRED_KEYS = ("name", "title", "summary", "engine", "catalog", "project", "tools", "tags", "license")
REQUIRED_SECTIONS = ["What it shows", "Run", "Targets", "Baseline", "Files"]
RUN_FILES = {"manifest.json", "engine_request.json", "simulation_result.json", "ray_data.json",
             "local_validation.json", "optical_scene.json", "optical_scene.glb"}
IMAGE_LIMIT_BYTES = 400 * 1024
IMAGE_MAX_COUNT = 4
FILE_LIMIT_BYTES = 1024 * 1024
TEXT_SUFFIXES = {".md", ".json", ".py", ".txt", ".csv"}
VIEWS = (("iso", "Isometric view"), ("top", "Top view"))
IMAGE_SIZES = ("1280x720", "960x540")
MAX_TABLE_ROWS = 12
FILE_NOTES = {
    "project.json": "the design (ProjectDocumentV2) with its targets",
    "expected.json": "the verified run status and target assessments",
    "README.md": "illustrated page",
    "CASE.md": "this page",
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
    parser.add_argument("--tools", help="comma-separated os-optics-kit commands the case uses (default: none)")
    parser.add_argument("--tags", help="comma-separated kebab-case tags")
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
    return C.EXIT_OK


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


def render_images(run_dir: Path, workdir: Path, case_dir: Path, env: dict, warnings: list[str]) -> list[str]:
    """Snapshot the VIEWS of the run in one ``os-optics view`` call; a view over 400 KB is redrawn smaller."""
    images = case_dir / "images"
    names, wanted, chosen_by_view = [], [view for view, _ in VIEWS], {}
    for size in IMAGE_SIZES:
        pending = [view for view in wanted if view not in chosen_by_view]
        if not pending:
            break
        snap = workdir / f"snap-{size}"
        result = C.run(_engine_cmd("view", str(run_dir), "--no-browser", "--snapshot", str(snap), "--views",
                                   ",".join(pending), "--size", size), env=env)
        for view in pending:
            png = snap / f"{view}.png"
            if result.returncode == 0 and png.is_file() and png.stat().st_size <= IMAGE_LIMIT_BYTES:
                chosen_by_view[view] = png
    for view in wanted:
        chosen = chosen_by_view.get(view)
        if chosen is None:
            warnings.append(f"IMAGE_NOT_RENDERED: os-optics view --snapshot gave no {view}.png within 400 KB")
            continue
        images.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(chosen, images / f"{view}.png")
        names.append(f"images/{view}.png")
    return names


# ------------------------------------------------------------------------------------------------------- prose

def expected_document(verified: dict) -> dict:
    return {
        "schema": C.EXPECTED_SCHEMA,
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


def targets_table(targets: list[dict]) -> str:
    rows = ["| Target | Metric | Observation | Bounds | Value | Assessment |", "| --- | --- | --- | --- | --- | --- |"]
    for target in targets[:MAX_TABLE_ROWS]:
        unit = target.get("unit") or ""
        value = target.get("value")
        shown = f"{value:.4g}" if isinstance(value, (int, float)) else "-"
        rows.append(f"| `{target['target_id']}` | `{target.get('metric')}` | `{target.get('observation_id')}` | "
                    f"{_bounds(target.get('bounds') or {}, unit)} | {shown} | {target.get('assessment')} |")
    if len(targets) > MAX_TABLE_ROWS:
        rows.append(f"\n{len(targets) - MAX_TABLE_ROWS} more targets in `expected.json`.")
    return "\n".join(rows)


def catalog_line(catalog: str | None) -> str:
    if not catalog:
        return "no catalog products (catalog-free)"
    return f"catalog release {catalog} (`os-optics catalog sync --release {catalog}`)"


def files_list(case_dir: Path) -> str:
    lines, images = [], []
    for rel in C.relative_listing(case_dir):
        if rel.startswith("images/"):
            images.append(f"`{rel}`")
        else:
            note = FILE_NOTES.get(rel)
            lines.append(f"- `{rel}`" + (f": {note}" if note else ""))
    present = C.relative_listing(case_dir)
    lines += [f"- `{rel}`: {FILE_NOTES[rel]}" for rel in ("CASE.md", "README.md") if rel not in present]
    if images:
        lines.append(f"- {', '.join(images)}: rendered by `os-optics view --snapshot` from the verified run")
    return "\n".join(sorted(lines, key=lambda line: (line.startswith("- `images"), line)))


def default_summary(project, title: str, verified: dict) -> str:
    targets = verified["targets"].get("targets", [])
    met = sum(1 for t in targets if t.get("assessment") == "MET")
    parts = len([c for c in project.components if getattr(c, "optical_role", None) != "source"])
    catalog = f"catalog release {verified['catalog']}" if verified["catalog"] else "declared models only"
    return (f"{title}: {parts} optical elements ({catalog}); {met} of {len(targets)} targets met, "
            f"run status {verified['status']}.")[:300]


def one_line(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def prose_values(name: str, title: str, summary: str, tools: list[str], tags: list[str], verified: dict,
                 case_dir: Path, images: list[str]) -> dict[str, str]:
    targets = verified["targets"].get("targets", [])
    alt = dict(VIEWS)
    return {
        "name": name,
        "title": title,
        "summary": summary,
        "engine": verified["engine"],
        "catalog": verified["catalog"] or "none",
        "tools": ", ".join(tools) if tools else "none",
        "tags": ", ".join(tags) if tags else "none",
        "status": verified["status"],
        "catalog_line": catalog_line(verified["catalog"]),
        "targets_table": targets_table(targets),
        "images_markdown": "\n\n".join(f"![{alt.get(Path(i).stem, Path(i).stem)}]({i})" for i in images)
                           or "Render the views with `os-optics view run --no-browser --snapshot images`.",
        "files_list": files_list(case_dir),
        "run_commands": "os-optics project check project.json\nos-optics project run project.json --out-dir run\n"
                        "os-optics results targets run",
    }


# ------------------------------------------------------------------------------------------------- validation

def validate_case_dir(case_dir: Path, name: str, verified: dict, commands: set[str]) -> list[str]:
    problems: list[str] = []
    for required in ("project.json", "CASE.md", "README.md", "expected.json"):
        if not (case_dir / required).is_file():
            problems.append(f"{required} missing")
    files = C.relative_listing(case_dir)
    images = [f for f in files if f.startswith("images/")]
    for rel in files:
        path = case_dir / rel
        if Path(rel).name in RUN_FILES or rel.endswith(".glb"):
            problems.append(f"{rel}: run outputs are not part of a case")
        elif rel.startswith("images/") and (not rel.endswith(".png") or path.stat().st_size > IMAGE_LIMIT_BYTES):
            problems.append(f"{rel}: images are PNG files of at most 400 KB")
        elif path.stat().st_size > FILE_LIMIT_BYTES:
            problems.append(f"{rel}: larger than 1 MB; keep large data outside the case")
    if len(images) > IMAGE_MAX_COUNT:
        problems.append(f"{len(images)} images: at most {IMAGE_MAX_COUNT} per case")
    if problems and any("missing" in p for p in problems):
        return problems
    case_md = (case_dir / "CASE.md").read_text(encoding="utf-8")
    try:
        front, body = C.parse_front_matter(case_md)
    except ValueError as exc:
        return problems + [f"CASE.md: {exc}"]
    for key in REQUIRED_KEYS:
        if not front.get(key):
            problems.append(f"CASE.md: front matter key '{key}' missing or empty")
    expect = {"name": name, "engine": verified["engine"], "catalog": verified["catalog"] or "none",
              "project": "project.json", "license": "MIT"}
    for key, value in expect.items():
        if front.get(key) and front[key] != value:
            problems.append(f"CASE.md: {key} is {front[key]!r}, expected {value!r}")
    if len(front.get("summary", "")) > 300:
        problems.append("CASE.md: summary longer than 300 characters")
    unknown = [tool for tool in C.split_list(front.get("tools")) if tool not in commands]
    if unknown:
        problems.append(f"CASE.md: tools {', '.join(unknown)} are not os-optics-kit commands")
    bad_tags = [tag for tag in C.split_list(front.get("tags")) if not C.NAME_RE.match(tag)]
    if bad_tags:
        problems.append(f"CASE.md: tags must be kebab-case ({', '.join(bad_tags)})")
    problems += [f"CASE.md: {p}" for p in C.check_sections(C.body_sections(body), REQUIRED_SECTIONS)]
    tokens = C.token_estimate(case_md)
    if tokens > C.BUDGETS["CASE.md"]:
        problems.append(f"CASE.md: about {tokens} tokens, budget {C.BUDGETS['CASE.md']} ({C.TOKEN_ESTIMATE})")
    readme = (case_dir / "README.md").read_text(encoding="utf-8")
    for label, text in (("CASE.md", case_md), ("README.md", readme)):
        if C.PLACEHOLDER_RE.search(text):
            problems.append(f"{label}: unfilled {{{{...}}}} placeholder")
    for ref in re.findall(r"\]\(([^)\s]+\.png)\)", readme):
        if not (case_dir / ref).is_file():
            problems.append(f"README.md: image {ref} not found")
    return problems


def rewrite_front_matter(case_md: str, updates: dict[str, str]) -> str:
    front, body = C.parse_front_matter(case_md)
    front.update({k: v for k, v in updates.items() if k in front})
    return C.render_front_matter(front) + body


# ------------------------------------------------------------------------------------------------------- flow

def contribute_case(args) -> dict:
    warnings: list[str] = []
    if args.refresh:
        bundle = Path(args.refresh).resolve()
        previous = C.load_bundle(bundle, "case")
        name = previous["name"]
        out = bundle
        source = bundle / "case" / "project.json"
        if any(getattr(args, key) for key in ("project", "name", "title", "summary", "tools", "tags", "out")):
            raise C.ContribError("USAGE", "--refresh takes its name, prose and files from the bundle",
                                 hint="edit BUNDLE/case/CASE.md (front matter included) and run --refresh again")
    else:
        if not args.project or not args.name:
            raise C.ContribError("USAGE", "PROJECT.json and --name are required (or --refresh BUNDLE)",
                                 hint="os-optics-kit contrib case PROJECT.json --name my-case --dry-run")
        name = C.check_name(args.name, what="case name")
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
        tags = [tag.lower() for tag in C.split_list(args.tags)]
        unknown = [tool for tool in tools if tool not in commands]
        if unknown:
            raise C.ContribError("TOOLS_UNKNOWN", f"not os-optics-kit commands: {', '.join(unknown)}",
                                 hint="os-optics-kit --help lists them")
        for tag in tags:
            C.check_name(tag, what="tag")

    workdir = Path(tempfile.mkdtemp(prefix="os-optics-kit-contrib-"))
    try:
        case_dir = workdir / "case"
        if args.refresh:
            shutil.copytree(bundle / "case", case_dir)
            for path in case_dir.rglob("*"):
                if path.is_file() and path.suffix in TEXT_SUFFIXES:
                    C.write_text(path, path.read_bytes().decode("utf-8"))
        else:
            case_dir.mkdir()
        C.write_text(case_dir / "project.json", normalized)
        env = engine_env(args, workdir, pins)
        verified = verify_project(case_dir, workdir / "run", env)
        if pins and verified["catalog"] != pins[0]:
            raise C.ContribError("CATALOG_MISMATCH", f"the run used catalog {verified['catalog']}, the Project pins "
                                 f"{pins[0]}", exit_code=C.EXIT_VERIFICATION)
        if verified["status"] not in ("PASS", "WARNING"):
            warnings.append(f"RUN_STATUS_{verified['status']}: the case records this outcome; say why in CASE.md")
        C.write_text(case_dir / "expected.json", C.dump_json(expected_document(verified)))

        if args.refresh:
            case_md = (case_dir / "CASE.md").read_text(encoding="utf-8")
            try:
                case_md = rewrite_front_matter(case_md, {"engine": verified["engine"],
                                                         "catalog": verified["catalog"] or "none"})
            except ValueError as exc:
                raise C.ContribError("CASE_FILES_INVALID", "CASE.md front matter unreadable", problems=[str(exc)])
            C.write_text(case_dir / "CASE.md", case_md)
            images = [rel for rel in C.relative_listing(case_dir) if rel.startswith("images/")]
        else:
            images = render_images(workdir / "run", workdir, case_dir, env, warnings)
            title = one_line(args.title) or name.replace("-", " ").capitalize()
            summary = one_line(args.summary) or default_summary(project, title, verified)
            if len(summary) > 300:
                raise C.ContribError("SUMMARY_TOO_LONG", f"--summary has {len(summary)} characters; at most 300")
            values = prose_values(name, title, summary, tools, tags, verified, case_dir, images)
            C.write_text(case_dir / "CASE.md", C.choose_template(repo, "templates/CASE.md", T.CASE_MD, values, warnings))
            C.write_text(case_dir / "README.md",
                         C.choose_template(repo, "templates/README.case.md", T.README_CASE_MD, values, warnings))

        problems = validate_case_dir(case_dir, name, verified, commands)
        if problems:
            raise C.ContribError("CASE_FILES_INVALID", "the case files do not meet the case format", problems=problems,
                                 hint="edit BUNDLE/case and run 'os-optics-kit contrib case --refresh BUNDLE'")
        front, _ = C.parse_front_matter((case_dir / "CASE.md").read_text(encoding="utf-8"))
        entry = C.case_index_entry(case_dir, front)

        patch_dir = workdir / "patch"
        patch_dir.mkdir()
        patch, clone, committed, index_note = None, None, [], None
        branch = f"contrib/case-{name}"
        subject = f"feat(cases): add {name}"
        if repo is not None:
            clone = C.make_clone(repo, base, branch, workdir)
            target = clone.path / "cases" / name
            shutil.copytree(case_dir, target)
            index_note = update_case_indexes(clone, entry)
            catalog_text = f"catalog release {verified['catalog']}" if verified["catalog"] else "no catalog products"
            message = (f"{subject}\n\n{front['summary']}\n\nVerified with os-optics {verified['engine']} and "
                       f"{catalog_text}: run {verified['status']}, {_counts(verified)}.")
            patch = C.commit_and_format_patch(clone, [f"cases/{name}", "cases.json"], message, patch_dir)
            committed = C.committed_files(clone)
        else:
            warnings.append("NO_KIT_CHECKOUT: no os-optics-kit checkout found; the bundle holds the case files only")

        baseline = C.read_baseline(repo)
        pr_body = case_pr_body(front, verified, baseline, entry, committed or [f"cases/{name}/{f}" for f in entry["files"]],
                               repo, index_note)

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

        summary_doc = {
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
            "cases_index": index_note or "entry below (no kit checkout)",
            "cases_index_entry": entry,
            **summary_doc,
            "warnings": warnings,
        }
        pr_info = None
        if args.open_pr:
            plan = C.PullRequestPlan(branch=branch, title=subject, body_file=out / "PR_BODY.md", upstream=args.upstream)
            if args.dry_run:
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
        "status": "PR_OPENED" if opened else "READY",
        "kind": "case", "name": name, "dry_run": bool(args.dry_run),
        "bundle": str(out), "case_dir": str(out / "case"),
        "patch": str(bundle_patch) if bundle_patch else None, "pr_body": str(out / "PR_BODY.md"),
        "files": committed or [f["path"] for f in manifest["files"]],
        **summary_doc,
        "images": images,
        "warnings": warnings,
        **({"pr": pr_info} if pr_info else {}),
        "next_step": _next_step(out, bundle_patch, args, opened, pr_info),
    }


def update_case_indexes(clone: C.Clone, entry: dict) -> str:
    """cases.json through the checkout's own indexer when it has one, else the internal implementation."""
    if (clone.path / "tools" / "os_optics_kit" / "cases.py").is_file():
        result = clone.kit("cases", "index", "--write")
        if result.returncode != 0:
            raise C.ContribError("CASES_INDEX_FAILED", "os-optics-kit cases index --write refused the case",
                                 problems=[C._tail(result.stdout or result.stderr)])
        return "regenerated by os-optics-kit cases index --write"
    C.update_cases_index(clone.path, entry)
    return "updated by os-optics-kit contrib"


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


def _baseline_warnings(actual: dict, baseline: dict | None) -> list[str]:
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


def case_pr_body(front: dict, verified: dict, baseline: dict | None, entry: dict, files: list[str],
                 repo, index_note: str | None) -> str:
    kit = _kit_baseline(baseline) or {}
    checklist = [
        "[x] `project.json` is the engine-normalized ProjectDocumentV2 with its targets",
        "[x] `expected.json` is the status and target assessments of the verified run",
        "[x] `CASE.md` front matter and sections complete, within 700 tokens",
        f"[x] {len([f for f in entry['files'] if f.startswith('images/')])} images rendered by `os-optics view "
        "--snapshot`, each within 400 KB",
        "[x] no run directories, GLB files or bulk outputs",
        "[x] commit signed off (DCO)" if repo else "[ ] commit signed off (DCO): no patch in this bundle",
        f"[x] `cases.json` {index_note}" if index_note else "[ ] `cases.json` entry: in bundle.json",
    ]
    return T.PR_BODY_CASE.format(
        name=front["name"], title=front["title"], summary=front["summary"],
        engine=verified["engine"], kit_engine=kit.get("engine") or "-",
        catalog=verified["catalog"] or "none", kit_catalog=kit.get("catalog") or "-",
        check_status=verified["check"].get("status"), run_status=verified["status"],
        execution=verified["execution"], target_counts=_counts(verified),
        targets_table=targets_table(verified["targets"].get("targets", [])),
        files_list="\n".join(f"- `{f}`" for f in files),
        checklist="\n".join(f"- {item}" for item in checklist),
        kit_version=_installed_kit_version(),
    )


def _next_step(out: Path, patch: Path | None, args, opened: bool, pr_info: dict | None) -> str:
    if opened:
        return f"pull request opened: {pr_info.get('url')}; follow the review there"
    if pr_info and pr_info.get("reason") in ("GH_NOT_AUTHENTICATED", "GH_NOT_INSTALLED"):
        return ("gh is not logged in ('gh auth login'); " + C.submission_hint(out, patch))
    if not args.refresh:
        return (f"edit {out / 'case' / 'CASE.md'} ('What it shows') and README.md, then run "
                f"'os-optics-kit contrib case --refresh {out}'; " + C.submission_hint(out, patch))
    return C.submission_hint(out, patch)
