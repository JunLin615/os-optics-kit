# 圆形平顶光整形与入射束宽敏感性

[English](README.md) · [智能体案例说明](CASE.md)

纯相位平顶设计能容忍多大的照明束宽变化？本案例设计一个圆形图案，再将光源束腰缩小 10%，使用同一相位掩模重新计算。核心区域的强度变异系数（CV）由 **0.01197 升至 0.07450**，但目标区域仍包含几乎全部功率。

![原生参考系统的三维视图：光源 `LASER`、理想透镜 `NEG`、`POS` 与 `FOCUS`、交接面 `phase_input` 及未调制焦面 `unmodulated_focus`](images/iso.png)

![沿光轴的截面：扩束系统扩大光束，`phase_input` 位于 `FOCUS` 前 1 mm，未调制光束在其后 100 mm 处会聚于 `unmodulated_focus`](images/section.png)

两幅图都是引擎对 `project.json` 一次运行的无浏览器快照：`os-optics view run --no-browser --snapshot DIR` 加 `--views iso --size 960x540` 运行一次，再加 `--section ALL --frame=-6,-6,-4,6,6,168 --size 1360x440` 运行一次。
在 kit 检出目录中，`os-optics-kit cases images phase-only-flat-top` 会按 `images.json` 中的记录原样重新渲染这些视图。

原生系统包含 532 nm、10 mW、束腰半径 0.5 mm 的 TEM00 光源，理想 −40/+60 mm 扩束系统和理想 100 mm 傅里叶透镜。引擎在该透镜前 1 mm 处导出 TEM00 交接数据，此处光束半径为 0.750275 mm。外部计算将该复场传播 1 mm，在透镜平面施加相位掩模，再计算后焦面复场。原生焦点与上面的视图表示未调制的参考系统；平顶结果来自保存的外部复场。

## 运行与修改参数

固定版本的科学计算依赖需要 **Python 3.12 或更新版本**，本例已在 Python 3.14.8 验证。安装 os-optics 0.13.0 与 os-optics-kit 0.3.0，复制本案例，然后在复制后的目录内运行：

```bash
python -m pip install -r requirements-flat-top.txt
python workflow.py --out out/default
python workflow.py --out out/wider --width-scale 1.1
python workflow.py --out out/larger --radius-mm 0.45
python workflow.py --out out/default --redraw
```

第一个计算同时运行标称照明和光源束腰缩至 0.9 倍的方案。`--width-scale` 通过第二次原生计算改变光源束腰，随后使用**固定的标称掩模**，保留原有波前补偿。`--radius-mm` 为不同目标半径重新设计掩模。每次计算使用新的输出目录。`--redraw` 只读取保存的数组重绘预览，不运行引擎或优化器。默认计算连同检查，在记录的 Windows 环境中约需 7 秒。

## 解读结果

目标强度正比于 `exp(-2*(r/0.42 mm)^16)`。CV 是 `r < 0.315 mm`（目标半径的 75%）内强度标准差除以平均值。目标区域功率比例是目标强度超过峰值 10% 的区域内积分功率，除以整个采样焦面的功率。两项指标均无量纲；场强度单位为 W/mm²。

| 照明条件 | 核心 CV | 目标区域功率比例 |
| --- | ---: | ---: |
| 设计入射光 | 0.0119713 | 0.9948493 |
| 光源束腰 ×0.9，固定掩模 | 0.0745046 | 0.9970254 |

两个输出的积分功率均为 10 mW。目标区域功率比例与照明均匀性分别描述光束的不同特征。打开 `preview.png` 查看使用共同色标的强度图与中心剖面；较窄入射光在中心产生更高强度。

相位初值来自平滑径向能量映射。251 次 Gerchberg–Saxton 更新最小化整个目标的相对强度平方误差，使其从 0.00059689 降至 0.00009383。平滑初始场的核心 CV 已达 0.00495，功率比例为 0.98976。优化改善目标吻合程度和功率比例，同时将核心 CV 增至 0.01197；优化以目标强度误差为目标，CV 则反映均匀性的折中变化。

## 模型与检查

原生目标检查照明半径是否在 0.749–0.752 mm 内。外部模型采用单色、相干、标量近轴条件，使用连续无损相位掩模与理想透镜，保留交接数据中的功率与曲率。入射网格为 384×384，窗口宽度 4.608 mm，焦面采样间隔 0.011545 mm。分析实际像素化掩模时，还需提供像素间距、相位级数、填充率和透过率。

工作流以独立复 q 传播核对原生交接，以直接积分核对傅里叶复振幅，并检查两个场的功率守恒。将固定掩模的入射采样间隔减半后，在 `r < 0.525 mm` 内得到焦面强度相对 L2 差异 3.90×10⁻⁶。该检查插值复相位因子，保持物理窗口不变，不重新优化。默认结果还要求 CV 小于 0.02、目标区域功率比例大于 0.99。`os-optics-kit cases verify phase-only-flat-top` 检查原生目标；外部光学结果由本工作流计算与检查。

## 保存的数据

- `native-default/`、`native-width-variant/`：完整引擎运行；对应的 handoff JSON 文件保留光学输入交接。
- `science.npz`：单位为 mm 的入射与焦面坐标、单位为 √(W/mm²) 的入射和输出复场、单位为弧度的固定相位掩模、归一化总和为 1 的目标、初始输出与迭代误差。二维数组轴顺序为 `(y, x)`。
- `configuration.json`：物理参数、指标区域定义、相位约定与掩模标识。
- `results.json`：原生运行标识和目标、外部观测量、独立检查、依赖版本与实测计算耗时。

生成的运行目录和数组保存到你指定的输出目录。本案例可以通过 `os-optics-kit cases copy phase-only-flat-top --dest DEST` 独立复制使用。

## 展示保存结果

图库为可选内容。其中的三维系统图由 kit `render` 截取，需要 `render` 扩展（已在
`requirements-flat-top.txt` 中固定）以及 Chrome 或 Chromium。为每种样式创建新的输出目录：

```bash
python present_case.py out/default --out figures/en --bundle figures/flat-top-en.zip
python present_case.py out/default --out figures/zh --language zh-CN
python present_case.py out/default --out figures/dark --language zh-CN --background dark
```

在本地打开 `index.html`。图库包含高斯参考系统、照明半径对比，以及共用物理
色标的外部标量傅里叶强度图和中心剖面。变体的原生半径目标随光源宽度缩放，
外部相位掩模保持固定。

图旁提供 PNG、SVG 曲线图、双语说明、数值快照和重绘设置。中文绘图需要微软雅黑
或 Noto Sans CJK SC 字体。辅助脚本读取保存结果，以保存的场核对 CV 与功率。
相机设置、输入哈希、单位及数据来源保存在图件记录中。

使用 `present_case.py` 从保存的运行目录重建完整图库，迁移后传入新目录位置。
`presentation.json` 对应原生目标图；对该文件运行 `present build` 会将图库
索引替换为这张图。

ZIP 包含离线图库与外部强度快照。解压 `flat-top-en.zip` 后使用包内脚本重绘：

```bash
python flat-top-en/assets/flat-top/present_case.py --redraw-external flat-top-en/assets/flat-top/case-redraw.json --out redrawn --language zh-CN --background dark
```

该命令使用包内数据。完整系统及目标重绘需要保存的原生运行目录和上面的完整
图库命令。
