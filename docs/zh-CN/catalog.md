# 目录与项目复现

[用户指南](README.md) · [English](../en/catalog.md) · 简体中文

光学目录将设计与 OpenSci 元件连接起来。每个产品包含光学 SKU、参数、仿真模型和设计几何，并提供中英文显示名称。

## 查找元件

```bash
os-optics catalog list --category LENS --wavelength-nm 780 --limit 20
os-optics catalog show OPTICAL_SKU
```

将 `OPTICAL_SKU` 替换为第一条命令返回的标识。使用 `os-optics catalog list --help` 查看可用筛选条件。

Python 调用方式：

```python
from os_optics.local import load_public_catalog

catalog = load_public_catalog()
lenses = catalog.select(category="LENS", form="PLANO_CONVEX_LENS", wavelength_nm="780")
for lens in lenses[:5]:
    print(lens.optical_sku, lens.display_name, lens.display_name_zh_cn)
```

## 管理目录版本

| 命令 | 用途 |
| --- | --- |
| `os-optics catalog status` | 查看本地当前版本与已保存版本 |
| `os-optics catalog check` | 向服务查询当前发布版本 |
| `os-optics catalog sync` | 下载并启用当前发布版本 |
| `os-optics catalog verify` | 校验本地文件 |
| `os-optics catalog use VERSION` | 启用一个已保存的版本 |
| `os-optics catalog import catalog.json` | 导入目录发布文件，供离线使用 |

目录默认保存在 `~/.opensci/catalog/`，也可以通过 `OPENSCI_CATALOG_DIR` 指定位置。第一次使用设计命令时会按需下载目录，后续命令复用本地数据并定期检查更新。已保存的版本支持离线使用。

## 更新已保存的项目

项目会固定各元件使用的目录版本。同步新目录不会直接改写已有项目。

```bash
os-optics catalog sync
os-optics project update-catalog project.json --out project-updated.json
os-optics project run project-updated.json --out-dir run-updated
```

更新报告会列出发生变化、撤下和无法比对的产品。添加 `--include-changed` 可以将发生变化及无法比对的元件也更新到当前版本。更新时检查元件数据，并查看新的仿真结果。

## 保存可复现记录

保留项目和完整运行目录。清单记录引擎标识、目录标识、数值配置和文件摘要。运行 `os-optics project verify RUN_DIRECTORY` 可以校验这些结果文件是否属于同一次完整运行。

目录和引擎分别发布：产品数据更新不需要重新安装引擎。如果新的目录格式要求更新引擎，按[安装指南](installation.md)升级软件包即可。
