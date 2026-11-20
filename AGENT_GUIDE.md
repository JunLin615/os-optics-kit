# Agent guide

os-optics-kit: tools, engine-reproducible cases and skills for the OS Optics engine (`os-optics`).
Install with `python -m pip install "os-optics[protected]" os-optics-kit` (Python 3.11+); kit 0.4.x
requires `os-optics>=0.13,<0.14`. Engine guide:
[os-optics AGENT_GUIDE](https://github.com/JunLin615/os-optics/blob/main/AGENT_GUIDE.md).

## Load the skills

```bash
os-optics-kit skills list
os-optics-kit skills show NAME [PAGE]
os-optics-kit skills install --dest DIR
```

Read a skill before its command; `skills show NAME PAGE` prints a page it names. `skills install` copies
them into your agent host's skills directory (`--force` overwrites a different file).

## Route by task

| Task | Skill / command |
| --- | --- |
| Start from a worked design | `run-case`: `cases list --class lens --tag mtf` (values: `cases vocabulary`), `cases copy NAME --dest .`, `cases verify NAME` |
| Case images / system view (no browser) | `os-optics view RUN --no-browser --snapshot DIR --views iso`; lens section `--section ALL`; kit checkout: `cases images NAME` |
| Read a run's outcome | `summary RUN` |
| Discover IDs, units, axes; edit by ID | `edit P.json --describe --ids ID`; `edit P.json --edits OPS.json --out NEW.json --run RUN` |
| Explore a parameter grid | `sweep SPEC.json --base P.json --out STUDY` |
| Explain what an edit changed | `compare RUN_A RUN_B --project CURRENT.json --out CMP` |
| Plots and a report | `plot sweep STUDY --y target:ID`; `report STUDY --project CURRENT.json` |
| Presentation-grade 3D figures (Chrome) | `render RUN --out FIGURES --camera 55,22 --background transparent` |
| Presentation material | `present` skill: `present init RUN STUDY --out DIR`; `present build DIR/presentation.json` |
| Restyle without rerunning | `present build DIR/presentation.json --set theme.language=zh-CN` |
| Compare saved alternatives | `present init RUN_A RUN_B --labels A,B --out DIR` |
| Gallery or bundle handoff | `present list DIR`; `present bundle DIR --out X.zip --only ID,...` |
| Orbit, detail move, sample playback | `present init ... --animate`; fields: `present settings` |
| Sensitivity, tolerance | `sensitivity SPEC.json --out DIR`; `tolerance SPEC.json --out DIR` |
| Parallel, resumable runs | `study` skill: `sweep SPEC.json --out S --workers 4`; `study run S --resume`; `study candidates P1.json P2.json --out C` |
| Catalog parts | `candidates --category ... --wavelength-nm ...` |
| Detector CSV data | `measurements DATA.csv --x ... --signal ... --out DIR` |
| Slanted-edge SFR vs design MTF | `sfr IMG --roi X,Y,W,H --out DIR`; `--design imaging.json --pixel-pitch-um P` |

Unprefixed commands are `os-optics-kit <command>`; `--help` specifies each.
`render` and `present` need `os-optics-kit[render]` and Chrome or Chromium; case images do not.
[Workflow recipes](docs/workflow-recipes.md) give spec keys, outputs and error codes.
Cases `expansion-focus`, `polarization-control` and `mirror-alignment` ship a parameterized `workflow.py`
(`--list-parameters`).

## What the outputs state

A run states separate facts: `outcome.execution` (`COMPLETED`, or `PARTIAL` with the budgets it
reached), each quantity's state (a `BOUNDED` quantity is an interval), each target's assessment
(`MET`, `UNMET`, `INDETERMINATE`, `NOT_APPLICABLE`) and `integrity` (whether its files match their
hashes). A Project without targets assesses no requirement.

A sample's class (`PASS`, `FAIL`, `OUT_OF_MODEL`, `UNKNOWN`, `NOT_APPLICABLE`, `ERROR`) follows
its selected targets' assessments; a `PARTIAL` run did not complete and is never `PASS` or
`NOT_APPLICABLE` (`UNKNOWN` when its targets are met or none is selected). A study table holds every
requested sample; target values have a `value_kind`. `objective_valid` and
`feasible` are separate; `NO_FEASIBLE`: no sample meets the constraints. `pass_fraction` divides by all
samples, `pass_fraction_among_evaluable` by PASS + FAIL; tolerance fractions count simulated samples.

`compare` and `os-optics results freshness RUN --project P` report `CURRENT`, `HISTORICAL` or
`UNDETERMINED` with reasons (`PHYSICS_CHANGED`, `TARGETS_CHANGED`, ...); a historical result describes
the earlier design. A study has one freshness record.

Exit codes: 0 completed or PASS; 1 FAIL, `NO_FEASIBLE`, `NO_MATCH` or a case mismatch; 2 UNKNOWN or
`INCOMPLETE`; 3 OUT_OF_MODEL, or a `PARTIAL` run whatever its class; 4 refused input (JSON error on
stderr); 130 cancelled (`study run DIR --resume` continues). Stdout is compact JSON;
details stay in files.

## Inputs and recovery

Establish wavelength (nm), power (W), Gaussian 1/e² intensity radius and positions (mm), object IDs,
poses and the user's actual target bounds. Coordinates are right-handed; quaternions are
`[w, x, y, z]`. Read `local_axes` before moving along a local axis.

Inputs are never modified; an existing output is refused (`OUTPUT_EXISTS`) unless `--overwrite`.
Invalid edits are refused atomically. Repeating an identical `edit` returns `already_applied`;
`--expect-base-hash` refuses a changed base. Optional `max_evaluations` caps a study's runs, the
Project's compute budget the work in each run. Size workers from a timed pilot
(`skills show study recipe.md`): `--workers N` (`auto` assumes 0.2 s per sample),
`--time-limit-s` / `--max-memory-mib` per evaluation, workers x threads within the CPUs.

## Contribute a case

`skills show contrib-case`, then `contrib case P.json --name NAME --class lens --domain imaging --tags mtf
--dry-run` runs the engine, renders the views browser-free, writes the case, `cases.json` and README tables.
By hand: `cases images NAME` records the views that README.md and README.zh-CN.md show; `cases index --write`,
`python scripts/case_tables.py --write`, `cases index --check`. Values: `cases vocabulary`.
`--open-pr` opens the pull request from the user's GitHub account; else a bundle holds the patch.
A helper script: `contrib-tool`. See [contributing with an agent](docs/contributing-with-an-agent.md).
