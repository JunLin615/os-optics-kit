# 偏振控制

波长 1030 nm 的线偏振激光（1 mW，偏振沿设计 +Y）依次经过半波片与线偏振器。半波片快轴位于 22.5° 时，偏振方向转到
45°，即偏振器的透光轴，探测面接收 0.891 mW（波片透过率 0.99，偏振器 0.9）。该设计不需要目录且完全参数化：
`workflow.py` 按你的输入重建设计。

![等轴测视图：光源、半波片 HWP、after_hwp 面、偏振器 POL 与 detector 面](images/iso.png)

## 运行

```bash
os-optics-kit cases copy polarization-control --dest .
cd polarization-control
os-optics project run project.json --out-dir run
python workflow.py --out wf
```

## 结果

| 目标 | 数值 | 界限 | 评估 |
| --- | --- | --- | --- |
| `transmitted_power` | 0.891 mW | ≥ 0.8 mW | MET |
| `output_azimuth` | 45° | 45° ± 0.5° | MET |

默认工作流运行 14 个设计；参与比较的 94 个数值（功率、方位角、斯托克斯参数）全部与琼斯矩阵计算和马吕斯定律一致。

| 研究 | 取值 | PASS | FAIL | 超出模型 |
| --- | --- | --- | --- | --- |
| `hwp_angle_deg` | 0 … 67.5° | 1（22.5°） | 7 | 0 |
| `wavelength_nm` | 1000 … 1100 | 3 | 0 | 2（1064 与 1100 nm，超出波片波段） |

已用 os-optics 0.13.0 验证；运行状态 PASS。
