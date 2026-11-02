"""Locate the packaged data: skills, cases and their indexes.

An installed wheel carries them under ``os_optics_kit/_data``; a source checkout (editable install, tests) reads them
from the repository root.
"""

from __future__ import annotations

import os
from pathlib import Path

#: A kit checkout to use instead of the installed data (``--root`` of ``cases`` / ``skills`` sets it).
ROOT_ENV = "OS_OPTICS_KIT_ROOT"
_PACKAGE_DIR = Path(__file__).resolve().parent


def data_root() -> Path:
    """Directory holding ``skills/``, ``cases/``, ``skills.json``, ``cases.json`` and ``baseline.json``: the checkout
    named by ``OS_OPTICS_KIT_ROOT`` when set, else the installed data, else the source checkout of this package."""
    override = os.environ.get(ROOT_ENV)
    if override:
        return Path(override)
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
