# {{title}}

{{language_links}}

<!-- Case README template (the human page of cases/<name>/; README.case.zh-CN.md is the Chinese page with the same
placeholders). `os-optics-kit contrib case` fills it and drops these comments; by hand, replace every {{...}} value
and delete the comments. {{language_links}} is the language line under the title, here
`English · [简体中文](README.zh-CN.md)`: README.md and README.zh-CN.md link each other. {{images}} shows exactly
the images recorded in images.json, in that order, a system view first. System views come from the engine without
a browser or the `render` extra: `os-optics view RUN --no-browser --snapshot DIR --views iso` (3D) and
`--section ALL --frame=scene` (lens section); in a kit checkout `os-optics-kit cases images NAME` renders and
records them (bare, it re-renders the views already recorded in images.json). Figures are drawn by the case's own
scripts. At most 5 PNG images of at most 400 KiB each; result tables are Markdown tables, never images. -->

{{summary}}

{{images}}

## Run it

```bash
os-optics-kit cases copy {{name}} --dest .
cd {{name}}
os-optics project run project.json --out-dir run
os-optics results targets run
os-optics view run
```

## Results

| Target | Value | Bound | Assessment |
| --- | --- | --- | --- |
{{results_rows}}

Verified with os-optics {{engine}} ({{catalog_text}}); run status {{status}}.
