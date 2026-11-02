"""Check the Developer Certificate of Origin sign-off of every commit in a range.

python scripts/check_dco.py BASE HEAD

Every non-merge commit in BASE..HEAD needs a "Signed-off-by: Name <email>" trailer whose email matches the commit
author (``git commit -s`` writes it). Commits by automation accounts (``...[bot]``) are exempt, and so is the
os-optics history imported when this repository was created (it predates the sign-off rule). Prints one compact
JSON document; exit 0 when every commit is signed off, 1 otherwise, 4 when the range cannot be read.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

SIGN_OFF = re.compile(r"^Signed-off-by:\s*(?P<name>.+?)\s*<(?P<email>[^<>\s]+)>\s*$", re.M)
BOT = re.compile(r"\[bot\]$|\[bot\]@users\.noreply\.github\.com$", re.I)
# Tip of the os-optics history imported into this repository; it and its ancestors are not checked.
IMPORTED_HISTORY = ("550896632482327600636151fca80b2444ae08aa",)
FIELD = "\x1f"
RECORD = "\x1e"


def sign_offs(message: str) -> list[tuple[str, str]]:
    return [(match.group("name"), match.group("email")) for match in SIGN_OFF.finditer(message)]


def commit_problem(commit: dict) -> str | None:
    """Why a commit fails the sign-off rule, or None."""
    if len(commit["parents"]) > 1:
        return None
    if BOT.search(commit["author_name"]) or BOT.search(commit["author_email"]):
        return None
    signatures = sign_offs(commit["message"])
    if not signatures:
        return "no Signed-off-by trailer"
    if not any(email.lower() == commit["author_email"].lower() for _, email in signatures):
        found = ", ".join(f"{name} <{email}>" for name, email in signatures)
        return f"Signed-off-by ({found}) does not match the author {commit['author_name']} <{commit['author_email']}>"
    return None


def _present(commit: str, git: str, cwd: str | None) -> bool:
    return subprocess.run([git, "cat-file", "-e", commit + "^{commit}"], capture_output=True, cwd=cwd).returncode == 0


def read_commits(base: str, head: str, git: str = "git", exempt: tuple[str, ...] = IMPORTED_HISTORY,
                 cwd: str | None = None) -> list[dict]:
    excluded = [commit for commit in exempt if _present(commit, git, cwd)]
    output = subprocess.run(
        [git, "log", "--format=%H" + FIELD + "%P" + FIELD + "%an" + FIELD + "%ae" + FIELD + "%B" + RECORD,
         f"{base}..{head}", *(["--not", *excluded] if excluded else [])],
        capture_output=True, check=True, text=True, encoding="utf-8", cwd=cwd).stdout
    commits = []
    for record in output.split(RECORD):
        record = record.strip("\n")
        if not record:
            continue
        sha, parents, name, email, message = record.split(FIELD, 4)
        commits.append({"sha": sha, "parents": parents.split(), "author_name": name, "author_email": email,
                        "message": message})
    return commits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("base", help="Base commit (excluded)")
    parser.add_argument("head", help="Head commit (included)")
    args = parser.parse_args(argv)
    try:
        commits = read_commits(args.base, args.head)
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", None) or str(exc)
        print(json.dumps({"status": "ERROR", "error": f"cannot read {args.base}..{args.head}: {detail.strip()}"}))
        return 4
    failures = [{"sha": commit["sha"], "subject": commit["message"].splitlines()[0] if commit["message"] else "",
                 "problem": problem} for commit in commits if (problem := commit_problem(commit))]
    print(json.dumps({"status": "FAIL" if failures else "PASS", "commits": len(commits), "failures": failures},
                     separators=(",", ":")))
    if failures:
        print("Sign off each commit with `git commit -s`. To add the sign-off to existing commits: "
              f"`git rebase --signoff {args.base}` and force-push the branch.", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
