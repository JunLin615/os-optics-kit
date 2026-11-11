---
name: {{name}}
title: {{title}}
summary: {{summary}}
class: {{class}}
domain: {{domain}}
provenance: {{provenance}}
publication: {{publication}}
source: {{source}}
engine: {{engine}}
catalog: {{catalog}}
project: project.json
tools: {{tools}}
tags: {{tags}}
license: MIT
---

<!-- Case page template. `os-optics-kit contrib case` fills it from the verified run and drops these comments;
by hand, replace every {{...}} value and delete the comments. Front matter: flat `key: value` lines in this order;
`name` equals the directory name; `summary` at most 300 characters; `class` lens, free-space or integrated-path;
`domain` 1-3 values, primary first; `provenance` and `publication` say where the design comes from and on what
basis it is published (self-built goes with own-work); `source` cites the third-party origin (at most 200
characters; delete the line for self-built work); `engine` the verified os-optics version; `catalog` the release the
components pin, or `none`; `tools` comma-separated kit commands, or `none`; `tags` 1-8 values. Values:
`os-optics-kit cases vocabulary`. Keep the five sections in this order; the whole page stays within 700 tokens,
estimated as ceil(characters / 4). -->

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
- `README.md`, `README.zh-CN.md`, `images.json`, `images/`: the illustrated pages and their image record.
{{extra_files}}
