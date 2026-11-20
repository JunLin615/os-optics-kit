"""The Python example of the studies guide runs as a script, as published (English and Chinese pages).

The example is copied from docs/en/studies.md into a temporary directory next to two catalog-free Projects and run
with ``python search.py``: two worker processes evaluate both samples. The same example without its
``if __name__ == "__main__":`` guard is run too: every sample is then lost, and the record says why.
"""

import json
import os
import re
import subprocess
import sys
import textwrap
import unittest

from tests.workflow_fixtures import ROOT, TempDir, quiet_environment, requires_engine

GUIDES = {"en": ROOT / "docs" / "en" / "studies.md", "zh-CN": ROOT / "docs" / "zh-CN" / "studies.md"}
PYTHON_BLOCK = re.compile(r"^```python\n(.*?)^```", re.M | re.S)
GUARD = 'if __name__ == "__main__":\n'


def python_examples(path):
    return PYTHON_BLOCK.findall(path.read_text(encoding="utf-8"))


def unguarded(script):
    """The example with the body of its main guard moved to the top level."""
    head, _guard, body = script.partition(GUARD)
    return head + textwrap.dedent(body)


def write_projects(folder):
    quiet_environment()
    from os_optics_kit import optical_designs as D

    for name, gap in (("a.json", 0.0), ("b.json", 2.0)):
        (folder / name).write_text(json.dumps(D.expander_focus(gap_error_mm=gap)), encoding="utf-8")


def run_script(folder, script):
    (folder / "search.py").write_text(script, encoding="utf-8")
    quiet_environment()
    return subprocess.run([sys.executable, "search.py"], cwd=folder, capture_output=True, text=True, timeout=600,
                          env=dict(os.environ))


class StudiesGuideExampleTests(unittest.TestCase):
    def test_both_pages_print_the_same_example_with_a_main_guard(self):
        examples = {language: python_examples(path) for language, path in GUIDES.items()}
        self.assertEqual(len(examples["en"]), 1)
        self.assertEqual(examples["en"], examples["zh-CN"])
        self.assertIn(GUARD, examples["en"][0])

    @requires_engine
    def test_the_example_runs_as_a_script_with_two_workers(self):
        script = python_examples(GUIDES["en"])[0]
        with TempDir() as tmp:
            write_projects(tmp)
            result = run_script(tmp, script)
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = result.stdout.splitlines()
            self.assertEqual([line.split()[0] for line in lines], ["s0001", "s0002"])
            for line in lines:
                self.assertIn("'spot_radius':", line)
            study = json.loads((tmp / "batch-01" / "study.json").read_text(encoding="utf-8"))
            self.assertEqual((study["execution"]["state"], study["execution"]["scheduler"],
                              study["execution"]["workers"]), ("COMPLETED", "process", 2))
            states = [json.loads((tmp / "batch-01" / "samples" / sid / "sample.json").read_text(encoding="utf-8"))
                      ["state"] for sid in ("s0001", "s0002")]
            self.assertEqual(states, ["COMPLETED", "COMPLETED"])

    @requires_engine
    def test_without_the_guard_every_lost_sample_names_the_guard(self):
        script = unguarded(python_examples(GUIDES["en"])[0])
        self.assertNotIn("__main__", script)
        with TempDir() as tmp:
            write_projects(tmp)
            result = run_script(tmp, script)
            records = [json.loads(path.read_text(encoding="utf-8"))
                       for path in sorted((tmp / "batch-01" / "samples").glob("*/sample.json"))]
            self.assertEqual(len(records), 2, result.stderr)
            for record in records:
                self.assertEqual((record["state"], record["error"]["code"]), ("FAILED", "WORKER_LOST"))
                self.assertIn('if __name__ == "__main__":', record["error"]["problems"][0]["message"])


if __name__ == "__main__":
    unittest.main()
