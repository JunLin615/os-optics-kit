# 测量斜边 SFR

[os-optics-kit](../../README.zh-CN.md) · [English](../en/sfr.md) · 简体中文

`os-optics-kit sfr` 按 ISO 12233 e-SFR 方法，从一张斜边图像测量相机或成像模组的空间频率响应
（SFR）。此流程无需运行光学仿真。加上 `--design` 时，它把 `os-optics results imaging` 保存的设计
MTF 画在同一坐标轴上，并说明两者的差异。

## 运行

```bash
python -m pip install "os-optics-kit[plot]"
os-optics-kit sfr edge.png --roi 400,300,80,120 --pixel-pitch-um 3.45 --out sfr-run
```

- **输入**：PNG 或 TIFF（8 位或 16 位灰度、RGB、RGBA）、`.npy` 数组，或无表头、逗号分隔的
  `.csv` 数组。RGB 默认按线性化后的通道合成亮度 `Y = 0.2126 R + 0.7152 G + 0.0722 B`
  （ITU-R BT.709），也可用 `--channel R|G|B` 选一个通道。
- **ROI**：`--roi X,Y,W,H`，单位为像素，原点在左上角，X 沿列方向。ROI 内只含一条直边，
  相对竖直或水平方向倾斜约 5°，两侧各留至少 8 像素平坦区域。方向由工具自动判断。
- **线性化**：默认按原值使用（`--oecf linear`，适用于原始或线性数据）。`--oecf gamma --gamma 2.2`
  计算 `(v / full_scale) ** 2.2`，`--oecf srgb` 使用 sRGB 反变换，`--oecf table --oecf-table oecf.csv`
  使用实测 OECF（每行 `code,linear`，分段线性）。8 位和 16 位数据的满量程为 255 和 65535；
  浮点数据用 `--full-scale` 指定。
- **单位**：频率单位为沿边缘法线方向的周期/像素。`--pixel-pitch-um`（或 `--pixel-pitch-mm`，
  方形像素）增加周期/毫米（lp/mm）。
- `--at 0.125,0.25` 和 `--at-mm 50` 报告指定频率处的 SFR。

标准输出是一个紧凑 JSON 对象。已存在且非空的 `--out` 目录会被拒绝（`OUTPUT_EXISTS`），除非
加 `--overwrite`。`--no-plot` 不写 PNG，也不需要 matplotlib；此时 `.npy` 和 `.csv` 输入既不需要
matplotlib 也不需要 Pillow。

## 方法

| 步骤 | 实现 |
| --- | --- |
| 方向 | 比较 ROI 的平均水平梯度和平均竖直梯度 |
| 逐行边缘位置 | 中心差分导数乘 Hamming 窗后的质心；先以 ROI 中心为窗心，再两次以拟合边缘为窗心 |
| 边缘拟合 | 位置对行号的最小二乘直线（`--fit-order 2`：二次曲线）；给出角度和残差 RMS |
| ESF | 每个像素投影到其沿边缘法线的距离；每像素 4 个分箱，覆盖所有行共有的范围；空箱计数并按线性插值填充 |
| LSF | ESF 的中心差分 |
| 窗口 | 以 LSF 峰值为中心的 Hamming 窗（较远一端为 0.08） |
| SFR | FFT 的模，在零频处归一化为 1，再除以导数的传递函数 `sinc(2 f / 4)` |

SFR 报告为非负的模：边缘测量丢失相位。`sfr50` 是 SFR 首次低于 0.5 的频率（线性插值）；
`sfr_at_nyquist` 是 0.5 周期/像素处的 SFR。曲线写到 1 周期/像素。每个 1/4 像素分箱内的平均
效应与 ISO 12233 一样保留在估计中（在 0.5 周期/像素处为 0.974 倍）。

## 读取结果

| 文件 | 内容 |
| --- | --- |
| `sfr.csv` | 频率（周期/像素；给出像元尺寸时另有周期/毫米）、SFR、导数校正 |
| `esf.csv` | 分箱距离、ESF、每箱样本数、插值填充的箱标为 `filled` |
| `lsf.csv` | LSF、窗口和加窗后的 LSF |
| `edge.csv` | 每行的质心、拟合位置、残差以及该行是否参与拟合 |
| `sfr.png` | 带拟合边缘的 ROI、ESF、LSF 与窗口、带 Nyquist 和 SFR50 的 SFR |
| `summary.json` | 标准输出摘要及来源记录：输入路径与 SHA-256、ROI、设置、方法、软件版本 |

`edge.angle_deg` 是相对图像列方向（竖直边）或行方向（水平边）的角度。状态与问题：

| 状态 / 退出码 | 含义 |
| --- | --- |
| `PASS` / 0 | 无诊断问题 |
| `WARNING` / 0 | 已测得 SFR；见 `issues` |
| `UNKNOWN` / 2 | 超过 25 % 的 ESF 分箱为空：边缘角度和行数提供的亚像素相位不足 |
| 错误 / 4 | 输入被拒绝，标准错误输出 JSON 错误 |

| 问题 | 触发条件 |
| --- | --- |
| `EDGE_ANGLE_SHALLOW`、`EDGE_ANGLE_STEEP` | 角度小于 2° 或大于 30° |
| `INCOMPLETE_PHASE_COVERAGE`、`EMPTY_BINS` | 边缘在所有行上移动不足 1 像素；存在无样本的分箱 |
| `CLIPPED_PIXELS` | ROI 中有像素为 0 或满量程 |
| `FEW_ROWS`、`ROWS_WITHOUT_EDGE` | 少于 20 行；某些行没有明显台阶 |
| `LARGE_FIT_RESIDUAL` | 边缘拟合残差 RMS 大于 0.25 像素（噪声、弯曲边缘或第二条边） |
| `LOW_EDGE_CONTRAST`、`SHORT_ESF_SPAN` | Michelson 对比度低于 0.2；ESF 范围不足 ±8 像素 |
| `SFR50_NOT_REACHED` | 到 1 周期/像素为止 SFR 一直不低于 0.5 |

拒绝代码包括 `INPUT_NOT_FOUND`、`UNSUPPORTED_INPUT`、`UNSUPPORTED_IMAGE_MODE`、`ROI_INVALID`、
`ROI_TOO_SMALL`、`EDGE_NOT_FOUND`、`EDGE_OUTSIDE_ROI`、`NONFINITE_VALUES`、`OECF_RANGE` 和
`IMAGE_READER_MISSING`（读取 PNG/TIFF 但未安装 Pillow）。

## 与设计比较

```bash
os-optics results imaging run --monitor image_plane --request imaging-request.json > imaging.json
os-optics-kit sfr edge.png --roi 400,300,80,120 --pixel-pitch-um 3.45 --design imaging.json --design-line SRC0#0 --out sfr-cmp
```

`--design` 读取 `os-optics results imaging` 的 JSON（UTF-8，或 Windows 重定向产生的 UTF-16），
或带 `--design-unit cy/mm|cy/px` 的 `frequency,value` CSV。对于引擎 JSON，当其中有多个视场和
波长时，用 `--design-line SOURCE#INDEX` 选择；`--design-direction`（`both`、`tangential`、
`sagittal`）和 `--design-curve`（`mtf`、`diffraction_limited_mtf`、`geometric_mtf`）选择曲线。
周期/毫米的设计曲线需要像元尺寸（否则 `PIXEL_PITCH_REQUIRED`）：`f[cy/px] = f[cy/mm] × pitch[mm]`。

只采用引擎状态为 `KNOWN`（或 `KNOWN_ZERO`）的曲线：被引擎拒绝的曲线（例如超出适用范围的几何 MTF，或替代光瞳下的曲线）
即使 JSON 中保留了数组，也会以 `DESIGN_CURVE_UNAVAILABLE` 拒绝，并给出引擎的状态和原因。
`os-optics-kit mtf` 单独输出设计的光学 MTF：一张表、一个 JSON 文档和每条谱线一张图（[MTF](mtf.md)）；
它保存的 `imaging.json` 可直接作为 `--design`。

两条曲线是不同的量：

| 方面 | 测得的 SFR | 设计曲线 |
| --- | --- | --- |
| 物理量 | 光学 × 像素孔径 × 采样 × 图像中已有的任何处理 | 仅光学 MTF |
| 像素孔径、采样、图像处理 | 包含 | 不包含 |
| 符号 | 模 | 保留有符号值；按 \|MTF\| 比较，并列出为负的频率 |
| 方向 | 沿图像 x 或 y 的边缘法线 | 设计视场的子午或弧矢方向 |

因此测得的 SFR 通常低于光学 MTF。工具不会给设计曲线乘上像素孔径或采样项。`comparison.csv`
包含每条设计曲线频率范围内的测量频率、线性插值（不外推）的设计值、`|design|` 以及差值
measured − |design|。`comparison.json` 说明输入差异、设计来源（line、波长、引擎输入哈希），并按
曲线给出 Nyquist 处的值、0.5 交点、最大差和 RMS 差，以及每个设计原始频率处的测量 SFR。
`comparison.png` 同时画出两者。

## 验证

测试渲染答案已知的斜边：阶跃经标准差 σ 像素的高斯 PSF 模糊后在方形像素上积分（100 % 填充
因子）；垂直边缘方向精确积分，沿边缘方向取 32 个子行。沿边缘法线的真实 SFR 为
`exp(-2π²σ²f²) |sinc(f cos θ) sinc(f sin θ)|`，θ = 0 时即 `exp(-2π²σ²f²) |sinc f|`。在 0–0.5
周期/像素范围内，对 3°、5°、8°、−6° 的竖直和水平边，σ = 0.3、0.5、1.0 像素：

| 检查 | 声明容差 | 典型值 |
| --- | --- | --- |
| SFR，无噪声 | ±0.025 | ≤ 0.019（σ = 0.3 像素），≤ 0.01（σ ≥ 0.5 像素） |
| 边缘角度，无噪声 | 0.01° | < 10⁻⁵° |
| SFR50，无噪声 | 0.012 周期/像素 | ≤ 0.01 |
| 边缘角度，台阶 0.6、噪声标准差 0.02 | 0.1° | 0.03° |
| SFR，噪声标准差 0.005 / 0.01 / 0.02 | ±0.035 / 0.06 / 0.12 | 0.02 / 0.036 / 0.072（五个随机种子中的最差值） |

## 在 Python 中复用

```python
from os_optics_kit.sfr import measure_sfr, synthetic_edge, true_sfr

image = synthetic_edge((100, 80), angle_deg=5.0, sigma_px=0.5)
result = measure_sfr(image)
f, sfr = result["frequency"], result["sfr"]
print(result["angle_deg"], abs(sfr - true_sfr(f, 0.5, 5.0))[f <= 0.5].max())
```

`measure_sfr(array)` 接受已线性化的数值，不写文件；`analyze_sfr(path, out_dir, SfrConfig(...))`
运行完整命令。
