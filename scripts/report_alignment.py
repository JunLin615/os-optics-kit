"""Render alignment results and optionally report a new scheduled observation.

Reading and rendering are offline. GitHub is contacted only with --publish;
the workflow grants that capability exclusively to its scheduled reporter job.
"""

import argparse
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shlex
import sys
from urllib import request


def read_report(path):
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise ValueError("Expected an alignment report object")
    if report.get("schema_version") != 1:
        raise ValueError("Expected alignment report schema_version 1")
    if report.get("status") not in {"PASS", "FAIL"}:
        raise ValueError("Expected alignment report status PASS or FAIL")
    if not isinstance(report.get("checks"), list):
        raise ValueError("Expected alignment report checks list")
    for check in report["checks"]:
        if not isinstance(check, dict):
            raise ValueError("Expected a check object")
        if check.get("status") not in {"PASS", "FAIL", "SKIPPED"}:
            raise ValueError("Unexpected check status")
        for field in ("name", "category"):
            if not isinstance(check.get(field), str):
                raise ValueError("Expected check " + field)
    # A malformed/missing baseline itself produces a useful setup failure report.
    if "baseline_version" not in report:
        raise ValueError("Expected alignment report baseline_version")
    for field in ("baseline_version", "installed_version", "candidate_version"):
        if report.get(field) is not None and not isinstance(report[field], str):
            raise ValueError("Expected optional version string: " + field)
    if not isinstance(report.get("requested_version", "baseline"), str):
        raise ValueError("Expected requested_version string")
    for field in ("catalog_source", "optional_checks", "engine_source"):
        if report.get(field) is not None and not isinstance(report[field], dict):
            raise ValueError("Expected optional object: " + field)
    return report


def observed_version(report):
    return report.get("installed_version") or report.get("candidate_version")


def failed_checks(report):
    return [check for check in report["checks"] if check["status"] == "FAIL"]


def observation_marker(report):
    """Ignore changing paths, timestamps and error wording when deduplicating."""
    failures = sorted({(check["name"], check["category"])
                       for check in failed_checks(report)})
    observation = {
        "version": observed_version(report) or "unresolved:" + report.get(
            "requested_version", "baseline"),
        "status": report["status"],
        "failures": failures,
    }
    digest = hashlib.sha256(json.dumps(
        observation, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    return "<!-- opensci-engine-alignment:" + digest + " -->"


def needs_issue(report):
    if (report.get("engine_source") or {}).get("mode") == "local-wheel":
        return False
    version = observed_version(report)
    return (report["status"] == "FAIL" or bool(failed_checks(report))
            or bool(version and version != report["baseline_version"]))


def code_block(value):
    text = str(value)
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return fence + "text\n" + text + "\n" + fence


def cell(value):
    if value is None:
        return "not recorded"
    return html.escape(str(value)).replace("|", "&#124;").replace("\n", "<br>")


def catalog_text(source):
    """Name the catalog source: an imported file is not a fresh download."""
    if not source:
        return None
    release = source.get("catalog_version") or source.get("file_catalog_version")
    if source.get("mode") == "file":
        text = "imported file " + str(source.get("file"))
        if source.get("sha256"):
            text += " (sha256 " + source["sha256"] + ")"
    else:
        text = "fresh sync"
        if source.get("service_url"):
            text += " from " + source["service_url"]
    return text + (", release " + release if release else ", no active release")


def render_summary(report, run_url="", artifact_url=""):
    lines = [
        "## Engine alignment: " + report["status"],
        "",
        "| Version | Value |",
        "| --- | --- |",
        "| Verified baseline | " + cell(report["baseline_version"]) + " |",
        "| Requested | " + cell(report.get("requested_version")) + " |",
        "| Resolved candidate | " + cell(report.get("candidate_version")) + " |",
        "| Installed engine | " + cell(report.get("installed_version")) + " |",
        "| Python | " + cell(report.get("python_version")) + " |",
        "| Engine source | " + cell((report.get("engine_source") or {}).get("mode", "pypi")) + " |",
        "| Engine artifact SHA-256 | " + cell((report.get("engine_source") or {}).get("sha256")) + " |",
        "| Catalog | " + cell(catalog_text(report.get("catalog_source"))) + " |",
        "",
    ]
    if (report.get("engine_source") or {}).get("mode") == "local-wheel":
        lines += ["Local-wheel preparation result. Published-package alignment requires a fresh "
                  "PyPI installation and a new check report.", ""]
    if run_url:
        lines += ["[Actions run](" + run_url + ")", ""]
    if artifact_url:
        lines += ["[Report, logs and workflow artifacts](" + artifact_url + ")", ""]
    elif run_url:
        lines += ["Artifacts are available in the Actions run when upload succeeds.", ""]
    if observed_version(report) != report["baseline_version"]:
        lines += [
            "The verified baseline has not changed. A passing candidate still needs "
            "review before becoming the baseline.", "",
        ]
    lines += [
        "| Check | Category | Status | Exit code |",
        "| --- | --- | --- | --- |",
    ]
    for check in report["checks"]:
        status = check["status"]
        if status == "SKIPPED" and check.get("blocked_by"):
            status += " (blocked by " + check["blocked_by"] + ")"
        elif status == "SKIPPED" and check.get("requires_python"):
            status += " (requires Python >= " + check["requires_python"] + ")"
        lines.append("| " + " | ".join(cell(value) for value in (
            check["name"], check["category"], status, check.get("exit_code"))) + " |")
    optional = (report.get("optional_checks") or {}).get("not_selected") or []
    if optional:
        lines += ["", "Optional checks not selected (`--optional NAME` or `--optional all`): "
                  + cell(", ".join(optional))]

    for check in failed_checks(report):
        lines += ["", "### Failed check: " + cell(check["name"]), ""]
        command = check.get("command") or []
        if command:
            lines += ["Command:", "", code_block(shlex.join(command)), ""]
        error = check.get("error") or "Process returned exit code " + str(
            check.get("exit_code"))
        lines += ["Error:", "", code_block(error), ""]
        for stream in ("stdout", "stderr"):
            if check.get(stream):
                lines.append(stream + " log in artifact: " + cell(check[stream]))

    reproduction = ["python", "scripts/check_alignment.py", "--out", "run-alignment"]
    version = observed_version(report) or report.get("requested_version", "baseline")
    if version != "baseline":
        reproduction += ["--candidate", version]
    engine = report.get("engine_source") or {}
    if engine.get("mode") == "local-wheel":
        reproduction += ["--candidate-wheel", engine.get("filename", "CANDIDATE.whl"),
                         "--candidate-sha256", engine.get("sha256", "SHA256")]
    source = report.get("catalog_source") or {}
    if source.get("mode") == "file" and source.get("file"):
        reproduction += ["--catalog-file", source["file"]]
        if source.get("expected_sha256"):
            reproduction += ["--catalog-sha256", source["expected_sha256"]]
    for name in (report.get("optional_checks") or {}).get("requested") or []:
        reproduction += ["--optional", name]
    lines += ["", "### Reproduce", "", code_block(shlex.join(reproduction)), ""]
    return "\n".join(lines)


class GitHubIssues:
    """Small REST client; scans closed issues too, without comments or reopenings."""

    def __init__(self, repository, token, api_url="https://api.github.com", opener=None):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("Expected GitHub repository owner/name")
        self.url = api_url.rstrip("/") + "/repos/" + repository + "/issues"
        self.token = token
        self.opener = opener or request.urlopen

    def send(self, url, body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + self.token,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "os-optics-alignment",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = request.Request(url, data=data, headers=headers)
        with self.opener(req, timeout=30) as response:
            return json.load(response)

    def find(self, marker):
        page = 1
        while True:
            issues = self.send(self.url + "?state=all&per_page=100&page=" + str(page))
            for issue in issues:
                if "pull_request" not in issue and marker in (issue.get("body") or ""):
                    return issue
            if len(issues) < 100:
                return None
            page += 1

    def create(self, title, body):
        return self.send(self.url, {"title": title, "body": body})


def publish_report(report, github, run_url="", artifact_url=""):
    if not needs_issue(report):
        return {"action": "none", "url": None}
    marker = observation_marker(report)
    existing = github.find(marker)
    if existing:
        return {"action": "existing", "url": existing["html_url"]}
    version = observed_version(report) or "unresolved " + report.get(
        "requested_version", "baseline")
    finding = "checks failed" if report["status"] == "FAIL" else "new unverified version"
    title = "[engine alignment] " + version + ": " + finding
    body = "\n\n".join([
        marker,
        "Scheduled compatibility observation. Baseline updates require a reviewed "
        "pull request; this report does not gate engine releases.",
        render_summary(report, run_url, artifact_url),
    ])
    created = github.create(title, body)
    return {"action": "created", "url": created["html_url"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--summary", type=Path, help="Append Markdown to this file")
    parser.add_argument("--run-url", default="")
    parser.add_argument("--artifact-url", default="")
    parser.add_argument("--publish", action="store_true", help="Create a novel observation issue")
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    args = parser.parse_args(argv)
    try:
        report = read_report(args.report)
        summary = render_summary(report, args.run_url, args.artifact_url)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        summary = "## Engine alignment report unavailable\n\n" + code_block(exc) + "\n"
        if args.run_url:
            summary += "\n[Inspect the Actions run](" + args.run_url + ")\n"
        if args.summary:
            with args.summary.open("a", encoding="utf-8") as target:
                target.write(summary)
        print(summary, file=sys.stderr)
        return 1
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as target:
            target.write(summary)
    else:
        print(summary)
    if args.publish:
        if (report.get("engine_source") or {}).get("mode") == "local-wheel":
            print("Local-wheel reports are preliminary; publish observations from a PyPI check.", file=sys.stderr)
            return 1
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if not token:
            print("Publishing requires GH_TOKEN or GITHUB_TOKEN", file=sys.stderr)
            return 1
        try:
            github = GitHubIssues(args.repository, token, os.environ.get(
                "GITHUB_API_URL", "https://api.github.com"))
            print(json.dumps(publish_report(report, github, args.run_url, args.artifact_url)))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print("Could not publish alignment report: " + str(exc), file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
