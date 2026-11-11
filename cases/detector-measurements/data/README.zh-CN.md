# 合成测量数据与来源说明

[English](README.md)

这两个 CSV 文件中的读数是合成的：按下列公式构造，答案已知，供本案例的 `os-optics-kit measurements`
分析使用（[README](../README.zh-CN.md)）。

## 探测器响应

`detector_response.csv` 包含 `input_power_mW`（mW）、`signal_V`（V）、`dark_V`（V）以及文本标签
`data_origin`。七个有效行的序号为 `i = 0..6`：

```text
input_power_mW = i / 10
dark_V = 0.020
signal_V = dark_V + 2 * input_power_mW + 0.005 + residual_V[i]
residual_V = [0.002, -0.003, 0.001, 0, 0.001, -0.003, 0.002]
```

残差之和为零，且与 x 的协方差为零。因此在实数运算下，普通最小二乘斜率为 2 V/mW，截距为 0.005 V，RMSE 恰为
0.002 V。测试对这些量接受 1e-12 的绝对误差。另外追加的三行分别检验信号缺失、入射功率无效和暗背景缺失。

## 参考漂移

`reference_drift.csv` 另有 `time_s`（s）、`reference_V`（V）和 `reference_dark_V`（V）。八个有效行的
`time_s = i = 0..7`：

```text
reference_dark_V = 0.010
dark_V = 0.020
reference_V = reference_dark_V + 1 + 0.01 * i
signal_V = dark_V + 0.4 * (1 + 0.01 * i)
```

扣除暗读数后的两个通道都增长 7%；二者之商保持 0.4。在实数运算下，比值的拟合斜率为零，截距为 0.4，RMSE 为零。
测试使用 1e-12 的绝对容差。对这一恒定响应，R² 无定义。另外追加的五行分别检验修正后参考值为零、参考值缺失、
信号无效、修正后参考值为负以及参考背景缺失。

## 来源与改动

工作流思路改编自 **JunLin615 and contributors**，*Free-space Optical Experiment Benchmark*，提交
`e72ad3a9b6be12c7f0dbb00121fda23289c4e908`：

- [EX-DETECT-01: Detector linearity calibration](https://github.com/JunLin615/free-space-optical-experiment-benchmark/blob/e72ad3a9b6be12c7f0dbb00121fda23289c4e908/benchmark/case_library/EX-DETECT-01.md)（探测器线性度标定），blob `81fc82e61aec5e3929b51f45eea4eb8c6b869817`。
- [EX-DRIFT-01: Reference channel for slow drift](https://github.com/JunLin615/free-space-optical-experiment-benchmark/blob/e72ad3a9b6be12c7f0dbb00121fda23289c4e908/benchmark/case_library/EX-DRIFT-01.md)（用参考通道处理缓慢漂移），blob `55374f93e53046837423e909b2e55696bbe12854`。

该基准的案例内容采用 [Creative Commons Attribution 4.0
International](https://creativecommons.org/licenses/by/4.0/) 许可，见其
[LICENSE-CONTENT](https://github.com/JunLin615/free-space-optical-experiment-benchmark/blob/e72ad3a9b6be12c7f0dbb00121fda23289c4e908/LICENSE-CONTENT)。
本改编把探测器案例中示意性的 12% 滤光片测试换成标定功率扫描，加入明确的偏置、确定性残差和无效记录，并把参考案例中
7% 共同漂移的思路落实为具体的 CSV 数值。Python 实现、这些数据的公式以及实际数据值均为本仓库编写。没有复制任何基准
数据集、标准答案或评分程序。案例 README 解释了改编后的工作流；生成的来源记录（provenance）保存这些来源链接与确切修订版本。
