# 成像运行的光学 MTF

[os-optics-kit](../../README.zh-CN.md) · [English](../en/mtf.md) · 简体中文

`os-optics-kit mtf` 把引擎的成像分析整理成一张 MTF 表、一个 JSON 文档和每条谱线一张图。
数值全部来自引擎（`os-optics results imaging`、`RunResults.imaging`）：本工具只读取、标明状态并绘图，
自己不计算任何 MTF。

它只给出光学 MTF：不含像素孔径、不含采样，也不是斜边 SFR。实测 SFR 用
[`os-optics-kit sfr`](sfr.md)；设计侧 SFR（光学 MTF × 像素孔径）见 JunLin615/os-optics#42。
这里没有 PASS/FAIL，也没有目标；设计循环中的 MTF 下限是 `optimize` 和
[评估适配器](evaluation-adapter.md) 的约束。

## 运行

从已保存的运行出发（在进程内运行一次成像分析）：

```bash
os-optics project run project.json --out-dir run
os-optics-kit mtf run --monitor image_plane --request imaging-request.json --out mtf
```

分析结果保存为 `mtf/imaging.json`。从已保存的分析出发（完全不运行引擎分析；`--run` 按
`engine_input_hash` 和 `run_result_hash` 检查该文档是否来自这次运行）：

```bash
os-optics results imaging run --monitor image_plane --request imaging-request.json > imaging.json
os-optics-kit mtf --imaging imaging.json --run run --out mtf
```

带配置（configurations）的 Project 的运行，每个配置各有一个运行目录：用 `--configuration ID`
指定（不指定时命令列出所有配置 id，代码 `CONFIGURATION_REQUIRED`）。`--no-figures` 只写表和文档，
不需要 matplotlib。`--theme presentation|report|FILE` 和 `--set KEY=VALUE`（例如
`--set language=zh-CN`）按[演示](presentation.md)中的方式设置图的样式。输出目录必须是新的或空的；
`--overwrite` 会替换其中本命令自己写的文件。

请求就是引擎的成像请求（`os-optics schema imaging-request`）。有两项设置决定表格是否符合预期：

- `frequencies_cyc_per_mm`：不设置时，每条谱线有自己的频率轴（从 0 到其截止频率 2 NA/λ 的
  33 个点），各谱线的行不对齐。
- `fields`：不设置时，引擎只分析在观测面孔径内到达的光源。被观测面孔径截掉的谱线，本工具都列为
  `OUTSIDE_ACTIVE_AREA`（RUN 模式，或 `--imaging` 加 `--run`）；要分析它，在 `fields` 中写明该视场，
  或加大观测面孔径。

快速镜头需要较密的 `launch_grid`：太稀时引擎拒绝给出衍射 MTF（`PUPIL_UNDERSAMPLED`），表中会写明。

## 读取结果

`mtf.csv` 中每条谱线 × 方向 × 该谱线频率轴上的每个频率占一行：

| 列 | 含义 |
| --- | --- |
| `field` | 谱线，`<source>#<spectral index>`（`SRC#0`） |
| `source_id`、`wavelength_nm` | 光源和该谱线的真空波长 |
| `direction` | `tangential`（子午）或 `sagittal`（弧矢） |
| `frequency_cyc_per_mm` | 引擎的频率，cycles/mm |
| `mtf`、`diffraction_limited_mtf`、`geometric_mtf` | 引擎的数值，原样写出 |
| `*_status` | 该值存在或缺失的原因（见下表） |
| `unit` | `1`：MTF 是调制度之比 |

| 状态 | 有值 | 含义 |
| --- | --- | --- |
| `KNOWN` | 是 | 引擎的数值 |
| `NOT_CONVERGED` | 是 | 收敛检验（发射网格加倍）差异超过 0.03 |
| `BEYOND_NYQUIST` | 空 | 高于 PSF 图的奈奎斯特频率 |
| `OUT_OF_MODEL_SCOPE`、`UNKNOWN`、`NOT_COMPUTED` | 空 | 引擎拒绝给出该曲线；原因见 `mtf.json` |
| `ABSENT` | 空 | 分析中没有这条曲线 |

被拒绝的曲线一律不写出，即使引擎的 JSON 为它保留了数组（例如超出适用范围的几何 MTF，或替代光瞳下的曲线）。
`os_optics_kit.mtf.read_csv(path)` 读回的记录与写出的完全相同。

`mtf.json`（schema `os-optics-kit.mtf`，版本 1）包含：

- `input`：模式（`run` 或 `imaging`）、运行、观测面、配置、分析文件及其 sha256、请求文件；
- `identity`：运行的身份（`RunResults.identity()`：各哈希、引擎构建、目录）和分析的身份
  （`identity.physical`、请求、分析版本）；
- `conventions`：本页的约定，以及引擎自己对像面和方向的说明；
- `lines`：每条谱线的波长、像点（`reference` 为 `CENTROID` 或 `CHIEF`、`point_mm`、`image_axis`、
  `tangential_axis`）、PSF 窗口的光能占比和奈奎斯特频率，以及每条曲线的状态、原因、模型依据、
  收敛情况和数值类型（只有可用曲线带数值）；
- `not_analysed`：不是光束光源的视场（`FIELD_NOT_A_BEAM_SOURCE`）、没有到达的谱线（`NO_ARRIVAL`）、
  请求了却没有得到谱线的视场或波长（`NO_LINE`、`WAVELENGTH_NOT_ANALYSED`），以及被截掉的谱线
  （`OUTSIDE_ACTIVE_AREA`）；
- `counts` 和 `files`。

每张图 `mtf-<source>-<index>.png` 画出子午和弧矢 MTF（实线）及其衍射极限（虚线）。没有数值的曲线不画，
并在图中注明；未收敛的曲线会加标注。

当每条谱线的子午和弧矢 MTF 都是 `KNOWN` 且没有遗漏时，状态为 `COMPLETED`（退出码 0），否则为
`INCOMPLETE`（退出码 2，文件照常写出）。拒绝时退出码为 4，stderr 上给出 JSON 错误：
`BAD_ARGUMENT`、`OUTPUT_EXISTS`、`IMAGING_INVALID`、`MONITOR_NOT_FOUND`、`CONFIGURATION_REQUIRED`、
`IMAGING_RUN_MISMATCH`、`RUN_UNREADABLE`、`ANALYSIS_REFUSED`（附引擎的 `engine_code`）、
`PLOT_EXTRA_MISSING`。

## 约定

- 每条谱线一条曲线。引擎没有多色或加权 MTF，把这些幅值加权求和也得不到它（会丢掉 OTF 相位和
  各谱线各自的中心位置）。
- MTF(0) 是 PSF 窗口内的光能占比，不是 1；曲线没有重新归一化。
- PSF 和 MTF 位于过参考点、垂直于该谱线像方轴的平面上，而不是倾斜的探测器平面。子午方向是像面法线
  在该平面上的投影；在轴上即像面的 u 轴。

## 验证

以 os-optics 0.14.0 记录。sonnar-telephoto-lens 案例（85 mm，f/2.4，0°、7°、14° 视场，587.56 nm，
使用案例自己的请求）：

| 谱线 | 方向 | MTF 20 | MTF 40 | MTF 100 | 40 处衍射极限 | 几何 MTF |
| --- | --- | --- | --- | --- | --- | --- |
| SRC0#0（0°） | T | 0.9015 | 0.7039 | 0.0995 | 0.9267 | `OUT_OF_MODEL_SCOPE` |
| SRC1#0（7°） | T | 0.6699 | 0.3491 | 0.1069 | 0.9266 | 40 处 0.3188 |
| SRC1#0（7°） | S | 0.3762 | 0.0597 | 0.0240 | 0.9260 | 40 处 0.0624 |
| SRC2#0（14°） | T | 0.7158 | 0.5533 | 0.3352 | 0.8680 | `OUT_OF_MODEL_SCOPE` |

表中数值与引擎的数组逐一相等（在保存的分析 `tests/data/mtf-sonnar-imaging.json` 上测试）。在 achromatic-imaging 案例上（使用案例的请求，轴上三条谱线），本命令的 24 个 MTF 值与案例记录的
`data/snapshot.json` 完全相同，例如 40 cycles/mm 处 486 / 588 / 656 nm 的 0.6891 / 0.6323 / 0.5889。
sonnar-telephoto-lens、aspheric-lens 和 midwave-infrared-imager 三个案例的绘图脚本现在在进程内运行分析，
并通过同一个读取模块读取，重新绘制的图与记录的图逐字节相同。一个引擎测试在 achromatic-imaging 的
Project 上运行一次本命令，检查只运行了一次分析、表格与直接调用 `RunResults.imaging` 的结果相同、
从保存的 `imaging.json` 重新运行得到同样的表，以及被篡改的文档会被拒绝。

## 在 Python 中复用

```python
from os_optics_kit import imaging_results, mtf

document = mtf.imaging_of("run", "image_plane", {"frequencies_cyc_per_mm": [10, 20, 40]})
for row in mtf.rows(document):          # mtf.csv 的记录
    print(row["field"], row["direction"], row["frequency_cyc_per_mm"], row["mtf"], row["mtf_status"])
curve = imaging_results.curve(document, "SRC#0", "mtf", "tangential")
imaging_results.value_at(curve, 20.0)   # (数值, 状态)：必须是该谱线自身频率轴上的频率
```

`imaging_of(run, monitor, request, saved=FILE)` 读取已保存的分析并与运行核对，而不是重新运行；
`mtf.analyse(out_dir, ...)` 就是整个命令。

相关：JunLin615/os-optics-kit#31、JunLin615/os-optics#42。
