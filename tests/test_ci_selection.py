"""Offline tests of the CI case selection and the once-per-tree verification record (no engine, no network)."""

from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import changed_cases, verified_tree


TREE = "1" * 40
OTHER_TREE = "2" * 40
COMMIT = "3" * 40
REPO, REPO_ID, FORK_ID = "owner/kit", 1001, 2002
RUN_ID = 77
NOW = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
CASES = {"beam-expander", "ideal-lens-focus", "sonnar-telephoto-lens"}
BASELINE = {"engine": "0.13.0", "catalog_version": "2026-10-28.1", "catalog_sha256": "a" * 64}
API = "https://api.example"


def verify_document(results=None):
    results = results or {name: "MATCH" for name in sorted(CASES)}
    counts = {key: sum(1 for r in results.values() if r == key) for key in ("MATCH", "MISMATCH", "ERROR")}
    return {"status": "MATCH" if counts["MATCH"] == len(results) else "MISMATCH", "counts": counts,
            "cases": [{"name": name, "result": result} for name, result in results.items()]}


REPORT = {"installed_version": "0.13.0",
          "catalog_source": {"catalog_version": "2026-10-28.1", "active_sha256": "a" * 64}}


def record(**changes):
    value = verified_tree.make_record(verify_document(), REPORT, tree=TREE, commit=COMMIT, event="pull_request",
                                      run_id=RUN_ID, cases=CASES)
    value.update(changes)
    return value


def artifact(**changes):
    value = {"id": 5, "name": verified_tree.artifact_name(TREE), "expired": False,
             "created_at": "2026-11-10T12:00:00Z", "expires_at": "2026-12-10T12:00:00Z",
             "workflow_run": {"id": RUN_ID, "repository_id": REPO_ID, "head_repository_id": REPO_ID}}
    value.update(changes)
    return value


def archive(document, name=verified_tree.RECORD_FILE) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as handle:
        handle.writestr(name, json.dumps(document))
    return buffer.getvalue()


class FakeGitHub:
    """Serves the artifact list, the redirecting download and the storage URL; records every request."""

    def __init__(self, artifacts, records=None, list_status=200, storage_status=200, storage_body=None, error=None):
        self.artifacts, self.records = artifacts, records or {}
        self.list_status, self.storage_status, self.error = list_status, storage_status, error
        self.storage_body = storage_body
        self.requests = []

    def __call__(self, url, headers):
        self.requests.append((url, dict(headers)))
        if self.error:
            raise self.error
        if url.startswith(f"{API}/repos/{REPO}/actions/artifacts?"):
            return self.list_status, {}, json.dumps({"total_count": len(self.artifacts),
                                                     "artifacts": self.artifacts}).encode()
        prefix = f"{API}/repos/{REPO}/actions/artifacts/"
        if url.startswith(prefix) and url.endswith("/zip"):
            artifact_id = url[len(prefix):-len("/zip")]
            return 302, {"location": f"https://storage.example/blob/{artifact_id}?sig=x"}, b""
        if url.startswith("https://storage.example/blob/"):
            artifact_id = int(url.rsplit("/", 1)[1].split("?")[0])
            body = self.storage_body if self.storage_body is not None else archive(self.records[artifact_id])
            return self.storage_status, {}, body
        return 404, {}, b""


def look(fake, token="t0ken", cases=CASES, baseline=BASELINE):
    return verified_tree.lookup(tree=TREE, repository=REPO, repository_id=REPO_ID, token=token, baseline=baseline,
                                cases=cases, fetch=fake, api=API, server="https://github.example", now=NOW)


class SelectionTests(unittest.TestCase):
    existing = {"beam-expander", "ideal-lens-focus"}

    def test_every_case_path_selects_all_cases(self):
        self.assertEqual(changed_cases.EVERY_CASE, {
            "baseline.json", "pyproject.toml", "alignment-checks.json", ".github/workflows/ci.yml",
            "scripts/changed_cases.py", "scripts/verified_tree.py", "scripts/check_alignment.py",
            "scripts/requirements-alignment.txt", "tools/os_optics_kit/__init__.py",
            "tools/os_optics_kit/__main__.py", "tools/os_optics_kit/_version.py", "tools/os_optics_kit/cli.py",
            "tools/os_optics_kit/cases.py", "tools/os_optics_kit/case_taxonomy.py", "tools/os_optics_kit/_engine.py",
            "tools/os_optics_kit/_frontmatter.py", "tools/os_optics_kit/_resources.py"})
        for path in sorted(changed_cases.EVERY_CASE):
            with self.subTest(path=path):
                self.assertEqual(changed_cases.select_cases(["docs/en/x.md", path], self.existing),
                                 {"all": True, "cases": ["beam-expander", "ideal-lens-focus"]})

    def test_every_case_files_exist_and_cover_the_verify_command_path(self):
        root = Path(__file__).resolve().parents[1]
        for path in sorted(changed_cases.EVERY_CASE):
            with self.subTest(path=path):
                self.assertTrue((root / path).is_file(), path)
        # The CLI entry point, the environment script and every kit module cases.py imports select every case.
        package = root / "tools" / "os_optics_kit"
        imported = set(re.findall(r"^\s*from \.(\w+) import|^\s*from \. import (\w+)",
                                  (package / "cases.py").read_text(encoding="utf-8"), re.M))
        modules = {name for pair in imported for name in pair if name}
        self.assertTrue(modules)
        for module in sorted(modules):
            with self.subTest(module=module):
                self.assertIn(f"tools/os_optics_kit/{module}.py", changed_cases.EVERY_CASE)
        workflow = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("python scripts/check_alignment.py --out run-alignment", workflow)
        self.assertIn("-m os_optics_kit cases verify", workflow)

    def test_case_directories_are_selected_and_other_paths_are_not(self):
        paths = ["cases/beam-expander/images/iso.png", "cases/beam-expander/README.md", "cases/README.md",
                 "cases/cases.json", "docs/case-format.md", "tools/os_optics_kit/render_views.py",
                 "scripts/check_repo.py", ".github/workflows/scheduled.yml"]
        self.assertEqual(changed_cases.select_cases(paths, self.existing), {"all": False, "cases": ["beam-expander"]})

    def test_deleted_and_unknown_cases_are_not_selected(self):
        self.assertEqual(changed_cases.select_cases(["cases/removed/CASE.md", "cases/removed/project.json"],
                                                    self.existing), {"all": False, "cases": []})
        self.assertEqual(changed_cases.select_cases([], self.existing), {"all": False, "cases": []})

    def test_push_and_merge_group_select_every_case(self):
        for event in ("push", "merge_group"):
            with self.subTest(event=event):
                self.assertEqual(changed_cases.select_cases([], self.existing, event=event),
                                 {"all": True, "cases": ["beam-expander", "ideal-lens-focus"]})
        with self.assertRaises(ValueError):
            changed_cases.select_cases([], self.existing, event="schedule")

    def test_existing_cases_need_a_case_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "cases" / "a").mkdir(parents=True)
            (root / "cases" / "a" / "CASE.md").write_text("x", encoding="utf-8")
            (root / "cases" / "b").mkdir()
            (root / "cases" / "README.md").write_text("x", encoding="utf-8")
            self.assertEqual(changed_cases.existing_cases(root), {"a"})

    def test_main_writes_outputs_with_the_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "out"
            with patch.object(changed_cases, "_git", side_effect=lambda *args: TREE + "\n"), \
                    patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}), patch("sys.stdout", io.StringIO()):
                self.assertEqual(changed_cases.main(["--event", "push"]), 0)
            text = output.read_text(encoding="utf-8")
            self.assertIn("all=true\n", text)
            self.assertIn(f"tree={TREE}\n", text)

    def test_pull_request_diff_lists_moved_files_on_both_sides(self):
        calls = []

        def git(*args):
            calls.append(args)
            return "cases/beam-expander/x.json\0cases/ideal-lens-focus/x.json\0" if args[0] == "diff" else TREE

        with patch.object(changed_cases, "_git", side_effect=git), patch("sys.stdout", io.StringIO()) as out, \
                patch.dict(os.environ, {"GITHUB_OUTPUT": ""}):
            changed_cases.main(["--event", "pull_request", "BASE", "HEAD"])
        self.assertIn("--no-renames", calls[0])
        self.assertEqual(json.loads(out.getvalue())["cases"], ["beam-expander", "ideal-lens-focus"])


class RecordTests(unittest.TestCase):
    def test_a_full_all_match_verification_is_recorded(self):
        value = record()
        self.assertEqual(value["tree"], TREE)
        self.assertEqual(value["counts"], {"cases": 3, "MATCH": 3, "MISMATCH": 0, "ERROR": 0})
        self.assertEqual((value["engine"], value["catalog"]["sha256"]), ("0.13.0", "a" * 64))

    def test_partial_or_failing_verifications_are_not_recorded(self):
        partial = verify_document({"beam-expander": "MATCH"})
        failing = verify_document({name: "MATCH" for name in CASES} | {"beam-expander": "MISMATCH"})
        erroring = verify_document({name: "MATCH" for name in CASES} | {"ideal-lens-focus": "ERROR"})
        for document in (partial, failing, erroring, {}, {"cases": "x"}):
            with self.subTest(document=document):
                with self.assertRaises(ValueError):
                    verified_tree.make_record(document, REPORT, tree=TREE, commit=COMMIT, event="push",
                                              run_id=RUN_ID, cases=CASES)
        with self.assertRaises(ValueError):
            verified_tree.make_record(verify_document(), {}, tree=TREE, commit=COMMIT, event="push", run_id=RUN_ID,
                                      cases=CASES)
        with self.assertRaises(ValueError):
            verified_tree.make_record(verify_document(), REPORT, tree="HEAD", commit=COMMIT, event="push",
                                      run_id=RUN_ID, cases=CASES)

    def test_record_command_writes_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            (base / "verify.json").write_text(json.dumps(verify_document()), encoding="utf-8")
            (base / "report.json").write_text(json.dumps(REPORT), encoding="utf-8")
            output = base / "output"
            argv = ["record", "--verify", str(base / "verify.json"), "--report", str(base / "report.json"),
                    "--tree", TREE, "--commit", COMMIT, "--event", "push", "--run-id", str(RUN_ID),
                    "--out", str(base / "rec" / "verified-tree.json")]
            with patch.object(verified_tree, "existing_cases", return_value=CASES), \
                    patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}), patch("sys.stdout", io.StringIO()):
                self.assertEqual(verified_tree.main(argv), 0)
            self.assertEqual(output.read_text(encoding="utf-8"),
                             f"recorded=true\nname=cases-verified-{TREE}\n")
            self.assertEqual(json.loads((base / "rec" / "verified-tree.json").read_text(encoding="utf-8"))["tree"],
                             TREE)
            output.unlink()
            with patch.object(verified_tree, "existing_cases", return_value=CASES | {"new-case"}), \
                    patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}), patch("sys.stdout", io.StringIO()):
                self.assertEqual(verified_tree.main(argv), 0)
            self.assertEqual(output.read_text(encoding="utf-8"), "recorded=false\n")


class LookupTests(unittest.TestCase):
    def test_a_valid_record_is_accepted_and_the_token_never_leaves_the_api(self):
        fake = FakeGitHub([artifact()], {5: record()})
        result = look(fake)
        self.assertTrue(result["verified"])
        self.assertEqual(result["run_url"], f"https://github.example/{REPO}/actions/runs/{RUN_ID}")
        self.assertIn(f"name=cases-verified-{TREE}", fake.requests[0][0])
        for url, headers in fake.requests:
            self.assertEqual("Authorization" in headers, url.startswith(API), url)

    def test_expired_artifacts_are_rejected(self):
        for changes in ({"expired": True}, {"expires_at": (NOW - timedelta(minutes=1)).isoformat()},
                        {"expires_at": None}):
            with self.subTest(changes=changes):
                fake = FakeGitHub([artifact(**changes)], {5: record()})
                result = look(fake)
                self.assertFalse(result["verified"])
                self.assertEqual(result["rejected"][0]["problem"], "expired")
                self.assertEqual(len(fake.requests), 1)  # nothing downloaded

    def test_fork_artifacts_are_rejected(self):
        for run in ({"id": RUN_ID, "repository_id": REPO_ID, "head_repository_id": FORK_ID},
                    {"id": RUN_ID, "repository_id": REPO_ID}, {}):
            with self.subTest(run=run):
                fake = FakeGitHub([artifact(workflow_run=run)], {5: record()})
                result = look(fake)
                self.assertFalse(result["verified"])
                self.assertIn("fork", result["rejected"][0]["problem"])
                self.assertEqual(len(fake.requests), 1)

    def test_records_of_another_tree_or_run_are_rejected(self):
        for value, problem in ((record(tree=OTHER_TREE), "tree"), (record(run_id=RUN_ID + 1), "runs"),
                               ({"schema": "x"}, "not a verified-tree record")):
            with self.subTest(problem=problem):
                result = look(FakeGitHub([artifact()], {5: value}))
                self.assertFalse(result["verified"])
                self.assertIn(problem, result["rejected"][0]["problem"])
        result = look(FakeGitHub([artifact(name=verified_tree.artifact_name(OTHER_TREE))], {5: record()}))
        self.assertFalse(result["verified"])

    def test_counts_must_show_every_case_match(self):
        for counts in ({"cases": 3, "MATCH": 2, "MISMATCH": 1, "ERROR": 0},
                       {"cases": 3, "MATCH": 2, "MISMATCH": 0, "ERROR": 1},
                       {"cases": 2, "MATCH": 2, "MISMATCH": 0, "ERROR": 0}, {}):
            with self.subTest(counts=counts):
                result = look(FakeGitHub([artifact()], {5: record(counts=counts)}))
                self.assertFalse(result["verified"])
                self.assertIn("counts", result["rejected"][0]["problem"])
        result = look(FakeGitHub([artifact()], {5: record()}), cases=CASES | {"new-case"})
        self.assertFalse(result["verified"])

    def test_engine_or_catalog_other_than_the_baseline_is_rejected(self):
        for value in (record(engine="0.13.1"), record(catalog={"version": "2026-10-28.1", "sha256": "b" * 64}),
                      record(catalog={"version": "2026-11-01.1", "sha256": "a" * 64})):
            with self.subTest(value=value):
                self.assertFalse(look(FakeGitHub([artifact()], {5: value}))["verified"])
        self.assertFalse(look(FakeGitHub([artifact()], {5: record()}),
                              baseline=BASELINE | {"engine": "0.14.0"})["verified"])

    def test_api_errors_mean_verify(self):
        cases = {"list 500": FakeGitHub([artifact()], {5: record()}, list_status=500),
                 "storage 403": FakeGitHub([artifact()], {5: record()}, storage_status=403),
                 "network": FakeGitHub([artifact()], {5: record()}, error=OSError("unreachable")),
                 "bad archive": FakeGitHub([artifact()], {5: record()}, storage_body=b"not a zip"),
                 "no record file": FakeGitHub([artifact()], storage_body=archive(record(), "other.json"))}
        for label, fake in cases.items():
            with self.subTest(label=label):
                result = look(fake)
                self.assertFalse(result["verified"])
                self.assertIsNotNone(result["reason"])

    def test_no_token_no_artifact_or_a_plain_http_redirect_means_verify(self):
        fake = FakeGitHub([artifact()], {5: record()})
        self.assertFalse(look(fake, token=None)["verified"])
        self.assertEqual(fake.requests, [])
        self.assertEqual(look(FakeGitHub([]))["reason"], "no verification of this tree")

        def insecure(url, headers):
            if url.endswith("/zip"):
                return 302, {"location": "http://storage.example/blob/5"}, b""
            return FakeGitHub([artifact()], {5: record()})(url, headers)
        self.assertFalse(look(insecure)["verified"])

    def test_a_later_valid_artifact_is_used_after_rejected_ones(self):
        fork = artifact(id=6, created_at="2026-11-11T00:00:00Z",
                        workflow_run={"id": 90, "repository_id": REPO_ID, "head_repository_id": FORK_ID})
        result = look(FakeGitHub([artifact(), fork], {5: record()}))
        self.assertTrue(result["verified"])
        self.assertEqual(result["rejected"], [{"id": 6, "problem": "made by a run of another repository (fork)"}])

    def test_lookup_command_writes_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "out"
            with patch.object(verified_tree, "lookup", return_value={"verified": True, "run_url": "https://x/1"}), \
                    patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}), patch("sys.stdout", io.StringIO()):
                self.assertEqual(verified_tree.main(["lookup", "--tree", TREE, "--repository", REPO,
                                                     "--repository-id", str(REPO_ID)]), 0)
            self.assertEqual(output.read_text(encoding="utf-8"), "verified=true\nrun_url=https://x/1\n")


class HttpFetchTests(unittest.TestCase):
    def test_redirects_are_returned_not_followed(self):
        import http.server
        import threading

        seen = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append((self.path, self.headers.get("Authorization")))
                if self.path == "/zip":
                    self.send_response(302)
                    self.send_header("Location", "/storage")
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"zip")

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_port}"
        status, headers, body = verified_tree.http_fetch(base + "/zip", {"Authorization": "Bearer t"})
        self.assertEqual((status, headers.get("location"), body), (302, "/storage", b""))
        self.assertEqual(seen, [("/zip", "Bearer t")])


class SummaryTests(unittest.TestCase):
    def test_summary_lines(self):
        line = verified_tree.summary_line(selection="all", document=verify_document(
            {"beam-expander": "MATCH", "ideal-lens-focus": "MISMATCH", "sonnar-telephoto-lens": "ERROR"}), exit_code=1)
        self.assertEqual(line, "Verify cases (every case): 3 cases, MATCH 1, MISMATCH 1, ERROR 1.")
        self.assertIn("the cases the pull request changes",
                      verified_tree.summary_line(selection="changed", document=verify_document(), exit_code=0))
        self.assertIn("https://x/1", verified_tree.summary_line(selection="all", skipped="verified",
                                                                run_url="https://x/1"))
        self.assertIn("changes no case", verified_tree.summary_line(selection="changed", skipped="none-changed"))
        self.assertIn("no result document (exit 4)",
                      verified_tree.summary_line(selection="all", document=None, exit_code=4))

    def test_summary_command_appends_to_the_step_summary(self):
        with tempfile.TemporaryDirectory() as temporary:
            summary = Path(temporary) / "summary"
            with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary)}), patch("sys.stdout", io.StringIO()):
                verified_tree.main(["summary", "--selection", "all", "--verify", str(Path(temporary) / "none.json"),
                                    "--exit", "4"])
            self.assertIn("no result document (exit 4)", summary.read_text(encoding="utf-8"))

    def run_summary(self, temporary, document, *arguments):
        verify = Path(temporary) / "cases-verify.json"
        if document is not None:
            verify.write_text(json.dumps(document), encoding="utf-8")
        output = io.StringIO()
        with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": ""}), patch("sys.stdout", output):
            code = verified_tree.main(["summary", "--selection", "all", "--verify", str(verify), *arguments])
        return code, output.getvalue()

    def test_summary_fails_when_the_document_is_not_match_even_if_the_exit_code_is_0(self):
        mismatch = verify_document({"beam-expander": "MATCH", "ideal-lens-focus": "MISMATCH"})
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(self.run_summary(temporary, verify_document(), "--exit", "0")[0], 0)
            code, line = self.run_summary(temporary, mismatch, "--exit", "0")
            self.assertEqual(code, 1)
            self.assertIn("status is 'MISMATCH', not 'MATCH', although the command exited 0", line)
            self.assertEqual(self.run_summary(temporary, dict(verify_document(), status="ERROR"), "--exit", "0")[0], 1)
            # A missing or unreadable document, or no exit code at all, never passes.
            self.assertEqual(self.run_summary(temporary, None, "--exit", "0")[0], 1)
            self.assertEqual(self.run_summary(temporary, ["not", "a", "document"], "--exit", "0")[0], 1)
            self.assertEqual(self.run_summary(temporary, mismatch)[0], 1)
            # A non-zero exit code is kept, whatever the document says.
            self.assertEqual(self.run_summary(temporary, mismatch, "--exit", "3")[0], 3)
            self.assertEqual(self.run_summary(temporary, verify_document(), "--exit", "4")[0], 4)

    def test_skipped_summaries_pass(self):
        with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": ""}), patch("sys.stdout", io.StringIO()):
            self.assertEqual(verified_tree.main(["summary", "--selection", "changed", "--skipped", "none-changed"]), 0)
            self.assertEqual(verified_tree.main(["summary", "--selection", "all", "--skipped", "verified",
                                                 "--run-url", "https://x/1"]), 0)
        self.assertEqual(verified_tree.summary_exit(document=None, exit_code=None, skipped="verified"), 0)


if __name__ == "__main__":
    unittest.main()
