# os-optics-kit

**OS Optics 的工具、可由引擎复现的案例和 Agent 技能。**

[English](README.md) · 简体中文

[![PyPI](https://img.shields.io/pypi/v/os-optics-kit.svg)](https://pypi.org/project/os-optics-kit/)
[![Python](https://img.shields.io/pypi/pyversions/os-optics-kit.svg)](https://pypi.org/project/os-optics-kit/)

os-optics-kit 基于 [OS Optics](https://github.com/JunLin615/os-optics) 引擎，提供一个命令 `os-optics-kit`：
其中的工具可以按标识修改 Project、扫描参数、比较已保存的运行、绘图、生成报告、渲染三维图、估计灵敏度和公差、
挑选目录元件、分析探测器测量数据以及制作演示材料；一组案例，即可在引擎上重新运行并与记录结果比较的设计；以及每个工具
对应的一份与厂商无关的技能说明，任何编程 Agent 都可以加载。

| 名称 | 位置 | 内容 |
| --- | --- | --- |
| [`os-optics`](https://pypi.org/project/os-optics/) | PyPI | 引擎：光学元件目录、仿真、结果与三维视图 |
| [`os-optics-kit`](https://pypi.org/project/os-optics-kit/) | PyPI | 基于引擎的工具、案例和 Agent 技能 |
| [`os-optics`](https://github.com/JunLin615/os-optics) | GitHub | 产品文档与问题反馈 |
| [`os-optics-kit`](https://github.com/JunLin615/os-optics-kit) | GitHub | 本仓库：工具、案例、技能与贡献 |

**[Agent 指南（英文）](AGENT_GUIDE.md) · [设计研究](docs/zh-CN/workflows.md) · [测量数据](docs/zh-CN/measurements.md) · [演示材料](docs/zh-CN/presentation.md) · [外部工具](docs/zh-CN/interop.md) · [工作流程步骤（英文）](docs/workflow-recipes.md) · [参与贡献（英文）](CONTRIBUTING.md)**

## 安装

```bash
python -m pip install "os-optics[protected]" os-optics-kit
os-optics doctor
```

需要 Python 3.11 及以上版本。绘图需要 `plot` 扩展：`python -m pip install "os-optics-kit[plot]"`；
截取三维图需要 `render` 扩展和 Chrome 或 Chromium；MP4 动画需要 `media` 扩展。
使用目录元件的案例需要光学元件目录：`os-optics catalog sync`。虚拟环境和升级方法见引擎的
[安装指南](https://github.com/JunLin615/os-optics/blob/main/docs/zh-CN/installation.md)。

## 快速开始

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

`skills list` 以紧凑 JSON 输出技能索引；`skills install` 把技能说明复制到 Agent 宿主的技能目录
（`SKILLS_DIR`）。案例随软件包一起安装，因此 `cases copy` 和 `cases verify` 无需克隆仓库；`verify`
重新运行案例，并将运行状态和每个目标判定与记录结果比较。`os-optics-kit <命令> --help` 说明每个命令。

## 演示材料

`os-optics-kit present` 把已保存的运行和研究整理成可直接用于幻灯片的材料：系统视图、结果图、比较图和动画，
并生成离线图库和可移植的材料包。所有内容都依据一份保存的 `presentation.json` 绘制，因此更换语言、背景、字号或
图片尺寸只需重新构建，无需重新仿真。

```bash
python -m pip install "os-optics-kit[plot,render,media]"
os-optics-kit present init run --out talk --title "Focusing lens"
os-optics-kit present build talk/presentation.json
os-optics-kit present build talk/presentation.json --out talk-zh \
    --set theme.language=zh-CN --set theme.background=light --set theme.font_scale=1.3
os-optics-kit present bundle talk-zh --out talk-zh.zip
```

尺寸、字体、方案比较、动画和交付方式见[演示材料指南](docs/zh-CN/presentation.md)。

## 案例

| 案例 | 内容 | 目录 |
| --- | --- | --- |
| [beam-expander](cases/beam-expander) | 目录元件扩束器：更换透镜、扫描间距、恢复准直 | 是 |
| [achromatic-imaging](cases/achromatic-imaging/README.zh-CN.md) | 三色原生 PSF/MTF、色焦移与装调公差 | 否 |
| [sonnar-telephoto-lens](cases/sonnar-telephoto-lens/README.zh-CN.md) | 六片式 85 mm f/2.4 Sonnar，在标称口径下测量原生 PSF、MTF、Strehl 与色焦移 | 否 |
| [midwave-infrared-imager](cases/midwave-infrared-imager/README.zh-CN.md) | 五片式 3–5 µm 成像镜头，元件厚度可制造：九个视场的原生 PSF、MTF 与 Strehl | 否 |
| [aspheric-lens](cases/aspheric-lens/README.zh-CN.md) | 非球面使五片式 f/3.5 镜头达到 25° 半视场，并与球面版本对照 | 否 |
| [focus-requirement](cases/focus-requirement/README.zh-CN.md) | 固定工作面聚焦要求、加密位置公差与报告 | 否 |
| [ideal-lens-focus](cases/ideal-lens-focus) | 理想薄透镜的高斯聚焦 | 否 |
| [expansion-focus](cases/expansion-focus) | 扩束与聚焦工作流程，含灵敏度和公差研究 | 否 |
| [polarization-control](cases/polarization-control) | 用半波片和偏振器控制偏振 | 否 |
| [mirror-alignment](cases/mirror-alignment) | 折返镜后的光束位置和指向 | 否 |
| [detector-measurements](cases/detector-measurements) | 探测器线性度与参考通道归一化 | 否 |
| [interop-optimization](cases/interop-optimization) | 扩束器的外部优化与 Sobol 公差分析 | 否 |
| [interop-diffraction](cases/interop-diffraction) | 声明光阑处的衍射，交给外部程序计算 | 否 |
| [interop-quantum](cases/interop-quantum) | 基于线性光学网络导出的 Hong-Ou-Mandel 干涉 | 否 |
| [interop-metasurface](cases/interop-metasurface) | 作为表格化虚拟元件的超表面透镜 | 否 |
| [cylindrical-light-sheet](cases/cylindrical-light-sheet) | 柱面聚焦：光片厚度与传播范围的权衡 | 否 |
| [phase-only-flat-top](cases/phase-only-flat-top) | 圆形平顶相位设计及其对入射光束宽度的敏感性 | 否 |
| [bounded-aberration-correction](cases/bounded-aberration-correction) | 仅用强度反馈校正五个有界相位模式 | 否 |

每个案例包含 `project.json`（带目标的 Project）、`CASE.md`（供 Agent 阅读）、带插图的 `README.md`
和 `expected.json`（验证结果），以及案例自己的脚本、研究规格或数据。见[案例格式（英文）](docs/case-format.md)。

## 工具与技能

| 技能 | 命令 | 用途 |
| --- | --- | --- |
| `summary` | `os-optics-kit summary` | 把已保存的运行读成一个紧凑 JSON 文档，并按结果给出退出码 |
| `edit` | `os-optics-kit edit` | 列出 Project 中的对象，按标识修改，由引擎检查并原子写入 |
| `sweep` | `os-optics-kit sweep` | 运行有界的单参数或双参数扫描，保存并统计每个样本 |
| `compare` | `os-optics-kit compare` | 按目标和平面标识比较已保存的运行或扫描样本，并给出时效 |
| `plot` | `os-optics-kit plot` | 绘制扫描曲线、热图、功率分布、条形图和直方图 |
| `report` | `os-optics-kit report` | 生成 Markdown 和 JSON 报告，含计数、诊断信息和产物哈希 |
| `render` | `os-optics-kit render` | 按指定相机角度渲染运行的三维图，可对准单个元件，并输出清单文件 |
| `present` | `os-optics-kit present` | 依据已保存的结果生成带主题的视图、结果图、比较图和动画，附图库和材料包 |
| `sensitivity` | `os-optics-kit sensitivity` | 以两种步长的有限差分灵敏度对设计参数排序 |
| `tolerance` | `os-optics-kit tolerance` | 在声明的分布下运行带种子的公差研究 |
| `candidates` | `os-optics-kit candidates` | 列出满足明确筛选条件的目录产品，按与给定值的接近程度排序 |
| `measurements` | `os-optics-kit measurements` | 扣除背景、按参考通道归一化并拟合探测器 CSV 数据 |
| `run-case` | `os-optics-kit cases` | 查找、复制、运行、核对并修改案例 |
| `contrib-case` | `os-optics-kit contrib case` | 把完成的设计整理成可直接提交拉取请求的案例 |
| `contrib-tool` | `os-optics-kit contrib tool` | 把辅助脚本打包为套件命令，附技能说明和测试 |

`os-optics-kit skills show NAME` 输出一份技能说明；[`skills.json`](skills.json) 是技能索引。工具也可以
在 Python 中导入（`import os_optics_kit`），`python -m os_optics_kit <命令>` 运行同样的命令。

## 版本对应

os-optics-kit 0.3.x 需要 `os-optics>=0.13,<0.14`，已在 os-optics 0.13.0 和目录版本 2026-10-28.1 上验证
（[`baseline.json`](baseline.json)）。每个案例记录验证时使用的引擎和目录版本。见
[案例格式（英文）](docs/case-format.md)和[引擎对齐（英文）](docs/engine-alignment.md)。

## 参与贡献

欢迎以拉取请求提交案例、工具、技能和文档。Agent 使用用户自己的 GitHub 账号贡献：
`os-optics-kit contrib case` 和 `os-optics-kit contrib tool` 验证贡献内容并打开拉取请求，或生成含补丁的
文件包，供之后提交。见 [CONTRIBUTING.md](CONTRIBUTING.md) 和[用 Agent 参与贡献（英文）](docs/contributing-with-an-agent.md)。
引擎相关的问题和缺陷报告请提交到 [os-optics 问题反馈](https://github.com/JunLin615/os-optics/issues)。

## 许可证

os-optics-kit 采用 [MIT 许可证](LICENSE)。引擎 os-optics 按其自身的许可证分发。
