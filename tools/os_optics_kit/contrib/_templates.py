"""Generated texts of ``os-optics-kit contrib`` that are not contributor templates: the test module of a contributed
tool and the pull-request descriptions.

The case and skill pages come from the contributor templates templates/CASE.md, templates/README.case.md and
templates/SKILL.md (``_common.render_template``). Their ``{{key}}`` placeholders:

CASE.md / README.case.md: name, title, summary, what_it_shows, engine, catalog, catalog_text, tools, tags, status,
targets_rows, results_rows, images, extra_files (``case.prose_values``).
SKILL.md: name, description, kind, command, requires, kit_version, when_to_use, when_not_to_use, inputs, outputs,
example, limits (``tool.skill_values``).
"""

TOOL_TEST_PY = '''"""Tests of ``os-optics-kit {command}`` (tools/os_optics_kit/{module}.py): import, --help, doctests."""

import contextlib
import doctest
import io
import subprocess
import sys
import unittest

import os_optics_kit.{module} as helper
from os_optics_kit.cli import COMMANDS


class {class_name}(unittest.TestCase):
    def test_exposes_main(self):
        self.assertTrue(callable(helper.main))

    def test_registered_command(self):
        self.assertEqual(COMMANDS["{command}"][0], "{module}")

    def test_help_exits_zero(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                code = helper.main(["--help"])
            except SystemExit as exc:
                code = exc.code
        self.assertIn(code, (0, None))
        self.assertIn("usage", out.getvalue().lower())

    def test_help_without_engine(self):
        script = ("import sys; sys.modules['os_optics'] = None; "
                  "from os_optics_kit.cli import main; raise SystemExit(main(['{command}', '--help']))")
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_doctests(self):
        failed, _ = doctest.testmod(helper, verbose=False)
        self.assertEqual(failed, 0)


if __name__ == "__main__":
    unittest.main()
'''

PR_BODY_CASE = """## Case `{name}`: {title}

{summary}

### Baseline

| | Verified with | Kit baseline |
| --- | --- | --- |
| os-optics | {engine} | {kit_engine} |
| catalog | {catalog} | {kit_catalog} |

### Verification

| Step | Result |
| --- | --- |
| `os-optics project check project.json` | {check_status} |
| `os-optics project run project.json --out-dir RUN` | {run_status} ({execution}) |
| `os-optics results targets RUN` | {target_counts} |

{targets_table}

### Files

{files_list}

### Checklist

{checklist}

Prepared with `os-optics-kit contrib case` ({kit_version}).
"""

PR_BODY_TOOL = """## Tool `os-optics-kit {command}` (`{module}`)

{description}

### Files

{files_list}

### Verification

| Check | Result |
| --- | --- |
| `os-optics-kit {command} --help` | exit 0 |
| `os-optics-kit {command} --help` without os-optics installed | {help_without_engine} |
| `python -m unittest tests/test_{module}.py` | {test_result} |
| skills.json | {skills_index} |

Requires: {requires}.

### Checklist

{checklist}

Prepared with `os-optics-kit contrib tool` ({kit_version}).
"""
