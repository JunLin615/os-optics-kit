# 案例库

[os-optics-kit](../../README.zh-CN.md) · [English](../en/case-library.md) · 简体中文

每个案例都是完整的设计，可用已发布的引擎重新运行，并与记录的结果比较。每个案例带有一个类别、一到三个领域、来源、
发布依据、自动推导的规模和若干标签，因此可以浏览和筛选：`os-optics-kit cases list --class lens`、`--domain`、
`--provenance`、`--publication`、`--scale`、`--tag`、`--catalog RELEASE|none`。`os-optics-kit cases vocabulary`
列出所有取值及其英文和中文名称。具体的文件规则见[案例格式（英文）](../case-format.md)。

## 类别

每个案例只有一个类别，按其主要结论确定。仅有机械载体不改变类别。

| 类别 | 含义 |
| --- | --- |
| 镜头（`lens`） | 同轴透镜或透镜组（处方、理想或表格化透镜模型），结论是透镜自身的行为：聚焦、处方与成像质量。 |
| 自由空间平台（`free-space`） | 在开放空间中逐个元件搭建的光路（扩束、中继、折转、分束、偏振链、光阑），结论是装配后的光路满足具体的光学需求；使用目录元件时，还包括它可以采购并搭建。 |
| 集成光路（`integrated-path`） | 由定制机械固定的光路（光谱仪、单色仪、封装激光器），结论是内部光路与其机械约束共同成立。 |

## 领域

每个案例一到三个，主要领域在前。

| 领域 | 范围 |
| --- | --- |
| 成像（`imaging`） | 成像与成像质量：点列、MTF、Strehl 比、PSF |
| 高斯光束（`gaussian-beam`） | 传播、聚焦、扩束与束腰 |
| 衍射（`diffraction`） | 波动光学、光阑、傅里叶光学，以及由衍射得到的 PSF 和 MTF |
| 色散（`dispersion`） | 随波长变化的行为、色焦移 |
| 偏振（`polarization`） | 偏振态与偏振元件 |
| 干涉（`interference`） | 相干场的叠加 |
| 量子光学（`quantum-optics`） | 光子统计与线性光学网络 |
| 纳米光子学（`nanophotonics`） | 超表面、RCWA |
| 光束整形（`beam-shaping`） | 相位掩模、平顶光束、光片 |
| 波前控制（`wavefront-control`） | 用模式校正像差 |
| 光束指向（`beam-steering`） | 反射几何、光束位置与指向 |
| 辐射度量（`radiometry`） | 探测器上的功率、探测与测量数据 |

## 规模

规模由 Project 计算得出，从不手写。元件是光源以外的光学部件（透镜组按其中每片透镜计数，孔径光阑不计）；分支是
分束器。

| 规模 | 元件与分支 |
| --- | --- |
| 小（`small`） | 最多 3 个元件，无分支 |
| 中（`medium`） | 最多 12 个元件，最多 2 个分支 |
| 大（`large`） | 最多 40 个元件，最多 8 个分支 |
| 超大（`very-large`） | 更大的系统 |

## 来源与发布依据

来源说明设计从何而来：文献（`literature`）、专利（`patent`）、厂商文件（`manufacturer-file`）、客户任务
（`customer-task`）或自建（`self-built`）。发布依据说明它为何可以在此发布：

| 发布依据 | 含义 |
| --- | --- |
| 原创（`own-work`） | 为本工具包编写，按仓库的 MIT 许可证发布 |
| 开放许可（`open-license`） | 采用开放许可的第三方材料，注明出处 |
| 授权发布（`permission`） | 经权利人书面许可发布 |
| 公有领域（`public-domain`） | 处于公有领域的材料 |

自建案例一定是原创。其他来源的案例注明出处，并给出原创以外的发布依据。

## 图片

每个案例最多展示五张图片：一到三张系统视图和最多三张图表，每张都是不超过 400 KiB 的 PNG。系统视图由引擎直接生成，
无需浏览器：`os-optics view RUN --no-browser --snapshot DIR --views iso` 生成三维视图，`--section ALL` 生成镜头的
二维截面。在工具包仓库中，`os-optics-kit cases images NAME` 生成这些视图并记录到 `images.json`；不带视图选项再次
运行时，它按 `images.json` 中记录的视图原样重绘。第一张图是三维快照视图（iso 视角清晰时优先），镜头案例还要展示截面。
图表是案例自带脚本绘制的曲线；结果表格保持为 Markdown 表格。两份 README 以相同顺序展示相同的图片，并相互链接。

工具包的 `render` 和 `present` 命令用于制作演示级图片、相机组、图集和动画；它们需要 `os-optics-kit[render]` 和
Chrome 或 Chromium，案例图片从不需要它们。
