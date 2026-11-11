"""``os-optics-kit cases``: list, show, copy, verify, index and illustrate the engine-reproducible cases.

A case is a directory ``cases/<name>/`` holding ``project.json`` (an engine-written ProjectDocumentV2 with its
targets), ``CASE.md`` (flat front matter and fixed sections), ``README.md`` and ``README.zh-CN.md`` (the illustrated
pages), ``images.json`` (the record of its images) and ``expected.json`` (the verified run status and target
assessments), plus optional case scripts, study specs and data. ``cases.json`` indexes them. The cases ship inside
the wheel, so list, show, copy, verify and vocabulary work without a clone.

Subcommands (compact JSON on stdout, except ``show``):

  list [--class C] [--domain D] [--provenance P] [--publication P] [--scale S] [--tag T] [--catalog RELEASE|none]
                                   the case index, filtered (filters combine with AND)
  vocabulary                       classes, domains, provenance, publication, scale bands, tags and image budget
  show NAME                        the case's CASE.md
  copy NAME --dest DIR [--force]   copy the case to DIR/NAME
  verify NAME... | --all [--out DIR] [--catalog FILE] [--jobs N] [--update-expected]
                                   project check -> project run -> results targets -> compare with expected.json
                                   and the run identity recorded in images.json
  index --write | --check          generate or validate cases.json and every case's format
  images NAME [--run RUN] [--view V]... [--camera A,E[,R[,D]]] [--section ELEMENT|ALL] [--size WxH]
              [--section-size WxH] [--frame FRAME] [--catalog FILE]
                                   render the case's system views with the engine and record them in images.json;
                                   without a view option, the views recorded in images.json are rendered again
  images NAME --figure images/X.png --script SCRIPT.py
                                   record or refresh one figure drawn by a case script

Case images need no browser: system views are the engine's own snapshots,
``os-optics view RUN --no-browser --snapshot DIR --views iso`` (3D) and ``--section ALL`` (2D lens section), and
``cases images`` runs exactly that. The kit's ``render`` and ``present`` (presentation-grade figures, camera sets,
galleries, animations) need ``os-optics-kit[render]`` and Chrome or Chromium; case images never need them.

Every subcommand takes --root DIR (or OS_OPTICS_KIT_ROOT): a kit checkout to use instead of the installed cases.
``images`` and ``verify --update-expected`` write into a kit checkout only.

Exit codes: 0 success / every case matches / index valid; 1 a case differs from expected.json or images.json, or the
index or a case fails its format checks; 4 usage error, unknown case, refused request or no engine.

``list``, ``vocabulary``, ``show``, ``copy`` and ``index`` need only the standard library; ``verify`` and ``images``
run the installed engine (``python -m os_optics`` of this interpreter).
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
from importlib.util import find_spec
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from . import _engine as _kit_engine
from . import _frontmatter as fm
from . import case_taxonomy as T
from ._resources import ROOT_ENV, data_root

INDEX_SCHEMA = "os-optics-kit.cases"
INDEX_SCHEMA_VERSION = 2
EXPECTED_SCHEMA = "os-optics-kit.case-expected"
EXPECTED_SCHEMA_VERSION = 1
IMAGES_SCHEMA = "os-optics-kit.case-images"
IMAGES_SCHEMA_VERSION = 1
IMAGES_FILE = "images.json"
#: required front matter keys, in the order CASE.md states them; ``source`` is optional and follows ``publication``
FRONT_MATTER_KEYS = ("name", "title", "summary", "class", "domain", "provenance", "publication", "engine", "catalog",
                     "project", "tools", "tags", "license")
OPTIONAL_FRONT_MATTER_KEYS = ("source",)
FRONT_MATTER_ORDER = ("name", "title", "summary", "class", "domain", "provenance", "publication", "source", "engine",
                      "catalog", "project", "tools", "tags", "license")
SECTIONS = ("What it shows", "Run", "Targets", "Baseline", "Files")
REQUIRED_FILES = ("project.json", "CASE.md", "README.md", "README.zh-CN.md", "expected.json")
#: the README pair: each shows exactly the recorded images, each once, in images.json order
README_PAIR = ("README.md", "README.zh-CN.md")
ASSESSMENTS = ("MET", "UNMET", "INDETERMINATE", "NOT_APPLICABLE")
RUN_STATUSES = ("PASS", "WARNING", "FAIL", "UNKNOWN", "OUT_OF_MODEL_SCOPE", "NOT_APPLICABLE")
CASE_TOKEN_BUDGET = fm.BUDGETS["CASE.md"]
SUMMARY_MAX_CHARS = 300
IMAGE_MAX_BYTES = T.IMAGE_MAX_BYTES
FILE_MAX_BYTES = T.FILE_MAX_BYTES
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
CATALOG_RELEASE = re.compile(r"^\d{4}-\d{2}-\d{2}\.\d+$")
SIZE_PATTERN = re.compile(r"^(\d{2,5})x(\d{2,5})$")
SECTION_ARGUMENT = re.compile(r"[A-Za-z0-9_.-]+")
CAMERA_PATTERN = re.compile(r"^-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?(?:,-?\d+(?:\.\d+)?(?:,\d+(?:\.\d+)?x?)?)?$")
SKIPPED_NAMES = {"__pycache__", ".pytest_cache", ".DS_Store"}
SKIPPED_SUFFIXES = (".pyc", ".pyo")
# Machine-local absolute paths: a Windows drive path, a UNC path or a POSIX home / temporary directory.
LOCAL_PATH = re.compile(r"(?<![A-Za-z])[A-Za-z]:\\[A-Za-z0-9_.$-]|(?<![A-Za-z:/])[A-Za-z]:/[A-Za-z0-9_.$-]|"
                        r"\\\\[A-Za-z0-9_.$-]+\\[A-Za-z0-9_.$-]|(?<![\w.~-])/(?:home|Users|root|mnt|tmp|private)/")
TEXT_SUFFIXES = (".md", ".json", ".py", ".txt", ".csv", ".toml", ".cfg", ".yml", ".yaml", ".svg")
#: images shown by a README, in reading order: Markdown image syntax ``![alt](images/... "title")`` and HTML
#: ``<img src="images/...">``; a plain link ``[x](images/...)`` shows nothing (HTML comments and fenced code are
#: removed first, see ``readme_text``)
SHOWN_IMAGE = re.compile(r"!\[(?:[^\[\]]|\[[^\[\]]*\])*\]\(\s*<?(images/[^)\s>]+)>?(?:\s+\"[^\"]*\"|\s+'[^']*')?\s*\)"
                         r"|<img\b[^>]*\ssrc=[\"'](images/[^\"'\s]+)[\"']", re.IGNORECASE)
#: link targets of a README: Markdown links ``[text](target)`` (not images) and HTML ``<a href="target">``
LINK_TARGET = re.compile(r"(?<!!)\[(?:[^\[\]]|\[[^\[\]]*\])*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\"|\s+'[^']*')?\s*\)"
                         r"|<a\b[^>]*\shref=[\"']([^\"'\s]+)[\"']", re.IGNORECASE)
#: a recorded image file: a PNG directly under the case's images/ directory
IMAGE_FILE = re.compile(r"images/[A-Za-z0-9_.-]+\.png")
#: the README pair links each other: README.md -> README.zh-CN.md and README.zh-CN.md -> README.md
README_LINKS = {"README.md": "README.zh-CN.md", "README.zh-CN.md": "README.md"}
#: the frame of the section in the default views of a lens case (no view recorded and no view option given)
DEFAULT_SECTION_FRAME = "scene"
#: the options of a recorded system view (``os-optics view`` arguments); --frame is one token, ``--frame=VALUE``
VIEW_OPTIONS = ("--views", "--camera", "--section", "--frame", "--size")
#: default image sizes of ``cases images`` (snapshot views / sections); a PNG over the budget is redrawn smaller
SNAPSHOT_SIZE = "960x540"
SECTION_SIZE = "1200x420"
SIZE_STEPS = (1.0, 0.75, 0.5)
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


def normalized_json(document):
    """The normalized form of project.json / expected.json / images.json bytes (LF line ends)."""
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ----------------------------------------------------------------------------------------------- front matter

def parse_front_matter(text):
    """``(fields, body)`` of CASE.md: the kit's flat front matter (``_frontmatter.split``); raises ValueError."""
    return fm.split(text)


def _items(value):
    return [] if value.strip().lower() == "none" else [item.strip() for item in value.split(",") if item.strip()]


def parse_list(value):
    """A comma-separated front matter value as a list (``none`` is kept as a value: callers refuse it)."""
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def section_titles(body):
    return [line[3:].strip() for line in body.split("\n") if line.startswith("## ")]


def _allowed(terms):
    return ", ".join(T.values(terms))


def check_front_matter(meta, name=None):
    """Every front matter rule of CASE.md -> list of (code, message); ``name`` is the case directory name."""
    problems = []

    def add(code, message):
        problems.append((code, message))

    for key in FRONT_MATTER_KEYS:
        if not meta.get(key):
            add("FRONT_MATTER_KEY", f"CASE.md front matter needs {key!r}")
    for key in sorted(set(meta) - set(FRONT_MATTER_ORDER)):
        add("FRONT_MATTER_KEY", f"CASE.md front matter has an unknown key {key!r}")
    present = [key for key in meta if key in FRONT_MATTER_ORDER]
    if present != [key for key in FRONT_MATTER_ORDER if key in meta]:
        add("FRONT_MATTER_ORDER", "CASE.md front matter keys must be in the order " + ", ".join(FRONT_MATTER_ORDER))
    if name is not None and meta.get("name") and meta["name"] != name:
        add("FRONT_MATTER_NAME", "CASE.md name must equal the directory name")
    if len(meta.get("summary", "")) > SUMMARY_MAX_CHARS:
        add("SUMMARY_TOO_LONG", f"summary has more than {SUMMARY_MAX_CHARS} characters")
    if meta.get("project") and meta["project"] != "project.json":
        add("FRONT_MATTER_PROJECT", "CASE.md project must be project.json")
    if meta.get("license") and meta["license"] != "MIT":
        add("FRONT_MATTER_LICENSE", "CASE.md license must be MIT")
    if meta.get("engine") and not re.fullmatch(r"\d+\.\d+\.\d+", meta["engine"]):
        add("FRONT_MATTER_ENGINE", "CASE.md engine must be a released version such as 0.13.0")
    from .cli import COMMANDS

    for tool in _items(meta.get("tools", "none")):
        if tool.removeprefix("os-optics-kit ") not in COMMANDS:
            add("FRONT_MATTER_TOOLS", f"CASE.md tools lists {tool!r}, which is not an os-optics-kit command")
    if meta.get("class") and meta["class"] not in T.values(T.CLASSES):
        add("FRONT_MATTER_CLASS", f"class {meta['class']!r} is not one of {_allowed(T.CLASSES)}")
    if meta.get("domain"):
        domains = parse_list(meta["domain"])
        for domain in domains:
            if domain not in T.values(T.DOMAINS):
                add("FRONT_MATTER_DOMAIN", f"domain {domain!r} is not one of {_allowed(T.DOMAINS)}")
        if len(set(domains)) != len(domains):
            add("FRONT_MATTER_DOMAIN", "domain values must be distinct")
        if not T.DOMAIN_MIN <= len(domains) <= T.DOMAIN_MAX:
            add("FRONT_MATTER_DOMAIN", f"domain lists {len(domains)} values; {T.DOMAIN_MIN} to {T.DOMAIN_MAX}, "
                                       "primary first")
    provenance, publication, source = meta.get("provenance"), meta.get("publication"), meta.get("source")
    if provenance and provenance not in T.values(T.PROVENANCE):
        add("FRONT_MATTER_PROVENANCE", f"provenance {provenance!r} is not one of {_allowed(T.PROVENANCE)}")
    if publication and publication not in T.values(T.PUBLICATION):
        add("FRONT_MATTER_PUBLICATION", f"publication {publication!r} is not one of {_allowed(T.PUBLICATION)}")
    if provenance not in T.values(T.PROVENANCE):
        provenance = None  # reported above; the cross-field rules need a known provenance
    if provenance and publication in T.values(T.PUBLICATION):
        if provenance == T.SELF_BUILT and publication != T.OWN_WORK:
            add("FRONT_MATTER_PUBLICATION", f"provenance {T.SELF_BUILT} goes with publication {T.OWN_WORK}")
        if provenance != T.SELF_BUILT and publication == T.OWN_WORK:
            add("FRONT_MATTER_PUBLICATION", f"provenance {provenance} needs a publication basis other than "
                                            f"{T.OWN_WORK} (open-license, permission or public-domain)")
    if provenance == T.SELF_BUILT and source:
        add("FRONT_MATTER_SOURCE", f"source is not given for provenance {T.SELF_BUILT}")
    if provenance and provenance != T.SELF_BUILT and not source:
        add("FRONT_MATTER_SOURCE", f"provenance {provenance} needs source: the citation of the third-party origin")
    if source and len(source) > T.SOURCE_MAX_CHARS:
        add("FRONT_MATTER_SOURCE", f"source has more than {T.SOURCE_MAX_CHARS} characters")
    if meta.get("tags"):
        tags = parse_list(meta["tags"])
        fields = T.field_values()
        for tag in tags:
            if tag in fields or tag in T.FIELD_WORDS:
                add("FRONT_MATTER_TAGS", f"tag {tag!r} repeats a front matter field (class, domain, provenance, "
                                         "publication or catalog); drop it")
            elif tag not in T.values(T.TAGS):
                add("FRONT_MATTER_TAGS", f"tag {tag!r} is not in the tag vocabulary "
                                         "('os-optics-kit cases vocabulary' lists it)")
        if len(set(tags)) != len(tags):
            add("FRONT_MATTER_TAGS", "tags must be distinct")
        if not T.TAG_MIN <= len(tags) <= T.TAG_MAX:
            add("FRONT_MATTER_TAGS", f"tags lists {len(tags)} values; {T.TAG_MIN} to {T.TAG_MAX}")
    return problems


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


def read_images(path):
    """The images.json document of a case directory, or None when it is missing or not a JSON object."""
    try:
        document = json.loads((Path(path) / IMAGES_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def index_images(document):
    """The ``images`` list of an index entry: file, kind and view (system views) or script (figures)."""
    records = (document or {}).get("images")
    entries = []
    for record in records if isinstance(records, list) else []:
        if not isinstance(record, dict):
            continue
        if record.get("kind") == T.FIGURE_KIND:
            entries.append({"file": record.get("file"), "kind": record.get("kind"), "script": record.get("script")})
        else:
            entries.append({"file": record.get("file"), "kind": record.get("kind"), "view": record.get("view")})
    return entries


def index_entry(path):
    path = Path(path)
    meta, _body, _expected = read_case(path)
    project_bytes = (path / "project.json").read_bytes()
    entry = {"name": meta["name"], "title": meta["title"], "summary": meta["summary"], "path": f"cases/{path.name}",
             "class": meta["class"], "domain": parse_list(meta["domain"]), "provenance": meta["provenance"],
             "publication": meta["publication"]}
    if meta.get("source"):
        entry["source"] = meta["source"]
    entry.update({"scale": T.scale_of(json.loads(project_bytes.decode("utf-8"))), "engine": meta["engine"],
                  "catalog": _catalog(meta["catalog"]), "tools": _items(meta["tools"]),
                  "tags": parse_list(meta["tags"]), "images": index_images(read_images(path)),
                  "files": case_files(path), "project_sha256": hashlib.sha256(project_bytes).hexdigest()})
    return entry


def build_index(root=None):
    from ._version import __version__

    return {"schema": INDEX_SCHEMA, "schema_version": INDEX_SCHEMA_VERSION, "kit_version": __version__,
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
    if expected["schema"] != EXPECTED_SCHEMA or expected["schema_version"] != EXPECTED_SCHEMA_VERSION:
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


_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_COMMENT = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)


def readme_text(text):
    """The rendered part of a README: fenced code blocks and HTML comments removed (an unclosed fence or comment
    runs to the end, as in CommonMark)."""
    kept, fence = [], None
    for line in text.split("\n"):
        match = _FENCE.match(line)
        if fence is None:
            if match and not (match.group(1)[0] == "`" and "`" in line[match.end():]):
                fence = match.group(1)
                continue
            kept.append(line)
        elif match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence) \
                and not line[match.end():].strip():
            fence = None
    return _COMMENT.sub("", "\n".join(kept))


def shown_images(text):
    """The images/ paths a README shows, in reading order (repeats kept): image syntax only, outside fenced code
    and HTML comments."""
    return [a or b for a, b in SHOWN_IMAGE.findall(readme_text(text))]


def linked_files(text):
    """The link targets of a README outside fenced code and HTML comments (``./`` and ``#fragment`` dropped)."""
    targets = []
    for a, b in LINK_TARGET.findall(readme_text(text)):
        target = (a or b).split("#", 1)[0].removeprefix("./")
        if target:
            targets.append(target)
    return targets


def _read_text(path):
    """A text file's contents; bytes that are not UTF-8 are replaced (TEXT_ENCODING reports them)."""
    return Path(path).read_bytes().decode("utf-8", errors="replace")


def is_snapshot_view(view):
    return view in T.SNAPSHOT_VIEWS or view == T.CAMERA_VIEW


IMAGES_KEYS = {"images", "project_sha256", "run", "schema", "schema_version"}
RUN_KEYS = {"catalog", "engine", "engine_input_hash", "project_hash", "scene_content_hash"}
RECORD_KEYS = {T.SYSTEM_VIEW_KIND: {"file", "kind", "options", "sha256", "view"},
               T.FIGURE_KIND: {"file", "kind", "script", "sha256"}}


def parse_view_options(options, *, strict=True):
    """``{option: value}`` of recorded ``os-optics view`` options; raises ValueError for a form ``cases images``
    cannot replay: an unknown option, a missing value, a repeat, or a frame not given as one ``--frame=VALUE`` token
    (a separate negative box frame such as ``--frame -6,-6,-4,6,6,168`` is refused by argparse on Python 3.11-3.13).
    ``strict=False`` also reads the earlier two-token form ``--frame VALUE`` (and other values starting with '-'),
    so ``cases images`` can render such a record again and record it in the one-token form."""
    if not isinstance(options, list) or not all(isinstance(o, str) for o in options):
        raise ValueError("options must be a list of strings (the os-optics view arguments after RUN --no-browser "
                         "--snapshot DIR)")
    parsed = {}
    number = 0
    while number < len(options):
        option = options[number]
        name, equals, value = option.partition("=")
        if name not in VIEW_OPTIONS:
            raise ValueError(f"option {option!r} is not one of {', '.join(VIEW_OPTIONS)}")
        if equals:
            number += 1
        elif name == "--frame" and strict:
            raise ValueError("give the frame as one token '--frame=VALUE' (a separate negative box frame is not "
                             "parsed on Python 3.11-3.13)")
        elif number + 1 >= len(options) or options[number + 1].startswith("--"):
            raise ValueError(f"option {name} needs a value")
        else:
            value = options[number + 1]
            if value.startswith("-") and strict:
                raise ValueError(f"give {name} with a value starting with '-' as one token '{name}={value}'")
            number += 2
        if not value:
            raise ValueError(f"option {name} needs a value")
        if name in parsed:
            raise ValueError(f"option {name} is given twice")
        parsed[name] = value
    return parsed


def _option_tokens(name, value):
    """``[name, value]``, or the one token ``name=value`` for a frame or a value starting with '-' (argparse on
    Python 3.11-3.13 would read such a value as an option)."""
    return [f"{name}={value}"] if name == "--frame" or value.startswith("-") else [name, value]


def _options_problem(view, options, *, strict=True):
    """Why ``options`` cannot have drawn ``view`` or cannot be replayed (None when they agree)."""
    try:
        parsed = parse_view_options(options, strict=strict)
    except ValueError as exc:
        return str(exc)
    if view in T.SNAPSHOT_VIEWS:
        if parsed.get("--views") != view:
            return f"options must hold '--views {view}'"
        allowed = {"--views", "--size"}
    elif view == T.CAMERA_VIEW:
        if "--camera" not in parsed:
            return "options of a camera view must hold '--camera A,E'"
        allowed = {"--camera", "--size"}
    else:
        if "--section" not in parsed:
            return "options of a section view must hold '--section ELEMENT|ALL'"
        allowed = {"--section", "--frame", "--size"}
    extra = sorted(set(parsed) - allowed)
    if extra:
        return f"options of a {view} view take only {', '.join(sorted(allowed))}, not {', '.join(extra)}"
    if "--size" in parsed and not SIZE_PATTERN.match(parsed["--size"]):
        return f"--size {parsed['--size']!r} is not WIDTHxHEIGHT"
    if view == T.CAMERA_VIEW and not CAMERA_PATTERN.match(parsed["--camera"]):
        return f"--camera {parsed['--camera']!r} is not ANGLE,ELEVATION[,ROLL[,DISTANCE]]"
    if view == T.SECTION_VIEW and not SECTION_ARGUMENT.fullmatch(parsed["--section"]):
        return f"--section {parsed['--section']!r} is not an element id or ALL"
    return None


def recorded_view(record, *, strict=True):
    """``(view, argument, size, frame)`` of a system-view record of images.json, as ``cases images`` replays it
    (size defaults to the view's default size, frame is None when not recorded); raises ValueError for a record that
    cannot be replayed exactly: an unknown view, options that do not draw the view, or a file other than the one
    the view writes."""
    view = record.get("view")
    if not isinstance(view, str) or not (is_snapshot_view(view) or view == T.SECTION_VIEW):
        raise ValueError(f"view {view!r} is not one of "
                         f"{', '.join(T.SNAPSHOT_VIEWS + (T.CAMERA_VIEW, T.SECTION_VIEW))}")
    options = record.get("options")
    problem = _options_problem(view, options, strict=strict)
    if problem:
        raise ValueError(problem)
    parsed = parse_view_options(options, strict=strict)
    flag = {T.SECTION_VIEW: "--section", T.CAMERA_VIEW: "--camera"}.get(view, "--views")
    argument = parsed[flag]
    file = _image_file(view, argument)
    if record.get("file") != file:
        raise ValueError(f"file {record.get('file')!r} must be {file}, the file this view writes")
    size = parsed.get("--size") or (SECTION_SIZE if view == T.SECTION_VIEW else SNAPSHOT_SIZE)
    return view, argument, size, parsed.get("--frame")


def recorded_views(document, *, strict=True):
    """The system views recorded in an images.json document, in recorded order, as ``[(view, argument, size,
    frame)]`` (``recorded_view``); raises ValueError naming the first record that cannot be replayed."""
    plan = []
    records = (document or {}).get("images")
    for number, record in enumerate(records if isinstance(records, list) else [], start=1):
        if isinstance(record, dict) and record.get("kind") == T.SYSTEM_VIEW_KIND:
            try:
                plan.append(recorded_view(record, strict=strict))
            except ValueError as exc:
                raise ValueError(f"{IMAGES_FILE} image {number}: {exc}") from None
    return plan


def check_images(path, meta, files):
    """The image rules of a case -> list of (code, message): images.json (IMAGES_RECORD) and the files, budget
    and README pair (IMAGES)."""
    path = Path(path)
    problems = []

    def add(code, message):
        problems.append((code, message))

    on_disk = [f for f in files if f.startswith("images/")]
    for name in on_disk:
        data = (path / name).read_bytes()
        if not name.endswith(".png") or not data.startswith(PNG_SIGNATURE):
            add("IMAGES", f"{name} must be a PNG file")
        if len(data) > IMAGE_MAX_BYTES:
            add("IMAGES", f"{name} is {len(data)} bytes; at most {IMAGE_MAX_BYTES}")
    readmes = {p.name: shown_images(_read_text(p)) for p in sorted(path.glob("README*.md"))}
    for readme, names in readmes.items():
        for name in sorted(set(names) - set(on_disk)):
            add("IMAGES", f"{readme} shows {name}, which does not exist")
    if IMAGES_FILE not in files:
        add("IMAGES_RECORD", f"{IMAGES_FILE} is missing; 'os-optics-kit cases images {path.name}' renders the system "
                             "views and writes it")
        return problems
    try:  # bytes, not read_text: universal newlines would hide CR bytes from the normalized-form check
        text = (path / IMAGES_FILE).read_bytes().decode("utf-8")
        document = json.loads(text)
    except (UnicodeDecodeError, ValueError) as exc:
        add("IMAGES_RECORD", f"{IMAGES_FILE} is not JSON in UTF-8: {exc}")
        return problems
    if not isinstance(document, dict) or set(document) != IMAGES_KEYS:
        add("IMAGES_RECORD", f"{IMAGES_FILE} must have exactly the keys {sorted(IMAGES_KEYS)}")
        return problems
    if text != normalized_json(document):
        add("IMAGES_RECORD", f"{IMAGES_FILE} is not in the normalized form (indent 2, sorted keys, LF, final newline)")
    if document["schema"] != IMAGES_SCHEMA or document["schema_version"] != IMAGES_SCHEMA_VERSION:
        add("IMAGES_RECORD", f"{IMAGES_FILE} schema must be {IMAGES_SCHEMA} version {IMAGES_SCHEMA_VERSION}")
    if (path / "project.json").is_file() and document["project_sha256"] != sha256_file(path / "project.json"):
        add("IMAGES_RECORD", f"{IMAGES_FILE} project_sha256 is not the SHA-256 of project.json: the images come "
                             f"from another design; rerun 'os-optics-kit cases images {path.name}'")
    run = document["run"]
    if not isinstance(run, dict) or set(run) != RUN_KEYS:
        add("IMAGES_RECORD", f"{IMAGES_FILE} run must have exactly the keys {sorted(RUN_KEYS)}")
    elif meta is not None:
        if run["engine"] != meta.get("engine"):
            add("IMAGES_RECORD", f"{IMAGES_FILE} run.engine {run['engine']!r} differs from the CASE.md engine")
        if run["catalog"] != _catalog(meta.get("catalog")):
            add("IMAGES_RECORD", f"{IMAGES_FILE} run.catalog {run['catalog']!r} differs from the CASE.md catalog")
    records = document["images"]
    if not isinstance(records, list):
        add("IMAGES_RECORD", f"{IMAGES_FILE} images must be a list")
        return problems
    recorded, views, figures = [], [], []
    for number, record in enumerate(records, start=1):
        where = f"{IMAGES_FILE} image {number}"
        kind = record.get("kind") if isinstance(record, dict) else None
        if kind not in RECORD_KEYS or set(record) != RECORD_KEYS[kind]:
            add("IMAGES_RECORD", f"{where} must be a system-view {sorted(RECORD_KEYS[T.SYSTEM_VIEW_KIND])} or a "
                                 f"figure {sorted(RECORD_KEYS[T.FIGURE_KIND])}")
            continue
        file = record["file"]
        if not isinstance(file, str) or not IMAGE_FILE.fullmatch(file):
            add("IMAGES_RECORD", f"{where}: file {file!r} must be a PNG file directly under images/")
            continue
        if file.lower() in {r.lower() for r in recorded}:
            add("IMAGES_RECORD", f"{where}: {file} is recorded twice (file names are compared ignoring case)")
            continue
        recorded.append(file)
        if not (path / file).is_file():
            add("IMAGES_RECORD", f"{where}: {file} does not exist")
        elif record["sha256"] != sha256_file(path / file):
            add("IMAGES_RECORD", f"{where}: the sha256 of {file} differs from the file; re-record it")
        if kind == T.SYSTEM_VIEW_KIND:
            try:
                recorded_view(record)
            except ValueError as exc:
                add("IMAGES_RECORD", f"{where}: {exc}")
            views.append(record["view"])
        else:
            script = record["script"]
            if not isinstance(script, str) or not script.endswith(".py") or script not in files:
                add("IMAGES_RECORD", f"{where}: script {script!r} must be a .py file of the case")
            figures.append(file)
    for name in on_disk:
        if name not in recorded:
            add("IMAGES", f"{name} is not recorded in {IMAGES_FILE}; record it or remove it")
    if len(records) > T.IMAGE_MAX_COUNT:
        add("IMAGES", f"{len(records)} images; at most {T.IMAGE_MAX_COUNT} per case")
    if not T.SYSTEM_VIEW_MIN <= len(views) <= T.SYSTEM_VIEW_MAX:
        add("IMAGES", f"{len(views)} system views; {T.SYSTEM_VIEW_MIN} to {T.SYSTEM_VIEW_MAX} per case")
    if len(figures) > T.FIGURE_MAX:
        add("IMAGES", f"{len(figures)} figures; at most {T.FIGURE_MAX} per case")
    first = records[0] if records and isinstance(records[0], dict) else {}
    if records and not (first.get("kind") == T.SYSTEM_VIEW_KIND and is_snapshot_view(first.get("view"))):
        add("IMAGES", "the first image must be a snapshot view (iso preferred): the READMEs show it first")
    if meta is not None and meta.get("class") == "lens" and T.SECTION_VIEW not in views:
        add("IMAGES", "a lens case has a section view ('os-optics-kit cases images NAME ... --section ALL')")
    for readme in README_PAIR:
        if readme not in readmes:
            continue
        shown = readmes[readme]
        if shown != recorded:
            add("IMAGES", f"{readme} shows {shown or 'no images'}; it must show exactly {recorded}, each once, "
                          f"in {IMAGES_FILE} order")
    return problems


def check_readme_pair(path):
    """README_PAIR: README.md links README.zh-CN.md and README.zh-CN.md links README.md (a Markdown link or an
    HTML ``<a href>`` outside fenced code and HTML comments) -> list of (code, message)."""
    problems = []
    for readme, partner in README_LINKS.items():
        if (Path(path) / readme).is_file() and partner not in linked_files(_read_text(Path(path) / readme)):
            problems.append(("README_PAIR", f"{readme} must link {partner} near the top, for example "
                                            + ("'English · [简体中文](README.zh-CN.md)'" if readme == "README.md"
                                               else "'[English](README.md) · 简体中文'")))
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
            for code, message in check_front_matter(meta, path.name):
                add(code, message)
            if section_titles(body) != list(SECTIONS):
                add("SECTIONS", "CASE.md sections must be, in order: " + ", ".join(SECTIONS))
            if fm.token_estimate(text) > CASE_TOKEN_BUDGET:
                add("TOKEN_BUDGET", f"CASE.md is {fm.token_estimate(text)} tokens (ceil(characters/4)); "
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
    for code, message in check_images(path, meta, files):
        add(code, message)
    for code, message in check_readme_pair(path):
        add(code, message)
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


# ----------------------------------------------------------------------------------------------- list filters

def list_filters():
    """``cases list`` filter name -> (index key, allowed values or None for a pattern-checked value)."""
    return {"class": ("class", T.values(T.CLASSES)), "domain": ("domain", T.values(T.DOMAINS)),
            "provenance": ("provenance", T.values(T.PROVENANCE)),
            "publication": ("publication", T.values(T.PUBLICATION)), "scale": ("scale", T.values(T.SCALE_BANDS)),
            "tag": ("tags", T.values(T.TAGS)), "catalog": ("catalog", None)}


def filter_cases(cases, filters):
    """The index entries matching every given filter (``{"class": "lens", "catalog": "none", ...}``); refuses an
    unknown value with BAD_ARGUMENT naming the allowed values."""
    known = list_filters()
    selected = list(cases)
    for name, value in filters.items():
        if value is None:
            continue
        key, allowed = known[name]
        if allowed is None:
            if value != "none" and not CATALOG_RELEASE.match(value):
                raise CaseError("BAD_ARGUMENT", f"--catalog {value!r}: give none or a catalog release such as "
                                                "2026-10-28.1")
        elif value not in allowed:
            raise CaseError("BAD_ARGUMENT", f"--{name} {value!r} is not a known value; allowed: {', '.join(allowed)}")

        def matches(entry, key=key, value=value, name=name):
            field = entry.get(key)
            if name == "scale":
                return isinstance(field, dict) and field.get("band") == value
            if name == "catalog":
                return field == (None if value == "none" else value)
            if isinstance(field, list):
                return value in field
            return field == value

        selected = [entry for entry in selected if matches(entry)]
    return selected


# ----------------------------------------------------------------------------------------------- verify

def engine_command():
    """argv prefix of the engine CLI installed with this kit (same interpreter); refuses an engine outside the
    kit's supported range (ENGINE_NOT_INSTALLED / ENGINE_UNSUPPORTED, with the install command)."""
    try:
        _kit_engine.require_engine()
    except _kit_engine.DependencyError as exc:
        raise CaseError(exc.code, str(exc)) from None
    if find_spec("os_optics") is None:
        raise CaseError("ENGINE_NOT_INSTALLED", f"this command needs os-optics: {_kit_engine.install_hint()}")
    return [sys.executable, "-m", "os_optics"]


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


def run_identity(manifest):
    """The images.json ``run`` block of a run directory's manifest.json."""
    manifest = manifest if isinstance(manifest, dict) else {}
    engine_run = manifest.get("engine_run") or {}
    return {"catalog": manifest.get("catalog_version"), "engine": engine_run.get("engine_version"),
            "engine_input_hash": (engine_run.get("input_hash") or {}).get("digest"),
            "project_hash": ((manifest.get("project") or {}).get("project_hash") or {}).get("digest"),
            "scene_content_hash": (manifest.get("optical_view") or {}).get("scene_content_hash_osc1")}


def compare_images_run(recorded, manifest):
    """Differences between the run recorded in images.json and a run's manifest.json: project_hash and
    engine_input_hash (scene_content_hash is recorded for traceability only)."""
    actual = run_identity(manifest)
    recorded = recorded if isinstance(recorded, dict) else {}
    return [{"field": f"images.run.{key}", "expected": recorded.get(key), "actual": actual[key]}
            for key in ("project_hash", "engine_input_hash") if recorded.get(key) != actual[key]]


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
    images = read_images(path)
    if images is not None:
        try:
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            manifest = None
        entry["differences"] += compare_images_run(images.get("run"), manifest)
    entry["result"] = "MISMATCH" if entry["differences"] else "MATCH"
    if update_expected:
        entry["updated"] = write_expected(path, actual, run_dir)
    return entry


def _require_checkout(path, option="--update-expected"):
    if not (Path(path).parent.parent / "pyproject.toml").is_file():
        raise CaseError("NOT_A_CHECKOUT", f"{option} writes into a kit checkout, not an installed wheel")


def write_expected(path, actual, run_dir):
    """Record a verified run as the case baseline: the engine-written project.json of the run (normalized form),
    expected.json and the CASE.md engine / catalog lines. images.json is left as it is."""
    path = Path(path)
    _require_checkout(path)
    shutil.copyfile(Path(run_dir) / "project.json", path / "project.json")
    expected = {"schema": EXPECTED_SCHEMA, "schema_version": EXPECTED_SCHEMA_VERSION, "engine": actual["engine"],
                "catalog": actual["catalog"], "status": actual["status"], "targets": actual["targets"]}
    (path / "expected.json").write_text(normalized_json(expected), encoding="utf-8", newline="\n")
    text = (path / "CASE.md").read_text(encoding="utf-8")
    text = re.sub(r"(?m)^engine:.*$", f"engine: {actual['engine']}", text, count=1)
    text = re.sub(r"(?m)^catalog:.*$", f"catalog: {actual['catalog'] or 'none'}", text, count=1)
    (path / "CASE.md").write_text(text, encoding="utf-8", newline="\n")
    return ["project.json", "expected.json", "CASE.md"]


def _catalog_env(catalog, store, env):
    """Import a catalog release file into a private store; returns {"version", "sha256"} and updates ``env``."""
    catalog = Path(catalog)
    if not catalog.is_file():
        raise CaseError("BAD_ARGUMENT", f"--catalog {catalog}: no such file")
    env.update({"OPENSCI_CATALOG_DIR": str(store), "OPENSCI_CATALOG_CHECK_INTERVAL": "0"})
    code, imported, stderr = _engine(["catalog", "import", str(catalog)], env)
    if code != 0:
        raise CaseError("CATALOG_IMPORT_FAILED", f"os-optics catalog import failed (exit {code}): {stderr}")
    active = (imported or {}).get("active") or {}
    return {"version": active.get("catalog_version"), "sha256": active.get("sha256")}


def _refuse_duplicates(names):
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise CaseError("BAD_ARGUMENT", f"case named more than once: {', '.join(duplicates)}")


def verify(names, *, out=None, catalog=None, root=None, update_expected=False, jobs=1):
    """Verify cases; ``jobs`` > 1 runs up to that many cases at once (the output keeps the order of ``names``).
    A name given twice is refused: both copies would run into the same OUT/NAME directory."""
    if not isinstance(jobs, int) or jobs < 1:
        raise CaseError("BAD_ARGUMENT", "--jobs must be a positive integer")
    names = list(names)
    _refuse_duplicates(names)
    env = dict(os.environ)
    temporary = None
    if out is None:
        temporary = tempfile.mkdtemp(prefix="os-optics-kit-cases-")
        out = temporary
    out = Path(out)
    try:
        catalog_info = None
        if catalog is not None:
            catalog_info = _catalog_env(catalog, out / ".catalog-store", env)

        def one(name):
            return verify_case(name, out, env, root=root, update_expected=update_expected)

        if jobs == 1 or len(names) < 2:
            results = [one(name) for name in names]
        else:
            with ThreadPoolExecutor(max_workers=min(jobs, len(names))) as pool:
                results = list(pool.map(one, names))
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


# ----------------------------------------------------------------------------------------------- images

def _sizes(size, option):
    match = SIZE_PATTERN.match(size)
    if not match:
        raise CaseError("BAD_ARGUMENT", f"{option} {size!r}: give WIDTHxHEIGHT in pixels, such as 960x540")
    width, height = int(match.group(1)), int(match.group(2))
    sizes = []
    for step in SIZE_STEPS:
        candidate = f"{round(width * step)}x{round(height * step)}"
        if candidate not in sizes:
            sizes.append(candidate)
    return sizes


def plan_views(meta, snapshots=None, section=None):
    """The system views to render, in order: ``[(view, argument)]`` with view a snapshot view name, ``camera`` or
    ``section``. Without any view: ``iso``, plus ``section ALL`` for class lens."""
    snapshots = list(snapshots or [])
    if not snapshots and section is None:
        snapshots = [("view", "iso")]
        if (meta or {}).get("class") == "lens":
            section = "ALL"
    plan = []
    for kind, value in snapshots:
        if kind == "camera":
            if not CAMERA_PATTERN.match(value):
                raise CaseError("BAD_ARGUMENT", f"--camera {value!r}: give ANGLE,ELEVATION[,ROLL[,DISTANCE]]")
            if any(view == T.CAMERA_VIEW for view, _ in plan):
                raise CaseError("BAD_ARGUMENT", "give --camera once (it writes images/camera.png)")
            plan.append((T.CAMERA_VIEW, value))
        else:
            if value not in T.SNAPSHOT_VIEWS:
                raise CaseError("BAD_ARGUMENT", f"--view {value!r} is not a snapshot view; allowed: "
                                                f"{', '.join(T.SNAPSHOT_VIEWS)}")
            if any(view == value for view, _ in plan):
                raise CaseError("BAD_ARGUMENT", f"--view {value} is given twice")
            plan.append((value, value))
    if not plan:
        raise CaseError("BAD_ARGUMENT", "give at least one snapshot view (--view or --camera): the READMEs show a "
                                        "snapshot view first")
    if section is not None:
        if not SECTION_ARGUMENT.fullmatch(section):
            raise CaseError("BAD_ARGUMENT", f"--section {section!r}: give an element id or ALL")
        plan.append((T.SECTION_VIEW, section))
    if len(plan) > T.SYSTEM_VIEW_MAX:
        raise CaseError("IMAGE_BUDGET", f"{len(plan)} system views; at most {T.SYSTEM_VIEW_MAX} per case")
    return plan


def _image_file(view, argument):
    if view == T.SECTION_VIEW:
        return "images/section.png" if argument == "ALL" else f"images/section-{argument}.png"
    return f"images/{view}.png"


def default_views(meta):
    """``[(view, argument, size, frame)]`` of a case with no recorded view: ``iso``, plus ``section ALL
    --frame=scene`` for class lens (the views ``contrib case`` gives a new case)."""
    return [(view, argument, SECTION_SIZE if view == T.SECTION_VIEW else SNAPSHOT_SIZE,
             DEFAULT_SECTION_FRAME if view == T.SECTION_VIEW else None) for view, argument in plan_views(meta)]


def _view_options(view, argument, size, frame):
    """The ``os-optics view`` options of one system view, as recorded in images.json: the frame (sections only) is
    always the one token ``--frame=VALUE``."""
    if view == T.SECTION_VIEW:
        return (_option_tokens("--section", argument) + (_option_tokens("--frame", frame) if frame else [])
                + ["--size", size])
    if view == T.CAMERA_VIEW:
        return _option_tokens("--camera", argument) + ["--size", size]
    return ["--views", argument, "--size", size]


def _render_view(run_dir, work, view, argument, sizes, frame, env):
    """Render one system view, smaller when the PNG is over the budget -> (png path, options)."""
    tried = []
    for number, size in enumerate(sizes):
        options = _view_options(view, argument, size, frame)
        snap = work / f"{view}-{number}"
        code, document, stderr = _engine(["view", str(run_dir), "--no-browser", "--snapshot", str(snap)] + options,
                                         env)
        if code != 0:
            raise CaseError("ENGINE_FAILED", f"os-optics view {' '.join(options)} failed (exit {code}): "
                                             f"{stderr or json.dumps(document)[:400]}")
        if view == T.SECTION_VIEW:
            png = ((document or {}).get("sections") or {}).get(argument, {}).get("png")
            candidates = [Path(png)] if png else sorted(snap.glob(f"section-{argument}-*.png"))
        else:
            candidates = [snap / f"{view}.png"]
        if not candidates or not candidates[0].is_file():
            raise CaseError("ENGINE_FAILED", f"os-optics view {' '.join(options)} wrote no PNG")
        size_bytes = candidates[0].stat().st_size
        if size_bytes <= IMAGE_MAX_BYTES:
            return candidates[0], options
        tried.append(f"{size}: {size_bytes} bytes")
    raise CaseError("IMAGE_TOO_LARGE", f"{view} stays over {IMAGE_MAX_BYTES} bytes at every size tried "
                                       f"({'; '.join(tried)}); give a smaller --size")


def _prepare_run(path, run, work, env):
    """A run of the case's project.json: the given RUN (checked) or a fresh ``project run`` under ``work``."""
    if run is not None:
        run = Path(run)
        project = run / "project.json"
        if not project.is_file() or project.read_bytes() != (path / "project.json").read_bytes():
            raise CaseError("RUN_NOT_FROM_CASE", f"{run.as_posix()} is not a run of {path.name}: its project.json "
                                                 "differs from the case's project.json")
        if not (run / "manifest.json").is_file():
            raise CaseError("RUN_NOT_FROM_CASE", f"{run.as_posix()} has no manifest.json: not a run directory")
        return run
    run = work / "run"
    code, _document, stderr = _engine(["project", "run", str(path / "project.json"), "--out-dir", str(run)], env,
                                      json_out=False)
    if code not in (0, 1, 2, 3) or not (run / "manifest.json").is_file():
        raise CaseError("ENGINE_FAILED", f"os-optics project run failed (exit {code}): {stderr}")
    return run


def write_images_record(path, records, run_block):
    document = {"images": records, "project_sha256": sha256_file(Path(path) / "project.json"), "run": run_block,
                "schema": IMAGES_SCHEMA, "schema_version": IMAGES_SCHEMA_VERSION}
    (Path(path) / IMAGES_FILE).write_text(normalized_json(document), encoding="utf-8", newline="\n")
    return document


def _figures(document):
    records = (document or {}).get("images")
    return [r for r in records if isinstance(r, dict) and r.get("kind") == T.FIGURE_KIND] \
        if isinstance(records, list) else []


def _case_image(path, file):
    """True when ``file`` names a PNG directly under the case's images/ directory (``images/NAME.png``, resolving
    inside it): the only recorded files ``cases images`` overwrites, deletes or hashes. A symbolic link (the images/
    directory or the file itself) never qualifies, so nothing outside the case is reached through one."""
    if not isinstance(file, str) or not IMAGE_FILE.fullmatch(file):
        return False
    images = Path(path) / "images"
    target = Path(path) / file
    if images.is_symlink() or target.is_symlink():
        return False
    return target.resolve().parent == Path(path).resolve() / "images"


def render_case_images(name, *, run=None, snapshots=None, section=None, size=None, section_size=None, frame=None,
                       catalog=None, root=None):
    """Render the case's system views with the engine (browser-free snapshots) and record them in images.json:
    every previous system view is replaced, figures are kept after the new views.

    Without any view option (``snapshots``, ``section``, ``size``, ``section_size`` and ``frame`` all unset) the
    views recorded in images.json are rendered again, each with its own recorded view, size and frame, in recorded
    order; only when none is recorded are the defaults used (``default_views``). With a view option the views are
    planned from the options (``plan_views``), as given.

    Only files matching ``images/NAME.png`` directly under the case's images/ directory are ever overwritten,
    deleted or hashed: any other recorded file is left alone and reported under ``skipped``."""
    path = case_dir(name, root)
    _require_checkout(path, "cases images")
    meta = None
    try:
        meta = parse_front_matter((path / "CASE.md").read_text(encoding="utf-8"))[0]
    except (OSError, ValueError):
        meta = None
    previous = read_images(path)
    if previous is None and (path / IMAGES_FILE).exists():
        raise CaseError("IMAGES_RECORD", f"{IMAGES_FILE} is not a UTF-8 JSON object: fix it, or delete it to start from "
                                         f"the default views, before running os-optics-kit cases images {name}")
    explicit = bool(snapshots) or any(value is not None for value in (section, size, section_size, frame))
    if explicit:
        snapshot_sizes = _sizes(size or SNAPSHOT_SIZE, "--size")
        section_sizes = _sizes(section_size or SECTION_SIZE, "--section-size")
        plan = [(view, argument, section_sizes if view == T.SECTION_VIEW else snapshot_sizes,
                 frame if view == T.SECTION_VIEW else None) for view, argument in plan_views(meta, snapshots, section)]
    else:
        try:
            views = recorded_views(previous, strict=False)
        except ValueError as exc:
            raise CaseError("IMAGES_RECORD", f"{exc}: the recorded views cannot be rendered again; give the views "
                                             f"explicitly (os-optics-kit cases images {name} --view iso ...)") from None
        views = views or default_views(meta)
        plan = [(view, argument, _sizes(view_size, "--section-size" if view == T.SECTION_VIEW else "--size"),
                 view_frame) for view, argument, view_size, view_frame in views]
        if len(plan) > T.SYSTEM_VIEW_MAX:
            raise CaseError("IMAGE_BUDGET", f"{len(plan)} system views; at most {T.SYSTEM_VIEW_MAX} per case")
    planned = [_image_file(view, argument) for view, argument, _sizes_, _frame in plan]
    if len({file.lower() for file in planned}) != len(planned):
        raise CaseError("IMAGES_RECORD", f"{IMAGES_FILE} records a view file twice ({', '.join(planned)})")
    linked = [name_ for name_ in ["images"] + planned if (path / name_).is_symlink()]
    if linked:
        raise CaseError("BAD_ARGUMENT", f"{', '.join(linked)} is a symbolic link; replace it with a regular "
                                        "directory or file before rendering")
    skipped = []
    figures = []
    for figure in _figures(previous):
        if _case_image(path, figure.get("file")):
            figures.append(figure)
        else:
            skipped.append(figure.get("file"))
    folded = {file.lower() for file in planned}
    clash = sorted(figure["file"] for figure in figures if figure["file"].lower() in folded)
    if clash:
        raise CaseError("BAD_ARGUMENT", f"{', '.join(clash)} is recorded as a figure; rename the figure (draw it to "
                                        "another images/NAME.png and record that with --figure) before rendering "
                                        "that view")
    if len(plan) + len(figures) > T.IMAGE_MAX_COUNT:
        raise CaseError("IMAGE_BUDGET", f"{len(plan)} system views and {len(figures)} figures; at most "
                                        f"{T.IMAGE_MAX_COUNT} images per case")
    engine_command()
    env = dict(os.environ)
    with tempfile.TemporaryDirectory(prefix="os-optics-kit-images-") as tmp:
        work = Path(tmp)
        if catalog is not None:
            _catalog_env(catalog, work / ".catalog-store", env)
        run_dir = _prepare_run(path, run, work, env)
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        rendered = []
        for view, argument, sizes, view_frame in plan:
            png, options = _render_view(run_dir, work, view, argument, sizes, view_frame, env)
            rendered.append((view, _image_file(view, argument), png, options))
        removed = []
        for record in (previous or {}).get("images") or []:
            if isinstance(record, dict) and record.get("kind") == T.SYSTEM_VIEW_KIND:
                old = record.get("file")
                if old in planned:
                    continue
                if not _case_image(path, old):
                    skipped.append(old)
                elif (path / old).is_file():
                    (path / old).unlink()
                    removed.append(old)
        (path / "images").mkdir(exist_ok=True)
        records = []
        for view, file, png, options in rendered:
            shutil.copyfile(png, path / file)
            records.append({"file": file, "kind": T.SYSTEM_VIEW_KIND, "options": options,
                            "sha256": sha256_file(path / file), "view": view})
        kept = []
        for figure in figures:
            if (path / figure["file"]).is_file():
                kept.append({**figure, "sha256": sha256_file(path / figure["file"])})
        document = write_images_record(path, records + kept, run_identity(manifest))
    recorded = {r["file"] for r in document["images"]}
    unrecorded = [f for f in case_files(path) if f.startswith("images/") and f not in recorded]
    warnings = []
    if (meta or {}).get("class") == "lens" and not any(view == T.SECTION_VIEW for view, *_rest in plan):
        warnings.append("class lens needs a section view: add --section ALL")
    if unrecorded:
        warnings.append(f"images/ holds unrecorded files {unrecorded}; remove them or record them as figures")
    if skipped:
        warnings.append(f"{IMAGES_FILE} named files outside images/NAME.png, left untouched and dropped from the "
                        f"record: {skipped}")
    return {"status": "WRITTEN", "case": name, "images": document["images"], "run": document["run"],
            "removed": removed, "skipped": skipped, "unrecorded": unrecorded, "warnings": warnings}


def _is_view_file(file):
    """True for a file name a system view writes: images/<snapshot view>.png, images/camera.png, images/section.png
    or images/section-<ELEMENT>.png."""
    stem = file.removeprefix("images/").removesuffix(".png").lower()
    return stem in T.SNAPSHOT_VIEWS or stem in (T.CAMERA_VIEW, T.SECTION_VIEW) or stem.startswith("section-")


def record_figure(name, figure, script, *, root=None):
    """Record (or refresh) one figure: a PNG under images/ drawn by a .py script of the case."""
    path = case_dir(name, root)
    _require_checkout(path, "cases images")
    document = read_images(path)
    if document is None or not isinstance(document.get("images"), list) or not isinstance(document.get("run"), dict):
        raise CaseError("IMAGES_RECORD_MISSING", f"{name} has no readable {IMAGES_FILE}; render the system views "
                                                 f"first: os-optics-kit cases images {name}")
    if not any(isinstance(r, dict) and r.get("kind") == T.SYSTEM_VIEW_KIND for r in document["images"]):
        raise CaseError("IMAGES_RECORD_MISSING", f"{name} records no system view in {IMAGES_FILE}; render the system "
                                                 f"views first: os-optics-kit cases images {name}")
    figure = Path(figure).as_posix().removeprefix("./")
    if not IMAGE_FILE.fullmatch(figure) or not _case_image(path, figure) or not (path / figure).is_file():
        raise CaseError("BAD_ARGUMENT", f"--figure {figure!r}: give an existing PNG file images/NAME.png of the case")
    if _is_view_file(figure):
        raise CaseError("BAD_ARGUMENT", f"--figure {figure}: the file name is the one a system view writes "
                                        "(images/<view>.png, images/camera.png, images/section*.png); choose "
                                        "another name")
    data = (path / figure).read_bytes()
    if not data.startswith(PNG_SIGNATURE):
        raise CaseError("BAD_ARGUMENT", f"--figure {figure} is not a PNG file")
    if len(data) > IMAGE_MAX_BYTES:
        raise CaseError("IMAGE_TOO_LARGE", f"{figure} is {len(data)} bytes; at most {IMAGE_MAX_BYTES}")
    script = Path(script).as_posix().removeprefix("./")
    if not script.endswith(".py") or script not in case_files(path):
        raise CaseError("BAD_ARGUMENT", f"--script {script!r}: give a .py file of the case (path relative to it)")
    records = [r for r in document["images"] if isinstance(r, dict)]
    entry = {"file": figure, "kind": T.FIGURE_KIND, "script": script, "sha256": hashlib.sha256(data).hexdigest()}
    existing = [i for i, r in enumerate(records) if r.get("file") == figure]
    if existing and records[existing[0]].get("kind") != T.FIGURE_KIND:
        raise CaseError("BAD_ARGUMENT", f"{figure} is recorded as a system view; choose another file name")
    if existing:
        records[existing[0]] = entry
    else:
        records.append(entry)
    figures = sum(1 for r in records if r.get("kind") == T.FIGURE_KIND)
    if figures > T.FIGURE_MAX or len(records) > T.IMAGE_MAX_COUNT:
        raise CaseError("IMAGE_BUDGET", f"{figures} figures and {len(records)} images; at most {T.FIGURE_MAX} figures "
                                        f"and {T.IMAGE_MAX_COUNT} images per case")
    written = write_images_record(path, records, document["run"])
    return {"status": "WRITTEN", "case": name, "figure": figure, "images": written["images"]}


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

class _Snapshot(argparse.Action):
    """--view and --camera append to one ordered list, so views are rendered in the order given."""

    def __call__(self, parser, namespace, values, option_string=None):
        items = list(getattr(namespace, "snapshots", None) or [])
        items.append(("camera" if option_string == "--camera" else "view", values))
        namespace.snapshots = items


def _positive_int(text):
    try:
        value = int(text)
    except ValueError:
        value = 0
    if value < 1:
        raise argparse.ArgumentTypeError(f"{text!r} is not a positive integer")
    return value


def _parser():
    parser = ArgumentParser(prog="os-optics-kit cases", description=__doc__,
                            formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", type=Path, help="a kit checkout to use instead of the installed cases "
                                                  "(also OS_OPTICS_KIT_ROOT)")
    listing = sub.add_parser("list", parents=[common], help="print the case index (compact JSON), filtered")
    listing.add_argument("--class", dest="case_class", metavar="CLASS", help="only cases of this class")
    listing.add_argument("--domain", help="only cases with this domain")
    listing.add_argument("--provenance", help="only cases with this provenance")
    listing.add_argument("--publication", help="only cases with this publication basis")
    listing.add_argument("--scale", help="only cases of this scale band")
    listing.add_argument("--tag", help="only cases with this tag")
    listing.add_argument("--catalog", metavar="RELEASE|none", help="only cases pinning this catalog release (none: "
                                                                   "catalog-free cases)")
    sub.add_parser("vocabulary", parents=[common], help="print the classes, domains, provenance, publication, scale "
                                                        "bands, tags and image budget (compact JSON)")
    show = sub.add_parser("show", parents=[common], help="print a case's CASE.md")
    show.add_argument("name")
    copy = sub.add_parser("copy", parents=[common], help="copy a case directory to DEST/NAME")
    copy.add_argument("name")
    copy.add_argument("--dest", type=Path, required=True, help="destination directory")
    copy.add_argument("--force", action="store_true", help="overwrite different existing files")
    check = sub.add_parser("verify", parents=[common], help="run cases with the installed engine and compare with "
                                                            "expected.json and the run recorded in images.json")
    check.add_argument("names", nargs="*", metavar="NAME")
    check.add_argument("--all", action="store_true", help="every case")
    check.add_argument("--out", type=Path, help="keep the runs in OUT/NAME/run (default: a temporary directory)")
    check.add_argument("--catalog", type=Path, help="import this catalog release file into a private store under "
                                                    "OUT first (no download)")
    check.add_argument("--jobs", type=_positive_int, default=1, metavar="N",
                       help="verify up to N cases at once (default 1); the output keeps the given order")
    check.add_argument("--update-expected", action="store_true",
                       help="write the runs' outcomes to expected.json and CASE.md (kit checkout only)")
    index = sub.add_parser("index", parents=[common], help="generate (--write) or validate (--check) cases.json and "
                                                           "every case")
    mode = index.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    images = sub.add_parser(
        "images", parents=[common], formatter_class=argparse.RawDescriptionHelpFormatter,
        help="render a case's system views with the engine (no browser) and record them in images.json",
        description="Render the case's system views with the engine's browser-free snapshot, 'os-optics view RUN "
                    "--no-browser --snapshot DIR', and record them in images.json (kit checkout only). RUN is a fresh "
                    "'os-optics project run' of the case's project.json unless --run names one. Without any view "
                    "option (--view, --camera, --section, --size, --section-size, --frame) the views recorded in "
                    "images.json are rendered again, each with its recorded size and frame, in recorded order; "
                    "with nothing recorded: --view iso, plus --section ALL --frame=scene for a lens case. With view "
                    "options, views are rendered in the order given, the section last (no view: --view iso, plus "
                    "--section ALL for a lens case). Every previous system view is replaced; figures are kept. A PNG "
                    "over 400 KiB is redrawn smaller. With --figure and --script: record or refresh one figure drawn "
                    "by a case script.")
    images.add_argument("name")
    images.add_argument("--run", type=Path, help="a run directory of this case's project.json (default: a fresh run)")
    images.add_argument("--view", dest="snapshots", action=_Snapshot, metavar="VIEW",
                        help="a snapshot view: " + ", ".join(T.SNAPSHOT_VIEWS) + " (repeatable)")
    images.add_argument("--camera", dest="snapshots", action=_Snapshot, metavar="A,E[,R[,D]]",
                        help="a camera stated by angles to the main beam's axis (images/camera.png)")
    images.add_argument("--section", metavar="ELEMENT|ALL", help="the 2D section through an element's axis "
                                                                 "(ALL: the whole system; images/section.png)")
    images.add_argument("--size", metavar="WxH", help=f"snapshot image size (default {SNAPSHOT_SIZE})")
    images.add_argument("--section-size", metavar="WxH", help=f"section image size (default {SECTION_SIZE})")
    images.add_argument("--frame", help="what the section shows: optics (engine default), scene or a box "
                                        "--frame=X0,Y0,Z0,X1,Y1,Z1 (one token: a negative box needs the '=' form); "
                                        "recorded as one token --frame=VALUE")
    images.add_argument("--catalog", type=Path, help="import this catalog release file into a private store first "
                                                     "(catalog cases, no download)")
    images.add_argument("--figure", metavar="images/X.png", help="record or refresh this figure")
    images.add_argument("--script", metavar="SCRIPT.py", help="the case script that draws --figure")
    return parser


def _images_command(args):
    if args.figure is not None or args.script is not None:
        if args.figure is None or args.script is None:
            raise CaseError("BAD_ARGUMENT", "--figure and --script go together")
        rendering = [args.run, args.snapshots, args.section, args.size, args.section_size, args.frame, args.catalog]
        if any(value is not None for value in rendering):
            raise CaseError("BAD_ARGUMENT", "--figure records one figure; render system views in a separate call")
        return record_figure(args.name, args.figure, args.script)
    return render_case_images(args.name, run=args.run, snapshots=args.snapshots, section=args.section,
                              size=args.size, section_size=args.section_size, frame=args.frame, catalog=args.catalog)


def run(argv):
    args = _parser().parse_args(argv)
    if args.root is not None:
        if not (args.root / "cases").is_dir():
            raise CaseError("BAD_ARGUMENT", f"--root {args.root.as_posix()} has no cases/ directory")
        os.environ[ROOT_ENV] = str(args.root.resolve())
    if args.command == "list":
        document = load_index()
        filters = {"class": args.case_class, "domain": args.domain, "provenance": args.provenance,
                   "publication": args.publication, "scale": args.scale, "tag": args.tag, "catalog": args.catalog}
        if any(value is not None for value in filters.values()):
            document = {**document, "cases": filter_cases(document["cases"], filters)}
        _emit(document)
        return EXIT_OK
    if args.command == "vocabulary":
        _emit(T.vocabulary())
        return EXIT_OK
    if args.command == "show":
        _write((case_dir(args.name) / "CASE.md").read_text(encoding="utf-8"))
        return EXIT_OK
    if args.command == "copy":
        _emit(copy_case(args.name, args.dest, force=args.force))
        return EXIT_OK
    if args.command == "images":
        _emit(_images_command(args))
        return EXIT_OK
    if args.command == "verify":
        if bool(args.all) == bool(args.names):
            raise CaseError("BAD_ARGUMENT", "give case names or --all")
        names = case_names() if args.all else args.names
        _refuse_duplicates(names)
        for name in names:
            case_dir(name)
        engine_command()
        document = verify(names, out=args.out, catalog=args.catalog, update_expected=args.update_expected,
                          jobs=args.jobs)
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
        generated = None  # a case without readable CASE.md / expected.json / project.json: already reported above
    if generated is not None and current != generated:
        problems.append({"case": None, "code": "INDEX_STALE",
                         "message": "cases.json differs from the cases; run 'os-optics-kit cases index --write'"})
    _emit({"status": "OK" if not problems else "PROBLEMS", "cases": len(case_names()), "root": data_root().as_posix(),
           "problems": problems})
    return EXIT_OK if not problems else EXIT_DIFFERENT


def main(argv=None):
    previous_root = os.environ.get(ROOT_ENV)
    try:
        return run(list(sys.argv[1:] if argv is None else argv))
    except CaseError as exc:
        print(json.dumps({"status": "ERROR", "error": {"code": exc.code, "message": str(exc)}},
                         separators=(",", ":")), file=sys.stderr)
        return EXIT_USAGE
    except SystemExit as exc:  # --help
        return int(exc.code or 0)
    finally:  # --root applies to this call only
        if previous_root is None:
            os.environ.pop(ROOT_ENV, None)
        else:
            os.environ[ROOT_ENV] = previous_root


if __name__ == "__main__":
    raise SystemExit(main())
