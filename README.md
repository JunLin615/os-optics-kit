# os-optics-kit

**Tools, engine-reproducible cases and agent skills for OS Optics.**

English · [简体中文](https://github.com/JunLin615/os-optics-kit/blob/main/README.zh-CN.md)

[![PyPI](https://img.shields.io/pypi/v/os-optics-kit.svg)](https://pypi.org/project/os-optics-kit/)
[![Python](https://img.shields.io/pypi/pyversions/os-optics-kit.svg)](https://pypi.org/project/os-optics-kit/)

os-optics-kit builds on the [OS Optics](https://github.com/JunLin615/os-optics) engine. It provides one command,
`os-optics-kit`, with tools that edit Projects by ID, sweep parameters, compare saved runs, plot, report, estimate
sensitivity and tolerance, select catalog parts and analyze detector measurements; a collection of cases, designs
that rerun on the engine and compare with their recorded outcome; and a vendor-neutral skill page for each tool,
which any coding agent can load.

| Name | Where | Contents |
| --- | --- | --- |
| [`os-optics`](https://pypi.org/project/os-optics/) | PyPI | The engine: optical catalog, simulation, results and 3D view |
| [`os-optics-kit`](https://pypi.org/project/os-optics-kit/) | PyPI | Tools, cases and agent skills built on the engine |
| [`os-optics`](https://github.com/JunLin615/os-optics) | GitHub | Product documentation and feedback |
| [`os-optics-kit`](https://github.com/JunLin615/os-optics-kit) | GitHub | This repository: tools, cases, skills and contributions |

**[Agent guide](https://github.com/JunLin615/os-optics-kit/blob/main/AGENT_GUIDE.md) · [Design studies](https://github.com/JunLin615/os-optics-kit/blob/main/docs/en/workflows.md) · [Measurements](https://github.com/JunLin615/os-optics-kit/blob/main/docs/en/measurements.md) · [External tools](https://github.com/JunLin615/os-optics-kit/blob/main/docs/en/interop.md) · [Workflow recipes](https://github.com/JunLin615/os-optics-kit/blob/main/docs/workflow-recipes.md) · [Contributing](https://github.com/JunLin615/os-optics-kit/blob/main/CONTRIBUTING.md)**

## Install

```bash
python -m pip install "os-optics[protected]" os-optics-kit
os-optics doctor
```

Python 3.11 or newer. Plots need the `plot` extra: `python -m pip install "os-optics-kit[plot]"`. Cases that use
catalog components need the catalog: `os-optics catalog sync`. The engine's
[installation guide](https://github.com/JunLin615/os-optics/blob/main/docs/en/installation.md) covers virtual
environments and upgrades.

## Quick start

```bash
os-optics-kit skills list
os-optics-kit skills install --dest SKILLS_DIR
os-optics-kit cases list
os-optics-kit cases copy ideal-lens-focus --dest .
os-optics-kit cases verify ideal-lens-focus
cd ideal-lens-focus
os-optics project run project.json --out-dir run
os-optics-kit summary run
```

`skills list` prints the skill index as compact JSON; `skills install` copies the skill pages into the skills
directory of your agent host (`SKILLS_DIR`). The cases ship inside the package, so `cases copy` and `cases verify`
work without a clone; `verify` reruns the case and compares the run status and every target assessment with the
recorded outcome. `os-optics-kit <command> --help` describes every command.

## Cases

| Case | What it shows | Catalog |
| --- | --- | --- |
| [beam-expander](https://github.com/JunLin615/os-optics-kit/tree/main/cases/beam-expander) | Catalog beam expander: swap a lens, sweep its spacing, restore collimation | yes |
| [ideal-lens-focus](https://github.com/JunLin615/os-optics-kit/tree/main/cases/ideal-lens-focus) | Gaussian focus of an ideal thin lens | no |
| [expansion-focus](https://github.com/JunLin615/os-optics-kit/tree/main/cases/expansion-focus) | Beam expansion and focusing workflow with sensitivity and tolerance studies | no |
| [polarization-control](https://github.com/JunLin615/os-optics-kit/tree/main/cases/polarization-control) | Polarization control with a half-wave plate and a polarizer | no |
| [mirror-alignment](https://github.com/JunLin615/os-optics-kit/tree/main/cases/mirror-alignment) | Beam position and pointing after a fold mirror | no |
| [detector-measurements](https://github.com/JunLin615/os-optics-kit/tree/main/cases/detector-measurements) | Detector linearity and reference-channel normalization | no |
| [interop-optimization](https://github.com/JunLin615/os-optics-kit/tree/main/cases/interop-optimization) | External optimization and Sobol tolerance of a beam expander | no |
| [interop-diffraction](https://github.com/JunLin615/os-optics-kit/tree/main/cases/interop-diffraction) | Diffraction at a declared stop, handed off to external codes | no |
| [interop-quantum](https://github.com/JunLin615/os-optics-kit/tree/main/cases/interop-quantum) | Hong-Ou-Mandel interference from the linear optical network export | no |
| [interop-metasurface](https://github.com/JunLin615/os-optics-kit/tree/main/cases/interop-metasurface) | Metasurface lens as a tabulated virtual component | no |

Each case holds `project.json` (the Project with its targets), `CASE.md` (for agents), an illustrated `README.md`
and `expected.json` (the verified outcome), plus its own scripts, study specs or data. See the
[case format](https://github.com/JunLin615/os-optics-kit/blob/main/docs/case-format.md).

## Tools and skills

| Skill | Command | Use it to |
| --- | --- | --- |
| `summary` | `os-optics-kit summary` | Read a saved run into one compact JSON document with an exit code per outcome |
| `edit` | `os-optics-kit edit` | List a Project's objects and apply edits by ID, checked by the engine and written atomically |
| `sweep` | `os-optics-kit sweep` | Run a bounded one- or two-parameter sweep, every sample saved and counted |
| `compare` | `os-optics-kit compare` | Compare saved runs or sweep samples by target and plane ID, with freshness |
| `plot` | `os-optics-kit plot` | Draw sweep curves, heatmaps, power distributions, bar charts and histograms |
| `report` | `os-optics-kit report` | Assemble a Markdown and JSON report with counts, diagnostics and artifact hashes |
| `sensitivity` | `os-optics-kit sensitivity` | Rank design parameters by finite-difference sensitivity at two step sizes |
| `tolerance` | `os-optics-kit tolerance` | Run a seeded tolerance study under declared distributions |
| `candidates` | `os-optics-kit candidates` | List catalog products that pass explicit filters, sorted by closeness to a value |
| `measurements` | `os-optics-kit measurements` | Subtract backgrounds, normalize by a reference channel and fit detector CSV data |
| `run-case` | `os-optics-kit cases` | Find, copy, run, check and adapt a case |
| `contrib-case` | `os-optics-kit contrib case` | Turn a finished design into a pull-request-ready case |
| `contrib-tool` | `os-optics-kit contrib tool` | Package a helper as a kit command with its skill page and tests |

`os-optics-kit skills show NAME` prints a skill page; [`skills.json`](https://github.com/JunLin615/os-optics-kit/blob/main/skills.json) indexes them. Tools are also
importable (`import os_optics_kit`), and `python -m os_optics_kit <command>` runs the same commands.

## Version alignment

os-optics-kit 0.1.x requires `os-optics>=0.12,<0.13` and was verified with os-optics 0.12.0 and catalog release
2026-10-28.1 ([`baseline.json`](https://github.com/JunLin615/os-optics-kit/blob/main/baseline.json)). Each case records the engine and catalog release it was verified
with. See [case format](https://github.com/JunLin615/os-optics-kit/blob/main/docs/case-format.md) and [engine alignment](https://github.com/JunLin615/os-optics-kit/blob/main/docs/engine-alignment.md).

## Contributing

Cases, tools, skills and documentation are welcome as pull requests. An agent contributes with the user's own
GitHub account: `os-optics-kit contrib case` and `os-optics-kit contrib tool` verify the contribution and open the
pull request, or stop at a bundle with the patch to submit later. See [CONTRIBUTING.md](https://github.com/JunLin615/os-optics-kit/blob/main/CONTRIBUTING.md) and
[contributing with an agent](https://github.com/JunLin615/os-optics-kit/blob/main/docs/contributing-with-an-agent.md). Engine questions and bug reports go to the
[os-optics issue tracker](https://github.com/JunLin615/os-optics/issues).

## License

os-optics-kit is released under the [MIT License](https://github.com/JunLin615/os-optics-kit/blob/main/LICENSE). The engine, os-optics, is distributed under its own
license.
