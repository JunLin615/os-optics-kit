# {{title}}

{{language_links}}

<!-- 案例中文说明页模板（cases/<name>/README.zh-CN.md；英文页模板 README.case.md 使用相同的占位符）。
`os-optics-kit contrib case` 填写本模板并删除这些注释；手工编写时，替换每个 {{...}} 并删除注释。
{{language_links}} 是标题下的语言行，此处为 `[English](README.md) · 简体中文`：README.md 与 README.zh-CN.md
互相链接。{{images}} 按 images.json 的顺序逐一展示其中记录的图片，第一张是系统视图。系统视图由引擎生成，无需浏览器和
`render` 扩展：`os-optics view RUN --no-browser --snapshot DIR --views iso`（三维）和
`--section ALL --frame=scene`（透镜剖面）；在套件仓库中，`os-optics-kit cases images NAME` 生成并记录这些视图
（不带视图选项时，重新生成 images.json 中已记录的视图）。结果图由案例自己的脚本绘制。每个案例最多 5 张 PNG 图片，
每张不超过 400 KiB；结果表格写成 Markdown 表格，不用图片。 -->

{{summary}}

{{images}}

## 运行

```bash
os-optics-kit cases copy {{name}} --dest .
cd {{name}}
os-optics project run project.json --out-dir run
os-optics results targets run
os-optics view run
```

## 结果

| 目标 | 数值 | 界限 | 判定 |
| --- | --- | --- | --- |
{{results_rows}}

已用 os-optics {{engine}}（{{catalog_text}}）验证；运行状态 {{status}}。
