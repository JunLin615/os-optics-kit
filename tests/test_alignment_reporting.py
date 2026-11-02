"""Offline checks for scheduled reporting and its GitHub HTTP boundary."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib import error, parse

from scripts.report_alignment import (
    GitHubIssues, main, observation_marker, publish_report, read_report,
    render_summary,
)


RUN_URL = "https://github.com/example/engine/actions/runs/123"
ARTIFACT_URL = RUN_URL + "/artifacts/456"


def report(version="0.6.1", failed=False):
    return {
        "schema_version": 1,
        "baseline_version": "0.6.1",
        "requested_version": "latest",
        "candidate_version": version,
        "installed_version": version,
        "status": "FAIL" if failed else "PASS",
        "checks": [{
            "name": "example_focus",
            "category": "package_api",
            "status": "FAIL" if failed else "PASS",
            "command": ["/tmp/build-123/.venv/bin/python", "examples/ideal_lens_focus.py"],
            "exit_code": 1 if failed else 0,
            "stdout": "run-alignment/logs/example.stdout.log",
            "stderr": "run-alignment/logs/example.stderr.log",
            "error": "Result API changed" if failed else None,
        }],
        "artifact_directory": "run-alignment",
    }


class FakeHTTP:
    """A stateful issue endpoint; every test stays offline, including POSTs."""

    def __init__(self, issues=None):
        self.issues = list(issues or [])
        self.calls = []

    def __call__(self, req, timeout):
        self.calls.append(req)
        assert timeout == 30
        assert req.get_header("Authorization") == "Bearer fake-token"
        assert req.get_header("X-github-api-version") == "2022-11-28"
        if req.get_method() == "POST":
            payload = json.loads(req.data)
            assert set(payload) == {"title", "body"}
            result = dict(payload, state="open", html_url="https://github.com/example/engine/issues/42")
            self.issues.insert(0, result)
        else:
            assert req.get_method() == "GET"
            params = parse.parse_qs(parse.urlsplit(req.full_url).query)
            assert params["state"] == ["all"]
            assert params["per_page"] == ["100"]
            start = (int(params["page"][0]) - 1) * 100
            result = self.issues[start:start + 100]
        return io.BytesIO(json.dumps(result).encode("utf-8"))

    def client(self):
        return GitHubIssues("example/engine", "fake-token", opener=self)


class AlignmentReportingTests(unittest.TestCase):
    def test_local_candidate_stays_preliminary_and_never_posts_an_observation(self):
        candidate = report("0.9.0", failed=True)
        candidate["engine_source"] = {"mode": "local-wheel", "publication_verified": False,
                                     "filename": "os_optics-0.9.0-py3-none-any.whl", "sha256": "b" * 64}
        http = FakeHTTP()
        self.assertEqual(publish_report(candidate, http.client()), {"action": "none", "url": None})
        self.assertEqual(http.calls, [])
        summary = render_summary(candidate)
        self.assertIn("Local-wheel preparation result", summary)
        self.assertIn("--candidate-wheel os_optics-0.9.0-py3-none-any.whl", summary)
        self.assertIn("--candidate-sha256 " + "b" * 64, summary)

    def test_verified_pass_is_quiet_without_http(self):
        http = FakeHTTP()
        self.assertEqual(publish_report(report(), http.client()), {
            "action": "none", "url": None,
        })
        self.assertEqual(http.calls, [])

    def test_new_passing_version_creates_one_issue_with_run_and_artifact_links(self):
        http = FakeHTTP()
        result = publish_report(report("0.7.0"), http.client(), RUN_URL, ARTIFACT_URL)
        self.assertEqual(result["action"], "created")
        issue = http.issues[0]
        self.assertIn("0.7.0: new unverified version", issue["title"])
        for expected in ("0.6.1", "0.7.0", RUN_URL, ARTIFACT_URL, "PASS",
                         "does not gate engine releases", "--candidate 0.7.0"):
            self.assertIn(expected, issue["body"])

    def test_repeated_observation_does_not_post_comment_or_reopen_closed_issue(self):
        http = FakeHTTP()
        candidate = report("0.7.0")
        first = publish_report(candidate, http.client())
        self.assertEqual(publish_report(candidate, http.client())["action"], "existing")
        http.issues[0]["state"] = "closed"
        repeated = publish_report(candidate, http.client())
        self.assertEqual(repeated, {"action": "existing", "url": first["url"]})
        self.assertEqual([req.get_method() for req in http.calls], ["GET", "POST", "GET", "GET"])
        self.assertEqual(http.issues[0]["state"], "closed")

    def test_new_failure_on_same_version_is_reported_once(self):
        http = FakeHTTP()
        candidate = report("0.7.0")
        publish_report(candidate, http.client())
        failed = report("0.7.0", failed=True)
        self.assertEqual(publish_report(failed, http.client())["action"], "created")
        failed["checks"][0]["error"] = "Different transient text /tmp/run-456"
        self.assertEqual(publish_report(failed, http.client())["action"], "existing")
        self.assertEqual(len(http.issues), 2)

    def test_failed_baseline_is_reported(self):
        http = FakeHTTP()
        self.assertEqual(publish_report(report(failed=True), http.client())["action"], "created")
        self.assertIn("0.6.1: checks failed", http.issues[0]["title"])

    def test_install_failure_without_resolved_version_is_reported_and_deduplicated(self):
        http = FakeHTTP()
        failed = report(None, failed=True)
        failed["checks"][0].update(name="install", category="setup", exit_code=None)
        self.assertEqual(publish_report(failed, http.client())["action"], "created")
        self.assertIn("unresolved latest", http.issues[0]["title"])
        self.assertEqual(publish_report(failed, http.client())["action"], "existing")

    def test_resolved_candidate_identifies_failure_before_installation(self):
        candidate = report("0.7.0", failed=True)
        candidate["installed_version"] = None
        http = FakeHTTP()
        publish_report(candidate, http.client())
        self.assertIn("0.7.0: checks failed", http.issues[0]["title"])

    def test_unreadable_baseline_keeps_actionable_setup_failure(self):
        failed = report(None, failed=True)
        failed["baseline_version"] = None
        failed["checks"][0].update(name="baseline-record", category="setup",
                                   error="engine-baseline.json contains invalid JSON")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "report.json"
            source.write_text(json.dumps(failed), encoding="utf-8")
            loaded = read_report(source)
        self.assertIn("invalid JSON", render_summary(loaded))
        http = FakeHTTP()
        self.assertEqual(publish_report(loaded, http.client())["action"], "created")
        self.assertIn("baseline-record", http.issues[0]["body"])

    def test_fingerprint_ignores_volatile_data_and_requested_alias(self):
        original = report("0.7.0", failed=True)
        changed = deepcopy(original)
        changed["requested_version"] = "0.7.0"
        changed["artifact_directory"] = "/tmp/a-different-run"
        changed["checks"][0].update(
            command=["/tmp/other/.venv/bin/python", "focus.py"],
            stdout="/tmp/new/stdout.log", stderr="/tmp/new/stderr.log",
            error="Different traceback, timestamp and path", exit_code=2,
        )
        changed["checks"].append({"name": "downstream", "category": "catalog_data", "status": "SKIPPED"})
        self.assertEqual(observation_marker(original), observation_marker(changed))

    def test_fingerprint_distinguishes_versions_failed_commands_and_categories(self):
        original = report("0.7.0", failed=True)
        for field, value in (("name", "beam_workflow"), ("category", "catalog_network")):
            with self.subTest(field=field):
                changed = deepcopy(original)
                changed["checks"][0][field] = value
                self.assertNotEqual(observation_marker(original), observation_marker(changed))
        self.assertNotEqual(observation_marker(original), observation_marker(report("0.8.0", failed=True)))

    def test_check_order_does_not_change_fingerprint(self):
        original = report("0.7.0", failed=True)
        original["checks"].append(dict(original["checks"][0], name="beam_workflow"))
        changed = deepcopy(original)
        changed["checks"].reverse()
        self.assertEqual(observation_marker(original), observation_marker(changed))

    def test_scan_paginates_and_matches_closed_issues_but_not_pull_requests(self):
        candidate = report("0.7.0")
        marker = observation_marker(candidate)
        issues = [{"body": marker, "pull_request": {}, "html_url": "wrong"}]
        issues += [{"body": None} for _ in range(99)]
        issues += [{"body": marker, "state": "closed", "html_url": "https://github.com/example/engine/issues/8"}]
        http = FakeHTTP(issues)
        result = publish_report(candidate, http.client())
        self.assertEqual(result, {"action": "existing", "url": issues[-1]["html_url"]})
        self.assertEqual(len(http.calls), 2)
        self.assertTrue(http.calls[-1].full_url.endswith("page=2"))

    def test_summary_contains_failed_command_diagnostic_and_classification(self):
        failed = report("0.7.0", failed=True)
        failed["checks"][0]["category"] = "catalog_network"
        summary = render_summary(failed, RUN_URL, ARTIFACT_URL)
        for expected in ("FAIL", "catalog_network", "examples/ideal_lens_focus.py",
                         "Result API changed", "example.stderr.log", RUN_URL, ARTIFACT_URL):
            self.assertIn(expected, summary)

    def test_expected_nonzero_success_does_not_become_an_issue(self):
        passed = report()
        passed["checks"][0].update(exit_code=1, expected_exit_code=1, result_status="FAIL")
        http = FakeHTTP()
        self.assertEqual(publish_report(passed, http.client())["action"], "none")
        self.assertNotIn("### Failed check", render_summary(passed))

    def test_diagnostics_cannot_close_their_markdown_code_block(self):
        failed = report(failed=True)
        failed["checks"][0]["error"] = "Exception\n```\nMore diagnostic text"
        self.assertIn("````text\nException\n```\nMore diagnostic text\n````", render_summary(failed))

    def test_cli_appends_summary_without_network_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "report.json"
            summary = Path(directory) / "summary.md"
            source.write_text(json.dumps(report("0.7.0")), encoding="utf-8")
            summary.write_text("Existing step summary\n", encoding="utf-8")
            with patch("scripts.report_alignment.request.urlopen") as urlopen:
                self.assertEqual(main([str(source), "--summary", str(summary)]), 0)
                urlopen.assert_not_called()
            self.assertTrue(summary.read_text(encoding="utf-8").startswith("Existing step summary\n## Engine alignment"))

    def test_missing_report_still_writes_actionable_job_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / "summary.md"
            with redirect_stderr(io.StringIO()):
                result = main([str(Path(directory) / "missing.json"), "--summary", str(summary), "--run-url", RUN_URL])
            self.assertEqual(result, 1)
            text = summary.read_text(encoding="utf-8")
            self.assertIn("report unavailable", text)
            self.assertIn(RUN_URL, text)

    def test_schema_rejects_unknown_report_and_check_status(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "report.json"
            for key, value in (("schema_version", 2), ("status", "UNKNOWN"), ("checks", {})):
                invalid = report()
                invalid[key] = value
                source.write_text(json.dumps(invalid), encoding="utf-8")
                with self.subTest(key=key), self.assertRaises(ValueError):
                    read_report(source)
            invalid = report()
            invalid["checks"][0]["status"] = "UNKNOWN"
            source.write_text(json.dumps(invalid), encoding="utf-8")
            with self.assertRaises(ValueError):
                read_report(source)

    def test_publish_failure_preserves_summary_and_reports_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "report.json"
            summary = Path(directory) / "summary.md"
            source.write_text(json.dumps(report("0.7.0")), encoding="utf-8")
            failure = error.HTTPError("https://api.github.com", 503, "Unavailable", None, None)
            with patch.dict("os.environ", {"GH_TOKEN": "fake-token"}), \
                    patch("scripts.report_alignment.request.urlopen", side_effect=failure), \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as stderr:
                result = main([str(source), "--summary", str(summary), "--publish", "--repository", "example/engine"])
            self.assertEqual(result, 1)
            self.assertIn("503", stderr.getvalue())
            self.assertIn("## Engine alignment: PASS", summary.read_text(encoding="utf-8"))

    def test_summary_names_catalog_source_python_and_blocking_check(self):
        failed = report("0.8.0", failed=True)
        failed["checks"][0].update(name="catalog-sync", category="catalog_network")
        failed["checks"].append({"name": "beam-summary", "category": "catalog_network", "status": "SKIPPED",
                                 "blocked_by": "catalog-sync"})
        failed.update(python_version="3.11.16", catalog_source={
            "mode": "sync", "file": None, "service_url": "https://opensci-optics.com/api/v1/catalog"})
        failed["optional_checks"] = {"requested": [], "selected": [], "not_selected": ["interop-optimization"]}
        failed["checks"].append({"name": "interop-quantum", "category": "setup", "status": "SKIPPED",
                                 "requires_python": "3.12"})
        summary = render_summary(read_back(failed))
        for expected in ("| Python | 3.11.16 |", "fresh sync from https://opensci-optics.com/api/v1/catalog, no active release",
                         "SKIPPED (blocked by catalog-sync)", "interop-optimization",
                         "SKIPPED (requires Python &gt;= 3.12)"):
            self.assertIn(expected, summary)
        imported = report("0.8.0")
        imported["requested_version"] = "0.8.0"
        imported.update(catalog_source={"mode": "file", "file": "/data/2026-10-15.1.json", "sha256": "ce97" * 16,
                                        "expected_sha256": "ce97" * 16, "catalog_version": "2026-10-15.1"},
                        optional_checks={"requested": ["interop-optimization"], "selected": [], "not_selected": []})
        summary = render_summary(read_back(imported))
        self.assertIn("imported file /data/2026-10-15.1.json (sha256 " + "ce97" * 16 + "), release 2026-10-15.1", summary)
        self.assertIn("--catalog-file /data/2026-10-15.1.json --catalog-sha256 " + "ce97" * 16
                      + " --optional interop-optimization", summary)

    def test_new_report_fields_do_not_change_the_fingerprint(self):
        original = report("0.8.0", failed=True)
        extended = deepcopy(original)
        extended.update(python_version="3.14.0", catalog_source={"mode": "sync", "service_url": "x"},
                        optional_checks={"requested": ["all"], "selected": ["topic"], "not_selected": []})
        extended["checks"].append({"name": "topic", "category": "package_api", "status": "SKIPPED",
                                   "blocked_by": "install-env-topic"})
        self.assertEqual(observation_marker(original), observation_marker(extended))

    def test_schema_rejects_malformed_optional_objects(self):
        for field in ("catalog_source", "optional_checks"):
            invalid = report()
            invalid[field] = "file"
            with self.subTest(field=field), self.assertRaises(ValueError):
                read_back(invalid)


def read_back(document):
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "report.json"
        source.write_text(json.dumps(document), encoding="utf-8")
        return read_report(source)


if __name__ == "__main__":
    unittest.main()
