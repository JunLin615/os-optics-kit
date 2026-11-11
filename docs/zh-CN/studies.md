# 研究：规划、执行、汇总、展示

[os-optics-kit](../../README.zh-CN.md) · [English](../en/studies.md) · 简体中文

参数扫描、灵敏度或公差研究以及一组候选设计，都由许多相互独立的引擎评估组成。套件把它们分成四步，
每一步都读写同一个研究目录：

| 步骤 | 写出 | 命令 |
| --- | --- | --- |
| 规划 | `study.json`、`plan.jsonl`、`requests/<id>.json`（运行前写好每个样本的 Project） | `sweep` / `sensitivity` / `tolerance` 加 `--plan-only`，`study candidates ... --plan-only` |
| 执行 | `samples/<id>/{project.json, run/, sample.json}`、`journal.jsonl` | `study run STUDY [--workers N]` |
| 汇总 | `table.csv`、`summary.json`（以及工具自己的分析、图和报告） | `study aggregate STUDY` |
| 展示 | 结果图、报告、比较和演示材料 | `plot`、`report`、`compare`、`present` |

不加 `--plan-only` 时，工具通过同样的函数一次完成全部步骤。任何一步都可以从上一步的文件开始：执行先前
写好的规划、汇总已保存的样本、展示已保存的研究。执行之后的步骤都不运行引擎。

## 用多个工作进程运行研究

```bash
os-optics project example --kind focus > project.json
os-optics-kit sweep sweep.json --base project.json --out study --workers 4
```

`sweep.json` 是针对该 Project 的扫描规格：`os-optics-kit skills show sweep` 给出一个示例，
[扫描透镜间距](workflows.md#2-扫描透镜间距)说明其中的键。

`--workers 4` 一次启动四个工作进程，每个进程只导入一次引擎，随后持续评估样本直到研究完成。结果与串行
运行相同：`table.csv` 和 `summary.json` 按规划顺序列出样本，数值和结果哈希一致；只有 `seconds`、每条
记录的 `timing`、运行它的工作进程以及 `study.json` 中的执行信息不同。`--workers auto` 根据待运行的样本数
及其运行时间估算总耗时：加 `--resume` 时取本研究已完成样本 `timing.run_s` 的中位数，否则按每个样本 0.2 s
计。只有当工作进程（每个启动约 4 s）至少节省 10 % 时才并行，最多 4 个；因此 31 个及以下样本的新规划串行
运行。试验显示样本更耗时时，请自己指定工作进程数。

每个工作进程只使用一个数值库线程（`OPENBLAS_NUM_THREADS`、`OMP_NUM_THREADS`、`MKL_NUM_THREADS`），使
工作进程数 x 线程数不超过逻辑 CPU 数；超过时请求被拒绝。工作进程启动之前，命令先取得每个 Project 指定的
目录版本，因此能串行运行的研究也能用工作进程运行。`--time-limit-s` 和 `--max-memory-mib` 限制每次评估：
引擎的运行监视器在检查点（每次阶段切换以及长阶段内部）读取它们并在该处停止运行；超过时间限制五秒仍未返回
的工作进程会被终止。两种情况下样本都记为 `CANCELLED`，错误代码为 `RUN_STOPPED`，原因（`TIME_LIMIT`、
`MEMORY_LIMIT`）作为问题代码。串行调度器没有其他抢占方式。

## 中断、查看与续跑

```bash
os-optics-kit sweep sweep.json --base project.json --out study --plan-only
os-optics-kit study run study --workers 2      # 中途 Ctrl-C：退出码 130，正在运行的样本记为 CANCELLED，其余为 NOT_RUN
os-optics-kit study status study               # 状态、执行信息、最近的日志事件
os-optics-kit study run study --resume         # 只运行没有完成记录的样本
```

无论有几个工作进程，Ctrl-C 都会停止整个研究：正在运行的样本被取消，不再启动其他样本，`study.json` 的
`execution.state` 变为 `CANCELLED`，命令以退出码 130 结束，并给出可续跑的 `next` 命令。同一时间只有一次
执行占用一个研究目录（`owner.json`，执行结束时删除）：只要前一次执行的进程仍在运行，对该目录再执行
`study run` 或规划命令都会以 `STUDY_RUNNING` 被拒绝；`study status` 也只在该进程运行期间报告 `RUNNING`
（进程未完成就结束时报告 `INTERRUPTED`；续跑会接管该研究）。

每条样本记录在结果类别（`outcome`）之外还有一个作业状态 `state`：`COMPLETED`（引擎给出结果或拒绝了该
设计）、`REFUSED`（规划时无法构建该样本）、`FAILED`、`CANCELLED`（被限制或取消停止）或 `NOT_RUN`，并记录
产生它的评估器 `evaluator`。续跑时保留请求未变、已完成且由同一评估器产生的记录，读回已完成但缺少记录的
运行目录，并运行其余样本；`journal.jsonl` 记录哪些样本被复用、哪些实际运行。请求由其 Project、所选目标、
请求的输出和种子的 SHA-256 标识：用 `--resume` 和修改后的规格重新运行工具时，只运行请求发生变化的样本
（保留的记录采用新规划的标签）；公差研究在种子不变时增大 `--samples`，已运行的样本保留。用其他引擎构建
或目录版本规划的研究，`study run` 无论是否加 `--resume` 都会拒绝（`STUDY_IDENTITY_CHANGED` 指出变化的
字段）：用其规划命令加 `--overwrite` 重新规划。

## 候选设计与外部搜索代码

```bash
os-optics-kit study candidates a.json b.json c.json --out cand --targets spot_radius --workers 2
os-optics-kit compare cand
```

每个 Project 文件是一个样本（`param:candidate` 为其序号，标签 `candidate` 为不含 `.json` 的文件名）。
优化器保留自己的顺序步骤，把每一批相互独立的评估交给执行器。下面的脚本用两个工作进程评估 Project
`a.json` 和 `b.json`，并打印每个样本的结果类别和目标值：

```python
import json
from pathlib import Path

from os_optics_kit.study import collect_records, execute_study, make_request, plan_study, study_header


def evaluate(documents, study_dir, workers=2):
    requests = [make_request(f"s{i:04d}", document, selected_targets=["spot_radius"])
                for i, document in enumerate(documents, start=1)]
    plan_study(study_dir, study_header("my-search.batch"), requests)
    execute_study(study_dir, workers=workers)
    return collect_records(study_dir)  # [(plan entry, record)] in plan order


if __name__ == "__main__":
    batch = [json.loads(Path(name).read_text(encoding="utf-8")) for name in ("a.json", "b.json")]
    for entry, record in evaluate(batch, "batch-01"):
        print(entry["sample_id"], record["outcome"], {t["id"]: t["value"] for t in record["targets"]})
```

使用多个工作进程时，执行器启动的工作进程会导入调用它的脚本：请把调用放在 `if __name__ == "__main__":`
之下。每一批使用新的研究目录：对样本已经运行过的目录，`execute_study` 除非以 `resume=True` 调用，否则会
拒绝（`STUDY_STARTED`）。评估器（每个请求一次引擎运行）和调度器都是 `execute_study` 的参数；调用方可以
传入自己的函数 `f(request, sample_dir, limits) -> record`。

## 何时值得并行

工作进程只付出一次启动开销（创建进程并导入引擎）。下表是在有 12 个逻辑 CPU 的 Windows 台式机上用
os-optics 0.13.0 运行 `python scripts/bench_study.py --workers 1,2,4 --samples 48` 的结果，每个工作进程数
各运行一次：

| 设计（48 个样本） | 工作进程 | 命令总时间 s | 执行 s | 样本/s | 加速比 | 工作进程启动 s | 峰值内存 MiB | 结果相同 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 扩束器，3 个薄透镜（0.20 s/样本） | 1 | 13.63 | 9.56 | 5.02 | 1.0 | - | 157 | 是 |
| | 2 | 12.58 | 8.59 | 5.59 | 1.11 | 3.19-3.21 | 443 | 是 |
| | 4 | 11.12 | 7.16 | 6.71 | 1.34 | 3.41-3.52 | 731 | 是 |
| 中继系统，12 个薄透镜、13 个平面（0.43 s/样本） | 1 | 24.83 | 20.45 | 2.35 | 1.0 | - | 168 | 是 |
| | 2 | 19.43 | 15.05 | 3.19 | 1.36 | 3.31-3.40 | 465 | 是 |
| | 4 | 15.15 | 10.89 | 4.41 | 1.88 | 3.46-3.57 | 768 | 是 |

“命令总时间”包括启动 Python 并在命令自身进程中导入引擎（约 4 s）；加速比按执行时间计算。每个工作进程
增加 3.2-3.6 s 启动时间和 143-150 MiB 内存。启动之后，两个工作进程时每个进程的单样本速度约为串行的 90 %，
四个时约为三分之二。几十个亚秒级样本的研究收益有限；样本越重、研究越大，收益越明显。各工作进程数下的
结果完全相同。

先运行一个小规模试验（对两个设计运行 `study candidates`，查看每条记录的 `timing.run_s` 和
`timing.phase_seconds`），再决定工作进程数；`os-optics-kit study bench` 可在当前机器上测量（工作进程数
1、2、4，不超过其逻辑 CPU 数）。
