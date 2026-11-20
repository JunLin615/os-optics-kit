"""Generate or check the case tables of README.md and README.zh-CN.md.

The tables sit between the lines ``<!-- cases:start -->`` and ``<!-- cases:end -->``. They are generated from
``cases.json`` and the case vocabulary (``tools/os_optics_kit/case_taxonomy.py``): one ``###`` sub-heading per class
that has cases (vocabulary order, localized label), one row per case sorted by name. "What it shows" is the first
``# `` heading of the case's README.md (English table) or README.zh-CN.md (Chinese table).

  python scripts/case_tables.py --write    rewrite both tables
  python scripts/case_tables.py --check    exit 1 when a table differs from what --write would write

Standard library only.
"""

from __future__ import annotations

import argparse
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import sys
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
START, END = "<!-- cases:start -->", "<!-- cases:end -->"
TREE_URL = "https://github.com/JunLin615/os-optics-kit/tree/main/cases/"
FORMAT_URL = "https://github.com/JunLin615/os-optics-kit/blob/main/docs/case-format.md"
#: README file -> (language, case README to read the title from)
READMES = {"README.md": ("en", "README.md"), "README.zh-CN.md": ("zh-CN", "README.zh-CN.md")}
TEXT: dict[str, dict[str, Any]] = {
    "en": {"columns": ("Case", "What it shows", "Domain", "Scale", "Catalog"), "join": ", ",
           "yes": "yes", "no": "no",
           "note": "The classes, domains and tags are listed by `os-optics-kit cases vocabulary` and defined in the "
                   f"[case format]({FORMAT_URL})."},
    "zh-CN": {"columns": ("案例", "内容", "领域", "规模", "目录"), "join": "、", "yes": "是", "no": "否",
              "note": "类别、领域和标签由 `os-optics-kit cases vocabulary` 列出，定义见"
                      f"[案例格式（英文）]({FORMAT_URL})。"},
}


def taxonomy(root: Path = ROOT) -> ModuleType:
    """The case vocabulary module, imported by path (no install needed)."""
    spec = spec_from_file_location("case_taxonomy", root / "tools" / "os_optics_kit" / "case_taxonomy.py")
    if spec is None or spec.loader is None:
        raise ImportError("tools/os_optics_kit/case_taxonomy.py not found")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def readme_title(path: Path) -> str:
    """The first ``# `` heading of a Markdown file (outside code fences), or '' when there is none."""
    if not path.is_file():
        return ""
    fenced = False
    for line in path.read_text(encoding="utf-8").split("\n"):
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and line.startswith("# "):
            return line[2:].strip()
    return ""


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_tables(index: dict[str, Any], language: str, root: Path = ROOT, vocabulary: ModuleType | None = None) -> str:
    """The text between the markers for one language (leading and trailing newline included)."""
    T = vocabulary or taxonomy(root)
    words = TEXT[language]
    readme = READMES["README.md" if language == "en" else "README.zh-CN.md"][1]
    by_class: dict[str, list[dict[str, Any]]] = {}
    for entry in index.get("cases", []):
        by_class.setdefault(entry.get("class"), []).append(entry)
    blocks = []
    for value in T.values(T.CLASSES):
        entries = sorted(by_class.get(value, []), key=lambda e: e["name"])
        if not entries:
            continue
        lines = [f"### {T.label(T.CLASSES, value, language)}", "",
                 "| " + " | ".join(words["columns"]) + " |", "|" + " --- |" * len(words["columns"])]
        for entry in entries:
            name = entry["name"]
            title = readme_title(root / "cases" / name / readme)
            domains = words["join"].join(T.label(T.DOMAINS, d, language) for d in entry.get("domain", []))
            band = (entry.get("scale") or {}).get("band", "")
            scale = T.label(T.SCALE_BANDS, band, language) if band else ""
            catalog = words["yes"] if entry.get("catalog") else words["no"]
            lines.append(f"| [{name}]({TREE_URL}{name}) | {_cell(title)} | {_cell(domains)} | {scale} | {catalog} |")
        blocks.append("\n".join(lines))
    blocks.append(words["note"])
    return "\n" + "\n\n".join(blocks) + "\n"


def unclassified(index: dict[str, Any], root: Path = ROOT) -> list[str]:
    T = taxonomy(root)
    known = set(T.values(T.CLASSES))
    return [entry.get("name") for entry in index.get("cases", []) if entry.get("class") not in known]


def replace_block(text: str, block: str) -> str:
    """``text`` with the part between the markers replaced by ``block``; raises ValueError without the markers."""
    start, end = text.find(START), text.find(END)
    if start < 0 or end < start or text.count(START) != 1 or text.count(END) != 1:
        raise ValueError(f"needs exactly one '{START}' line followed by one '{END}' line")
    return text[:start + len(START)] + block + text[end:]


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(prog="case_tables.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="rewrite the tables of README.md and README.zh-CN.md")
    mode.add_argument("--check", action="store_true", help="exit 1 when a table is stale")
    args = parser.parse_args(argv)
    index = json.loads((root / "cases.json").read_text(encoding="utf-8"))
    vocabulary = taxonomy(root)
    missing = unclassified(index, root)
    if missing:
        print(f"case_tables: {len(missing)} cases in cases.json have no known class and are left out: "
              f"{', '.join(str(m) for m in missing)}", file=sys.stderr)
    stale = []
    for name, (language, _readme) in READMES.items():
        path = root / name
        text = path.read_text(encoding="utf-8")
        try:
            updated = replace_block(text, render_tables(index, language, root, vocabulary))
        except ValueError as exc:
            print(f"case_tables: {name}: {exc}", file=sys.stderr)
            return 1
        if updated != text:
            if args.write:
                path.write_text(updated, encoding="utf-8", newline="\n")
            stale.append(name)
    if args.write:
        print(json.dumps({"status": "WRITTEN", "files": stale}))
        return 0
    if stale:
        print(f"case_tables: the case tables of {', '.join(stale)} are stale; run 'python scripts/case_tables.py "
              "--write'", file=sys.stderr)
        return 1
    print(json.dumps({"status": "OK"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
