"""Agent guidance: case images and system views route to the browser-free engine snapshot; the kit's `render` and
`present` are the presentation tools that need Chrome. The rule is stated in every entry point an agent reads first,
the case README templates come in both languages with one placeholder set, and AGENT_GUIDE.md keeps its budget."""

import math
import re
import unittest

from os_optics_kit import _frontmatter as fm
from os_optics_kit.contrib import _common as C
from tests.workflow_fixtures import ROOT

ENGINE_SNAPSHOT = "os-optics view RUN --no-browser --snapshot"


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def section(text: str, heading: str) -> str:
    """The body of the ``## heading`` section of a Markdown page ('' when absent)."""
    match = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return match.group(1) if match else ""


def placeholders(template: str) -> set[str]:
    """The ``{{key}}`` names a template fills, its HTML comments excluded (``contrib`` drops them)."""
    return {m.group(1) for m in C.PLACEHOLDER_RE.finditer(C.COMMENT_RE.sub("", template))}


class AgentGuideTests(unittest.TestCase):
    def setUp(self):
        self.text = read("AGENT_GUIDE.md")

    def test_route_row_names_the_browser_free_engine_snapshot(self):
        rows = [line for line in section(self.text, "Route by task").splitlines() if line.startswith("| ")]
        view_rows = [row for row in rows if "`os-optics view" in row]
        self.assertTrue(view_rows, "no route row names os-optics view")
        row = view_rows[0]
        for needle in ("--no-browser", "--snapshot", "--views iso", "--section ALL", "cases images NAME"):
            self.assertIn(needle, row)
        self.assertIn("case images", row.lower())
        self.assertIn("no browser", row.lower())

    def test_render_row_is_the_presentation_route(self):
        rows = [line for line in section(self.text, "Route by task").splitlines() if "`render RUN" in line]
        self.assertEqual(len(rows), 1)
        self.assertIn("presentation", rows[0].lower())
        self.assertIn("chrome", rows[0].lower())

    def test_cases_are_found_by_classification(self):
        row = next(line for line in self.text.splitlines() if line.startswith("| Start from a worked design"))
        self.assertIn("--class", row)
        self.assertIn("cases vocabulary", row)

    def test_contribute_a_case_covers_the_image_path(self):
        body = section(self.text, "Contribute a case")
        for needle in ("contrib case", "--class", "--domain", "cases images NAME", "README.zh-CN.md",
                       "cases index --write", "--check", "cases vocabulary", "--open-pr"):
            self.assertIn(needle, body)

    def test_within_budget(self):
        budget = fm.BUDGETS["AGENT_GUIDE.md"]
        self.assertLessEqual(math.ceil(len(self.text) / 4), budget)


class SkillSplitTests(unittest.TestCase):
    def test_render_and_contrib_case_state_the_split(self):
        for name in ("render", "contrib-case"):
            with self.subTest(skill=name):
                text = read(f"skills/{name}/SKILL.md")
                self.assertIn(ENGINE_SNAPSHOT, text)
                self.assertRegex(text, r"[Cc]ase images (and plain system views )?(need no browser|come from the engine)")
                self.assertIn("no `render` extra", text)
                self.assertIn("Chrome or Chromium", text)

    def test_present_and_plot_route_system_views_to_the_engine(self):
        for name in ("present", "plot"):
            with self.subTest(skill=name):
                self.assertIn("os-optics view RUN --no-browser --snapshot", read(f"skills/{name}/SKILL.md"))

    def test_run_case_uses_the_catalog_filter(self):
        text = read("skills/run-case/SKILL.md")
        self.assertNotIn("catalog-free", text)
        for needle in ("--catalog RELEASE|none", "--class", "cases vocabulary"):
            self.assertIn(needle, text)


class PublicPagesTests(unittest.TestCase):
    def test_install_notes_name_the_engine_snapshot_in_both_languages(self):
        for rel in ("README.md", "README.zh-CN.md", "docs/en/presentation.md", "docs/zh-CN/presentation.md"):
            with self.subTest(page=rel):
                self.assertIn("os-optics view RUN --no-browser --snapshot DIR --views iso", read(rel))

    def test_contributor_pages_name_the_case_image_command(self):
        for rel in ("CONTRIBUTING.md", "docs/contributing-with-an-agent.md", "docs/workflow-recipes.md"):
            with self.subTest(page=rel):
                text = read(rel)
                self.assertIn("cases images", text)
                self.assertIn("--no-browser --snapshot", text)


class CaseReadmeTemplateTests(unittest.TestCase):
    def test_both_templates_exist_with_the_same_placeholders(self):
        english = read("templates/README.case.md")
        chinese = read("templates/README.case.zh-CN.md")
        expected = {"title", "language_links", "summary", "images", "name", "results_rows", "engine", "catalog_text",
                    "status"}
        self.assertEqual(placeholders(english), expected)
        self.assertEqual(placeholders(chinese), expected)

    def test_chinese_template_fills_completely(self):
        values = {key: f"<{key}>" for key in placeholders(read("templates/README.case.md"))}
        text = C.fill_template(read("templates/README.case.zh-CN.md"), values)
        self.assertNotIn("{{", text)
        self.assertNotIn("<!--", text)
        self.assertNotIn("\n\n\n", text)
        # the language line under the title: the two READMEs link each other
        self.assertTrue(text.startswith("# <title>\n\n<language_links>\n\n<summary>\n\n<images>\n"))

    def test_template_comments_name_the_engine_snapshot(self):
        for rel in ("templates/README.case.md", "templates/README.case.zh-CN.md"):
            with self.subTest(template=rel):
                comment = "".join(re.findall(r"<!--(.*?)-->", read(rel), re.S))
                self.assertIn("--no-browser --snapshot", comment)
                self.assertIn("cases images NAME", comment)


if __name__ == "__main__":
    unittest.main()
