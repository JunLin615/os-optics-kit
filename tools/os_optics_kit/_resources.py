"""Locate the packaged data: skills, cases and their indexes.

An installed wheel carries them under ``os_optics_kit/_data``; a source checkout (editable install, tests) reads them
from the repository root.
"""

from __future__ import annotations

from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent


def data_root() -> Path:
    """Directory holding ``skills/``, ``cases/``, ``skills.json``, ``cases.json`` and ``baseline.json``."""
    packaged = _PACKAGE_DIR / "_data"
    if (packaged / "skills.json").is_file():
        return packaged
    checkout = _PACKAGE_DIR.parents[1]
    if (checkout / "skills.json").is_file() or (checkout / "pyproject.toml").is_file():
        return checkout
    raise FileNotFoundError("os-optics-kit data not found: reinstall the package")


def skills_dir() -> Path:
    return data_root() / "skills"


def cases_dir() -> Path:
    return data_root() / "cases"
