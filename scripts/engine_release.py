"""Decide whether engine-dependent CI can run: is the needed os-optics release on PyPI?

python scripts/engine_release.py                    # the verified engine of baseline.json
python scripts/engine_release.py --candidate latest # the newest release, if newer than the verified one
python scripts/engine_release.py --candidate 0.13.0

Writes ``run=true|false`` and ``version=X`` to $GITHUB_OUTPUT (or --github-output) and one line to the job summary
when nothing runs. Prints one compact JSON document. Exit 0 whether or not the release exists; 1 when PyPI cannot
be read.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
VERSION = re.compile(r"\d+(?:\.\d+)+(?:a\d+|b\d+|rc\d+|\.post\d+|\.dev\d+)?")
PYPI = "https://pypi.org/pypi/os-optics"


def fetch(url: str, opener=urllib.request.urlopen) -> dict | None:
    """PyPI JSON for a URL, or None for 404."""
    request = urllib.request.Request(url, headers={"User-Agent": "os-optics-kit-ci"})
    try:
        with opener(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def version_key(version: str) -> tuple:
    try:
        from packaging.version import Version
        return (Version(version),)
    except ImportError:
        release = re.match(r"\d+(?:\.\d+)*", version)
        parts = [int(part) for part in release.group(0).split(".")] if release else []
        while parts and parts[-1] == 0:
            parts.pop()
        # Pre-releases and development releases sort before their final release.
        return (tuple(parts), 0 if re.search(r"(a|b|rc|\.dev)\d+$", version) else 1)


def decide(verified: str, candidate: str | None, opener=urllib.request.urlopen) -> dict:
    if candidate is None:
        published = fetch(f"{PYPI}/{verified}/json", opener) is not None
        message = None if published else (
            f"Engine checks did not run: os-optics {verified} (engine.verified_version in baseline.json) is not "
            "on PyPI yet. They run once it is published.")
        return {"run": published, "version": verified, "verified": verified, "message": message}
    if candidate == "latest":
        document = fetch(f"{PYPI}/json", opener)
        latest = (document or {}).get("info", {}).get("version")
        if not isinstance(latest, str) or not VERSION.fullmatch(latest):
            raise ValueError(f"unexpected latest version {latest!r}")
        newer = version_key(latest) > version_key(verified)
        message = None if newer else (
            f"No candidate check: the latest os-optics on PyPI is {latest}, not newer than the verified {verified}.")
        return {"run": newer, "version": latest, "verified": verified, "message": message}
    if not VERSION.fullmatch(candidate):
        raise ValueError(f"expected an exact version or latest, got {candidate!r}")
    published = fetch(f"{PYPI}/{candidate}/json", opener) is not None
    message = None if published else f"No candidate check: os-optics {candidate} is not on PyPI."
    return {"run": published, "version": candidate, "verified": verified, "message": message}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--candidate", help="latest or an exact version instead of the verified engine")
    parser.add_argument("--github-output", type=Path, default=os.environ.get("GITHUB_OUTPUT") or None)
    parser.add_argument("--summary", type=Path, default=os.environ.get("GITHUB_STEP_SUMMARY") or None)
    args = parser.parse_args(argv)
    try:
        verified = json.loads((ROOT / "baseline.json").read_text(encoding="utf-8"))["engine"]["verified_version"]
        result = decide(verified, args.candidate or None)
    except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError) as exc:
        print(json.dumps({"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as output:
            output.write(f"run={'true' if result['run'] else 'false'}\nversion={result['version']}\n")
    if result["message"]:
        print(result["message"], file=sys.stderr)
        if args.summary:
            with args.summary.open("a", encoding="utf-8") as summary:
                summary.write(result["message"] + "\n")
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
