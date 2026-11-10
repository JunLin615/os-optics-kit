"""The engine requirement of this kit and the errors of missing optional dependencies.

``ENGINE_REQUIREMENT`` is the single place the kit states which ``os-optics`` releases it supports; it equals the
dependency specifier in ``pyproject.toml`` (a test keeps them equal). Nothing here imports the engine.
"""

from __future__ import annotations

import operator

ENGINE_DISTRIBUTION = "os-optics"
ENGINE_REQUIREMENT = ">=0.13,<0.14"
PLOT_EXTRA = "os-optics-kit[plot]"

_OPERATORS = (("==", operator.eq), ("!=", operator.ne), (">=", operator.ge), ("<=", operator.le),
              (">", operator.gt), ("<", operator.lt))


def version_tuple(text):
    """Leading numeric release of a version string: '0.8.0', '0.8.0rc1', '0.8.0+abc' -> (0, 8, 0)."""
    parts = []
    for piece in str(text).split("+")[0].split("."):
        digits = ""
        for char in piece:
            if not char.isdigit():
                break
            digits += char
        if not digits:
            break
        parts.append(int(digits))
        if digits != piece:
            break
    return tuple(parts)


def _padded(left, right):
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)), right + (0,) * (width - len(right))


def _clauses(requirement):
    clauses = []
    for clause in requirement.split(","):
        clause = clause.strip()
        for symbol, compare in _OPERATORS:
            if clause.startswith(symbol):
                clauses.append((symbol, compare, version_tuple(clause[len(symbol):].strip())))
                break
        else:
            raise ValueError(f"unsupported version clause {clause!r} in {requirement!r}")
    return clauses


def satisfies(version, requirement=ENGINE_REQUIREMENT):
    """Whether ``version`` meets every clause of ``requirement`` (release numbers only, e.g. '>=0.13,<0.14')."""
    if version is None:
        return False
    installed = version_tuple(version)
    for _symbol, compare, bound in _clauses(requirement):
        if not compare(*_padded(installed, bound)):
            return False
    return True


def minimum_version(requirement=ENGINE_REQUIREMENT):
    """Lower bound of ``requirement`` as a tuple (the '>=' clause), e.g. (0, 12)."""
    for symbol, _compare, bound in _clauses(requirement):
        if symbol in (">=", "=="):
            return bound
    return ()


def engine_version():
    """Installed ``os-optics`` version, or None when it is not installed (never imports the engine)."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(ENGINE_DISTRIBUTION)
    except PackageNotFoundError:
        return None


def install_hint():
    return f"python -m pip install \"{ENGINE_DISTRIBUTION}{ENGINE_REQUIREMENT}\""


class DependencyError(RuntimeError):
    """A missing or unsupported optional dependency, reported as a structured error with a stable ``code``."""

    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.details = details

    def as_dict(self):
        return {"code": self.code, "message": str(self), **self.details}


def require_engine():
    """Return the installed engine version; raise ``DependencyError`` unless it meets ``ENGINE_REQUIREMENT``."""
    installed = engine_version()
    if installed is None:
        raise DependencyError("ENGINE_NOT_INSTALLED",
                              f"this command needs {ENGINE_DISTRIBUTION}{ENGINE_REQUIREMENT}: {install_hint()}",
                              requirement=f"{ENGINE_DISTRIBUTION}{ENGINE_REQUIREMENT}", installed=None)
    if not satisfies(installed):
        raise DependencyError("ENGINE_UNSUPPORTED",
                              f"this kit supports {ENGINE_DISTRIBUTION}{ENGINE_REQUIREMENT}; installed: {installed} "
                              f"({install_hint()})",
                              requirement=f"{ENGINE_DISTRIBUTION}{ENGINE_REQUIREMENT}", installed=installed)
    return installed


def require_matplotlib():
    """Import check for the plot extra; raise ``DependencyError`` (PLOT_EXTRA_MISSING) when matplotlib is absent."""
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        raise DependencyError("PLOT_EXTRA_MISSING", matplotlib_missing_message(), extra=PLOT_EXTRA) from None


def matplotlib_missing_message():
    return f"matplotlib is not installed; plots need the plot extra: python -m pip install \"{PLOT_EXTRA}\""


def dependency_error(exc):
    """The structured error of ``exc`` when it is a missing or unsupported dependency, else None.

    A ``ModuleNotFoundError`` / ``ImportError`` of the engine becomes ENGINE_NOT_INSTALLED (or ENGINE_UNSUPPORTED
    when the installed release is outside the requirement), one of matplotlib becomes PLOT_EXTRA_MISSING."""
    if isinstance(exc, DependencyError):
        return exc.as_dict()
    if not isinstance(exc, ImportError):
        return None
    name = (getattr(exc, "name", None) or "").split(".")[0]
    if name == "os_optics":
        installed = engine_version()
        if installed is None:
            return DependencyError("ENGINE_NOT_INSTALLED",
                                   f"this command needs {ENGINE_DISTRIBUTION}{ENGINE_REQUIREMENT}: {install_hint()}",
                                   requirement=f"{ENGINE_DISTRIBUTION}{ENGINE_REQUIREMENT}", installed=None).as_dict()
        return DependencyError("ENGINE_UNSUPPORTED",
                               f"{exc}; this kit supports {ENGINE_DISTRIBUTION}{ENGINE_REQUIREMENT} "
                               f"(installed: {installed})",
                               requirement=f"{ENGINE_DISTRIBUTION}{ENGINE_REQUIREMENT}", installed=installed).as_dict()
    if name == "matplotlib":
        return DependencyError("PLOT_EXTRA_MISSING", matplotlib_missing_message(), extra=PLOT_EXTRA).as_dict()
    return None
