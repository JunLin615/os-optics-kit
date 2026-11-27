# 优化

[os-optics-kit](../../README.zh-CN.md) · [English](../en/optimize.md) · 简体中文

`os-optics-kit optimize` 在边界内细化一个 Project 的连续参数。JSON 规格给出变量（带 `"$value"` 的编辑操作）、
要推向目标值的量，以及候选设计必须满足的限值。SciPy 的有界局部求解器（信赖域反射最小二乘，或 SLSQP）选择候选设计；
引擎通过[评估适配器](evaluation-adapter.md)在同一个 `os_optics.local.EvaluationSession` 上评估每一个候选。命令把每次
计算写入历史记录，写出最佳可行 Project，并给出结果：搜索为何停止、哪些限值在制约设计、哪个视场最差。

## 运行

示例是 [interop-optimization](../../cases/interop-optimization) 中的扩束器：移动 L2 使 532 nm 光束准直。

```bash
os-optics-kit cases copy interop-optimization --dest .
cd interop-optimization
os-optics-kit optimize studies/optimize-expander.json --out opt
os-optics-kit optimize studies/optimize-expander.json --out opt-slsqp --method slsqp
os-optics-kit optimize studies/optimize-expander-infeasible.json --out strict
```

选项：`--method trf|slsqp` 与 `--max-evaluations N` 覆盖规格中的 `solver`；`--overwrite` 替换 `--out` 中上一次运行
的文件（否则目录必须是新的或空的）。SciPy 与 numpy 随 os-optics 安装。

## 规格

```json
{
  "schema": "os-optics-kit.optimize-spec", "schema_version": 1,
  "description": "Collimate the expander: move L2 along the axis",
  "project": "../project.json",
  "variables": [
    {"name": "separation", "unit": "mm", "bounds": [150, 250], "scale": 1.0,
     "ops": [{"op": "set_component_pose", "instance_id": "L2",
              "position_mm": [{"$value": {"offset": 100.0}}, 0.0, 50.0]}]}
  ],
  "objectives": [{"name": "divergence", "source": {"target": "collimated"}, "goal": 0.0, "scale": 1e-4}],
  "constraints": [{"name": "collimated", "source": {"target": "collimated"}, "maximum": 1e-3, "scale": 1e-4}],
  "solver": {"method": "trf", "max_evaluations": 120,
             "tolerances": {"ftol": 1e-12, "xtol": 1e-12, "gtol": 1e-12}, "diff_step": 1e-3}
}
```

| 字段 | 含义 |
| --- | --- |
| `project` | 基础 Project：相对规格文件的路径，或 Project 对象本身；对带配置的 Project，`configuration` 指定要优化的状态（先展平） |
| `variables[]` | `name`、`unit`、`ops`（`os-optics-kit edit` 的编辑操作，`"$value"` 为取值；`{"$value": {"offset", "scale"}}` 写入 `offset + scale * value`）、`bounds`、`nominal`（省略时从基础 Project 中这些操作写入的字段读取，`transform` 为 0）、`scale`（变量的步长单位，默认 1） |
| `objectives[]` | `name`、`source`、`goal`、`scale`、`weight`：残差为 `sqrt(weight) * (value - goal) / scale` |
| `constraints[]` | `name`、`source`、`minimum` 和/或 `maximum`、`scale`：`margin` 是到最近限值的缩放距离，在限值内为正 |
| `aggregation`、`value_use`、`imaging`、`invalid_residual` | 同评估适配器 |
| `ray_loss` | `infeasible`（默认）或 `report`：光瞳光线丢失意味着什么 |
| `active_margin` | 约束的 \|margin\| 不超过此值时视为起作用（默认 1e-3） |
| `solver` | `method`（`trf`、`slsqp`）、`max_evaluations`（默认 200）、`max_iterations`、`tolerances`（`ftol`、`xtol`、`gtol`；默认 1e-8）、`diff_step`（默认 1e-3 个变量尺度）、`difference`（`central`、`forward`）、`x_scale`（`variables`、`jac`） |
| `verify` | 施加在最佳 Project 上做第二次检查的 `ops` 与 `imaging`（例如更密的光瞳采样），以及复现检查的 `tolerances`（`relative` 1e-9、`absolute` 1e-12） |

量的来源：`{"target": ID}`（声明的目标）、`{"rays": {"target": ID}}`（光斑目标的光瞳光线到达其平面的比例）、
`{"paraxial": {"components", "wavelength_nm", "quantity"}}`（引擎近轴分析的 EFL、BFL 等）、
`{"solids": {"quantity", "component", "member"}}`（光学实体检查中最薄的玻璃间隙）以及 `{"mtf": {...}}`
（MTF 下限，只能作约束）。[评估适配器](evaluation-adapter.md#量的来源)逐一说明。

编辑操作 `set_optical_model_value {model_id, path, value}` 用 JSON 指针定位声明模型中的一个数，例如
`/surfaces/1/radius_mm`、`/surfaces/0/conic` 或 `/surfaces/0/aspheric_coefficients/0`；十进制字符串仍写成字符串。
它可用于 `os-optics-kit edit` 和变量模板，省略的 `nominal` 也从它读取。

整个规格在任何计算之前检查。未知键、格式错误的来源、把 MTF 作为目标量、`trf` 配合 `sum_squares` 以外的聚合方式、
超出边界的 nominal、无法施加的操作，都以 `SPEC_INVALID` 拒绝，并给出出错字段的 `path`（退出码 4）；字段间不一致的
nominal 为 `NOMINAL_AMBIGUOUS`，带配置却未给出 `configuration` 的 Project 为 `CONFIGURATION_REQUIRED`。

## 方法

- 求解器在缩放变量 `z = (x - start) / scale` 中工作，从 nominal 值出发。
- `trf` 是对适配器残差向量做 `scipy.optimize.least_squares(method="trf")`：先是各目标量的误差，再是每个约束的违反量
  `max(0, -margin)`，作为二次罚项。它要求 `aggregation: sum_squares`。
- `slsqp` 是对适配器目标函数做 `scipy.optimize.minimize(method="SLSQP")`，每个约束裕量 `>= 0` 作为不等式约束。
  它只使用 `ftol`。
- 导数用中心差分，步长为 `diff_step` 个变量尺度，在边界处改用单侧差分。每次探测都是一次引擎计算：计数、按精确的
  候选向量缓存（重复的点不再计算）、写入历史，并且从不越出边界。
- `max_evaluations` 是引擎计算次数的硬上限，起点与每次探测都计入：会超出上限的那次计算不运行。
- 可行性取自适配器：每个约束都成立，并且在 `ray_loss: infeasible` 下，没有目标量或约束读到丢失了光瞳光线的目标。
  最佳候选是目标函数最小的可行计算。
- 搜索是确定的：同一规格与引擎给出同样顺序的同样计算。

丢失的光线。光斑 RMS（`spot_rms_mm`）只统计到达平面的光瞳光线，因此搜索可能靠渐晕把光斑变小。在默认的
`ray_loss: infeasible` 下，丢失光瞳光线的候选设计不可行。起点已经丢失光线时，请设置 `ray_loss: report`，并用
`rays` 约束限制丢失，如下面的非球面示例。`rays` 的值以一条光线为台阶变化：其有限差分导数为零，或在探测点跨过台阶时
出现尖峰，因此两种求解器都无法预见这一限值。请把变量边界限定在满足该限值的起点附近。最佳候选始终只从可行的计算中选取。

起作用的约束。`trf` 把每个约束当作二次罚项，因此约束起作用时它会收敛到约束之外不远处。此时其最后一点不可行：终止原因为
`CONVERGED_INFEASIBLE`，状态为 `STOPPED`，`best` 为最佳的可行计算。最后一点与每个限值的差距在 1e-6（以约束尺度计）以内时（SLSQP 落在起作用的非线性约束上即如此），视为在限值上。预计约束会起作用时，请用 `slsqp`，或减小约束的
`scale`（罚项更陡）。

## 读取结果

| 状态 | 退出码 | 含义 |
| --- | --- | --- |
| `CONVERGED` | 0 | 求解器在可行点上达到容差（`termination.solver_index`） |
| `NO_FEASIBLE` | 1 | 没有可行的候选：不写 `best-project.json`；给出 `closest`，`history.jsonl` 中有每个裕量 |
| `STOPPED` | 2 | 预算、迭代上限、求解器停止，或在不可行点上收敛，存在可行的最佳设计 |
| `INTERRUPTED` | 130 | Ctrl-C；保留已写出的文件 |

`--out` 中有 `spec.json`（实际运行的规格：补全默认值，内联基础 Project，可单独再次运行）、`history.jsonl`（每次
引擎计算一行：`index`、`role`（`start`、`step`、`jacobian`）、`candidate`、目标函数、数值、裕量、`infeasible`、
`rays_lost`、`reason`、`seconds`、标识哈希）、`result.json` 与 `best-project.json`。`result.json`（schema
`os-optics-kit.optimize` v1）：

- `termination`：`reason`（`CONVERGED`、`CONVERGED_INFEASIBLE`、`BUDGET_EXHAUSTED`、`ITERATION_LIMIT`、
  `SOLVER_STOPPED`、`INTERRUPTED`）、求解器的消息与状态、其最后一点（`solver_x`）及该点的计算序号（`solver_index`）；
- `counts`：`evaluations`（按角色）、`cache_hits`、`iterations`（预算在求解器返回前终止搜索时为 null）、`seconds`、
  `verification_evaluations`；
- `variables`：每个 nominal 及其读取来源（`given`、`base`、`default`）；
- `best` 与 `start`：候选、目标函数、每个目标量与约束的数值、裕量、`active` 及其 `conditions`（目标、平面、光源、
  波长、量、起决定作用的间隙）、`active_constraints`、`worst_residual`、`ray_loss`、标识；没有可行设计时由 `closest`
  代替 `best`；
- `feasible_count`、`infeasible_reasons`（`CONSTRAINT_VIOLATED`、`RAYS_LOST`、无效候选的原因码）；
- `verification`：`reproduction` 在新会话中评估写出的 `best-project.json`，比较每个数值、裕量与残差（`REPRODUCED`
  或 `MISMATCH`）；`dense` 施加 `verify` 的操作与成像设置后再评估一次，列出每个数值的前后值以及可行性是否改变；
- `identity`：引擎版本与构建、目录、基础 Project 哈希、`start_reproduces_base`、规格摘要。

## 验证

扩束器（无需目录，os-optics 0.14.0）。案例脚本独立的 ABCD 最优解为 200.0572873 mm。

| 规格 | 方法 | 状态 | 计算次数 | 间距（mm） | 与 ABCD 之差 |
| --- | --- | --- | --- | --- | --- |
| `optimize-expander.json` | `trf` | `CONVERGED` | 42 | 200.0572870 | 3e-7 mm |
| `optimize-expander.json` | `slsqp` | `CONVERGED` | 9 | 200.0572869 | 3e-7 mm |
| `optimize-expander-infeasible.json` | `trf` | `NO_FEASIBLE` | 42 | closest：200.0572870 | closest 的裕量 −1.128 |

非球面镜头（[aspheric-lens](../../cases/aspheric-lens)，`studies/optimize-aspheric.json`）：五个变量（L1 前表面
半径、L5 后表面半径、L5 前表面四阶系数、L2 前表面二次曲面系数、像面位置）各在局部范围内，九个光斑 RMS 目标作为
目标量，近轴 EFL 保持在 50 ± 0.25 mm，玻璃厚度至少 3 mm，并用 `rays` 约束限制渐晕（轴上与 12.5° 的光瞳光线全部
到达，25° 至少 75 %）。两种方法使用相同的起点、边界、要求与预算（150 次计算）；os-optics 0.14.0，耗时在共享
机器上测得。

| 方法 | 状态 | 计算次数（起点 / 步 / 探测） | 可行 | 目标函数（起点 162.0） | 25° F 光斑（起点 86.2 µm） | 耗时 |
| --- | --- | --- | --- | --- | --- | --- |
| `trf` | `STOPPED`（预算） | 150（1 / 20 / 129） | 55 | 94.09 | 66.4 µm | 280 s |
| `slsqp` | `STOPPED`（预算） | 150（1 / 29 / 120），缓存命中 218 次 | 11 | 161.92 | 86.2 µm | 213 s |

`trf` 的最佳设计 EFL 为 50.137 mm，满足所有限值；用加倍的光瞳环再评估仍可行（目标函数 93.80，25° C 的到达比例
0.750，正好在下限）。SLSQP 把 `rays` 限值线性化为零导数，一步就深入渐晕区并在那里循环：它的最佳设计是起点旁的一个
有限差分探测点。不加 `rays` 约束时，`trf` 的目标函数降到 26.0，但 25° 视场的 96 条光瞳光线只到达 44、27 和 19 条：
光斑是靠渐晕变小的，这正是这些约束要防止的。

## 在 Python 中复用

```python
from os_optics_kit import optimize

result = optimize.run("studies/optimize-expander.json", "opt", method="slsqp")
print(result["status"], result["best"]["candidate"], result["counts"]["evaluations"])
```

`optimize.run(spec, out_dir, *, session=None, session_factory=None, adapter_options=None)` 写出目录并返回
`result.json`；`optimize.load_spec` 只校验规格而不计算。`os_optics_kit.study.solvers` 包含求解器：
`solve(adapter, Settings(...), recorder)` 驱动任意评估适配器 `Adapter`，返回每次计算及停止原因；`best` 与 `closest`
从中挑选。

相关：[评估适配器](evaluation-adapter.md)、[研究](studies.md)、用 `os-optics-kit sensitivity` 给变量排序并检查最优解。
