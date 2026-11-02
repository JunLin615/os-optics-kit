"""Shared helpers for the external-tool (interop) examples.

The interop examples require os-optics 0.10.0 or newer. Each topic keeps its
own third-party requirements file; nothing here imports a third-party solver.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import hashlib
import json
import math
from pathlib import Path
import platform
import sys

MINIMUM_ENGINE = (0, 10, 0)
# Outcome words of these scripts. They describe a comparison with an external or independent reference (or a
# completed production step), never an OpenSci run status.
EXIT_CODES = {"WITHIN_TOLERANCE": 0, "COMPLETED": 0, "OUT_OF_TOLERANCE": 1, "UNEXPECTED_RESULT": 1,
              "NO_FEASIBLE_RESULT": 2, "INCOMPLETE_SAMPLE_SET": 2}
ERROR_EXIT = 4


def comparison_status(within):
    return "WITHIN_TOLERANCE" if within else "OUT_OF_TOLERANCE"


class ArgumentParser(argparse.ArgumentParser):
    """Report usage errors as JSON errors (exit 4) like the other repository scripts."""

    def error(self, message):
        raise ValueError(message)


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


def engine_version():
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("os-optics")
    except PackageNotFoundError:
        return None


def require_engine():
    """Return the installed engine version; refuse anything older than 0.10.0."""
    installed = engine_version()
    if installed is None or version_tuple(installed) < MINIMUM_ENGINE:
        needed = ".".join(str(part) for part in MINIMUM_ENGINE)
        raise RuntimeError(
            f"this example requires os-optics {needed} or newer (installed: {installed or 'none'}); "
            "install the topic's requirements file in a fresh virtual environment"
        )
    return installed


def package_versions(*names):
    """Installed versions of the named distributions (None when absent)."""
    from importlib.metadata import PackageNotFoundError, version

    found = {}
    for name in names:
        try:
            found[name] = version(name)
        except PackageNotFoundError:
            found[name] = None
    return found


def environment(*names):
    """Python, OS and package versions for a report."""
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "os": f"{platform.system()} {platform.release()}",
        "machine": platform.machine(),
        "packages": package_versions("os-optics", *names),
    }


def decimal_text(value, digits=9):
    """Canonical plain decimal for Project DecimalString fields (no exponent, no trailing zeros)."""
    if not math.isfinite(value):
        raise ValueError(f"not a finite number: {value}")
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, document):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=1, ensure_ascii=True, allow_nan=False) + "\n", encoding="utf-8")
    return path


def json_safe(value):
    """Replace non-finite floats (not representable in strict JSON) by None, recursively."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def emit(workflow):
    """Run ``workflow``; print one compact JSON summary on stdout, diagnostics on stderr.

    ``summary["outcome"]``: ``WITHIN_TOLERANCE`` / ``COMPLETED`` -> exit 0, ``OUT_OF_TOLERANCE`` /
    ``UNEXPECTED_RESULT`` -> exit 1; any error -> exit 4 (JSON error on stderr).
    """
    try:
        with redirect_stdout(sys.stderr):
            summary = workflow()
        code = EXIT_CODES[summary["outcome"]]
        output = json.dumps(json_safe(summary), ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    except Exception as exc:  # noqa: BLE001 - every failure becomes a JSON error
        error = {"error": {"code": getattr(exc, "code", type(exc).__name__), "message": str(exc)}}
        print(json.dumps(error, ensure_ascii=True), file=sys.stderr)
        return ERROR_EXIT
    print(output)
    return code


def run_identity(results):
    """Identity of one Engine run from the public results API (never rewritten by external tools)."""
    identity = results.identity()
    return {key: identity.get(key) for key in (
        "engine_version", "engine_build_id", "project_hash", "input_hash", "result_hash", "catalog_version",
    )}


def quaternion(z_axis, x_axis):
    """Unit quaternion (w, x, y, z) of the rotation taking local +Z to z_axis and local +X to x_axis (orthonormal)."""
    z = [float(v) for v in z_axis]
    x = [float(v) for v in x_axis]
    y = [z[1] * x[2] - z[2] * x[1], z[2] * x[0] - z[0] * x[2], z[0] * x[1] - z[1] * x[0]]
    m = [[x[0], y[0], z[0]], [x[1], y[1], z[1]], [x[2], y[2], z[2]]]
    trace = m[0][0] + m[1][1] + m[2][2]
    if trace > 0:
        s = 2.0 * math.sqrt(trace + 1.0)
        q = [0.25 * s, (m[2][1] - m[1][2]) / s, (m[0][2] - m[2][0]) / s, (m[1][0] - m[0][1]) / s]
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = 2.0 * math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2])
        q = [(m[2][1] - m[1][2]) / s, 0.25 * s, (m[0][1] + m[1][0]) / s, (m[0][2] + m[2][0]) / s]
    elif m[1][1] > m[2][2]:
        s = 2.0 * math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2])
        q = [(m[0][2] - m[2][0]) / s, (m[0][1] + m[1][0]) / s, 0.25 * s, (m[1][2] + m[2][1]) / s]
    else:
        s = 2.0 * math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1])
        q = [(m[1][0] - m[0][1]) / s, (m[0][2] + m[2][0]) / s, (m[1][2] + m[2][1]) / s, 0.25 * s]
    norm = math.sqrt(sum(v * v for v in q))
    return [v / norm for v in q]


def pose(position, z_axis, x_axis=(0.0, 0.0, 1.0)):
    """Project pose: local +Z along z_axis, local +X along x_axis made perpendicular to it (default design +Z)."""
    z = [float(v) for v in z_axis]
    norm = math.sqrt(sum(v * v for v in z))
    z = [v / norm for v in z]
    x = [float(v) for v in x_axis]
    dot = sum(a * b for a, b in zip(x, z))
    x = [a - dot * b for a, b in zip(x, z)]
    norm = math.sqrt(sum(v * v for v in x))
    if norm < 1e-9:
        raise ValueError("x_axis must not be parallel to z_axis")
    return {"position_mm": [float(v) for v in position], "quaternion_wxyz": quaternion(z, [v / norm for v in x])}
