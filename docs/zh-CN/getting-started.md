# 第一个光学设计

[用户指南](README.md) · [English](../en/getting-started.md) · 简体中文

本教程创建一个双透镜扩束系统，在本地运行仿真并打开三维光路。开始前请完成[安装](installation.md)。

## 1. 获取目录

```bash
os-optics catalog sync
os-optics catalog list --category LENS --wavelength-nm 780 --limit 20
```

目录提供 OpenSci 光学元件的设计参数与仿真模型。将返回的 `optical_sku` 填入 `os-optics catalog show OPTICAL_SKU`，即可查看元件详情。

## 2. 创建项目

```bash
os-optics project example --kind beam_expander > project.json
```

Windows PowerShell 5.1 使用：

```powershell
os-optics project example --kind beam_expander | Out-File -Encoding utf8 project.json
```

生成的 JSON 包含光学元件、位姿、光源、观测平面和设计目标。目录元件同时记录该设计采用的目录版本。

其他可用起点：

| 示例名称 | 光学系统 |
| --- | --- |
| `focus` | 单透镜聚焦 |
| `folded` | 折转反射镜与透镜 |
| `beam_expander` | 双透镜扩束系统 |
| `relay_4f` | 4f 中继系统 |
| `folded_expander` | 折叠扩束系统 |
| `dichroic_routing` | 二向色镜双波长分路 |
| `polarization` | 波片与偏振片 |
| `two_beam_interference` | 同一激光器分出的两臂在观测平面交汇 |
| `two_color_dichroic` | 双谱线光源经二向色镜分到两个探测面 |
| `dichroic_combiner` | 两束激光通过二向色镜合束 |

目录版本给出各镀膜的群延迟上限时（目录 schema 3.2，例如 `2026-10-28.1`），
`two_beam_interference` 会给出条纹可见度和相干性，并同时注明该延迟上限。镀膜未给出相位时，
`phase_difference_rad` 没有数值，结果中列出 `PHASE_UNDEFINED`。

## 3. 运行仿真

```bash
os-optics project run project.json --out-dir run
os-optics project verify run
```

运行结果和相关文件保存在 `run` 目录。先阅读 `manifest.json` 中的整体状态和目标结果，再通过[结果指南](results.md)了解各文件与状态含义。

## 4. 查看三维光路

```bash
os-optics view run
```

可以切换预设视角，选中光学元件查看属性，显示或隐藏光束路径。查看器支持中英文切换。在终端按 Ctrl+C 可停止本地查看器。

生成独立 HTML 视图或截图：

```bash
os-optics view run --write-html view.html
os-optics view run --snapshot shots
```

## 5. 分析、修改、再次运行

```bash
os-optics results describe run
os-optics export arrivals run --format csv --out arrivals.csv
os-optics schema project
```

OS Optics 0.10.0 提供语义编辑与精简结果读取；匹配的安装方式见
[版本匹配说明](installation.md#让示例与安装版本一致)。将[编辑步骤](../workflow-recipes.md#edit-a-project-by-id)
中的操作保存为 `edits.json`，然后保留原始 Project，运行修改后的设计：

```bash
python examples/edit_project.py project.json --edits edits.json --out revised.json
os-optics project check revised.json
os-optics project run revised.json --out-dir run-revised
os-optics results targets run-revised
os-optics results freshness run --project revised.json
```

工具使用引擎执行语义验证，并保存重试记录。最后一条命令说明旧运行与修改后 Project 的关系。
分别读取执行情况、科学量有效性和可选目标判定。未配置目标的运行仍可计算有用的科学量。
保留每次运行以便比较；通过[预算示例](../workflow-recipes.md#computation-budgets)了解限额
如何影响完成情况与可用结果。

## 与自己的 Agent 协作

可以将具体任务交给编程 Agent：

> 使用 OS Optics 创建扩束系统示例，在本地运行，查看目标结果与功率收支，将光束到达数据导出为 CSV。展示三维光路，并解释光束尺寸和元件间距。

命令行提供结构化输出，Python API 可以读取相同的项目和科学结果。[示例脚本](../../examples/beam_expander.py)展示了完整的本地工作流。

下一步：[读取与导出结果](results.md)，或[继续完成硬件实现](hardware.md)。
