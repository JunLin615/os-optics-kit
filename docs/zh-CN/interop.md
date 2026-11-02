# 外部科学工具联用

[用户指南](README.md) · [English](../en/interop.md) · 简体中文

OpenSci 计算光路、高斯光束、偏振、功率以及设计的线性光学网络。优化器、衍射程序、量子光学模拟器和电磁求解器在你自己的 Python 环境中运行，读取 OpenSci 导出的数据。下面四个示例都有独立参考比较的验证记录；版本、输入、输出和偏差见[验证记录](../interop-validation.md)。

更新后的引擎调用脚本使用 os-optics **0.10.0**，匹配的安装方式见
[版本匹配说明](installation.md#让示例与安装版本一致)。
下文数值比较描述已经记录的验证运行，另行标明的新结果除外。 它们用到带逐路径群延迟的线性光学网络、原生元件和 `TABULATED_THIN_LENS` 模型类型。运行引擎的脚本会检查已安装版本，引擎版本过旧时给出明确提示并停止；外部侧脚本只读取导出文件，不导入引擎。

| 专题 | OpenSci 提供 | 外部工具计算 | 示例 |
| --- | --- | --- | --- |
| [优化与容差](#优化与容差) | 每个设计的运行、目标指标、运行身份 | 下一组设计值（SciPy、Optuna）、敏感度指数（SALib） | `examples/interop/optimization/` |
| [声明光阑后的衍射](#声明光阑后的衍射) | 高斯光束状态、功率、光阑和透镜参数、平面坐标系 | 焦面衍射场（LightPipes；prysm、POPPY、Diffractio 交叉校验） | `examples/interop/diffraction/` |
| [量子光子学](#基于网络导出的量子光子学) | 线性光学网络：模式、含损耗传输矩阵、路径延迟、约定 | 光子统计（Perceval；Piquasso 交叉校验） | `examples/interop/quantum/` |
| [超表面透镜虚拟元件](#超表面透镜作为虚拟聚焦元件) | 在已验证有效域内使用表格化薄聚焦元件的运行 | 单元响应（grcwa）和聚焦（角谱法） | `examples/interop/metasurface/` |

外部结果单独保存为文件，放在所用 OpenSci 运行旁边，并带有该运行的身份信息。它们不会改变运行本身、运行的 hash 或 Local Validation；由外部结果生成的虚拟元件是仿真模型，不是可订购的产品。

给自己 Agent 的提示词：[English](../en/interop-prompts.md) · [简体中文](interop-prompts.md)。

## 准备工作

外部工具环境需要 Python 3.12 或更高，因为固定版本的 numpy 2.5.4 和 scipy 1.18.1 要求如此：量子专题为 3.12 至 3.14，单独的 Diffractio 环境为 3.12。示例在 Python 3.12.14 上验证；本仓库的核心示例在 Python 3.11 或更高版本上运行。每个专题使用一个新的虚拟环境，在仓库根目录运行：

```bash
python -m venv .venv-optimization
.venv-optimization/bin/python -m pip install -r examples/interop/optimization/requirements-optimization.txt
```

Windows 上使用 `.venv-optimization\Scripts\python`。依赖文件要求 os-optics 0.10.0 或更高版本，并固定第三方软件包的已验证版本；这些软件包都不是 `os-optics` 本身的依赖。

每个脚本在 stdout 输出一个 JSON 摘要。其中的 `outcome` 是 OpenSci 与外部工具或独立参考的比较结果，不是 OpenSci 运行状态：所有比较都在给定容差内时为 `WITHIN_TOLERANCE`（退出码 0），有比较超出容差时为 `OUT_OF_TOLERANCE`（退出码 1），只生成数据的步骤为 `COMPLETED`（退出码 0），出错时退出码为 4（stderr 上给出 JSON 错误）。OpenSci 自身的状态（`PASS`、`WARNING`、`OUT_OF_MODEL_SCOPE` 等）单独列在 `engine_status` 中。

## 优化与容差

`optimize_expander.py` 通过移动透镜 `L2` 使无目录开普勒扩束器（两个声明的理想薄透镜，f = 50 mm 和 150 mm，532 nm，束腰 0.5 mm）输出准直：

```bash
python examples/interop/optimization/optimize_expander.py --out run-interop-optimization
```

每次评估都复制 Project，按 `instance_id` 设置 `L2` 的位置，在内存中运行引擎，并从运行结果中读取目标指标 `divergence_half_angle_x_rad`。SciPy（`minimize_scalar`，有界）和 Optuna（TPE，种子 1，60 次试验）搜索透镜间距；随后 SALib 针对两片透镜 ±0.5 mm 的均匀装调误差和光源束腰 ±5 % 的误差，估计发散角的一阶和总 Sobol 指数。独立的 ABCD 计算（只用 numpy）给出参考最优解，以及每个被评估设计的参考值。

`--out` 目录中的输出：`evaluations.jsonl`（每次引擎运行一行，包含输入、指标值、状态以及 `project_hash`、`input_hash`、`result_hash`、`engine_build_id`）、`project-baseline.json` 和 `report.json`。在已发布的 0.10.0 wheel 上，SciPy 用 10 次评估达到参考最优间距（200.0573 mm），误差 9e-7 mm；每个引擎结果与 ABCD 参考的相对差为 2e-14；引擎与参考的 Sobol 指数相差 2e-14。优化器决定取值，OpenSci 计算每个设计。更新后的工作流程将目标函数有效性与要求可行性分开：
所选发散角必须是可用的精确点值，且 `collimated` 要求的判定为 `MET`，才能成为可行最优解。
无关的模型范围问题不会使该目标函数失效。每次评估都保留在 `evaluations.jsonl`，包括无效、
部分完成、未满足和无法判定的情况。没有可行最优解时报告 `NO_FEASIBLE_RESULT`（退出码 2）。

用 `--max-divergence-rad` 设置客户要求，`--max-evaluations` 限制总评估次数，
`--max-trace-steps` / `--max-interactions-per-path` 限制每个设计的引擎工作量。
例如，在相同搜索条件下收紧发散角要求：

```bash
python examples/interop/optimization/optimize_expander.py --out run-optimization-strict --max-divergence-rad 1e-9
```

SALib 分析使用所有有效目标函数值，包括未满足要求的样本；采样集不完整时会拒绝分析，
不会丢弃评估后报告改变了含义的 Sobol 指数。保留采样日志，以便解释分母与缺失结果。

## 声明光阑后的衍射

532 nm TEM00 光束（束腰半径 1 mm，1 mW）通过声明的圆形 `APERTURE_STOP` 和 `IDEAL_THIN_LENS`（f = 200 mm），问题是后焦面的辐照度。两侧可在两个环境中分别运行：

```bash
python examples/interop/diffraction/opensci_handoff.py --out run-interop-diffraction
python examples/interop/diffraction/external_focus.py run-interop-diffraction
```

`opensci_handoff.py` 对光阑半径 3、1.2 和 0.5 mm 分别运行 Project，并为每种情况写出 `handoff.json`：引擎在光阑所在平面的观察面上给出的横截面交接数据（`RunResults.handoff`，也可用 `os-optics results handoff RUN --monitor ID --element ID`），包括各轴高斯 q、半径、波前曲率半径、M²、功率、波长、介质、光束坐标轴、Jones 矢量、该平面坐标系中的光阑轮廓，以及场构造约定和适用条件；另外还有来自 Project 的透镜参数、引擎自己的焦面结果和运行身份。脚本会用上游 1 mm 处 pupil 面的状态经 ABCD 传播来核对交接数据。`external_focus.py` 只读取这些文件。OpenSci 不导出场：仅凭光强或功率不能确定复场，运行中稀疏的瞳面光线也不等于采样的瞳面场。因此脚本按明确约定由高斯状态构造采样场（`tem00_field.py`）：

* 场 `E ~ exp[i(k·r − ωt)]`；在光束坐标轴中 `U = sqrt(2P/(π w_x w_y)) exp(−x²/w_x² − y²/w_y²) exp(+ik(x²/2R_x + y²/2R_y))`，发散光束 `R > 0`；省略 Gouy 相位和整体相位；`|U|²` 的积分等于光束功率；
* 只适用于两个方向 M² = 1 的情况（否则脚本拒绝）；仅凭 M² > 1 不能确定模式组成或相干性；
* 光阑为无限薄硬边，边缘像素按面积加权；理想透镜用二次相位表示（引擎不建模理想透镜的相位）。

LightPipes 以绝对单位在六种网格和窗口上传播（收敛研究）；prysm 和 POPPY 复算归一化径向剖面，Diffractio 复算绝对峰值；Hankel 积分（`hankel_reference.py`）作为独立参考。验证运行结果：

| 光阑半径 | 引擎状态 | 引擎焦面峰值相对参考 | LightPipes 最细网格：峰值相对误差（收敛分散） |
| --- | --- | --- | --- |
| 3 mm | `PASS` | +0.024 % | 3e-8（1e-7） |
| 1.2 mm（挡掉 5.6 % 功率） | `WARNING` `GAUSSIAN_TRUNCATION` | **+62 %** | −1.0e-4（1.7e-4） |
| 0.5 mm | `OUT_OF_MODEL_SCOPE` `GAUSSIAN_SEVERE_TRUNCATION` | 不给出高斯结果 | 8.6e-4（1.7e-3） |

所有情况下引擎的透过功率都等于 `1 − exp(−2a²/w²)`。引擎的焦面辐照度是近轴高斯量：光阑足够大时准确；出现 `GAUSSIAN_TRUNCATION` 警告后只有功率可靠，焦面辐照度需要像这里这样做衍射计算。prysm 和 POPPY 与参考剖面相差 4e-7；两者对同一采样场做的是同一种离散傅里叶变换，因此合起来只算一项采样交叉校验，而不是两个独立参考。Diffractio 的绝对峰值相差 3e-5。

Diffractio 1.0.0 要求 numpy < 2，不能与 os-optics 共用环境。在第二个环境中安装 `requirements-diffractio.txt`，再在其中运行同一个 `external_focus.py`：它只读取 handoff 文件。主环境中的脚本写出 `focus_profiles.png`（每种情况的引擎高斯结果与衍射参考）。

单独的 Diffractio 环境会写入 `external_report_diffractio.json`、
`external_summary_diffractio.json` 和 `focus_profiles_diffractio.png`，保留主环境的报告和图像。

## 基于网络导出的量子光子学

`build_layouts.py` 用原生元件（薄 50:50 平板分束器、平面反射镜、声明 N-BK7 Sellmeier 玻璃的无镀膜窗口）构建小型无目录 Project，并用公开命令逐个导出：

```bash
python examples/interop/quantum/build_layouts.py --out run-interop-quantum
python examples/interop/quantum/run_quantum.py run-interop-quantum --inputs examples/interop/quantum/quantum_inputs.json
```

情况包括：780 nm Hong-Ou-Mandel 干涉；光源 B 移动 −0.3 … +0.3 mm 的 13 次导出组成的延迟扫描；一臂放入 3 mm 窗口，另一光源分别后移窗口群延迟长度或仅后移相位延迟长度；532 nm 双输入 Mach-Zehnder，窗口厚度 0、1、1.0003 mm；以及光路上有理想薄透镜、被导出拒绝的情况（`PHASE_UNDEFINED_IDEAL_LENS`）。

Project 中的激光器只确定网络的模式、波长和偏振。所有量子参数都来自 `quantum_inputs.json`：每个光源一个光子，处于 V 模式；高斯波包（HOM 情况均方根光强时长 0.25 ps，MZI 为 1000 ps），零延迟不可区分度 0.95；每个输出面一个阈值探测器，效率 0.8，每个门的暗计数概率为 0（无损 HOM 情况和有损 1 mm MZI 情况为 0.01）。`run_quantum.py` 不导入 os-optics：

* 按 `T[输出, 输入]` 读取矩阵（Perceval `Unitary(Matrix(U))` 和 Piquasso `Interferometer(U)` 的读取方式相同），并乘以 `sqrt(η)` 表示探测效率；
* 有损 T 嵌入两倍维度的幺正矩阵（幺正扩张），额外模式求迹消去；
* 每条路径的群延迟取自导出的 `group_delay_ps`，并与由 Project 声明玻璃算出的值核对；两个光子的不可区分度为 `p0 exp(−Δτ²/4σ²)`，Δτ 为两者的相对群延迟；
* 只有当同一光子到同一输出的各路径群延迟差不超过其时长的 5 % 时，才使用单一波长的 T；否则拒绝该情况（`SINGLE_FREQUENCY_MODEL_INVALID`，以 1 mm MZI 中的 0.25 ps 光子演示）。

Perceval（SLOS）用噪声标签模拟部分可区分光子；Piquasso 把同一分布按 p 加权的不可区分与可区分光子混合重新计算。闭式参考（`reference.py`）不使用任何导出数据：2 × 2 分束器代数、SCHOTT N-BK7 Sellmeier 公式、无镀膜表面的菲涅耳损耗以及高斯波包重叠。对有损光路，它用 `L = I − M†M` 给出两个光子的损耗振幅，因此经过同一有损元件的损耗会像物理上那样发生干涉；带暗计数的阈值探测作用于完整的光子数分布（每个探测器上一个、两个或没有光子）。验证运行中，所有情况下 Perceval 与参考相差 4e-8，Piquasso 相差 2e-9；扫描范围内 HOM 凹陷可见度为 0.9491（参考 0.9491；延迟范围无限大时的解析值为 0.95）。若只按相位延迟补偿 3 mm 窗口，会留下 0.163 ps 的群延迟失配，使光子不可区分度从 0.95 降到 0.854；导出的群延迟使这一点可以直接看出。

上游自述的许可证：Perceval（`perceval-quandela`）为 MIT；其必需依赖 `exqalibur` 在软件包元数据中声明 CC BY-NC-ND 4.0；Piquasso 为 Apache-2.0。Piquasso 8.0.1 与 numba 0.68 重新加载自身编译缓存时可能失败；脚本为每次运行指定新的 `NUMBA_CACHE_DIR`。

## 超表面透镜作为虚拟聚焦元件

步骤 4 在 0.10.0 上通过：范围内的数值对照和收敛检查满足容差，四个超出模型范围的反例
（450 nm 波长、5°、8°、20° 视场角）分别返回对应的适用范围代码，其目标判定为
`INDETERMINATE`。具体案例见[验证记录](../interop-validation.md)；下方数值表记录的是在已发布的 0.9.0 wheel 上的完整运行。

一个 TiO2 纳米柱超透镜（532 nm 时 f = 200 µm，口径 100 µm）先用外部工具表征，再转为 OpenSci 的 `TABULATED_THIN_LENS` 模型：

```bash
python examples/interop/metasurface/step1_unit_cells.py --out run-interop-metasurface
python examples/interop/metasurface/step1_unit_cells.py --out run-interop-metasurface --nG 201 --wavelengths-nm 490 515 532 545 580
python examples/interop/metasurface/step2_focusing.py run-interop-metasurface
python examples/interop/metasurface/step2_focusing.py run-interop-metasurface --nG 201
python examples/interop/metasurface/step2_focusing.py run-interop-metasurface --samples 2048 --wavelengths-nm 490 515 532 545 580
python examples/interop/metasurface/step3_fit_model.py run-interop-metasurface
python examples/interop/metasurface/step4_engine_check.py run-interop-metasurface
```

第 1 步的第二条命令和第 2 步的后两条命令计算收敛表：RCWA 级次由 101 增至 201，传播网格由 1024 采样（333 µm）增至 2048 采样（666 µm），覆盖波段内 5 个波长以及视场角和光束尺寸情形。第 3 步从这些表中取外部数值分散；任一类缺失时以 `CONVERGENCE_DATA_MISSING`（退出码 4）停止，因为不含数值分散的误差上限会低估外部参考自身的不确定度。

外部结果分成三组，各自用途不同：

| 组 | 内容 | 用途 |
| --- | --- | --- |
| 表格 | 490–580 nm 的 10 个波长，30 µm 光束，正入射 | 模型的表格行 |
| 标定 | 表格行之间的 5 个波长（495、515、525、545、565 nm）、20 µm 光束、532 nm 下 2、5、8、20° 视场角 | 误差上限和有效域 |
| 测试 | 另外 4 个波长（505、535、555、575 nm）、545 nm 下的 25 µm 光束、555 nm 下 1.5° | 只用于最终检查 |

1. **单元**（grcwa）：半径 40–150 nm 柱体（周期 325 nm，高 600 nm，常数折射率 TiO2 2.43 / SiO2 1.46，正入射，x 偏振）在全部 19 个波长上的零级复透射系数。使用通量归一化振幅 `t·sqrt(T/|t|²)`，因为在折射率不同的介质之间，仅 `|t|²` 并不是功率比例。
2. **聚焦**（numpy）：局部周期近似和标量角谱传播；给出每种情况的焦点位置、二阶矩半径和聚焦效率。斜入射沿用正入射的单元响应，这一近似随角度增大而变差。
3. **模型**：每个表格波长一行（由高斯聚焦公式得到等效薄透镜焦距；透过率 = 聚焦效率 / 通光孔径内的高斯功率），光焦度和透过率随波长线性插值。各支持指标的误差上限 = 标定组上的最大偏差 + 标定情形的外部数值分散（RCWA 级次 101/201、传播网格 1024/2048 采样，来自收敛命令）。为确定视场角有效域，第 3 步在 OpenSci 中以各标定角度运行模型，把引擎焦点与外部焦点比较；不超出误差上限所能达到的最大角度作为 `max_field_angle_deg`。已验证的最大入射光束半径（30 µm）作为 `max_beam_radius_mm`；`LINEAR_X` 为已验证偏振；证据中记录工具版本和单元库的 SHA-256。结果为 `metalens_model.json`。
4. **OpenSci 检查**：在无目录 Project 中声明该模型。引擎必须复现模型自身的薄透镜预测，在测试组上保持在声明误差内，并拒绝各反例。标定组的偏差只作为包络报告：它们决定了误差上限，因此不能用来检验上限。

在已发布的 PyPI 0.9.0 wheel 上的验证运行结果（外部数值分散：焦点位置 0.50 %，光斑半径 0.71 %，效率 0.015）：

| 量 | 标定组（决定上限） | 测试组（检验上限） | 模型声明误差 |
| --- | --- | --- | --- |
| 焦点位置 | ≤ 0.30 µm | ≤ 0.32 µm | `waist_distance_*_mm` ± 0.0014 mm |
| 光斑半径（引擎高斯 vs 外部二阶矩） | −0.6 … −7.3 % | −1.6 … −5.8 % | `waist_radius_*_mm` ± 8.1 % |
| 输出功率 | −5.4 … +15.9 % | −3.6 … +15.7 % | `power_W` ± 19 % |

所有测试情况都在声明误差内。焦点位置在整个波段内都与外部结果吻合；理想高斯焦斑比外部光斑小 2–7 %（单元相位离散所致），这部分由声明的半径误差承担。聚焦效率在 10 nm 内从 0.64 变到 0.95，因此行间线性插值对输出功率的预测只能达到约 16 %；更密的表格行或 `transmittance_interpolation: POINTS_ONLY` 可以得到更紧的功率模型。2° 时引擎焦点在上限内（轴向 −0.23 µm，光斑 −5.2 %）；5° 时外部焦点向透镜移动 1.56 µm 而引擎焦点不变，超过 1.4 µm 的上限，因此 `max_field_angle_deg` 为 2。引擎复现模型薄透镜预测的偏差为 4.4e-16；把全部 19 个波长作为同一光源的多条谱线一次运行，结果相同；未进入焦斑的功率在功率预算中显示为该元件的 `UNMODELLED_RESPONSE` 汇。

反例均为 `OUT_OF_MODEL_SCOPE`，受影响目标的判定为 `INDETERMINATE`：450 nm 光源在 490–580 nm 表格范围之外（`MODEL_WAVELENGTH_OUTSIDE_BAND`）；5°、8° 和 20°（`MODEL_FIELD_ANGLE_EXCEEDED`；20° 时外部焦点比引擎焦点更靠近透镜 15.6 µm、更靠近光轴 5.6 µm，光斑大 68 %，功率少 13 %）；以及经过该元件、要求模型未验证指标（`peak_irradiance_W_per_mm2`）的目标（`METRIC_NOT_VALIDATED_FOR_ELEMENT`，不给出数值）。适用范围代码出现在目标的 `applicability_issue_codes` 中：数值仍可读取，而判定明确表示它不能说明模型范围外的结果有效。`--model per-wavelength-ideal` 用每个波长一个 `IDEAL_THIN_LENS` 做同样比较；该类型无法声明视场角有效域，5°、8° 和 20° 时返回 `WARNING`。30 µm 光束的所有运行都报告 `GAUSSIAN_TRUNCATION`，因为 100 µm 口径截去了其 0.4 % 的功率；引擎自身已计入这部分损耗。20 µm 和 25 µm 光束的情形为 `WARNING`，来自束腰目标的高斯状态。

只要第 1 步写出相同的库字段，其他单元求解器也可接入同一流程：meent（MIT）和 torcwa（LGPL）可用 pip 安装；DFlat（`dflat-opt`）和 metabox 依赖 gdspy，在 Windows 上需要 C 编译器，metabox 还要求 Python 3.8 和 TensorFlow 2.11，应放在独立环境中通过文件交换。本示例只实际运行了 grcwa（其软件包元数据声明 GPLv3）。

一次外部聚焦结果不能定义通用元件。该模型只在其波长范围、不超过 `max_field_angle_deg` 的视场角、不超过 `max_beam_radius_mm` 的入射光束半径（在 20 和 30 µm 标定、25 µm 测试）、x 偏振以及所列指标下有效；目标若要求经过该元件的其他指标，则超出模型范围。模型不包含相位，因此网络导出和波前结果会拒绝经过它的路径。模型精度受外部参考本身限制：局部周期近似和标量传播忽略了单元耦合和矢量效应。
