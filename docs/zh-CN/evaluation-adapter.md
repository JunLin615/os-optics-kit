# 评估适配器：为你自己的求解器提供参数、目标与约束

[os-optics-kit](../../README.zh-CN.md) · [English](../en/evaluation-adapter.md) · 简体中文

`os_optics_kit.study.evaluation_adapter` 把一个 Project 变成以具名参数为自变量的函数，供你自己编写的优化器、
求根器或扫描调用。每次调用编辑 Project，在同一个 `os_optics.local.EvaluationSession`（os-optics 0.14）上只评估
所声明的量需要的部分，并返回定长残差向量，以及其背后的每个数值、裕量和原因。若要在工作进程中评估一批相互独立的
Project，请改用研究执行器（[研究](studies.md#候选设计与外部搜索代码)）。若要做带历史记录、给出最佳可行 Project 的
有界局部搜索，请用基于本适配器的 `os-optics-kit optimize`（[优化](optimize.md)）。

## 声明问题

```python
import json
from pathlib import Path

from os_optics_kit.study.evaluation_adapter import Adapter, Constraint, Objective, Problem, Variable

REFOCUS = [{"op": "transform", "selector": {"ids": ["focus"]}, "frame": "design", "translation_mm": ["$value", 0, 0]}]
problem = Problem(
    project=json.loads(Path("project.json").read_text(encoding="utf-8")),
    variables=[Variable("refocus", REFOCUS, (-2.0, 2.0), unit="mm", scale=0.1)],
    objectives=[Objective("spot", {"target": "spot_radius"}, scale=0.02)],
    constraints=[Constraint("waist", {"target": "waist_location"}, minimum=-0.5, maximum=0.5, scale=0.5)])
adapter = Adapter(problem)
result = adapter.evaluate({"refocus": 0.3})   # 或 adapter.evaluate([0.3])，按声明顺序
```

| 部分 | 字段 |
| --- | --- |
| `Variable` | `name`、`ops`（`os-optics-kit edit` 的编辑操作；`"$value"` 为以 `unit` 计的取值）、`bounds`（有限，下限 < 上限）、`nominal`、`scale` |
| `Objective` | `name`、`source`、`goal`、`scale`（> 0）、`weight`（>= 0） |
| `Constraint` | `name`、`source`、`minimum` 和/或 `maximum`（有限，`minimum <= maximum`）、`scale`（> 0） |
| `Problem` | `project`、`variables`、`objectives`、`constraints`、`aggregation`（`sum_squares`、`sum_abs`、`max_abs`）、`imaging`、`invalid_residual`、`value_use`（`exact`、`reported`）、`ray_loss`（`infeasible`、`report`） |

目标与约束的名称合起来必须唯一：它们是残差的名称（`problem.residual_names`）。违反这些规则的声明在构建
`Problem` 时抛出 `ProblemError`。

## 量的来源

- `{"target": TARGET_ID}`：Project 声明的某个目标，经引擎的 `requirements` 评估。
- `{"paraxial": {"components": [ID, ...], "wavelength_nm": W, "quantity": Q}}`：引擎对候选设计中这些元件在 `W`
  处的近轴分析（`os_optics.local.paraxial_analysis`）；`Q` 为 `efl_mm`、`bfl_mm`、`ffd_mm`、
  `front_principal_plane_mm` 或 `rear_principal_plane_mm`（可选的 `axis`（`X` / `Y`）与 `source_id` 含义同该分析）。
  二次曲面系数与多项式项不改变近轴光焦度。分析给出 null 的值（无焦双透镜的 `efl_mm`）为 `NO_VALUE`；分析拒绝时为
  `ANALYSIS_REFUSED`（stage `paraxial`）。同一候选设计中，同一请求的所有量共用一次分析。
- `{"solids": {"quantity": Q, "component": ID, "member": ID}}`：光学实体检查（`solid_check`）：所选元件（以及
  COMPOUND 成员；不指定时为全部元件）各玻璃间隙中 `Q`（`min_thickness_mm`、`edge_thickness_mm`、
  `centre_thickness_mm`、`clear_aperture_min_thickness_mm`）的最小值。读数在 `binding` 中给出起决定作用的间隙
  （实例、成员、面、介质）。
- `{"mtf": {"observation": ID, "frequency_cyc_per_mm": F, "direction": "tangential"}}`：平面 `ID` 成像分析的 MTF
  曲线，在频率 `F` 处读取。MTF 只能作约束：MTF 来源，或指标名包含 `mtf` 的目标，作为目标量时会被拒绝。

成像分析为每个光源和波长各给出一条 MTF 曲线：一个光源三个波长为 `SRC#0`、`SRC#1`、`SRC#2`，三个视场点三个波长为
`SRC0_0#0` … `SRC2_2#0`。MTF 约束覆盖其方向上的每一条曲线，除非用 `source_id`、`wavelength_nm`（在 0.05 nm 内匹配）
或 `quantity_id` 缩小范围。约束在每条选中的曲线上都成立时才成立。其结果给出起决定作用的那条曲线（`quantity_id`、
`source_id`、`field_id`、`wavelength_nm`、`value`、`margin`），并在 `members` 中列出每条曲线。没有匹配任何曲线的
选择得到原因 `MTF_SOURCE_NOT_FOUND`，其中列出分析给出的曲线。

`Problem.imaging` 是成像请求（`launch_grid`、`psf_half_width_um`、`psf_samples` 等）。不给出时适配器使用
`launch_grid` 24，这对许多光瞳采样不足：曲线随之为 `OUT_OF_MODEL_SCOPE`。请传入案例自己的请求，例如
`cases/achromatic-imaging/imaging-request.json`。当某个平面的每个 MTF 约束都指定了 `source_id`（至多 32 个）时，
请求只分析这些视场；当每个约束都指定了 `wavelength_nm` 时，只分析与其相差 0.05 nm 以内的引擎谱线；请求自己写明了
`fields` 或 `wavelengths_nm` 时保持不变。引擎逐条谱线独立分析，因此数值不变，追迹的谱线更少。每个 MTF 结果记录其读取
所用的请求（`imaging_request`）。

## 丢失的光线

`spot_rms_mm` 与 `spot_centroid_*` 由到达平面的光瞳光线计算：途中被截掉的光线不计入 RMS，因此渐晕可能让光斑变小。
这类目标的读数带有它所选到达光束（平面、`source_id`、`wavelength_nm`、`path_id`、`crossing_index`）的
`pupil_rays_launched` 与 `pupil_rays_arrived`；来源 `{"rays": {"target": ID}}` 读取这类目标的两者之比
`arrived / launched`（单位 1），约束可借此限制渐晕。`Problem.ray_loss` 决定丢失意味着什么：`"infeasible"`（默认）使候选
设计 `feasible: false`，并在 `infeasible` 中列出 `RAYS_LOST`；`"report"` 只做记录。两种情况下残差都相同，`ray_loss`
列出读到丢失的每个目标量或约束。

## 数值是什么

每个读数都有 `value_kind`，规则与研究表格相同：`EXACT`、`BOUNDED`（下界或上界）、`QUANTITY_UNKNOWN`（估计值、
`UNKNOWN` 量，或细化检查未收敛的 MTF 曲线）、`OUTSIDE_MODEL`（状态为 `OUT_OF_MODEL_SCOPE`、有指向该目标的模型范围
发现、有效性检查未通过）、`UNDECIDED`（目标状态不是 PASS、WARNING 或 FAIL）或 `NO_VALUE`。目标读数还带有引擎的
`status` 和 `assessment`；MTF 读数带有曲线的 `state`、`convergence` 以及 `value_basis` 或 `basis_note`。

`value_use="exact"`（默认）时只使用 `EXACT` 数值；其他任何读数都使候选无效，原因为 `QUANTITY_UNAVAILABLE`。
`value_use="reported"` 时每个有限数值都用于残差并记录其类别；由非精确数值读出的约束，`satisfied` 为 `null`，
除非有精确数值违反它；有约束未确立时 `feasible` 为 `null`。

## 结果

`CandidateResult`（`as_dict()` 输出 JSON）：

| 字段 | 含义 |
| --- | --- |
| `valid` | 候选已评估，且每个量都被使用 |
| `residuals` | 始终有 `len(objectives) + len(constraints)` 项：每个目标为 `sqrt(weight) * (value - goal) / scale`，随后每个约束为 `max(0, -margin)` |
| `objective` | 目标误差按 `aggregation` 的汇总（不计约束违反） |
| `residual_objective` | `sum(r ** 2 for r in residuals)`；`sum_squares` 时等于 `objective + sum(violation ** 2)`，且 `dls_compatible` 为真 |
| `feasible` | 每个约束都成立（`true`）、有约束被违反或在 `ray_loss: "infeasible"` 下丢失了光瞳光线（`false`），或未确立（`null`） |
| `infeasible`、`ray_loss` | 有效候选设计不可行的原因（`CONSTRAINT_VIOLATED`、`RAYS_LOST`）；读到光瞳光线丢失的读数 |
| `objectives`、`constraints` | 每个声明项一条，含数值、类别和依据；候选无效时为空 |
| `evaluation`、`identity` | 引擎的评估范围和这些数值的身份 |
| `reason` | 候选无效时：`code`、`stage`、`message` 及细节 |

无效候选保持残差长度：每项为 `invalid_residual`（默认 1000；应取大于有效候选可能达到的任何残差），
`OUT_OF_BOUNDS` 时随超出边界的距离增大，上限为 `sqrt(sys.float_info.max / (2 * len(residuals)))`，使残差平方和不超过浮点范围的一半
（距离本身溢出时 `violation` 为 `null`）。每个结果的 `residuals`、`objective` 和 `residual_objective` 都是有限数
或 `null`，因此 `json.dumps(result.as_dict(), allow_nan=False)` 可以接受它们。`evaluate` 对任何候选都不抛出异常。
原因代码：

| 代码 | 何时 |
| --- | --- |
| `OUT_OF_BOUNDS` | 取值超出边界，或不是有限实数 |
| `UNKNOWN_VARIABLE`、`MISSING_VARIABLE` | 候选给出了问题未声明的变量，或缺少某个变量（序列：取值过多或过少） |
| `EDIT_REFUSED` | 编辑操作拒绝了该取值 |
| `PROJECT_REFUSED`、`PLAN_REFUSED`、`RUN_STOPPED` | 引擎拒绝了 Project 或评估计划，或已停止 |
| `ANALYSIS_REFUSED` | 成像或近轴分析拒绝了请求（`stage` 为 `imaging` 或 `paraxial`） |
| `MTF_SOURCE_NOT_FOUND` | MTF 选择没有匹配任何曲线 |
| `QUANTITY_UNAVAILABLE` | 某个量未计算，或其数值在 `value_use` 下不被使用 |
| `RESIDUAL_NOT_FINITE` | 某个残差或裕量溢出（残差溢出时，`not_finite` 列出这些残差，`residuals` 中对应位置为 `null`） |
| `AGGREGATE_NOT_FINITE` | 每个残差都有限，但 `objective` 或 `residual_objective` 超出浮点范围（约 1e154 及以上的残差平方后即超出）；`overflowed` 指明是哪一个，`residuals` 和 `largest_residual` 保留有限值。增大尺度或减小权重可使其回到范围内 |
| `ENGINE_ERROR` | 引擎的其他错误 |

一个会话一次只评估一个候选：不要从多个线程调用同一个 `Adapter`，或共享同一会话的多个适配器。并行评估时，
每个进程创建一个 `Adapter`。

## 示例

[`cases/expansion-focus/adapter_example.py`](../../cases/expansion-focus/adapter_example.py) 扫描扩束/聚焦设计的
重新调焦偏移（包括一个超出边界的候选），并以 JSON 打印每个结果：

```bash
python cases/expansion-focus/adapter_example.py
```
