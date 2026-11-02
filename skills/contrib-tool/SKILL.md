---
name: contrib-tool
description: Package a helper script as an os-optics-kit command with its skill page and tests, verified and exported as a PR-ready git patch. Use when a helper is useful beyond one design or case.
version: 0.1.0
command: os-optics-kit contrib tool
requires: none
kind: contribution
---

# contrib-tool

## When to use

- A helper that reads, transforms, exports or checks os-optics Projects or run results and serves more than one
  design; it becomes `os-optics-kit COMMAND` for every kit user.
- After editing a tool bundle's SKILL.md or tests: `--refresh BUNDLE` re-runs the checks and rebuilds the patch.

## When not to use

- Logic specific to one case: keep it in that case (`workflow.py` next to its `project.json`).
- Anything that simulates optics itself: the engine does that (`os-optics project run`).
- A helper that needs packages beyond os-optics and matplotlib: ship it with a case and its pinned requirements.

## Inputs

- `HELPER.py`: defines `main(argv) -> int` with an argparse `--help`; imports `os_optics` inside functions, not at
  module level, so `--help` works without the engine.
- `--name` (kebab-case skill name; also the command unless `--command`), optional `--module`, `--description`
  (at most 300 characters; default: the docstring's first sentence), `--base REF`, `--out BUNDLE`.
- `--repo`: a kit clone (default: the clone around the current directory; without one, the installed package is used
  for the tests and no patch is written).
- `--dry-run`: nothing leaves the device. `--open-pr`: fork, push and open the pull request with the user's `gh` login.

## Outputs

One JSON object on stdout: `status`, `bundle`, `patch`, `files`, `requires`, `verification` (`--help` with and
without the engine, test count and result, skills index), `warnings`, `next_step`.

The bundle directory: `tool/tools/os_optics_kit/MODULE.py`, `tool/skills/NAME/SKILL.md`, `tool/tests/test_MODULE.py`,
`0001-feat-tools-add-the-COMMAND-command.patch` (one signed-off commit that also registers the command in `cli.py` and regenerates `skills.json`),
`PR_BODY.md`, `bundle.json`.

Exit codes: 0 ready, 1 `--help` or the tests failed, 4 refusal (`error.code`, `error.problems`, `error.hint`).

## Minimal example

```sh
os-optics-kit contrib tool power_budget_csv.py --name power-budget-csv --dry-run
# edit contrib-tool-power-budget-csv/tool/skills/power-budget-csv/SKILL.md, add tests
os-optics-kit contrib tool --refresh contrib-tool-power-budget-csv --open-pr
```

`--open-pr` publishes under the user's GitHub account: confirm with the user first.

## Limits

- Generated tests cover import, registration, `--help` (with and without the engine) and doctests; add tests of the
  helper's real behaviour before submitting.
- SKILL.md: at most 900 tokens; sections When to use, When not to use, Inputs, Outputs, Minimal example, Limits,
  Version.
- The command name must be new; tools never import cases.

## Version

0.1.0: first version (os-optics-kit 0.1.0).
