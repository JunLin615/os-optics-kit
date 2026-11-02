<p align="center">
  <a href="https://opensci-optics.com/"><img src="assets/logo.svg" alt="OpenSci" width="88"></a>
</p>

# OS Optics

**从光学设计、物理仿真，到真实硬件。**

[English](README.md) · 简体中文

[![PyPI](https://img.shields.io/pypi/v/os-optics.svg)](https://pypi.org/project/os-optics/)
[![Python](https://img.shields.io/pypi/pyversions/os-optics.svg)](https://pypi.org/project/os-optics/)
[![Website](https://img.shields.io/badge/website-opensci--optics.com-167D8D)](https://opensci-optics.com/)

在自己的电脑上搭建光学系统、仿真光的传播，并查看结果。OS Optics 将光学元件目录、确定性物理计算、结构化科学结果和交互式三维视图整合为一个 Python 软件包。你可以直接使用，也可以交给自己的编程 Agent 操作。

设计完成后，前往 [OpenSci 官网](https://opensci-optics.com/)，继续完成光学元件选购、装配配置和硬件订购。

**[Agent 指南（英文）](AGENT_GUIDE.md) · [快速开始](docs/zh-CN/getting-started.md) · [用户指南](docs/zh-CN/README.md) · [PyPI](https://pypi.org/project/os-optics/) · [官网](https://opensci-optics.com/) · [问题反馈](https://github.com/JunLin615/os-optics/issues)**

**OS Optics 0.10.0：** 本仓库的示例和工作流程工具对应已发布的 os-optics **0.10.0**。
[engine-baseline.json](engine-baseline.json) 记录已验证的版本、wheel 与源码包哈希以及测试环境。

## 你可以做什么

| 设计 | 仿真 | 分析 | 实现 |
| --- | --- | --- | --- |
| 查找光学元件，定义位置、光源和设计目标 | 追迹光线，传播高斯光束，计算偏振与干涉 | 查看三维光路、评估目标并导出科学数据 | 选择元件，获取硬件配置并订购 |

- **本地计算**：支持 Windows、macOS 和 Linux，使用 Python 3.11 及以上版本。
- **可复现项目**：记录引擎标识、目录版本、数值设置和结果哈希。
- **科学结果**：读取观测平面、光束状态、光线路径、功率收支和波前数据。
- **Python 与命令行**：接入 Notebook、脚本、参数研究和 Agent 工作流。
- **连接真实硬件**：通过 OpenSci，从本地设计继续进入采购与装配流程。

## 安装

```bash
python -m pip install "os-optics[protected]"
os-optics doctor
```

也可以使用 `uv tool install "os-optics[protected]"` 安装独立命令行工具。
虚拟环境配置和升级方法见[安装指南](docs/zh-CN/installation.md)。
0.9.0 及更早版本以 `opensci-engine` 名称发布；以该名称安装的环境同样运行
`python -m pip install --upgrade "os-optics[protected]"`，之后使用 `os-optics` 命令和 `import os_optics`。
以该名称通过 `uv tool` 安装的环境运行 `uv tool install "os-optics[protected]"`，之后使用 `os-optics`。

## 运行第一个设计

```bash
os-optics catalog sync
os-optics project example --kind beam_expander > project.json
os-optics project run project.json --out-dir run
os-optics view run
```

第一条命令下载当前光学目录。示例会创建一个双透镜扩束系统，运行后生成仿真结果、本地验证记录和三维场景。三维视图在本地浏览器中打开。

Windows PowerShell 5.1 请使用 `os-optics project example --kind beam_expander | Out-File -Encoding utf8 project.json` 创建 JSON 文件。

查看并导出本次运行：

```bash
os-optics results describe run
os-optics export arrivals run --format csv --out arrivals.csv
os-optics project verify run
```

## 使用 Python

```python
from os_optics.local import example_project_of_kind, run_optical_project
from os_optics.results import load_run

project = example_project_of_kind("beam_expander")
run = run_optical_project(project, output_dir="run-python")
print(run.status.value)

results = load_run("run-python")
print(results.power_budget())
```

可以直接运行仓库中的[扩束系统示例](examples/beam_expander.py)和[理想透镜聚焦示例](examples/ideal_lens_focus.py)。

## 文档

| 指南 | 简体中文 | English |
| --- | --- | --- |
| 安装与升级 | [阅读](docs/zh-CN/installation.md) | [Read](docs/en/installation.md) |
| 第一个光学设计 | [阅读](docs/zh-CN/getting-started.md) | [Read](docs/en/getting-started.md) |
| 目录与项目复现 | [阅读](docs/zh-CN/catalog.md) | [Read](docs/en/catalog.md) |
| 结果、可视化与导出 | [阅读](docs/zh-CN/results.md) | [Read](docs/en/results.md) |
| 设计研究：修改、扫描、比较、灵敏度、公差与三个光学工作流程 | [阅读](docs/zh-CN/workflows.md) | [Read](docs/en/workflows.md) |
| 测量数据预处理、拟合与参考归一化 | [阅读](docs/zh-CN/measurements.md) | [Read](docs/en/measurements.md) |
| 从设计到硬件 | [阅读](docs/zh-CN/hardware.md) | [Read](docs/en/hardware.md) |
| 外部科学工具联用 | [阅读](docs/zh-CN/interop.md) | [Read](docs/en/interop.md) |

本仓库的示例和指南对应已发布的 **0.10.0** 版本。外部工具示例涵盖优化、衍射、量子光子学和超表面元件。其他软件包版本见 [PyPI 发布记录](https://pypi.org/project/os-optics/#history)。

已验证的版本与测试环境记录在 [engine-baseline.json](engine-baseline.json) 中。[版本对齐检查](docs/engine-alignment.md)用于验证当前基线与候选 PyPI 版本；只有经过评审的更新才会推进基线。

## 参与项目

欢迎用中文或英文交流使用问题、分享示例设计、报告 Bug，以及改进文档。[提交 Issue](https://github.com/JunLin615/os-optics/issues/new/choose)，或通过 Pull Request 贡献文档和示例。

本仓库汇集 OS Optics 的用户文档、示例和社区反馈。引擎通过 [PyPI](https://pypi.org/project/os-optics/) 安装，完整产品体验请访问 [opensci-optics.com](https://opensci-optics.com/)。
