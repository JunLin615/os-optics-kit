# 仅用强度反馈的像差校正

[English](README.md) · [智能体案例说明](CASE.md)

球差无法控制时，五个相位自由度能恢复多少轴上焦点强度？本案例使用一个
标量强度传感器进行优化，在相同透射功率下比较理想、受扰和校正后的焦斑。

![引擎扩束光路的三维视图：光源 `laser`、理想透镜 `expander_negative` 与 `expander_positive`，以及交接面 `modulator`](images/iso.png)

![沿扩束光路光轴的截面：光束在两片透镜之间扩展，此后平行传播至 `modulator`](images/section.png)

两幅图都是引擎对 `project.json` 一次运行的无浏览器快照：`os-optics view run --no-browser --snapshot DIR` 加 `--views iso --size 960x540` 运行一次，再加 `--section ALL --frame=scene --size 1360x440` 运行一次。
在 kit 检出目录中，`os-optics-kit cases images bounded-aberration-correction` 会按 `images.json` 中的记录原样重新渲染这些视图。
图中只有引擎扩束光路：光瞳、相位模式与傅里叶透镜属于 `workflow.py`。

## 运行

```bash
os-optics-kit cases copy bounded-aberration-correction --dest .
cd bounded-aberration-correction
python -m pip install -r requirements-aberration.txt
python workflow.py --out out/default
python workflow.py --out out/stronger --spherical-waves 0.1
python workflow.py --out out/default --redraw-only
```

外部计算需要 Python 3.12+ 及固定版本依赖。使用源码时，在本案例目录运行。
每次计算使用新的输出目录。默认计算在 Python 3.14.8、os-optics
0.13.0 下约需 3 秒，无需下载目录。打开生成的 `preview.png` 查看焦斑。

## 光学模型与数据交接

引擎追迹 532 nm、20 mW 的 TEM00 光源（束腰半径 0.65 mm），以及位于
z = 20、60 mm 的理想 −40、+80 mm 透镜。z = 119 mm 处的 `handoff`
提供波长、两轴 q、功率与场重建适用性。引擎目标 `relay_radius` 为 MET：
1/e² 强度半径 1.300312968 mm，位于 1.29–1.31 mm 范围内。

外部计算用交接的半径和功率构建半径 1.5 mm 的圆光瞳。固定相位板补偿名义
曲率，扰动和校正相位在光瞳处作用，再经过理想 120 mm 傅里叶透镜形成焦场。
光瞳透射功率为 18.603049 mW，采样振幅按这一解析孔径功率归一化。
传感器无噪声，相位控制连续。PSF 和校正结果来自外部标量近轴计算；保存的
引擎运行描述为它们提供输入的扩束光路。

设归一化光瞳半径为 r、方位角为 θ，六个相位模式依次为
√3(2r²−1)、√6r²cos2θ、√6r²sin2θ、√8(3r³−2r)cosθ、
√8(3r³−2r)sinθ、√5(6r⁴−6r²+1)。默认系数为
[0.24, 0.17, −0.11, 0.12, −0.09, 0.055] waves。
Powell 搜索只使用标量传感器读数，调整前五项，每项范围为 ±0.6 waves，
最后一项保持不变。这组基函数在均匀照明单位圆盘上具有单位 RMS；实际光瞳
使用高斯照明。

## 结果与参数变化

主要观测量为**固定光轴位置的强度**除以同孔径、同功率下的理想轴上强度。
焦斑发生偏移时，仍在原光轴位置取样，因此该指标同时反映光轴处的强度损失。

| 不受控球差系数 | 校正前 | 校正后 | 传感器评估次数 |
| --- | --- | --- | --- |
| 0.055 waves | 0.092393 | 0.895105 | 221 |
| 0.100 waves | 0.106409 | 0.692747 | 149 |

增大不受控球差会降低恢复后的轴上强度。默认离焦校正量为 −0.219892 waves：
在高斯加权下，少量残余离焦能补偿部分球差。
可尝试 `--bound-waves 0.1` 观察执行器饱和。包括较差试探点在内的所有评估
均保存在 `science.npz` 中，收敛检查始终开启。

优化使用 160² 光瞳网格，结果在 512² 网格上评估。增至 768² 后，默认和
较强球差的校正强度比分别变化 0.000185 和 0.000470。独立极坐标高斯求积
给出 0.894818 和 0.692023，与 512² 结果的绝对差均低于 0.00073。
扩束光路的 q 与独立 ABCD 计算相对误差低于 6×10⁻¹⁵；Parseval 功率守恒、
直接相干求和与 FFT 中心值的误差低于 7×10⁻¹⁶。默认结果是有界局部最优解。

## 保存的数据

- `engine/`：归一化引擎输入、结果和光线文件。
- `handoff.json`：包含运行标识的引擎交接数据。
- `config.json`：光学参数、网格和输入项目摘要。
- `metrics.json`：结果、检查、版本、耗时与引擎目标标识。
- `science.npz`：复数光瞳场（√W/mm）、光瞳坐标（mm）、相位（waves）、
  ±80 µm 范围的焦面强度（W/mm²）及坐标（mm）、理想峰值、扰动和校正系数、
  每次传感器试探。状态顺序为理想、受扰、校正后；历史记录前五列是控制量，
  最后一列是无量纲轴上强度比。孔径外未包裹相位不承载光场，绘图时使用
  零振幅掩膜。
- `preview.png`：三状态共用线性强度色标。重绘读取已保存数组并沿用已有计算设置。

傅里叶计算采用 6 mm 光瞳窗口，将 512² 网格补零至 2048² 以细化焦面采样。
若需其它视野，可从保存的复数光瞳重建完整焦场。生成结果保存在版本控制之外。

## 从保存结果制作展示素材（kit 0.3.0）

图库为可选内容，由保存的运行目录生成：

```bash
python present_case.py out/default out/stronger --out talk/en --language en --background light --size 1920x1080 --bundle talk/aberration-en.zip
python present_case.py out/default out/stronger --out talk/zh --language zh-CN --background light
python present_case.py out/default out/stronger --out talk/dark --language zh-CN --background dark
os-optics-kit present gallery talk/en
os-optics-kit present bundle talk/en --out talk/share.zip --only psf,response
```

离线打开 `talk/en/index.html`（或 `talk/zh/index.html`）。图库包含高斯扩束光路及其光束半径目标、外部标量
傅里叶 PSF 和轴上强度对比。`psf` 展示首组结果的三个状态，共用 0–1 线性色标；
`response` 将各组轴上强度除以该组等功率理想轴上强度。比较输入应使用相同的
光学参数、网格和控制边界，以球差系数作为变化参数。

辅助脚本读取保存的数组与指标，以复数光瞳场核对强度比。每种样式使用新的
`--out`。图库中的三维光路图由 kit `render` 截取：需要 `render` 扩展（已在
`requirements-aberration.txt` 中固定）以及 Chrome 或 Chromium。中文需要微软雅黑或
Noto Sans CJK SC 等字体。

使用上面的 `present_case.py` 命令从保存的运行目录重建完整图库，目录迁移后传入
新路径。`presentation.json` 对应原生半径目标图；对该文件运行 `present build`
会将图库索引替换为这张图。完整图库提供 PNG、SVG 曲线图、双语图注、数据快照和
输入哈希。相机设置、数据来源、单位、归一化及数据选择保存在各图的溯源和重绘文件中。

ZIP 包含离线图库与外部绘图快照。解压 `aberration-en.zip` 后，使用包内脚本重绘快照：

```bash
python aberration-en/assets/psf/present_case.py --redraw-external aberration-en/assets/psf/redraw.json --out psf-zh --language zh-CN --background dark
python aberration-en/assets/response/present_case.py --redraw-external aberration-en/assets/response/redraw.json --out response-zh --language zh-CN --background dark
```

这些命令使用包内数据，默认保留已保存主题，指定样式参数时覆盖对应设置。
重建包含扩束光路与 PSF 的完整图库，需要原始运行目录及上面的完整图库命令。
