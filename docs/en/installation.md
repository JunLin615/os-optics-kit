# Installation

[User guide](README.md) · English · [简体中文](../zh-CN/installation.md)

OS Optics runs on Windows, macOS, and Linux with **Python 3.11 or newer**. Choose pip for Python scripts and notebooks, or uv for a standalone command-line tool.

## Match the examples to the installed version

These examples and guides use os-optics **0.10.0**, recorded in
[engine-baseline.json](../../engine-baseline.json) together with its wheel and source
hashes and the tested environment. Install that exact version in a fresh environment:

```bash
python -m pip install "os-optics[protected]==0.10.0"
```

The [alignment checks](../engine-alignment.md) run the examples against that
installation. `os-optics doctor` reports the installed version before you run an
example.

## Python environment

Create a virtual environment:

```bash
python -m venv .venv
```

Activate it on macOS or Linux:

```bash
source .venv/bin/activate
```

On Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Install and check the engine:

```bash
python -m pip install "os-optics[protected]"
os-optics doctor
```

You can also run the CLI as `python -m os_optics`. If PowerShell activation is unavailable, use `.venv\Scripts\python.exe -m pip install "os-optics[protected]"` and `.venv\Scripts\python.exe -m os_optics doctor` directly.

The `protected` extra adds support for opening assembly previews and assembly details in the hardware workflow. For optical design and simulation alone, `python -m pip install os-optics` is sufficient.

## Standalone CLI with uv

With [uv](https://docs.astral.sh/uv/) installed:

```bash
uv tool install "os-optics[protected]"
os-optics doctor
```

If the command is not on your PATH, run `uv tool update-shell` and open a new terminal. To run a command without a persistent tool installation:

```bash
uvx --from "os-optics[protected]" os-optics doctor
```

## Prepare the catalog

```bash
os-optics catalog sync
os-optics catalog status
```

The catalog downloads into a local store. Later simulations use stored releases, including when working offline. `doctor` checks local installation readiness; `catalog sync` retrieves the current catalog from OpenSci.

## Upgrade

For a pip installation:

```bash
python -m pip install --upgrade "os-optics[protected]"
```

For a uv tool installation:

```bash
uv tool upgrade os-optics
```

Up to 0.9.0 the package was published as `opensci-engine` (command `opensci-engine`,
`import opensci_engine`). An installation under that name takes the same
`python -m pip install --upgrade "os-optics[protected]"`; the two packages share no files.
Then use the `os-optics` command and `import os_optics`. A `uv tool` installation under the
earlier name installs the new one with `uv tool install "os-optics[protected]"`, then uses `os-optics`.

Run `os-optics doctor` to see the installed version. To pin the version these examples use, install `"os-optics[protected]==0.10.0"`.

[engine-baseline.json](../../engine-baseline.json) records the verified release and test environment. Maintainers can check another released version with the [alignment workflow](../engine-alignment.md); a successful candidate check does not automatically change this baseline.

## Common setup questions

| Symptom | Next step |
| --- | --- |
| Python version error during installation | Check `python --version`; use Python 3.11+ or the uv route |
| `os-optics` command not found | Activate the environment or use `python -m os_optics` |
| `DOWNLOAD_ON_FIRST_USE` in doctor | Run `os-optics catalog sync` or start a catalog-based design |
| Catalog download fails | Check connectivity with `os-optics catalog check`; offline catalog import is described in the [catalog guide](catalog.md) |
| Service requests a newer engine | Upgrade, update the project's catalog, and rerun the design before submitting it |

Next: [Run your first optical design](getting-started.md).
