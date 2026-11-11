# os-optics-kit

**Tools, engine-reproducible cases and agent skills for OS Optics.**

English · [简体中文](https://github.com/JunLin615/os-optics-kit/blob/main/README.zh-CN.md)

[![PyPI](https://img.shields.io/pypi/v/os-optics-kit.svg)](https://pypi.org/project/os-optics-kit/)
[![Python](https://img.shields.io/pypi/pyversions/os-optics-kit.svg)](https://pypi.org/project/os-optics-kit/)

os-optics-kit builds on the [OS Optics](https://github.com/JunLin615/os-optics) engine. It provides one command,
`os-optics-kit`, with tools that edit Projects by ID, sweep parameters, compare saved runs, plot, report, render
presentation-grade 3D figures, estimate sensitivity and tolerance, select catalog parts, analyze detector measurements and prepare presentation
material; a collection of cases, designs
that rerun on the engine and compare with their recorded outcome; and a vendor-neutral skill page for each tool,
which any coding agent can load.

| Name | Where | Contents |
| --- | --- | --- |
| [`os-optics`](https://pypi.org/project/os-optics/) | PyPI | The engine: optical catalog, simulation, results and 3D view |
| [`os-optics-kit`](https://pypi.org/project/os-optics-kit/) | PyPI | Tools, cases and agent skills built on the engine |
| [`os-optics`](https://github.com/JunLin615/os-optics) | GitHub | Product documentation and feedback |
| [`os-optics-kit`](https://github.com/JunLin615/os-optics-kit) | GitHub | This repository: tools, cases, skills and contributions |

**[Agent guide](https://github.com/JunLin615/os-optics-kit/blob/main/AGENT_GUIDE.md) · [Design studies](https://github.com/JunLin615/os-optics-kit/blob/main/docs/en/workflows.md) · [Measurements](https://github.com/JunLin615/os-optics-kit/blob/main/docs/en/measurements.md) · [Presentation](https://github.com/JunLin615/os-optics-kit/blob/main/docs/en/presentation.md) · [External tools](https://github.com/JunLin615/os-optics-kit/blob/main/docs/en/interop.md) · [Workflow recipes](https://github.com/JunLin615/os-optics-kit/blob/main/docs/workflow-recipes.md) · [Contributing](https://github.com/JunLin615/os-optics-kit/blob/main/CONTRIBUTING.md)**

## Install

```bash
python -m pip install "os-optics[protected]" os-optics-kit
os-optics doctor
```

Python 3.11 or newer. Plots need the `plot` extra: `python -m pip install "os-optics-kit[plot]"`. System views and
case images come from the engine without a browser (`os-optics view RUN --no-browser --snapshot DIR --views iso`,
`--section ALL` for a lens section); the kit's presentation-grade figures (`render`, `present`) need the `render`
extra and Chrome or Chromium; MP4 animations need the `media` extra. Cases that use catalog components need the
catalog: `os-optics catalog sync`. The engine's
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

## Presentation material

`os-optics-kit present` turns saved runs and studies into slide-ready material: system views, result plots,
comparison figures and animations, with an offline gallery and a portable bundle. Everything is drawn from one saved
`presentation.json`, so a new language, background, text size or figure size is a rebuild, not a new simulation.

```bash
python -m pip install "os-optics-kit[plot,render,media]"
os-optics-kit present init run --out talk --title "Focusing lens"
os-optics-kit present build talk/presentation.json
os-optics-kit present build talk/presentation.json --out talk-zh \
    --set theme.language=zh-CN --set theme.background=light --set theme.font_scale=1.3
os-optics-kit present bundle talk-zh --out talk-zh.zip
```

See the [presentation guide](https://github.com/JunLin615/os-optics-kit/blob/main/docs/en/presentation.md) for sizes,
fonts, comparisons, animations and handoff.

## Cases

<!-- cases:start -->
### Lens

| Case | What it shows | Domain | Scale | Catalog |
| --- | --- | --- | --- | --- |
| [achromatic-imaging](https://github.com/JunLin615/os-optics-kit/tree/main/cases/achromatic-imaging) | Three-colour imaging with a declared achromatic objective | Imaging, Dispersion, Diffraction | Small | no |
| [aspheric-lens](https://github.com/JunLin615/os-optics-kit/tree/main/cases/aspheric-lens) | Even aspheres carry a five-element f/3.5 lens to 25° half field | Imaging, Diffraction | Medium | no |
| [cylindrical-light-sheet](https://github.com/JunLin615/os-optics-kit/tree/main/cases/cylindrical-light-sheet) | Cylindrical light-sheet thickness and range | Gaussian beams, Beam shaping | Small | no |
| [ideal-lens-focus](https://github.com/JunLin615/os-optics-kit/tree/main/cases/ideal-lens-focus) | Gaussian focus of an ideal thin lens | Gaussian beams | Small | no |
| [interop-metasurface](https://github.com/JunLin615/os-optics-kit/tree/main/cases/interop-metasurface) | Metasurface lens as a virtual component | Nanophotonics, Diffraction | Small | no |
| [midwave-infrared-imager](https://github.com/JunLin615/os-optics-kit/tree/main/cases/midwave-infrared-imager) | A five-element 3-5 µm imager with manufacturable element thicknesses | Imaging, Diffraction | Medium | no |
| [sonnar-telephoto-lens](https://github.com/JunLin615/os-optics-kit/tree/main/cases/sonnar-telephoto-lens) | Six-element Sonnar at f/2.4, measured across the full entrance pupil | Imaging, Diffraction | Medium | no |

### Free-space platform

| Case | What it shows | Domain | Scale | Catalog |
| --- | --- | --- | --- | --- |
| [beam-expander](https://github.com/JunLin615/os-optics-kit/tree/main/cases/beam-expander) | Catalog beam expander | Gaussian beams | Small | yes |
| [bounded-aberration-correction](https://github.com/JunLin615/os-optics-kit/tree/main/cases/bounded-aberration-correction) | Intensity-only aberration correction | Wavefront control, Diffraction | Small | no |
| [detector-measurements](https://github.com/JunLin615/os-optics-kit/tree/main/cases/detector-measurements) | Detector linearity and reference-channel normalization | Radiometry | Medium | no |
| [expansion-focus](https://github.com/JunLin615/os-optics-kit/tree/main/cases/expansion-focus) | Beam expansion and focusing | Gaussian beams | Small | no |
| [focus-requirement](https://github.com/JunLin615/os-optics-kit/tree/main/cases/focus-requirement) | A focusing requirement verified at a fixed work plane | Gaussian beams | Small | no |
| [interop-diffraction](https://github.com/JunLin615/os-optics-kit/tree/main/cases/interop-diffraction) | Diffraction at a declared stop | Diffraction, Gaussian beams | Small | no |
| [interop-optimization](https://github.com/JunLin615/os-optics-kit/tree/main/cases/interop-optimization) | External optimization and Sobol tolerance | Gaussian beams | Small | no |
| [interop-quantum](https://github.com/JunLin615/os-optics-kit/tree/main/cases/interop-quantum) | Hong-Ou-Mandel interference from the linear optical network export | Quantum optics, Interference | Medium | no |
| [mirror-alignment](https://github.com/JunLin615/os-optics-kit/tree/main/cases/mirror-alignment) | Beam position and pointing after a fold mirror | Beam steering | Small | no |
| [phase-only-flat-top](https://github.com/JunLin615/os-optics-kit/tree/main/cases/phase-only-flat-top) | Circular flat-top shaping and input-width sensitivity | Beam shaping, Diffraction | Small | no |
| [polarization-control](https://github.com/JunLin615/os-optics-kit/tree/main/cases/polarization-control) | Polarization control with a half-wave plate and a polarizer | Polarization | Small | no |

The classes, domains and tags are listed by `os-optics-kit cases vocabulary` and defined in the [case format](https://github.com/JunLin615/os-optics-kit/blob/main/docs/case-format.md).
<!-- cases:end -->

Each case holds `project.json` (the Project with its targets), `CASE.md` (for agents), `README.md` and
`README.zh-CN.md` illustrated with the system views the engine renders from the case's own run (recorded in
`images.json`), and `expected.json` (the verified outcome), plus its own scripts, study specs or data.
`os-optics-kit cases list --class lens` filters by class, domain, provenance, publication, scale, tag or catalog;
`os-optics-kit cases vocabulary` lists the values. See the
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
| `render` | `os-optics-kit render` | Render presentation-grade 3D figures of a run at chosen camera angles, with element framing and a manifest (needs Chrome or Chromium) |
| `present` | `os-optics-kit present` | Build themed views, plots, comparisons and animations from saved results, with a gallery and bundle |
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

os-optics-kit 0.3.x requires `os-optics>=0.13,<0.14` and was verified with os-optics 0.13.0 and catalog release
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
