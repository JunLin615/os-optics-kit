# 由 Project 计算视场点

[os-optics-kit](../../README.zh-CN.md) · [English](../en/field-points.md) · 简体中文

`os-optics-kit field-points` 在固定像面上评估原生 Project 的指定视场点：视场角、物高或实际像高。
对每个视场点，它报告主光线在像面上的落点、像高和角度，给出一阶比较；按需还会用 Engine 的成像分析
给出每条谱线的子午和弧矢光学 MTF，以及按视场采样的最小值。输入 Project 不会被修改。

## 运行

```bash
os-optics-kit field-points project.json --plane image_plane --out fp \
    --angles-deg 0,5,10 --mtf-frequencies 20,40 --launch-grid 128
```

- **视场类型**：每次请求只能用一种：`--angles-deg`（ANGULAR，相对参考轴的角度，单位度）、
  `--object-heights-mm`（OBJECT_HEIGHT，仅限有限远物）或 `--image-heights-mm`（IMAGE_HEIGHT，
  像面上的实际主光线高度，通过求解得到）。同时给出两种会以 `FIELD_KIND_MIXED` 拒绝。
  `--expect-field-kind KIND` 会拒绝其他类型的请求（`FIELD_KIND_MISMATCH`）：传入 Project 文档
  所述的视场类型，角度就不会被当成高度。
- **方向**：`--azimuth-deg`（默认 0）是视场在像面 (u, v) 轴中的方向，从 +u 量向 +v。ANGULAR
  光束朝该方向转动，正光焦度镜头把它成像在同一侧；OBJECT_HEIGHT 的物点沿该方向移动，其倒立实像位于
  相反一侧。
- **参考光源**：`--source SRC` 指定位于光轴上的光源（默认取 Project 中唯一的光源）。它的发射轴即
  本次请求的光轴；视场点的谱线沿用它的谱线，除非 `--wavelengths-nm` 另行指定（此时按其功率等分，
  或沿用它自身的各谱线功率）。
- **配置**：声明了配置的 Project 需要 `--configuration ID`（`CONFIGURATION_REQUIRED` 会列出可选
  的配置）；所选配置用 Engine 的配置展开 Project 处理。
- **MTF**：`--mtf-frequencies F,..`（周/毫米）在评估用 Project 上运行一次成像分析（每次调用最多
  32 个视场点）。`--launch-grid`、`--no-convergence` 和 `--imaging-request FILE`（`os-optics schema
  imaging-request` 中的其他字段）设定光瞳采样；视场、谱线、频率和主光线的穿越序号由本命令自行设定。
  成像分析是最耗时的一步：对 30 个面的 Yutong P2b 镜头，默认发射网格（64，细化到 128）下一条非偏振谱线
  在本机约需 13 分钟，而十一个视场点的几何计算只需 2 分钟。
- **文件**：`--out DIR` 须为新目录或空目录（`--overwrite` 会替换本命令写出的文件）。`--write-run`
  另写出评估运行 `DIR/run`，供 `os-optics-kit field`、`fans` 和 `mtf` 读取；这是一次完整运行，
  大型镜头需要数分钟。

## 方法

视场点是参考光源的一个副本：绕设计坐标系中横跨光束的轴（`参考轴 x 视场方向`）**转动**，并放置到
使其主光线（Engine 追迹的光束中心光线）穿过孔径光阑中心的位置。所有视场点都是同一个评估用 Project
中的附加光源组件；该 Project 由输入（展开后）去掉其他光源和目标得到，一次评估和一次成像调用即可
覆盖全部视场点。视场点不是配置：在镜头 Project 中，配置表示设计状态。

- **物方共轭**由参考光源的束腰决定：束腰距光源原点在一个瑞利长度以内时，光线近乎平行出射，物在
  无穷远；束腰在上游至少十个瑞利长度处时，物是位于束腰的点物。介于两者之间会被拒绝
  （`CONJUGATE_UNDETERMINED`）。具体数值见 `conditions.conjugate`。物在无穷远时给出物高会被拒绝
  （`OBJECT_AT_INFINITY`）。
- **光线瞄准**：三条探测主光线给出近轴入瞳估计；每个视场点先让主光线通过该估计点，再修正其横向偏移
  （ANGULAR）或瞄准点（OBJECT_HEIGHT），直到主光线在光阑面上距光阑中心不超过 `--aim-tolerance-mm`
  （默认 0.001 mm）。穿越位置取自 Engine 的主光线线段，因此评估用 Project 中不会加入任何辅助元件。
  瞄准阶段的评估只追迹每个视场点的主光线谱线，并采用线偏振：主光线的路径与偏振和其他谱线无关。没有
  孔径光阑时，可用 `--pivot-mm X,Y,Z` 让视场点绕该点转动（`aim: NOT_AIMED`）；两者都没有时命令拒绝
  （`NO_APERTURE_STOP`）。
- **像高**在像面上从轴上主光线的落点量起。IMAGE_HEIGHT 请求从一阶估计出发，求解视场角（物在无穷远）
  或物高（有限远物），直到像高误差不超过 `--height-tolerance-mm`（默认 1e-4 mm）。轴上视场点总会被
  追迹（请求中的零值，或 `FP_AXIS`）。
- **像面孔径**：瞄准时临时放大像面的有效区域，以读到每个落点；评估用 Project 保留声明的孔径，只有
  当某个视场点落在其外时才放大（位姿不变，`plane.widened`）。`within_declared_aperture` 逐点说明
  声明的像面是否容纳该点。
- **追迹预算**：输入的计算预算是按它自己的光源设定的。当视场点的主光线路径需要更多时，评估用 Project
  把 `max_paths` 和 `max_trace_steps` 提高到所需的两倍（`derivation.compute_budget`），使 Engine 在追迹
  这些视场点的反射光之前先追迹全部视场点。`evaluated.execution` 是 Engine 的执行记录；若某条谱线仍因
  预算耗尽而未被追迹，会标为 `TRACE_BUDGET_EXHAUSTED`。
- **一阶比较**（`FIRST_ORDER_COMPARISON`，不是畸变数值）：物在无穷远时为 `f tan(theta)`，其中 EFL
  取自 Engine 对参考轴上元件在主光线波长处的近轴分析，并给出近轴焦点到像面的距离；有限远物时为
  `|m| h`，`m` 是从物面到像面的近轴 ABCD 矩阵的 A 元素。近轴分析被拒绝时，参考值为 null 并附原因。
- **MTF**：按视场点、谱线、方向和频率，经 kit 的成像读取器读出，从不插值：`value`、`status`、
  `value_kind`、`reason`、`converged`、`model_basis`。只有 `EXACT` 值（状态 `KNOWN`、已收敛、在模型
  范围内）才算得到确认。

## 读取结果

`field-points.json`（模式 `os-optics-kit.field-points` v1）：

- `input`：`path`、按读入字节计算的 `sha256`、`sha256_lf`（CRLF 换成 LF 之后）、Engine 的
  `project_hash`、`engine_input_hash`、`catalog_version` 以及 `configuration_id`。
- `evaluated`：`evaluated-project.json` 及其哈希、Engine 的 `execution` 执行记录和 `derivation`
  （去掉的光源和目标、加入的光源及其位姿、像面声明的孔径与评估时的孔径、计算预算）；`engine` 给出版本
  和构建号。
- `conditions`：视场类型和单位、方位角和视场方向、`requested`（按请求顺序）、共轭、参考光源的事实
  （其光轴、像面法线、其主光线的落点和光阑偏移）、主光线波长、各谱线及其功率、频率、成像请求、瞄准
  （`pupil_magnification`、`entrance_pupil_from_stop_mm`）和一阶参考。
- `points[]`：`status`（`EVALUATED`，或 `NOT_EVALUATED` 并附 `reason`：`NO_ARRIVAL`、
  `OUTSIDE_PLANE`、`STOP_NOT_REACHED`、`CHIEF_RAY_PATH_DIFFERS`、`AIM_NOT_CONVERGED`、
  `HEIGHT_NOT_REACHED`、`TRACE_BUDGET_EXHAUSTED`）、`field`（角度、物高）、`pose`、`chief_ray`（`image_point_uv_mm`、
  `image_height_mm`、`image_azimuth_deg`、相对像面法线的 `angle_deg`、`stop_offset_mm`、`aim`、
  `iterations`、`within_declared_aperture`）、`first_order`、`spot`（发射和到达的光瞳光线数）、
  `lines`（逐谱线的同类数据）以及 `mtf[]`。
- `minima[]` 按频率给出：`per_line`（每个波长）和 `all_lines`，附 `field_grid`、`directions`、
  `value`、`status` 和 `limiting_sample`。只有每个采样都是 `EXACT` 时才是 `CONFIRMED`；否则为
  `UNCONFIRMED`，`value` 是精确采样中的最小值并标 `bound: UPPER`，`missing[]` 列出其余每个采样及
  原因。`metric: per_line_minimum`：逐谱线的最差值不是按光谱加权的（多光谱）MTF。
- `imaging[]`：每个成像文件、其身份信息和请求。`scope` 说明支持的范围。

退出码：0 `COMPLETED`（所有视场点都已评估，所有 MTF 采样都是精确值），2 `INCOMPLETE`（仍会写出全部
文件），4 拒绝，并在 stderr 输出 JSON 错误。

`CHIEF_RAY_PATH_DIFFERS` 的视场点与轴上主光线经过的表面不同（漏过某个元件，或发生反射）：它在系统
视场之外。

## 手工构造视场点的四个陷阱

技能页面 `skills/field-points/traps.md` 逐条给出症状；Engine 侧的问题见
[os-optics#87](https://github.com/JunLin615/os-optics/issues/87)。

1. **平移而非转动**：运行正常完成，像点仍在轴附近而入射角变化，或出现模型范围提示；没有任何信息说明
   视场并未生效。
2. **绕光束自身的轴转动**：四元数变了，结果却与 0° 视场相同。
3. **像面孔径裁掉光束**：该视场点没有到达记录（它出现在 `outside_active_area` 中），默认的成像请求会
   悄悄漏掉它。
4. **`readings` 使 `quantity_state` 停在 `NOT_COMPUTED`**：到达记录都在，只是像面的功率界
   （`plane_bounds`）没有计算。

## 镜头库记录

为 os-optics-lens 记录建立事实时，只绑定到提交的 Project：

| Lens | 报告 |
|---|---|
| `native_project.sha256` | `input.sha256_lf` |
| `configuration_id` | `input.configuration_id` |
| 像面（固定） | `plane.observation_id`；位姿从不改变 |
| `field.kind` | `conditions.field_kind`（作为 `--expect-field-kind` 传入） |
| `field_basis` | 由请求决定，例如沿 `--azimuth-deg` 的像圆半径的像高比例 |
| `sampled_field_fractions` | `conditions.requested` 除以该半径，顺序不变 |
| `frequency_lp_mm` | `minima[].frequency_cyc_per_mm`（数值相同） |
| 方向 | `minima[].directions` |
| `limiting_sample` | `minima[].all_lines.limiting_sample` |
| 光谱 | `conditions.wavelengths_nm` 和 `line_powers_w` |

最小值按谱线计算（`per_line_minimum`）：以光谱加权 MTF 为指标的记录有不同的条件。逐点数值由 Engine
计算，采样视场上的最小值由这些数值导出。`evaluated.derivation` 列出相对提交 Project 的全部改动。

## 验证

已用 os-optics 0.14.0 验证：

- **aspheric-lens 案例**（光阑位于五片元件之前，587.5618 nm 处 EFL 49.9967 mm）：12.5° 主光线落在
  距光轴 11.0997738 mm 处，与案例自身对准光阑的 12.5° 光源的像高相差不超过 1e-6 mm；f tan(theta)
  为 11.0840111 mm（+0.14 %）。80° 的请求漏过各元件（`CHIEF_RAY_PATH_DIFFERS`），最小值保持
  `UNCONFIRMED`。子午和弧矢 MTF 与相同请求的直接成像调用结果一致（`tests/test_field_points_engine.py`）。
- **lenslibrary-528155**（Dagor，f/8，EFL 239.8934 mm）：瞄准后的 5°、17.7° 和 26.6° 主光线分别落在
  20.98841、76.58583 和 120.25078 mm，与 f tan(theta) 相差不超过 0.11 %，像面从声明的 19.6045 mm 半径
  放大。同一 5° 光源若绕自身原点转动而不瞄准（`--pivot-mm -100,0,0`），主光线在距光阑中心 8.91 mm
  处穿过光阑，落在 20.8662 mm（-0.58 %）：未瞄准的主光线会被误读为畸变。
- **Yutong P2b**（`--source F0W1`，在 587 nm 下沿传感器对角线取 21.635 mm 的 0 到 0.8 共十一个实际
  像高）：求解得到的视场角与记录中对准光阑的光源一致，相差不超过 2e-4°（0.1 处 6.58848°，0.8 处
  42.78214°），0.8 处主光线角为 14.4153°。

## 在 Python 中复用

```python
from os_optics_kit import field_points

request = field_points.Request(project="project.json", plane="image_plane", kind="ANGULAR",
                               values=[0.0, 5.0, 10.0], mtf_frequencies=[20.0, 40.0])
report = field_points.run(request, "fp")
report["minima"][0]["all_lines"]
```

`field_points.minima(samples, frequencies, wavelengths, field_grid)` 以及
`os_optics_kit.field_geometry` 中的几何函数（转动位姿、共轭规则、瞄准）都可以单独使用。

相关：[os-optics#87](https://github.com/JunLin615/os-optics/issues/87)、`os-optics-kit field`、
`os-optics-kit mtf`。
