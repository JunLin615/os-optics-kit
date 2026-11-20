"""scripts/case_tables.py: the README case tables generated from cases.json (no engine, no network)."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from scripts import case_tables

ROOT = Path(__file__).resolve().parents[1]
URL = "https://github.com/JunLin615/os-optics-kit/tree/main/cases/"


def entry(name, case_class, domain, band, catalog=None):
    return {"name": name, "class": case_class, "domain": domain, "catalog": catalog,
            "scale": {"band": band, "elements": 1, "sources": 1, "branches": 0}}


class CaseTablesTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "tools" / "os_optics_kit").mkdir(parents=True)
        shutil.copyfile(ROOT / "tools" / "os_optics_kit" / "case_taxonomy.py",
                        self.root / "tools" / "os_optics_kit" / "case_taxonomy.py")
        cases = {"b-lens": ("Focus | with a bar", "聚焦"), "a-lens": ("A lens", "一个镜头"),
                 "table": ("Table path", "平台光路")}
        for name, (en, zh) in cases.items():
            folder = self.root / "cases" / name
            folder.mkdir(parents=True)
            (folder / "README.md").write_text(f"```\n# not this\n```\n# {en}\n\n## Run\n", encoding="utf-8")
            (folder / "README.zh-CN.md").write_text(f"# {zh}\n", encoding="utf-8")
        index = {"cases": [entry("b-lens", "lens", ["gaussian-beam", "diffraction"], "small"),
                           entry("table", "free-space", ["beam-steering"], "medium", "2026-10-28.1"),
                           entry("a-lens", "lens", ["imaging"], "very-large")]}
        (self.root / "cases.json").write_text(json.dumps(index), encoding="utf-8")
        for name, heading in (("README.md", "## Cases"), ("README.zh-CN.md", "## 案例")):
            (self.root / name).write_text(f"# Kit\n\n{heading}\n\n{case_tables.START}\nold\n{case_tables.END}\n\n"
                                          "After the table.\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = case_tables.main(list(argv), root=self.root)
        return code, out.getvalue(), err.getvalue()

    def test_write_then_check(self):
        code, _out, err = self.run_main("--check")
        self.assertEqual(code, 1)
        self.assertIn("stale", err)
        code, out, _err = self.run_main("--write")
        self.assertEqual((code, json.loads(out)["files"]), (0, ["README.md", "README.zh-CN.md"]))
        self.assertEqual(self.run_main("--check")[0], 0)
        english = (self.root / "README.md").read_text(encoding="utf-8")
        self.assertEqual(english, f"""# Kit

## Cases

{case_tables.START}
### Lens

| Case | What it shows | Domain | Scale | Catalog |
| --- | --- | --- | --- | --- |
| [a-lens]({URL}a-lens) | A lens | Imaging | Very large | no |
| [b-lens]({URL}b-lens) | Focus \\| with a bar | Gaussian beams, Diffraction | Small | no |

### Free-space platform

| Case | What it shows | Domain | Scale | Catalog |
| --- | --- | --- | --- | --- |
| [table]({URL}table) | Table path | Beam steering | Medium | yes |

{case_tables.TEXT["en"]["note"]}
{case_tables.END}

After the table.
""")
        chinese = (self.root / "README.zh-CN.md").read_text(encoding="utf-8")
        self.assertIn("### 镜头\n\n| 案例 | 内容 | 领域 | 规模 | 目录 |", chinese)
        self.assertIn(f"| [b-lens]({URL}b-lens) | 聚焦 | 高斯光束、衍射 | 小 | 否 |", chinese)
        self.assertIn(f"### 自由空间平台\n\n| 案例 | 内容 | 领域 | 规模 | 目录 |\n| --- | --- | --- | --- | --- |\n"
                      f"| [table]({URL}table) | 平台光路 | 光束指向 | 中 | 是 |", chinese)
        self.assertNotIn("集成光路", chinese, "a class without cases has no table")
        self.assertIn("os-optics-kit cases vocabulary", chinese)
        # a hand edit inside the markers makes the check fail
        (self.root / "README.zh-CN.md").write_text(chinese.replace("| 小 |", "| 中 |"), encoding="utf-8")
        code, _out, err = self.run_main("--check")
        self.assertEqual(code, 1)
        self.assertIn("README.zh-CN.md", err)

    def test_markers_are_required(self):
        (self.root / "README.md").write_text("# Kit\n", encoding="utf-8")
        code, _out, err = self.run_main("--write")
        self.assertEqual(code, 1)
        self.assertIn("cases:start", err)
        with self.assertRaises(ValueError):
            case_tables.replace_block(f"{case_tables.END}\n{case_tables.START}\n", "x")

    def test_unclassified_cases_are_reported(self):
        index = json.loads((self.root / "cases.json").read_text(encoding="utf-8"))
        index["cases"].append({"name": "old-entry"})
        (self.root / "cases.json").write_text(json.dumps(index), encoding="utf-8")
        _code, _out, err = self.run_main("--write")
        self.assertIn("old-entry", err)
        self.assertNotIn("old-entry", (self.root / "README.md").read_text(encoding="utf-8"))

    def test_this_repository_tables_are_current(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(case_tables.main(["--check"]), 0)
        for name in ("README.md", "README.zh-CN.md"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertEqual((text.count(case_tables.START), text.count(case_tables.END)), (1, 1), name)


if __name__ == "__main__":
    unittest.main()
