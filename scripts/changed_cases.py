"""Cases to verify for a change: the cases it touches, or all of them when the baseline changes.

python scripts/changed_cases.py BASE HEAD

Every case directory with a changed file is selected (deleted cases are not). A change to baseline.json,
pyproject.toml or the case verifier selects every case. Writes ``all=true|false`` and ``cases=name name ...`` to
$GITHUB_OUTPUT when set, and prints one compact JSON document.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import subprocess

ROOT = Path(__file__).resolve().parents[1]
EVERY_CASE = {"baseline.json", "pyproject.toml", "tools/os_optics_kit/cases.py"}


def select_cases(paths: list[str], existing: set[str]) -> dict:
    if any(path in EVERY_CASE for path in paths):
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("base")
    parser.add_argument("head")
    args = parser.parse_args(argv)
    paths = subprocess.run(["git", "-C", str(ROOT), "diff", "--name-only", f"{args.base}...{args.head}"],
                           capture_output=True, check=True, text=True).stdout.split()
    result = select_cases(paths, existing_cases(ROOT))
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"all={'true' if result['all'] else 'false'}\ncases={' '.join(result['cases'])}\n")
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
