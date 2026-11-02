"""List, show and install the kit's vendor-neutral agent skills; build and check their index (skills.json).

Usage:
  os-optics-kit skills list                         the skill index (compact JSON): load this first
  os-optics-kit skills show NAME                    print skills/NAME/SKILL.md
  os-optics-kit skills install --dest DIR [--only a,b] [--force]
                                                    copy skills/<name>/ to DIR/<name>/ and write DIR/skills.json
  os-optics-kit skills index --check [--root DIR]   validate every skill and compare skills.json with the skills
  os-optics-kit skills index --write [--root DIR]   validate and (re)generate skills.json

A skill is a directory skills/<name>/ holding SKILL.md: flat front matter (name, description, version, command,
requires, optional kind) and the sections When to use, When not to use, Inputs, Outputs, Minimal example, Limits,
Version, in this order. Budget: ceil(characters / 4) <= 900 per SKILL.md; description <= 300 characters.

install is idempotent: a file with the same content is reported unchanged; an existing file with other content is
refused (nothing is written) unless --force; files of DIR that the kit does not provide are never touched.
DIR/skills.json is the index of the kit skills present in DIR (paths relative to DIR); it is rewritten on every
install, and an existing DIR/skills.json that is not such an index is refused unless --force.

Exit codes: 0 done / index current, 1 index stale or a skill invalid, 4 usage error or refused install.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

from . import _frontmatter as fm
from ._engine import ENGINE_DISTRIBUTION, ENGINE_REQUIREMENT
from ._resources import data_root
from ._version import __version__

PROG = "os-optics-kit skills"
SCHEMA = "os-optics-kit.skills"
SCHEMA_VERSION = 1
INDEX_FILE = "skills.json"
SKILL_FILE = "SKILL.md"
REQUIRED_KEYS = ("name", "description", "version", "command", "requires")
OPTIONAL_KEYS = ("kind",)
KINDS = ("tool", "contribution", "guide")
SECTIONS = ("When to use", "When not to use", "Inputs", "Outputs", "Minimal example", "Limits", "Version")
MAX_NAME = 40
MAX_DESCRIPTION = 300
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
SEMVER = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
#: names of agent hosts and host-specific protocols that a vendor-neutral skill does not mention
HOST_TERMS = ("MCP", "Claude", "Anthropic", "OpenAI", "ChatGPT", "Codex", "Copilot", "Gemini", "Cursor")
NON_TOOL_COMMANDS = ("cases", "skills", "contrib")
EXIT_OK, EXIT_FAILED, EXIT_USAGE = 0, 1, 4


class SkillsError(Exception):
    """A refused request with a stable ``code`` (printed as a JSON error, exit 4)."""

    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.details = details

    def as_dict(self):
        return {"code": self.code, "message": str(self), **self.details}


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise SkillsError("BAD_ARGUMENT", f"{self.prog}: {message}")


# --------------------------------------------------------------------------------------------- reading skills

def tool_commands():
    """The CLI tool commands (every one needs a skill of the same name)."""
    from .cli import COMMANDS

    return [name for name in COMMANDS if name not in NON_TOOL_COMMANDS]


def _kind(fields):
    if "kind" in fields:
        return fields["kind"]
    command = fields.get("command", "")
    if command == "none":
        return "guide"
    return "contribution" if command.startswith("os-optics-kit contrib") else "tool"


def check_skill(directory):
    """``(entry, problems)`` of one skill directory: its index entry (None when unreadable) and every problem."""
    directory = Path(directory)
    name = directory.name
    path = directory / SKILL_FILE
    if not path.is_file():
        return None, [f"{name}: no {SKILL_FILE}"]
    data = path.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return None, [f"{name}: {SKILL_FILE} is not UTF-8 ({exc.reason})"]
    try:
        fields, body = fm.split(text)
    except fm.FrontMatterError as exc:
        return None, [f"{name}: front matter: {exc}"]
    problems = [f"{name}: {p}" for p in fm.check_keys(fields, REQUIRED_KEYS, OPTIONAL_KEYS)]
    problems += [f"{name}: {p}" for p in fm.check_sections(body, SECTIONS)]
    problems += [f"{name}: {p}" for p in _check_fields(name, fields)]
    if b"\r" in data:
        problems.append(f"{name}: {SKILL_FILE} must use LF line endings")
    tokens = fm.token_estimate(text)
    if tokens > fm.BUDGETS[SKILL_FILE]:
        problems.append(f"{name}: {SKILL_FILE} is {tokens} tokens (ceil(characters/4)), budget "
                        f"{fm.BUDGETS[SKILL_FILE]}")
    for term in HOST_TERMS:
        if re.search(rf"\b{re.escape(term)}\b", text):
            problems.append(f"{name}: names the agent host or protocol {term!r}; skills are vendor-neutral")
    entry = {"name": fields.get("name", name), "kind": _kind(fields), "description": fields.get("description", ""),
             "version": fields.get("version", ""), "command": fields.get("command", ""),
             "requires": fields.get("requires", ""), "path": f"skills/{name}/{SKILL_FILE}", "tokens": tokens,
             "sha256": hashlib.sha256(data).hexdigest()}
    return entry, problems


def _check_fields(directory_name, fields):
    from .cli import COMMANDS

    problems = []
    name = fields.get("name")
    if name is not None:
        if name != directory_name:
            problems.append(f"name {name!r} differs from the directory name")
        if not NAME.fullmatch(name) or len(name) > MAX_NAME:
            problems.append(f"name {name!r} must be kebab-case, at most {MAX_NAME} characters")
    description = fields.get("description")
    if description is not None and len(description) > MAX_DESCRIPTION:
        problems.append(f"description is {len(description)} characters, at most {MAX_DESCRIPTION}")
    version = fields.get("version")
    if version is not None and not SEMVER.fullmatch(version):
        problems.append(f"version {version!r} is not MAJOR.MINOR.PATCH")
    kind = fields.get("kind")
    if kind is not None and kind not in KINDS:
        problems.append(f"kind {kind!r} must be one of {', '.join(KINDS)}")
    command = fields.get("command")
    if command is not None and command != "none":
        words = command.split()
        if len(words) < 2 or words[0] != "os-optics-kit" or words[1] not in COMMANDS:
            problems.append(f"command {command!r} must be 'none' or 'os-optics-kit <command> ...' with a command "
                            f"of the CLI ({', '.join(COMMANDS)})")
    requires = fields.get("requires")
    if requires is not None and requires != "none":
        for item in (part.strip() for part in requires.split(";")):
            if item.startswith(ENGINE_DISTRIBUTION) and not item.startswith(f"{ENGINE_DISTRIBUTION}-kit"):
                if item != f"{ENGINE_DISTRIBUTION}{ENGINE_REQUIREMENT}":
                    problems.append(f"requires {item!r}: the engine requirement of this kit is "
                                    f"{ENGINE_DISTRIBUTION}{ENGINE_REQUIREMENT}")
            elif not item:
                problems.append("requires has an empty item (items are separated by '; ')")
    return problems


def collect(root):
    """``(entries, problems)`` of every skill under ``root/skills`` (entries sorted by name), plus kit-level checks:
    every CLI tool command has a skill of the same name whose command is ``os-optics-kit <name>``, and the
    contributor template (``root/templates/SKILL.md``, when present) is well formed."""
    root = Path(root)
    skills_dir = root / "skills"
    entries, problems = [], []
    if not skills_dir.is_dir():
        return [], [f"no skills directory at {skills_dir.as_posix()}"]
    for directory in sorted(p for p in skills_dir.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))):
        entry, found = check_skill(directory)
        problems += found
        if entry is not None:
            entries.append(entry)
    by_name = {entry["name"]: entry for entry in entries}
    for command in tool_commands():
        entry = by_name.get(command)
        if entry is None:
            problems.append(f"CLI tool command {command!r} has no skill skills/{command}/")
        elif entry["command"] != f"os-optics-kit {command}":
            problems.append(f"{command}: command must be 'os-optics-kit {command}', got {entry['command']!r}")
    template = root / "templates" / SKILL_FILE
    if template.is_file():
        problems += [f"templates/{SKILL_FILE}: {p}" for p in check_template(template.read_text(encoding="utf-8"))]
    return sorted(entries, key=lambda e: e["name"]), problems


def check_template(text):
    """Problems of the contributor template: parseable front matter with the skill keys and the skill sections."""
    try:
        fields, body = fm.split(text)
    except fm.FrontMatterError as exc:
        return [f"front matter: {exc}"]
    return fm.check_keys(fields, REQUIRED_KEYS, OPTIONAL_KEYS) + fm.check_sections(body, SECTIONS)


def build_index(entries):
    return {"schema": SCHEMA, "schema_version": SCHEMA_VERSION, "kit_version": __version__,
            "token_estimate": fm.TOKEN_ESTIMATE, "budgets": dict(fm.BUDGETS), "skills": list(entries)}


def index_text(index):
    """The checked-in text of an index: 2-space indented JSON, keys in schema order, LF, final newline."""
    return json.dumps(index, indent=2, ensure_ascii=False) + "\n"


def load_index(root=None):
    path = Path(root or data_root()) / INDEX_FILE
    if not path.is_file():
        raise SkillsError("INDEX_MISSING", f"{path.as_posix()} not found; generate it with "
                          "'os-optics-kit skills index --write'")
    return json.loads(path.read_text(encoding="utf-8"))


def skill_names(index):
    return [entry["name"] for entry in index["skills"]]


# --------------------------------------------------------------------------------------------- commands

def cmd_list(args):
    index = load_index(args.root)
    print(json.dumps(index, ensure_ascii=False, separators=(",", ":")))
    return EXIT_OK


def cmd_show(args):
    root = Path(args.root or data_root())
    names = skill_names(load_index(root))
    if args.name not in names:
        raise SkillsError("SKILL_UNKNOWN", f"no skill {args.name!r}", known=names)
    sys.stdout.write((root / "skills" / args.name / SKILL_FILE).read_text(encoding="utf-8"))
    return EXIT_OK


def cmd_index(args):
    root = Path(args.root or data_root())
    entries, problems = collect(root)
    text = index_text(build_index(entries))
    path = root / INDEX_FILE
    current = path.read_text(encoding="utf-8") if path.is_file() else None
    if problems:
        _print({"status": "INVALID", "skills": len(entries), "problems": problems})
        return EXIT_FAILED
    if args.check:
        if current != text:
            _print({"status": "STALE", "skills": len(entries), "path": path.as_posix(),
                    "problems": [f"{INDEX_FILE} is {'missing' if current is None else 'out of date'}; "
                                 "run 'os-optics-kit skills index --write'"]})
            return EXIT_FAILED
        _print({"status": "CURRENT", "skills": len(entries), "path": path.as_posix()})
        return EXIT_OK
    if current == text:
        _print({"status": "UNCHANGED", "skills": len(entries), "path": path.as_posix()})
        return EXIT_OK
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    _print({"status": "WRITTEN", "skills": len(entries), "path": path.as_posix()})
    return EXIT_OK


def install(dest, *, only=None, force=False, root=None):
    """Install skills into ``dest`` (see the module docstring); returns the summary document."""
    root = Path(root or data_root())
    dest = Path(dest)
    index = load_index(root)
    by_name = {entry["name"]: entry for entry in index["skills"]}
    names = list(by_name) if not only else list(dict.fromkeys(only))
    unknown = [name for name in names if name not in by_name]
    if unknown:
        raise SkillsError("SKILL_UNKNOWN", f"unknown skill(s): {', '.join(unknown)}", known=list(by_name))
    if dest.exists() and not dest.is_dir():
        raise SkillsError("DEST_NOT_A_DIRECTORY", f"{dest.as_posix()} exists and is not a directory")
    plan = []  # (target, content bytes, relative path)
    for name in names:
        source = root / "skills" / name
        for file in sorted(p for p in source.rglob("*") if p.is_file()):
            relative = Path(name) / file.relative_to(source)
            plan.append((dest / relative, file.read_bytes(), relative.as_posix()))
    present = [name for name in by_name if name in names or (dest / name / SKILL_FILE).is_file()]
    dest_index = dict(index, skills=[dict(by_name[name], path=f"{name}/{SKILL_FILE}") for name in present])
    index_bytes = index_text(dest_index).encode("utf-8")
    written, unchanged, conflicts = [], [], []
    for target, content, relative in plan:
        if target.is_file():
            if target.read_bytes() == content:
                unchanged.append(relative)
                continue
            conflicts.append(relative)
        elif target.exists():
            raise SkillsError("DEST_PATH_NOT_A_FILE", f"{target.as_posix()} exists and is not a file; nothing was "
                              "written")
        elif any(parent.exists() and not parent.is_dir() for parent in target.parents):
            raise SkillsError("DEST_PATH_NOT_A_DIRECTORY", f"a parent of {target.as_posix()} is not a directory; "
                              "nothing was written")
    index_path = dest / INDEX_FILE
    if index_path.exists() and not index_path.is_file():
        raise SkillsError("DEST_PATH_NOT_A_FILE", f"{index_path.as_posix()} exists and is not a file; nothing was "
                          "written")
    if index_path.exists() and not _is_skill_index(index_path):
        conflicts.append(INDEX_FILE)
    if conflicts and not force:
        raise SkillsError("DEST_FILE_DIFFERS", "existing files differ from the kit's skills; nothing was written "
                          "(use --force to overwrite them)", dest=dest.as_posix(), paths=conflicts)
    for target, content, relative in plan:
        if relative in unchanged:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        written.append(relative)
    if index_path.is_file() and index_path.read_bytes() == index_bytes:
        unchanged.append(INDEX_FILE)
    else:
        dest.mkdir(parents=True, exist_ok=True)
        index_path.write_bytes(index_bytes)
        written.append(INDEX_FILE)
    return {"status": "INSTALLED" if written else "UNCHANGED", "dest": dest.as_posix(), "skills": names,
            "written": written, "unchanged": unchanged, "overwritten": [p for p in conflicts if p in written]}


def _is_skill_index(path):
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("schema") == SCHEMA
    except (OSError, ValueError, AttributeError):
        return False


def cmd_install(args):
    only = [name.strip() for name in args.only.split(",") if name.strip()] if args.only else None
    _print(install(args.dest, only=only, force=args.force, root=args.root))
    return EXIT_OK


def _print(document):
    print(json.dumps(document, ensure_ascii=False, separators=(",", ":")))


def build_parser():
    parser = _Parser(prog=PROG, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="action", metavar="{list,show,install,index}")
    sub.required = True
    root_help = "Kit directory holding skills/ and skills.json (default: the installed package data or checkout)"
    p = sub.add_parser("list", help="Print the skill index (compact JSON)")
    p.add_argument("--root", type=Path, help=root_help)
    p.set_defaults(handler=cmd_list)
    p = sub.add_parser("show", help="Print one skill's SKILL.md")
    p.add_argument("name", help="Skill name")
    p.add_argument("--root", type=Path, help=root_help)
    p.set_defaults(handler=cmd_show)
    p = sub.add_parser("install", help="Copy skills to a directory")
    p.add_argument("--dest", type=Path, required=True, help="Directory to install into (created when missing)")
    p.add_argument("--only", help="Comma-separated skill names (default: all)")
    p.add_argument("--force", action="store_true", help="Overwrite existing files that differ")
    p.add_argument("--root", type=Path, help=root_help)
    p.set_defaults(handler=cmd_install)
    p = sub.add_parser("index", help="Check or write skills.json")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Validate the skills; fail when skills.json is stale")
    mode.add_argument("--write", action="store_true", help="Validate the skills and write skills.json")
    p.add_argument("--root", type=Path, help=root_help)
    p.set_defaults(handler=cmd_index)
    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        args = build_parser().parse_args(argv)
        return args.handler(args)
    except SystemExit as exc:  # --help
        return exc.code if isinstance(exc.code, int) else EXIT_OK if exc.code is None else EXIT_USAGE
    except (SkillsError, OSError, ValueError) as exc:
        error = exc.as_dict() if isinstance(exc, SkillsError) else {"code": type(exc).__name__, "message": str(exc)}
        print(json.dumps({"error": error}, ensure_ascii=False), file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
