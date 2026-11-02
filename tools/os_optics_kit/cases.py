"""``os-optics-kit cases``: list, show, copy, verify and index the engine-reproducible cases.

A case is a directory ``cases/<name>/`` holding ``project.json`` (an engine-written ProjectDocumentV2 with its
targets), ``CASE.md`` (flat front matter and fixed sections), ``README.md`` with engine-rendered images and
``expected.json`` (the verified run status and target assessments), plus optional case scripts, study specs and data.
``cases.json`` indexes them. The cases ship inside the wheel, so every subcommand works without a clone.

Subcommands (compact JSON on stdout, except ``show``):

  list [--tag TAG]                 the case index
  show NAME                        the case's CASE.md
  copy NAME --dest DIR [--force]   copy the case to DIR/NAME
  verify NAME... | --all [--out DIR] [--catalog FILE] [--update-expected]
                                   project check -> project run -> results targets -> compare with expected.json
  index --write | --check          generate or validate cases.json and every case's format

Exit codes: 0 success / every case matches / index valid; 1 a case differs from expected.json or the index or a case
fails its format checks; 4 usage error, unknown case or no engine for ``verify``.

``list``, ``show``, ``copy`` and ``index`` need only the standard library; ``verify`` runs the installed engine
(``python -m os_optics`` of this interpreter, else ``os-optics`` on PATH).
"""

from __future__ import annotations

import argparse
from importlib.util import find_spec
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from ._resources import data_root

INDEX_SCHEMA = "os-optics-kit.cases"
EXPECTED_SCHEMA = "os-optics-kit.case-expected"
SCHEMA_VERSION = 1
FRONT_MATTER_KEYS = ("name", "title", "summary", "engine", "catalog", "project", "tools", "tags", "license")
SECTIONS = ("What it shows", "Run", "Targets", "Baseline", "Files")
REQUIRED_FILES = ("project.json", "CASE.md", "README.md", "expected.json")
ASSESSMENTS = ("MET", "UNMET", "INDETERMINATE", "NOT_APPLICABLE")
RUN_STATUSES = ("PASS", "WARNING", "FAIL", "UNKNOWN", "OUT_OF_MODEL_SCOPE", "NOT_APPLICABLE")
CASE_TOKEN_BUDGET = 700
SUMMARY_MAX_CHARS = 300
IMAGE_MAX_BYTES = 400 * 1024
MAX_IMAGES = 4
FILE_MAX_BYTES = 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SKIPPED_NAMES = {"__pycache__", ".pytest_cache", ".DS_Store"}
SKIPPED_SUFFIXES = (".pyc", ".pyo")
# Machine-local absolute paths: a Windows drive path, a UNC path or a POSIX home / temporary directory.
LOCAL_PATH = re.compile(r"(?<![A-Za-z])[A-Za-z]:\\[A-Za-z0-9_.$-]|(?<![A-Za-z:/])[A-Za-z]:/[A-Za-z0-9_.$-]|"
                        r"\\\\[A-Za-z0-9_.$-]+\\[A-Za-z0-9_.$-]|(?<![\w.~-])/(?:home|Users|root|mnt|tmp|private)/")
TEXT_SUFFIXES = (".md", ".json", ".py", ".txt", ".csv", ".toml", ".cfg", ".yml", ".yaml", ".svg")
EXIT_OK, EXIT_DIFFERENT, EXIT_USAGE = 0, 1, 4


class CaseError(Exception):
    """A refused request (exit 4) with a stable code."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise CaseError("BAD_ARGUMENT", message)


def _write(text):
    """UTF-8 on stdout whatever the console code page is (CASE.md holds units such as µm and °)."""
    stream = getattr(sys.stdout, "buffer", None)
    if stream is None:
        sys.stdout.write(text)
    else:
        sys.stdout.flush()
        stream.write(text.encode("utf-8"))
        stream.flush()


def _emit(document):
    _write(json.dumps(document, separators=(",", ":"), ensure_ascii=False) + "\n")


def token_estimate(text):
    """The documented budget approximation: ceil(characters / 4)."""
    return math.ceil(len(text) / 4)


def normalized_json(document):
    """The normalized form of project.json / expected.json / cases.json bytes (LF line ends)."""
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


# ----------------------------------------------------------------------------------------------- front matter

def parse_front_matter(text):
    """``---`` / flat ``key: value`` lines / ``---`` -> (dict, body). No nesting, no lists, no YAML library."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise ValueError("the file must start with a '---' front-matter line")
    meta = {}
    for number, line in enumerate(lines[1:], start=2):
        if line.strip() == "---":
            return meta, "\n".join(lines[number:])
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        key = key.strip()
        if not sep or not re.fullmatch(r"[a-z][a-z0-9_]*", key):
            raise ValueError(f"front matter line {number}: expected 'key: value'")
        if key in meta:
            raise ValueError(f"front matter line {number}: duplicate key {key!r}")
        meta[key] = value.strip()
    raise ValueError("the front matter has no closing '---' line")


def _items(value):
    return [] if value.strip().lower() == "none" else [item.strip() for item in value.split(",") if item.strip()]


def section_titles(body):
    return [line[3:].strip() for line in body.split("\n") if line.startswith("## ")]


# ----------------------------------------------------------------------------------------------- locating cases

def cases_root(root=None):
    return Path(root) if root is not None else data_root() / "cases"


def index_path(root=None):
    return (Path(root).parent if root is not None else data_root()) / "cases.json"


def case_names(root=None):
    base = cases_root(root)
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir() and p.name not in SKIPPED_NAMES)


def case_dir(name, root=None):
    path = cases_root(root) / str(name)
    if not NAME_PATTERN.match(str(name)) or not path.is_dir():
        known = ", ".join(case_names(root)) or "none"
        raise CaseError("UNKNOWN_CASE", f"no case named {name!r}; cases: {known}")
    return path


def _skipped(relative):
    return any(part in SKIPPED_NAMES for part in relative.parts) or relative.suffix in SKIPPED_SUFFIXES


def case_files(path):
    """Every file of a case, relative POSIX paths, sorted (caches excluded)."""
    path = Path(path)
    return sorted(p.relative_to(path).as_posix() for p in path.rglob("*")
                  if p.is_file() and not _skipped(p.relative_to(path)))


def read_case(path):
    """Front matter, body and expected outcome of a case directory."""
    path = Path(path)
    meta, body = parse_front_matter((path / "CASE.md").read_text(encoding="utf-8"))
    expected = json.loads((path / "expected.json").read_text(encoding="utf-8"))
    return meta, body, expected


def _catalog(value):
    return None if value is None or str(value).strip().lower() == "none" else str(value).strip()


def index_entry(path):
    path = Path(path)
    meta, _body, _expected = read_case(path)
    import hashlib

    return {"name": meta["name"], "title": meta["title"], "summary": meta["summary"],
            "path": f"cases/{path.name}", "engine": meta["engine"], "catalog": _catalog(meta["catalog"]),
            "tools": _items(meta["tools"]), "tags": _items(meta["tags"]), "files": case_files(path),
            "project_sha256": hashlib.sha256((path / "project.json").read_bytes()).hexdigest()}


def build_index(root=None):
    from ._version import __version__

    return {"schema": INDEX_SCHEMA, "schema_version": SCHEMA_VERSION, "kit_version": __version__,
            "cases": [index_entry(cases_root(root) / name) for name in case_names(root)]}


def index_text(index):
    return json.dumps(index, indent=2, ensure_ascii=False) + "\n"


def load_index(root=None):
    path = index_path(root)
    if not path.is_file():
        raise CaseError("INDEX_MISSING", "cases.json is missing; run 'os-optics-kit cases index --write'")
    return json.loads(path.read_text(encoding="utf-8"))


# ----------------------------------------------------------------------------------------------- format checks

def _catalog_pins(value, found):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "catalog_version" and item is not None:
                found.add(str(item))
            else:
                _catalog_pins(item, found)
    elif isinstance(value, list):
        for item in value:
            _catalog_pins(item, found)
    return found


def check_project(data):
    """Structural ProjectDocumentV2 checks that need no engine: JSON, schema, normalized bytes, targets."""
    problems = []
    try:
        text = data.decode("utf-8")
        document = json.loads(text)
    except (UnicodeDecodeError, ValueError) as exc:
        return [("PROJECT_INVALID", f"project.json is not UTF-8 JSON: {exc}")], None
    if not isinstance(document, dict):
        return [("PROJECT_INVALID", "project.json must hold a JSON object")], None
    if document.get("schema") != "opensci.project" or str(document.get("project_schema_version")) != "2.0":
        problems.append(("PROJECT_SCHEMA", "project.json must be a ProjectDocumentV2 (schema opensci.project, "
                                           "project_schema_version 2.0)"))
    if not isinstance(document.get("components"), list) or not document["components"]:
        problems.append(("PROJECT_INVALID", "project.json declares no components"))
    targets = document.get("targets")
    ids = [t.get("target_id") for t in targets if isinstance(t, dict)] if isinstance(targets, list) else []
    if not ids:
        problems.append(("PROJECT_TARGETS", "project.json declares no targets"))
    elif len(set(ids)) != len(ids) or not all(isinstance(i, str) and i for i in ids):
        problems.append(("PROJECT_TARGETS", "project.json target ids must be unique non-empty strings"))
    if text != normalized_json(document):
        problems.append(("PROJECT_NOT_NORMALIZED", "project.json is not in the engine's normalized form "
                                                   "(indent 2, sorted keys, LF, final newline)"))
    return problems, document


def check_expected(expected, target_ids, meta):
    problems = []
    keys = {"schema", "schema_version", "engine", "catalog", "status", "targets"}
    if not isinstance(expected, dict) or set(expected) != keys:
        return [("EXPECTED_SCHEMA", f"expected.json must have exactly the keys {sorted(keys)}")]
    if expected["schema"] != EXPECTED_SCHEMA or expected["schema_version"] != SCHEMA_VERSION:
        problems.append(("EXPECTED_SCHEMA", f"expected.json schema must be {EXPECTED_SCHEMA} version 1"))
    if expected["status"] not in RUN_STATUSES:
        problems.append(("EXPECTED_SCHEMA", f"expected.json status {expected['status']!r} is not a run status"))
    targets = expected["targets"]
    if not isinstance(targets, dict) or any(v not in ASSESSMENTS for v in targets.values()):
        problems.append(("EXPECTED_SCHEMA", f"expected.json targets must map target ids to {', '.join(ASSESSMENTS)}"))
    elif target_ids is not None and sorted(targets) != sorted(target_ids):
        problems.append(("EXPECTED_TARGETS", "expected.json targets must list exactly the project's targets"))
    if meta is not None:
        if expected["engine"] != meta.get("engine"):
            problems.append(("BASELINE_MISMATCH", "CASE.md engine and expected.json engine differ"))
        if expected["catalog"] != _catalog(meta.get("catalog")):
            problems.append(("BASELINE_MISMATCH", "CASE.md catalog and expected.json catalog differ"))
    return problems


def check_case(path):
    """Every format rule of one case directory -> list of {"case", "code", "message"}."""
    path = Path(path)
    problems = []

    def add(code, message):
        problems.append({"case": path.name, "code": code, "message": message})

    if not NAME_PATTERN.match(path.name):
        add("NAME_INVALID", "case directory names are kebab-case")
    missing = [name for name in REQUIRED_FILES if not (path / name).is_file()]
    for name in missing:
        add("FILE_MISSING", f"{name} is missing")
    meta = None
    if "CASE.md" not in missing:
        text = (path / "CASE.md").read_text(encoding="utf-8")
        try:
            meta, body = parse_front_matter(text)
        except ValueError as exc:
            add("FRONT_MATTER_INVALID", str(exc))
        else:
            for key in FRONT_MATTER_KEYS:
                if not meta.get(key):
                    add("FRONT_MATTER_KEY", f"CASE.md front matter needs {key!r}")
            for key in sorted(set(meta) - set(FRONT_MATTER_KEYS)):
                add("FRONT_MATTER_KEY", f"CASE.md front matter has an unknown key {key!r}")
            if meta.get("name") and meta["name"] != path.name:
                add("FRONT_MATTER_NAME", "CASE.md name must equal the directory name")
            if len(meta.get("summary", "")) > SUMMARY_MAX_CHARS:
                add("SUMMARY_TOO_LONG", f"summary has more than {SUMMARY_MAX_CHARS} characters")
            if meta.get("project") and meta["project"] != "project.json":
                add("FRONT_MATTER_PROJECT", "CASE.md project must be project.json")
            if meta.get("license") and meta["license"] != "MIT":
                add("FRONT_MATTER_LICENSE", "CASE.md license must be MIT")
            if meta.get("engine") and not re.fullmatch(r"\d+\.\d+\.\d+", meta["engine"]):
                add("FRONT_MATTER_ENGINE", "CASE.md engine must be a released version such as 0.12.0")
            from .cli import COMMANDS

            for tool in _items(meta.get("tools", "none")):
                if tool.removeprefix("os-optics-kit ") not in COMMANDS:
                    add("FRONT_MATTER_TOOLS", f"CASE.md tools lists {tool!r}, which is not an os-optics-kit command")
            for tag in _items(meta.get("tags", "")):
                if not NAME_PATTERN.match(tag):
                    add("FRONT_MATTER_TAGS", f"tag {tag!r} is not kebab-case")
            if section_titles(body) != list(SECTIONS):
                add("SECTIONS", "CASE.md sections must be, in order: " + ", ".join(SECTIONS))
            if token_estimate(text) > CASE_TOKEN_BUDGET:
                add("TOKEN_BUDGET", f"CASE.md is {token_estimate(text)} tokens (ceil(characters/4)); "
                                    f"the budget is {CASE_TOKEN_BUDGET}")
    target_ids = None
    document = None
    if "project.json" not in missing:
        found, document = check_project((path / "project.json").read_bytes())
        for code, message in found:
            add(code, message)
        if document is not None and isinstance(document.get("targets"), list):
            target_ids = [t.get("target_id") for t in document["targets"] if isinstance(t, dict)]
        if document is not None and meta is not None and meta.get("catalog"):
            pins = _catalog_pins(document, set())
            catalog = _catalog(meta["catalog"])
            if catalog is None and pins:
                add("CATALOG_PIN", f"catalog is none but project.json pins {sorted(pins)}")
            if catalog is not None and pins != {catalog}:
                add("CATALOG_PIN", f"catalog is {catalog} but project.json pins {sorted(pins) or 'no release'}")
    if "expected.json" not in missing:
        try:
            expected = json.loads((path / "expected.json").read_text(encoding="utf-8"))
        except ValueError as exc:
            add("EXPECTED_SCHEMA", f"expected.json is not JSON: {exc}")
        else:
            for code, message in check_expected(expected, target_ids, meta):
                add(code, message)
    files = case_files(path)
    images = [f for f in files if f.startswith("images/")]
    if len(images) > MAX_IMAGES:
        add("IMAGES", f"{len(images)} images; at most {MAX_IMAGES} per case")
    readmes = sorted(path.glob("README*.md"))  # README.md and optional translations such as README.zh-CN.md
    shown = {p.name: set(re.findall(r"\]\((images/[^)\s]+)\)", p.read_text(encoding="utf-8"))) for p in readmes}
    referenced = shown.get("README.md", set())
    for name in images:
        data = (path / name).read_bytes()
        if not name.endswith(".png") or not data.startswith(PNG_SIGNATURE):
            add("IMAGES", f"{name} must be a PNG file")
        if len(data) > IMAGE_MAX_BYTES:
            add("IMAGES", f"{name} is {len(data)} bytes; at most {IMAGE_MAX_BYTES}")
        if name not in referenced:
            add("IMAGES", f"{name} is not shown in README.md")
    for readme, names in shown.items():
        for name in sorted(names - set(images)):
            add("IMAGES", f"{readme} shows {name}, which does not exist")
    for name in files:
        full = path / name
        size = full.stat().st_size
        if size > FILE_MAX_BYTES:
            add("FILE_TOO_LARGE", f"{name} is {size} bytes; files stay under {FILE_MAX_BYTES}")
        if name.endswith(".glb"):
            add("GENERATED_FILE", f"{name}: generated 3D scenes are not committed")
        if full.name in ("manifest.json", "simulation_result.json", "engine_request.json"):
            add("GENERATED_FILE", f"{name}: run directories are not committed")
        if full.suffix in TEXT_SUFFIXES:
            try:
                text = full.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                add("TEXT_ENCODING", f"{name} is not UTF-8")
                continue
            if '"opensci.public_optical_catalog' in text or "public_optical_catalog.v2" in full.name:
                add("GENERATED_FILE", f"{name}: the catalog is never committed")
            match = LOCAL_PATH.search(text)
            if match:
                line = text.count("\n", 0, match.start()) + 1
                add("LOCAL_PATH", f"{name}:{line} holds a machine-local absolute path ({match.group(0)!r})")
    return problems


def check_all(root=None):
    problems = []
    for name in case_names(root):
        problems.extend(check_case(cases_root(root) / name))
    return problems


# ----------------------------------------------------------------------------------------------- verify

def engine_command():
    """argv prefix of the installed engine CLI: this interpreter's ``os_optics`` if importable, else PATH."""
    if find_spec("os_optics") is not None:
        return [sys.executable, "-m", "os_optics"]
    found = shutil.which("os-optics")
    if found:
        return [found]
    raise CaseError("ENGINE_MISSING", "verify runs the engine: install os-optics (pip install os-optics)")


def _engine(argv, env, *, json_out=True):
    done = subprocess.run(engine_command() + argv, capture_output=True, text=True, encoding="utf-8", env=env)
    document = None
    if json_out:
        try:
            document = json.loads(done.stdout)
        except ValueError:
            document = None
    return done.returncode, document, done.stderr.strip()[-800:]


def actual_outcome(targets_document):
    run = targets_document.get("run") or {}
    status = targets_document.get("validation_status") or targets_document.get("engine_status")
    return {"status": status, "targets": {t["target_id"]: t.get("assessment") for t in targets_document["targets"]},
            "engine": run.get("engine_version"), "catalog": run.get("catalog_version")}


def compare_outcome(expected, actual):
    differences = []
    if actual["status"] != expected["status"]:
        differences.append({"field": "status", "expected": expected["status"], "actual": actual["status"]})
    for target_id in sorted(set(expected["targets"]) | set(actual["targets"])):
        want, got = expected["targets"].get(target_id), actual["targets"].get(target_id)
        if want != got:
            differences.append({"field": f"targets.{target_id}", "expected": want, "actual": got})
    if actual["catalog"] != expected["catalog"]:
        differences.append({"field": "catalog", "expected": expected["catalog"], "actual": actual["catalog"]})
    return differences


def verify_case(name, out_dir, env, *, root=None, update_expected=False):
    path = case_dir(name, root)
    if update_expected:
        _require_checkout(path)
    expected = json.loads((path / "expected.json").read_text(encoding="utf-8"))
    work = Path(out_dir) / name
    if work.exists() and any(work.iterdir()):
        raise CaseError("OUTPUT_EXISTS", f"{work} is not empty; choose another --out directory")
    work.mkdir(parents=True, exist_ok=True)
    run_dir = work / "run"
    entry = {"name": name, "result": "ERROR", "expected": {"status": expected["status"],
                                                          "targets": expected["targets"]},
             "actual": None, "differences": [], "run": run_dir.as_posix()}
    code, check, stderr = _engine(["project", "check", str(path / "project.json")], env)
    if code != 0 or not check or check.get("status") != "ACCEPTED":
        entry["error"] = {"stage": "project check", "exit": code,
                          "status": (check or {}).get("status"), "problems": (check or {}).get("problems"),
                          "stderr": stderr or None}
        return entry
    code, _run, stderr = _engine(["project", "run", str(path / "project.json"), "--out-dir", str(run_dir)], env,
                                 json_out=False)
    if code not in (0, 1, 2, 3):
        entry["error"] = {"stage": "project run", "exit": code, "stderr": stderr or None}
        return entry
    code, targets, stderr = _engine(["results", "targets", str(run_dir)], env)
    if code != 0 or not targets:
        entry["error"] = {"stage": "results targets", "exit": code, "stderr": stderr or None}
        return entry
    actual = actual_outcome(targets)
    entry["actual"] = {"status": actual["status"], "targets": actual["targets"]}
    entry["engine"] = {"expected": expected["engine"], "run": actual["engine"]}
    entry["differences"] = compare_outcome(expected, actual)
    entry["result"] = "MISMATCH" if entry["differences"] else "MATCH"
    if update_expected:
        entry["updated"] = write_expected(path, actual, run_dir)
    return entry


def _require_checkout(path):
    if not (Path(path).parent.parent / "pyproject.toml").is_file():
        raise CaseError("NOT_A_CHECKOUT", "--update-expected writes into a kit checkout, not an installed wheel")


def write_expected(path, actual, run_dir):
    """Record a verified run as the case baseline: the engine-written project.json of the run (normalized form),
    expected.json and the CASE.md engine / catalog lines."""
    path = Path(path)
    _require_checkout(path)
    shutil.copyfile(Path(run_dir) / "project.json", path / "project.json")
    expected = {"schema": EXPECTED_SCHEMA, "schema_version": SCHEMA_VERSION, "engine": actual["engine"],
                "catalog": actual["catalog"], "status": actual["status"], "targets": actual["targets"]}
    (path / "expected.json").write_text(normalized_json(expected), encoding="utf-8", newline="\n")
    text = (path / "CASE.md").read_text(encoding="utf-8")
    text = re.sub(r"(?m)^engine:.*$", f"engine: {actual['engine']}", text, count=1)
    text = re.sub(r"(?m)^catalog:.*$", f"catalog: {actual['catalog'] or 'none'}", text, count=1)
    (path / "CASE.md").write_text(text, encoding="utf-8", newline="\n")
    return ["project.json", "expected.json", "CASE.md"]


def verify(names, *, out=None, catalog=None, root=None, update_expected=False):
    env = dict(os.environ)
    temporary = None
    if out is None:
        temporary = tempfile.mkdtemp(prefix="os-optics-kit-cases-")
        out = temporary
    out = Path(out)
    try:
        catalog_info = None
        if catalog is not None:
            catalog = Path(catalog)
            if not catalog.is_file():
                raise CaseError("BAD_ARGUMENT", f"--catalog {catalog}: no such file")
            store = out / ".catalog-store"
            env.update({"OPENSCI_CATALOG_DIR": str(store), "OPENSCI_CATALOG_CHECK_INTERVAL": "0"})
            code, imported, stderr = _engine(["catalog", "import", str(catalog)], env)
            if code != 0:
                raise CaseError("CATALOG_IMPORT_FAILED", f"os-optics catalog import failed (exit {code}): {stderr}")
            active = (imported or {}).get("active") or {}
            catalog_info = {"version": active.get("catalog_version"), "sha256": active.get("sha256")}
        results = [verify_case(name, out, env, root=root, update_expected=update_expected) for name in names]
    finally:
        if temporary is not None:
            shutil.rmtree(temporary, ignore_errors=True)
    counts = {key: sum(1 for r in results if r["result"] == key) for key in ("MATCH", "MISMATCH", "ERROR")}
    if temporary is not None:
        for entry in results:
            entry.pop("run", None)
    document = {"status": "MATCH" if counts["MATCH"] == len(results) else "MISMATCH", "counts": counts,
                "out": None if temporary else out.as_posix(), "cases": results}
    if catalog_info:
        document["catalog"] = catalog_info
    return document


# ----------------------------------------------------------------------------------------------- copy

def copy_case(name, dest, *, force=False, root=None):
    source = case_dir(name, root)
    target = Path(dest) / name
    files = case_files(source)
    conflicts = [f for f in files if (target / f).exists() and (target / f).read_bytes() != (source / f).read_bytes()]
    if conflicts and not force:
        raise CaseError("DESTINATION_EXISTS", f"{target} holds different files ({', '.join(conflicts[:5])}); "
                                              "use --force to overwrite them")
    written = []
    for relative in files:
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists() or destination.read_bytes() != (source / relative).read_bytes():
            shutil.copyfile(source / relative, destination)
            written.append(relative)
    return {"status": "COPIED", "case": name, "dest": target.as_posix(), "files": files, "written": written,
            "unchanged": len(files) - len(written)}


# ----------------------------------------------------------------------------------------------- CLI

def _parser():
    parser = ArgumentParser(prog="os-optics-kit cases", description=__doc__,
                            formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("list", help="print the case index (compact JSON)")
    listing.add_argument("--tag", help="only cases with this tag")
    show = sub.add_parser("show", help="print a case's CASE.md")
    show.add_argument("name")
    copy = sub.add_parser("copy", help="copy a case directory to DEST/NAME")
    copy.add_argument("name")
    copy.add_argument("--dest", type=Path, required=True, help="destination directory")
    copy.add_argument("--force", action="store_true", help="overwrite different existing files")
    check = sub.add_parser("verify", help="run cases with the installed engine and compare with expected.json")
    check.add_argument("names", nargs="*", metavar="NAME")
    check.add_argument("--all", action="store_true", help="every case")
    check.add_argument("--out", type=Path, help="keep the runs in OUT/NAME/run (default: a temporary directory)")
    check.add_argument("--catalog", type=Path, help="import this catalog release file into a private store under "
                                                    "OUT first (no download)")
    check.add_argument("--update-expected", action="store_true",
                       help="write the runs' outcomes to expected.json and CASE.md (kit checkout only)")
    index = sub.add_parser("index", help="generate (--write) or validate (--check) cases.json and every case")
    mode = index.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    return parser


def run(argv):
    args = _parser().parse_args(argv)
    if args.command == "list":
        document = load_index()
        if args.tag:
            document = {**document, "cases": [c for c in document["cases"] if args.tag in c["tags"]]}
        _emit(document)
        return EXIT_OK
    if args.command == "show":
        _write((case_dir(args.name) / "CASE.md").read_text(encoding="utf-8"))
        return EXIT_OK
    if args.command == "copy":
        _emit(copy_case(args.name, args.dest, force=args.force))
        return EXIT_OK
    if args.command == "verify":
        if bool(args.all) == bool(args.names):
            raise CaseError("BAD_ARGUMENT", "give case names or --all")
        names = case_names() if args.all else args.names
        for name in names:
            case_dir(name)
        engine_command()
        document = verify(names, out=args.out, catalog=args.catalog, update_expected=args.update_expected)
        _emit(document)
        return EXIT_OK if document["status"] == "MATCH" else EXIT_DIFFERENT
    problems = check_all()
    if args.write:
        if problems:
            _emit({"status": "PROBLEMS", "problems": problems})
            return EXIT_DIFFERENT
        index = build_index()
        index_path().write_text(index_text(index), encoding="utf-8", newline="\n")
        _emit({"status": "WRITTEN", "path": "cases.json", "cases": len(index["cases"])})
        return EXIT_OK
    try:
        current = index_path().read_text(encoding="utf-8")
    except FileNotFoundError:
        current = None
    try:
        generated = index_text(build_index())
    except (KeyError, ValueError, OSError):
        generated = None  # a case without readable CASE.md / expected.json: already reported above
    if generated is not None and current != generated:
        problems.append({"case": None, "code": "INDEX_STALE",
                         "message": "cases.json differs from the cases; run 'os-optics-kit cases index --write'"})
    _emit({"status": "OK" if not problems else "PROBLEMS", "cases": len(case_names()), "problems": problems})
    return EXIT_OK if not problems else EXIT_DIFFERENT


def main(argv=None):
    try:
        return run(list(sys.argv[1:] if argv is None else argv))
    except CaseError as exc:
        print(json.dumps({"status": "ERROR", "error": {"code": exc.code, "message": str(exc)}},
                         separators=(",", ":")), file=sys.stderr)
        return EXIT_USAGE
    except SystemExit as exc:  # --help
        return int(exc.code or 0)


if __name__ == "__main__":
    raise SystemExit(main())
