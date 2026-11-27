"""Placeholder for ``os-optics-kit field-points`` (replaced by its implementation on this branch)."""

from __future__ import annotations

from . import workflow_common as wc

PROG = "os-optics-kit field-points"


def main(argv: list[str] | None = None) -> int:
    parser = wc.ArgumentParser(prog=PROG, description="Field points of a Project at stated angles or heights: chief-ray hit point and angle, first-order comparison and optical MTF at a fixed image plane.")
    parser.add_argument("args", nargs="*")

    def workflow():
        parser.parse_args(argv)
        raise wc.WorkflowError("NOT_IMPLEMENTED", "field-points is not implemented yet")

    return wc.emit(workflow)
