# 柱面光片的厚度与传播范围

柱面透镜只聚焦高斯光束的一个横向方向。缩短焦距会减小光片厚度，同时缩短有效传播范围。本案例通过真实双界面柱面处方和可分离的近轴高斯模型，比较这两个变化。

![柱面透镜与观测面](images/layout.png)

光源沿 +z 传播，波长为 488 nm，束腰半径为 1 mm，功率为 10 mW。透镜前顶点位于 z=20 mm；透镜厚 2 mm、折射率 n=1.52，入射面为曲面、出射面为平面。柱轴沿 x，因此聚焦方向为 y。默认入射面半径为 26 mm，通光直径为 10 mm，每个面的功率透射率为 0.995。折射率在 450–650 nm 范围内取常数，表面采用理想面形，M²=1。

| 透镜焦距 | 束腰处光片 FWHM 厚度 | 厚度不超过束腰值 sqrt(2) 倍的全范围 | 束腰 z 坐标 |
| --- | --- | --- | --- |
| 50 mm | 9.14456 um | 0.776659 mm | 70.6824 mm |
| 40 mm | 7.31569 um | 0.497068 mm | 60.6834 mm |
| 65 mm | 11.88777 um | 1.312519 mm | 85.6796 mm |

三个方案输出功率均为 9.90025 mW。默认焦点处的宽轴 FWHM 为 1.17748 mm。40 mm 透镜使束腰厚度减小约 20%，传播范围缩短约 36%。本例目标为束腰厚度不超过 10 um、全范围不小于 0.45 mm；40 和 50 mm 方案均满足；65 mm 方案的传播范围更长，但厚度未达标，流程退出码为 1。

## 运行与参数调整

在本案例目录中使用 Python 3.12 或更高版本，安装固定版本依赖后运行：

```bash
python -m pip install -r requirements-light-sheet.txt
os-optics project run project.json --out-dir ../sheet-native
os-optics results targets ../sheet-native
python workflow.py --out ../sheet-default
python workflow.py --focal-length-mm 40 --out ../sheet-f40
python workflow.py --focal-length-mm 65 --out ../sheet-f65
python workflow.py --out ../sheet-default --plot-only
```

在已检查的 Windows/Python 3.14 环境中，默认完整流程约需 4–5 秒，包含预览绘制；物理配置构建与计算约需 2 秒。`--focal-length-mm` 接受 25–110 mm，`--input-waist-mm` 接受 0.5–1.5 mm。改变焦距时，流程使用公开的原生建模接口重新构造柱面处方。缩短焦距或增大入射束腰，会减小光片厚度和传播范围；引擎逐项评估两个目标。流程退出码 1 表示至少一个示例阈值未满足。

## 读取保存的结果

引擎计算双界面折射、两轴高斯 q 参数和功率。z=23 mm 观测面的交接数据保存在 `handoff.json`，原生目标评估保存在 `targets.json`。外部脚本由这些数据计算 FWHM = sqrt(2 ln 2) w0、全范围 = 2 zR，其中 w0 是薄轴的 1/e² 强度半径。这里的范围指厚度不超过束腰厚度 sqrt(2) 倍的区间，与固定的 10 um 束腰厚度目标分别定义。

`profiles.npz` 保存以 mm 为单位的 z、y 坐标和两轴 1/e² 半径，x=0 纵截面强度的单位为 W/mm²，束腰面沿 x 积分后的强度单位为 W/mm。这些曲线由原生 q 和功率代入可分离 TEM00 公式得到。`preview.png` 显示纵截面和厚度曲线。`configuration.json` 保存物理输入，与绘图设置分开；`--plot-only` 仅读取已有数据，不进行仿真。`run/project.json` 是该参数样本经引擎规范化后的配置。

独立 ABCD 计算依次完成 q 在入射面折射、玻璃内传播、出射面折射和到观测面的传播。三个方案中两轴 q 与引擎的相对误差均小于 2e-16。流程还检查 0.995² 的功率透射率，并对束腰边缘分布进行数值积分。外部检查保存在 `metrics.json`，与引擎目标评估分开。所有生成文件均写入指定的输出目录。

## 从保存结果制作展示材料

展示脚本使用 kit 0.3.0、Chrome/Chromium，以及微软雅黑或 Noto Sans CJK SC 中文字体。输入是已完成的 `workflow.py` 结果目录，不是项目文件。首个输入提供参考纵截面和原生视图；加入 65 mm 结果可展示厚度目标未满足的情况。

```bash
python present_case.py ../sheet-default ../sheet-f40 --out ../sheet-talk-en --language en --background light --size 1920x1080 --bundle ../sheet-talk-en.zip
python present_case.py ../sheet-default ../sheet-f40 ../sheet-f65 --out ../sheet-talk-zh --language zh-CN --background dark --size 1920x1080 --bundle ../sheet-talk-zh.zip
```

打开 `index.html` 查看三张卡片：原生系统视图、官方 `present` 目标对比、外部纵截面及 FWHM 曲线。图件附有 PNG/SVG、双语图注、数据快照、来源记录和相对路径资产索引；系统视图为原生光栅截图。FWHM 曲线的两个圆点表示 ±zR，图例数值依次为束腰 FWHM 和全范围。原生目标图保留实际的半径与瑞利长度量纲。输入科学文件始终只读；改变语言、背景或尺寸时使用新的输出目录，无须再次求解。

kit 0.3.0 的两处限制在此明确区分：原生 `present` 图型支持 sweep、power 和 targets，不支持这些外部 NPZ 数组；其 view 资产也不能设置本 +Z 模型所需的非平行向上轴，会返回 `CAMERA_UNDEFINED`。因此，本脚本通过公开 `render_shots` 接口设置 Z 光轴、Y 向上生成视图，通过公开展示主题和字体接口绘制外部图，并按图库资产索引协议如实登记两者。`presentation.json` 仍是有效的原生目标图配置；单独重建它不会重绘或保留本脚本额外登记的两张卡片。完整图库应重新运行本脚本。分发包不复制原始原生结果目录。

分发包包含外部数据快照、单位与来源、`case-redraw.json` 和本脚本副本。解压后可以脱离原始结果单独重绘外部图：

```bash
python sheet-talk-en/assets/profiles/present_case.py --redraw-external sheet-talk-en/assets/profiles/case-redraw.json --out ../sheet-profiles-restyled --language zh-CN --background dark --size 1920x1080
```

最后一条命令仅生成外部图及其图库，不重建原生视图或目标图。完整三卡片展示材料通过保存的结果目录及前面的命令重新生成。
