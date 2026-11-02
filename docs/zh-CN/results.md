# 结果、可视化与导出

[用户指南](README.md) · [English](../en/results.md) · 简体中文

每次运行都会生成完整的设计与仿真记录。结果 API 直接读取该记录，查看或导出数据时无需重新仿真。

## 阅读运行目录

| 文件 | 内容 |
| --- | --- |
| `manifest.json` | 整体状态、目标结果、标识和文件哈希 |
| `project.json` | 本次运行的光学项目 |
| `engine_request.json` | 传入物理引擎的输入 |
| `simulation_result.json` | 目标数值、光束、观测结果、路径、功率收支和问题记录 |
| `ray_data.json` | 主光线与瞳面采样光线的追迹数据，包括表面交点和光程 |
| `local_validation.json` | 本地验证记录，关联项目、目录、引擎和结果标识 |
| `optical_scene.json` | 结构化光学场景 |
| `optical_scene.glb` | glTF 二进制格式的三维场景 |

```bash
os-optics results describe run
os-optics results monitor run output
os-optics project verify run
```

`output` 是扩束示例中的观测平面标识。自己的项目可以通过 `results describe` 查看可用观测平面。

## 理解结果

以下精简控制接口对应 0.10.0；请按
[版本匹配说明](installation.md#让示例与安装版本一致)安装匹配的软件包。

当某个科学量超出元件模型声明的波长、角度或光束范围时，依赖它的目标判定为 `INDETERMINATE`，
并在 `applicability_issue_codes` 中给出适用范围问题代码。该科学量仍保留数值、单位和上下界：
数值可读，但判定明确表示它不能说明声明范围外的结果有效。需要 `MET` 或 `UNMET` 判定时，
请将计算保持在适用范围内；具体案例见
[超表面验证记录](../interop-validation.md)。

```bash
os-optics results targets run
os-optics results freshness run --project project.json
```

分别读取以下信息：

| 维度 | 查看内容 | 含义 |
| --- | --- | --- |
| 计算执行 | `outcome.execution` 中的完成情况、终止原因和预算 | 请求的计算是否完成，哪些限额已用尽 |
| 科学量 | 指标的数值、单位、有效性、上下界及适用性问题 | 可用点值、区间、不可用的量或超出模型范围的量 |
| 客户要求 | 各目标的 `assessment` 与 `outcome.requirements` | `MET`、`UNMET`、`INDETERMINATE` 或 `NOT_APPLICABLE`；未配置目标表示未评估要求 |
| 结果时效 | `CURRENT`、`HISTORICAL` 或 `UNDETERMINED` 及原因代码 | 保存的运行是否对应给定 Project |
| 商业结果 | 配置或订单命令的响应 | 在该操作条件下哪些内容可以采购或下单 |

一个汇总 `PASS` 不能同时回答这五个问题。计算完成时，可选目标可能未满足，但仍有可用的科学量。
应读取所用科学量或目标的适用性：无关的模型范围问题不会使所有结果失效。
有界量应保留为区间；如果区间无法确定比较结果，目标应保留为无法判定。

物理参数或计算预算修改会报告 `PHYSICS_CHANGED`，仅修改目标会报告 `TARGETS_CHANGED`。
保留原始运行及其哈希。修改目标不会把旧判定变成新判定。只要 Project 与转换后的引擎输入一致，
其他引擎构建生成的运行仍可能为 `CURRENT`；复现时还应查看 `engine_build_matches`。
详见[比较工作流程](../workflow-recipes.md#compare-and-freshness)。

`project verify` 检查记录中的文件哈希及一致性，不会与独立光学解或实验进行比较。
工作示例中的解析参考提供单独的校验，并注明适用假设。

`results targets` 成功返回时退出码为 0，即使目标未满足；`results freshness` 的退出码
0、1、2 分别表示 `CURRENT`、`HISTORICAL`、`UNDETERMINED`。科学任务是否完成应依据结构化
判定，不能仅根据命令退出成功。

## 示例中的精简摘要

在仓库根目录运行示例：

```bash
python examples/beam_expander.py --out run-example
python examples/ideal_lens_focus.py --out run-ideal-focus
```

每个示例向标准输出写入一个 JSON 摘要。结构化字段名使用英文，诊断信息写入标准错误。默认输出包含整体 `status`、`engine_status`、可为空的 `validation_status`，以及目标标识、指标、实际值、单位、适用的上下界、判定结果和可用的消息或问题代码。摘要还保留需要处理的 `issues`、`model_scope_issues` 和 `element_models`，以及 `identity` 和 `integrity` 信息。存在功率数据时，精简的 `power_budget` 仅包含标量汇总值和 `total_sinks`；添加 `--details` 可查看完整功率明细。

`artifacts` 和 `exports` 字段指向磁盘上的完整数据。扩束示例保存原生本地运行记录和到达光束 CSV，并提供打开三维场景的 `view_command`。理想透镜示例无需下载目录，会将请求、仿真结果、光线和到达光束数据保存在 `--out` 指定的目录中。其底层 API 结果不包含原生本地运行清单或三维查看器命令。两个示例的默认输出目录分别为 `run-example` 和 `run-ideal-focus`。

读取已保存的原生本地运行记录，无需重新仿真：

```bash
python examples/result_summary.py run-example
python examples/result_summary.py run-example --details
```

摘要工具先检查保存文件的完整性，再通过公开结果 API 生成摘要，同时保留执行、要求和科学量信息以及旧状态字段。此命令读取扩束示例或 `os-optics project run` 生成的原生本地运行产物；理想透镜示例的输出是底层 API 结果文件集。[result_summary.py](../../examples/result_summary.py) 提供可复用的 Python 函数 `summarize_results(results, *, artifacts=None, details=False)` 和 `summarize_run(directory, *, details=False)`，两者均返回可用于 JSON 输出的字典。

示例脚本和已保存运行摘要命令使用 `requirement_outcome` 决定退出码；记录同时保留原始
汇总 `status`、`outcome.execution` 和 `outcome.requirements`。工具退出码如下：

退出码 `0` 描述所选要求的判定，并不表示执行完整。例如，无目标且受到计算预算限制的运行，
可同时保留 `execution.outcome: PARTIAL`、汇总 `status: UNKNOWN` 和
`requirement_outcome: NOT_APPLICABLE`，并以 `0` 退出。使用数值前仍须检查执行情况和量的有效性。

| 退出码 | 报告的结果 |
| --- | --- |
| `0` | 所选要求满足或不适用 |
| `1` | 所选要求未满足 |
| `2` | 所选要求无法判定 |
| `3` | 所选科学量超出模型范围 |
| `4` | 输入、产物完整性、运行时或命令用法错误 |

设计结果即使未通过，也会生成 JSON 摘要并保留完整数据。输入或运行时异常会向标准错误写入 JSON 错误信息，标准输出为空，退出码为 `4`。若已保存的运行记录表明执行失败或取消，标准输出仍保留 JSON 结果摘要，退出码同样为 `4`。这些示例错误码与引擎 CLI 的文件和用法错误码分别定义。

理想透镜示例可通过 `--maximum-radius-mm` 修改目标上限（默认 `0.05`）。以下命令分别使用默认目标、预期不通过的更严格目标和无效输入：

```bash
python examples/ideal_lens_focus.py --out run-focus-default
python examples/ideal_lens_focus.py --out run-focus-strict --maximum-radius-mm 0.001
python examples/ideal_lens_focus.py --maximum-radius-mm -1
```

半径上限必须为有限非负数，`nan` 也属于无效输入。每条命令运行后，可立即在 PowerShell 中查看 `$LASTEXITCODE`，或在 POSIX shell 中查看 `$?`。两个示例均支持 `--details`，用于在摘要中包含完整功率明细；完整光线数据可通过已保存的产物路径访问。

## 查看与分享

```bash
os-optics view run
os-optics view run --write-html view.html
os-optics view run --snapshot shots
```

浏览器查看器提供预设视角、元件选择、光束显隐和中英文标签。截图输出包含正交视图 PNG、`scene_summary.json` 和图例。

## 导出表格

```bash
os-optics export arrivals run --format csv --out arrivals.csv
os-optics export rays run --format jsonl --out rays.jsonl
os-optics results columns arrivals
os-optics results metrics --name waist_distance_x_mm
```

可导出的内容包括观测面、到达光束、光线、路径、路径分段、功率收支、叠加结果、波前、辐照度和指标定义。使用 `export --help` 查看格式与筛选参数。波前导出采用追迹光程差，辐照度图采用近轴高斯光束叠加模型。

Python 调用方式：

```python
from pathlib import Path
from os_optics.results import load_run

results = load_run("run")
print(results.describe())
print(results.power_budget())
Path("arrivals.csv").write_bytes(results.export("arrivals", "csv"))
```

## 单位与约定

API 使用毫米表示长度、纳米表示真空波长、角度制表示角度、瓦特表示功率。高斯光束尺寸 `w` 表示**光强降至 1/e² 处的半径**。坐标系为右手系，透射元件的局部 `+z` 为光轴，反射元件的局部 `+z` 为镀膜表面向外的法线。导出列名和指标说明会标明相应单位。

下一步：[从设计到硬件](hardware.md)。
