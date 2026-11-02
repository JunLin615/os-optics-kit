"""Built-in templates of ``os-optics-kit contrib``.

A checkout's ``templates/CASE.md``, ``templates/README.case.md`` and ``templates/SKILL.md`` are used instead when they
are ``{{key}}`` templates whose keys are all among the ones listed here.

CASE.md / README.case.md keys: name, title, summary, engine, catalog, tools, tags, status, catalog_line,
targets_table, images_markdown, files_list, run_commands.
SKILL.md keys: name, description, when_to_use, command, command_name, requires, module, help_text, requires_line,
kit_version.
"""

CASE_MD = """---
name: {{name}}
title: {{title}}
summary: {{summary}}
engine: {{engine}}
catalog: {{catalog}}
project: project.json
tools: {{tools}}
tags: {{tags}}
license: MIT
---

# {{title}}

## What it shows

{{summary}}

## Run

```sh
os-optics-kit cases verify {{name}}
```

From this directory, with the engine alone:

```sh
{{run_commands}}
```

## Targets

{{targets_table}}

## Baseline

- os-optics {{engine}}
- {{catalog_line}}
- verified run status: {{status}}

`expected.json` holds the status and every target assessment that `os-optics-kit cases verify` compares.

## Files

{{files_list}}
"""

README_CASE_MD = """# {{title}}

{{summary}}

{{images_markdown}}

## Run it

```sh
os-optics-kit cases verify {{name}}
```

or, from this directory:

```sh
{{run_commands}}
```

## Result

{{targets_table}}

Verified with os-optics {{engine}} and {{catalog_line}}; run status {{status}}.
Agent-readable summary: [CASE.md](CASE.md).
"""

SKILL_MD = """---
name: {{name}}
description: {{description}}
version: 0.1.0
command: {{command}}
requires: {{requires}}
kind: tool
---

# {{name}}

{{description}}

## When to use

{{when_to_use}}

## When not to use

When an `os-optics` command answers the question directly (`os-optics --help`).

## Inputs

```text
{{help_text}}
```

## Outputs

What the options above state; exit code 0 on success.

## Minimal example

```sh
{{command}} --help
python -m os_optics_kit {{command_name}} --help
```

## Limits

{{requires_line}}

## Version

0.1.0: first version, contributed against os-optics-kit {{kit_version}}.
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
