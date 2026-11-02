"""Tests of ``os-optics-kit contrib`` that need no engine: argument checks, refusals, front matter, templates,
command registration, case/skill validation, the pull-request steps and patch generation (``git am`` round trip).

Run from the repository root: python -m pytest tests/test_contrib.py (or python -m unittest discover -s tests).
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
try:
    import os_optics_kit  # noqa: F401
except ImportError:  # a plain checkout without an install
    sys.path.insert(0, str(ROOT / "tools"))

from os_optics_kit import _frontmatter as fm  # noqa: E402
from os_optics_kit import cases as kit_cases  # noqa: E402
from os_optics_kit import contrib  # noqa: E402
from os_optics_kit.contrib import _common as C  # noqa: E402
from os_optics_kit.contrib import case as case_mod  # noqa: E402
from os_optics_kit.contrib import tool as tool_mod  # noqa: E402

HAS_GIT = shutil.which("git") is not None

HELPER = '''"""Print a greeting for a name.

Writes one line to standard output.

>>> greeting("run")
'hello run'
"""

import argparse


def greeting(name):
    return f"hello {name}"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Print a greeting for a name.")
    parser.add_argument("name")
    args = parser.parse_args(argv)
    print(greeting(args.name))
    return 0
'''


def call(argv: list[str]) -> tuple[int, dict | str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = contrib.main(argv)
    text = out.getvalue()
    try:
        return code, json.loads(text)
    except ValueError:
        return code, text


def git(*args: str, cwd: Path) -> str:
    result = subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false", *args], cwd=cwd,
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)}: {result.stderr}")
    return result.stdout


def make_kit_repo(where: Path) -> Path:
    """A small os-optics-kit checkout: the package's top-level modules, the skills and their index, the templates,
    pyproject and baseline, no cases yet; one commit."""
    repo = where / "kit"
    package = repo / "tools" / "os_optics_kit"
    package.mkdir(parents=True)
    for module in (ROOT / "tools" / "os_optics_kit").glob("*.py"):
        shutil.copyfile(module, package / module.name)
    for folder in ("skills", "templates"):
        shutil.copytree(ROOT / folder, repo / folder, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("pyproject.toml", "baseline.json", "skills.json"):
        shutil.copyfile(ROOT / name, repo / name)
    (repo / ".gitattributes").write_bytes(b"* text=auto eol=lf\n*.png binary\n")
    git("init", "--quiet", "-b", "main", cwd=repo)
    git("config", "user.name", "Kit Contributor", cwd=repo)
    git("config", "user.email", "contributor@example.com", cwd=repo)
    git("add", "-A", cwd=repo)
    git("commit", "--quiet", "-m", "chore: kit", cwd=repo)
    return repo


def fresh_clone(repo: Path, where: Path) -> Path:
    target = where / "applied"
    git("clone", "--quiet", str(repo), str(target), cwd=where)
    git("config", "user.name", "Reviewer", cwd=target)
    git("config", "user.email", "reviewer@example.com", cwd=target)
    return target


def demo_case(target: Path) -> None:
    """A valid case named after ``target``: a copy of cases/ideal-lens-focus (checkable without the engine)."""
    shutil.copytree(ROOT / "cases" / "ideal-lens-focus", target,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "run*", "out"))
    text = (target / "CASE.md").read_text(encoding="utf-8")
    (target / "CASE.md").write_text(text.replace("name: ideal-lens-focus", f"name: {target.name}", 1),
                                    encoding="utf-8", newline="\n")


def case_files(case_dir: Path, *, summary: str = "A short summary.", body_extra: str = "") -> None:
    case_dir.mkdir(parents=True, exist_ok=True)
    C.write_text(case_dir / "project.json", "{}\n")
    C.write_text(case_dir / "expected.json", "{}\n")
    C.write_text(case_dir / "README.md", "# Demo\n\n![Top view](images/top.png)\n")
    (case_dir / "images").mkdir(exist_ok=True)
    (case_dir / "images" / "top.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    front = {"name": "demo-case", "title": "Demo", "summary": summary, "engine": "0.12.0", "catalog": "none",
             "project": "project.json", "tools": "summary", "tags": "demo", "license": "MIT"}
    body = "\n".join(f"## {s}\n\ntext\n" for s in case_mod.REQUIRED_SECTIONS) + body_extra
    C.write_text(case_dir / "CASE.md", fm.render(front) + "\n# Demo\n\n" + body)


VERIFIED = {"engine": "0.12.0", "catalog": None}


class FrontMatterTests(unittest.TestCase):
    """The parser itself (os_optics_kit._frontmatter) is tested in test_skills; contrib only uses it."""

    def test_rewrite_front_matter_keeps_order_and_body(self):
        text = fm.render({"name": "a", "engine": "0.11.0", "catalog": "none"}) + "\n## What it shows\n"
        updated = case_mod.rewrite_front_matter(text, {"engine": "0.12.0", "unknown": "x"})
        self.assertEqual(fm.split(updated), ({"name": "a", "engine": "0.12.0", "catalog": "none"},
                                             "\n## What it shows\n"))
        with self.assertRaises(fm.FrontMatterError):
            case_mod.rewrite_front_matter("no front matter\n", {})

    def test_names(self):
        self.assertEqual(C.check_name("beam-expander-2x"), "beam-expander-2x")
        for bad in ("Beam", "a--b", "-a", "a_b", "x" * 41, ""):
            with self.subTest(bad=bad), self.assertRaises(C.ContribError):
                C.check_name(bad)
        self.assertEqual(C.split_list(" a, b ,,"), ["a", "b"])
        self.assertEqual(C.split_list("none"), [])


SKILL_VALUES = {"name": "hello", "description": "Print a greeting.", "kind": "tool", "command": "os-optics-kit hello",
                "requires": "none", "kit_version": "0.1.0", "when_to_use": "- w", "when_not_to_use": "- n",
                "inputs": "- i", "outputs": "- o", "example": "os-optics-kit hello --help", "limits": "- l"}


def case_values(case_dir: Path) -> dict:
    verified = {"engine": "0.12.0", "catalog": None, "status": "PASS",
                "targets": {"targets": [{"target_id": "w", "metric": "beam_radius_mm", "observation_id": "out",
                                         "unit": "mm", "bounds": {"maximum": 2.0}, "value": 1.5,
                                         "assessment": "MET"}]}}
    return case_mod.prose_values("demo-case", "Demo", "A short summary.", ["summary"], ["demo"], verified,
                                 case_dir, ["images/iso.png"])


class TemplateTests(unittest.TestCase):
    def test_fill_drops_comments_and_refuses_unknown_placeholders(self):
        self.assertEqual(C.fill_template("x {{ a }} {{b}}", {"a": "1", "b": "2"}), "x 1 2\n")
        self.assertEqual(C.fill_template("# T\n\n<!-- how to\nfill -->\n\n{{a}}\n\n\n", {"a": "1"}), "# T\n\n1\n")
        with self.assertRaises(KeyError):
            C.fill_template("{{zzz}}", {})

    def test_checkout_template_preferred_and_an_unfillable_one_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "templates").mkdir()
            warnings: list[str] = []
            packaged = C.render_template(None, "SKILL.md", SKILL_VALUES, warnings)
            self.assertEqual(C.render_template(repo, "SKILL.md", SKILL_VALUES, warnings), packaged)
            (repo / "templates" / "SKILL.md").write_text("<!-- guide -->\nrepo {{name}}\n", encoding="utf-8")
            self.assertEqual(C.render_template(repo, "SKILL.md", SKILL_VALUES, warnings), "repo hello\n")
            (repo / "templates" / "SKILL.md").write_text("repo {{unknown_key}}", encoding="utf-8")
            self.assertEqual(C.render_template(repo, "SKILL.md", SKILL_VALUES, warnings), packaged)
            self.assertEqual(len(warnings), 1)
            self.assertTrue(warnings[0].startswith("TEMPLATE_NOT_USED: templates/SKILL.md"))
            with self.assertRaises(C.ContribError) as ctx:
                C.render_template(repo, "NOPE.md", SKILL_VALUES, warnings)
            self.assertEqual(ctx.exception.code, "TEMPLATE_MISSING")

    def test_repository_templates_fill_completely_into_valid_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            values = case_values(Path(tmp))
        for name in ("CASE.md", "README.case.md"):
            with self.subTest(template=name):
                text = C.render_template(None, name, values, [])
                self.assertNotIn("{{", text)
                self.assertNotIn("<!--", text)
                self.assertNotIn("\n\n\n", text)
        case = C.render_template(None, "CASE.md", values, [])
        front, body = fm.split(case)
        self.assertEqual(fm.check_keys(front, kit_cases.FRONT_MATTER_KEYS), [])
        self.assertEqual(fm.headings(body), list(kit_cases.SECTIONS))
        self.assertIn("| `w` | `beam_radius_mm` | `out` | <= 2 mm | 1.5 mm, MET |", case)
        readme = C.render_template(None, "README.case.md", values, [])
        self.assertIn("![Isometric view](images/iso.png)", readme)
        self.assertIn("| `w` | 1.5 mm | <= 2 mm | MET |", readme)
        skill = C.render_template(None, "SKILL.md", SKILL_VALUES, [])
        front, body = fm.split(skill)
        self.assertEqual(front["kind"], "tool")
        self.assertEqual(fm.check_sections(body, tool_mod.REQUIRED_SECTIONS), [])
        self.assertNotIn("<!--", skill)

    def test_targets_table(self):
        rows = [{"target_id": "w", "metric": "waist_radius_x_mm", "observation_id": "out", "unit": "mm",
                 "bounds": {"target_value": 2.0, "tolerance": 0.02}, "value": 1.9961, "assessment": "MET"},
                {"target_id": "r", "metric": "beam_radius_mm", "observation_id": "out", "unit": "mm",
                 "bounds": {"maximum": 0.05}, "value": None, "assessment": "INDETERMINATE"}]
        table = case_mod.targets_table(rows)
        self.assertIn("| `w` | `waist_radius_x_mm` | `out` | 2 ± 0.02 mm | 1.9961 mm, MET |", table)
        self.assertIn("<= 0.05 mm | -, INDETERMINATE", table)
        self.assertIn("| `r` | - | <= 0.05 mm | INDETERMINATE |", case_mod.results_rows(rows))
        many = case_mod.targets_table(rows * 10)
        self.assertIn("8 more targets in `expected.json`", many)


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.cli = (ROOT / "tools" / "os_optics_kit" / "cli.py").read_text(encoding="utf-8")

    def _commands(self, text):
        node = C._commands_node(__import__("ast").parse(text))
        return {k.value: v for k, v in zip(node.keys, [__import__("ast").literal_eval(v) for v in node.values])}

    def test_inserted_before_cases(self):
        updated = C.register_command(self.cli, "hello", "hello_kit", "print a greeting")
        commands = list(self._commands(updated))
        self.assertEqual(commands[commands.index("hello") + 1], "cases")
        self.assertEqual(self._commands(updated)["hello"], ("hello_kit", "print a greeting"))

    def test_long_entry_wrapped(self):
        updated = C.register_command(self.cli, "hello", "hello_kit", "x" * 90)
        self.assertTrue(all(len(line) <= 120 for line in updated.splitlines()))
        self.assertEqual(self._commands(updated)["hello"][0], "hello_kit")

    def test_existing_command_refused(self):
        with self.assertRaises(C.ContribError) as ctx:
            C.register_command(self.cli, "summary", "x", "y")
        self.assertEqual(ctx.exception.code, "COMMAND_EXISTS")


class HelperAnalysisTests(unittest.TestCase):
    def test_accepts_main_with_argv(self):
        facts = tool_mod.analyze_helper(HELPER, set())
        self.assertFalse(facts["engine"])
        self.assertEqual(facts["third_party"], [])

    def test_engine_and_third_party_imports(self):
        source = "import numpy\nfrom os_optics.results import load_run\nfrom .workflow_common import x\n" \
                 "def main(argv=None):\n    return 0\n"
        facts = tool_mod.analyze_helper(source, {"workflow_common"})
        self.assertTrue(facts["engine"])
        self.assertEqual(facts["third_party"], ["numpy"])

    def test_refusals(self):
        for source, needle in (("def run(argv):\n    return 0\n", "no top-level"),
                               ("def main():\n    return 0\n", "takes no argument"),
                               ("from .missing import x\ndef main(argv):\n    return 0\n", "missing"),
                               ("import cases\ndef main(argv):\n    return 0\n", "never import cases"),
                               ("def main(argv:\n", "does not parse")):
            with self.subTest(needle=needle), self.assertRaises(C.ContribError) as ctx:
                tool_mod.analyze_helper(source, set())
            self.assertEqual(ctx.exception.code, "HELPER_INVALID")
            self.assertIn(needle, json.dumps(ctx.exception.as_dict()))

    def test_text_helpers(self):
        self.assertEqual(tool_mod.first_sentence("Print a greeting. More.\n\nNext."), "Print a greeting.")
        self.assertEqual(tool_mod.command_summary("Print a greeting for a name."), "print a greeting for a name")
        self.assertEqual(tool_mod.command_summary("CSV export."), "CSV export")
        self.assertEqual(tool_mod.when_to_use('Title.\n\n>>> x()\n\nWrites one line.', "d"), "Writes one line.")
        self.assertEqual(tool_mod.when_to_use("Title only.", "d"), "d")
        self.assertEqual(tool_mod.class_name("power_budget_csv"), "PowerBudgetCsvTests")


class ValidationTests(unittest.TestCase):
    def test_case_dir_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            case_dir = Path(tmp) / "demo-case"
            case_files(case_dir)
            self.assertEqual(case_mod.validate_case_dir(case_dir, "demo-case", VERIFIED, {"summary"}), [])

    def test_case_dir_problems(self):
        with tempfile.TemporaryDirectory() as tmp:
            case_dir = Path(tmp) / "demo-case"
            case_files(case_dir, summary="s" * 301, body_extra="x" * 3000)
            (case_dir / "optical_scene.glb").write_bytes(b"glb")
            (case_dir / "images" / "big.png").write_bytes(b"0" * (400 * 1024 + 1))
            problems = "\n".join(case_mod.validate_case_dir(case_dir, "other-name", {"engine": "0.13.0",
                                                                                     "catalog": None}, set()))
            for needle in ("optical_scene.glb", "big.png", "summary longer", "budget 700", "name is",
                           "engine is", "not os-optics-kit commands"):
                self.assertIn(needle, problems)

    def test_case_dir_missing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            problems = case_mod.validate_case_dir(Path(tmp), "x", VERIFIED, set())
            self.assertIn("CASE.md missing", problems)

    def test_skill_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SKILL.md"
            path.write_text(C.render_template(None, "SKILL.md", SKILL_VALUES, []), encoding="utf-8")
            self.assertEqual(tool_mod.validate_skill(path, "hello", "hello"), [])
            problems = "\n".join(tool_mod.validate_skill(path, "other", "other"))
            self.assertIn("directory is 'other'", problems)
            self.assertIn("expected 'os-optics-kit other'", problems)
            path.write_text(path.read_text(encoding="utf-8").replace("## Limits", "## Notes") + "x" * 4000,
                            encoding="utf-8")
            problems = "\n".join(tool_mod.validate_skill(path, "hello", "hello"))
            self.assertIn("missing section '## Limits'", problems)
            self.assertIn("budget 900", problems)

    def test_bundle_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "b"
            C.check_bundle_target(out, "case", "a", force=False)
            out.mkdir()
            (out / "other.txt").write_text("x", encoding="utf-8")
            with self.assertRaises(C.ContribError) as ctx:
                C.check_bundle_target(out, "case", "a", force=True)
            self.assertEqual(ctx.exception.code, "OUT_NOT_EMPTY")
            (out / "bundle.json").write_text(json.dumps({"kind": "case", "name": "a"}), encoding="utf-8")
            with self.assertRaises(C.ContribError) as ctx:
                C.check_bundle_target(out, "case", "a", force=False)
            self.assertEqual(ctx.exception.code, "BUNDLE_EXISTS")
            C.prepare_bundle_dir(out, "case", "a", force=True)
            self.assertEqual(list(out.iterdir()), [])


class OwnSkillTests(unittest.TestCase):
    def test_contribution_skills_meet_the_format(self):
        for name, command in (("contrib-case", "contrib case"), ("contrib-tool", "contrib tool")):
            with self.subTest(name=name):
                path = ROOT / "skills" / name / "SKILL.md"
                self.assertEqual(tool_mod.validate_skill(path, name, command), [])
                front, _ = fm.split(path.read_text(encoding="utf-8"))
                self.assertEqual(front["kind"], "contribution")
                self.assertLessEqual(len(front["description"]), 300)

    def test_doc_names_both_commands(self):
        text = (ROOT / "docs" / "contributing-with-an-agent.md").read_text(encoding="utf-8")
        for needle in ("os-optics-kit contrib case", "os-optics-kit contrib tool", "--open-pr", "--refresh"):
            self.assertIn(needle, text)


class CliArgumentTests(unittest.TestCase):
    def test_usage(self):
        code, text = call([])
        self.assertEqual(code, 0)
        self.assertIn("contrib {case,tool}", text)
        self.assertEqual(call(["publish"])[0], 4)

    def test_case_argument_refusals(self):
        cases = [
            ([], "USAGE"),
            (["p.json"], "USAGE"),
            (["p.json", "--name", "Bad_Name"], "NAME_INVALID"),
            (["missing-file.json", "--name", "ok-name", "--out", "unused"], "PROJECT_MISSING"),
            (["--refresh", "nowhere"], "BUNDLE_INVALID"),
            (["--bogus"], "USAGE"),
        ]
        for argv, code_name in cases:
            with self.subTest(argv=argv):
                code, doc = call(["case", *argv])
                self.assertEqual(code, 4)
                self.assertEqual(doc["status"], "ERROR")
                self.assertEqual(doc["error"]["code"], code_name)

    def test_tool_argument_refusals(self):
        with tempfile.TemporaryDirectory() as tmp:
            text_file = Path(tmp) / "notes.txt"
            text_file.write_text("x", encoding="utf-8")
            cases = [
                ([], "USAGE"),
                (["h.py", "--name", "ok", "--module", "cli"], "MODULE_INVALID"),
                (["h.py", "--name", "ok", "--module", "Bad"], "MODULE_INVALID"),
                ([str(text_file), "--name", "ok", "--out", str(Path(tmp) / "o")], "HELPER_MISSING"),
                (["h.py", "--name", "ok", "--command", "Bad"], "NAME_INVALID"),
            ]
            for argv, code_name in cases:
                with self.subTest(argv=argv):
                    code, doc = call(["tool", *argv])
                    self.assertEqual((code, doc["error"]["code"]), (4, code_name))

    def test_refresh_rejects_new_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            (bundle / "bundle.json").write_text(json.dumps({"schema": C.BUNDLE_SCHEMA, "kind": "case",
                                                            "name": "a"}), encoding="utf-8")
            code, doc = call(["case", "--refresh", str(bundle), "--name", "b"])
            self.assertEqual((code, doc["error"]["code"]), (4, "USAGE"))


class FakeRunner:
    def __init__(self, login="someone", authenticated=True):
        self.login, self.authenticated, self.calls = login, authenticated, []

    def __call__(self, cmd, check=False, **kwargs):
        self.calls.append(cmd)
        code, out = 0, b""
        if cmd[:3] == ["gh", "auth", "status"]:
            code = 0 if self.authenticated else 1
        elif cmd[:2] == ["gh", "api"]:
            out = self.login.encode() + b"\n"
        elif cmd[:3] == ["gh", "pr", "create"]:
            out = b"https://github.com/JunLin615/os-optics-kit/pull/7\n"
        return subprocess.CompletedProcess(cmd, code, out, b"")


@unittest.skipUnless(shutil.which("gh"), "gh is not installed")
class PullRequestTests(unittest.TestCase):
    def setUp(self):
        self.plan = C.PullRequestPlan(branch="contrib/case-a", title="feat(cases): add a", body_file=Path("PR_BODY.md"))
        self.clone = C.Clone(path=Path("clone"), base="abc", branch="contrib/case-a", identity=("n", "e"))

    def test_fork_push_create(self):
        runner = FakeRunner()
        result = C.open_pull_request(self.plan, self.clone, runner)
        self.assertEqual(result, {"opened": True, "url": "https://github.com/JunLin615/os-optics-kit/pull/7",
                                  "head": "someone:contrib/case-a"})
        joined = [" ".join(map(str, cmd)) for cmd in runner.calls]
        self.assertTrue(any(c.startswith("gh repo fork JunLin615/os-optics-kit") for c in joined))
        self.assertTrue(any("push https://github.com/someone/os-optics-kit.git HEAD:refs/heads/contrib/case-a" in c
                            for c in joined))
        self.assertIn("--head someone:contrib/case-a", joined[-1])

    def test_owner_pushes_without_fork(self):
        runner = FakeRunner(login="JunLin615")
        result = C.open_pull_request(self.plan, self.clone, runner)
        self.assertEqual(result["head"], "contrib/case-a")
        self.assertFalse(any(cmd[:3] == ["gh", "repo", "fork"] for cmd in runner.calls))

    def test_not_authenticated_stops(self):
        runner = FakeRunner(authenticated=False)
        self.assertEqual(C.open_pull_request(self.plan, self.clone, runner),
                         {"opened": False, "reason": "GH_NOT_AUTHENTICATED"})
        self.assertEqual(len(runner.calls), 1)

    def test_planned_commands(self):
        planned = self.plan.planned_commands()
        self.assertEqual(planned[0], "gh auth status")
        self.assertIn("--body-file PR_BODY.md", planned[-1])


@unittest.skipUnless(HAS_GIT, "git is not installed")
class PatchTests(unittest.TestCase):
    def test_case_patch_applies_with_git_am(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = make_kit_repo(tmp)
            base = C.resolve_base(repo, None)
            work = tmp / "work"
            work.mkdir()
            clone = C.make_clone(repo, base, "contrib/case-demo-case", work)
            case_dir = clone.path / "cases" / "demo-case"
            demo_case(case_dir)
            self.assertEqual(case_mod.update_case_indexes(clone), "regenerated by os-optics-kit cases index --write")
            patches = tmp / "patches"
            patches.mkdir()
            patch = C.commit_and_format_patch(clone, ["cases/demo-case", "cases.json"], "feat(cases): add demo-case",
                                              patches)
            text = patch.read_text(encoding="utf-8")
            self.assertIn("Signed-off-by: Kit Contributor <contributor@example.com>", text)
            self.assertIn("GIT binary patch", text)
            self.assertEqual(git("status", "--porcelain", cwd=repo), "")  # the contributor's checkout is untouched
            applied = fresh_clone(repo, tmp)
            git("am", str(patch), cwd=applied)
            index = json.loads((applied / "cases.json").read_text(encoding="utf-8"))
            self.assertEqual([c["name"] for c in index["cases"]], ["demo-case"])
            self.assertEqual(index["cases"][0]["project_sha256"],
                             C.sha256_file(applied / "cases" / "demo-case" / "project.json"))
            images = sorted(p.name for p in (case_dir / "images").iterdir())
            for image in images:
                self.assertEqual((applied / "cases" / "demo-case" / "images" / image).read_bytes(),
                                 (case_dir / "images" / image).read_bytes())
            env = dict(os.environ, PYTHONPATH=str(applied / "tools"))
            checked = subprocess.run([sys.executable, "-m", "os_optics_kit", "cases", "index", "--check"],
                                     cwd=applied, env=env, capture_output=True, text=True)
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_case_index_refuses_an_invalid_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = make_kit_repo(tmp)
            clone = C.make_clone(repo, C.resolve_base(repo, None), "contrib/case-demo-case", tmp / "work")
            case_files(clone.path / "cases" / "demo-case")  # project.json and expected.json are not valid
            with self.assertRaises(C.ContribError) as ctx:
                case_mod.update_case_indexes(clone)
            self.assertEqual(ctx.exception.code, "CASES_INDEX_FAILED")

    def test_tool_end_to_end_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = make_kit_repo(tmp)
            helper = tmp / "hello_kit.py"
            helper.write_text(HELPER, encoding="utf-8")
            out = tmp / "bundle"
            code, doc = call(["tool", str(helper), "--name", "hello-kit", "--repo", str(repo), "--out", str(out),
                              "--dry-run", "--open-pr"])
            self.assertEqual(code, 0, doc)
            self.assertEqual(doc["status"], "READY")
            self.assertEqual(doc["pr"]["reason"], "DRY_RUN")
            self.assertEqual(doc["verification"]["tests"]["result"], "OK")
            self.assertEqual(doc["requires"], "none")
            self.assertEqual(sorted(doc["files"]), ["skills.json", "skills/hello-kit/SKILL.md", "tests/test_hello_kit.py",
                                                    "tools/os_optics_kit/cli.py", "tools/os_optics_kit/hello_kit.py"])
            self.assertEqual(doc["verification"]["skills_index"], "regenerated by os-optics-kit skills index --write")
            self.assertEqual(doc["warnings"], [])
            manifest = json.loads((out / "bundle.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["commands_entry"], {"hello-kit": ["hello_kit", "print a greeting for a name"]})
            self.assertNotIn(str(tmp), json.dumps(manifest))  # no machine-local paths in the bundle manifest
            skill = (out / "tool" / "skills" / "hello-kit" / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("usage: os-optics-kit hello-kit", skill)
            self.assertIn("Writes one line to standard output.", skill)

            applied = fresh_clone(repo, tmp)
            git("am", str(out / manifest["patch"]), cwd=applied)
            env = dict(os.environ, PYTHONPATH=str(applied / "tools"))
            result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p",
                                     "test_hello_kit.py"], cwd=applied, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Ran 5 tests", result.stderr)
            ran = subprocess.run([sys.executable, "-m", "os_optics_kit", "hello-kit", "world"], cwd=applied, env=env,
                                 capture_output=True, text=True)
            self.assertEqual(ran.stdout.strip(), "hello world")
            indexed = subprocess.run([sys.executable, "-m", "os_optics_kit", "skills", "index", "--check"],
                                     cwd=applied, env=env, capture_output=True, text=True)
            self.assertEqual(indexed.returncode, 0, indexed.stdout + indexed.stderr)
            self.assertIn("hello-kit", (applied / "skills.json").read_text(encoding="utf-8"))

            # an edited skill page is kept by --refresh and lands in the new patch
            skill_path = out / "tool" / "skills" / "hello-kit" / "SKILL.md"
            skill_path.write_text(skill.replace("- What the options above state; exit code 0 on success.",
                                                "- One line on standard output."), encoding="utf-8")
            code, doc = call(["tool", "--refresh", str(out), "--repo", str(repo), "--dry-run"])
            self.assertEqual(code, 0, doc)
            self.assertIn("One line on standard output.",
                          (out / json.loads((out / "bundle.json").read_text("utf-8"))["patch"]).read_text("utf-8"))

    def test_tool_refusals_with_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = make_kit_repo(tmp)
            bad = tmp / "bad.py"
            bad.write_text("def main():\n    return 0\n", encoding="utf-8")
            code, doc = call(["tool", str(bad), "--name", "bad", "--repo", str(repo), "--out", str(tmp / "o")])
            self.assertEqual((code, doc["error"]["code"]), (4, "HELPER_INVALID"))
            helper = tmp / "summary_like.py"
            helper.write_text(HELPER, encoding="utf-8")
            code, doc = call(["tool", str(helper), "--name", "summary", "--repo", str(repo), "--out", str(tmp / "o")])
            self.assertEqual((code, doc["error"]["code"]), (4, "TOOL_EXISTS"))  # skills/summary exists
            code, doc = call(["tool", str(helper), "--name", "my-summary", "--command", "summary", "--repo", str(repo),
                              "--out", str(tmp / "o")])
            self.assertEqual((code, doc["error"]["code"]), (4, "COMMAND_EXISTS"))
            engine_at_import = tmp / "eager.py"
            engine_at_import.write_text(HELPER.replace("import argparse", "import argparse\nimport not_installed_pkg"),
                                        encoding="utf-8")
            code, doc = call(["tool", str(engine_at_import), "--name", "eager", "--repo", str(repo),
                              "--out", str(tmp / "o")])
            self.assertEqual((code, doc["error"]["code"]), (1, "HELP_FAILED"))
            code, doc = call(["tool", str(helper), "--name", "hello", "--repo", str(tmp), "--out", str(tmp / "o")])
            self.assertEqual((code, doc["error"]["code"]), (4, "REPO_INVALID"))

    def test_tool_without_checkout_builds_files_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            helper = tmp / "hello_kit.py"
            helper.write_text(HELPER, encoding="utf-8")
            outside = tmp / "elsewhere"
            outside.mkdir()
            with contextlib.chdir(outside):
                code, doc = call(["tool", str(helper), "--name", "hello-kit", "--out", str(tmp / "b"), "--dry-run"])
            self.assertEqual(code, 0, doc)
            self.assertIsNone(doc["patch"])
            self.assertTrue(any(w.startswith("NO_KIT_CHECKOUT") for w in doc["warnings"]))
            self.assertTrue((tmp / "b" / "tool" / "tests" / "test_hello_kit.py").is_file())


if __name__ == "__main__":
    unittest.main()
