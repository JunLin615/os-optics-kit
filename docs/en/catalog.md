# Catalog and reproducible projects

[User guide](README.md) · English · [简体中文](../zh-CN/catalog.md)

The optical catalog connects a design to OpenSci components. Each product includes an optical SKU, specifications, a simulation model, and design geometry, with English and Chinese display names.

## Find components

```bash
os-optics catalog list --category LENS --wavelength-nm 780 --limit 20
os-optics catalog show OPTICAL_SKU
```

Replace `OPTICAL_SKU` with an identifier returned by the first command. `os-optics catalog list --help` shows the available filters.

From Python:

```python
from os_optics.local import load_public_catalog

catalog = load_public_catalog()
lenses = catalog.select(category="LENS", form="PLANO_CONVEX_LENS", wavelength_nm="780")
for lens in lenses[:5]:
    print(lens.optical_sku, lens.display_name, lens.display_name_zh_cn)
```

## Manage releases

| Command | Purpose |
| --- | --- |
| `os-optics catalog status` | Read the local store's active and saved releases |
| `os-optics catalog check` | Check the service for the current release |
| `os-optics catalog sync` | Download and activate the current release |
| `os-optics catalog verify` | Verify the locally stored files |
| `os-optics catalog use VERSION` | Activate an already stored release |
| `os-optics catalog import catalog.json` | Import a catalog release file for offline use |

Releases are stored in `~/.opensci/catalog/`. Set `OPENSCI_CATALOG_DIR` to select a different location. The first design command downloads the catalog if needed; later commands reuse local data and periodically check for updates. Stored releases remain usable offline.

## Update a saved project

Projects pin the catalog versions used by their components. Synchronizing a new catalog does not rewrite an existing project.

```bash
os-optics catalog sync
os-optics project update-catalog project.json --out project-updated.json
os-optics project run project-updated.json --out-dir run-updated
```

The update report lists changed, withdrawn, and unverified products. `--include-changed` also moves changed and unverified components to the current release. Review their data and the new simulation results when making that update.

## Keep a reproducible record

Save the project and the complete run directory. Its manifest records the engine identity, catalog identity, numerical configuration, and artifact digests. Use `os-optics project verify RUN_DIRECTORY` to verify that the saved artifacts belong together.

Catalog releases and engine releases are separate: updating product data does not require reinstalling the engine. If a catalog format requires a newer engine, upgrade the package using the [installation guide](installation.md).
