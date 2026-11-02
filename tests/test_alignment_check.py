"""Offline checks of the release runner; no package installation or network use."""

import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import check_alignment as alignment


ROOT = Path(__file__).resolve().parents[1]
RETIRED = "opensci-" + "optic.com"  # assembled so this file never matches the repository scan
CATALOG_URL = "https://opensci-optics.com/api/v1/catalog"


def default_checks():
    """The repository's default (non-optional) checks, as other repositories would declare them."""
    document = json.loads((ROOT / "alignment-checks.json").read_text(encoding="utf-8"))
    return [check for check in document["checks"] if not check.get("optional")]


def declared_result(command):
    """Exit code and output a default check declares, for a command built from its argv (fake runs)."""
    for check in default_checks():
        template = check["argv"][1:]
        if len(template) != len(command) - 1:
            continue
        if all(item == arg or ("}" in item and arg.replace("\\", "/").endswith(item.split("}", 1)[1]))
               for item, arg in zip(template, command[1:])):
            code = check.get("expected_exit", 0)
            if check.get("json_error"):
                return code, "", json.dumps({"error": {"code": "DECLARED", "message": "declared refusal"}})
            status = check.get("expected_status", "COMPLETED")
            status = status[0] if isinstance(status, list) else status
            return code, json.dumps({check.get("status_field", "status"): status}), ""
    return None


class ReleaseSelectionTests(unittest.TestCase):
    def test_only_exact_public_versions_are_accepted(self):
        for version in ("0.6.1", "1.0", "1.2.3rc1", "1.2.3.post2", "1.2.3.dev1"):
            with self.subTest(version=version):
                self.assertEqual(alignment.release_version(version), version)
        for value in (None, "", "latest", "0.6.*", ">=0.6.1", "0.6.1 --extra-index-url x",
                      "https://example.com/engine.whl", "../local", "0.6.1+private", "0.6.1\n"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                alignment.release_version(value)

    def test_latest_resolves_from_pypi_and_revalidates_the_response(self):
        with patch.object(alignment.urllib.request, "urlopen", return_value=io.StringIO(
            json.dumps({"info": {"version": "0.7.0"}})
        )) as request:
            self.assertEqual(alignment.latest_version(), "0.7.0")
        self.assertEqual(request.call_args.args[0].full_url, "https://pypi.org/pypi/os-optics/json")
        self.assertEqual(request.call_args.kwargs["timeout"], 30)
        with patch.object(alignment.urllib.request, "urlopen", return_value=io.StringIO(
            json.dumps({"info": {"version": "https://example.com/engine.whl"}})
        )), self.assertRaises(ValueError):
            alignment.latest_version()


class ClassificationTests(unittest.TestCase):
    def test_catalog_classification_distinguishes_service_data_and_api_failures(self):
        for code, expected in (
            ("CATALOG_SERVICE_UNREACHABLE", "catalog_network"),
            ("CATALOG_NOT_AVAILABLE", "catalog_network"),
            ("CATALOG_HASH_MISMATCH", "catalog_data"),
            ("ENGINE_UPGRADE_REQUIRED", "catalog_data"),
            ("INVALID_INPUT", "package_api"), (None, "package_api"),
        ):
            with self.subTest(code=code):
                self.assertEqual(alignment.catalog_failure_category(json.dumps({"error": {"code": code}})), expected)
        for output in ("Traceback: incompatible API", "[]", "null", '{"error":null}', "", None):
            with self.subTest(output=output):
                self.assertEqual(alignment.catalog_failure_category(output), "package_api")

    def test_every_engine_catalog_code_has_a_category(self):
        network = ("CATALOG_SERVICE_UNREACHABLE", "CATALOG_SERVICE_ERROR", "CATALOG_NOT_AVAILABLE",
                   "CATALOG_RELEASE_NOT_FOUND", "CATALOG_SYNC_UNAVAILABLE", "CATALOG_ORIGIN_INVALID",
                   "CATALOG_STORE_BUSY")
        data = ("CATALOG_INVALID", "CATALOG_INTEGRITY_FAILED", "CATALOG_RELEASE_CONFLICT", "CATALOG_STORE_ERROR",
                "CATALOG_ACTIVE_UNKNOWN", "ENGINE_UPGRADE_REQUIRED", "OPTICAL_SKU_NOT_PUBLISHED",
                "CATALOG_CODE_ADDED_LATER")
        for codes, expected in ((network, "catalog_network"), (data, "catalog_data")):
            for code in codes:
                with self.subTest(code=code):
                    # The engine CLI prints {"status": "ERROR", "error": {...}} with indentation.
                    output = json.dumps({"status": "ERROR", "error": {"code": code, "message": "x"}}, indent=2)
                    self.assertEqual(alignment.catalog_failure_category(output), expected)

    def test_refused_queries_and_unsupported_models_are_package_failures(self):
        # A changed query vocabulary or a model kind the engine no longer supports is an engine change.
        for code in ("CATALOG_QUERY_INVALID", "SIMULATION_MODEL_UNSUPPORTED"):
            with self.subTest(code=code):
                output = json.dumps({"error": {"code": code, "message": "x"}})
                self.assertEqual(alignment.catalog_failure_category(output), "package_api")
                self.assertEqual(alignment.catalog_failure_category(
                    "CatalogError: " + code + ": refused", transport=True), "package_api")
        both = json.dumps({"error": {"code": "CATALOG_NOT_AVAILABLE", "problems": [
            {"code": "CATALOG_QUERY_INVALID"}]}})
        self.assertEqual(alignment.catalog_failure_category(both), "catalog_network")

    def test_non_json_failures_use_only_explicit_signals(self):
        cases = (
            ("Traceback (most recent call last):\nos_optics.catalog.errors.CatalogError: "
             "CATALOG_SERVICE_ERROR: the service answered with HTML", True, "catalog_network"),
            ("CatalogError: CATALOG_INTEGRITY_FAILED: size differs", True, "catalog_data"),
            ("Downloading catalog\nwarning: slow\n" + json.dumps({"code": "CATALOG_STORE_BUSY"}), True,
             "catalog_network"),
            ("Traceback (most recent call last):\nurllib.error.URLError: <urlopen error [Errno 104] reset>",
             True, "catalog_network"),
            ("Traceback (most recent call last):\nConnectionResetError: [WinError 10054]", True, "catalog_network"),
            ("Traceback (most recent call last):\nurllib.error.URLError: <urlopen error>", False, "package_api"),
            ("Traceback (most recent call last):\nAttributeError: no attribute 'sync'", True, "package_api"),
            ("the catalog service was unreachable", True, "package_api"),
        )
        for text, transport, expected in cases:
            with self.subTest(text=text[-40:], transport=transport):
                self.assertEqual(alignment.catalog_failure_category(text, transport=transport), expected)


class DocumentationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.baseline = {
            "version": "0.8.0", "website": "https://opensci-optics.com", "retired_hosts": [RETIRED],
            "verified_environment": {"catalog_version": "2026-10-15.1"},
            "documentation": ["README.md", {"path": "docs/en/README.md", "baseline_reference": False},
                              {"path": ".github/ISSUE_TEMPLATE/bug_report.yml", "baseline_reference": False}],
            "documentation_scan": {"include": ["*.md", "docs/**/*.md", ".github/**/*.yml", "examples/**/*.py"],
                                   "historical": ["docs/old-validation.md"]},
        }
        self.write("README.md", 'Install "os-optics[protected]==0.8.0"; the **0.8.0** baseline is in '
                                "engine-baseline.json. Website https://opensci-optics.com/.\n")
        self.write("docs/en/README.md", "Guides target **os-optics 0.8.0**.\n")
        self.write(".github/ISSUE_TEMPLATE/bug_report.yml", 'placeholder: "0.8.0"\n')
        self.write("docs/en/interop.md", "Requires **os-optics 0.8.0** or newer; `os-optics>=0.8.0`.\n")
        self.write("docs/zh-CN/interop.md", "需要 **os-optics 0.8.0** 或更高版本。\n")
        self.write("docs/old-validation.md", f"Recorded with os-optics==0.6.1 at https://{RETIRED}/.\n")
        self.write("examples/tool.py", '"""Requires os-optics 0.8.0."""\n')

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def problems(self, **changes):
        return alignment.documentation_problems(self.root, dict(self.baseline, **changes))

    def test_consistent_tree_has_no_problems_and_minimums_are_not_pins(self):
        self.assertEqual(self.problems(), [])
        # A newer baseline flags every current statement but no minimum requirement or history.
        flagged = {(problem["path"], problem["kind"]) for problem in self.problems(version="0.9.0")}
        self.assertEqual(flagged, {("README.md", "version_pin"), ("docs/en/README.md", "version_pin"),
                                   (".github/ISSUE_TEMPLATE/bug_report.yml", "version_pin")})

    def test_prose_pins_exclude_minimum_versions_and_build_ids(self):
        def pins(text):
            return [pin for pattern in alignment.PROSE_PINS for _, pin in alignment._lines(text, pattern)]
        for text in ("requires **os-optics 0.8.0** or newer", "(os-optics 0.8.0 or later)",
                     "（os-optics 0.8.0 或更高版本）", "**0.8.0** or newer", "build `os-optics 0.8.0+94f6dc70`",
                     "`os-optics>=0.8.0`", "older than 0.8.0"):
            with self.subTest(text=text):
                self.assertEqual(pins(text), [])
        for text, expected in (("Guides target **os-optics 0.8.0**.", ["0.8.0"]),
                               ("The verified engine baseline is **0.8.0**, recorded", ["0.8.0"]),
                               ("对应已发布的 **0.8.0** 版本", ["0.8.0"]), ("os-optics 0.10.2rc1 is", ["0.10.2rc1"]),
                               ("OS Optics 0.10.2 adds", ["0.10.2"]), ("opensci-engine 0.9.0 was", ["0.9.0"])):
            with self.subTest(text=text):
                self.assertEqual(pins(text), expected)

    def test_retired_host_is_flagged_in_any_scanned_file_including_badges(self):
        self.write("docs/en/hardware.md", f"See https://www.{RETIRED}/hardware.\n")
        self.write(".github/ISSUE_TEMPLATE/config.yml", f"url: https://{RETIRED}/\n")
        self.write("examples/tool.py", f'URL = "https://{RETIRED}"\n')
        self.write("README.md", self.root.joinpath("README.md").read_text(encoding="utf-8")
                   + "[![Website](https://img.shields.io/badge/website-opensci--optic.com-167D8D)](x)\n")
        problems = [problem for problem in self.problems() if problem["kind"] == "retired_host"]
        self.assertEqual({(problem["path"], problem["line"]) for problem in problems}, {
            ("docs/en/hardware.md", 1), (".github/ISSUE_TEMPLATE/config.yml", 1), ("examples/tool.py", 1),
            ("README.md", 2)})
        self.assertTrue(all("https://opensci-optics.com" in problem["message"] for problem in problems))

    def test_unlisted_files_are_checked_for_exact_pins_and_history_is_exempt(self):
        self.write("docs/en/catalog.md", 'pip install "os-optics==0.6.1"\n')
        self.write("run-alignment/.venv/README.md", 'pip install "os-optics==0.1.0"\n')
        problems = self.problems()
        self.assertEqual([(problem["path"], problem["found"]) for problem in problems],
                         [("docs/en/catalog.md", "0.6.1")])
        self.assertIn("docs/en/catalog.md:1: version pin 0.6.1 differs from baseline 0.8.0", problems[0]["message"])

    def test_pins_of_the_earlier_distribution_name_are_reported(self):
        self.write("docs/en/catalog.md", 'pip install "opensci-engine[protected]==0.8.0"\n')
        self.write("examples/tool.py", 'pip install "opensci-engine==0.6.1"\n')
        self.assertEqual([(problem["path"], problem["found"]) for problem in self.problems()],
                         [("examples/tool.py", "0.6.1")])

    def test_listed_documents_need_pin_reference_current_catalog_and_existence(self):
        self.write("README.md", "Results on catalog `2026-10-06.3`.\n")
        self.write("docs/en/README.md", "No version here.\n")
        self.baseline["documentation"].append("docs/en/missing.md")
        kinds = {(problem["path"], problem["kind"]) for problem in self.problems()}
        self.assertEqual(kinds, {("README.md", "missing_pin"), ("README.md", "missing_reference"),
                                 ("README.md", "catalog_version"), ("docs/en/README.md", "missing_pin"),
                                 ("docs/en/missing.md", "missing_file")})

    def test_repository_documentation_matches_its_baseline(self):
        baseline = json.loads((ROOT / "engine-baseline.json").read_text(encoding="utf-8"))
        self.assertEqual([problem["message"] for problem in alignment.documentation_problems(ROOT, baseline)], [])


class CheckDefinitionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "requirements-topic.txt").write_text("numpy\n", encoding="utf-8")

    def load(self, checks, **document):
        (self.root / "alignment-checks.json").write_text(json.dumps(
            {"schema_version": 1, "checks": checks, **document}), encoding="utf-8")
        return alignment.load_checks(self.root)

    def test_repository_check_list_is_valid_and_optional_checks_are_opt_in(self):
        definitions = alignment.load_checks(ROOT)
        names = [check["name"] for check in definitions["checks"]]
        default = alignment.select_checks(definitions["checks"], [])
        self.assertEqual(sorted(default), sorted(check["name"] for check in default_checks()))
        self.assertTrue(all(check["optional"] for check in definitions["checks"] if check["requirements"]))
        self.assertEqual(alignment.select_checks(definitions["checks"], ["all"]), set(names))

    def test_catalog_helper_flow_and_catalog_tests_run_by_default(self):
        definitions = alignment.load_checks(ROOT)
        by_name = {check["name"]: check for check in definitions["checks"]}
        default = alignment.select_checks(definitions["checks"], [])
        flow = ("catalog-candidates", "spec-be-base", "spec-be-swap", "spec-be-sweep", "spec-be-move",
                "spec-be-compare", "spec-be-plot", "spec-be-report", "catalog-tests")
        for name in flow:
            with self.subTest(name=name):
                self.assertIn(name, default)
                self.assertTrue(by_name[name]["needs_catalog"])
        tests = by_name["catalog-tests"]
        self.assertEqual(tests["env"], {"OPENSCI_EXAMPLES_REQUIRE_CATALOG": "1"})
        for module in tests["argv"][4:]:  # each named test class exists
            path, cls = module.rsplit(".", 1)
            self.assertIn(f"class {cls}(", (ROOT / (path.replace(".", "/") + ".py")).read_text(encoding="utf-8"))

    def test_required_catalog_fails_instead_of_skipping(self):
        from tests import workflow_fixtures

        with patch.dict(alignment.os.environ, {"OPENSCI_EXAMPLES_REQUIRE_CATALOG": "1"}), \
                patch("os_optics.local.json_api.catalog_status", return_value={"available": False}):
            with self.assertRaises(RuntimeError):
                workflow_fixtures.catalog_available()
        with patch.dict(alignment.os.environ, {"OPENSCI_EXAMPLES_REQUIRE_CATALOG": ""}), \
                patch("os_optics.local.json_api.catalog_status", return_value={"available": False}):
            self.assertFalse(workflow_fixtures.catalog_available())

    def test_defaults_and_normalization(self):
        definitions = self.load([{"name": "workflow", "argv": ["{python}", "examples/w.py", "--out", "{runs}/w"],
                                  "expected_status": "PASS"}], default_requirements=["requirements-topic.txt"])
        self.assertEqual(definitions["default_requirements"], ["requirements-topic.txt"])
        self.assertEqual(definitions["checks"][0], {
            "name": "workflow", "description": None, "argv": ["{python}", "examples/w.py", "--out", "{runs}/w"],
            "expected_exit": 0, "expected_status": ["PASS"], "status_field": "status", "needs": [],
            "requirements": None, "requires_python": None, "env": {}, "timeout": 180, "json_error": False,
            "needs_catalog": False, "optional": False})

    def test_invalid_definitions_are_rejected(self):
        valid = {"name": "workflow", "argv": ["{python}", "w.py"]}
        for change in (
            {"argv": ["python", "w.py"]}, {"argv": "{python} w.py"}, {"argv": ["{python}", "{home}/w.py"]},
            {"name": "catalog-sync"}, {"name": "install-env-topic"}, {"name": "Upper"}, {"unknown": 1},
            {"needs": ["later"]}, {"requirements": "../outside.txt"}, {"requirements": "missing.txt"},
            {"timeout": "60"}, {"expected_exit": True}, {"optional": "yes"}, {"expected_status": []},
            {"env": {"lower": "x"}}, {"requires_python": "3"}, {"requires_python": 3.12},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.load([dict(valid, **change)])
        with self.assertRaises(ValueError):
            self.load([valid, valid])
        with self.assertRaises(ValueError):
            self.load([valid], schema_version=2)

    def test_selection_adds_needed_checks_and_rejects_unknown_names(self):
        checks = self.load([
            {"name": "base", "argv": ["{python}", "a.py"]},
            {"name": "topic-a", "argv": ["{python}", "b.py"], "optional": True},
            {"name": "topic-b", "argv": ["{python}", "c.py"], "optional": True, "needs": ["topic-a"]},
        ])["checks"]
        self.assertEqual(alignment.select_checks(checks, []), {"base"})
        self.assertEqual(alignment.select_checks(checks, ["topic-b"]), {"base", "topic-a", "topic-b"})
        self.assertEqual(alignment.select_checks(checks, ["topic-a,topic-b"]), {"base", "topic-a", "topic-b"})
        with self.assertRaisesRegex(ValueError, "Unknown optional checks"):
            alignment.select_checks(checks, ["topic-c"])


class AlignmentRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "repository"
        self.root.mkdir()
        self.out = Path(self.temporary.name) / "evidence"
        self.baseline = {"version": "0.6.1", "documentation": ["README.md", "installation.md"],
                         "retired_hosts": [RETIRED],
                         "verified_environment": {"catalog_version": "2026-10-15.1"}}
        (self.root / "engine-baseline.json").write_text(json.dumps(self.baseline), encoding="utf-8")
        (self.root / "README.md").write_text(
            'Baseline **0.6.1**; see engine-baseline.json.\n', encoding="utf-8")
        (self.root / "installation.md").write_text(
            'pip install "os-optics[protected]==0.6.1"\nSee engine-baseline.json.\n', encoding="utf-8")
        (self.root / "requirements-examples.txt").write_text("matplotlib\n", encoding="utf-8")
        (self.root / "requirements-topic.txt").write_text("scipy\n", encoding="utf-8")
        self.catalog = Path(self.temporary.name) / "release.json"
        self.catalog.write_text(json.dumps({"catalog_version": "2026-10-15.1", "products": []}), encoding="utf-8")
        self.catalog_sha256 = hashlib.sha256(self.catalog.read_bytes()).hexdigest()
        self.baseline["verified_environment"]["catalog_sha256"] = self.catalog_sha256
        (self.root / "engine-baseline.json").write_text(json.dumps(self.baseline), encoding="utf-8")
        self.write_checks(default_checks())

    def write_checks(self, checks):
        (self.root / "alignment-checks.json").write_text(json.dumps({
            "schema_version": 1, "default_requirements": ["requirements-examples.txt"], "checks": checks,
        }), encoding="utf-8")

    def fake_processes(self, *, version="0.6.1", sync_error=None, beam_error=None, status=None,
                       topic_install_error=None):
        def run(command, **kwargs):
            code, stdout, stderr = 0, "", ""
            if "--report" in command:
                install_report = Path(command[command.index("--report") + 1])
                wheel = next((arg.split(" @ ", 1)[1] for arg in command
                              if arg.startswith("os-optics[protected] @ file:")), None)
                digest = hashlib.sha256(b"candidate wheel").hexdigest() if wheel else "a" * 64
                install_report.write_text(json.dumps({"version": "1", "install": [{"metadata": {"name": "os-optics"},
                    "download_info": {"url": wheel if wheel else
                    "https://files.pythonhosted.org/packages/os_optics.whl",
                    "archive_info": {"hashes": {"sha256": digest}}}}]}), encoding="utf-8")
            if "-c" in command and "engine_version" in command[-1]:
                stdout = json.dumps({"engine_version": version, "python": "test", "python_version": "3.11.9",
                                     "platform": "test", "executable": command[0], "module_path": str(
                                         Path(command[0]).parent.parent / "Lib" / "site-packages" /
                                         "os_optics" / "__init__.py")})
            elif command[-2:] == ["catalog", "sync"] and sync_error:
                if sync_error.startswith("{") or sync_error.startswith("Traceback"):
                    code, stderr = 1, sync_error
                else:
                    code, stdout = 1, json.dumps({"status": "ERROR", "error": {
                        "code": sync_error, "message": "Catalog unavailable", "details": {"url": CATALOG_URL}}},
                        indent=2)
            elif command[-2:] == ["catalog", "status"]:
                stdout = json.dumps(status or {"active": {"catalog_version": "2026-10-15.1",
                                                          "sha256": self.catalog_sha256},
                                               "service": {"manifest_url": CATALOG_URL}})
            elif "install" in command and "requirements-topic.txt" in " ".join(command) and topic_install_error:
                code, stderr = 1, topic_install_error
            elif "examples/ideal_lens_focus.py" in command:
                if "nan" in command:
                    code, stderr = 4, json.dumps({"error": {"code": "ValueError", "message": "Invalid bound"}})
                elif "0.001" in command:
                    code, stdout = 1, json.dumps({"status": "FAIL"})
                else:
                    stdout = json.dumps({"status": "PASS"})
            elif "tests.check_beam_summary" in command and beam_error:
                code, stderr = 1, beam_error
            elif "tests.check_beam_summary" in command or "examples/result_summary.py" in command:
                stdout = json.dumps({"status": "PASS"})
            elif "examples/topic.py" in command:
                stdout = json.dumps({"outcome": "WITHIN_TOLERANCE"})
            elif declared_result(command):
                code, stdout, stderr = declared_result(command)
            return subprocess.CompletedProcess(command, code, stdout, stderr)
        return run

    def rows(self, report):
        return {row["name"]: row for row in report["checks"]}

    def calls(self, commands, text):
        return [call for call in commands.call_args_list if text in " ".join(call.args[0])]

    def test_documentation_drift_is_a_failure_even_if_commands_succeed(self):
        (self.root / "README.md").write_text("Baseline **0.5.0**\n", encoding="utf-8")
        runner = alignment.AlignmentCheck(self.root, self.out)
        with patch.object(alignment.subprocess, "run", side_effect=self.fake_processes()):
            report = runner.run()
        check = next(row for row in report["checks"] if row["name"] == "baseline-documentation")
        self.assertEqual((report["status"], check["status"]), ("FAIL", "FAIL"))
        self.assertIn("0.5.0", check["error"])
        self.assertIn("missing reference", check["error"])
        self.assertEqual({problem["kind"] for problem in check["problems"]}, {"version_pin", "missing_reference"})
        self.assertTrue((self.out / "report.json").is_file())

    def test_candidate_uses_isolated_public_install_without_mutating_baseline(self):
        originals = {path.name: path.read_bytes() for path in self.root.iterdir()}
        with patch.dict(alignment.os.environ, {"PYTHONPATH": "private-engine", "PYTHONHOME": "private-runtime",
                                               "OPENSCI_CATALOG_CHECK_INTERVAL": "0"}):
            runner = alignment.AlignmentCheck(self.root, self.out, "latest")
        with patch.object(alignment, "latest_version", return_value="0.7.0") as latest, \
             patch.object(alignment.subprocess, "run", side_effect=self.fake_processes(version="0.7.0")) as commands:
            report = runner.run()
        latest.assert_called_once_with()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual((report["baseline_version"], report["candidate_version"], report["installed_version"]),
                         ("0.6.1", "0.7.0", "0.7.0"))
        self.assertEqual(report["python_version"], "3.11.9")
        self.assertEqual(originals, {path.name: path.read_bytes() for path in self.root.iterdir()})
        install = next(call for call in commands.call_args_list if "install" in call.args[0])
        self.assertEqual(install.args[0][1:], ["-m", "pip", "--isolated", "install", "--no-compile",
                                             "--index-url", "https://pypi.org/simple", "os-optics[protected]==0.7.0",
                                             "--report", str(self.out / "logs" / "install-engine.json")])
        self.assertTrue(report["engine_source"]["publication_verified"])
        self.assertEqual(report["engine_source"]["sha256"], "a" * 64)
        self.assertTrue(Path(install.args[0][0]).is_relative_to(self.out / ".venv"))
        for call in commands.call_args_list:
            # Compare key sets only, so a failure never writes the environment into logs.
            keys = set(call.kwargs["env"])
            self.assertEqual(keys & {"PYTHONPATH", "PYTHONHOME", "OPENSCI_CATALOG_CHECK_INTERVAL"}, set())
            self.assertEqual(call.kwargs["env"]["PYTHONNOUSERSITE"], "1")
            self.assertNotIn("import os_optics._", " ".join(call.args[0]))
            self.assertNotIn("from os_optics._", " ".join(call.args[0]))
        scientific_failure = next(row for row in report["checks"] if row["name"] == "ideal-unmet-target")
        self.assertEqual((scientific_failure["status"], scientific_failure["exit_code"]), ("PASS", 1))
        self.assertEqual(json.loads(Path(scientific_failure["stdout"]).read_text())["status"], "FAIL")
        self.assertEqual(report["catalog_source"]["mode"], "sync")
        self.assertEqual(report["catalog_source"]["catalog_version"], "2026-10-15.1")
        self.assertEqual(report["catalog_source"]["service_url"], CATALOG_URL)

    def test_catalog_failures_retain_evidence_and_skip_dependent_checks(self):
        for code, category in (("CATALOG_SERVICE_UNREACHABLE", "catalog_network"),
                               ("CATALOG_SERVICE_ERROR", "catalog_network"),
                               ("CATALOG_STORE_BUSY", "catalog_network"),
                               ("CATALOG_INTEGRITY_FAILED", "catalog_data"),
                               ("CATALOG_HASH_MISMATCH", "catalog_data"),
                               ("INVALID_INPUT", "package_api")):
            with self.subTest(code=code):
                runner = alignment.AlignmentCheck(self.root, self.out / code)
                with patch.object(alignment.subprocess, "run", side_effect=self.fake_processes(sync_error=code)) as commands:
                    report = runner.run()
                self.assertEqual(report["status"], "FAIL")
                checks = self.rows(report)
                self.assertEqual(checks["catalog-sync"]["category"], category)
                self.assertEqual(checks["catalog-sync"]["exit_code"], 1)
                self.assertIn(code, Path(checks["catalog-sync"]["stdout"]).read_text())
                # Downstream rows carry the blocking category and name what blocked them.
                self.assertEqual((checks["beam-summary"]["status"], checks["beam-summary"]["category"],
                                  checks["beam-summary"]["blocked_by"]), ("SKIPPED", category, "catalog-sync"))
                self.assertEqual((checks["saved-run-summary"]["status"], checks["saved-run-summary"]["category"],
                                  checks["saved-run-summary"]["blocked_by"]), ("SKIPPED", category, "beam-summary"))
                self.assertFalse(self.calls(commands, "tests.check_beam_summary"))
                self.assertFalse(self.calls(commands, "examples/result_summary.py"))
                self.assertEqual(checks["offline-tests"]["status"], "PASS")
                self.assertEqual(report["catalog_source"], {"mode": "sync", "file": None, "service_url": CATALOG_URL})
                self.assertEqual(json.loads((runner.out / "report.json").read_text())["status"], "FAIL")

    def test_non_json_sync_failures_are_classified_by_explicit_signals(self):
        for index, (stderr, category) in enumerate((
            ("Traceback (most recent call last):\nurllib.error.URLError: <urlopen error [Errno 104]>", "catalog_network"),
            ("Traceback (most recent call last):\nos_optics.catalog.errors.CatalogError: "
             "CATALOG_SERVICE_ERROR: not a manifest", "catalog_network"),
            ("Traceback (most recent call last):\nImportError: cannot import name 'sync'", "package_api"),
        )):
            with self.subTest(category=category, stderr=stderr[-30:]):
                runner = alignment.AlignmentCheck(self.root, self.out / f"sync-{index}")
                with patch.object(alignment.subprocess, "run", side_effect=self.fake_processes(sync_error=stderr)):
                    checks = self.rows(runner.run())
                self.assertEqual(checks["catalog-sync"]["category"], category)
                self.assertEqual(checks["beam-summary"]["category"], category)

    def test_beam_failure_after_successful_sync_preserves_catalog_or_api_category(self):
        for index, (diagnostic, category) in enumerate((
            (json.dumps({"code": "EXAMPLE_UNAVAILABLE", "message": "No compatible lens"}), "catalog_data"),
            (json.dumps({"problems": [{"code": "EXAMPLE_UNAVAILABLE", "message": "No compatible lens"}]}), "catalog_data"),
            (json.dumps({"code": "CATALOG_SERVICE_ERROR", "message": "Release download failed"}), "catalog_network"),
            ("Traceback (most recent call last):\nAssertionError: result fields changed", "package_api"),
        )):
            with self.subTest(category=category, diagnostic=diagnostic):
                runner = alignment.AlignmentCheck(self.root, self.out / f"beam-{index}")
                with patch.object(alignment.subprocess, "run", side_effect=self.fake_processes(beam_error=diagnostic)) as commands:
                    report = runner.run()
                checks = self.rows(report)
                self.assertEqual(report["status"], "FAIL")
                self.assertEqual(checks["catalog-sync"]["status"], "PASS")
                self.assertEqual(checks["beam-summary"]["status"], "FAIL")
                self.assertEqual(checks["beam-summary"]["category"], category)
                self.assertEqual(Path(checks["beam-summary"]["stderr"]).read_text(), diagnostic)
                self.assertEqual((checks["saved-run-summary"]["status"], checks["saved-run-summary"]["category"]),
                                 ("SKIPPED", category))
                self.assertFalse(self.calls(commands, "examples/result_summary.py"))

    def test_timeout_retains_partial_output_and_failure_report(self):
        runner = alignment.AlignmentCheck(self.root, self.out)
        timeout = subprocess.TimeoutExpired(["fake", "command"], 7, output=b"partial stdout", stderr=b"partial error")
        for name, catalog, category in (("slow-command", False, "package_api"),
                                        ("catalog-sync", True, "catalog_network"),
                                        ("beam-summary", True, "package_api")):
            with self.subTest(name=name), patch.object(alignment.subprocess, "run", side_effect=timeout):
                check = runner.command(name, category, ["fake", "command"], timeout=7, catalog=catalog)
                self.assertEqual(check["status"], "FAIL")
                self.assertEqual(check["category"], category)
                self.assertIsNone(check["exit_code"])
                self.assertIn("7s timeout", check["error"])
                self.assertEqual(Path(check["stdout"]).read_text(), "partial stdout")
                self.assertEqual(Path(check["stderr"]).read_text(), "partial error")
        runner.finish()
        self.assertEqual(json.loads((self.out / "report.json").read_text())["status"], "FAIL")

    def test_invalid_release_still_writes_report_without_running_commands(self):
        runner = alignment.AlignmentCheck(self.root, self.out, "../local-package")
        with patch.object(alignment.subprocess, "run") as command:
            report = runner.run()
        command.assert_not_called()
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["checks"][-1]["name"], "release-selection")
        self.assertEqual(json.loads((self.out / "report.json").read_text()), report)

    def test_wrong_installed_version_stops_before_running_examples(self):
        runner = alignment.AlignmentCheck(self.root, self.out, "0.7.0")
        with patch.object(alignment.subprocess, "run", side_effect=self.fake_processes(version="0.6.1")) as commands:
            report = runner.run()
        self.assertEqual(report["status"], "FAIL")
        names = [row["name"] for row in report["checks"]]
        blocked = report["checks"][names.index("installed-version-match") + 1:]
        self.assertEqual([row["name"] for row in blocked], [check["name"] for check in default_checks()])
        self.assertEqual({(row["status"], row["category"]) for row in blocked}, {("SKIPPED", "package_api")})
        self.assertEqual({row["blocked_by"] for row in blocked if not dict(
            (check["name"], check.get("needs")) for check in default_checks())[row["name"]]},
            {"installed-version-match"})
        self.assertFalse(self.calls(commands, "unittest"))

    def test_existing_output_is_rejected_without_touching_prior_evidence(self):
        self.out.mkdir()
        previous = self.out / "report.json"
        previous.write_text('{"status":"PASS","candidate":"older"}', encoding="utf-8")
        original = previous.read_bytes()
        with self.assertRaises(FileExistsError):
            alignment.AlignmentCheck(self.root, self.out)
        self.assertEqual(previous.read_bytes(), original)
        self.assertEqual(list(self.out.iterdir()), [previous])

    def test_invalid_check_list_is_a_setup_failure_without_commands(self):
        (self.root / "alignment-checks.json").write_text('{"schema_version": 1, "checks": [{"name": "x"}]}',
                                                         encoding="utf-8")
        with patch.object(alignment.subprocess, "run") as command:
            report = alignment.AlignmentCheck(self.root, self.out).run()
        command.assert_not_called()
        self.assertEqual((report["checks"][-1]["name"], report["checks"][-1]["category"]),
                         ("check-definitions", "setup"))

    def test_added_workflow_check_runs_without_code_changes(self):
        checks = default_checks() + [{
            "name": "workflow-sweep", "argv": ["{python}", "examples/result_summary.py", "{runs}/beam", "--details"],
            "needs": ["beam-summary"], "expected_status": ["PASS", "WARNING"], "env": {"STUDY_DIR": "{out}/study"},
        }]
        self.write_checks(checks)
        with patch.object(alignment.subprocess, "run", side_effect=self.fake_processes()) as commands:
            report = alignment.AlignmentCheck(self.root, self.out).run()
        self.assertEqual(report["status"], "PASS")
        call = self.calls(commands, "--details")[0]
        self.assertEqual(call.args[0][1:], ["examples/result_summary.py", str(self.out / "runs" / "beam"), "--details"])
        self.assertTrue(Path(call.args[0][0]).is_relative_to(self.out / ".venv"))
        self.assertEqual(call.kwargs["env"]["STUDY_DIR"], str(self.out / "study"))
        self.assertEqual(self.rows(report)["workflow-sweep"]["result_status"], ["PASS", "WARNING"])

    def test_optional_check_runs_only_when_selected_in_its_own_environment(self):
        topic = {"name": "topic", "argv": ["{python}", "examples/topic.py", "--out", "{runs}/topic"],
                 "requirements": "requirements-topic.txt", "optional": True, "status_field": "outcome",
                 "expected_status": "WITHIN_TOLERANCE"}
        self.write_checks(default_checks() + [topic])
        with patch.object(alignment.subprocess, "run", side_effect=self.fake_processes()) as commands:
            report = alignment.AlignmentCheck(self.root, self.out / "default").run()
        self.assertNotIn("topic", self.rows(report))
        self.assertFalse(self.calls(commands, "requirements-topic.txt"))
        self.assertEqual(report["optional_checks"]["not_selected"], ["topic"])
        with patch.object(alignment.subprocess, "run", side_effect=self.fake_processes()) as commands:
            report = alignment.AlignmentCheck(self.root, self.out / "topic", optional=["topic"]).run()
        self.assertEqual(report["status"], "PASS")
        rows = self.rows(report)
        self.assertEqual(rows["topic"]["status"], "PASS")
        install = self.calls(commands, "requirements-topic.txt")[0].args[0]
        self.assertTrue(Path(install[0]).is_relative_to(self.out / "topic" / ".venv-topic"))
        self.assertIn("os-optics[protected]==0.6.1", install)
        self.assertTrue(Path(rows["topic"]["command"][0]).is_relative_to(self.out / "topic" / ".venv-topic"))
        self.assertEqual(rows["installed-environment-env-topic"]["status"], "PASS")
        self.assertEqual(report["optional_checks"]["selected"], ["topic"])

    def test_optional_environment_failure_skips_its_checks_with_that_category(self):
        topic = {"name": "topic", "argv": ["{python}", "examples/topic.py"], "requirements": "requirements-topic.txt",
                 "optional": True}
        self.write_checks(default_checks() + [topic, dict(topic, name="topic-next", needs=["topic"])])
        with patch.object(alignment.subprocess, "run", side_effect=self.fake_processes(
                topic_install_error="ERROR: No matching distribution found for scipy==99")):
            report = alignment.AlignmentCheck(self.root, self.out, optional=["all"]).run()
        rows = self.rows(report)
        self.assertEqual((rows["install-env-topic"]["status"], rows["install-env-topic"]["category"]),
                         ("FAIL", "package_api"))
        self.assertEqual((rows["topic"]["status"], rows["topic"]["blocked_by"]), ("SKIPPED", "install-env-topic"))
        self.assertEqual((rows["topic-next"]["status"], rows["topic-next"]["blocked_by"]), ("SKIPPED", "topic"))
        self.assertEqual(rows["beam-summary"]["status"], "PASS")

    def test_check_needing_a_newer_python_is_skipped_without_building_its_environment(self):
        topic = {"name": "topic", "argv": ["{python}", "examples/topic.py"], "requirements": "requirements-topic.txt",
                 "optional": True, "requires_python": "3.99"}
        self.write_checks(default_checks() + [topic, dict(topic, name="topic-next", needs=["topic"],
                                                          requires_python="3.11")])
        with patch.object(alignment.subprocess, "run", side_effect=self.fake_processes()) as commands:
            report = alignment.AlignmentCheck(self.root, self.out, optional=["all"]).run()
        rows = self.rows(report)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual((rows["topic"]["status"], rows["topic"]["category"]), ("SKIPPED", "setup"))
        self.assertIn("Requires Python >= 3.99", rows["topic"]["error"])
        self.assertEqual((rows["topic-next"]["status"], rows["topic-next"]["blocked_by"]), ("SKIPPED", "topic"))
        self.assertFalse(self.calls(commands, "requirements-topic.txt"))

    def test_pinned_catalog_file_is_verified_imported_and_reported_distinctly(self):
        runner = alignment.AlignmentCheck(self.root, self.out, catalog_file=self.catalog)
        with patch.object(alignment.subprocess, "run", side_effect=self.fake_processes(
                sync_error="CATALOG_SERVICE_UNREACHABLE")) as commands:
            report = runner.run()
        self.assertEqual(report["status"], "PASS")
        rows = self.rows(report)
        self.assertNotIn("catalog-sync", rows)
        self.assertFalse(self.calls(commands, "catalog sync"))
        self.assertEqual(rows["catalog-import"]["command"][-3:], ["catalog", "import", str(self.catalog.resolve())])
        self.assertEqual(rows["catalog-file"]["sha256"], self.catalog_sha256)
        self.assertEqual(report["catalog_source"], {
            "mode": "file", "file": str(self.catalog.resolve()), "sha256": self.catalog_sha256,
            "expected_sha256": self.catalog_sha256, "file_catalog_version": "2026-10-15.1",
            "automatic_check_interval": "0", "catalog_version": "2026-10-15.1",
            "active_sha256": self.catalog_sha256, "service_url": CATALOG_URL})
        self.assertTrue(all(call.kwargs["env"]["OPENSCI_CATALOG_CHECK_INTERVAL"] == "0"
                            for call in commands.call_args_list))

    def test_catalog_file_digest_mismatch_blocks_catalog_checks_as_data_failure(self):
        for index, (sha256, error) in enumerate((("0" * 64, "differs from published"), (None, "No published SHA-256"))):
            with self.subTest(error=error):
                if sha256 is None:
                    self.catalog.write_text(json.dumps({"catalog_version": "2026-11-01.1"}), encoding="utf-8")
                runner = alignment.AlignmentCheck(self.root, self.out / str(index), catalog_file=self.catalog,
                                                  catalog_sha256=sha256)
                with patch.object(alignment.subprocess, "run", side_effect=self.fake_processes()) as commands:
                    report = runner.run()
                rows = self.rows(report)
                self.assertEqual((rows["catalog-file"]["status"], rows["catalog-file"]["category"]),
                                 ("FAIL", "catalog_data"))
                self.assertIn(error, rows["catalog-file"]["error"])
                self.assertEqual((rows["beam-summary"]["category"], rows["beam-summary"]["blocked_by"]),
                                 ("catalog_data", "catalog-file"))
                self.assertFalse(self.calls(commands, "catalog import"))
        # A newer published release is accepted with its digest from the manifest.
        digest = hashlib.sha256(self.catalog.read_bytes()).hexdigest()
        runner = alignment.AlignmentCheck(self.root, self.out / "explicit", catalog_file=self.catalog,
                                          catalog_sha256=digest)
        status = {"active": {"catalog_version": "2026-11-01.1", "sha256": digest}, "service": {}}
        with patch.object(alignment.subprocess, "run", side_effect=self.fake_processes(status=status)):
            self.assertEqual(runner.run()["status"], "PASS")

    def test_catalog_identity_rejects_unexpected_active_release_or_retired_service_host(self):
        for index, status in enumerate((
            {"active": {"catalog_version": "2026-10-15.1", "sha256": "f" * 64}, "service": {}},
            {"active": None, "service": {}},
            {"active": {"catalog_version": "2026-10-15.1", "sha256": "f" * 64},
             "service": {"manifest_url": f"https://{RETIRED}/api/v1/catalog"}},
        )):
            with self.subTest(status=status):
                catalog_file = self.catalog if index == 0 else None
                runner = alignment.AlignmentCheck(self.root, self.out / str(index), catalog_file=catalog_file)
                with patch.object(alignment.subprocess, "run", side_effect=self.fake_processes(status=status)):
                    rows = self.rows(runner.run())
                self.assertEqual(rows["catalog-identity"]["status"], "FAIL")
                self.assertEqual(rows["beam-summary"]["blocked_by"], "catalog-identity")

    def test_catalog_sha256_option_needs_a_catalog_file(self):
        with patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit) as raised:
            alignment.main(["--catalog-sha256", "0" * 64, "--out", str(self.out)])
        self.assertEqual(raised.exception.code, 2)
        self.assertFalse(self.out.exists())

    def test_local_wheel_is_explicit_hashed_and_never_publication_evidence(self):
        wheel = self.root / "os_optics-0.7.0-py3-none-any.whl"
        wheel.write_bytes(b"candidate wheel")
        digest = alignment.sha256_file(wheel)
        baseline = (self.root / "engine-baseline.json").read_bytes()
        runner = alignment.AlignmentCheck(self.root, self.out, "0.7.0", candidate_wheel=wheel,
                                          candidate_sha256=digest)
        with patch.object(alignment.subprocess, "run", side_effect=self.fake_processes(version="0.7.0")) as calls:
            report = runner.run()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["engine_source"]["mode"], "local-wheel")
        self.assertFalse(report["engine_source"]["publication_verified"])
        self.assertEqual(report["engine_source"]["sha256"], digest)
        self.assertEqual(baseline, (self.root / "engine-baseline.json").read_bytes())
        self.assertIn("os-optics[protected] @ " + wheel.as_uri(), self.calls(calls, "install")[0].args[0])

    def test_bad_candidate_digest_stops_before_installing(self):
        wheel = self.root / "os_optics-0.7.0-py3-none-any.whl"
        wheel.write_bytes(b"candidate wheel")
        for i, (version, digest) in enumerate((("0.7.0", "0" * 64), (None, alignment.sha256_file(wheel)))):
            with self.subTest(version=version):
                runner = alignment.AlignmentCheck(self.root, self.out / str(i), version,
                    candidate_wheel=wheel, candidate_sha256=digest)
                with patch.object(alignment.subprocess, "run") as commands:
                    report = runner.run()
                commands.assert_not_called()
                self.assertEqual(report["status"], "FAIL")
                self.assertEqual(report["checks"][-1]["name"], "candidate-artifact")

    def test_local_wheel_cli_requires_all_identity_arguments(self):
        for extra in (["--candidate-wheel", "test.whl"], ["--candidate-sha256", "0" * 64],
                      ["--candidate", "latest", "--candidate-wheel", "test.whl", "--candidate-sha256", "0" * 64]):
            with self.subTest(extra=extra), patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit) as raised:
                alignment.main(["--out", str(self.out)] + extra)
            self.assertEqual(raised.exception.code, 2)
        self.assertFalse(self.out.exists())

    def test_missing_or_untrusted_pip_artifact_blocks_examples(self):
        for i, url in enumerate(("https://example.org/engine.whl", None)):
            runner = alignment.AlignmentCheck(self.root, self.out / str(i))
            normal = self.fake_processes()
            def run(command, **kwargs):
                result = normal(command, **kwargs)
                if "--report" in command:
                    path = Path(command[command.index("--report") + 1])
                    if url is None:
                        path.unlink()
                    else:
                        data = json.loads(path.read_text())
                        data["install"][0]["download_info"]["url"] = url
                        path.write_text(json.dumps(data))
                return result
            with patch.object(alignment.subprocess, "run", side_effect=run):
                report = runner.run()
            rows = self.rows(report)
            self.assertEqual(rows["installed-artifact"]["status"], "FAIL")
            self.assertEqual(rows["offline-tests"]["blocked_by"], "installed-artifact")
            self.assertFalse(report["engine_source"]["publication_verified"])


class WorkflowTests(unittest.TestCase):
    def test_workflow_avoids_duplicate_runs_and_limits_issue_permission(self):
        text = (ROOT / ".github" / "workflows" / "engine-alignment.yml").read_text(encoding="utf-8")
        self.assertIn("push:\n    branches: [main]", text)
        self.assertEqual(text.count("issues: write"), 1)
        self.assertIn("if: ${{ always() && github.event_name == 'schedule' }}", text)
        self.assertIn("fromJSON(needs.baseline.outputs.python)", text)
        baseline = json.loads((ROOT / "engine-baseline.json").read_text(encoding="utf-8"))
        self.assertEqual(baseline["ci_python"][0], "3.11")


if __name__ == "__main__":
    unittest.main()
