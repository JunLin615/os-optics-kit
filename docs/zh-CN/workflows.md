# 设计研究与参数化工作流程

[用户指南](README.md) · [English](../en/workflows.md) · 简体中文

`examples/` 中的工作流程脚本帮助你从首次运行出发，完成一项有完整记录的设计研究：按标识修改
元件和目标、扫描参数、比较已保存的运行、绘图并生成报告，以及估计灵敏度和公差。每一步都会保存
各自的项目和运行，输出一个紧凑的 JSON 摘要，并把完整数据保存在文件中。三个参数化工作流程
把同样的步骤用于扩束与聚焦、偏振控制和光路对准。

## 准备工作

本仓库的示例和工具使用 os-optics **0.10.0**，版本记录在
[engine-baseline.json](../../engine-baseline.json)。请按[版本匹配说明](installation.md#让示例与安装版本一致)
安装该版本，并在独立环境中从仓库根目录运行命令；绘图另需安装 `requirements-examples.txt`。

第 1–3 节使用目录元件，需要 `os-optics catalog sync`；第 4、5 节使用无需目录的理想元件。
下文引用的数值是各个验证文档中运行的**参考结果**，涉及目录时采用 `2026-10-28.1`；
对齐检查会在当前版本上复现这些结果。每次新运行仍应读取其实际标识与目标判定：
引擎或目录改变时，目录内容和结果标识可能不同。

### 修改、检查、运行并读取精简结果

```bash
python examples/beam_expansion_focus.py --out run-controls --no-studies
os-optics project check run-controls/nominal/project.json
os-optics results targets run-controls/nominal/run
os-optics results freshness run-controls/nominal/run --project run-controls/nominal/project.json
```

`project check` 验证 Project 并生成引擎输入，但不运行仿真；`results targets`
读取执行情况、可选要求、目标判定及适用性问题，不重复计算；`results freshness`
将保存的标识与当前 Project 比较。[原生编辑步骤](../workflow-recipes.md#native-project-controls)
包含原子拒绝、预期哈希及响应中断后的重试方法。

### 选择计算限额

研究的 `max_evaluations` 限制设计数量，各 Project 的 `compute_budget` 限制单次设计内的工作量。
[预算示例](../workflow-recipes.md#computation-budgets)先将三透镜设计限制为每路径一次相互作用，
再将上限提高到八次。受限运行仍保存文件，并报告部分完成及受影响目标无法判定。
修改预算会使旧结果成为历史结果。选择更大的限额前应检查这些信息，并保留两次运行以便比较。

## 1. 调整设计

从目录扩束示例开始：两片平凸透镜（f = 50 mm 和 100 mm）对 780 nm、光腰半径 1 mm 的高斯光束
扩束。其目标 `expanded_waist` 要求光腰半径为 1.9961 mm，允许偏差 ±1 %。

```bash
python examples/beam_expander.py --out run-tutorial/base
python examples/edit_project.py run-tutorial/base/project.json --describe --ids L2
```

运行结果为 1.99607 mm，判定 PASS。`--describe` 按标识列出所有元件、观测平面和目标；加上
`--ids` 后，还会显示 L2 当前的位姿、其局部坐标轴在设计坐标系中的方向、所用目录产品及其有效
波段，以及可用于修改它的操作。

查找直径相同、焦距接近 100 mm 的另一片透镜作为第二片透镜：

```bash
python examples/catalog_candidates.py --category LENS --form PLANO_CONVEX_LENS --wavelength-nm 780 \
    --range focal_length_mm=95..105 --range outer_diameter_mm=12.7..12.7 --near focal_length_mm=100 \
    --limit 8 --columns optical_sku,focal_length_mm,outer_diameter_mm,working_band_nm
```

在 471 款模型适用于 780 nm 的平凸透镜中，有 8 款符合条件：5 款 f = 100 mm，2 款
f = 100.3 mm 的熔融石英透镜，1 款 f = 100.4 mm。

[`beam_expander_swap_l2.json`](../../examples/specs/beam_expander_swap_l2.json) 把 L2 换成
f = 100.3 mm 的熔融石英透镜，并新增目标 `output_beam_radius`：输出平面上的 1/e² 光束半径，
目标值和容差与原目标相同。

```bash
python examples/edit_project.py run-tutorial/base/project.json --edits examples/specs/beam_expander_swap_l2.json \
    --out run-tutorial/swapped.json --run run-tutorial/swapped-run
```

修改在写入任何文件之前先由引擎验证。新项目写入 `run-tutorial/swapped.json`，记录每个字段
修改前后数值的变更记录写入 `run-tutorial/swapped.changes.json`，原项目保持不变。运行完成，
结果为 `FAIL`（退出码 1）：`expanded_waist` 降到 1.4407 mm，而 `output_beam_radius` 以
2.0102 mm 通过。两个目标衡量的是不同的量。光腰半径反映准直程度：输出光束接近准直，瑞利
长度达数米，焦距的微小变化会把光腰移到很远处，并显著改变光腰大小；而平面上的光束半径几乎
不变。

修改写成一组操作，每个操作按标识指定要修改的对象。数值使用字段本身的单位（`_mm`、`_nm`、`_w`、`_deg`），
也可以自带单位，例如 `{"value": 540, "unit": "um"}`。`transform` 操作在设计坐标系或某个元件
自身的坐标系中平移或旋转所选对象。重复执行完全相同的命令会报告 `already_applied`，不做任何
改动。

## 2. 扫描透镜间距

[`beam_expander_l2_sweep.json`](../../examples/specs/beam_expander_l2_sweep.json) 沿光束方向
移动 L2，从 −1 mm 到 +2 mm 共 12 个取值，保留 `expanded_waist` 通过的样本，并从中选出最接近
目标值的一个。20 次计算的预算在第一次运行前检查。

```bash
python examples/sweep_parameters.py examples/specs/beam_expander_l2_sweep.json --base run-tutorial/swapped.json \
    --out run-tutorial/l2-sweep
python examples/plot_results.py sweep run-tutorial/l2-sweep --y target:expanded_waist
```

| L2 位移 (mm) | −1 | −0.5 | 0 | 0.25 | 0.4 | 0.5 | 0.54 | 0.55 | 0.6 | 0.7 | 1 | 2 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| expanded_waist (mm) | 0.7323 | 0.9911 | 1.4407 | 1.7410 | 1.9034 | 1.9780 | 1.9964 | 1.9999 | 2.0098 | 1.9923 | 1.7162 | 0.8340 |
| 结果类别 | FAIL | FAIL | FAIL | FAIL | FAIL | PASS | PASS | PASS | PASS | PASS | FAIL | FAIL |

12 个样本中有 5 个通过（通过比例 5/12）；最佳样本为位移 +0.54 mm，光腰半径 1.99645 mm。每个
样本在 `run-tutorial/l2-sweep/samples/` 下保存各自的项目和运行，`table.csv` 列出带单位的参数
值、结果类别、每个目标的数值和判定、身份信息（哈希、引擎构建和目录版本）以及运行路径。图
`run-tutorial/l2-sweep/plots/sweep_target_expanded_waist.png`（及 `.svg`）用结果类别标记显示
每个样本，并画出目标范围。

应用最佳位移（以微米给出）：

```bash
python examples/edit_project.py run-tutorial/swapped.json --edits examples/specs/beam_expander_move_l2.json \
    --out run-tutorial/final.json --run run-tutorial/final-run
```

运行通过，`expanded_waist` 为 1.99645 mm，与扫描样本完全一致。

## 3. 比较与报告

以最终项目为基准比较三次运行，无需重新运行：

```bash
python examples/compare_runs.py run-tutorial/base run-tutorial/swapped-run run-tutorial/final-run \
    --project run-tutorial/final.json --out run-tutorial/compare
```

目标和观测平面按标识对齐：`output_beam_radius` 在初始运行中显示为缺失。输入差异包括 L2 的
位置（152.841 mm → 153.381 mm）、L2 的产品以及新增的目标。每次运行都会给出相对最终项目的
时效状态：最终运行为 `CURRENT`，另外两次为 `HISTORICAL`（具体修改原因由引擎给出），因此初始运行的
通过显示为 `HISTORICAL PASS`：它属于先前的设计。

为扫描生成一份报告，附上图和更换透镜的修改记录：

```bash
python examples/build_report.py run-tutorial/l2-sweep --project run-tutorial/final.json \
    --plots run-tutorial/l2-sweep/plots --changes run-tutorial/swapped.changes.json
```

`run-tutorial/l2-sweep/report/report.md`（及 `report.json`）列出输入和修改、带分母的结果计数、
最佳样本、诊断信息、每个样本的时效状态、图的链接，以及所有产物及其 SHA-256 校验值。若要为
单独的运行生成报告，可传入多个运行目录代替研究目录。

## 4. 灵敏度与公差

这两项研究使用第 5.1 节中无需目录的扩束聚焦设计，直接由规格文件构建。

```bash
python examples/sensitivity_analysis.py examples/specs/sensitivity_expander.json --out run-tutorial/sensitivity
```

每个参数以两种步长作 ±h 扰动；脚本给出中心差分、两种步长结果的一致性、二阶差分，以及按各
参数给定公差（例如透镜轴向位置取 50 µm）缩放后的排序。扩束后的光束半径几乎只取决于 L2 的轴向
位置：0.006668 mm/mm，两种步长结果的相对差小于 1e-10。焦斑半径对聚焦平面位置最敏感，但该平面位于聚焦
光腰之前 36.5 µm，接近曲线的极小值。因此一阶导数（−0.000497 mm/mm）被标记为
`NEAR_EXTREMUM`，此处用二阶差分（0.01357 mm/mm²）描述响应。理想聚焦透镜的倾斜和偏心只在二阶
上改变焦斑半径。结果保存在 `summary.json` 和 `sensitivity.csv` 中，条形图和龙卷风图位于
`plots/`。

[`sensitivity_expander_band_edge.json`](../../examples/specs/sensitivity_expander_band_edge.json)
从 1099.8 nm 开始，比透镜 1100 nm 的波段上限低 0.2 nm。+0.5 nm 的样本超出波段，被归为超出模型
范围，因此该步长改用后向差分，并作相应标注；标称焦斑半径（0.02334 mm）未满足 0.02 mm 的上限，
以 `FAIL` 保留在输出中。

```bash
python examples/tolerance_study.py examples/specs/tolerance_expander.json --out run-tutorial/tolerance
python examples/tolerance_study.py --verify-seed run-tutorial/tolerance
python examples/tolerance_study.py examples/specs/tolerance_expander.json --out run-tutorial/tolerance-zero \
    --zero-tolerance --samples 5
```

公差研究用种子 20261018 抽取 100 个设计：L2 轴向位置在 ±0.5 mm 内均匀分布，聚焦平面位置在
±0.3 mm 内均匀分布，光源光腰半径服从 σ = 5 µm、截断于 3σ 的正态分布，各参数相互独立。抽样
偏移量在第一次运行前保存到 `samples.csv`。100 个样本中有 92 个满足全部三个目标；其余 8 个未
满足扩束后光束半径 1.5 mm ± 2 % 的要求（抽样值范围 1.462–1.540 mm）。通过比例 92/100 是在上述
分布下仿真样本中满足目标的比例。`--verify-seed` 重新抽样并确认 `samples.csv` 的字节完全相同。
第三条命令中所有偏移量为零，5 个样本全部精确复现标称项目和结果的哈希。

## 5. 参数化工作流程

每个工作流程根据你的输入构建设计并运行，再运行单参数研究，把每项研究与标称运行比较，绘制
发生变化的目标，用独立的解析计算核对引擎数值，并生成报告。`--list-parameters` 列出所有输入
及其单位和默认值。输入可以带单位后缀（`--gap-error-mm 250um`）；`--study NAME=V1,V2` 或
`--study NAME=START..STOP:COUNT` 替换默认研究，`--no-studies` 只运行标称设计。

`--out DIR` 的输出结构：`DIR/workflow.json`（完整摘要）、`DIR/nominal/`（项目、运行、解析
对照、功率图）、`DIR/studies/<参数>/`（每个样本的项目和运行、`table.csv`、比较结果、图、报告）
以及链接全部内容的 `DIR/report/report.md`。退出码反映标称设计（0 PASS、1 FAIL、2 UNKNOWN、
3 OUT_OF_MODEL）；研究中未通过的样本计入摘要。

### 5.1 扩束与聚焦

```bash
python examples/beam_expansion_focus.py --out run-tutorial/expansion
python examples/beam_expansion_focus.py --out run-tutorial/expansion-strict --no-studies --max-spot-radius-mm 10um
```

- **设计：** 高斯光源（780 nm，光腰半径 0.5 mm）依次通过由理想薄透镜 L1（f1 = 50 mm）和 L2
  （f2 = 150 mm）组成的开普勒扩束器，以及聚焦透镜 L3（f3 = 100 mm）。`gap_error_mm` 改变
  L1–L2 间距；`focus_plane_shift_mm` 移动聚焦平面。长度单位为 mm，波长单位为 nm。
- **模型：** 傍轴高斯光束通过理想薄透镜（无像差、无色散），通光孔径 25 mm，透镜声明的有效
  波段为 400–1100 nm。
- **检查项：** `expanded_radius`：L2 后 50 mm 平面上的半径在光源光腰半径 × f2/f1 = 1.5 mm 的
  ±2 % 以内；`spot_radius`：聚焦平面上的半径不超过 0.02 mm；`waist_location`：光腰与该平面的距离
  在 ±0.5 mm 以内。标称值为 1.50112 mm、0.016552 mm、−0.0365 mm，全部 `PASS`。复数 q 参数
  参考计算的偏差小于 6e-14 mm。
- **研究：** 间距误差研究中，只有间距误差绝对值 ≤ 1 mm 时焦斑通过；f3 研究中，只有 f3 ≤ 100 mm
  时焦斑通过；1064 nm 时焦斑增大到
  0.02258 mm（`FAIL`）；1300 nm 超出透镜声明的波段，样本为 `OUT_OF_MODEL`；光源光腰 3 mm 时
  带警告 `GAUSSIAN_TRUNCATION` 通过，4 mm 时为 `OUT_OF_MODEL`（`GAUSSIAN_SEVERE_TRUNCATION`）。
- **未达标示例：** 第二条命令把焦斑上限收紧到 10 µm；0.016552 mm 的焦斑半径不满足，命令退出码
  为 1。

### 5.2 偏振控制

```bash
python examples/polarization_control.py --out run-tutorial/polarization
python examples/polarization_control.py --out run-tutorial/polarization-1064 --no-studies --wavelength-nm 1064
```

- **设计：** 1030 nm 线偏振光源（1 mW，偏振方向 0°）依次通过 22.5° 的半波片和 45° 的线偏振器。
  角度单位为度，绕光束方向从设计坐标 +Y 转向 +Z 计量。
- **模型：** 理想薄琼斯元件：零级半波相位延迟片（透过率 0.99），其特性标定范围为
  1000–1060 nm，相位延迟按 1030 nm / λ 变化；线偏振器透过率 0.9，消光比 1000。
- **检查项：** `transmitted_power`：探测器处功率不低于 0.8 mW（标称 0.891 mW）；
  `output_azimuth`：半波片后的偏振方位角为 45° ± 0.5°（标称 45°）。独立的琼斯矩阵计算和马吕斯
  定律在各自容差内复现所有比较值；功率的最大相对偏差为 3.4e-15。
- **研究：** 把半波片从 0° 转到 67.5°，只有 22.5° 时两个目标都满足；15° 和 30° 时功率
  （0.831 mW）仍然通过，但方位角（30°、60°）不通过；67.5° 时偏振器只透过消光本底 0.89 µW。
  1064 nm 和 1100 nm 位于半波片的标定波段之外。
- **诊断示例：** 1064 nm 时两个目标都显示 `PASS`（0.88988 mW、44.93°），但运行在半波片处报告
  `MODEL_WAVELENGTH_OUTSIDE_BAND`。由于半波片的相位延迟只在 1000–1060 nm 内标定，工作流程把该
  设计归为 `OUT_OF_MODEL`，退出码为 3。

### 5.3 光路对准分析

```bash
python examples/alignment_analysis.py --out run-tutorial/alignment
python examples/alignment_analysis.py --out run-tutorial/alignment-synthetic --no-studies \
    --mirror-tilt-local-y-mrad 1 --synthetic-measurements
```

- **设计：** 633 nm 光源位于平面折返镜前 100 mm，入射角 45°；观测平面 `near` 和 `far` 位于
  反射镜后 100 mm 和 400 mm（半径 12.5 mm）。反射镜倾斜（mrad）使反射镜绕自身镜面内的轴转动；
  光源偏移（mm）和倾斜（mrad）改变入射光束。
- **模型：** 带声明反射率的平面镜，三维主光线精确追迹。参考计算为精确的矢量反射与平面求交，
  另加小角度规律：反射镜倾斜 θ 使光束偏转 2θ（在入射面之外为 2θ·cos 45°）。
- **检查项：** 两个平面上的光束位置在 ±0.1 mm 以内，`far` 平面上的方向在 ±0.5 mrad 以内；
  分析结果给出各平面位置和两平面指向角 1000·atan(Δ / 300 mm)（mrad）。
- **研究：** 0.25 mrad 的反射镜倾斜使光束在 `near` 上移动 0.05 mm（`PASS`），在 `far` 上移动
  0.20 mm（`FAIL`），指向角 0.5 mrad；15 mrad 时光束到达远平面边缘（`INCOMPLETE_ENVELOPE`）；
  60 mrad 时光束未到达远平面，该平面报告 `OBSERVATION_NOT_REACHED` 且没有数值，因此该样本为
  `UNKNOWN`。单纯 0.15 mm 的光源偏移使两个平面上的位置同样偏移，指向角仍为 0。
- **合成测量示例：** 第二条命令在 100–400 mm 处增加观测平面，取引擎给出的光束位置，叠加
  人为设定的残差和 0.05 mm 的人为相机零点，并在
  `run-tutorial/alignment-synthetic/synthetic/synthetic_measurements.csv` 中把每一行标记为 `SYNTHETIC`。
  [测量数据分析](measurements.md)脚本拟合得到指向角 1.9973 ± 0.0182 mrad，预期值为 2 mrad。
  1 mrad 的倾斜不满足位置容差，因此命令退出码为 1。

## 解读结果

分别读取执行情况、科学量有效性、所选目标判定和结果时效。
`MET`、`UNMET`、`INDETERMINATE`、`NOT_APPLICABLE` 是引擎目标判定；工作流程的
`PASS`、`FAIL`、`UNKNOWN`、`OUT_OF_MODEL`、`NOT_APPLICABLE` 分类用于所选要求的后续处理。
模型范围问题影响依赖该模型的量，无关量超出范围不会使其他可用结果全部失效。

所有请求的样本都保留，包括无效、部分完成及无法判定的样本。目标函数的有效性与要求的可行性是
不同字段。精确目标函数和有限差分使用有效点值，有界量保留其区间。没有可行样本时扫描会明确报告，
不会挑出无效的最优解。通过比例以全部请求样本为分母；可评估样本中的比例使用单独分母。
公差通过比例仅描述给定分布下的仿真样本结果。

比较工具使用引擎的时效判断。物理或预算修改对应 `PHYSICS_CHANGED`，目标修改对应
`TARGETS_CHANGED`。历史结果保留标识与原始判定。详见[结果解读](results.md)及
[工作流程步骤](../workflow-recipes.md)。

## 基准来源

三个工作流程参考了
[Free-Space Optical Experiment Benchmark](https://github.com/JunLin615/free-space-optical-experiment-benchmark)
（JunLin615 and contributors，提交 `e72ad3a9b6be12c7f0dbb00121fda23289c4e908`）中的任务类型。
其案例内容采用 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) 许可。这里只借鉴任务
类型：设计、数值和检查项均为这些示例重新编写，未复制任何案例文本、标准答案或评分材料。

| 工作流程 | 基准案例 | 案例修订版 | 借鉴内容 |
| --- | --- | --- | --- |
| 扩束与聚焦 | SEED-2-2 Designing a fivefold afocal beam expander | 0.2.2 | 放大率为 f2/f1 的双透镜开普勒扩束 |
| 扩束与聚焦 | SEED-7-4 Independently verify a fivefold beam expander | 0.1.0 | 通过平面上的光束尺寸和光腰位置进行验证 |
| 偏振控制 | SEED-1-7 Half-wave plate and linear polarization | 0.2.1 | 半波片使线偏振方向转过其角度的两倍 |
| 偏振控制 | SEED-1-8 Continuously adjustable free-space attenuation | 0.1.0 | 固定偏振器前的可旋转半波片用作衰减器 |
| 光路对准分析 | SEED-1-2 Establishing an axis with two steering mirrors and two apertures | 0.1.0 | 在两个分开的平面上检查位置和方向 |
| 光路对准分析 | EX-ALIGN-01 Two-iris alignment diagnosis | 1.0.0 (optics-examples-v1) | 利用两个距离区分位置误差和角度误差 |

每个 `workflow.json` 都包含相同的来源记录及署名说明。内容哈希、运行时间和全部已核对数值见
[工作流程验证记录](../workflow-validation.md)和[灵敏度与公差验证记录](../sensitivity-tolerance-validation.md)（英文）。

下一步：[读取与导出结果](results.md)或[联用外部科学工具](interop.md)。
