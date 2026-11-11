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

1. `os-optics-kit contrib case design.json --name my-case --class free-space --domain gaussian-beam
   --tags beam-expander,focusing --dry-run` normalizes the design, runs `os-optics project check`, `project run`
   and `results targets`, renders the system views with the engine (`os-optics view RUN --no-browser --snapshot
   DIR --views iso`, plus `--section ALL --frame=scene` for a `lens` case), and writes `contrib-case-my-case/` with
   `case/` (project.json, CASE.md, README.md, README.zh-CN.md, expected.json, images.json, images/), the patch,
   `PR_BODY.md` and `bundle.json`. The class, domain and tag values are listed by `os-optics-kit cases vocabulary`;
   `--provenance`, `--publication` and `--source` record where third-party material comes from. The patch adds the
   case, `cases.json` and the README case tables (`os-optics-kit cases index --write` and
   `python scripts/case_tables.py --write` in a temporary clone), so the pull request passes the index checks.
2. The agent replaces the generated summary and "What it shows" in `case/CASE.md` and the intro of
   `case/README.md` and `case/README.zh-CN.md` with what the design demonstrates. The language line under each
   title (`English · [简体中文](README.zh-CN.md)` and `[English](README.md) · 简体中文`) stays: the two READMEs link
   each other.
3. `os-optics-kit contrib case --refresh contrib-case-my-case` re-runs the engine, checks the case format
   (CASE.md within 700 tokens, required sections, classification and tags, images recorded in `images.json` and
   shown in both READMEs, the READMEs linked to each other) and rebuilds the patch.

Case images come from the engine and need no browser and no `render` extra. The kit's `render` and `present`
commands make presentation-grade figures with Chrome or Chromium; a case never needs them. If the command warns
`IMAGE_NOT_RENDERED` (status `INCOMPLETE`: the bundle holds the case files but no patch yet), run the engine
command itself on a run of the case (`os-optics view RUN --no-browser --snapshot DIR --views iso`) to read its
error, fix the cause and `--refresh`. The refresh renders the recorded views again, plus every view that was not
rendered before and the views the class needs (a snapshot view first, the section of a `lens`), and updates the
images of both READMEs: the image lines are rebuilt in `images.json` order, keeping your captions. A figure drawn
by a case script is recorded with `os-optics-kit cases images NAME --figure images/X.png --script SCRIPT.py`;
result tables stay Markdown tables.

To change an existing case by hand, edit `cases/NAME/` in a kit checkout and re-record its views with
`os-optics-kit cases images NAME`: without view options it renders again the views recorded in its `images.json`,
each with its recorded options (a new case gets `iso`, plus `--section ALL --frame=scene` for a lens). Keep both
READMEs showing exactly the recorded images (a system view first) and linking each other, then run
`os-optics-kit cases index --write`, `python scripts/case_tables.py --write` (the README case tables) and
`os-optics-kit cases index --check`, and commit `cases.json`, `README.md` and `README.zh-CN.md` with the case.

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
