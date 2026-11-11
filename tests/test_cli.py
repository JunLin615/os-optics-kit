"""The os-optics-kit dispatcher, the engine requirement and the lazy-import rule (no engine run needed)."""

from contextlib import redirect_stderr, redirect_stdout
import importlib
import io
import json
import re
import subprocess
import sys
import unittest
from unittest.mock import patch

from os_optics_kit import _engine, cli, interop
from tests.workflow_fixtures import KIT, ROOT, TempDir

TOOLS = ("summary", "edit", "sweep", "compare", "plot", "report", "sensitivity", "tolerance", "candidates",
         "measurements", "study")
# Runs a kit command in a child process in which the engine (and optionally matplotlib) cannot be imported, even
# when it is installed: any attempt raises ModuleNotFoundError, like a missing distribution.
BLOCKED_RUN = """import sys
blocked = sys.argv[1].split(",")
class Block:
    def find_spec(self, fullname, *args):
        if any(fullname == name or fullname.startswith(name + ".") for name in blocked):
            raise ModuleNotFoundError(f"No module named {fullname!r}", name=fullname)
sys.meta_path.insert(0, Block())
from os_optics_kit.cli import main
code = main(sys.argv[2:])
leaked = [name for name in sys.modules if name.split(".")[0] in blocked]
if leaked:
    raise SystemExit(f"imported {leaked}")
raise SystemExit(code)
"""


def run_blocked(blocked, *args, cwd=None):
    return subprocess.run([sys.executable, "-c", BLOCKED_RUN, ",".join(blocked), *args], capture_output=True,
                          text=True, timeout=120, cwd=cwd)


def call(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class DispatcherTests(unittest.TestCase):
    def test_commands_table_names_every_tool(self):
        self.assertEqual(list(cli.COMMANDS)[:len(TOOLS)], list(TOOLS))
        for name in ("cases", "skills", "contrib"):
            self.assertIn(name, cli.COMMANDS)

    def test_help_lists_every_command(self):
        for argv in ([], ["--help"], ["-h"], ["help"]):
            code, out, err = call(argv)
            self.assertEqual((code, err), (0, ""))
            for name in cli.COMMANDS:
                self.assertRegex(out, rf"(?m)^  {name} ")

    def test_version(self):
        code, out, _err = call(["--version"])
        self.assertEqual((code, out.strip()), (0, f"os-optics-kit {cli.__version__}"))

    def test_unknown_command_exits_4(self):
        code, out, err = call(["no-such-command"])
        self.assertEqual((code, out), (4, ""))
        self.assertIn("unknown command 'no-such-command'", err)
        self.assertIn("commands:", err)

    def test_every_tool_main_returns_an_int_for_help(self):
        for name in TOOLS:
            module = importlib.import_module(f"os_optics_kit.{cli.COMMANDS[name][0]}")
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = module.main(["--help"])
            self.assertEqual(code, 0, name)
            self.assertIsInstance(code, int, name)
            self.assertTrue(out.getvalue().startswith(f"usage: os-optics-kit {name}"), name)
            self.assertEqual(err.getvalue(), "", name)

    def test_usage_error_is_a_json_error_with_exit_4(self):
        code, out, err = call(["sweep"])
        self.assertEqual((code, out), (4, ""))
        self.assertIn("required", json.loads(err)["error"]["message"])

    def test_python_dash_m_runs_the_dispatcher(self):
        done = subprocess.run([sys.executable, *KIT, "--version"], capture_output=True, text=True, timeout=120)
        self.assertEqual((done.returncode, done.stdout.strip()), (0, f"os-optics-kit {cli.__version__}"))


class LazyImportTests(unittest.TestCase):
    """--help of the dispatcher and of every tool, and the skills command, never import the engine."""

    def test_help_without_the_engine(self):
        done = run_blocked(["os_optics"], "--help")
        self.assertEqual(done.returncode, 0, done.stderr)
        for name in TOOLS:
            with self.subTest(tool=name):
                done = run_blocked(["os_optics"], name, "--help")
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertTrue(done.stdout.startswith(f"usage: os-optics-kit {name}"))

    def test_skills_without_the_engine(self):
        done = run_blocked(["os_optics"], "skills", "list")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)["schema"], "os-optics-kit.skills")
        done = run_blocked(["os_optics"], "skills", "index", "--check")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_engine_command_without_the_engine_is_a_structured_error(self):
        done = run_blocked(["os_optics"], "summary", str(ROOT / "no-such-run"))
        self.assertEqual((done.returncode, done.stdout), (4, ""))
        error = json.loads(done.stderr)["error"]
        self.assertIn(error["code"], ("ENGINE_NOT_INSTALLED", "ENGINE_UNSUPPORTED"))
        self.assertIn(f"os-optics{_engine.ENGINE_REQUIREMENT}", error["message"])

    def test_plot_without_matplotlib_is_a_structured_error(self):
        with TempDir() as tmp:
            data = tmp / "bars.json"
            data.write_text(json.dumps({"labels": ["a"], "values": [1.0], "unit": "mm", "title": "t"}),
                            encoding="utf-8")
            done = run_blocked(["os_optics", "matplotlib"], "plot", "bars", str(data), "--out", str(tmp / "plots"))
            self.assertFalse((tmp / "plots").exists())
        self.assertEqual((done.returncode, done.stdout), (4, ""))
        error = json.loads(done.stderr)["error"]
        self.assertEqual(error["code"], "PLOT_EXTRA_MISSING")
        self.assertIn("os-optics-kit[plot]", error["message"])


class EngineRequirementTests(unittest.TestCase):
    def test_requirement_matches_the_package_dependency(self):
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        dependencies = re.search(r"(?m)^dependencies = \[(.*)\]$", text).group(1)
        self.assertIn(f'"{_engine.ENGINE_DISTRIBUTION}{_engine.ENGINE_REQUIREMENT}"', dependencies)

    def test_satisfies(self):
        for version, expected in (("0.13.0", True), ("0.13.3", True), ("0.13.0rc1", True), ("0.13.0+abc", True),
                                  ("0.12.9", False), ("0.14.0", False), ("1.0", False), (None, False)):
            self.assertEqual(_engine.satisfies(version), expected, version)
        self.assertTrue(_engine.satisfies("2.1", ">=2,!=2.0"))
        self.assertFalse(_engine.satisfies("2.0.0", ">=2,!=2.0"))
        with self.assertRaises(ValueError):
            _engine.satisfies("1.0", "~=1.0")

    def test_interop_minimum_is_derived_from_the_requirement(self):
        self.assertEqual(interop.MINIMUM_ENGINE, _engine.minimum_version())
        self.assertEqual(interop.MINIMUM_ENGINE, (0, 13))
        self.assertEqual(interop.version_tuple("0.10.2+abc"), (0, 10, 2))
        self.assertTrue(interop.engine_supported("0.13.0"))
        self.assertFalse(interop.engine_supported("0.10.0"))

    def test_require_engine_refusals(self):
        with patch.object(_engine, "engine_version", return_value=None):
            with self.assertRaises(_engine.DependencyError) as caught:
                interop.require_engine()
        self.assertEqual(caught.exception.code, "ENGINE_NOT_INSTALLED")
        self.assertIsInstance(caught.exception, RuntimeError)
        with patch.object(_engine, "engine_version", return_value="0.12.0"):
            with self.assertRaises(_engine.DependencyError) as caught:
                interop.require_engine()
        self.assertEqual((caught.exception.code, caught.exception.details["installed"]),
                         ("ENGINE_UNSUPPORTED", "0.12.0"))
        with patch.object(_engine, "engine_version", return_value="0.13.1"):
            self.assertEqual(interop.require_engine(), "0.13.1")

    def test_dependency_errors(self):
        with patch.object(_engine, "engine_version", return_value=None):
            error = _engine.dependency_error(ModuleNotFoundError("x", name="os_optics.local"))
        self.assertEqual(error["code"], "ENGINE_NOT_INSTALLED")
        with patch.object(_engine, "engine_version", return_value="0.13.0"):
            error = _engine.dependency_error(ImportError("cannot import name 'x'", name="os_optics.results"))
        self.assertEqual((error["code"], error["installed"]), ("ENGINE_UNSUPPORTED", "0.13.0"))
        error = _engine.dependency_error(ModuleNotFoundError("x", name="matplotlib.pyplot"))
        self.assertEqual(error["code"], "PLOT_EXTRA_MISSING")
        self.assertIsNone(_engine.dependency_error(ModuleNotFoundError("x", name="scipy")))
        self.assertIsNone(_engine.dependency_error(ValueError("x")))


class NoCasePathsTests(unittest.TestCase):
    """Tools never import from or locate files in the case collection."""

    def test_tool_sources_do_not_reference_cases(self):
        package = ROOT / "tools" / "os_optics_kit"
        for path in sorted(package.glob("*.py")):
            if path.name in ("cases.py", "_resources.py"):
                continue  # the cases command and the data locator serve the case collection itself
            text = path.read_text(encoding="utf-8")
            with self.subTest(module=path.name):
                self.assertNotRegex(text, r"examples[/.]")
                self.assertNotRegex(text, r"cases/|cases_dir|\"cases\"\s*/")


if __name__ == "__main__":
    unittest.main()
