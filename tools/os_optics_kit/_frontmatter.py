"""Flat front matter of SKILL.md and CASE.md files, and the documented token estimate.

Format: the file starts with a line ``---``, then one ``key: value`` line per key, then a line ``---``; the body
follows. Keys are lower-case identifiers (``[a-z][a-z0-9_]*``), each given once; values are single-line text, never
empty, and are not interpreted (no YAML: no nesting, lists, quotes or comments). Any other line is an error.
Dependency-free on purpose: skills, cases and contribution tooling all parse with this module.
"""

from __future__ import annotations

import math
import re

DELIMITER = "---"
KEY = re.compile(r"[a-z][a-z0-9_]*")
#: token budgets per file name; the estimate is ``token_estimate`` (documented approximation, deterministic)
BUDGETS = {"SKILL.md": 900, "AGENT_GUIDE.md": 1500, "CASE.md": 700}
TOKEN_ESTIMATE = "ceil(characters/4)"


class FrontMatterError(ValueError):
    """A malformed front matter block; ``line`` is the 1-based line number (None for the whole file)."""

    def __init__(self, message, line=None):
        super().__init__(message if line is None else f"line {line}: {message}")
        self.line = line


def token_estimate(text):
    """Token estimate of a text: ceil(number of characters / 4)."""
    return math.ceil(len(text) / 4)


def split(text):
    """``(fields, body)`` of a document with front matter; raises ``FrontMatterError``.

    Line endings may be LF or CRLF; ``body`` is the text after the closing delimiter line, with LF line endings."""
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0] != DELIMITER:
        raise FrontMatterError(f"the file must start with a '{DELIMITER}' line", 1)
    fields = {}
    for number, line in enumerate(lines[1:], start=2):
        if line == "" and number == len(lines):
            break  # end of the file
        if line == DELIMITER:
            if not fields:
                raise FrontMatterError("the front matter is empty", number)
            return fields, "\n".join(lines[number:])
        key, sep, value = line.partition(":")
        if not sep:
            raise FrontMatterError(f"expected 'key: value', got {line!r}", number)
        if not KEY.fullmatch(key):
            raise FrontMatterError(f"invalid key {key!r} (lower-case letters, digits, '_'; no indentation)", number)
        if not value.startswith(" ") or not value.strip():
            raise FrontMatterError(f"key {key!r}: expected one space and a non-empty value after ':'", number)
        if key in fields:
            raise FrontMatterError(f"duplicate key {key!r}", number)
        fields[key] = value.strip()
    raise FrontMatterError(f"no closing '{DELIMITER}' line")


def parse(text):
    """The front matter fields of ``text`` (see ``split``)."""
    return split(text)[0]


def check_keys(fields, required, optional=()):
    """Problems (strings) for missing required keys and unknown keys, in a stable order."""
    problems = [f"missing key {key!r}" for key in required if key not in fields]
    allowed = set(required) | set(optional)
    problems += [f"unknown key {key!r}" for key in fields if key not in allowed]
    return problems


def headings(body, level=2):
    """The titles of the Markdown headings of one level (``## Title`` for level 2), in order (outside code fences)."""
    marker = "#" * level + " "
    titles, fenced = [], False
    for line in body.split("\n"):
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and line.startswith(marker):
            titles.append(line[len(marker):].strip())
    return titles


def check_sections(body, required):
    """Problems unless every ``required`` level-2 heading appears exactly once and in the given order."""
    found = headings(body)
    problems = [f"missing section '## {title}'" for title in required if title not in found]
    problems += [f"section '## {title}' appears more than once" for title in required if found.count(title) > 1]
    if not problems:
        order = [title for title in found if title in required]
        if order != list(required):
            problems.append("sections out of order: expected " + ", ".join(required))
    return problems


def render(fields):
    """The front matter block (with both delimiters and a final newline) of ``fields``, in their order."""
    lines = [DELIMITER]
    for key, value in fields.items():
        text = str(value)
        if not KEY.fullmatch(key):
            raise FrontMatterError(f"invalid key {key!r}")
        if not text.strip() or "\n" in text or "\r" in text:
            raise FrontMatterError(f"key {key!r}: the value must be one non-empty line")
        lines.append(f"{key}: {text.strip()}")
    lines.append(DELIMITER)
    return "\n".join(lines) + "\n"
