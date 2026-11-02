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

from os_optics_kit import contrib
from os_optics_kit.contrib import _common as C
from os_optics_kit.contrib import _templates as T
from os_optics_kit.contrib import case as case_mod
from os_optics_kit.contrib import tool as tool_mod

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
    """A minimal os-optics-kit checkout: the package's top-level modules, pyproject, one commit."""
    repo = where / "kit"
    package = repo / "tools" / "os_optics_kit"
    package.mkdir(parents=True)
    for module in (ROOT / "tools" / "os_optics_kit").glob("*.py"):
        shutil.copyfile(module, package / module.name)
    shutil.copyfile(ROOT / "pyproject.toml", repo / "pyproject.toml")
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
    C.write_text(case_dir / "CASE.md", C.render_front_matter(front) + "\n# Demo\n\n" + body)


VERIFIED = {"engine": "0.12.0", "catalog": None}


class FrontMatterTests(unittest.TestCase):
    def test_round_trip(self):
        keys = {"name": "a-b", "description": "Does x: and y.", "version": "0.1.0"}
        parsed, body = C.parse_front_matter(C.render_front_matter(keys) + "\nbody\n")
        self.assertEqual(parsed, keys)
        self.assertEqual(body, "\nbody\n")

    def test_refusals(self):
        for text in ("name: x\n", "---\nname: x\n", "---\nname x\n---\n", "---\nname: a\nname: b\n---\n",
                     "---\nName: x\n---\n"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                C.parse_front_matter(text)

    def test_sections_skip_code_and_check_order(self):
        body = "## A\n```sh\n## not a heading\n```\n## B\n## Extra\n"
        self.assertEqual(C.body_sections(body), ["A", "B", "Extra"])
        self.assertEqual(C.check_sections(["A", "B"], ["A", "B"]), [])
        self.assertIn("section '## C' missing", C.check_sections(["A", "B"], ["A", "B", "C"]))
        self.assertEqual(len(C.check_sections(["B", "A"], ["A", "B"])), 1)

    def test_token_estimate_and_names(self):
        self.assertEqual(C.token_estimate("a" * 9), 3)
        self.assertEqual(C.check_name("beam-expander-2x"), "beam-expander-2x")
        for bad in ("Beam", "a--b", "-a", "a_b", "x" * 41, ""):
            with self.subTest(bad=bad), self.assertRaises(C.ContribError):
                C.check_name(bad)
        self.assertEqual(C.split_list(" a, b ,,"), ["a", "b"])
        self.assertEqual(C.split_list("none"), [])


class TemplateTests(unittest.TestCase):
    def test_fill_and_unknown_placeholders(self):
        self.assertEqual(C.fill_template("x {{ a }} {{b}}", {"a": "1", "b": "2"}), "x 1 2")
        with self.assertRaises(KeyError):
            C.fill_template("{{zzz}}", {})

    def test_repo_template_used_only_when_fillable(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "templates").mkdir()
            warnings: list[str] = []
            self.assertEqual(C.choose_template(repo, "templates/X.md", "builtin {{a}}", {"a": "1"}, warnings),
                             "builtin 1")
            (repo / "templates" / "X.md").write_text("repo {{a}}", encoding="utf-8")
            self.assertEqual(C.choose_template(repo, "templates/X.md", "builtin {{a}}", {"a": "1"}, warnings),
                             "repo 1")
            (repo / "templates" / "X.md").write_text("repo {{unknown_key}}", encoding="utf-8")
            self.assertEqual(C.choose_template(repo, "templates/X.md", "builtin {{a}}", {"a": "1"}, warnings),
                             "builtin 1")
            self.assertTrue(warnings and warnings[0].startswith("TEMPLATE_NOT_USED"))

    def test_builtin_templates_fill_completely(self):
        values = {key: "v" for key in ("name", "title", "summary", "engine", "catalog", "tools", "tags", "status",
                                       "catalog_line", "targets_table", "images_markdown", "files_list",
                                       "run_commands")}
        for template in (T.CASE_MD, T.README_CASE_MD):
            self.assertNotIn("{{", C.fill_template(template, values))
        skill = {key: "v" for key in ("name", "description", "when_to_use", "command", "command_name", "requires",
                                      "module", "help_text", "requires_line", "kit_version")}
        text = C.fill_template(T.SKILL_MD, skill)
        front, body = C.parse_front_matter(text)
        self.assertEqual(C.check_sections(C.body_sections(body), tool_mod.REQUIRED_SECTIONS), [])
        self.assertEqual(front["kind"], "tool")
        case = C.fill_template(T.CASE_MD, values)
        self.assertEqual(C.check_sections(C.body_sections(C.parse_front_matter(case)[1]),
                                          case_mod.REQUIRED_SECTIONS), [])

    def test_targets_table(self):
        rows = [{"target_id": "w", "metric": "waist_radius_x_mm", "observation_id": "out", "unit": "mm",
                 "bounds": {"target_value": 2.0, "tolerance": 0.02}, "value": 1.9961, "assessment": "MET"},
                {"target_id": "r", "metric": "beam_radius_mm", "observation_id": "out", "unit": "mm",
                 "bounds": {"maximum": 0.05}, "value": None, "assessment": "INDETERMINATE"}]
        table = case_mod.targets_table(rows)
        self.assertIn("| `w` | `waist_radius_x_mm` | `out` | 2 ± 0.02 mm | 1.996 | MET |", table)
        self.assertIn("<= 0.05 mm | - | INDETERMINATE", table)
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
            values = {key: "v" for key in ("when_to_use", "command_name", "module", "help_text", "requires_line",
                                           "kit_version")}
            values.update(name="hello", description="Print a greeting.", command="os-optics-kit hello",
                          requires="none")
            path.write_text(C.fill_template(T.SKILL_MD, values), encoding="utf-8")
            self.assertEqual(tool_mod.validate_skill(path, "hello", "hello"), [])
            problems = "\n".join(tool_mod.validate_skill(path, "other", "other"))
            self.assertIn("directory is 'other'", problems)
            self.assertIn("expected 'os-optics-kit other'", problems)
            path.write_text(path.read_text(encoding="utf-8").replace("## Limits", "## Notes") + "x" * 4000,
                            encoding="utf-8")
            problems = "\n".join(tool_mod.validate_skill(path, "hello", "hello"))
            self.assertIn("'## Limits' missing", problems)
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
            case_files(case_dir)
            front, _ = C.parse_front_matter((case_dir / "CASE.md").read_text(encoding="utf-8"))
            entry = C.case_index_entry(case_dir, front)
            self.assertEqual(entry["files"], ["CASE.md", "README.md", "expected.json", "images/top.png",
                                              "project.json"])
            C.update_cases_index(clone.path, entry)
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
            self.assertEqual(index["cases"][0]["project_sha256"],
                             C.sha256_file(applied / "cases" / "demo-case" / "project.json"))
            self.assertEqual((applied / "cases" / "demo-case" / "images" / "top.png").read_bytes(),
                             (case_dir / "images" / "top.png").read_bytes())

    def test_cases_index_replaces_and_sorts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tools" / "os_optics_kit").mkdir(parents=True)
            shutil.copyfile(ROOT / "tools" / "os_optics_kit" / "_version.py",
                            root / "tools" / "os_optics_kit" / "_version.py")
            for name in ("b", "a", "b"):
                C.update_cases_index(root, {"name": name, "title": name})
            index = json.loads((root / "cases.json").read_text(encoding="utf-8"))
            self.assertEqual([c["name"] for c in index["cases"]], ["a", "b"])
            self.assertEqual(index["schema"], "os-optics-kit.cases")

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
            self.assertEqual(sorted(doc["files"]), ["skills/hello-kit/SKILL.md", "tests/test_hello_kit.py",
                                                    "tools/os_optics_kit/cli.py", "tools/os_optics_kit/hello_kit.py"])
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

            # an edited skill page is kept by --refresh and lands in the new patch
            skill_path = out / "tool" / "skills" / "hello-kit" / "SKILL.md"
            skill_path.write_text(skill.replace("What the options above state; exit code 0 on success.",
                                                "One line on standard output."), encoding="utf-8")
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
