"""Tests of ``os-optics-kit contrib`` that need no engine: argument checks, refusals, front matter, templates,
command registration, case/skill validation, the pull-request steps and patch generation (``git am`` round trip).

Run from the repository root: python -m pytest tests/test_contrib.py (or python -m unittest discover -s tests).
"""

from __future__ import annotations

import argparse
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
from unittest import mock

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
    pyproject and baseline, both top READMEs with their case tables and scripts/case_tables.py, no cases yet; one
    commit."""
    repo = where / "kit"
    package = repo / "tools" / "os_optics_kit"
    package.mkdir(parents=True)
    for module in (ROOT / "tools" / "os_optics_kit").glob("*.py"):
        shutil.copyfile(module, package / module.name)
    for folder in ("skills", "templates"):
        shutil.copytree(ROOT / folder, repo / folder, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("pyproject.toml", "baseline.json", "skills.json", "README.md", "README.zh-CN.md"):
        shutil.copyfile(ROOT / name, repo / name)
    (repo / "scripts").mkdir()
    shutil.copyfile(ROOT / "scripts" / "case_tables.py", repo / "scripts" / "case_tables.py")
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
#: the verified run of cases/ideal-lens-focus (the source of demo_case)
DEMO_VERIFIED = {"engine": "0.13.0", "catalog": None}
CLASSIFIED = {"class": "free-space", "domain": "gaussian-beam", "provenance": "self-built",
              "publication": "own-work", "tags": "beam-expander, focusing"}
ISO_RECORD = {"file": "images/iso.png", "kind": "system-view", "options": ["--views", "iso", "--size", "960x540"],
              "sha256": "0" * 64, "view": "iso"}
SECTION_RECORD = {"file": "images/section.png", "kind": "system-view",
                  "options": ["--section", "ALL", "--frame=scene", "--size", "1200x420"], "sha256": "0" * 64,
                  "view": "section"}


def case_args(**overrides) -> argparse.Namespace:
    """The classification options of ``contrib case`` (``None`` = not given)."""
    values = {"case_class": "free-space", "domain": "gaussian-beam", "tags": "beam-expander", "provenance": None,
              "publication": None, "source": None}
    values.update(overrides)
    return argparse.Namespace(**values)


def _replace(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, (path, old)
    path.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")


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


def case_values(case_dir: Path, classified: dict | None = None, language: str = "en") -> dict:
    verified = {"engine": "0.12.0", "catalog": None, "status": "PASS",
                "targets": {"targets": [{"target_id": "w", "metric": "beam_radius_mm", "observation_id": "out",
                                         "unit": "mm", "bounds": {"maximum": 2.0}, "value": 1.5,
                                         "assessment": "MET"}]}}
    return case_mod.prose_values("demo-case", "Demo", "A short summary.", ["summary"], classified or CLASSIFIED,
                                 verified, case_dir, [ISO_RECORD, SECTION_RECORD], language)


class TemplateTests(unittest.TestCase):
    def test_fill_drops_comments_and_refuses_unknown_placeholders(self):
        self.assertEqual(C.fill_template("x {{ a }} {{b}}", {"a": "1", "b": "2"}), "x 1 2\n")
        self.assertEqual(C.fill_template("# T\n\n<!-- how to\nfill -->\n\n{{a}}\n\n\n", {"a": "1"}), "# T\n\n1\n")
        with self.assertRaises(KeyError):
            C.fill_template("{{zzz}}", {})

    def test_optional_front_matter_line_dropped_when_empty(self):
        template = "---\na: {{a}}\nsource: {{source}}\nb: {{b}}\n---\nsource: {{source}} in text\n"
        self.assertEqual(C.fill_template(template, {"a": "1", "source": "", "b": "2"}),
                         "---\na: 1\nb: 2\n---\nsource:  in text\n")
        self.assertEqual(C.fill_template(template, {"a": "1", "source": "Ref 2020", "b": "2"}),
                         "---\na: 1\nsource: Ref 2020\nb: 2\n---\nsource: Ref 2020 in text\n")

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
            zh_values = case_values(Path(tmp), language="zh-CN")
        for name, filled in (("CASE.md", values), ("README.case.md", values), ("README.case.zh-CN.md", zh_values)):
            with self.subTest(template=name):
                text = C.render_template(None, name, filled, [])
                self.assertNotIn("{{", text)
                self.assertNotIn("<!--", text)
                self.assertNotIn("\n\n\n", text)
        case = C.render_template(None, "CASE.md", values, [])
        front, body = fm.split(case)
        self.assertEqual(fm.check_keys(front, kit_cases.FRONT_MATTER_KEYS, kit_cases.OPTIONAL_FRONT_MATTER_KEYS), [])
        self.assertEqual(list(front), list(kit_cases.FRONT_MATTER_KEYS))  # no source line for self-built work
        self.assertEqual([c for c, _ in kit_cases.check_front_matter(front, "demo-case")], [])
        self.assertEqual(fm.headings(body), list(kit_cases.SECTIONS))
        self.assertIn("| `w` | `beam_radius_mm` | `out` | <= 2 mm | 1.5 mm, MET |", case)
        readme = C.render_template(None, "README.case.md", values, [])
        self.assertEqual(readme.split("\n")[:3], ["# Demo", "", "English · [简体中文](README.zh-CN.md)"])
        self.assertEqual(kit_cases.shown_images(readme), ["images/iso.png", "images/section.png"])
        self.assertIn(f"![{case_mod.CAPTIONS['iso'][0]}](images/iso.png)", readme)
        self.assertIn("| `w` | 1.5 mm | <= 2 mm | MET |", readme)
        readme_zh = C.render_template(None, "README.case.zh-CN.md", zh_values, [])
        self.assertEqual(readme_zh.split("\n")[:3], ["# Demo", "", "[English](README.md) · 简体中文"])
        self.assertEqual(kit_cases.shown_images(readme_zh), ["images/iso.png", "images/section.png"])
        self.assertIn(f"![{case_mod.CAPTIONS['section'][1]}](images/section.png)", readme_zh)
        self.assertIn("不使用目录产品", readme_zh)

    def test_case_template_states_a_source_after_publication(self):
        classified = {"class": "lens", "domain": "imaging, diffraction", "provenance": "literature",
                      "publication": "public-domain", "source": "A. Author, Lens Design (1950), example 3",
                      "tags": "achromat, mtf"}
        with tempfile.TemporaryDirectory() as tmp:
            values = case_values(Path(tmp), classified)
        front, _ = fm.split(C.render_template(None, "CASE.md", values, []))
        self.assertEqual(list(front), list(kit_cases.FRONT_MATTER_ORDER))
        self.assertEqual(front["source"], classified["source"])
        self.assertEqual(fm.check_keys(front, kit_cases.FRONT_MATTER_KEYS, kit_cases.OPTIONAL_FRONT_MATTER_KEYS), [])
        self.assertEqual([c for c, _ in kit_cases.check_front_matter(front, "demo-case")], [])

    def test_images_block_and_captions(self):
        figure = {"file": "images/mtf.png", "kind": "figure", "script": "figures.py", "sha256": "0" * 64}
        block = case_mod.images_block("demo-case", [ISO_RECORD, figure])
        self.assertEqual(kit_cases.shown_images(block), ["images/iso.png", "images/mtf.png"])
        self.assertIn("Figure drawn by `figures.py`", block)
        self.assertIn("由 `figures.py` 绘制", case_mod.images_block("demo-case", [figure], "zh-CN"))
        top = dict(ISO_RECORD, file="images/path_horizontal.png", view="path_horizontal")
        self.assertIn("Path horizontal view", case_mod.caption(top))
        for language in ("en", "zh-CN"):
            empty = case_mod.images_block("demo-case", [], language)
            self.assertEqual(kit_cases.shown_images(empty), [])
            self.assertIn("os-optics-kit contrib case --refresh BUNDLE", empty)
            self.assertTrue(case_mod.PLACEHOLDER_LINE.fullmatch(empty))
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
            demo_case(case_dir)
            self.assertEqual(kit_cases.check_case(case_dir), [])
            self.assertEqual(case_mod.validate_case_dir(case_dir, "demo-case", DEMO_VERIFIED, {"summary"}), [])

    def test_case_dir_problems(self):
        with tempfile.TemporaryDirectory() as tmp:
            case_dir = Path(tmp) / "demo-case"
            case_files(case_dir, summary="s" * 301, body_extra="x" * 3000)
            (case_dir / "optical_scene.glb").write_bytes(b"glb")
            (case_dir / "ray_data.json").write_text("{}", encoding="utf-8")
            (case_dir / "images" / "big.png").write_bytes(b"0" * (400 * 1024 + 1))
            problems = "\n".join(case_mod.validate_case_dir(case_dir, "other-name", {"engine": "0.13.0",
                                                                                     "catalog": None}, set()))
            for needle in ("GENERATED_FILE: optical_scene.glb", "GENERATED_FILE: ray_data.json", "big.png",
                           "SUMMARY_TOO_LONG", "the budget is 700", "CASE.md name is", "CASE.md engine is",
                           "not os-optics-kit commands", "FRONT_MATTER_KEY", "IMAGES_RECORD: images.json is missing",
                           "FILE_MISSING: README.zh-CN.md"):
                self.assertIn(needle, problems)

    def test_case_dir_missing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            problems = case_mod.validate_case_dir(Path(tmp) / "x", "x", VERIFIED, set())
            self.assertIn("FILE_MISSING: CASE.md is missing", problems)

    def test_validation_reports_every_index_check_problem(self):
        """Parity with ``cases index --check``: whatever check_case reports, validate_case_dir reports too."""
        mutations = {
            "class": lambda d: _replace(d / "CASE.md", "class: lens", "class: telescope"),
            "tag": lambda d: _replace(d / "CASE.md", "tags: ideal-lens", "tags: catalog-free, ideal-lens"),
            "source": lambda d: _replace(d / "CASE.md", "publication: own-work",
                                         "publication: own-work\nsource: somewhere"),
            "order": lambda d: _replace(d / "CASE.md", "domain: gaussian-beam\nprovenance: self-built",
                                        "provenance: self-built\ndomain: gaussian-beam"),
            "images-record": lambda d: (d / "images.json").unlink(),
            "image-sha": lambda d: (d / "images" / "iso.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64),
            "readme-order": lambda d: _replace(d / "README.zh-CN.md", "](images/iso.png)", "](images/section.png)"),
            "zh-readme": lambda d: (d / "README.zh-CN.md").unlink(),
            "unrecorded-image": lambda d: (d / "images" / "top.png").write_bytes(b"\x89PNG\r\n\x1a\n"),
            "expected": lambda d: _replace(d / "expected.json", '"status": "PASS"', '"status": "GREAT"'),
        }
        for label, mutate in mutations.items():
            with self.subTest(mutation=label), tempfile.TemporaryDirectory() as tmp:
                case_dir = Path(tmp) / "demo-case"
                demo_case(case_dir)
                mutate(case_dir)
                checked = [f"{p['code']}: {p['message']}" for p in kit_cases.check_case(case_dir)]
                self.assertTrue(checked, label)
                validated = case_mod.validate_case_dir(case_dir, "demo-case", DEMO_VERIFIED, {"summary"})
                self.assertEqual([p for p in checked if p not in validated], [])

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
            (["missing-file.json", "--name", "ok-name", "--out", "unused", "--class", "lens", "--domain",
              "imaging", "--tags", "mtf"], "PROJECT_MISSING"),
            (["--refresh", "nowhere"], "BUNDLE_INVALID"),
            (["--bogus"], "USAGE"),
        ]
        for argv, code_name in cases:
            with self.subTest(argv=argv):
                code, doc = call(["case", *argv])
                self.assertEqual(code, 4)
                self.assertEqual(doc["status"], "ERROR")
                self.assertEqual(doc["error"]["code"], code_name)

    def test_classification_refusals_name_the_allowed_values(self):
        """Missing or unknown classification values: exit 4, BAD_ARGUMENT, before the project file is read."""
        base = ["missing-file.json", "--name", "ok-name", "--out", "unused"]
        good = ["--class", "lens", "--domain", "imaging", "--tags", "mtf"]
        cases = [
            ([], {"class", "domain", "tags"}),
            (["--class", "telescope", "--domain", "imaging", "--tags", "mtf"], {"class"}),
            (["--class", "lens", "--domain", "imaging,optics", "--tags", "mtf"], {"domain"}),
            (["--class", "lens", "--domain", "imaging,diffraction,dispersion,polarization", "--tags", "mtf"],
             {"domain"}),
            (["--class", "lens", "--domain", "imaging", "--tags", "catalog-free"], {"tags"}),
            (["--class", "lens", "--domain", "imaging", "--tags", "lens"], {"tags"}),
            (["--class", "lens", "--domain", "imaging", "--tags", "none"], {"tags"}),
            (["--class", "lens", "--domain", "imaging", "--tags", "mtf,mtf"], {"tags"}),
            ([*good, "--provenance", "blog"], {"provenance"}),
            ([*good, "--provenance", "literature"], {"publication", "source"}),
            ([*good, "--provenance", "literature", "--publication", "public-domain"], {"source"}),
            ([*good, "--source", "A paper"], {"source"}),
            ([*good, "--publication", "permission"], {"publication"}),
            ([*good, "--provenance", "patent", "--publication", "permission", "--source", "x" * 201], {"source"}),
        ]
        for extra, fields in cases:
            with self.subTest(extra=extra):
                code, doc = call(["case", *base, *extra])
                self.assertEqual((code, doc["status"], doc["error"]["code"]), (4, "ERROR", "BAD_ARGUMENT"))
                self.assertEqual(set(doc["error"]["allowed"]), fields)
                self.assertIn("cases vocabulary", doc["error"]["hint"])
        code, doc = call(["case", *base, "--class", "telescope", "--domain", "imaging", "--tags", "mtf"])
        self.assertEqual(doc["error"]["allowed"]["class"], ["lens", "free-space", "integrated-path"])
        # valid classifications pass on to the next step (the missing project file)
        for extra in (good, [*good, "--provenance", "literature", "--publication", "open-license", "--source",
                             "J. Doe, Optics Letters 1 (2020) 1"], ["--class", "free-space", "--domain",
                                                                   "Gaussian-Beam,diffraction", "--tags", "MTF"]):
            with self.subTest(extra=extra):
                self.assertEqual(call(["case", *base, *extra])[1]["error"]["code"], "PROJECT_MISSING")

    def test_classification_values(self):
        values = case_mod.classification(case_args(domain="gaussian-beam, diffraction", tags="focusing,MTF"))
        self.assertEqual(values, {"class": "free-space", "domain": "gaussian-beam, diffraction",
                                  "provenance": "self-built", "publication": "own-work", "tags": "focusing, mtf"})
        third_party = case_mod.classification(case_args(provenance="literature", publication="open-license",
                                                        source="  A.  Author,\n Book  "))
        self.assertEqual(list(third_party), ["class", "domain", "provenance", "publication", "source", "tags"])
        self.assertEqual(third_party["source"], "A. Author, Book")

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
            for extra in (["--name", "b"], ["--class", "lens"], ["--domain", "imaging"], ["--tags", "mtf"],
                          ["--provenance", "self-built"], ["--publication", "own-work"], ["--source", "x"]):
                with self.subTest(extra=extra):
                    code, doc = call(["case", "--refresh", str(bundle), *extra])
                    self.assertEqual((code, doc["error"]["code"]), (4, "USAGE"))


class ImagePlanTests(unittest.TestCase):
    def test_default_views(self):
        self.assertEqual(case_mod.default_views("free-space"), [("iso", "iso", "960x540", None)])
        self.assertEqual(case_mod.default_views("lens"), [("iso", "iso", "960x540", None),
                                                          ("section", "ALL", "1200x420", "scene")])

    def test_recorded_views_follow_images_json(self):
        figure = {"file": "images/mtf.png", "kind": "figure", "script": "figures.py", "sha256": "0" * 64}
        camera = {"file": "images/camera.png", "kind": "system-view", "options": ["--camera", "30,20"],
                  "sha256": "0" * 64, "view": "camera"}
        document = {"images": [ISO_RECORD, camera, SECTION_RECORD, figure]}
        self.assertEqual(case_mod.recorded_views(document), [("iso", "iso", "960x540", None),
                                                             ("camera", "30,20", "960x540", None),
                                                             ("section", "ALL", "1200x420", "scene")])
        self.assertIsNone(case_mod.recorded_views({"images": [figure]}))
        self.assertIsNone(case_mod.recorded_views(None))
        # the frame is recorded as one token (a negative box frame stays one argument); the split form of earlier
        # bundles is still read
        box = dict(SECTION_RECORD, options=["--section", "ALL", "--frame=-5,-5,-5,5,5,5", "--size", "1200x420"])
        split = dict(SECTION_RECORD, options=["--section", "ALL", "--frame", "scene", "--size", "800x300"])
        self.assertEqual(case_mod.recorded_views({"images": [ISO_RECORD, box]})[1],
                         ("section", "ALL", "1200x420", "-5,-5,-5,5,5,5"))
        self.assertEqual(case_mod.recorded_views({"images": [ISO_RECORD, split]})[1],
                         ("section", "ALL", "800x300", "scene"))

    def test_refresh_plan_completes_what_the_class_needs(self):
        iso, section = case_mod.default_views("lens")
        top = ("top", "top", "960x540", None)
        # a lens whose section was not rendered: the section is planned again
        self.assertEqual(case_mod.refresh_plan([iso], None, "lens"), [iso, section])
        # a lens whose iso was not rendered: a snapshot view goes first
        self.assertEqual(case_mod.refresh_plan([section], None, "lens"), [iso, section])
        # a recorded snapshot view later in the list is moved first, not replaced
        self.assertEqual(case_mod.refresh_plan([section, top], None, "lens"), [top, section])
        # a class edited to lens gets its section; a free-space case keeps what it records
        self.assertEqual(case_mod.refresh_plan([iso], [], "lens"), [iso, section])
        self.assertEqual(case_mod.refresh_plan([iso, top], None, "free-space"), [iso, top])
        self.assertEqual(case_mod.refresh_plan(None, None, "free-space"), [iso])
        self.assertEqual(case_mod.refresh_plan(None, [], "lens"), [iso, section])
        # recorded options are kept: no second section when the recorded one has its own frame and size
        boxed = ("section", "ALL", "1000x400", "-5,-5,-5,5,5,5")
        self.assertEqual(case_mod.refresh_plan([iso, boxed], None, "lens"), [iso, boxed])

    def test_refresh_plan_puts_unrendered_views_back_at_their_place(self):
        iso, section = case_mod.default_views("lens")
        top = ("top", "top", "960x540", None)
        entries = case_mod.unrendered_entries([(1, top)])
        self.assertEqual(entries, [{"index": 1, "view": "top", "argument": "top", "size": "960x540", "frame": None}])
        self.assertEqual(json.loads(json.dumps(entries)), entries)  # bundle.json keeps them as they are
        self.assertEqual(case_mod.refresh_plan([iso, section], entries, "lens"), [iso, top, section])
        self.assertEqual(case_mod.refresh_plan([iso, top, section], entries, "lens"), [iso, top, section])
        # the planned iso of a case none of whose views rendered
        self.assertEqual(case_mod.refresh_plan(None, case_mod.unrendered_entries([(0, iso), (1, section)]), "lens"),
                         [iso, section])
        # entries that are not views are ignored
        junk = [{"index": "1", "view": "top"}, {"index": 0}, "top", {"index": 2, "view": "top", "argument": "top",
                                                                    "size": "960x540", "frame": 3}]
        self.assertEqual(case_mod.refresh_plan([iso], junk, "free-space"), [iso])

    def test_images_command_renders_the_planned_views(self):
        iso, section = case_mod.default_views("lens")
        self.assertEqual(case_mod.images_command("demo-case", [iso, section]),
                         "os-optics-kit cases images demo-case --view iso --section ALL --frame=scene")
        self.assertEqual(case_mod.images_command("demo-case", [iso]), "os-optics-kit cases images demo-case --view iso")
        self.assertEqual(case_mod.images_command("demo-case", [("camera", "30,20", "640x360", None),
                                                               ("section", "L1", "1000x400", "-5,-5,-5,5,5,5")]),
                         "os-optics-kit cases images demo-case --camera 30,20 --section L1 --frame=-5,-5,-5,5,5,5 "
                         "--size 640x360 --section-size 1000x400")

    def test_unrendered_view_warns_with_the_recovery(self):
        def failing(*args, **kwargs):
            raise kit_cases.CaseError("IMAGE_TOO_LARGE", "iso stays over 409600 bytes")

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(kit_cases, "_render_view", failing):
            warnings: list[str] = []
            plan = case_mod.default_views("lens")
            records, missing = case_mod.render_system_views(
                "demo-case", plan, Path(tmp) / "run", Path(tmp), Path(tmp) / "case", {}, warnings)
        self.assertEqual((records, missing), ([], [(0, plan[0]), (1, plan[1])]))
        self.assertEqual(len(warnings), 2)
        self.assertTrue(warnings[0].startswith("IMAGE_NOT_RENDERED: images/iso.png (IMAGE_TOO_LARGE"))
        self.assertIn("os-optics view RUN --no-browser --snapshot DIR --views iso", warnings[0])
        self.assertIn("os-optics view RUN --no-browser --snapshot DIR --section ALL", warnings[1])
        for warning in warnings:
            self.assertIn("'os-optics-kit contrib case --refresh BUNDLE' renders this view again and puts it into "
                          "both READMEs", warning)
            self.assertIn("without a browser or the render extra", warning)


    def test_refresh_never_deletes_or_hashes_files_outside_the_case_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case_dir = root / "bundle" / "case"
            (case_dir / "images").mkdir(parents=True)
            victim = root / "bundle" / "victim.png"
            victim.write_bytes(b"\x89PNG\r\n\x1a\nvictim")
            (case_dir / "images" / "iso.png").write_bytes(b"\x89PNG\r\n\x1a\nold")
            run_dir = root / "run"
            run_dir.mkdir()
            (run_dir / "manifest.json").write_text("{}", encoding="utf-8")
            previous = {"images": [
                {"file": "images/iso.png", "kind": "system-view", "options": ["--views", "iso", "--size", "960x540"],
                 "sha256": "0" * 64, "view": "iso"},
                {"file": "images/../../victim.png", "kind": "system-view", "options": ["--views", "top"],
                 "sha256": "0" * 64, "view": "top"},
                {"file": "../victim.png", "kind": "figure", "script": "x.py", "sha256": "0" * 64},
                {"file": victim.as_posix(), "kind": "figure", "script": "x.py", "sha256": "0" * 64}]}
            with mock.patch.object(case_mod, "render_system_views", return_value=([], [])), \
                    mock.patch.object(kit_cases, "write_images_record", side_effect=lambda _d, records, _r: records), \
                    mock.patch.object(kit_cases, "run_identity", return_value={}):
                records, _missing = case_mod.record_images("demo-case", case_dir, run_dir, root, {}, [],
                                                           case_class="free-space", previous=previous)
            self.assertEqual(victim.read_bytes(), b"\x89PNG\r\n\x1a\nvictim")
            self.assertFalse((case_dir / "images" / "iso.png").exists(), "the case's own old view is replaced")
            self.assertEqual(records, [], "no outside file is hashed into the record")


ISO_LINE = f"![{case_mod.CAPTIONS['iso'][0]}](images/iso.png)"
SECTION_LINE = f"![{case_mod.CAPTIONS['section'][0]}](images/section.png)"


class ReadmeImagesTests(unittest.TestCase):
    """--refresh makes both READMEs show exactly the images rendered now: a refresh that renders a view
    missing before also shows it."""

    def test_block_rebuilt_in_order_keeping_captions(self):
        text = "# Demo\n\nIntro.\n\n![My own iso caption](images/iso.png)\n\n## Run it\n"
        updated = case_mod.replace_images(text, "demo-case", [ISO_RECORD, SECTION_RECORD])
        self.assertEqual(updated, "# Demo\n\nIntro.\n\n![My own iso caption](images/iso.png)\n\n"
                                  f"{SECTION_LINE}\n\n## Run it\n")
        # a view no longer rendered leaves the block; HTML images count as image lines
        html = '<img src="images/section.png" alt="section" width="600">'
        text = f"# Demo\n\n{html}\n\n{ISO_LINE}\n\nMore text.\n"
        self.assertEqual(case_mod.replace_images(text, "demo-case", [ISO_RECORD]),
                         f"# Demo\n\n{ISO_LINE}\n\nMore text.\n")
        self.assertEqual(case_mod.replace_images(text, "demo-case", [ISO_RECORD, SECTION_RECORD]),
                         f"# Demo\n\n{ISO_LINE}\n\n{html}\n\nMore text.\n")
        # nothing rendered: the block becomes the generated placeholder line
        self.assertEqual(case_mod.replace_images(text, "demo-case", []),
                         f"# Demo\n\n{case_mod.images_block('demo-case', [])}\n\nMore text.\n")

    def test_placeholder_of_this_and_earlier_versions_replaced(self):
        earlier = {"en": "No system view is recorded yet: `os-optics-kit cases images demo-case` in a kit checkout "
                         "renders and records it (no browser needed).",
                   "zh-CN": "尚未记录系统视图：在套件仓库中运行 `os-optics-kit cases images demo-case` 生成并记录（无需浏览器）。"}
        for language in ("en", "zh-CN"):
            for line in (case_mod.images_block("demo-case", [], language), earlier[language]):
                with self.subTest(language=language, line=line[:20]):
                    text = f"# Demo\n\nIntro.\n\n{line}\n\n## Run\n"
                    updated = case_mod.replace_images(text, "demo-case", [ISO_RECORD, SECTION_RECORD], language)
                    self.assertEqual(updated, "# Demo\n\nIntro.\n\n"
                                     + case_mod.images_block("demo-case", [ISO_RECORD, SECTION_RECORD], language)
                                     + "\n\n## Run\n")

    def test_hand_edited_images_are_left_alone(self):
        for text in (f"# Demo\n\n{ISO_LINE}\n\nProse between.\n\n{SECTION_LINE}\n",
                     f"# Demo\n\nSee {ISO_LINE} here.\n",
                     f"# Demo\n\n{ISO_LINE} {SECTION_LINE}\n",
                     f"# Demo\n\n{ISO_LINE}\n\n```\nx\n```\n\n{SECTION_LINE}\n",
                     "# Demo\n\nNo image and no placeholder.\n"):
            with self.subTest(text=text):
                self.assertIsNone(case_mod.replace_images(text, "demo-case", [ISO_RECORD]))
        # an image inside a code fence is not an image line of the README
        fenced = f"# Demo\n\n```md\n{ISO_LINE}\n```\n\n{case_mod.images_block('demo-case', [])}\n"
        self.assertEqual(case_mod.replace_images(fenced, "demo-case", [ISO_RECORD]),
                         f"# Demo\n\n```md\n{ISO_LINE}\n```\n\n{ISO_LINE}\n")

    def test_update_both_readmes(self):
        with tempfile.TemporaryDirectory() as tmp:
            case_dir = Path(tmp)
            C.write_text(case_dir / "README.md", f"# Demo\n\n{ISO_LINE}\n")
            zh_iso = f"![{case_mod.CAPTIONS['iso'][1]}](images/iso.png)"
            C.write_text(case_dir / "README.zh-CN.md", f"# 演示\n\n{zh_iso}\n\n文字 {zh_iso}\n")
            stuck = case_mod.update_readme_images("demo-case", case_dir, [ISO_RECORD, SECTION_RECORD])
            self.assertEqual(stuck, ["README.zh-CN.md"])
            self.assertEqual(kit_cases.shown_images((case_dir / "README.md").read_text(encoding="utf-8")),
                             ["images/iso.png", "images/section.png"])
            before = (case_dir / "README.md").read_bytes()
            self.assertEqual(case_mod.update_readme_images("demo-case", case_dir, [ISO_RECORD, SECTION_RECORD]),
                             ["README.zh-CN.md"])
            self.assertEqual((case_dir / "README.md").read_bytes(), before)  # already right: not rewritten


class PullRequestBodyTests(unittest.TestCase):
    def test_body_shows_classification_and_images(self):
        front = {"name": "demo-case", "title": "Demo", "summary": "A short summary.", "class": "lens",
                 "domain": "imaging, diffraction", "provenance": "literature", "publication": "public-domain",
                 "source": "A. Author, Lens Design (1950)", "tags": "achromat, mtf"}
        verified = {"engine": "0.13.0", "catalog": None, "status": "PASS", "execution": "COMPLETED",
                    "check": {"status": "ACCEPTED"}, "targets": {"targets": []}}
        figure = {"file": "images/mtf.png", "kind": "figure", "script": "figures.py", "sha256": "0" * 64}
        body = case_mod.case_pr_body(front, verified, None, {"files": []}, ["cases/demo-case/CASE.md"], True,
                                     "regenerated", [ISO_RECORD, SECTION_RECORD, figure])
        for needle in ("| Class | `lens` |", "| Domain | `imaging`, `diffraction` |", "| Tags | `achromat`, `mtf` |",
                       "| Provenance | `literature` |", "| Publication | `public-domain` |",
                       "| Source | A. Author, Lens Design (1950) |",
                       "- `images/iso.png`: system view `iso` (`os-optics view RUN --no-browser --snapshot DIR "
                       "--views iso --size 960x540`)",
                       "- `images/mtf.png`: figure drawn by `figures.py`",
                       "[x] 2 system views rendered by `os-optics view --no-browser --snapshot` and 1 figures",
                       "[x] `README.md` and `README.zh-CN.md` link each other and show the recorded images in order",
                       "[x] regenerated", "[x] commit signed off (DCO)"):
            self.assertIn(needle, body)
        self.assertLess(body.index("images/iso.png"), body.index("images/section.png"))
        bare = case_mod.case_pr_body({**front, "provenance": "self-built", "publication": "own-work",
                                      "source": ""}, verified, None, {"files": []}, [], False, None, [])
        self.assertNotIn("| Source |", bare)
        self.assertIn("[ ] 0 system views", bare)
        self.assertIn("[ ] commit signed off (DCO): no patch in this bundle", bare)
        self.assertIn("[ ] `cases.json` and the README case tables: the entry is in bundle.json; in a kit checkout "
                      "run `os-optics-kit cases index --write` and `python scripts/case_tables.py --write`", bare)


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
            self.assertEqual(case_mod.update_case_indexes(clone),
                             "`cases.json` and the README case tables regenerated by `os-optics-kit cases index "
                             "--write` and `python scripts/case_tables.py --write`")
            paths = case_mod.index_paths(clone, "demo-case")
            self.assertEqual(paths, ["cases/demo-case", "cases.json", "README.md", "README.zh-CN.md"])
            patches = tmp / "patches"
            patches.mkdir()
            patch = C.commit_and_format_patch(clone, paths, "feat(cases): add demo-case", patches)
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
            # the README case tables CI checks are current, with a row for the new case in both languages
            tables = subprocess.run([sys.executable, "scripts/case_tables.py", "--check"], cwd=applied,
                                    capture_output=True, text=True)
            self.assertEqual(tables.returncode, 0, tables.stdout + tables.stderr)
            self.assertIn("README.md", git("show", "--name-only", "--format=", "HEAD", cwd=applied).split())
            for readme in ("README.md", "README.zh-CN.md"):
                self.assertIn("cases/demo-case", (applied / readme).read_text(encoding="utf-8"))

    def test_case_tables_skipped_in_a_checkout_without_the_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = make_kit_repo(tmp)
            git("rm", "--quiet", "scripts/case_tables.py", cwd=repo)
            git("commit", "--quiet", "-m", "older checkout", cwd=repo)
            clone = C.make_clone(repo, C.resolve_base(repo, None), "contrib/case-demo-case", tmp / "work")
            demo_case(clone.path / "cases" / "demo-case")
            self.assertIn("no README case tables", case_mod.update_case_indexes(clone))
            # a checkout whose README has no case table markers is refused, naming the script
            repo2 = make_kit_repo(tmp / "second")
            (repo2 / "README.md").write_text("# Kit\n", encoding="utf-8")
            git("commit", "--quiet", "-am", "no tables", cwd=repo2)
            clone2 = C.make_clone(repo2, C.resolve_base(repo2, None), "contrib/case-demo-case", tmp / "work2")
            demo_case(clone2.path / "cases" / "demo-case")
            with self.assertRaises(C.ContribError) as ctx:
                case_mod.update_case_indexes(clone2)
            self.assertEqual(ctx.exception.code, "CASES_INDEX_FAILED")
            self.assertIn("case_tables.py", ctx.exception.message)

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
