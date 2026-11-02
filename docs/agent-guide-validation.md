# Agent-guide validation

Evidence for [issue #3](https://github.com/JunLin615/opensci-engine/issues/3),
following standing rules [#1](https://github.com/JunLin615/opensci-engine/issues/1)
and [#2](https://github.com/JunLin615/opensci-engine/issues/2).

## 0.8.0 baseline (2026-10-18)

The entry now routes to the workflow scripts (editing by ID, sweeps, saved-run comparison,
plots, reports, catalog candidates, sensitivity, tolerance and three design workflows) and
points to [workflow recipes](workflow-recipes.md) for their specifications. Sizes by the
tokenizer method below:

| File | Tokens | UTF-8 bytes | SHA-256 |
| --- | ---: | ---: | --- |
| `AGENT_GUIDE.md` | 2,269 | 9,182 | `4755192b4e453b07a5cfb51639cc2e6835ec8dbcae55e9c3dc6ee43fa951f1b4` |
| `docs/agent-recipes.md` | 1,284 | 5,614 | `a04688548a824551b5664c1d0c9ccbad071c3e7a9784c0233b8e4fe6a44f6764` |
| `docs/workflow-recipes.md` | 5,038 | 18,453 | `8d76cf3233955c10e24e83976199dbeee99055227a692a332d47e27e9eb4848d` |

The trials below used this revision. The current revision, after the 2026-10-19 corrections
(complete input comparison, spec column checks, outcome rule for issues with status
`OUT_OF_MODEL_SCOPE`) and the 2026-10-21 wording corrections, measures:

| File | Tokens | UTF-8 bytes | SHA-256 |
| --- | ---: | ---: | --- |
| `AGENT_GUIDE.md` | 2,336 | 9,417 | `6d49d53a08c10e5ba5ed0d44d3bf01b1a7cce7f93cda91f2617370e9d9bbe222` |
| `docs/agent-recipes.md` | 1,284 | 5,614 | `a04688548a824551b5664c1d0c9ccbad071c3e7a9784c0233b8e4fe6a44f6764` |
| `docs/workflow-recipes.md` | 5,731 | 21,210 | `04517aa8bae405a54fd4ab190915d6e3cb19f250d488969ff098fdf1f97ca3bd` |

Environment: Windows 10 (AMD64), CPython 3.11.16, published PyPI `opensci-engine[protected]==0.8.0`
(build `0.8.0+94f6dc70bb40a2ed`), matplotlib 3.10.7, catalog release `2026-10-15.1` imported from
its release file. Every task-table invocation without a placeholder was run once, in table order,
from a clean working tree:

| Invocation | Exit | Status | Stdout bytes |
| --- | ---: | --- | ---: |
| `python -m opensci_engine doctor` | 0 | `report` | 4,087 |
| `python examples/ideal_lens_focus.py --out run-ideal-focus` | 0 | `PASS` | 1,189 |
| `python examples/beam_expander.py --out run-example` | 0 | `PASS` | 2,616 |
| `python examples/result_summary.py run-example` | 0 | `PASS` | 2,562 |
| `python examples/result_summary.py run-example --details` | 0 | `PASS` | 7,003 |
| `python examples/edit_project.py run-example/project.json --describe` | 0 | `DESCRIBED` | 686 |
| `python examples/edit_project.py run-example/project.json --describe --ids L2` | 0 | `DESCRIBED` | 1,760 |
| `python examples/catalog_candidates.py --category LENS --wavelength-nm 780 --range focal_length_mm=95..105 --near focal_length_mm=100 --limit 5` | 0 | `COMPLETED` | 2,130 |
| `python examples/sensitivity_analysis.py examples/specs/sensitivity_expander.json --out run-sensitivity` | 0 | `COMPLETED` | 3,391 |
| `python examples/tolerance_study.py examples/specs/tolerance_expander.json --out run-tolerance` | 0 | `COMPLETED` | 3,285 |
| `python examples/beam_expansion_focus.py --out run-expansion` | 0 | `PASS` | 3,869 |
| `python examples/polarization_control.py --out run-polarization` | 0 | `PASS` | 2,934 |
| `python examples/alignment_analysis.py --out run-alignment` | 0 | `PASS` | 4,802 |
| `python examples/analyze_measurements.py examples/data/measurements/detector_response.csv --x input_power_mW --signal signal_V --background dark_V --x-unit mW --signal-unit V --out run-detector` | 0 | `WARNING` | 1,871 |

The placeholder rows (edit, sweep, compare, plot, report) were run with concrete files through
the sequences in [workflow recipes](workflow-recipes.md) and the
[design-study walkthrough](en/workflows.md); their results are recorded in
[workflow validation](workflow-validation.md) and
[sensitivity and tolerance validation](sensitivity-tolerance-validation.md). The [editing recipe](agent-recipes.md)
reproduces 1.1148985411200965 mm (`FAIL`, exit 1) on catalog `2026-10-15.1`, and the ideal
focus with `--maximum-radius-mm 0.01` gives 0.016934085944977664 mm (`FAIL`, exit 1).

## Paired agent trials (2026-10-18)

Nine customer tasks were each given to fresh agents twice with this revision's material and
twice with the previous revision (the 0.8.0-aligned guide and result summary, without the
workflow scripts): an edit with an evaluation of every target, a 19-point waveplate scan, a
read of one saved result, a wavelength outside a waveplate's band, the currency of a saved
PASS after an edit, a mirror tilt that an earlier attempt may already have applied (two
variants), a two-lens move, and a tolerance study started from a local text copy of the
Portal's Agent guidance: for the previous revision the v0.9.3 text, which does not name this
repository, without a checkout of it; for this revision the v0.9.4 text, which links this
repository, with a checkout of it. Starting projects used parameters that appear in no example. The agents used
the same model, prompt, PyPI 0.8.0 environment and catalog `2026-10-15.1`; their answers
were graded against values computed directly with the engine.

| Measure (18 trials per revision) | Previous revision | This revision |
| --- | ---: | ---: |
| Answers agreeing with the reference in every field (strict grading) | 15 | 16 |
| Out-of-band waveplate run identified as out of model scope | 0 of 2 | 2 of 2 |
| Tolerance study found the public workflow from the Portal's Agent guidance | 0 of 2 | 2 of 2 |
| Agent tokens, total | 1,573,437 | 1,555,980 |
| Tool calls, total | 216 | 208 |

In both revisions the out-of-band waveplate task failed strict grading on its
`requirements_met` field: the answers said the requirements were not met, while the reference
for an out-of-model run is "not determined". Scientific values agreed in every graded field of
the other tasks in both revisions, and every retry task applied the tilt exactly once. The
token totals include each agent's fixed context and differ by about 1 %; these tasks show no
token saving. The gains are in the out-of-scope classification, the saved and reproducible
studies, and discovery. Durations are not compared because up to eight trials shared one host.

The sections that follow are the 2026-10-13 record for engine 0.6.1 and its entry revision.

## Entry size

On 2026-10-13, the complete UTF-8 Markdown of `AGENT_GUIDE.md` contained **1,289
tokens** and **5,755 bytes**. The optional `docs/agent-recipes.md` contained 1,098
tokens and 4,728 bytes. Counts use `tiktoken==0.14.0`, `o200k_base`, including
headings, links, code and table markup. This is a reproducible document-size
measure, not a claim about a particular agent's billed or internal tokens.

To measure a revision from the repository root (the tokenizer is a maintainer
measurement tool, not an example dependency):

```bash
python -m pip install "tiktoken==0.14.0"
python -c "from pathlib import Path; import tiktoken; p=Path('AGENT_GUIDE.md'); print(len(tiktoken.get_encoding('o200k_base').encode(p.read_text(encoding='utf-8'))))"
```

The measured document SHA-256 values were:

| File | SHA-256 |
| --- | --- |
| `AGENT_GUIDE.md` | `10216381aa5065575ed63af9dbd677527e24a369926743effab690a9431efebb` |
| `docs/agent-recipes.md` | `54c7844d23666812053ae30ce8322fd4f22884ebe6d0f4f5b6eeb480fc7ab7bc` |

## Command and recipe checks

Environment: Windows, Python 3.13.5, published PyPI `opensci-engine==0.6.1`
(build `0.6.1+e5785dea69453e1e`), catalog `2026-10-06.3`. The package was installed
in a virtual environment; no private source-tree imports were used. The baseline
remains consistent with both READMEs and the [results validation](result-summary-validation.md).

| Check | Observed result |
| --- | --- |
| Version discovery, `doctor`, engine `--help` | Exit 0; installed version 0.6.1 |
| Catalog list filtered to lenses at 780 nm, limit 5 | Exit 0 |
| Default ideal-lens run | `PASS`, exit 0 |
| Ideal maximum radius 0.02 mm | `PASS`, exit 0 |
| Ideal maximum radius 0.001 mm | `FAIL`, exit 1 |
| Catalog beam-expander baseline | `PASS`, exit 0 |
| Native saved summary, with and without `--details` | Parseable JSON with full artifact references |
| Complete editing recipe from the Markdown | `FAIL`, exit 1; waist radius 1.1148985411200965 mm outside 1.8–2.2 mm |
| Reading that revised run through the summary script | Same result, exit 1 |
| Recipe variant with radius interval 0–0.001 mm | `FAIL`, exit 1 |

The complete recipe's Python block was extracted and executed as written. The
strict variant changed only its interval and output directory. SHA-256 checks
confirmed the original native run files were unchanged. Failure here means the
computed design missed its target; it does not mean the recipe failed to execute.

Both READMEs link to the same English entry. Local Markdown links were checked,
and the three issue-template YAML files parsed successfully. English template
defaults retain links to both human documentation languages and permit feedback
in the reporter's preferred language.

## Representative task trial

The trial used a fresh agent without the implementation conversation. It received
the repository path, the already prepared Python environment, and a customer
task: create a beam-expander baseline, change the wavelength to 785 nm, shift L2
and the output plane by +1 mm in design X, set the waist-radius interval to
1.0–1.2 mm, and assess the result. It was directed to start with the agent guide
and read only relevant linked content, without changing tracked files.

The task completed successfully: the adapted waist radius was
**1.1148985411200965 mm**, `PASS` against **1.0–1.2 mm**. Saved inputs confirmed
785 nm, L2 X changing from 152.841 to 153.841 mm, and observation X changing from
252.841 to 253.841 mm. Both runs had `PASS` overall/engine/validation status,
empty issue/model-scope lists, and consistent verified artifacts. A separate
saved-summary invocation reproduced the adapted result. No blocking ambiguity
was reported.

Full files read into the trial agent's context:

| File | UTF-8 bytes |
| --- | ---: |
| `AGENT_GUIDE.md` | 5,755 |
| `docs/agent-recipes.md` | 4,728 |
| `run-agent-trial-baseline/project.json` | 3,353 |
| `run-agent-trial-adapted/project.json` | 3,326 |
| Total | 17,162 |

Documentation reading totaled **10,483 bytes / 2,387 tokens** by the tokenizer
method above; generated input artifacts accounted for another 6,679 bytes. No
implementation source or other validation report was read. Runtime imports are
excluded from model-visible reading volume; these are not total conversation
token counts.

The agent recorded **6 orchestration tool calls**, containing **7 shell-tool
calls and 1 file-edit call**. The seven shell invocations launched four Python
processes: version discovery, baseline simulation, adapted simulation, and saved
summary inspection. Other shell work read the four files with byte counts. There
were **2 simulation attempts, 0 retries**, and all shell/Python invocations exited
0. No separate measurement-accounting commands were needed; byte counts were
collected with the task's file reads.

The generated recipe applied the documented edits with the task's requested
interval and separate output paths. Local evidence was retained in
`run-agent-trial-baseline/`, `run-agent-trial-adapted/`, and the baseline arrivals
CSV. These generated artifacts are not committed; reproduce the task from the
linked recipe and substitutions above.

The trial tests routing and recipe use in a prepared environment. It is a
document-assisted task variant, not an independent benchmark or a comparison
against the old documentation. Installation effort is outside its measured task
window. No token-saving percentage or general performance claim is inferred.
