# User guide

[Home](../../README.md) · English · [简体中文](../zh-CN/README.md)

Start with a working optical design, then explore its results and adapt it to your experiment.

1. [Install OS Optics](installation.md) — Python, virtual environments, uv, and upgrades.
2. [Run your first design](getting-started.md) — generate a project, simulate it, and open the 3D view.
3. [Work with the catalog](catalog.md) — find optics, manage releases, and reproduce a project.
4. [Read and export results](results.md) — validation, beam data, visualization, and CSV exports.
5. [Continue to hardware](hardware.md) — component selection, assembly preview, and ordering.
6. [Run design studies and workflows](workflows.md) — edit by ID, parameter sweeps, run comparison, plots, reports, sensitivity and tolerance studies, and three parameterized optical workflows.
7. [Analyze measurements](measurements.md) — background subtraction, reference normalization, linear fits and residual plots.
8. [Use external scientific tools](interop.md) — optimization, diffraction, quantum photonics and metasurface components with SciPy, Optuna, SALib, LightPipes, prysm, POPPY, Perceval, Piquasso and grcwa.

## Examples

| Example | Run from the repository root | Uses the optical catalog |
| --- | --- | --- |
| [Beam expander](../../examples/beam_expander.py) | `python examples/beam_expander.py --out run-example` | Yes; downloads on first use |
| [Ideal-lens focus](../../examples/ideal_lens_focus.py) | `python examples/ideal_lens_focus.py` | No; uses mathematical elements |
| [Design studies and workflows](../../examples/) | Follow the [workflow guide](workflows.md) | Sections 1–3 yes; sections 4–5 no |
| [Measurement analysis](../../examples/analyze_measurements.py) | Follow the [two CSV examples](measurements.md) | No; engine-independent, install `requirements-examples.txt` |
| [External tools](../../examples/interop/) | Follow the [external tools guide](interop.md) | No; install each topic's requirements file |

Install the package into your Python environment before running these scripts. `uv tool install` provides an isolated CLI; use the [pip installation](installation.md) for Python imports.

These guides and examples use **os-optics 0.10.0**; see [version matching](installation.md#match-the-examples-to-the-installed-version) for the matching installation. Commands expose their options through `--help`; Python APIs are documented in the installed package and the [PyPI reference](https://pypi.org/project/os-optics/).

[OpenSci website](https://opensci-optics.com/) · [Package releases](https://pypi.org/project/os-optics/#history) · [Ask a question](https://github.com/JunLin615/os-optics/issues/new/choose)
