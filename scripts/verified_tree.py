"""Verify every case once per tree: record a full verification, find it again, summarize the Verify cases step.

python scripts/verified_tree.py record --verify run-alignment/cases-verify.json --report run-alignment/report.json \
    --tree TREE --commit SHA --event EVENT --run-id ID --out run-alignment/verified-tree/verified-tree.json
python scripts/verified_tree.py lookup --tree TREE --repository OWNER/NAME --repository-id ID
python scripts/verified_tree.py summary --selection all --verify run-alignment/cases-verify.json --exit 0

``record`` writes a small JSON record when a `cases verify` document covers every case of the checked-out tree and
every case is MATCH; CI uploads it as the artifact ``cases-verified-TREE``. ``lookup`` (push and merge queue) asks
the GitHub REST API for that artifact (token in GITHUB_TOKEN) and accepts it only when it is not expired, was made by
a run of this repository (not a fork), and its record names this tree, every case MATCH, and the engine version and
catalog SHA-256 of baseline.json. Anything else, including any API or download error, means: verify. Writes step
outputs to $GITHUB_OUTPUT and summary lines to $GITHUB_STEP_SUMMARY when set; prints one compact JSON document.
``summary`` prints one line and, unless the step was skipped, exits non-zero when the verification failed: with the
``--exit`` code when that is not 0, and with 1 when it is 0 but the ``--verify`` document is missing or its status is
not MATCH. Standard library only.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import re
from typing import Callable
import urllib.error
from urllib.parse import quote, urlparse
import urllib.request
import zipfile

try:
    from scripts.changed_cases import existing_cases
except ImportError:  # run as a script: scripts/ is on sys.path
    from changed_cases import existing_cases  # type: ignore[no-redef]

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "os-optics-kit.cases-verified"
ARTIFACT_PREFIX = "cases-verified-"
RECORD_FILE = "verified-tree.json"
EVENTS = ("pull_request", "push", "merge_group")
TREE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
MAX_ARCHIVE = 1 << 20
MAX_RECORD = 64 << 10
API_VERSION = "2022-11-28"

# fetch(url, headers) -> (status, lower-case headers, body); never follows redirects.
Fetch = Callable[[str, dict], tuple]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # the caller follows a redirect itself, without the token


def http_fetch(url: str, headers: dict) -> tuple:
    request = urllib.request.Request(url, headers={"User-Agent": "os-optics-kit-ci", **headers})
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=30) as response:
            return response.status, {k.lower(): v for k, v in response.headers.items()}, \
                response.read(MAX_ARCHIVE + 1)
    except urllib.error.HTTPError as exc:
        return exc.code, {k.lower(): v for k, v in (exc.headers or {}).items()}, b""


def artifact_name(tree: str) -> str:
    return ARTIFACT_PREFIX + tree


def read_baseline(root: Path = ROOT) -> dict:
    baseline = json.loads((root / "baseline.json").read_text(encoding="utf-8"))
    return {"engine": baseline["engine"]["verified_version"], "catalog_version": baseline["catalog"]["version"],
            "catalog_sha256": baseline["catalog"]["sha256"]}


# ----------------------------------------------------------------------------------------------- record

def make_record(document: dict, report: dict, *, tree: str, commit: str, event: str, run_id: int,
                cases: set[str]) -> dict:
    """The record of a full, all-MATCH verification of ``cases``; ValueError says why there is none."""
    if not TREE.fullmatch(tree) or not TREE.fullmatch(commit):
        raise ValueError("tree and commit must be Git object names")
    if event not in EVENTS:
        raise ValueError(f"unknown event {event!r}")
    results = document.get("cases")
    if not isinstance(results, list):
        raise ValueError("the verify document has no case list")
    names = [entry.get("name") for entry in results if isinstance(entry, dict)]
    if len(names) != len(results) or len(set(names)) != len(names) or set(names) != cases:
        raise ValueError("the verify document does not cover exactly the cases of this tree")
    if document.get("status") != "MATCH" or any(entry.get("result") != "MATCH" for entry in results):
        raise ValueError("not every case is MATCH")
    catalog = report.get("catalog_source") or {}
    engine, version, sha256 = report.get("installed_version"), catalog.get("catalog_version"), \
        catalog.get("active_sha256")
    if not (isinstance(engine, str) and isinstance(version, str) and isinstance(sha256, str)):
        raise ValueError("the alignment report names no installed engine or active catalog")
    return {"schema": SCHEMA, "schema_version": 1, "tree": tree, "commit": commit, "event": event,
            "run_id": int(run_id), "engine": engine, "catalog": {"version": version, "sha256": sha256},
            "counts": {"cases": len(names), "MATCH": len(names), "MISMATCH": 0, "ERROR": 0}}


# ----------------------------------------------------------------------------------------------- lookup

def _time(value) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def artifact_problem(artifact: dict, *, name: str, repository_id: int, now: datetime) -> str | None:
    """Why an artifact listed by the API cannot be used, or None."""
    if artifact.get("name") != name:
        return "different name"
    if not isinstance(artifact.get("id"), int):
        return "no artifact id"
    expires = _time(artifact.get("expires_at"))
    if artifact.get("expired") is not False or expires is None or expires <= now:
        return "expired"
    run = artifact.get("workflow_run") or {}
    if run.get("repository_id") != repository_id or run.get("head_repository_id") != repository_id:
        return "made by a run of another repository (fork)"
    if not isinstance(run.get("id"), int):
        return "no workflow run id"
    return None


def record_problem(record, *, tree: str, run_id: int, baseline: dict, cases: set[str]) -> str | None:
    """Why a downloaded record does not show this tree fully verified, or None."""
    if not isinstance(record, dict) or record.get("schema") != SCHEMA or record.get("schema_version") != 1:
        return "not a verified-tree record"
    if record.get("tree") != tree:
        return f"record is for tree {record.get('tree')}"
    if record.get("run_id") != run_id:
        return "record and artifact name different runs"
    counts = record.get("counts") or {}
    if not (counts.get("cases") == counts.get("MATCH") == len(cases) and len(cases) > 0
            and counts.get("MISMATCH") == 0 and counts.get("ERROR") == 0):
        return "counts do not show every case MATCH"
    if record.get("engine") != baseline["engine"]:
        return f"verified with engine {record.get('engine')}, baseline.json names {baseline['engine']}"
    catalog = record.get("catalog") or {}
    if catalog.get("sha256") != baseline["catalog_sha256"] or catalog.get("version") != baseline["catalog_version"]:
        return "verified with a catalog other than baseline.json's"
    return None


def _download(fetch: Fetch, api: str, repository: str, artifact_id: int, token: str) -> dict:
    url = f"{api}/repos/{repository}/actions/artifacts/{artifact_id}/zip"
    status, headers, body = fetch(url, {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                        "X-GitHub-Api-Version": API_VERSION})
    if status in (301, 302, 303, 307, 308):
        location = headers.get("location", "")
        if urlparse(location).scheme != "https":
            raise ValueError("artifact download redirect is not https")
        status, headers, body = fetch(location, {})  # signed storage URL: never send the token there
    if status != 200:
        raise ValueError(f"artifact download returned HTTP {status}")
    if len(body) > MAX_ARCHIVE:
        raise ValueError("artifact archive is too large")
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        info = archive.getinfo(RECORD_FILE)
        if info.file_size > MAX_RECORD:
            raise ValueError("record is too large")
        return json.loads(archive.read(info).decode("utf-8"))


def lookup(*, tree: str, repository: str, repository_id: int, token: str | None, baseline: dict, cases: set[str],
           fetch: Fetch = http_fetch, api: str = "https://api.github.com", server: str = "https://github.com",
           now: datetime | None = None) -> dict:
    """Whether this tree was already verified: {"verified", "reason", "run_url"}. Any doubt means verify."""
    result: dict = {"verified": False, "tree": tree, "reason": None, "run_url": None, "rejected": []}
    if not TREE.fullmatch(tree) or not re.fullmatch(r"[\w.-]+/[\w.-]+", repository):
        result["reason"] = "invalid tree or repository"
        return result
    if not token:
        result["reason"] = "no token to read artifacts"
        return result
    now = now or datetime.now(timezone.utc)
    name = artifact_name(tree)
    try:
        status, _headers, body = fetch(
            f"{api}/repos/{repository}/actions/artifacts?name={quote(name)}&per_page=100",
            {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
             "X-GitHub-Api-Version": API_VERSION})
        if status != 200:
            raise ValueError(f"artifact list returned HTTP {status}")
        artifacts = json.loads(body.decode("utf-8")).get("artifacts")
        if not isinstance(artifacts, list):
            raise ValueError("artifact list has no artifacts")
        artifacts = sorted((a for a in artifacts if isinstance(a, dict)),
                           key=lambda a: str(a.get("created_at") or ""), reverse=True)
        for artifact in artifacts:
            problem = artifact_problem(artifact, name=name, repository_id=repository_id, now=now)
            if problem is None:
                run_id = artifact["workflow_run"]["id"]
                record = _download(fetch, api, repository, artifact["id"], token)
                problem = record_problem(record, tree=tree, run_id=run_id, baseline=baseline, cases=cases)
                if problem is None:
                    result.update(verified=True, reason=None,
                                  run_url=f"{server}/{repository}/actions/runs/{run_id}")
                    return result
            result["rejected"].append({"id": artifact.get("id"), "problem": problem})
    except (OSError, ValueError, KeyError, TypeError, AttributeError, zipfile.BadZipFile) as exc:
        result.update(reason=f"could not read the verification record ({type(exc).__name__}: {exc})")
        return result
    result["reason"] = "no usable verification of this tree" if result["rejected"] else \
        "no verification of this tree"
    return result


# ----------------------------------------------------------------------------------------------- summary

def summary_line(*, selection: str, document: dict | None = None, exit_code: int | None = None,
                 skipped: str | None = None, run_url: str | None = None) -> str:
    scope = "every case" if selection == "all" else "the cases the pull request changes"
    if skipped == "verified":
        return f"Verify cases: skipped, this tree was already verified with every case MATCH by {run_url}."
    if skipped == "none-changed":
        return "Verify cases: skipped, the pull request changes no case."
    counts = (document or {}).get("counts") if isinstance(document, dict) else None
    if not isinstance(counts, dict):
        return f"Verify cases ({scope}): no result document (exit {exit_code}); see the step log."
    total = len((document or {}).get("cases") or [])
    line = (f"Verify cases ({scope}): {total} cases, MATCH {counts.get('MATCH', 0)}, "
            f"MISMATCH {counts.get('MISMATCH', 0)}, ERROR {counts.get('ERROR', 0)}.")
    status = (document or {}).get("status")
    if exit_code == 0 and status != "MATCH":
        line += f" The result document's status is {status!r}, not 'MATCH', although the command exited 0."
    return line


def summary_exit(*, document: dict | None = None, exit_code: int | None = None, skipped: str | None = None) -> int:
    """The exit code of the summary command: 0 for a skipped step; otherwise the command's exit code when it is not
    0, and 1 when it is 0 (or not given) but the result document is missing or its status is not MATCH, so an exit
    code that hides a failed verification cannot pass the step."""
    if skipped:
        return 0
    if exit_code:
        return exit_code
    if not isinstance(document, dict) or document.get("status") != "MATCH":
        return 1
    return 0


# ----------------------------------------------------------------------------------------------- CLI

def _append(variable: str, text: str) -> None:
    path = os.environ.get(variable)
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record", help="write the record of a full all-MATCH verification")
    rec.add_argument("--verify", type=Path, required=True)
    rec.add_argument("--report", type=Path, required=True)
    rec.add_argument("--tree", required=True)
    rec.add_argument("--commit", required=True)
    rec.add_argument("--event", required=True, choices=EVENTS)
    rec.add_argument("--run-id", type=int, required=True)
    rec.add_argument("--out", type=Path, required=True)
    look = sub.add_parser("lookup", help="find an accepted record of this tree")
    look.add_argument("--tree", required=True)
    look.add_argument("--repository", required=True)
    look.add_argument("--repository-id", type=int, required=True)
    summ = sub.add_parser("summary", help="one summary line for the Verify cases step")
    summ.add_argument("--selection", choices=("all", "changed"), required=True)
    summ.add_argument("--verify", type=Path)
    summ.add_argument("--exit", dest="exit_code", type=int)
    summ.add_argument("--skipped", choices=("verified", "none-changed"))
    summ.add_argument("--run-url")
    args = parser.parse_args(argv)

    if args.command == "record":
        try:
            record = make_record(_read_json(args.verify) or {}, _read_json(args.report) or {}, tree=args.tree,
                                 commit=args.commit, event=args.event, run_id=args.run_id,
                                 cases=existing_cases(ROOT))
        except ValueError as exc:
            _append("GITHUB_OUTPUT", "recorded=false\n")
            print(json.dumps({"recorded": False, "reason": str(exc)}, separators=(",", ":")))
            return 0
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        _append("GITHUB_OUTPUT", f"recorded=true\nname={artifact_name(args.tree)}\n")
        print(json.dumps({"recorded": True, "name": artifact_name(args.tree), "record": record},
                         separators=(",", ":")))
        return 0

    if args.command == "lookup":
        try:
            baseline = read_baseline()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            result = {"verified": False, "reason": f"baseline.json unreadable: {exc}", "run_url": None}
        else:
            result = lookup(tree=args.tree, repository=args.repository, repository_id=args.repository_id,
                            token=os.environ.get("GITHUB_TOKEN"), baseline=baseline, cases=existing_cases(ROOT),
                            api=os.environ.get("GITHUB_API_URL", "https://api.github.com"),
                            server=os.environ.get("GITHUB_SERVER_URL", "https://github.com"))
        _append("GITHUB_OUTPUT", f"verified={'true' if result['verified'] else 'false'}\n"
                                 f"run_url={result['run_url'] or ''}\n")
        print(json.dumps(result, separators=(",", ":")))
        return 0

    document = _read_json(args.verify) if args.verify else None
    line = summary_line(selection=args.selection, document=document, exit_code=args.exit_code,
                        skipped=args.skipped, run_url=args.run_url)
    _append("GITHUB_STEP_SUMMARY", line + "\n")
    print(line)
    return summary_exit(document=document, exit_code=args.exit_code, skipped=args.skipped)


if __name__ == "__main__":
    raise SystemExit(main())
