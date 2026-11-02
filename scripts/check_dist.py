"""Check built os-optics-kit distributions before they are installed or published.

python scripts/check_dist.py dist [--version 0.1.0]

For every wheel and source archive in the directory: the version matches (default: tools/os_optics_kit/_version.py),
the wheel carries the package, the skills, the cases, their indexes, the contributor templates and baseline.json, it declares the os-optics
requirement of baseline.json, and no member is a run output, 3D scene, key, catalog release, file over 1 MiB or
image over 400 KiB. Prints one compact JSON document; exit 0 when clean, 1 otherwise.
"""

from __future__ import annotations

import argparse
from email.parser import Parser
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
import zipfile

try:  # imported as scripts.check_dist (tests) or run as scripts/check_dist.py
    from scripts import check_repo
except ImportError:  # pragma: no cover - exercised by the command line
    import check_repo  # type: ignore[no-redef]

ROOT = Path(__file__).resolve().parents[1]
WHEEL_REQUIRED = ("os_optics_kit/__init__.py", "os_optics_kit/__main__.py", "os_optics_kit/cli.py",
                  "os_optics_kit/_version.py", "os_optics_kit/_resources.py", "os_optics_kit/_data/skills.json",
                  "os_optics_kit/_data/cases.json", "os_optics_kit/_data/baseline.json",
                  "os_optics_kit/_data/templates/CASE.md", "os_optics_kit/_data/templates/README.case.md",
                  "os_optics_kit/_data/templates/SKILL.md")
WHEEL_FORBIDDEN_TOP = ("tests/", "scripts/", ".github/", "docs/", "tools/")
SDIST_REQUIRED = ("pyproject.toml", "LICENSE", "README.md", "baseline.json", "tools/os_optics_kit/cli.py",
                  "templates/CASE.md", "scripts/check_repo.py")


def member_problems(name: str, size: int) -> list[str]:
    path = PurePosixPath(name)
    problems = []
    image = path.suffix.lower() in check_repo.IMAGE_SUFFIXES
    limit = check_repo.MAX_IMAGE_BYTES if image else check_repo.MAX_FILE_BYTES
    if size > limit:
        problems.append(f"{name}: {size} bytes; the limit is {limit} bytes")
    if path.name in check_repo.FORBIDDEN_NAMES or path.suffix.lower() in check_repo.FORBIDDEN_SUFFIXES \
            or check_repo.FORBIDDEN_PATTERN.search(path.name):
        problems.append(f"{name}: generated output, binary package, key or catalog release")
    parts = path.parts
    if "cases" in parts:
        below = parts[parts.index("cases") + 2:-1]
        if any(part == "out" or part.startswith("run") for part in below):
            problems.append(f"{name}: run directory inside a case")
    if "__pycache__" in parts:
        problems.append(f"{name}: compiled cache")
    return problems


def check_wheel(path: Path, version: str, requirement: str | None) -> list[str]:
    problems = []
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        names = {info.filename for info in infos}
        for info in infos:
            problems += member_problems(info.filename, info.file_size)
            if info.filename.startswith(WHEEL_FORBIDDEN_TOP):
                problems.append(f"{info.filename}: repository-only file in the wheel")
        for required in WHEEL_REQUIRED:
            if required not in names:
                problems.append(f"missing {required}")
        if not any(name.startswith("os_optics_kit/_data/skills/") and name.endswith("/SKILL.md") for name in names):
            problems.append("no skills/<name>/SKILL.md under os_optics_kit/_data")
        metadata_name = f"os_optics_kit-{version}.dist-info/METADATA"
        if metadata_name not in names:
            problems.append(f"missing {metadata_name} (wheel version differs from {version}?)")
            return problems
        metadata = Parser().parsestr(archive.read(metadata_name).decode("utf-8"))
    if metadata.get("Name") != "os-optics-kit" or metadata.get("Version") != version:
        problems.append(f"metadata names {metadata.get('Name')} {metadata.get('Version')}, expected os-optics-kit "
                        f"{version}")
    engine = [value[len("os-optics"):] for value in metadata.get_all("Requires-Dist") or []
              if re.match(r"os-optics(?![\w-])", value) and "extra ==" not in value]
    expected = check_repo.specifier_clauses(requirement) if requirement is not None else None
    if expected is not None and [check_repo.specifier_clauses(item) for item in engine] != [expected]:
        problems.append(f"Requires-Dist os-optics {engine} differs from baseline.json {requirement}")
    return problems


def check_sdist(path: Path, version: str) -> list[str]:
    problems = []
    prefix = f"os_optics_kit-{version}/"
    with tarfile.open(path, "r:gz") as archive:
        members = [member for member in archive.getmembers() if member.isfile()]
    names = set()
    for member in members:
        if not member.name.startswith(prefix):
            problems.append(f"{member.name}: outside {prefix} (version differs from {version}?)")
            continue
        name = member.name[len(prefix):]
        names.add(name)
        problems += member_problems(name, member.size)
    for required in SDIST_REQUIRED:
        if required not in names:
            problems.append(f"missing {required}")
    return problems


def run(directory: Path, version: str, requirement: str | None) -> dict:
    files = sorted(directory.glob("*.whl")) + sorted(directory.glob("*.tar.gz"))
    result: dict = {"version": version, "distributions": []}
    for path in files:
        try:
            problems = check_wheel(path, version, requirement) if path.suffix == ".whl" else check_sdist(path, version)
        except (OSError, zipfile.BadZipFile, tarfile.TarError, KeyError, UnicodeDecodeError) as exc:
            problems = [f"unreadable: {exc}"]
        result["distributions"].append({"file": path.name, "problems": problems})
    kinds = {path.suffix for path in files}
    missing = [kind for kind, suffix in (("wheel", ".whl"), ("sdist", ".gz")) if suffix not in kinds]
    result["missing"] = missing
    failed = missing or any(item["problems"] for item in result["distributions"])
    result["status"] = "FAIL" if failed else "PASS"
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("directory", type=Path, help="Directory with the built wheel and source archive")
    parser.add_argument("--version", help="Expected version (default: tools/os_optics_kit/_version.py)")
    args = parser.parse_args(argv)
    version = args.version
    if version is None:
        text = (ROOT / "tools" / "os_optics_kit" / "_version.py").read_text(encoding="utf-8")
        match = re.search(r"^__version__ = \"([^\"]+)\"", text, re.M)
        version = match.group(1) if match else ""
    try:
        requirement = json.loads((ROOT / "baseline.json").read_text(encoding="utf-8"))["engine"]["requirement"]
    except (OSError, ValueError, KeyError, TypeError):
        requirement = None
    result = run(args.directory, version, requirement)
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
