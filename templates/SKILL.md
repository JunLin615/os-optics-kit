---
name: {{name}}
description: {{description}}
version: 0.1.0
command: {{command}}
requires: {{requires}}
kind: {{kind}}
---

<!-- Skill page template. `os-optics-kit contrib tool` fills it from the helper's docstring and --help and drops
these comments; by hand, copy it to skills/<name>/SKILL.md, replace every {{...}} value, delete the comments, then
run `os-optics-kit skills index --write` and `os-optics-kit skills index --check`.
Front matter: flat `key: value` lines only. `name` equals the directory name (kebab-case, at most 40 characters).
`description`: what the command does and when to use it, at most 300 characters. `version` is the skill's own
MAJOR.MINOR.PATCH. `command` is the CLI entry (`os-optics-kit <command>`), or `none` for a guide. `requires` lists
what must be installed besides the kit, items separated by `; `, or `none`; an engine requirement equals the kit's
(os-optics>=0.12,<0.13). `kind` is `tool`, `contribution` or `guide`.
Budget: at most 900 tokens, estimated as ceil(characters / 4). Name no agent host or host protocol. -->

# {{name}}

## When to use

<!-- The situations where this command is the right step, as short bullets. -->
{{when_to_use}}

## When not to use

<!-- Neighbouring tasks and the command that handles each. -->
{{when_not_to_use}}

## Inputs

<!-- Positional arguments, options and input file formats, with units. -->
{{inputs}}

## Outputs

<!-- Files written, the stdout document and its key fields, and every exit code. -->
{{outputs}}

## Minimal example

<!-- A real command sequence that runs as written with the verified engine (and catalog, when needed). -->
```sh
{{example}}
```

## Limits

<!-- Model, numeric or input limits the user must know before relying on a result. -->
{{limits}}

## Version

0.1.0 (os-optics-kit {{kit_version}}): first version.
