# 本地实时查看器

[os-optics-kit](../../README.zh-CN.md) · [English](../en/viewer.md) · 简体中文

`os-optics-kit view` 会在你的默认浏览器中打开一个轻量的本地只读页面：当你或编程 Agent 在本地修改原生 Project 文件或持续产出新的运行结果时，页面会自动跟随更新，让你实时看到光路和指标的变化。

查看器仅绑定本机回环地址（`127.0.0.1`），只需安装基础包 `os-optics-kit`（依赖 `os-optics>=0.14,<0.15`），**无需**安装 `[render]` 或 `[plot]` 扩展，**无需**下载 Playwright/Chromium，也**无需**任何云端账号。

## 快速开始

```bash
python -m pip install os-optics os-optics-kit

# 查看并实时跟随一个已保存的运行目录（3D 光路、功率账本、执行状态与指标评估）
os-optics-kit view run

# 查看原生 Project 文件（未关联运行结果时，仅展示标称几何预览）
os-optics-kit view project.json

# 同时关联 Project 与运行目录（自动跟踪新鲜度，绝不张冠李戴）
os-optics-kit view project.json --run run

# 在不修改原文件的前提下，于临时目录执行一次显式本地计算并查看
os-optics-kit view project.json --evaluate
```

常用参数：

- `--port PORT` / `--host 127.0.0.1` — 指定回环端口（默认由系统自动分配空闲端口；拒绝非回环地址）。
- `--project PROJECT.json` — 打开运行目录时，额外指定用于对比新鲜度的 Project 文件。
- `--configuration ID` — 当运行目录包含多构型时，指定查看某一构型。
- `--poll-interval SECONDS` — 文件变化轮询间隔（默认 `0.4` 秒）。
- `--no-open` — 只启动服务并在标准输出打印就绪 JSON，不自动弹浏览器。
- `--once` — 输出单次快照 JSON 后直接退出。

按 `Ctrl-C` 即可干净关闭查看器。

## 页面展示内容

- **交互式 3D 光路与追迹光线** — 直接复用引擎自带的离线 3D 场景视图（零外部 CDN），明确区分光线分支数、可绘制光线数与观测面交叉记录数（完整兼容 `--ray-data summary` 与无观测面运行），底部附光线分支色标、功率与终止状态。
- **诚实的未计算工程几何** — 仅打开 Project 文件时，通过引擎标称布局接口解析目录元件与标称尺寸（无需执行光线追迹）；若个别元件缺少尺寸，会明确标注为示意占位几何，绝不以假尺寸冒充设计。
- **功率账本** — 不仅靠光线颜色示意，而是完整呈现总输入功率、残差、守恒状态、多通道堆叠比例条、各通道精确功率与单位，以及按元件展开的功率明细。
- **执行状态、完整性与新鲜度** — 清晰区分计算是否完成（`COMPLETED` / 触及预算的 `PARTIAL`）、模型适用范围、manifest 哈希校验以及结果新鲜度（`CURRENT` / `HISTORICAL`）。
- **指标达成与观测面物理量** — 逐条展示 `MET` / `UNMET` / `INDETERMINATE` / `NOT_APPLICABLE` 判定、实测值与声明范围，并在部分完成时给出有界区间和未追迹功率提示。
- **界面语言切换（EN / 中文）** — 点击右上角即可随时切换中英文界面，无需重启服务。

## 实时跟随与只读安全保证

- **严格只读**：绝不改写你的 Project 文件或运行目录，服务端拒绝所有写入类 HTTP 请求（`POST`/`PUT`/`DELETE`/`PATCH` 返回 `405`）。
- **单次原子快照绑定**：无论是磁盘读取还是浏览器渲染，3D 几何、光线、功率账本、执行状态与指标判定都按同一 `snapshot_id` 严格绑定（`/engine-view/<snapshot_id>/` 搭配前端双缓冲原子切换）。服务端绝不会把快照 B 的字节返回给请求 A 的链接（已淘汰时返回 `409 SNAPSHOT_SUPERSEDED`），也不会发布未通过 manifest 完整性校验的运行结果。引擎查看器代码（`/engine-assets/`）对所有快照都相同，独立于快照缓存提供；已被淘汰的场景或未能启动的画面会被放弃，页面在保留上一份已验证画面的同时，无需刷新即可前进到最新的有效快照。
- **观测面坐标只有一种参照系**：`quantities[].plane` 中的 `origin_mm`、`u_axis`、`v_axis` 与 `normal` 一律是应用所选配置后的设计坐标系位姿，与 3D 场景绘制观测面的位置一致，无论是仅 Project、匹配运行还是历史运行视图。附着在器件上的观测面保留 `pose_frame: DEVICE_LOCAL` 与 `device_id`，Project 中写下的局部位姿放在 `declared_pose`。
- **几何预览绝不冒充计算结果**：未关联运行结果的 Project 只展示几何布局并明确标注「未计算」；`--evaluate` 仅在启动时于临时目录计算一次，后续文件编辑不会反复触发重算。
- **过期结果不误标**：当 Project 修改后与关联运行不再匹配时，新鲜度立即转为「历史过期」，旧功率与光线会被隐藏，只展示新工程几何，直到新运行写入完成。
- **半写入容错与自动恢复**：当外部 Agent 正在写文件（JSON 截断或目录尚未写齐）时，页面会保持上一份有效快照并显示「等待写入」提示，写入完成后自动恢复更新。
- **视角保持不变**：新的修订（Project 编辑、新的运行、追迹光线变为仅几何再变回来）沿用你当前的相机视角绘制，无论你是否手动调整过视角。视图按钮、Fit 与 Reset 可按需重新取景。历史运行的仅几何视图从该运行的视图方向开始。

## Agent 可读取的状态

页面以两种方式说明当前显示的内容，二者都与 `GET /api/snapshot`（其信封中的 `snapshot`）一致：

- **`#app` 上的属性**（对应屏幕上的快照）：`data-revision`、`data-snapshot-id`、`data-status`（`ok`、`waiting`、`error`）、`data-result-mode`（`computed_run`、`stale_run`、`geometry_preview`）、`data-computed`、`data-freshness`（`CURRENT`、`HISTORICAL`、`UNDETERMINED`、`RUN_ONLY`，未检查时为空）、`data-project-hash`、`data-execution-outcome`、`data-requirement-outcome`、`data-power-available`、`data-power-conserved`（`true`、`false`，账本被隐去时为空）、`data-branch-count`、`data-geometry-only` 与 `data-drawable-ray-count`。每个属性都是快照中同名字段的值，字段为 null 时为空。
- **可见文本**：不适用的徽标会被隐藏**并清空**（`aria-hidden="true"`），页面文本中不会残留早先快照的结论。例如在过期运行上，DOM 中任何地方都没有「功率守恒」。

当前的 3D 画面（`iframe.engine-frame.active`）带有相同的 `data-snapshot-id`，`data-camera` 说明它沿用了上一画面的相机（`carried`）还是从引擎默认取景开始（`default`）。

## 在浏览器中检查已发布版本

在本仓库的检出目录中运行 `python scripts/released_viewer_check.py OUT_DIR --engine-version 0.14.0 --kit-version 0.5.0`：它把已发布的 wheel 安装到检出目录之外的全新环境，核对其 SHA-256 与 PyPI 一致，并在本地 Chrome 的同一页面中跟随已保存的运行、一次 Project 编辑和一次重新运行，每一步比对页面、`/api/snapshot` 与引擎 `results` 命令的结果。`OUT_DIR` 中保存每项检查的 JSON 记录和每个修订的截图；`--kit-source .` 改为检查本检出目录的构建。`Released viewer check` 工作流在 Linux 与 Windows 上运行它（`workflow_dispatch`）。

## 与其他命令的分工

- `os-optics-kit view`：面向日常设计迭代的**本地实时交互查看**（普通浏览器直开，零额外依赖）。
- `os-optics view RUN --no-browser --snapshot DIR --views iso`：无需浏览器的静态系统视图导出。
- `os-optics-kit render` / `os-optics-kit present`：演示级高清出图、图库和幻灯片材料包。
