# 分析实验测量数据

[用户指南](README.md) · [English](../en/measurements.md) · 简体中文

使用 [analyze_measurements.py](../../examples/analyze_measurements.py) 扣除背景，
按需利用同步参考通道归一化，并进行线性拟合。此流程无需运行光学仿真，也无需安装
OS Optics。示例 CSV 全部是**合成数据**，包含已知关系和刻意设置的无效行。

## 安装与运行

在 Python 3.11+ 虚拟环境中，从仓库根目录执行：

```bash
python -m pip install -r requirements-examples.txt
python examples/analyze_measurements.py --help
python examples/analyze_measurements.py examples/data/measurements/detector_response.csv --x input_power_mW --signal signal_V --background dark_V --x-unit mW --signal-unit V --maximum-rmse 0.003 --out run-detector
python examples/analyze_measurements.py examples/data/measurements/reference_drift.csv --x time_s --signal signal_V --background dark_V --x-unit s --signal-unit V --reference reference_V --reference-background reference_dark_V --reference-unit V --maximum-rmse 0.000000000001 --out run-reference
```

每条命令向标准输出写入一个紧凑 JSON 对象。每次运行必须使用**新的输出目录**；
已存在的目录会被拒绝，以保留先前结果。两个示例均因排除刻意设置的无效行而返回
`WARNING`，退出码为 0，RMSE 目标均通过。目标未通过时也会保存完整分析结果。

| 示例 | 总行数 / 可用 / 排除 | 预期拟合关系 | 检查容差 |
| --- | --- | --- | --- |
| 探测器 | 10 / 7 / 3 | `response_V = 2 * input_power_mW + 0.005`，RMSE 为 0.002 V | 斜率、截距和 RMSE 的绝对误差均 ≤ 1e-12 |
| 参考通道 | 13 / 8 / 5 | `ratio = 0 * time_s + 0.4`，RMSE < 1e-12 | 斜率和截距的绝对误差均 ≤ 1e-12 |

探测器示例扣除 0.020 V 暗背景后，恢复 2 V/mW 响应，保留 0.005 V 截距及小幅确定性
残差。脚本不会强制拟合线穿过原点。实际校准时，应记录经校准的入射功率或滤光片
透射率及暗读数，保持带宽和增益不变，并检查残差是否显示饱和或其他非线性。
RMSE 较小本身并不能确定探测器的线性工作范围或完成探测器验证。

参考通道示例在相除前扣除**两个通道各自的暗背景**：

```text
corrected_signal = signal_V - dark_V
corrected_reference = reference_V - reference_dark_V
ratio = corrected_signal / corrected_reference
```

0–7 s 内，扣除背景后的信号和参考均增长 7%，比值保持 0.4。两通道单位都是 V，因此
比值无量纲；只有校准通道增益和分光比后，才可将其解释为样品透射率。参考归一化
只能抵消共同的光源波动，不能去除分光后出现的漂移或互不相关的探测器噪声。

## 使用自己的 CSV

输入应为 UTF-8 CSV（可带 BOM），使用逗号分隔和小数点，表头列名必须唯一且非空。
用 `--x`、`--signal`、`--background` 显式指定列名，脚本不会根据名称推断含义。
信号和背景必须使用 `--signal-unit` 指定的相同单位；脚本不进行单位换算。

归一化时，必须同时提供 `--reference`、`--reference-background` 和 `--reference-unit`。
两个参考列必须使用相同的参考单位，每个角色须选择不同的列。单位参数只是标签：
信号与参考的单位标签相同时，响应单位为 `1`，否则为两者之比。自变量单位由
`--x-unit` 指定，斜率单位为响应单位除以自变量单位。运行前需确认物理量及单位匹配。

脚本不填补缺失值，也不自动剔除离群点。扣除背景后为负的信号仍然有效。
以下情况会排除该行：

- 必需字段为空、缺失、不是数值，或为 NaN、正负无穷。
- 列数与表头不一致，包括空行。
- 扣除或相除后产生非有限数值。
- **扣除暗背景后的**参考值为零、负数，或不超过 `--minimum-reference`。
  该阈值默认是 0，单位与参考通道相同；可以根据噪声水平设置正阈值，避免除以极小参考值。

`rows.total` 统计表头后的逻辑 CSV 记录，包含空记录；带引号字段跨越多行时仍算一条。
`rows.used` 是预处理后的可用行数，`rows.excluded` 是其余行数；存在拟合时，`fit.n`
记录实际拟合样本数。一行可能有多个排除原因，因此 `exclusion_counts` 之和可能大于
被排除行数。CSV 语法错误、缺少必需列或配置无效会终止命令并返回错误。

## 查看输出

| JSON 链接的文件 | 内容 |
| --- | --- |
| `original-input.csv` | 原始输入的逐字节副本；不会写入源文件 |
| `processed.csv` | 原始列和全部记录，以及以 `_analysis_` 开头的记录号、末尾行号、可用标记、排除原因、校正值、响应、拟合值、有符号残差和输入 SHA-256；多余单元格保存在 `_analysis_extra_cells` |
| `measurements.png` | 校正后通道、响应及拟合、残差，包含坐标轴名称、单位、行数及输入哈希 |
| `provenance.json` | 输入路径/哈希/大小、列映射、单位、算法、UTC 时间、脚本哈希、Python/Matplotlib 版本、来源署名及文件哈希 |
| `summary.json` | 与标准输出相同的紧凑摘要 |

拟合采用带截距的非加权普通最小二乘法，至少需要三条可用观测，且自变量至少有两个
不同值。残差定义为**观测值 − 拟合值**。RMSE 将残差平方和除以 `n` 后开方；残差标准差
和系数标准误使用 `n - 2` 自由度。这些标准误假设自变量精确、响应误差独立且方差相等，
不包括校准不确定度、相关漂移或归一化引起的噪声变化。假设不成立时，应选择更合适的
模型。响应为常数时（如参考示例），`r_squared` 为 `null`，应查看斜率和残差。

拟合先减去第一条观测，再计算偏移量均值，以保留大时间戳或大直流偏置上的细微变化。
复用时可采用稳定形式
`response_origin + (response_offset_at_x_origin + slope * (x - x_origin))`；
摘要也提供常规 `slope` 和 `intercept`。残差在偏移量坐标中计算；直流偏置极大时，
保存的拟合响应可能因舍入而丢失微小残差。无法表示的数值范围会返回错误，需同步
调整输入数值尺度及单位标签。

`--maximum-rmse` 使用响应单位设定目标。应结合 `targets`、`issues`、`rows`、`fit`
查看结果；状态与仓库其他示例的退出码一致：

| 状态 / 退出码 | 含义 |
| --- | --- |
| `PASS` / 0 | 拟合完成且没有诊断问题；如配置了 RMSE 目标，则目标通过 |
| `WARNING` / 0 | 拟合完成，但存在排除行或常数响应导致的 R² 未定义 |
| `FAIL` / 1 | 超过配置的 RMSE 上限 |
| `UNKNOWN` / 2 | 可用观测不足或自变量无变化；仍保存审计文件 |
| 错误 / 4 | 输入、配置、依赖或运行错误；标准错误输出 JSON 错误对象 |

拟合完成不等于科学结论已通过验收。未指定 `--maximum-rmse` 时，不检查验收边界。
即使标量误差满足要求，残差结构仍可能提示模型不合适。不要忽略排除行，也不要将
常数响应的 R² 未定义误认为拟合失败。

## 在 Python 中复用

```python
from examples.analyze_measurements import MeasurementConfig, analyze_measurements

summary = analyze_measurements(
    "examples/data/measurements/detector_response.csv",
    "run-detector-python",
    MeasurementConfig(
        x="input_power_mW", signal="signal_V", background="dark_V",
        x_unit="mW", signal_unit="V", maximum_rmse=0.003,
    ),
)
print(summary["fit"]["slope"])
```

`preprocess_csv(bytes, config)` 和 `fit_linear(x, y)` 也可独立导入，不写入文件，也无需
绘图库。参见英文[数据集定义与来源署名](../../examples/data/measurements/README.md)
及[验证记录](../measurement-validation.md)。此流程用于补充光学仿真；修改仿真设计时，
另行使用英文 [agent 编辑配方](../agent-recipes.md)。
