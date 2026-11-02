"""``os-optics-kit contrib tool``: package a helper script as a kit command with its skill page and tests.

HELPER.py must define ``main(argv) -> int`` (argparse ``--help`` included). It becomes
``tools/os_optics_kit/MODULE.py`` and ``os-optics-kit COMMAND``, with ``skills/NAME/SKILL.md`` (from its ``--help``)
and ``tests/test_MODULE.py`` (import, registration, ``--help`` with and without the engine, doctests). The tests run
in a private clone of your kit checkout before the patch is written. Edit the bundle's SKILL.md and tests, then
``--refresh BUNDLE`` re-runs them and rebuilds the patch. ``--open-pr`` opens the pull request with your own ``gh``
login; ``--dry-run`` stays on this device.

Exit codes: 0 bundle ready (or PR opened), 1 a check or the generated tests failed, 4 usage error or refusal (JSON
with status ERROR on stdout).
"""

from __future__ import annotations

import argparse
import ast
import re
import shutil
import sys
import tempfile
from pathlib import Path

from . import _common as C
from . import _templates as T

REQUIRED_KEYS = ("name", "description", "version", "command", "requires")
REQUIRED_SECTIONS = ["When to use", "When not to use", "Inputs", "Outputs", "Minimal example", "Limits", "Version"]
RESERVED_MODULES = {"cli", "contrib", "skills", "cases", "_resources", "_version", "__init__", "__main__"}
KINDS = {"tool", "contribution", "guide"}


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise C.ContribError("USAGE", message, hint="os-optics-kit contrib tool --help")


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="os-optics-kit contrib tool", description=__doc__,
                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("helper", nargs="?", help="the helper script (defines main(argv) -> int)")
    parser.add_argument("--name", help="skill name, kebab-case (skills/NAME); also the command unless --command")
    parser.add_argument("--command", help="os-optics-kit subcommand (default: NAME)")
    parser.add_argument("--module", help="module name under os_optics_kit (default: from the file name)")
    parser.add_argument("--description", help="what it does and when to use it, at most 300 characters "
                                              "(default: the helper's docstring)")
    parser.add_argument("--repo", help="os-optics-kit Git checkout to patch (default: the checkout around the "
                                       "current directory; without one, the installed package is used and no "
                                       "patch is written)")
    parser.add_argument("--base", help="commit of the checkout the patch applies to (default: HEAD)")
    parser.add_argument("--out", help="bundle directory (default: ./contrib-tool-NAME)")
    parser.add_argument("--refresh", metavar="BUNDLE", help="re-test an edited bundle and rebuild its patch")
    parser.add_argument("--dry-run", action="store_true", help="stay on this device: no PR")
    parser.add_argument("--open-pr", action="store_true", help="fork, push and open the PR with your own gh login")
    parser.add_argument("--force", action="store_true", help="regenerate an existing bundle from scratch")
    parser.add_argument("--upstream", default=C.UPSTREAM, help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        result = contribute_tool(args)
    except C.ContribError as exc:
        C.emit(exc.as_dict())
        return exc.exit_code
    C.emit(result)
    return C.EXIT_OK


# ------------------------------------------------------------------------------------------------- the helper

def analyze_helper(source: str, kit_modules: set[str]) -> dict:
    """Static facts of a helper: docstring, ``main`` signature, imports. Raises ``HELPER_INVALID``."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise C.ContribError("HELPER_INVALID", f"the helper does not parse: {exc}") from None
    problems = []
    mains = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"]
    if not mains:
        problems.append("no top-level 'def main(argv)': the kit calls main(argv) and uses its return value as the "
                        "exit code")
    else:
        arguments = mains[-1].args
        if not (arguments.posonlyargs or arguments.args or arguments.vararg):
            problems.append("main() takes no argument: define main(argv=None) and parse argv")
    absolute, relative, engine = set(), set(), False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                absolute.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if node.module:
                    relative.add(node.module.split(".")[0])
                else:
                    relative.update(alias.name for alias in node.names)
            elif node.module:
                absolute.add(node.module.split(".")[0])
    engine = "os_optics" in absolute
    missing = sorted(relative - kit_modules)
    if missing:
        problems.append(f"relative imports of modules the kit does not have: {', '.join(missing)}")
    if absolute & {"cases", "examples"}:
        problems.append("tools never import cases: move the shared part into the helper")
    if problems:
        raise C.ContribError("HELPER_INVALID", "the helper cannot become a kit command", problems=problems)
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    third_party = sorted(absolute - stdlib - {"os_optics", "os_optics_kit", "__future__"} - kit_modules)
    return {"docstring": ast.get_docstring(tree) or "", "engine": engine, "third_party": third_party}


def first_sentence(text: str) -> str:
    paragraph = re.split(r"\n\s*\n", text.strip(), maxsplit=1)[0] if text.strip() else ""
    paragraph = re.sub(r"\s+", " ", paragraph)
    match = re.match(r"(.+?[.!?])(\s|$)", paragraph)
    return (match.group(1) if match else paragraph).strip()


def when_to_use(docstring: str, description: str) -> str:
    """The docstring's second paragraph (doctest lines left out) when it has one, else the description."""
    paragraphs = [p for p in re.split(r"\n\s*\n", docstring.strip()) if p.strip()][1:]
    for paragraph in paragraphs:
        lines = [line for line in paragraph.splitlines() if not line.lstrip().startswith((">>>", "..."))]
        text = re.sub(r"\s+", " ", " ".join(lines)).strip()
        if text and not text.lower().startswith("usage"):
            return text
    return description


def command_summary(description: str) -> str:
    summary = first_sentence(description).rstrip(".")
    if summary[:1].isupper() and not summary[1:2].isupper():
        summary = summary[0].lower() + summary[1:]
    return summary if len(summary) <= 90 else summary[:87].rstrip() + "..."


def class_name(module: str) -> str:
    return "".join(part.capitalize() for part in module.split("_") if part) + "Tests"


# -------------------------------------------------------------------------------------------------- checkout

def virtual_checkout(workdir: Path) -> C.Clone:
    """Without a kit checkout: a copy of the installed package, to register and test the helper (no patch)."""
    import os_optics_kit
    root = workdir / "kit"
    shutil.copytree(Path(os_optics_kit.__file__).resolve().parent, root / "tools" / "os_optics_kit",
                    ignore=shutil.ignore_patterns("__pycache__", "_data"))
    (root / "tests").mkdir()
    (root / "skills").mkdir()
    return C.Clone(path=root, base="", branch="", identity=("", ""))


def help_text(clone: C.Clone, command: str, *, without_engine: bool = False):
    block = "sys.modules['os_optics'] = None; " if without_engine else ""
    script = (f"import sys; {block}sys.argv = ['os-optics-kit {command}']; "
              f"from os_optics_kit.cli import main; raise SystemExit(main([{command!r}, '--help']))")
    return C.run([sys.executable, "-c", script], cwd=clone.path, env=clone.env())


def fit_help(text: str, render) -> str:
    """The full ``--help`` when the skill page stays within its budget, else its usage paragraph."""
    full = text.strip()
    if C.token_estimate(render(full)) <= C.BUDGETS["SKILL.md"]:
        return full
    return re.split(r"\n\s*\n", full, maxsplit=1)[0]


def validate_skill(path: Path, name: str, command: str) -> list[str]:
    text = path.read_text(encoding="utf-8")
    try:
        front, body = C.parse_front_matter(text)
    except ValueError as exc:
        return [f"SKILL.md: {exc}"]
    problems = [f"SKILL.md: front matter key '{key}' missing or empty" for key in REQUIRED_KEYS if not front.get(key)]
    if front.get("name") and front["name"] != name:
        problems.append(f"SKILL.md: name is {front['name']!r}, the directory is {name!r}")
    if front.get("command") and front["command"] != f"os-optics-kit {command}":
        problems.append(f"SKILL.md: command is {front['command']!r}, expected 'os-optics-kit {command}'")
    if len(front.get("description", "")) > 300:
        problems.append("SKILL.md: description longer than 300 characters")
    if front.get("kind") and front["kind"] not in KINDS:
        problems.append(f"SKILL.md: kind {front['kind']!r} is not one of {', '.join(sorted(KINDS))}")
    if front.get("version") and not re.match(r"^\d+\.\d+\.\d+$", front["version"]):
        problems.append("SKILL.md: version is not semver (MAJOR.MINOR.PATCH)")
    problems += [f"SKILL.md: {p}" for p in C.check_sections(C.body_sections(body), REQUIRED_SECTIONS)]
    tokens = C.token_estimate(text)
    if tokens > C.BUDGETS["SKILL.md"]:
        problems.append(f"SKILL.md: about {tokens} tokens, budget {C.BUDGETS['SKILL.md']} ({C.TOKEN_ESTIMATE})")
    if C.PLACEHOLDER_RE.search(text):
        problems.append("SKILL.md: unfilled {{...}} placeholder")
    return problems


def run_tests(clone: C.Clone, module: str) -> dict:
    result = C.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", f"test_{module}.py", "-v"],
                   cwd=clone.path, env=clone.env())
    output = (result.stderr or b"").decode("utf-8", "replace")
    ran = re.search(r"Ran (\d+) tests?", output)
    summary = {"exit_code": result.returncode, "ran": int(ran.group(1)) if ran else 0,
               "result": "OK" if result.returncode == 0 else "FAILED"}
    if result.returncode != 0 or not summary["ran"]:
        raise C.ContribError("TESTS_FAILED", f"tests/test_{module}.py did not pass", problems=[C._tail(result.stderr)],
                             exit_code=C.EXIT_VERIFICATION, details={"tests": summary})
    return summary


# ------------------------------------------------------------------------------------------------------- flow

def contribute_tool(args) -> dict:
    warnings: list[str] = []
    previous = None
    if args.refresh:
        out = Path(args.refresh).resolve()
        previous = C.load_bundle(out, "tool")
        if any(getattr(args, key) for key in ("helper", "name", "command", "module", "description", "out")):
            raise C.ContribError("USAGE", "--refresh takes its name, files and prose from the bundle",
                                 hint="edit BUNDLE/tool/... and run --refresh again")
        name, command, module = previous["name"], previous["command"], previous["module"]
        helper_path = out / "tool" / "tools" / "os_optics_kit" / f"{module}.py"
    else:
        if not args.helper or not args.name:
            raise C.ContribError("USAGE", "HELPER.py and --name are required (or --refresh BUNDLE)",
                                 hint="os-optics-kit contrib tool my_helper.py --name my-helper --dry-run")
        name = C.check_name(args.name, what="skill name")
        command = C.check_name(args.command or name, what="command")
        helper_path = Path(args.helper)
        module = args.module or helper_path.stem.lower().replace("-", "_")
        if not C.MODULE_RE.match(module) or module in RESERVED_MODULES:
            raise C.ContribError("MODULE_INVALID", f"module name {module!r} is not usable",
                                 hint="pass --module snake_case_name")
        out = Path(args.out or f"contrib-tool-{name}").resolve()
        C.check_bundle_target(out, "tool", name, force=args.force)
    if not helper_path.is_file() or helper_path.suffix != ".py":
        raise C.ContribError("HELPER_MISSING", f"{helper_path} is not a Python file")
    if name != command:
        warnings.append(f"NAME_DIFFERS_FROM_COMMAND: the kit names each tool's skill after its command ({command})")

    repo = C.resolve_repo(args.repo)
    base = C.resolve_base(repo, args.base or (previous or {}).get("base_commit")) if repo else None
    if repo is not None:
        for rel in (f"tools/os_optics_kit/{module}.py", f"skills/{name}", f"tests/test_{module}.py"):
            if C.run(["git", "-C", str(repo), "cat-file", "-e", f"{base}:{rel}"]).returncode == 0:
                raise C.ContribError("TOOL_EXISTS", f"{rel} already exists in the kit",
                                     hint="choose another --name / --module")
    if command in C.kit_commands(repo):
        raise C.ContribError("COMMAND_EXISTS", f"os-optics-kit already has a {command!r} command",
                             hint="choose another --command")

    workdir = Path(tempfile.mkdtemp(prefix="os-optics-kit-contrib-"))
    try:
        branch = f"contrib/tool-{name}"
        clone = C.make_clone(repo, base, branch, workdir) if repo else virtual_checkout(workdir)
        if repo is None:
            warnings.append("NO_KIT_CHECKOUT: tested against the installed package; the bundle holds the files only")
        package = clone.path / "tools" / "os_optics_kit"
        kit_modules = {p.stem for p in package.glob("*.py")} | {p.name for p in package.iterdir() if p.is_dir()}
        source = helper_path.read_text(encoding="utf-8")
        facts = analyze_helper(source, kit_modules)
        if facts["third_party"]:
            warnings.append("THIRD_PARTY_IMPORTS: " + ", ".join(facts["third_party"]) + " are not dependencies of "
                            "os-optics-kit (os-optics, plot extra: matplotlib); a helper that needs them belongs "
                            "with a case")
        baseline = C.read_baseline(repo) or {}
        requirement = (baseline.get("engine") or {}).get("requirement") or ">=0.12,<0.13"
        requires = f"os-optics{requirement}" if facts["engine"] else "none"

        if args.refresh:
            skill_front, _ = C.parse_front_matter((out / "tool" / "skills" / name / "SKILL.md").read_text("utf-8"))
            description = skill_front.get("description", "")
        else:
            description = re.sub(r"\s+", " ", args.description or first_sentence(facts["docstring"])).strip()
        if not description:
            raise C.ContribError("DESCRIPTION_MISSING", "no --description and the helper has no docstring")
        if len(description) > 300:
            raise C.ContribError("DESCRIPTION_TOO_LONG", f"the description has {len(description)} characters; at most 300")

        # register and check --help
        C.write_text(package / f"{module}.py", source)
        cli = package / "cli.py"
        summary = command_summary(description)
        C.write_text(cli, C.register_command(cli.read_text(encoding="utf-8"), command, module, summary))
        shown = help_text(clone, command)
        if shown.returncode != 0:
            raise C.ContribError("HELP_FAILED", f"os-optics-kit {command} --help exited with {shown.returncode}",
                                 problems=[C._tail(shown.stderr or shown.stdout)], exit_code=C.EXIT_VERIFICATION)
        if help_text(clone, command, without_engine=True).returncode != 0:
            raise C.ContribError("HELP_NEEDS_ENGINE", f"os-optics-kit {command} --help fails without os-optics",
                                 hint="import os_optics inside the functions that use it, not at module level",
                                 exit_code=C.EXIT_VERIFICATION)

        skill_path = clone.path / "skills" / name / "SKILL.md"
        test_path = clone.path / "tests" / f"test_{module}.py"
        if args.refresh:
            C.write_text(skill_path, (out / "tool" / "skills" / name / "SKILL.md").read_text(encoding="utf-8"))
            extra = out / "tool" / "skills" / name
            for item in extra.rglob("*"):
                if item.is_file() and item.name != "SKILL.md":
                    target = skill_path.parent / item.relative_to(extra)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(item, target)
            C.write_text(test_path, (out / "tool" / "tests" / f"test_{module}.py").read_text(encoding="utf-8"))
        else:
            kit_version = C.kit_version_of(clone.path)
            values = {
                "name": name, "description": description, "when_to_use": when_to_use(facts["docstring"], description),
                "command": f"os-optics-kit {command}",
                "command_name": command, "requires": requires, "module": module, "kit_version": kit_version,
                "requires_line": (f"Needs os-optics ({requirement}), imported when the command runs; "
                                  "`--help` works without it.") if facts["engine"] else
                                 "Python standard library and os-optics-kit only.",
            }
            render = lambda text: C.choose_template(repo, "templates/SKILL.md", T.SKILL_MD,
                                                    {**values, "help_text": text}, [])
            values["help_text"] = fit_help(shown.stdout.decode("utf-8", "replace"), render)
            C.write_text(skill_path, C.choose_template(repo, "templates/SKILL.md", T.SKILL_MD, values, warnings))
            C.write_text(test_path, T.TOOL_TEST_PY.format(command=command, module=module,
                                                          class_name=class_name(module)))

        problems = validate_skill(skill_path, name, command)
        if problems:
            raise C.ContribError("SKILL_INVALID", "the skill page does not meet the SKILL.md format", problems=problems,
                                 hint="edit BUNDLE/tool/skills/NAME/SKILL.md and run --refresh")
        tests = run_tests(clone, module)

        if (package / "skills.py").is_file():
            indexed = clone.kit("skills", "index", "--write")
            if indexed.returncode != 0:
                raise C.ContribError("SKILLS_INDEX_FAILED", "os-optics-kit skills index --write refused the skill",
                                     problems=[C._tail(indexed.stdout or indexed.stderr)])
            skills_index = "regenerated by os-optics-kit skills index --write"
        else:
            skills_index = "not regenerated: this checkout has no skills indexer"
            warnings.append("SKILLS_INDEX_NOT_REGENERATED: run 'os-optics-kit skills index --write' once the kit has it")

        staged = [f"tools/os_optics_kit/{module}.py", f"skills/{name}", f"tests/test_{module}.py"]
        patch, committed = None, []
        subject = f"feat(tools): add the {command} command"
        if repo is not None:
            patch_dir = workdir / "patch"
            patch_dir.mkdir()
            paths = staged + ["tools/os_optics_kit/cli.py"] + (["skills.json"] if (clone.path / "skills.json").exists()
                                                                 else [])
            message = (f"{subject}\n\n{description}\n\nos-optics-kit {command} -> os_optics_kit.{module}.main; "
                       f"skill skills/{name}; tests: {tests['ran']} passed. Requires: {requires}.")
            patch = C.commit_and_format_patch(clone, paths, message, patch_dir)
            committed = C.committed_files(clone)

        files = []
        for rel in staged:
            path = clone.path / rel
            for item in ([path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file())):
                files.append(item.relative_to(clone.path).as_posix())
        pr_body = T.PR_BODY_TOOL.format(
            command=command, module=module, description=description,
            files_list="\n".join(f"- `{f}`" for f in (committed or files + ["tools/os_optics_kit/cli.py"])),
            help_without_engine="exit 0", test_result=f"{tests['ran']} tests, {tests['result']}",
            skills_index=skills_index, requires=requires,
            checklist="\n".join(f"- {item}" for item in [
                "[x] `main(argv) -> int`, registered in `COMMANDS`",
                "[x] `SKILL.md` front matter and sections complete, within 900 tokens",
                "[x] generated tests pass",
                "[x] commit signed off (DCO)" if repo else "[ ] commit signed off (DCO): no patch in this bundle",
            ]),
            kit_version=C.kit_version_of(clone.path),
        )

        if args.refresh:
            C.clear_bundle_outputs(out, "tool")
        else:
            C.prepare_bundle_dir(out, "tool", name, force=args.force)
        for rel in files:
            target = out / "tool" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(clone.path / rel, target)
        C.write_text(out / "PR_BODY.md", pr_body)
        bundle_patch = None
        if patch is not None:
            bundle_patch = out / patch.name
            shutil.copyfile(patch, bundle_patch)
        manifest = {
            "schema": C.BUNDLE_SCHEMA, "schema_version": 1, "kind": "tool", "name": name, "command": command,
            "module": module, "kit_version": C.kit_version_of(clone.path), "base_commit": base or None,
            "branch": branch, "patch": bundle_patch.name if bundle_patch else None,
            "pr": {"title": subject, "body": "PR_BODY.md", "upstream": args.upstream},
            "commands_entry": {command: [module, summary]},
            "requires": requires,
            "files": [{"path": rel, "sha256": C.sha256_file(clone.path / rel)} for rel in files],
            "verification": {"help": "exit 0", "help_without_engine": "exit 0", "tests": tests,
                             "skills_index": skills_index},
            "warnings": warnings,
        }
        pr_info = None
        if args.open_pr:
            plan = C.PullRequestPlan(branch=branch, title=subject, body_file=out / "PR_BODY.md", upstream=args.upstream)
            if args.dry_run:
                pr_info = {"opened": False, "reason": "DRY_RUN", "planned": plan.planned_commands()}
            elif repo is None:
                pr_info = {"opened": False, "reason": "NO_PATCH"}
            else:
                pr_info = C.open_pull_request(plan, clone)
            manifest["pr"]["result"] = pr_info
        C.write_text(out / "bundle.json", C.dump_json(manifest))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    opened = bool(pr_info and pr_info.get("opened"))
    if opened:
        next_step = f"pull request opened: {pr_info.get('url')}; follow the review there"
    elif args.refresh:
        next_step = C.submission_hint(out, bundle_patch)
    else:
        next_step = (f"edit {out / 'tool' / 'skills' / name / 'SKILL.md'} (When not to use, Outputs, Minimal "
                     f"example) and add tests to tool/tests/test_{module}.py, then run "
                     f"'os-optics-kit contrib tool --refresh {out}'; " + C.submission_hint(out, bundle_patch))
    return {
        "status": "PR_OPENED" if opened else "READY",
        "kind": "tool", "name": name, "command": command, "module": module, "dry_run": bool(args.dry_run),
        "bundle": str(out), "patch": str(bundle_patch) if bundle_patch else None, "pr_body": str(out / "PR_BODY.md"),
        "files": committed or files,
        "requires": requires,
        "verification": manifest["verification"],
        "warnings": warnings,
        **({"pr": pr_info} if pr_info else {}),
        "next_step": next_step,
    }
