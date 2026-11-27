"""Placeholder for ``os-optics-kit optimize`` (replaced by its implementation on this branch)."""

from __future__ import annotations

from . import workflow_common as wc

PROG = "os-optics-kit optimize"


def main(argv: list[str] | None = None) -> int:
    parser = wc.ArgumentParser(prog=PROG, description="Bounded optimization of a Project from a JSON spec: variables, objectives and constraints, with a history and the best feasible Project.")
    parser.add_argument("args", nargs="*")

    def workflow():
        parser.parse_args(argv)
        raise wc.WorkflowError("NOT_IMPLEMENTED", "optimize is not implemented yet")

    return wc.emit(workflow)
