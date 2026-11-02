"""Offline tests of the repository checks in scripts/ (no engine, no network)."""

import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import zipfile

from scripts import changed_cases, check_dco, check_dist, check_repo, engine_release


ROOT = Path(__file__).resolve().parents[1]
RETIRED = "opensci-" + "optic.com"
BASELINE = {"schema": "os-optics-kit.baseline", "schema_version": 1, "kit_version": "0.1.0",
            "engine": {"distribution": "os-optics", "requirement": ">=0.12,<0.13", "verified_version": "0.12.0"},
            "catalog": {"version": "2026-10-28.1", "schema_version": "3.2", "sha256": "a" * 64},
            "python": ["3.11", "3.14"]}
PYPROJECT = """[project]
name = "os-optics-kit"
requires-python = ">=3.11"
classifiers = ["Programming Language :: Python :: 3.11", "Programming Language :: Python :: 3.14"]
dependencies = ["os-optics>=0.12,<0.13"]
"""


class RepositoryTree(unittest.TestCase):
    """A small clean repository in a temporary directory (not a Git checkout)."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.write("baseline.json", json.dumps(BASELINE))
        self.write("pyproject.toml", PYPROJECT)
        self.write("tools/os_optics_kit/_version.py", '__version__ = "0.1.0"\n')
        self.write("README.md", "# Kit\n\n[Guide](docs/en/guide.md) · [中文](README.zh-CN.md) · "
                                "![plot](cases/demo/images/plot.png) · [site](https://opensci-optics.com/)\n\n"
                                'Install `pip install "os-optics-kit==0.1.0"` with "os-optics[protected]==0.12.0".\n')
        self.write("README.zh-CN.md", "# 工具包\n\n[指南](docs/zh-CN/guide.md)\n")
        self.write("docs/en/guide.md", "See [the case](../../cases/demo/CASE.md#run) and [top](#guide).\n")
        self.write("docs/zh-CN/guide.md", "见[案例](../../cases/demo/CASE.md)。\n")
        self.write("cases/demo/CASE.md", "---\nname: demo\n---\n## Run\n")
        (self.root / "cases/demo/images").mkdir(parents=True)
        (self.root / "cases/demo/images/plot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 100)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def problems(self, check=None):
        result = check_repo.run_checks(self.root)
        return [(item["check"], item["path"], item["line"]) for item in result["problems"]
                if check is None or item["check"] == check]


class RepositoryCheckTests(RepositoryTree):
    def test_clean_tree_passes(self):
        self.assertEqual(check_repo.run_checks(self.root)["problems"], [])

    def test_broken_and_escaping_links_are_reported_but_code_and_urls_are_not(self):
        self.write("docs/en/guide.md", "See [missing](missing.md) and [up](../../../outside.md).\n\n"
                                       "```markdown\n[example](not-a-file.md)\n```\n\n"
                                       "Inline `[x](nope.md)`, [web](https://example.com/x.md), [mail](mailto:a@b).\n"
                                       "[ref]: ./also-missing.md\n<img src=\"gone.png\">\n")
        self.assertEqual(self.problems("links"), [("links", "docs/en/guide.md", 1), ("links", "docs/en/guide.md", 1),
                                                  ("links", "docs/en/guide.md", 8), ("links", "docs/en/guide.md", 9)])

    def test_size_limits_and_forbidden_files(self):
        (self.root / "cases/demo/images/big.png").write_bytes(b"\0" * (check_repo.MAX_IMAGE_BYTES + 1))
        (self.root / "data.bin").write_bytes(b"\0" * (check_repo.MAX_FILE_BYTES + 1))
        (self.root / "cases/demo/images/ok.png").write_bytes(b"\0" * check_repo.MAX_IMAGE_BYTES)
        for name in ("cases/demo/run-1/manifest.json", "cases/demo/scene.glb", "public_optical_catalog.v2.json",
                     "cases/demo/out/x.txt", "dist.whl"):
            self.write(name, "{}")
        self.assertEqual(sorted(self.problems("size")), [("size", "cases/demo/images/big.png", None),
                                                         ("size", "data.bin", None)])
        self.assertEqual(sorted(path for _, path, _ in self.problems("forbidden")),
                         ["cases/demo/out/x.txt", "cases/demo/run-1/manifest.json", "cases/demo/scene.glb",
                          "dist.whl", "public_optical_catalog.v2.json"])

    def test_machine_paths_and_secrets_are_reported(self):
        self.write("cases/demo/notes.md", "\n".join([
            "C:" + "\\Users\\someone\\project", "E:" + "\\\\working\\\\x", "/home/" + "alice/run",
            "/d/" + "cache/tmp", "~/.opensci/catalog is fine", "relative/home/alice/ is fine",
            "gh" + "p_" + "A" * 36, "pypi-" + "AgE" + "x" * 60, "-----BEGIN " + "OPENSSH PRIVATE KEY-----",
            "oat_" + "Z" * 40]))
        self.assertEqual([line for _, _, line in self.problems("paths")], [1, 2, 3, 4])
        self.assertEqual([line for _, _, line in self.problems("secrets")], [7, 8, 9, 10])

    def test_invalid_json_is_reported(self):
        self.write("cases/demo/expected.json", "{not json")
        self.assertEqual(self.problems("json"), [("json", "cases/demo/expected.json", None)])

    def test_pins_must_match_the_baseline_outside_tests_and_scripts(self):
        self.write("docs/en/guide.md", 'pip install "os-optics==0.11.0" "os-optics-kit[plot]==0.0.9"\n'
                                       "os-optics>=0.12 is a range, not a pin\n" + "opensci-" + "engine==0.9.0\n"
                                       f"https://{RETIRED}/ and "
                                       f"https://img.shields.io/badge/x-{RETIRED.replace('-', '--')}-1\n")
        self.write("tests/test_x.py", '"os-optics[protected]==0.6.1"\n')
        self.write("scripts/x.py", '"os-optics==0.7.0"\n')
        self.assertEqual(self.problems("pins"), [("pins", "docs/en/guide.md", 1), ("pins", "docs/en/guide.md", 1),
                                                 ("pins", "docs/en/guide.md", 3), ("pins", "docs/en/guide.md", 4),
                                                 ("pins", "docs/en/guide.md", 4)])

    def test_language_pairs(self):
        (self.root / "README.zh-CN.md").unlink()
        self.write("docs/zh-CN/only.md", "仅中文\n")
        self.write("CONTRIBUTING.md", "English only is fine here.\n")
        self.assertEqual(sorted(self.problems("pairs")), [("pairs", "README.md", None),
                                                          ("pairs", "docs/zh-CN/only.md", None)])

    def test_baseline_consistency(self):
        for change, fragment in (
            ({"kit_version": "0.2.0"}, "_version.py"),
            ({"engine": dict(BASELINE["engine"], requirement=">=0.12,<0.14")}, "pyproject.toml os-optics"),
            ({"engine": dict(BASELINE["engine"], verified_version="0.13.0")}, "outside"),
            ({"python": ["3.10"]}, "older than requires-python"),
            ({"python": ["3.12"]}, "classifier"),
            ({"catalog": dict(BASELINE["catalog"], sha256="xyz")}, "catalog.sha256"),
            ({"schema": "other"}, "schema"),
        ):
            with self.subTest(change=change):
                self.write("baseline.json", json.dumps(dict(BASELINE, **change)))
                messages = [item["message"] for item in check_repo.run_checks(self.root)["problems"]
                            if item["check"] == "baseline"]
                self.assertTrue(any(fragment in message for message in messages), messages)

    def test_specifier_fallback_without_packaging(self):
        with patch.dict("sys.modules", {"packaging": None, "packaging.specifiers": None, "packaging.version": None}):
            self.assertTrue(check_repo.satisfies("0.12.0", ">=0.12,<0.13"))
            self.assertTrue(check_repo.satisfies("0.12.3", ">=0.12, <0.13"))
            self.assertFalse(check_repo.satisfies("0.13.0", ">=0.12,<0.13"))
            self.assertFalse(check_repo.satisfies("0.11.9", ">=0.12,<0.13"))

    def test_command_line_prints_json_and_annotations(self):
        self.write("docs/en/guide.md", "[missing](missing.md)\n")
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("sys.stdout", stdout), patch("sys.stderr", stderr), \
                patch.dict(check_repo.os.environ, {"GITHUB_ACTIONS": "true"}):
            code = check_repo.main(["--root", str(self.root)])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "FAIL")
        self.assertIn("::error file=docs/en/guide.md,line=1,title=check_repo links::", stderr.getvalue())

    def test_git_checkout_ignores_untracked_ignored_files(self):
        if shutil.which("git") is None:
            self.skipTest("git is not installed")
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.write(".gitignore", "/run*/\n")
        self.write("run-local/huge.json", "{not json")
        self.assertEqual(check_repo.run_checks(self.root)["problems"], [])


class RepositoryStateTests(unittest.TestCase):
    """Checks of this checkout that do not depend on documents other changes are still moving."""

    def test_this_repository_has_no_size_path_secret_json_or_baseline_problems(self):
        problems = [item for item in check_repo.run_checks(ROOT)["problems"]
                    if item["check"] in {"size", "forbidden", "paths", "secrets", "json", "baseline", "pins"}]
        self.assertEqual(problems, [])


class DistributionCheckTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.dist = Path(self.temporary.name)

    def wheel(self, extra=(), metadata_version="0.1.0", requirement="os-optics<0.13,>=0.12"):
        path = self.dist / "os_optics_kit-0.1.0-py3-none-any.whl"
        with zipfile.ZipFile(path, "w") as archive:
            for name in check_dist.WHEEL_REQUIRED:
                archive.writestr(name, "x")
            archive.writestr("os_optics_kit/_data/skills/summary/SKILL.md", "---\n")
            archive.writestr("os_optics_kit-0.1.0.dist-info/METADATA",
                             f"Metadata-Version: 2.4\nName: os-optics-kit\nVersion: {metadata_version}\n"
                             f"Requires-Dist: {requirement}\nRequires-Dist: matplotlib<4,>=3.10; extra == 'plot'\n")
            for name, data in extra:
                archive.writestr(name, data)
        return path

    def sdist(self, extra=()):
        path = self.dist / "os_optics_kit-0.1.0.tar.gz"
        with tarfile.open(path, "w:gz") as archive:
            for name, data in [(name, b"x") for name in check_dist.SDIST_REQUIRED] + list(extra):
                info = tarfile.TarInfo("os_optics_kit-0.1.0/" + name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        return path

    def test_clean_distributions_pass(self):
        self.wheel()
        self.sdist()
        result = check_dist.run(self.dist, "0.1.0", ">=0.12,<0.13")
        self.assertEqual(result["status"], "PASS", result)

    def test_forbidden_members_versions_and_requirements_fail(self):
        self.wheel(extra=[("os_optics_kit/_data/cases/demo/run-1/manifest.json", "{}"),
                          ("os_optics_kit/_data/cases/demo/scene.glb", "x"),
                          ("os_optics_kit/_data/cases/demo/images/big.png",
                           b"\0" * (check_dist.check_repo.MAX_IMAGE_BYTES + 1)),
                          ("tests/test_x.py", "x")],
                   requirement="os-optics>=0.11")
        self.sdist(extra=[("cases/demo/out/result.json", b"{}")])
        result = check_dist.run(self.dist, "0.1.0", ">=0.12,<0.13")
        problems = " ".join(problem for item in result["distributions"] for problem in item["problems"])
        for fragment in ("run directory", "scene.glb", "big.png", "repository-only", "Requires-Dist",
                         "cases/demo/out/result.json"):
            self.assertIn(fragment, problems)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(check_dist.run(self.dist, "0.2.0", None)["status"], "FAIL")

    def test_missing_distribution_kinds_fail(self):
        self.wheel()
        self.assertEqual(check_dist.run(self.dist, "0.1.0", None)["missing"], ["sdist"])


class DcoTests(unittest.TestCase):
    def commit(self, message, name="Ada Lovelace", email="ada@example.org", parents=("a",)):
        return {"sha": "f" * 40, "parents": list(parents), "author_name": name, "author_email": email,
                "message": message}

    def test_sign_off_must_match_the_author(self):
        signed = "feat: x\n\nSigned-off-by: Ada Lovelace <ADA@example.org>\n"
        self.assertIsNone(check_dco.commit_problem(self.commit(signed)))
        self.assertIn("no Signed-off-by", check_dco.commit_problem(self.commit("feat: x\n")))
        other = "feat: x\n\nSigned-off-by: Someone Else <else@example.org>\n"
        self.assertIn("does not match", check_dco.commit_problem(self.commit(other)))
        self.assertIsNone(check_dco.commit_problem(self.commit("Merge x", parents=("a", "b"))))
        self.assertIsNone(check_dco.commit_problem(self.commit(
            "build(deps): bump", name="dependabot[bot]", email="49699333+dependabot[bot]@users.noreply.github.com")))

    def test_range_in_a_real_repository(self):
        if shutil.which("git") is None:
            self.skipTest("git is not installed")
        with tempfile.TemporaryDirectory() as temporary:
            def git(*args):
                return subprocess.run(["git", "-C", temporary, "-c", "user.name=Ada Lovelace",
                                       "-c", "user.email=ada@example.org", "-c", "commit.gpgsign=false", *args],
                                      capture_output=True, check=True, text=True).stdout.strip()
            git("init", "-q")
            git("commit", "-q", "--allow-empty", "-m", "base")
            base = git("rev-parse", "HEAD")
            git("commit", "-q", "--allow-empty", "-s", "-m", "signed")
            git("commit", "-q", "--allow-empty", "-m", "unsigned")
            head = git("rev-parse", "HEAD")
            commits = check_dco.read_commits(base, head, cwd=temporary)
            self.assertEqual([commit["message"].splitlines()[0] for commit in commits], ["unsigned", "signed"])
            self.assertEqual([bool(check_dco.commit_problem(commit)) for commit in commits], [True, False])


class ChangedCaseTests(unittest.TestCase):
    def test_changed_cases_and_baseline_changes(self):
        existing = {"beam-expander", "ideal-lens-focus"}
        self.assertEqual(changed_cases.select_cases(
            ["cases/beam-expander/CASE.md", "cases/removed/CASE.md", "docs/en/x.md", "cases/README.md"], existing),
            {"all": False, "cases": ["beam-expander"]})
        self.assertEqual(changed_cases.select_cases(["baseline.json"], existing),
                         {"all": True, "cases": ["beam-expander", "ideal-lens-focus"]})
        self.assertEqual(changed_cases.select_cases([], existing), {"all": False, "cases": []})


class EngineReleaseTests(unittest.TestCase):
    def opener(self, documents):
        def open_url(request, timeout):
            self.assertEqual(timeout, 30)
            if request.full_url not in documents:
                raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)
            return io.StringIO(json.dumps(documents[request.full_url]))
        return open_url

    def test_verified_release_gate(self):
        url = engine_release.PYPI + "/0.12.0/json"
        self.assertEqual(engine_release.decide("0.12.0", None, self.opener({url: {}}))["run"], True)
        missing = engine_release.decide("0.12.0", None, self.opener({}))
        self.assertEqual((missing["run"], missing["version"]), (False, "0.12.0"))
        self.assertIn("not on PyPI yet", missing["message"])

    def test_latest_runs_only_when_newer(self):
        latest = engine_release.PYPI + "/json"
        for version, run in (("0.11.0", False), ("0.12.0", False), ("0.12.1", True), ("0.13.0rc1", True)):
            with self.subTest(version=version):
                opener = self.opener({latest: {"info": {"version": version}}})
                result = engine_release.decide("0.12.0", "latest", opener)
                self.assertEqual((result["run"], result["version"]), (run, version))
        with self.assertRaises(ValueError):
            engine_release.decide("0.12.0", "latest", self.opener({latest: {"info": {"version": "x y"}}}))

    def test_outputs_and_summary(self):
        with tempfile.TemporaryDirectory() as temporary:
            output, summary = Path(temporary) / "out", Path(temporary) / "summary"
            with patch.object(engine_release, "decide", return_value={
                    "run": False, "version": "0.12.0", "verified": "0.12.0", "message": "not yet"}), \
                    patch("sys.stdout", io.StringIO()), patch("sys.stderr", io.StringIO()):
                self.assertEqual(engine_release.main(["--github-output", str(output), "--summary", str(summary)]), 0)
            self.assertEqual(output.read_text(), "run=false\nversion=0.12.0\n")
            self.assertEqual(summary.read_text(), "not yet\n")


class WorkflowTests(unittest.TestCase):
    """Text-level guarantees of the workflows; full validation is actionlint's job."""

    def workflows(self):
        return {path.name: path.read_text(encoding="utf-8") for path in (ROOT / ".github" / "workflows").glob("*.yml")}

    def test_expected_workflows_exist(self):
        self.assertTrue({"ci.yml", "scheduled.yml", "release.yml", "codeql.yml"} <= set(self.workflows()))

    def test_actions_are_pinned_by_commit_and_tokens_are_least_privilege(self):
        import re
        for name, text in self.workflows().items():
            with self.subTest(workflow=name):
                self.assertNotIn("pull_request_target", text)
                self.assertRegex(text, r"(?m)^permissions:\n  contents: read$")
                for use in re.findall(r"uses:\s*(\S+)", text):
                    if not use.startswith("./"):
                        self.assertRegex(use, r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
                self.assertEqual(text.count("uses: actions/checkout@"), text.count("persist-credentials: false"))
                self.assertIn("timeout-minutes:", text)

    def test_only_the_scheduled_reporter_writes_issues_and_only_release_gets_an_oidc_token(self):
        workflows = self.workflows()
        self.assertEqual(sum(text.count("issues: write") for text in workflows.values()), 1)
        self.assertIn("issues: write", workflows["scheduled.yml"])
        self.assertIn("github.event_name == 'schedule'", workflows["scheduled.yml"])
        self.assertEqual([name for name, text in workflows.items() if "id-token: write" in text], ["release.yml"])
        self.assertIn("environment:", workflows["release.yml"])


if __name__ == "__main__":
    unittest.main()
