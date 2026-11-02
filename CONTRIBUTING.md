# Contributing to os-optics-kit

os-optics-kit collects tools, engine-reproducible cases and agent skills for
[OS Optics](https://github.com/JunLin615/os-optics). Contributions are welcome as pull requests to this
repository. You open them from your own fork with your own GitHub account, by hand or through your agent.

Engine problems (the `os-optics` commands, simulation results, the optical catalog) go to the
[os-optics issue tracker](https://github.com/JunLin615/os-optics/issues).

## What you can contribute

| Contribution | Lives in | Starts from |
| --- | --- | --- |
| Case: a design that runs on the verified engine and catalog, with its expected outcomes | `cases/<name>/` | `os-optics-kit contrib case` or [the case format](docs/case-format.md) |
| Tool: a shared helper, exposed as an `os-optics-kit` command | `tools/os_optics_kit/` | `os-optics-kit contrib tool` |
| Skill: the page an agent reads to use a tool or follow a workflow | `skills/<name>/SKILL.md` | `templates/SKILL.md` |
| Documentation, fixes, CI | anywhere | an issue or a pull request |

A helper that only one case uses stays in that case's directory; tools never depend on cases.

## With an agent

The contribution commands do the preparation for you or your agent:

```bash
os-optics-kit contrib case my_design.json --name my-case --dry-run   # check and preview locally
os-optics-kit contrib case my_design.json --name my-case --open-pr  # fork, push and open the pull request
os-optics-kit contrib tool my_helper.py --name my-tool --dry-run
```

`contrib case` normalizes the Project, pins the engine and catalog versions, runs `project check`, `project run`
and `results targets`, renders the images, writes `CASE.md`, `README.md` and `expected.json`, and produces a
patch with a pull-request description. `--open-pr` uses your own `gh` login; without it you get a bundle directory
with the patch, which you can apply and push yourself. Step-by-step instructions, including what to tell your
agent, are in [Contributing with an agent](docs/contributing-with-an-agent.md).

## By hand

1. Fork the repository and create a branch.
2. Set up an environment with the engine version in [`baseline.json`](baseline.json):

   ```bash
   python -m venv .venv
   . .venv/bin/activate            # Windows: .venv\Scripts\activate
   python -m pip install --upgrade pip
   python -m pip install "os-optics[protected]==0.12.0" matplotlib
   python -m pip install --no-deps -e .
   python -m pip install --group dev
   ```

3. Make the change, then run the checks CI runs:

   ```bash
   python scripts/check_repo.py
   ruff check .
   mypy
   python -m pytest
   os-optics-kit skills index --check     # after changing skills: --write, then commit skills.json
   os-optics-kit cases index --check      # after changing cases:  --write, then commit cases.json
   os-optics-kit cases verify my-case     # for every case you added or changed
   ```

4. Commit with a sign-off (`git commit -s`), push to your fork and open a pull request.

## Sign-off (DCO)

Every commit needs a `Signed-off-by:` line with your name and the email of the commit author. It certifies the
[Developer Certificate of Origin](https://developercertificate.org/): you wrote the change or have the right to
submit it under the project's license. `git commit -s` adds the line. To sign off commits you already made on a
branch, run `git rebase --signoff main` and force-push the branch.

## What CI checks

| Check | Runs on |
| --- | --- |
| Lint (`ruff`) and types (`mypy`) | every pull request |
| Unit tests without the engine, on each Python version in `baseline.json` | every pull request |
| `scripts/check_repo.py`: Markdown links resolve, no file over 1 MiB and no image over 400 KiB, no run outputs, no machine-specific paths or secrets, JSON parses, `baseline.json` agrees with the package, English and Simplified Chinese pages come in pairs | every pull request |
| `skills.json` and `cases.json` match the skills and cases | every pull request |
| Wheel and source build, `twine check`, contents check, clean `--no-deps` install and command smoke test | every pull request |
| DCO sign-off of every commit | every pull request |
| Full test suite, tool workflows and `cases verify` on the published os-optics release and catalog snapshot in `baseline.json`: the cases a pull request changes, all cases on `main` | every pull request, once that os-optics release is on PyPI |
| Linux, Windows and macOS on every Python version, all cases, and the newest os-optics release as a candidate | weekly |
| CodeQL | every pull request and weekly |

## Acceptance criteria

A case is accepted when:

- it follows [the case format](docs/case-format.md): `project.json`, `CASE.md` (at most 700 tokens), `README.md`,
  `expected.json`, and at most four images of at most 400 KiB each, rendered by the engine from the case's own run;
- `os-optics-kit cases verify NAME` reproduces `expected.json` on the engine version and catalog release it
  declares, which are the ones in `baseline.json`;
- it shows something the collection does not show yet, and its README explains it in a few paragraphs;
- it contains only material you can publish under the MIT license: no private, supplier or customer data, no
  run directories or 3D scene files.

A tool is accepted when:

- more than one case or workflow can use it;
- it is a module of `tools/os_optics_kit/` with `main(argv) -> int`, registered in the command table, printing
  compact JSON and using the kit's exit codes;
- `os-optics-kit <command> --help` works without the engine installed (the engine is imported inside functions);
- it comes with a skill page of the same name (at most 900 tokens) and tests under `tests/`.

A skill page follows `templates/SKILL.md`: the required front matter and sections, no host-specific API, at most
900 tokens.

## Versions

`baseline.json` records the os-optics range the kit supports, the os-optics version it was verified with, and
the catalog release (with its SHA-256) the cases run on. Every case declares the engine and catalog it was
verified with. When a new catalog release or engine version changes a case's outcome, the case is re-verified and
its `expected.json`, `CASE.md` and images are updated in the same pull request that moves `baseline.json`.
[Engine alignment](docs/engine-alignment.md) describes the checks behind this.

## Conduct and license

Participation follows the [Code of Conduct](CODE_OF_CONDUCT.md). Contributions are released under the
[MIT License](LICENSE). Security issues: see [SECURITY.md](SECURITY.md).
