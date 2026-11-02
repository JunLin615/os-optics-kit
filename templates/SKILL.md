---
name: my-tool
description: One or two sentences - what the command does and when to use it (at most 300 characters).
version: 0.1.0
command: os-optics-kit my-tool
requires: os-optics>=0.12,<0.13
kind: tool
---

# my-tool

Copy this file to `skills/<name>/SKILL.md`, replace every placeholder, then run
`os-optics-kit skills index --write` and `os-optics-kit skills index --check`.

Front matter: flat `key: value` lines only. `name` equals the directory name (kebab-case, at most 40
characters). `version` is the skill's own MAJOR.MINOR.PATCH. `command` is the CLI entry, or `none` for a
guide. `requires` lists what must be installed besides the kit, items separated by `; `, or `none`;
an engine requirement equals the kit's. `kind` is `tool`, `contribution` or `guide`.
Budget: at most 900 tokens, estimated as ceil(characters / 4). Name no agent host or host protocol.

## When to use

- The situations where this command is the right step, as short bullets.

## When not to use

- Neighbouring tasks and the command that handles each.

## Inputs

- Positional arguments, options and input file formats, with units.

## Outputs

- Files written, the stdout document and its key fields, and every exit code.

## Minimal example

```
os-optics-kit my-tool INPUT --out OUT
```

A real command sequence that runs as written with the verified engine (and catalog, when needed).

## Limits

- Model, numeric or input limits the user must know before relying on a result.

## Version

0.1.0 (os-optics-kit 0.1.0), verified with os-optics 0.12.0.
