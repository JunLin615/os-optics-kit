# 用已保存的结果制作演示材料

[os-optics-kit](../../README.zh-CN.md) · [English](../en/presentation.md) · 简体中文

`os-optics-kit present` 把已保存的运行和扫描研究整理成可直接用于幻灯片的材料：系统视图、结果图、比较图、
动画、离线图库和可移植的材料包。所有内容都依据一份保存的配置 `presentation.json` 绘制。更改语言、背景、
字号或图片尺寸时，只依据已保存的结果重新绘图，不运行仿真，也不会修改运行和研究目录。

## 安装

```bash
python -m pip install "os-optics[protected]" "os-optics-kit[plot,render,media]"
os-optics catalog sync
```

| 扩展 | 用途 |
| --- | --- |
| `plot` | 所有图片（matplotlib） |
| `render` | 系统视图和动画，另需已安装的 Chrome 或 Chromium |
| `media` | MP4 动画；GIF 只需要 `plot` |

下面的示例使用目录中的透镜，因此需要 `os-optics catalog sync`。

## 准备一套演示材料

从已保存的结果开始：聚焦示例的一次运行，以及透镜位置的五个样本扫描。

```bash
os-optics project example --kind focus > project.json
os-optics project run project.json --out-dir nominal
```

`sweep.json`：

```json
{"base_project": "project.json", "max_evaluations": 5,
 "parameters": [{"name": "l1_shift", "unit": "mm", "values": [-1, -0.5, 0, 0.5, 1],
   "ops": [{"op": "transform", "selector": {"ids": ["L1"]}, "frame": {"component": "L1"},
            "translation_mm": [0, 0, "$value"]}]}]}
```

```bash
os-optics-kit sweep sweep.json --out study
os-optics-kit present init nominal study --out talk --title "Focusing lens"
os-optics-kit present build talk/presentation.json
```

`init` 写出 `talk/presentation.json`，其中包含数据源（路径相对于该文件）和一组默认素材。每个数据源以其目录名
命名；名为 `run` 的目录取上一级目录的名称，因此 `study/samples/s0003/run` 记为 `s0003`。

| 素材 | 类型 | 内容 |
| --- | --- | --- |
| `view-nominal` | view | 运行的三维系统视图，附交互式查看页面 |
| `power-nominal` | plot `power` | 各观测面接收的功率和功率账目中的去向 |
| `targets-nominal` | plot `targets` | 每个目标的取值、界限和判定 |
| `sweep-study-focus_position`、`sweep-study-spot_radius` | plot `sweep` | 每个研究目标一条曲线，标出每个样本的结果 |
| `system-and-result` | compose `system-plot` | 系统视图与第一条研究曲线并排 |

`build` 绘制所有素材，写出：

```text
talk/
  presentation.json        重绘配置
  assets.json              素材索引：每个素材的文件、尺寸、图注、数据源和状态
  index.html               离线图库
  contact-sheet.png        全部预览图，按素材标识标注
  previews/<id>.png        预览图，宽度不超过 640 像素
  assets/<id>/             原尺寸图片、SVG/PDF、data.json、caption.en.txt、
                           caption.zh-CN.txt、provenance.json
```

视图另有 `<id>.overlay.svg`（标题和图注为可编辑文字，尺寸与图片相同）、`<id>.annotated.png`（带标题的视图）
和 `camera.json`。标准输出列出每个素材的标识、状态、预览图和图片；`os-optics-kit present list talk` 可再次
输出索引。进度信息写到标准错误（`--quiet` 可关闭）。

在 `contact-sheet.png` 中按标识挑选素材；`assets.json` 给出每个标识对应的原尺寸图片、图注、数据和来源记录。
叙述内容请依据 `data.json` 中的数值和图注撰写。

## 不重新运行即可更改样式

```bash
os-optics-kit present build talk/presentation.json --out talk-zh \
    --set theme.language=zh-CN --set theme.background=light --set theme.font_scale=1.3
```

`--out` 把这一版本写到新目录，并附带它自己的 `presentation.json`，数据源路径改为相对于新目录。不加 `--out`
时，设置保存到 `talk/presentation.json`，并在原目录重绘。设置不变时再次构建会复用全部素材；只有定义、主题、
数据源或文件发生变化的素材才会重绘。

| 设置 | 取值 |
| --- | --- |
| `theme.preset` | `presentation`（默认）、`report` |
| `theme.language` | `en`、`zh-CN` |
| `theme.background` | `light`、`dark`、`transparent`、`#rrggbb` |
| `theme.font_scale` | 0.5 到 3 |
| `theme.size` | `slide`、`4k`、`half`、`square`、`report`、`thumbnail`、`WIDTHxHEIGHT` |
| `theme.title` | `true`、`false` |
| `theme.legend` | `auto`、`outside`、`none` |
| `theme.units` | 显示单位，例如 `{"mm": "um"}` |
| `theme.labels` | 标识的可读名称，含两种语言 |
| `assets.<id>.camera` | `[与光轴夹角, 仰角]`，单位为度，例如 `[30,15]` |
| `assets.<id>.caption`、`assets.<id>.title` | 文字或 `{"en": "...", "zh-CN": "..."}` |

`os-optics-kit present settings` 输出所有设置项、尺寸、素材类型和字段。`target:spot_radius` 这类标识在设置
名称之前按原样显示：

```bash
os-optics-kit present build talk-zh/presentation.json \
    --set 'theme.labels.target:spot_radius={"en": "Spot radius", "zh-CN": "光斑半径"}' \
    --set 'theme.labels.param:l1_shift={"en": "Lens shift", "zh-CN": "透镜位移"}'
```

含 JSON 的值也可以直接写入 `presentation.json`，免去命令行引号的处理。`--only ID,...` 只绘制所列素材及其
用到的素材，其余素材保留上一次的构建结果。

## 尺寸、格式与字体

| 尺寸 | 像素 |
| --- | --- |
| `slide` | 1920 × 1080 |
| `4k` | 3840 × 2160 |
| `half` | 960 × 1080 |
| `square` | 1080 × 1080 |
| `report` | 1600 × 1000 |
| `WIDTHxHEIGHT` | 按给定值 |

文字在任何尺寸下与图片保持相同比例：`4k` 图片就是更清晰的 `slide` 图片。单个素材可在其定义中用
`"theme": {"size": "half"}` 使用与整套材料不同的尺寸。

结果图输出 PNG 和 SVG（文字保留为文字），使用 `report` 预设时另有 PDF。三维视图是从引擎查看器截取的位图；
其标题和图注以可编辑的 SVG 文字保存在叠加文件中。

中文文字需要中日韩字体：Noto Sans CJK SC、Microsoft YaHei 或 PingFang SC。已安装的字体无法绘制某个标签时，
该素材报告 `FONT_MISSING` 并列出相关字符，而不会画出空白方框；请安装上述字体之一，或把 `theme.font_family`
设为已安装的字体。

## 比较多个方案

向 `init` 传入多个运行，并为每个运行指定标签：

```bash
os-optics-kit present init study/samples/s0001/run study/samples/s0003/run study/samples/s0005/run \
    --labels "Shift -1 mm,Nominal,Shift +1 mm" --out compare --title "Lens position"
os-optics-kit present build compare/presentation.json
```

有多个运行时，`init` 会加入：

- 每个运行一张视图，全部使用同一取景范围（`framing_sources`），系统以同一比例显示；
- `targets-compare`（图类型 `targets`）：每个目标在各运行中的取值，以及界限和判定；
- `views-compare`（类型 `compose`）：视图并排，两个运行时布局为 `before-after`，更多时为 `row`。

组合图按顺序列出各分图，每个分图可带标签：

```json
{"id": "views-compare", "kind": "compose", "layout": "row",
 "panels": [{"asset": "view-s0001"}, {"asset": "view-s0003", "label": {"en": "Nominal", "zh-CN": "标称"}},
            {"asset": "view-s0005"}]}
```

布局：`row`、`column`、`grid`、`before-after` 和 `system-plot`（系统视图与结果图并排，如上文的
`system-and-result`）。在 `presentation.json` 中调整顺序、标签或布局后重新构建即可，例如
`--set assets.views-compare.layout=column`。分图来自已构建的素材，不需要新的运行。

## 动画

`init --animate` 加入第一个运行的环绕动画（`orbit-nominal`）和第一个研究的样本回放（`playback-study`）：

```bash
os-optics-kit present init nominal study --out motion --title "Focusing lens" --animate
os-optics-kit present build motion/presentation.json
```

其他动画可添加到 `presentation.json` 的 `assets` 中：

| 模式 | 数据源 | 运动方式 |
| --- | --- | --- |
| `orbit` | 已保存的运行 | 相机以固定比例环绕系统；整圈环绕可无缝循环 |
| `transition` | 已保存的运行 | 相机从一个位姿移动到另一个位姿，例如从整个系统移到某个元件 |
| `playback` | 扫描研究 | 按扫描参数顺序逐个展示记录的样本 |

```json
{"id": "zoom-lens", "kind": "animation", "mode": "transition", "source": "nominal",
 "to": {"camera": [80, 10], "focus": "element:L1"}, "duration_s": 3}
```

样本回放展示每个样本已保存的运行和记录值：每一帧标出参数值、绘制量的取值和单位以及该样本的结果，并注明这些
帧是记录的样本。样本之间的数值从不插值，因此回放不是计算得到的时间演化；只有相机运动会插值。

每个动画写出 `<id>.mp4`、`<id>.gif`、一张代表帧 `<id>.png` 和 `sequence.json`（相机位姿、样本顺序和时长）；
样本回放另写出 `data.json`，记录所展示的数值。默认每秒 24 帧并输出两种格式；可用 `fps`、`duration_s`、
`hold_s` 和 `formats` 调整。MP4 需要 `media` 扩展，且宽和高为偶数；不安装该扩展时设置
`"formats": ["gif"]`。截取的视图会被缓存，只改动文字、语言或标签时重新编码即可，无需再次截取。

要用新主题重放一个序列，把它复制到 `presentation.json` 旁边，并让素材指向它：

```bash
cp motion/assets/orbit-nominal/sequence.json motion/orbit-sequence.json
os-optics-kit present build motion/presentation.json \
    --set assets.orbit-nominal.sequence=orbit-sequence.json --set theme.background=dark
```

## 图库与材料包

`index.html` 可在无网络连接时直接从文件夹打开：包括预览总览、每个素材的原尺寸视图及图注、它的文件（PNG、
SVG、PDF、MP4、GIF、数据、图注）、数据源和设置。同时有中英文图注时可切换语言。
`os-optics-kit present gallery talk` 重新生成 `index.html` 和 `contact-sheet.png`。

材料包是交给同事的可移植副本：

```bash
os-optics-kit present bundle talk --out share/focusing-lens.zip --only view-nominal,targets-nominal,system-and-result
os-optics-kit present bundle talk-zh --out share/focusing-lens-zh
```

目标为 `.zip` 时，压缩包内含一个以压缩包命名的文件夹；其他目标为目录。材料包包含所选素材（不加 `--only` 时
为全部已构建素材）的图片、矢量文件、媒体、数据、图注和来源记录，以及它们的预览图、筛选后的 `assets.json`、
`presentation.json`、`index.html` 和 `contact-sheet.png`。所有链接都是相对路径，接收者解压到任意位置即可打开
`index.html`。已保存的运行和研究、其他素材以及渲染缓存不会放入材料包；含本机绝对路径的文件会被拒绝
（`BUNDLE_PATH_LEAK`）。`presentation.json` 保留数据源的相对路径：接收者把材料包放在同一批运行旁边即可重新构建。

## 简洁版与技术版交付

同一批结果可以生成用于报告会的简洁版和用于技术记录的详细版：

```bash
os-optics-kit present build talk/presentation.json --out talk-report --set theme.preset=report
os-optics-kit report nominal --project project.json --plots talk-report/assets/targets-nominal --out report-nominal
```

| | `presentation` | `report` |
| --- | --- | --- |
| 尺寸 | `slide`（1920 × 1080） | `report`（1600 × 1000） |
| 基础字号 | 14 pt | 10 pt |
| 格式 | PNG、SVG | PNG、SVG、PDF |
| 图注 | 单独的 `caption.<lang>.txt` | 在图内，同时保存为文件 |
| 数据文件、引擎构建、目录版本 | 在 `provenance.json` 中 | 同时印在图内 |

`provenance.json` 为每个素材记录数据源标识（项目、输入和结果哈希、引擎构建、目录版本）、带 sha256 的数据文件
以及所用设置。`os-optics-kit report` 在所选图片之外加入计数、诊断信息和产物哈希。

## 退出码与处理方法

退出码 0：所有素材均已构建。退出码 2（`INCOMPLETE`）：部分素材无法绘制，`missing[]` 列出每个素材的代码和处理
步骤，其余素材照常构建并出现在图库中。退出码 4：命令被拒绝，标准错误中给出 JSON 错误。

| 代码 | 处理方法 |
| --- | --- |
| `SOURCE_MISSING` | 把 `sources.<id>.path` 指向运行目录（含 `manifest.json`）或研究目录（含 `summary.json`），路径相对于 `presentation.json` |
| `FONT_MISSING` | 安装 Noto Sans CJK SC、Microsoft YaHei 或 PingFang SC，或设置 `theme.font_family` |
| `BROWSER_NOT_FOUND` | 安装 Chrome 或 Chromium，或传入 `--browser PATH` |
| `MEDIA_EXTRA_MISSING` | `python -m pip install "os-optics-kit[media]"`，或设置 `"formats": ["gif"]` |
| `OUTPUT_EXISTS` | 换一个 `--out`；`bundle --overwrite` 可替换之前的材料包 |
| `OUTPUT_INVALID` | 在已保存的运行和研究之外、同一磁盘上构建 |

[工作流程步骤（英文）](../workflow-recipes.md#presentation-material)集中列出所有设置项和代码。
