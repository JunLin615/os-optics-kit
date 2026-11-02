---
name: {{name}}
title: {{title}}
summary: {{summary}}
engine: {{engine}}
catalog: {{catalog}}
project: project.json
tools: {{tools}}
tags: {{tags}}
license: MIT
---

<!-- Case page template. `os-optics-kit contrib case` fills it from the verified run and drops these comments;
by hand, replace every {{...}} value and delete the comments. Front matter: flat `key: value` lines; `name` equals
the directory name; `summary` at most 300 characters; `engine` the verified os-optics version; `catalog` the release
the components pin, or `none`; `tools` and `tags` comma-separated, or `none`. Keep the five sections in this order;
the whole page stays within 700 tokens, estimated as ceil(characters / 4). -->

## What it shows

<!-- What the design is, what its targets ask for and what the verified run shows, in a few sentences. -->
{{what_it_shows}}

## Run

<!-- Add the case's own commands (studies, scripts, kit tools) after the engine run. -->
```bash
os-optics project check project.json
os-optics project run project.json --out-dir run
os-optics results targets run
```

## Targets

<!-- One row per target of project.json, from `os-optics results targets run`. -->
| Target | Metric | Plane | Bound | Verified |
| --- | --- | --- | --- | --- |
{{targets_rows}}

## Baseline

os-optics {{engine}}, {{catalog_text}}: run status {{status}} (`expected.json`).
`os-optics-kit cases verify {{name}}` repeats the run and compares it.

## Files

<!-- List every other file of the case (studies, scripts, data) with one line on what it is for. -->
- `project.json`, `expected.json`: the design and its verified outcome.
- `README.md`, `images/`: the illustrated page.
{{extra_files}}
