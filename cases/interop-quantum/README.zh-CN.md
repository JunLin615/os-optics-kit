# 基于线性光学网络导出的 Hong-Ou-Mandel 干涉

[English](README.md)

两个 780 nm 光源在 50:50 平板分束镜上相遇。引擎把光路描述为线性光学网络：输入与输出模式、传输矩阵、每条路径的群延迟以及
约定。量子光学模拟器根据该导出计算光子统计；项目中的激光器只定义模式、波长与偏振。

![三维视图：光源 A 与 B、分束镜 BS、探测面 D1 与 D2 以及追迹光束](images/iso.png)

![俯视图：光源 A 与 B、分束镜 BS 以及探测面 D1 与 D2](images/top.png)

两幅图都是引擎对 `project.json` 一次运行的无浏览器快照：`os-optics view run --no-browser --snapshot DIR` 加 `--views iso --size 960x540` 运行一次，再加 `--views top --size 960x540` 运行一次。
在 kit 检出目录中，`os-optics-kit cases images interop-quantum` 会按 `images.json` 中的记录原样重新渲染这些视图。

## 运行

```bash
os-optics-kit cases copy interop-quantum --dest .
cd interop-quantum
os-optics project run project.json --out-dir run
os-optics project network project.json --out hom.network.json
python -m pip install -r requirements-quantum.txt
python build_layouts.py --out q
python run_quantum.py q --inputs quantum_inputs.json
```

上游声明的许可证：perceval-quandela 为 MIT，其必需依赖 exqalibur 为 CC BY-NC-ND 4.0（见软件包元数据），piquasso 为
Apache-2.0。

## 结果

| 目标 | 数值 | 界限 | 评估 |
| --- | --- | --- | --- |
| `d1_power` | 1 mW | 1 mW ± 1 % | MET |
| `d2_power` | 1 mW | 1 mW ± 1 % | MET |

| 量（已验证的运行） | 结果 |
| --- | --- |
| Perceval 与闭式参考值之差，全部情形 | 4e-8 |
| Piquasso 与闭式参考值之差，全部情形 | 2e-9 |
| ±0.3 mm 延迟扫描中的 HOM 凹陷可见度 | 0.9491（参考值 0.9491） |
| 仅按相位延迟补偿的 3 mm 窗口 | 不可区分度 0.95 → 0.854 |
| 1 mm 马赫-曾德尔干涉仪中的 0.25 ps 光子 | 拒绝：`SINGLE_FREQUENCY_MODEL_INVALID` |

导出中每条路径的群延迟使窗口 0.163 ps 的群延迟失配可见。已在 Python 3.14.7 上用 os-optics 0.13.0 验证
（perceval-quandela 1.3.1、piquasso 8.0.1）。
