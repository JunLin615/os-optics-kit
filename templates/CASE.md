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

## What it shows

{{what_it_shows}}

## Run

```bash
os-optics project check project.json
os-optics project run project.json --out-dir run
os-optics results targets run
```

## Targets

| Target | Metric | Plane | Bound | Verified |
| --- | --- | --- | --- | --- |
{{targets_table}}

## Baseline

os-optics {{engine}}, catalog {{catalog}}: run status {{status}}. `os-optics-kit cases verify {{name}}` repeats the
run and compares it with expected.json.

## Files

- `project.json`: the Project with its targets.
- `expected.json`: the verified run status and target assessments.
- `README.md`, `images/`: the illustrated page.
{{extra_files}}
