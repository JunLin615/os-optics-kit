"""Offline checks of the alignment runner; no package installation or network use."""

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
CATALOG_VERSION = "2026-10-15.1"


def default_checks():
    """The repository's default (non-optional) checks."""
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
            json.dumps({"info": {"version": "0.13.0"}})
        )) as request:
            self.assertEqual(alignment.latest_version(), "0.13.0")
        self.assertEqual(request.call_args.args[0].full_url, "https://pypi.org/pypi/os-optics/json")
        self.assertEqual(request.call_args.kwargs["timeout"], 30)
        with patch.object(alignment.urllib.request, "urlopen", return_value=io.StringIO(
            json.dumps({"info": {"version": "https://example.com/engine.whl"}})
        )), self.assertRaises(ValueError):
            alignment.latest_version()

    def test_wheel_versions_come_from_the_expected_distribution(self):
        self.assertEqual(alignment.wheel_version("dist/os_optics-0.12.0-py3-none-any.whl", "os-optics"), "0.12.0")
        self.assertEqual(alignment.wheel_version("os_optics_kit-0.1.0-py3-none-any.whl", "os-optics-kit"), "0.1.0")
        for name, distribution in (("os_optics_kit-0.1.0-py3-none-any.whl", "os-optics"),
                                   ("os_optics-0.12.0.tar.gz", "os-optics"),
                                   ("os_optics-0.12.0+local-py3-none-any.whl", "os-optics")):
            with self.subTest(name=name), self.assertRaises(ValueError):
                alignment.wheel_version(name, distribution)


class BaselineTests(unittest.TestCase):
    def test_repository_baseline_is_readable(self):
        baseline = alignment.read_baseline(ROOT)
        self.assertEqual(baseline["kit_version"], alignment.source_kit_version(ROOT))
        self.assertRegex(baseline["catalog_sha256"], "^[0-9a-f]{64}$")
        self.assertEqual(baseline["python"][0], "3.11")

    def test_malformed_baselines_are_rejected(self):
        valid = json.loads((ROOT / "baseline.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for change in ({"schema": "other"}, {"engine": {"verified_version": "latest"}},
                           {"catalog": dict(valid["catalog"], sha256="F" * 64)},
                           {"catalog": dict(valid["catalog"], version="current")}, {"kit_version": None}):
                with self.subTest(change=change):
                    (root / "baseline.json").write_text(json.dumps(dict(valid, **change)), encoding="utf-8")
                    with self.assertRaises((ValueError, KeyError, TypeError)):
                        alignment.read_baseline(root)


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

    def test_tools_cases_and_catalog_tests_run_by_default(self):
        definitions = alignment.load_checks(ROOT)
        by_name = {check["name"]: check for check in definitions["checks"]}
        default = alignment.select_checks(definitions["checks"], [])
        flow = ("beam-summary", "catalog-candidates", "spec-be-swap", "spec-be-sweep", "spec-be-move",
                "spec-be-compare", "spec-be-plot", "spec-be-report", "catalog-tests", "cases-verify")
        for name in flow:
            with self.subTest(name=name):
                self.assertIn(name, default)
                self.assertTrue(by_name[name]["needs_catalog"])
        self.assertEqual(by_name["cases-verify"]["argv"][1:6], ["-m", "os_optics_kit", "cases", "verify", "--all"])
        self.assertIn("offline-tests", default)
        self.assertFalse(by_name["offline-tests"]["needs_catalog"])

    def test_catalog_tests_name_existing_classes_and_the_fixture_reads_their_switch(self):
        tests = {check["name"]: check for check in alignment.load_checks(ROOT)["checks"]}["catalog-tests"]
        self.assertEqual(tests["env"], {"OS_OPTICS_KIT_REQUIRE_CATALOG": "1"})
        for node in [arg for arg in tests["argv"] if "::" in arg]:
            path, cls = node.split("::")
            self.assertIn(f"class {cls}(", (ROOT / path).read_text(encoding="utf-8"))
        fixtures = (ROOT / "tests" / "workflow_fixtures.py").read_text(encoding="utf-8")
        self.assertIn("OS_OPTICS_KIT_REQUIRE_CATALOG", fixtures)

    def test_repository_paths_in_check_arguments_exist(self):
        for check in alignment.load_checks(ROOT)["checks"]:
            for arg in check["argv"][1:]:
                if arg.startswith(("cases/", "tests/", "scripts/")):
                    with self.subTest(check=check["name"], path=arg):
                        self.assertTrue((ROOT / arg.split("::")[0]).exists())

    def test_defaults_and_normalization(self):
        definitions = self.load([{"name": "workflow", "argv": ["{python}", "cases/w.py", "--out", "{runs}/w"],
                                  "expected_status": "PASS"}], default_requirements=["requirements-topic.txt"])
        self.assertEqual(definitions["default_requirements"], ["requirements-topic.txt"])
        self.assertEqual(definitions["checks"][0], {
            "name": "workflow", "description": None, "argv": ["{python}", "cases/w.py", "--out", "{runs}/w"],
            "expected_exit": 0, "expected_status": ["PASS"], "status_field": "status", "needs": [],
            "requirements": None, "requires_python": None, "env": {}, "timeout": 180, "json_error": False,
            "needs_catalog": False, "optional": False})

    def test_invalid_definitions_are_rejected(self):
        valid = {"name": "workflow", "argv": ["{python}", "w.py"]}
        for change in (
            {"argv": ["python", "w.py"]}, {"argv": "{python} w.py"}, {"argv": ["{python}", "{home}/w.py"]},
            {"name": "catalog-sync"}, {"name": "install-env-topic"}, {"name": "install-kit"},
            {"name": "kit-artifact"}, {"name": "Upper"}, {"unknown": 1},
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
            {"name": "next", "argv": ["{python}", "a.py"], "needs": ["base"]},
            {"name": "topic-a", "argv": ["{python}", "b.py"], "optional": True},
            {"name": "topic-b", "argv": ["{python}", "c.py"], "optional": True, "needs": ["topic-a"]},
        ])["checks"]
        self.assertEqual(alignment.select_checks(checks, []), {"base", "next"})
        self.assertEqual(alignment.select_checks(checks, ["topic-b"]), {"base", "next", "topic-a", "topic-b"})
        self.assertEqual(alignment.select_checks(checks, ["topic-a,topic-b"]),
                         {"base", "next", "topic-a", "topic-b"})
        with self.assertRaisesRegex(ValueError, "Unknown optional checks"):
            alignment.select_checks(checks, ["topic-c"])

    def test_only_and_skip_narrow_the_selection(self):
        checks = self.load([
            {"name": "base", "argv": ["{python}", "a.py"]},
            {"name": "next", "argv": ["{python}", "a.py"], "needs": ["base"]},
            {"name": "other", "argv": ["{python}", "a.py"]},
            {"name": "topic", "argv": ["{python}", "b.py"], "optional": True, "needs": ["next"]},
        ])["checks"]
        self.assertEqual(alignment.select_checks(checks, [], only=["next"]), {"base", "next"})
        self.assertEqual(alignment.select_checks(checks, [], only=["topic"]), {"base", "next", "topic"})
        self.assertEqual(alignment.select_checks(checks, ["all"], skip=["base"]), {"other"})
        self.assertEqual(alignment.select_checks(checks, [], skip=["other,next"]), {"base"})
        with self.assertRaisesRegex(ValueError, "Unknown checks"):
            alignment.select_checks(checks, [], skip=["missing"])


class AlignmentRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "repository"
        (self.root / "scripts").mkdir(parents=True)
        (self.root / "tools" / "os_optics_kit").mkdir(parents=True)
        (self.root / "tools" / "os_optics_kit" / "_version.py").write_text('__version__ = "0.1.0"\n',
                                                                           encoding="utf-8")
        self.out = Path(self.temporary.name) / "evidence"
        (self.root / "scripts" / "requirements-alignment.txt").write_text("pytest\n", encoding="utf-8")
        (self.root / "requirements-topic.txt").write_text("scipy\n", encoding="utf-8")
        self.catalog = Path(self.temporary.name) / "release.json"
        self.catalog.write_text(json.dumps({"catalog_version": CATALOG_VERSION, "products": []}), encoding="utf-8")
        self.catalog_sha256 = hashlib.sha256(self.catalog.read_bytes()).hexdigest()
        self.baseline = {"schema": "os-optics-kit.baseline", "schema_version": 1, "kit_version": "0.1.0",
                         "engine": {"distribution": "os-optics", "requirement": ">=0.6,<0.7",
                                    "verified_version": "0.6.1"},
                         "catalog": {"version": CATALOG_VERSION, "schema_version": "3.2",
                                     "sha256": self.catalog_sha256},
                         "python": ["3.11", "3.14"]}
        (self.root / "baseline.json").write_text(json.dumps(self.baseline), encoding="utf-8")
        self.write_checks(default_checks())

    def write_checks(self, checks):
        (self.root / "alignment-checks.json").write_text(json.dumps({
            "schema_version": 1, "default_requirements": ["scripts/requirements-alignment.txt"], "checks": checks,
        }), encoding="utf-8")

    def fake_processes(self, *, version="0.6.1", kit_version="0.1.0", sync_error=None, beam_error=None,
                       status=None, topic_install_error=None, outside=None):
        def run(command, **kwargs):
            code, stdout, stderr = 0, "", ""
            if "--report" in command:
                install_report = Path(command[command.index("--report") + 1])
                wheel = next((arg.split(" @ ", 1)[1] for arg in command
                              if arg.startswith("os-optics[protected] @ file:")), None)
                digest = hashlib.sha256(b"engine wheel").hexdigest() if wheel else "a" * 64
                install_report.write_text(json.dumps({"version": "1", "install": [{"metadata": {"name": "os-optics"},
                    "download_info": {"url": wheel if wheel else
                    "https://files.pythonhosted.org/packages/os_optics.whl",
                    "archive_info": {"hashes": {"sha256": digest}}}}]}), encoding="utf-8")
            if "-c" in command and "engine_version" in command[-1]:
                site = Path(command[0]).parent.parent / "Lib" / "site-packages"
                stdout = json.dumps({"engine_version": version, "kit_version": kit_version, "python": "test",
                                     "python_version": "3.11.9", "platform": "test", "executable": command[0],
                                     "module_path": str(site / "os_optics" / "__init__.py"),
                                     "kit_module_path": outside or str(site / "os_optics_kit" / "__init__.py")})
            elif command[-4:-2] == ["catalog", "sync"] and sync_error:
                if sync_error.startswith("{") or sync_error.startswith("Traceback"):
                    code, stderr = 1, sync_error
                else:
                    code, stdout = 1, json.dumps({"status": "ERROR", "error": {
                        "code": sync_error, "message": "Catalog unavailable", "details": {"url": CATALOG_URL}}},
                        indent=2)
            elif command[-2:] == ["catalog", "status"]:
                stdout = json.dumps(status or {"active": {"catalog_version": CATALOG_VERSION,
                                                          "sha256": self.catalog_sha256},
                                               "service": {"manifest_url": CATALOG_URL}})
            elif "install" in command and "requirements-topic.txt" in " ".join(command) and topic_install_error:
                code, stderr = 1, topic_install_error
            elif "tests.check_beam_summary" in command and beam_error:
                code, stderr = 1, beam_error
            elif "cases/topic.py" in command:
                stdout = json.dumps({"outcome": "WITHIN_TOLERANCE"})
            elif declared_result(command):
                code, stdout, stderr = declared_result(command)
            return subprocess.CompletedProcess(command, code, stdout, stderr)
        return run

    def rows(self, report):
        return {row["name"]: row for row in report["checks"]}

    def calls(self, commands, text):
        return [call for call in commands.call_args_list if text in " ".join(call.args[0])]

    def test_baseline_engine_and_source_kit_install_isolated_without_changing_tracked_files(self):
        originals = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        with patch.dict(alignment.os.environ, {"PYTHONPATH": "private-engine", "PYTHONHOME": "private-runtime"}):
            runner = alignment.AlignmentCheck(self.root, self.out)
        with patch.object(alignment.subprocess, "run", side_effect=self.fake_processes()) as commands:
            report = runner.run()
        self.assertEqual(report["status"], "PASS", [row for row in report["checks"] if row["status"] == "FAIL"])
        self.assertEqual((report["baseline_version"], report["candidate_version"], report["installed_version"],
                          report["kit_version"]), ("0.6.1", "0.6.1", "0.6.1", "0.1.0"))
        self.assertEqual(originals, {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()})
        installs = [call.args[0] for call in commands.call_args_list if "install" in call.args[0]]
        self.assertEqual(installs[0][1:], ["-m", "pip", "--isolated", "install", "--no-compile",
                                           "--index-url", "https://pypi.org/simple", "os-optics[protected]==0.6.1",
                                           "--report", str(self.out / "logs" / "install-engine.json")])
        # The kit never pulls its own engine: the engine under test stays installed.
        self.assertEqual(installs[1][-2:], ["--no-deps", str(self.root.resolve())])
        self.assertIn(str(self.root.resolve() / "scripts" / "requirements-alignment.txt"), installs[2])
        self.assertTrue(report["engine_source"]["publication_verified"])
        self.assertEqual(report["kit_source"]["mode"], "source")
        for call in commands.call_args_list:
            env = call.kwargs["env"]
            # Compare key sets only, so a failure never writes the environment into logs.
            self.assertEqual(set(env) & {"PYTHONPATH", "PYTHONHOME"}, set())
            self.assertEqual((env["PYTHONNOUSERSITE"], env["OPENSCI_CATALOG_CHECK_INTERVAL"]), ("1", "0"))
            self.assertEqual(env["OPENSCI_CATALOG_DIR"], str(self.out.resolve() / ".catalog"))
        sync = self.calls(commands, "catalog sync")
        self.assertEqual(len(sync), 1)
        self.assertEqual(sync[0].args[0][-4:], ["catalog", "sync", "--release", CATALOG_VERSION])
        self.assertEqual(report["catalog_source"]["mode"], "sync")
        self.assertEqual(report["catalog_source"]["catalog_version"], CATALOG_VERSION)
        self.assertEqual(report["catalog_source"]["expected_sha256"], self.catalog_sha256)
        self.assertEqual(report["catalog_source"]["service_url"], CATALOG_URL)
        rows = self.rows(report)
        self.assertEqual((rows["summary-missing-run"]["status"], rows["summary-missing-run"]["exit_code"]),
                         ("PASS", 4))
        self.assertEqual(rows["cases-verify"]["status"], "PASS")

    def test_latest_candidate_is_resolved_and_kept_even_outside_the_declared_range(self):
        runner = alignment.AlignmentCheck(self.root, self.out, "latest")
        with patch.object(alignment, "latest_version", return_value="0.7.0") as latest, \
             patch.object(alignment.subprocess, "run", side_effect=self.fake_processes(version="0.7.0")) as commands:
            report = runner.run()
        latest.assert_called_once_with()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual((report["requested_version"], report["candidate_version"], report["installed_version"]),
                         ("latest", "0.7.0", "0.7.0"))
        self.assertIn("os-optics[protected]==0.7.0", self.calls(commands, "--report")[0].args[0])
        kit = [call.args[0] for call in commands.call_args_list if call.args[0][-1] == str(self.root.resolve())]
        self.assertEqual([command[-2] for command in kit], ["--no-deps"])

    def test_catalog_failures_retain_evidence_and_skip_dependent_checks(self):
        for code, category in (("CATALOG_SERVICE_UNREACHABLE", "catalog_network"),
                               ("CATALOG_RELEASE_NOT_FOUND", "catalog_network"),
                               ("CATALOG_STORE_BUSY", "catalog_network"),
                               ("CATALOG_INTEGRITY_FAILED", "catalog_data"),
                               ("CATALOG_HASH_MISMATCH", "catalog_data"),
                               ("INVALID_INPUT", "package_api")):
            with self.subTest(code=code):
                runner = alignment.AlignmentCheck(self.root, self.out / code)
                with patch.object(alignment.subprocess, "run",
                                  side_effect=self.fake_processes(sync_error=code)) as commands:
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
                self.assertEqual(checks["cases-verify"]["blocked_by"], "catalog-sync")
                self.assertFalse(self.calls(commands, "tests.check_beam_summary"))
                self.assertEqual(checks["offline-tests"]["status"], "PASS")
                self.assertEqual(report["catalog_source"]["service_url"], CATALOG_URL)
                self.assertEqual(json.loads((runner.out / "report.json").read_text())["status"], "FAIL")

    def test_non_json_sync_failures_are_classified_by_explicit_signals(self):
        for index, (stderr, category) in enumerate((
            ("Traceback (most recent call last):\nurllib.error.URLError: <urlopen error [Errno 104]>",
             "catalog_network"),
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
            (json.dumps({"problems": [{"code": "EXAMPLE_UNAVAILABLE", "message": "No compatible lens"}]}),
             "catalog_data"),
            (json.dumps({"code": "CATALOG_SERVICE_ERROR", "message": "Release download failed"}), "catalog_network"),
            ("Traceback (most recent call last):\nAssertionError: result fields changed", "package_api"),
        )):
            with self.subTest(category=category, diagnostic=diagnostic):
                runner = alignment.AlignmentCheck(self.root, self.out / f"beam-{index}")
                with patch.object(alignment.subprocess, "run",
                                  side_effect=self.fake_processes(beam_error=diagnostic)) as commands:
                    report = runner.run()
                checks = self.rows(report)
                self.assertEqual(report["status"], "FAIL")
                self.assertEqual(checks["catalog-sync"]["status"], "PASS")
                self.assertEqual(checks["beam-summary"]["status"], "FAIL")
                self.assertEqual(checks["beam-summary"]["category"], category)
                self.assertEqual(Path(checks["beam-summary"]["stderr"]).read_text(), diagnostic)
                self.assertEqual((checks["saved-run-summary"]["status"], checks["saved-run-summary"]["category"]),
                                 ("SKIPPED", category))
                self.assertFalse(self.calls(commands, "os_optics_kit summary " + str(runner.out / "runs" / "beam")))

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

    def test_wrong_installed_versions_stop_before_running_checks(self):
        for index, (engine, kit, expected) in enumerate((("0.6.0", "0.1.0", "os-optics: expected 0.6.1"),
                                                         ("0.6.1", "0.0.9", "os-optics-kit: expected 0.1.0"))):
            with self.subTest(engine=engine, kit=kit):
                runner = alignment.AlignmentCheck(self.root, self.out / str(index))
                with patch.object(alignment.subprocess, "run", side_effect=self.fake_processes(
                        version=engine, kit_version=kit)) as commands:
                    report = runner.run()
                self.assertEqual(report["status"], "FAIL")
                names = [row["name"] for row in report["checks"]]
                self.assertIn(expected, report["checks"][names.index("installed-version-match")]["error"])
                blocked = report["checks"][names.index("installed-version-match") + 1:]
                self.assertEqual([row["name"] for row in blocked], [check["name"] for check in default_checks()])
                self.assertEqual({(row["status"], row["category"]) for row in blocked}, {("SKIPPED", "package_api")})
                self.assertFalse(self.calls(commands, "pytest"))

    def test_kit_loaded_outside_the_environment_blocks_checks(self):
        runner = alignment.AlignmentCheck(self.root, self.out)
        with patch.object(alignment.subprocess, "run", side_effect=self.fake_processes(
                outside=str(self.root / "tools" / "os_optics_kit" / "__init__.py"))):
            rows = self.rows(runner.run())
        self.assertEqual(rows["installed-package-location"]["status"], "FAIL")
        self.assertEqual(rows["kit-cli"]["blocked_by"], "installed-package-location")

    def test_existing_output_is_rejected_without_touching_prior_evidence(self):
        self.out.mkdir()
        previous = self.out / "report.json"
        previous.write_text('{"status":"PASS","candidate":"older"}', encoding="utf-8")
        original = previous.read_bytes()
        with self.assertRaises(FileExistsError):
            alignment.AlignmentCheck(self.root, self.out)
        self.assertEqual(previous.read_bytes(), original)
        self.assertEqual(list(self.out.iterdir()), [previous])

    def test_invalid_check_list_or_baseline_is_a_setup_failure_without_commands(self):
        (self.root / "alignment-checks.json").write_text('{"schema_version": 1, "checks": [{"name": "x"}]}',
                                                         encoding="utf-8")
        with patch.object(alignment.subprocess, "run") as command:
            report = alignment.AlignmentCheck(self.root, self.out / "checks").run()
        command.assert_not_called()
        self.assertEqual((report["checks"][-1]["name"], report["checks"][-1]["category"]),
                         ("check-definitions", "setup"))
        (self.root / "baseline.json").write_text('{"schema_version": 1}', encoding="utf-8")
        with patch.object(alignment.subprocess, "run") as command:
            report = alignment.AlignmentCheck(self.root, self.out / "baseline").run()
        command.assert_not_called()
        self.assertEqual(report["checks"][-1]["name"], "baseline-record")

    def test_only_and_skip_are_recorded_and_left_out_checks_never_run(self):
        with patch.object(alignment.subprocess, "run", side_effect=self.fake_processes()) as commands:
            report = alignment.AlignmentCheck(self.root, self.out, skip=["cases-verify"]).run()
        self.assertEqual(report["status"], "PASS")
        self.assertNotIn("cases-verify", self.rows(report))
        self.assertFalse(self.calls(commands, "cases verify"))
        self.assertEqual(report["selection"], {"only": [], "skip": ["cases-verify"], "left_out": ["cases-verify"]})
        with patch.object(alignment.subprocess, "run", side_effect=self.fake_processes()) as commands:
            report = alignment.AlignmentCheck(self.root, self.out / "only", only=["kit-cli"]).run()
        self.assertEqual([row["name"] for row in report["checks"] if row["name"] in
                          {check["name"] for check in default_checks()}], ["kit-cli"])
        self.assertFalse(self.calls(commands, "catalog sync"))
        with patch.object(alignment.subprocess, "run") as command:
            report = alignment.AlignmentCheck(self.root, self.out / "unknown", only=["missing"]).run()
        command.assert_not_called()
        self.assertEqual(report["checks"][-1]["name"], "check-selection")

    def test_added_check_runs_without_code_changes(self):
        checks = default_checks() + [{
            "name": "summary-details", "argv": ["{python}", "-m", "os_optics_kit", "summary", "{runs}/beam",
                                                "--details"],
            "needs": ["beam-summary"], "expected_status": ["PASS", "WARNING"], "env": {"STUDY_DIR": "{out}/study"},
        }]
        self.write_checks(checks)

        def run(command, **kwargs):
            if "--details" in command:
                return subprocess.CompletedProcess(command, 0, json.dumps({"status": "WARNING"}), "")
            return self.fake_processes()(command, **kwargs)

        with patch.object(alignment.subprocess, "run", side_effect=run) as commands:
            report = alignment.AlignmentCheck(self.root, self.out).run()
        self.assertEqual(report["status"], "PASS")
        call = self.calls(commands, "--details")[0]
        self.assertEqual(call.args[0][1:], ["-m", "os_optics_kit", "summary", str(self.out / "runs" / "beam"),
                                            "--details"])
        self.assertTrue(Path(call.args[0][0]).is_relative_to(self.out / ".venv"))
        self.assertEqual(call.kwargs["env"]["STUDY_DIR"], str(self.out / "study"))
        self.assertEqual(self.rows(report)["summary-details"]["result_status"], ["PASS", "WARNING"])

    def test_optional_check_runs_only_when_selected_in_its_own_environment(self):
        topic = {"name": "topic", "argv": ["{python}", "cases/topic.py", "--out", "{runs}/topic"],
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
        self.assertEqual(rows["install-kit-env-topic"]["status"], "PASS")
        self.assertTrue(Path(rows["topic"]["command"][0]).is_relative_to(self.out / "topic" / ".venv-topic"))
        self.assertEqual(rows["installed-environment-env-topic"]["status"], "PASS")
        self.assertEqual(report["optional_checks"]["selected"], ["topic"])

    def test_optional_environment_failure_skips_its_checks_with_that_category(self):
        topic = {"name": "topic", "argv": ["{python}", "cases/topic.py"], "requirements": "requirements-topic.txt",
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
        topic = {"name": "topic", "argv": ["{python}", "cases/topic.py"], "requirements": "requirements-topic.txt",
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

    def test_catalog_file_is_verified_imported_and_reported_distinctly(self):
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
            "mode": "file", "file": str(self.catalog.resolve()), "release_requested": None,
            "sha256": self.catalog_sha256, "expected_sha256": self.catalog_sha256, "release": CATALOG_VERSION,
            "catalog_version": CATALOG_VERSION, "active_sha256": self.catalog_sha256, "service_url": CATALOG_URL})

    def test_catalog_file_digest_mismatch_blocks_catalog_checks_as_data_failure(self):
        for index, (sha256, error) in enumerate((("0" * 64, "differs from published"),
                                                 (None, "No published SHA-256"))):
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

    def test_other_catalog_release_needs_its_digest(self):
        runner = alignment.AlignmentCheck(self.root, self.out / "missing", catalog_release="2026-11-01.1")
        with patch.object(alignment.subprocess, "run", side_effect=self.fake_processes()) as commands:
            rows = self.rows(runner.run())
        self.assertIn("No published SHA-256", rows["catalog-sync"]["error"])
        self.assertFalse(self.calls(commands, "catalog sync"))
        status = {"active": {"catalog_version": "2026-11-01.1", "sha256": "b" * 64}, "service": {}}
        runner = alignment.AlignmentCheck(self.root, self.out / "given", catalog_release="2026-11-01.1",
                                          catalog_sha256="b" * 64)
        with patch.object(alignment.subprocess, "run", side_effect=self.fake_processes(status=status)) as commands:
            report = runner.run()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(self.calls(commands, "catalog sync")[0].args[0][-1], "2026-11-01.1")
        self.assertEqual(report["catalog_source"]["release_requested"], "2026-11-01.1")

    def test_catalog_identity_rejects_unexpected_active_release_or_retired_service_host(self):
        for index, status in enumerate((
            {"active": {"catalog_version": CATALOG_VERSION, "sha256": "f" * 64}, "service": {}},
            {"active": {"catalog_version": "2026-10-16.1", "sha256": None}, "service": {}},
            {"active": None, "service": {}},
            {"active": {"catalog_version": CATALOG_VERSION, "sha256": None},
             "service": {"manifest_url": f"https://{RETIRED}/api/v1/catalog"}},
        )):
            with self.subTest(status=status):
                if status["active"] and status["active"]["sha256"] is None:
                    status["active"]["sha256"] = self.catalog_sha256
                catalog_file = self.catalog if index == 0 else None
                runner = alignment.AlignmentCheck(self.root, self.out / str(index), catalog_file=catalog_file)
                with patch.object(alignment.subprocess, "run", side_effect=self.fake_processes(status=status)):
                    rows = self.rows(runner.run())
                self.assertEqual(rows["catalog-identity"]["status"], "FAIL")
                self.assertEqual(rows["beam-summary"]["blocked_by"], "catalog-identity")

    def test_cli_option_combinations(self):
        for extra in (["--catalog-sha256", "0" * 64], ["--engine-sha256", "0" * 64],
                      ["--engine", "latest", "--engine-wheel", "os_optics-0.6.1-py3-none-any.whl"],
                      ["--engine", ">=0.6"], ["--catalog-release", "current"],
                      ["--catalog-file", "a.json", "--catalog-release", "2026-10-15.1"]):
            with self.subTest(extra=extra), patch("sys.stderr", io.StringIO()), \
                    self.assertRaises(SystemExit) as raised:
                alignment.main(["--out", str(self.out)] + extra)
            self.assertEqual(raised.exception.code, 2)
        self.assertFalse(self.out.exists())

    def test_local_engine_wheel_is_hashed_and_never_publication_evidence(self):
        wheel = self.root / "os_optics-0.7.0-py3-none-any.whl"
        wheel.write_bytes(b"engine wheel")
        digest = alignment.sha256_file(wheel)
        baseline = (self.root / "baseline.json").read_bytes()
        runner = alignment.AlignmentCheck(self.root, self.out, engine_wheel=wheel)
        with patch.object(alignment.subprocess, "run", side_effect=self.fake_processes(version="0.7.0")) as calls:
            report = runner.run()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["candidate_version"], "0.7.0")
        self.assertEqual(report["engine_source"]["mode"], "local-wheel")
        self.assertFalse(report["engine_source"]["publication_verified"])
        self.assertEqual(report["engine_source"]["sha256"], digest)
        self.assertEqual(baseline, (self.root / "baseline.json").read_bytes())
        self.assertIn("os-optics[protected] @ " + wheel.as_uri(), self.calls(calls, "--report")[0].args[0])

    def test_bad_engine_wheel_stops_before_installing(self):
        wheel = self.root / "os_optics-0.7.0-py3-none-any.whl"
        wheel.write_bytes(b"engine wheel")
        for index, (engine, digest, path) in enumerate((
                (None, "0" * 64, wheel), ("0.7.1", None, wheel), (None, None, self.root / "os_optics-0.7.0.whl"),
                (None, None, self.root / "os_optics-0.7.0-py3-none-any.whl.missing"))):
            with self.subTest(index=index):
                runner = alignment.AlignmentCheck(self.root, self.out / str(index), engine, engine_wheel=path,
                                                  engine_sha256=digest)
                with patch.object(alignment.subprocess, "run") as commands:
                    report = runner.run()
                commands.assert_not_called()
                self.assertEqual(report["status"], "FAIL")
                self.assertEqual(report["checks"][-1]["name"], "engine-artifact")

    def test_kit_wheel_is_installed_without_dependencies_and_its_version_checked(self):
        wheel = self.root / "os_optics_kit-0.2.0-py3-none-any.whl"
        wheel.write_bytes(b"kit wheel")
        runner = alignment.AlignmentCheck(self.root, self.out, kit_wheel=wheel)
        with patch.object(alignment.subprocess, "run", side_effect=self.fake_processes(kit_version="0.2.0")) as calls:
            report = runner.run()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["kit_source"]["sha256"], alignment.sha256_file(wheel))
        self.assertEqual(report["kit_version"], "0.2.0")
        install = self.calls(calls, "os-optics-kit @ ")[0].args[0]
        self.assertEqual(install[-2:], ["--no-deps", "os-optics-kit @ " + wheel.resolve().as_uri()])
        runner = alignment.AlignmentCheck(self.root, self.out / "mismatch", kit_wheel=wheel)
        with patch.object(alignment.subprocess, "run", side_effect=self.fake_processes(kit_version="0.1.0")):
            rows = self.rows(runner.run())
        self.assertEqual(rows["installed-version-match"]["status"], "FAIL")

    def test_missing_or_untrusted_pip_artifact_blocks_checks(self):
        for index, url in enumerate(("https://example.org/engine.whl", None)):
            runner = alignment.AlignmentCheck(self.root, self.out / str(index))
            normal = self.fake_processes()

            def run(command, url=url, normal=normal, **kwargs):
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


if __name__ == "__main__":
    unittest.main()
