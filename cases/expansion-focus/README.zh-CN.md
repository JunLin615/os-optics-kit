# 扩束与聚焦

[English](README.md) · [智能体案例说明](CASE.md)

波长 780 nm、束腰半径 0.5 mm 的高斯光束由两片理想薄透镜（f = 50 mm 与 150 mm）扩束三倍，再由第三片透镜
（f = 100 mm）聚焦。该设计不需要目录且完全参数化：`workflow.py` 按你的输入重建设计，并逐一研究各参数。

![三维视图：光源 SRC、扩束透镜 L1 与 L2、`collimated` 面、聚焦透镜 L3、`focus` 面与追迹光束](images/iso.png)

![沿光束的截面：L1 与 L2 扩束，L3 把光束聚焦到 `focus` 面](images/section.png)

两幅图都是引擎对 `project.json` 一次运行的无浏览器快照：`os-optics view run --no-browser --snapshot DIR` 加 `--views iso --size 960x540` 运行一次，再加 `--section ALL --frame=scene --size 1200x420` 运行一次。
在 kit 检出目录中，`os-optics-kit cases images expansion-focus` 会按 `images.json` 中的记录原样重新渲染这些视图。

## 运行

```bash
os-optics-kit cases copy expansion-focus --dest .
cd expansion-focus
os-optics project run project.json --out-dir run
os-optics results targets run
python workflow.py --out wf
```

## 结果

| 目标 | 数值 | 界限 | 评估 |
| --- | --- | --- | --- |
| `expanded_radius` | 1.50112 mm | 1.5 mm ± 2 % | MET |
| `spot_radius` | 0.016552 mm | ≤ 0.02 mm | MET |
| `waist_location` | −0.03649 mm | −0.5 … 0.5 mm | MET |

默认工作流运行 22 个设计，每个引擎数值都与独立的复 q 高斯计算一致。

| 研究 | 取值 | PASS | FAIL | 超出模型 |
| --- | --- | --- | --- | --- |
| `gap_error_mm` | −5 … 5 | 3 | 4 | 0 |
| `f3_mm` | 50 … 200 | 2 | 3 | 0 |
| `wavelength_nm` | 532, 780, 1064, 1300 | 2 | 1 | 1（超出透镜波段） |
| `waist_radius_mm` | 0.25 … 4 | 3 | 1 | 1（严重截断） |

`studies/` 另含灵敏度研究、波段边缘灵敏度研究、带种子的公差研究（100 个样本中 92 个通过）以及两个计算预算编辑。
已用 os-optics 0.13.0 验证；运行状态 PASS。
