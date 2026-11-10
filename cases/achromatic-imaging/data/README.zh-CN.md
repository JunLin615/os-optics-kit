# 材料数据与处方

[English](README.md)

数值 Sellmeier 系数来自 SCHOTT 公开光学玻璃数据表：
[N-BK7](https://www.schott.com/shop/medias/schott-datasheet-n-bk7-eng.pdf?context=bWFzdGVyfHJvb3R8NjkxODAwfGFwcGxpY2F0aW9uL3BkZnxoZTUvaDM4Lzg4MTAzMTYxMDM3MTAucGRmfGJjNmI4ZjFmY2Q1NjMxMTE0MjkzMTUwOGRmMTUzOTg2NWJjZTgzMjA0OTc2NTNiMThjN2RhMjI4NGZmMWM4MWU)
及 [N-F2](https://media.schott.com/api/public/content/d9b4f8b7b230479fb8902ce568b5fd3b?v=a7e44e4e)，
访问日期为 2026-11-11。仅转录材料数值事实，未使用供应商图纸或镜片处方。

模型采用 `n²=1+Σ Bᵢ λ²/(λ²−Cᵢ)`，λ 单位为 µm，B 无量纲，C 单位为 µm²。

| 玻璃 | B1、B2、B3 | C1、C2、C3 [µm²] |
| --- | --- | --- |
| N-BK7 | 1.03961212, 0.231792344, 1.01046945 | 0.00600069867, 0.0200179144, 103.560653 |
| N-F2 | 1.39757037, 0.159201403, 1.2686543 | 0.00995906143, 0.0546931752, 119.248346 |

声明波段为 450–700 nm。d 线折射率分别为 1.51680003 与 1.62005314。引擎原生 `lens.achromat` 构建器使用
这些色散数据，以及 75 mm EFL、12.7 mm 外径、6 mm 通光直径、3/2 mm 中心厚度和零冠玻璃形状因子。
它求解中心波长 EFL 及短/长波长近轴 BFL 相等的条件，再把半径舍入至 1 nm。`prescription.json` 是设计记录；
上级目录的 `project.json` 包含完整材料与表面模型。此声明模型的胶层厚度、面形误差与体吸收均为零。
案例代码、处方及文档为原创 MIT 贡献。

`snapshot.json` 为已发布引擎生成的紧凑数值参考，并带有源 Project 摘要。完整运行和分析数组生成到用户输出目录。
