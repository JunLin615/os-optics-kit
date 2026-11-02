# Contributing with an agent

os-optics-kit grows through pull requests on GitHub. Two commands prepare a contribution on your own device, so
the agent you already work with can do the whole job: `os-optics-kit contrib case` turns a finished design into a
case, `os-optics-kit contrib tool` turns a helper script into a kit command. Their skill pages are what the agent
reads first:

```sh
os-optics-kit skills show contrib-case
os-optics-kit skills show contrib-tool
```

## What you need

- `os-optics-kit` (it installs `os-optics`).
- Git with your name and email configured. Each contribution is one commit with a DCO `Signed-off-by` line in
  your name.
- A clone of the kit to patch against: `git clone https://github.com/JunLin615/os-optics-kit`. Run the commands
  inside it, or pass `--repo PATH`.
- To open the pull request from the command: the GitHub CLI `gh`, logged in with your own account
  (`gh auth login`). Without it, the command stops at a bundle you can submit later.

## Contribute a case

A case is a design that runs: a ProjectDocumentV2 with its targets, verified by the engine.

1. `os-optics-kit contrib case design.json --name my-case --tags tag-a,tag-b --dry-run`
   normalizes the design, runs `os-optics project check`, `project run` and `results targets`, renders two views
   with `os-optics view --snapshot`, and writes `contrib-case-my-case/` with `case/` (project.json, CASE.md,
   README.md, expected.json, images/), the patch, `PR_BODY.md` and `bundle.json`.
2. The agent replaces the generated summary and "What it shows" in `case/CASE.md` and the intro of
   `case/README.md` with what the design demonstrates.
3. `os-optics-kit contrib case --refresh contrib-case-my-case` re-runs the engine, checks the case format
   (CASE.md within 700 tokens, required sections, at most 4 images of 400 KB) and rebuilds the patch.

The case records the engine version and the catalog release its components pin; `expected.json` holds the run
status and every target assessment, which `os-optics-kit cases verify` compares on every later run.

## Contribute a tool

A tool is a helper used beyond one design. It needs `main(argv) -> int` with an argparse `--help`, and imports
`os_optics` inside the functions that use it.

1. `os-optics-kit contrib tool my_helper.py --name my-helper --dry-run` copies it to
   `tools/os_optics_kit/my_helper.py`, registers `os-optics-kit my-helper`, writes `skills/my-helper/SKILL.md`
   from its `--help` and `tests/test_my_helper.py`, and runs those tests in a temporary clone.
2. The agent completes the skill page (when not to use, outputs, a real example) and adds tests of the helper's
   behaviour under `tool/tests/`.
3. `os-optics-kit contrib tool --refresh contrib-tool-my-helper` re-runs the tests and rebuilds the patch.

Logic that belongs to one case stays in that case directory.

## Submit

- `--open-pr` (with or after `--refresh`): forks `JunLin615/os-optics-kit` under your account, pushes the branch
  `contrib/case-NAME` or `contrib/tool-NAME`, and opens the pull request with `PR_BODY.md`.
- Or apply the patch yourself: `git am 0001-*.patch` in your fork, push, and open the pull request on GitHub.
- Without the `gh` CLI: attach the patch and `PR_BODY.md` to a new issue at
  <https://github.com/JunLin615/os-optics-kit/issues>. `git am` keeps you as the commit author.

Every command prints one JSON object (`status`, `bundle`, `patch`, `verification`, `warnings`, `next_step`);
refusals have `status: ERROR` with `error.code`, `error.problems` and `error.hint`. Exit codes: 0 ready,
1 verification failed, 4 refused. `--dry-run` keeps everything on your device.

## Review

The pull request is where review, discussion and changes happen; its history is the record of the contribution.
The repository's checks run on it, and maintainers may ask for edits: make them on the pull request's branch in
your fork.
