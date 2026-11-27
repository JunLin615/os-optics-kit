"""Placeholder for ``os-optics-kit mtf`` (replaced by its implementation on this branch)."""

from __future__ import annotations

from . import workflow_common as wc

PROG = "os-optics-kit mtf"


def main(argv: list[str] | None = None) -> int:
    parser = wc.ArgumentParser(prog=PROG, description="Tangential and sagittal optical MTF of an os-optics imaging analysis as a table, a JSON document and one figure per line.")
    parser.add_argument("args", nargs="*")

    def workflow():
        parser.parse_args(argv)
        raise wc.WorkflowError("NOT_IMPLEMENTED", "mtf is not implemented yet")

    return wc.emit(workflow)
