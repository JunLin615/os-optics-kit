# Agent guide

os-optics-kit: tools, engine-reproducible cases and skills for the OS Optics engine (`os-optics`).
Install with `python -m pip install "os-optics[protected]" os-optics-kit` (Python 3.11+); kit 0.2.x
requires `os-optics>=0.13,<0.14`. Engine guide:
[os-optics AGENT_GUIDE](https://github.com/JunLin615/os-optics/blob/main/AGENT_GUIDE.md).

## Load the skills

```bash
os-optics-kit skills list
os-optics-kit skills show NAME
os-optics-kit skills install --dest DIR
```

`skills list` prints the index (name, description, command, requirement) as compact JSON. Read one
skill before using its command. `skills install` copies the skill directories and `skills.json` into
DIR, the skills directory of your agent host; it never overwrites a different file without `--force`.

## Route by task

| Task | Skill / command |
| --- | --- |
| Start from a worked design | `run-case`: `os-optics-kit cases list`, `cases copy NAME --dest .`, `cases verify NAME` |
| Read a run before deciding | `summary RUN` |
| Discover IDs, units, axes; edit by ID | `edit P.json --describe --ids ID`; `edit P.json --edits OPS.json --out NEW.json --run RUN` |
| Explore one or two parameters | `sweep SPEC.json --base P.json --out STUDY` |
| Explain what an edit changed | `compare RUN_A RUN_B --project CURRENT.json --out CMP` |
| Plots and a report | `plot sweep STUDY --y target:ID`; `report STUDY --project CURRENT.json` |
| Reproducible 3D figures | `render RUN --out FIGURES --camera 55,22 --background transparent` |
| Sensitivity, tolerance | `sensitivity SPEC.json --out DIR`; `tolerance SPEC.json --out DIR` |
| Catalog parts | `candidates --category ... --wavelength-nm ...` |
| Detector CSV data | `measurements DATA.csv --x ... --signal ... --out DIR` |
| Share a design or helper | `contrib-case`, `contrib-tool` |

Each command is `os-optics-kit <command>`; `--help` holds its full specification.
[Workflow recipes](docs/workflow-recipes.md) give spec keys, outputs and error codes.
Cases `expansion-focus`, `polarization-control` and `mirror-alignment` include a parameterized
`workflow.py` (`--list-parameters`, unit-bearing inputs such as `--gap-error-mm 250um`,
`--study NAME=START..STOP:COUNT`, `--no-studies`).

## Interpret before deciding

Read computation completion, quantity validity, target assessment and freshness separately. Engine
assessments are `MET`, `UNMET`, `INDETERMINATE` or `NOT_APPLICABLE`. A bounded quantity is an
interval; an unrelated unavailable quantity does not invalidate every result. No configured target
means no requirement was assessed.

Study `PASS`/`FAIL` classes summarize the selected targets. Keep
every sample, including invalid, partial and indeterminate ones. Objective validity and feasibility
are separate; report `NO_FEASIBLE` when no sample meets the constraints. Fractions state their
denominator; a tolerance fraction describes simulated samples under the declared distributions.

Saved results keep their identities after edits. `compare` and `os-optics results freshness RUN
--project P` report `CURRENT`, `HISTORICAL` or `UNDETERMINED` with reasons (`PHYSICS_CHANGED`,
`TARGETS_CHANGED`, ...). A historical result describes the earlier design; rerun when the task needs
the edited one.

Exit codes: 0 completed or PASS; 1 FAIL, `NO_FEASIBLE`, `NO_MATCH` or a case mismatch; 2 UNKNOWN (also
a partial run, `PARTIAL`) or `INCOMPLETE`; 3 OUT_OF_MODEL; 4 refused input, with a JSON
error on stderr. Stdout is compact JSON;
details stay in files.

## Inputs and recovery

Establish wavelength (nm), power (W), Gaussian 1/e² intensity radius and positions (mm), object IDs,
poses and the user's actual target bounds. Coordinates are right-handed; quaternions are
`[w, x, y, z]`. Read `local_axes` before moving along a local axis.

Inputs are never modified; an existing output is refused (`OUTPUT_EXISTS`) unless `--overwrite`.
Invalid edits are refused atomically. Repeating an identical `edit` returns `already_applied`;
`--expect-base-hash` refuses a changed base. Set both a study's `max_evaluations` and the Project's
compute budget, and inspect limited or partial results before raising a limit. Report commands,
versions, units, selected assessments, freshness and artifact paths.

## Contribute

`os-optics-kit skills show contrib-case` or `contrib-tool`, then `os-optics-kit contrib case
P.json --name NAME --dry-run`. The user's own GitHub account opens the pull request (`--open-pr`);
without it the command leaves a bundle with the patch. See
[contributing with an agent](docs/contributing-with-an-agent.md).
