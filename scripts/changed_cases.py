"""Cases CI verifies: every case, or on a pull request the cases it changes.

python scripts/changed_cases.py --event pull_request BASE HEAD
python scripts/changed_cases.py --event push

A push and a merge-queue run select every case. A pull request selects every case directory with a file changed by
``git diff BASE...HEAD`` (a deleted case is not selected), or every case when it changes a file that ``cases verify``
or this selection depends on (EVERY_CASE). Writes ``all=true|false``, ``cases=name name ...`` and ``tree=SHA`` (the
checked-out tree) to $GITHUB_OUTPUT when set, and prints one compact JSON document.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import subprocess

ROOT = Path(__file__).resolve().parents[1]
# A change to any of these files can change the result of `cases verify` for every case, or which cases CI verifies:
# the selection and the step summary, the environment and catalog store check_alignment.py builds for the step
# (with the default requirements), the command path `python -m os_optics_kit cases verify` takes to its exit code
# (__main__.py, __init__.py, _version.py, cli.py) and the modules cases.py uses.
EVERY_CASE = frozenset({
    "baseline.json",
    "pyproject.toml",
    "alignment-checks.json",
    ".github/workflows/ci.yml",
    "scripts/changed_cases.py",
    "scripts/verified_tree.py",
    "scripts/check_alignment.py",
    "scripts/requirements-alignment.txt",
    "tools/os_optics_kit/__init__.py",
    "tools/os_optics_kit/__main__.py",
    "tools/os_optics_kit/_version.py",
    "tools/os_optics_kit/cli.py",
    "tools/os_optics_kit/cases.py",
    "tools/os_optics_kit/case_taxonomy.py",
    "tools/os_optics_kit/_engine.py",
    "tools/os_optics_kit/_frontmatter.py",
    "tools/os_optics_kit/_resources.py",
})
EVENTS = ("pull_request", "push", "merge_group")


def select_cases(paths: list[str], existing: set[str], *, event: str = "pull_request") -> dict:
    """The cases to verify for ``event``, given the changed ``paths`` (pull requests only) and the existing cases."""
    if event not in EVENTS:
        raise ValueError(f"unknown event {event!r}; expected one of {', '.join(EVENTS)}")
    if event != "pull_request" or any(path in EVERY_CASE for path in paths):
        return {"all": True, "cases": sorted(existing)}
    names = set()
    for path in paths:
        parts = PurePosixPath(path).parts
        if len(parts) >= 3 and parts[0] == "cases" and parts[1] in existing:
            names.add(parts[1])
    return {"all": False, "cases": sorted(names)}


def existing_cases(root: Path) -> set[str]:
    cases = root / "cases"
    return {path.name for path in cases.iterdir() if path.is_dir() and (path / "CASE.md").is_file()} \
        if cases.is_dir() else set()


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, check=True, text=True).stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--event", choices=EVENTS, default="pull_request")
    parser.add_argument("base", nargs="?", help="pull request base commit")
    parser.add_argument("head", nargs="?", help="pull request head commit")
    args = parser.parse_args(argv)
    paths: list[str] = []
    if args.event == "pull_request":
        if not (args.base and args.head):
            parser.error("a pull request needs BASE and HEAD")
        # --no-renames: a file moved out of a case also selects the case it left.
        paths = [path for path in _git("diff", "--name-only", "--no-renames", "-z",
                                       f"{args.base}...{args.head}").split("\0") if path]
    result = select_cases(paths, existing_cases(ROOT), event=args.event)
    result["tree"] = _git("rev-parse", "HEAD^{tree}").strip()
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"all={'true' if result['all'] else 'false'}\ncases={' '.join(result['cases'])}\n"
                         f"tree={result['tree']}\n")
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
