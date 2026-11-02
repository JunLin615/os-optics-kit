"""``os-optics-kit contrib``: prepare a contribution to the kit as a PR-ready patch or a contribution bundle.

``contrib case PROJECT.json --name NAME``: a finished design becomes ``cases/NAME/`` (normalized project.json,
CASE.md, README.md with engine-rendered images, expected.json), verified by the engine on this device.
``contrib tool HELPER.py --name NAME``: a helper with ``main(argv) -> int`` becomes a kit command with its skill page
and tests.

Both write a bundle directory (the files, a ``git format-patch`` patch against your kit checkout, PR_BODY.md and
bundle.json) and print one JSON object. ``--open-pr`` opens the pull request on GitHub with your own ``gh`` login;
``--dry-run`` stays on this device. Pull requests and their review on GitHub are the contribution record.
"""

from __future__ import annotations

import sys

USAGE = """usage: os-optics-kit contrib {case,tool} ...

  case PROJECT.json --name NAME [--title T] [--summary S] [--tools a,b] [--tags x,y]
       [--repo KIT_CHECKOUT] [--base REF] [--out BUNDLE] [--catalog FILE] [--dry-run] [--open-pr] [--force]
  case --refresh BUNDLE [--repo ...] [--dry-run] [--open-pr]
  tool HELPER.py --name NAME [--command CMD] [--module MODULE] [--description D]
       [--repo KIT_CHECKOUT] [--base REF] [--out BUNDLE] [--dry-run] [--open-pr] [--force]
  tool --refresh BUNDLE [--repo ...] [--dry-run] [--open-pr]

Prepare a case or a tool contribution as a PR-ready patch and bundle. 'os-optics-kit contrib case --help' and
'os-optics-kit contrib tool --help' for details. Exit codes: 0 ready, 1 verification failed, 4 usage error or refusal.
"""


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        print(USAGE.rstrip())
        return 0
    kind, rest = args[0], args[1:]
    if kind == "case":
        from .case import main as run
    elif kind == "tool":
        from .tool import main as run
    else:
        print(f"os-optics-kit contrib: unknown kind {kind!r}\n\n{USAGE.rstrip()}", file=sys.stderr)
        return 4
    return run(rest)
