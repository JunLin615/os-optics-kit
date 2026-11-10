"""os-optics-kit skills: index check, install idempotence and refusal, budgets, and the front matter parser."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import math
from pathlib import Path
import shutil
import unittest

from os_optics_kit import _frontmatter as fm
from os_optics_kit import skills
from os_optics_kit._resources import data_root
from tests.workflow_fixtures import ROOT, TempDir

SKILL = """---
name: {name}
description: Does one thing; use it when that thing is needed.
version: 0.1.0
command: {command}
requires: os-optics>=0.13,<0.14
kind: tool
---

# {name}

## When to use

- a

## When not to use

- b

## Inputs

- c

## Outputs

- d

## Minimal example

```
os-optics-kit {name} --help
```

## Limits

- e

## Version

0.1.0
"""


def call(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = skills.main(argv)
    return code, out.getvalue(), err.getvalue()


def fake_kit(root, names=None, **overrides):
    """A kit root with a valid skill per CLI tool command (``overrides[name]`` replaces one SKILL.md text)."""
    for name in names or skills.tool_commands():
        directory = Path(root) / "skills" / name
        directory.mkdir(parents=True)
        text = overrides.get(name, SKILL.format(name=name, command=f"os-optics-kit {name}"))
        with open(directory / "SKILL.md", "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    return Path(root)


class RepositorySkillsTests(unittest.TestCase):
    def test_index_is_current_and_every_skill_is_valid(self):
        code, out, err = call(["index", "--check", "--root", str(ROOT)])
        self.assertEqual(code, 0, out + err)
        self.assertEqual(json.loads(out)["status"], "CURRENT")
        entries, problems = skills.collect(ROOT)
        self.assertEqual(problems, [])
        self.assertTrue(entries)

    def test_every_cli_tool_has_a_skill_within_budget(self):
        index = json.loads((ROOT / "skills.json").read_text(encoding="utf-8"))
        self.assertEqual((index["schema"], index["schema_version"]), ("os-optics-kit.skills", 1))
        self.assertEqual(index["budgets"], {"SKILL.md": 900, "AGENT_GUIDE.md": 1500, "CASE.md": 700})
        by_name = {entry["name"]: entry for entry in index["skills"]}
        self.assertEqual([entry["name"] for entry in index["skills"]], sorted(by_name))
        for name in skills.tool_commands():
            with self.subTest(skill=name):
                entry = by_name[name]
                text = (ROOT / entry["path"]).read_text(encoding="utf-8")
                self.assertEqual(entry["command"], f"os-optics-kit {name}")
                self.assertEqual(entry["tokens"], math.ceil(len(text) / 4))
                self.assertLessEqual(entry["tokens"], 900)
                self.assertLessEqual(len(entry["description"]), 300)
                self.assertEqual(fm.parse(text)["version"], entry["version"])

    def test_template_is_well_formed(self):
        self.assertEqual(skills.check_template((ROOT / "templates" / "SKILL.md").read_text(encoding="utf-8")), [])

    def test_list_and_show(self):
        code, out, _err = call(["list"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), json.loads((data_root() / "skills.json").read_text(encoding="utf-8")))
        self.assertNotIn("\n", out.strip())
        code, out, _err = call(["show", "sweep"])
        self.assertEqual(code, 0)
        self.assertEqual(out, (data_root() / "skills" / "sweep" / "SKILL.md").read_text(encoding="utf-8"))
        code, out, err = call(["show", "no-such-skill"])
        self.assertEqual((code, out), (4, ""))
        self.assertEqual(json.loads(err)["error"]["code"], "SKILL_UNKNOWN")

    def test_usage_errors_exit_4(self):
        for argv in ([], ["index"], ["index", "--check", "--write"], ["install"], ["nope"]):
            with self.subTest(argv=argv):
                code, out, err = call(argv)
                self.assertEqual((code, out), (4, ""))
                self.assertEqual(json.loads(err)["error"]["code"], "BAD_ARGUMENT")
        code, out, _err = call(["--help"])
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("usage: os-optics-kit skills"))


class IndexCheckTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TempDir()
        self.root = self._tmp.__enter__()

    def tearDown(self):
        self._tmp.__exit__(None, None, None)

    def problems(self, text, name="sweep"):
        fake_kit(self.root, **{name: text})
        _entries, problems = skills.collect(self.root)
        return [p for p in problems if p.startswith(f"{name}:")]

    def test_write_then_check_then_stale(self):
        fake_kit(self.root)
        code, out, _err = call(["index", "--check", "--root", str(self.root)])
        self.assertEqual((code, json.loads(out)["status"]), (1, "STALE"))
        code, out, _err = call(["index", "--write", "--root", str(self.root)])
        self.assertEqual((code, json.loads(out)["status"]), (0, "WRITTEN"))
        code, out, _err = call(["index", "--write", "--root", str(self.root)])
        self.assertEqual((code, json.loads(out)["status"]), (0, "UNCHANGED"))
        code, out, _err = call(["index", "--check", "--root", str(self.root)])
        self.assertEqual((code, json.loads(out)["status"]), (0, "CURRENT"))
        written = (self.root / "skills.json").read_bytes()
        self.assertNotIn(b"\r", written)
        entry = json.loads(written)["skills"][0]
        self.assertEqual(list(entry), ["name", "kind", "description", "version", "command", "requires", "path",
                                       "tokens", "sha256"])
        path = self.root / "skills" / "sweep" / "SKILL.md"
        path.write_text(path.read_text(encoding="utf-8").replace("- e", "- f"), encoding="utf-8", newline="\n")
        code, out, _err = call(["index", "--check", "--root", str(self.root)])
        self.assertEqual((code, json.loads(out)["status"]), (1, "STALE"))

    def test_invalid_skill_is_refused_and_nothing_is_written(self):
        fake_kit(self.root, sweep=SKILL.format(name="other", command="os-optics-kit sweep"))
        code, out, _err = call(["index", "--write", "--root", str(self.root)])
        self.assertEqual((code, json.loads(out)["status"]), (1, "INVALID"))
        self.assertFalse((self.root / "skills.json").exists())

    def test_missing_tool_skill(self):
        fake_kit(self.root, names=[name for name in skills.tool_commands() if name != "plot"])
        _entries, problems = skills.collect(self.root)
        self.assertEqual(problems, ["CLI tool command 'plot' has no skill skills/plot/"])

    def test_front_matter_and_field_problems(self):
        good = SKILL.format(name="sweep", command="os-optics-kit sweep")
        self.assertEqual(self.problems(good), [])
        cases = {
            "name 'Sweep' differs": good.replace("name: sweep", "name: Sweep"),
            "missing key 'requires'": good.replace("requires: os-optics>=0.13,<0.14\n", ""),
            "unknown key 'author'": good.replace("kind: tool\n", "kind: tool\nauthor: x\n"),
            "version '1.0' is not": good.replace("version: 0.1.0", "version: 1.0"),
            "kind 'helper' must be": good.replace("kind: tool", "kind: helper"),
            "description is 301 characters": good.replace(
                "description: Does one thing; use it when that thing is needed.", "description: " + "x" * 301),
            "command 'os-optics-kit sweeps' must be": good.replace("command: os-optics-kit sweep",
                                                                  "command: os-optics-kit sweeps"),
            "the engine requirement of this kit": good.replace("os-optics>=0.13,<0.14", "os-optics>=0.12"),
            "front matter: line 1": good[4:],
            "front matter: line 8: duplicate key": good.replace("kind: tool", "kind: tool\nname: sweep"),
        }
        for expected, text in cases.items():
            with self.subTest(expected=expected):
                shutil.rmtree(self.root / "skills", ignore_errors=True)
                found = self.problems(text)
                self.assertTrue(any(expected in p for p in found), found)

    def test_sections_budget_host_names_and_line_endings(self):
        good = SKILL.format(name="sweep", command="os-optics-kit sweep")
        cases = {
            "missing section '## Limits'": good.replace("## Limits", "## Caveats"),
            "sections out of order": good.replace("## Inputs\n\n- c\n\n## Outputs\n\n- d",
                                                  "## Outputs\n\n- d\n\n## Inputs\n\n- c"),
            "appears more than once": good + "\n## Version\n\nagain\n",
            "budget 900": good + "x" * 3600,
            "names the agent host or protocol 'MCP'": good.replace("- e", "- served over MCP"),
            "LF line endings": good.replace("\n", "\r\n"),
        }
        for expected, text in cases.items():
            with self.subTest(expected=expected):
                shutil.rmtree(self.root / "skills", ignore_errors=True)
                fake_kit(self.root, sweep=text)
                if "\r" in text:
                    (self.root / "skills" / "sweep" / "SKILL.md").write_bytes(text.encode("utf-8"))
                _entries, problems = skills.collect(self.root)
                self.assertTrue(any(expected in p for p in problems), problems)

    def test_budget_boundary(self):
        good = SKILL.format(name="sweep", command="os-optics-kit sweep")
        exact = good + "x" * (900 * 4 - len(good))
        self.assertEqual(fm.token_estimate(exact), 900)
        self.assertEqual(self.problems(exact), [])
        shutil.rmtree(self.root / "skills")
        self.assertTrue(self.problems(exact + "x"))

    def test_guide_and_contribution_kinds(self):
        fake_kit(self.root)
        guide = SKILL.format(name="read-first", command="none").replace("kind: tool\n", "")
        directory = self.root / "skills" / "read-first"
        directory.mkdir()
        (directory / "SKILL.md").write_text(guide, encoding="utf-8", newline="\n")
        entries, problems = skills.collect(self.root)
        self.assertEqual(problems, [])
        self.assertEqual(next(e for e in entries if e["name"] == "read-first")["kind"], "guide")


class InstallTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TempDir()
        self.tmp = self._tmp.__enter__()
        # a packaged-data layout (as in the wheel's os_optics_kit/_data): skills/ and skills.json only
        self.root = fake_kit(self.tmp / "data")
        (self.root / "skills" / "sweep" / "example.json").write_text("{}\n", encoding="utf-8", newline="\n")
        self.assertEqual(call(["index", "--write", "--root", str(self.root)])[0], 0)
        self.dest = self.tmp / "dest"

    def tearDown(self):
        self._tmp.__exit__(None, None, None)

    def install(self, *extra):
        code, out, err = call(["install", "--dest", str(self.dest), "--root", str(self.root), *extra])
        return code, (json.loads(out) if out else None), (json.loads(err) if err else None)

    def test_install_is_idempotent(self):
        code, first, _ = self.install("--only", "sweep,plot")
        self.assertEqual((code, first["status"]), (0, "INSTALLED"))
        self.assertEqual(first["written"], ["sweep/SKILL.md", "sweep/example.json", "plot/SKILL.md", "skills.json"])
        self.assertEqual((self.dest / "sweep" / "SKILL.md").read_bytes(),
                         (self.root / "skills" / "sweep" / "SKILL.md").read_bytes())
        code, again, _ = self.install("--only", "sweep,plot")
        self.assertEqual((code, again["status"], again["written"]), (0, "UNCHANGED", []))
        index = json.loads((self.dest / "skills.json").read_text(encoding="utf-8"))
        self.assertEqual([(e["name"], e["path"]) for e in index["skills"]],
                         [("plot", "plot/SKILL.md"), ("sweep", "sweep/SKILL.md")])
        code, everything, _ = self.install()
        self.assertEqual(code, 0)
        self.assertIn("sweep/SKILL.md", everything["unchanged"])
        index = json.loads((self.dest / "skills.json").read_text(encoding="utf-8"))
        self.assertEqual(len(index["skills"]), len(skills.tool_commands()))
        self.assertEqual(self.install()[1]["status"], "UNCHANGED")

    def test_differing_file_is_refused_without_force_and_nothing_is_written(self):
        self.install("--only", "sweep")
        changed = self.dest / "sweep" / "SKILL.md"
        changed.write_text("my edit\n", encoding="utf-8")
        (self.dest / "notes.txt").write_text("mine\n", encoding="utf-8")
        before = sorted(p.relative_to(self.dest).as_posix() for p in self.dest.rglob("*"))
        code, out, error = self.install()
        self.assertEqual((code, out), (4, None))
        self.assertEqual((error["error"]["code"], error["error"]["paths"]), ("DEST_FILE_DIFFERS", ["sweep/SKILL.md"]))
        self.assertEqual(sorted(p.relative_to(self.dest).as_posix() for p in self.dest.rglob("*")), before)
        self.assertEqual(changed.read_text(encoding="utf-8"), "my edit\n")
        code, forced, _ = self.install("--force")
        self.assertEqual(code, 0)
        self.assertEqual(forced["overwritten"], ["sweep/SKILL.md"])
        self.assertEqual(changed.read_bytes(), (self.root / "skills" / "sweep" / "SKILL.md").read_bytes())
        self.assertEqual((self.dest / "notes.txt").read_text(encoding="utf-8"), "mine\n")

    def test_foreign_index_file_is_refused(self):
        self.dest.mkdir()
        (self.dest / "skills.json").write_text('{"mine": true}\n', encoding="utf-8")
        code, _out, error = self.install("--only", "sweep")
        self.assertEqual((code, error["error"]["paths"]), (4, ["skills.json"]))
        self.assertFalse((self.dest / "sweep").exists())
        self.assertEqual(self.install("--only", "sweep", "--force")[0], 0)

    def test_unknown_skill_and_bad_destination(self):
        code, _out, error = self.install("--only", "sweep,nope")
        self.assertEqual((code, error["error"]["code"]), (4, "SKILL_UNKNOWN"))
        self.assertFalse(self.dest.exists())
        self.dest.write_text("a file", encoding="utf-8")
        code, _out, error = self.install()
        self.assertEqual((code, error["error"]["code"]), (4, "DEST_NOT_A_DIRECTORY"))


class FrontMatterTests(unittest.TestCase):
    def test_parse_and_body(self):
        fields, body = fm.split("---\nname: a\ndescription: x: y, z\n---\n# A\n\n## One\n")
        self.assertEqual(fields, {"name": "a", "description": "x: y, z"})
        self.assertEqual(body, "# A\n\n## One\n")
        self.assertEqual(fm.split("---\r\nname: a\r\n---\r\nbody\r\n")[0], {"name": "a"})
        self.assertEqual(fm.headings("## One\n```\n## not a heading\n```\n## Two\n"), ["One", "Two"])

    def test_strict_errors(self):
        for text, message in (
            ("name: a\n---\n", "line 1"),
            ("---\nname: a\n", "no closing"),
            ("---\n---\n", "empty"),
            ("---\nname: a\nname: b\n---\n", "duplicate key 'name'"),
            ("---\n  name: a\n---\n", "invalid key"),
            ("---\nName: a\n---\n", "invalid key"),
            ("---\nname:\n---\n", "non-empty value"),
            ("---\nname:a\n---\n", "one space"),
            ("---\n- a\n---\n", "expected 'key: value'"),
            ("---\n\nname: a\n---\n", "expected 'key: value'"),
        ):
            with self.subTest(text=text):
                with self.assertRaises(fm.FrontMatterError) as caught:
                    fm.split(text)
                self.assertIn(message, str(caught.exception))

    def test_render_round_trip_and_checks(self):
        fields = {"name": "a", "summary": "one line"}
        self.assertEqual(fm.parse(fm.render(fields) + "body\n"), fields)
        with self.assertRaises(fm.FrontMatterError):
            fm.render({"name": "two\nlines"})
        self.assertEqual(fm.check_keys({"a": "1", "c": "3"}, ("a", "b"), ("d",)),
                         ["missing key 'b'", "unknown key 'c'"])
        self.assertEqual(fm.token_estimate(""), 0)
        self.assertEqual(fm.token_estimate("abcde"), 2)


if __name__ == "__main__":
    unittest.main()
