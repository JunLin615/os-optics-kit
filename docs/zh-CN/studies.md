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

`--workers 4` 一次启动四个工作进程，每个进程只导入一次引擎，随后持续评估样本直到研究完成。结果与串行
运行相同：`table.csv` 和 `summary.json` 按规划顺序列出样本，数值和结果哈希一致；只有 `seconds`、每条
记录的 `timing`、运行它的工作进程以及 `study.json` 中的执行信息不同。`--workers auto` 在规划较小时保持
串行，规划足够大、能抵消启动开销时才使用工作进程。

每个工作进程只使用一个数值库线程（`OPENBLAS_NUM_THREADS`、`OMP_NUM_THREADS`、`MKL_NUM_THREADS`），使
工作进程数 x 线程数不超过逻辑 CPU 数；超过时请求被拒绝。`--time-limit-s` 和 `--max-memory-mib` 限制每次
评估：引擎的运行监视器在检查点（每次阶段切换以及长阶段内部）读取它们并在该处停止运行；超过时间限制
五秒仍未返回的工作进程会被终止。串行调度器没有其他抢占方式。

## 中断、查看与续跑

```bash
os-optics-kit sweep sweep.json --base project.json --out study --plan-only
os-optics-kit study run study --workers 2      # 中途 Ctrl-C：退出码 130，未完成的样本记为 NOT_RUN
os-optics-kit study status study               # 状态、执行信息、最近的日志事件
os-optics-kit study run study --resume         # 只运行没有完成记录的样本
```

每条样本记录在需求结果之外还有一个作业状态 `state`：`COMPLETED`（引擎给出结果或拒绝了该设计）、
`REFUSED`（规划时无法构建该样本）、`FAILED`、`CANCELLED`（被限制或取消停止）或 `NOT_RUN`。续跑时保留
请求未变且已完成的记录，读回已完成但缺少记录的运行目录，其余样本重新运行；`journal.jsonl` 记录哪些样本
被复用、哪些重新运行。请求由其 Project、所选目标、请求的输出和种子的 SHA-256 标识：用 `--resume` 和修改
后的规格重新运行工具时，只运行请求发生变化的样本；公差研究在种子不变时增大 `--samples`，已运行的样本
保留。用其他引擎构建或目录版本规划的研究不会续跑（`STUDY_IDENTITY_CHANGED` 指出变化的字段）。

## 候选设计与外部搜索代码

```bash
os-optics-kit study candidates a.json b.json c.json --out cand --targets spot_radius --workers 2
os-optics-kit compare cand
```

每个 Project 文件是一个样本（`param:candidate` 为其序号，标签 `candidate` 为文件名）。优化器保留自己的
顺序步骤，把每一批相互独立的评估交给执行器：

```python
from os_optics_kit.study import collect_records, execute_study, make_request, plan_study, study_header

requests = [make_request(f"s{i:04d}", document, selected_targets=["spot_radius"])
            for i, document in enumerate(batch, start=1)]
plan_study("batch-01", study_header("my-search.batch"), requests)
execute_study("batch-01", workers=4)
values = [record["targets"] for _entry, record in collect_records("batch-01")]
```

评估器（每个请求一次引擎运行）和调度器都是 `execute_study` 的参数；调用方可以传入自己的函数
`f(request, sample_dir, limits) -> record`。[interop-optimization 案例](../../cases/interop-optimization)
就以这种方式运行其 640 个 Sobol 样本。

## 何时值得并行

工作进程只付出一次启动开销（创建进程并导入引擎），之后以串行速度运行样本。下表是在 12 个逻辑 CPU 的
Windows 台式机上用 os-optics 0.13.0 运行 `python scripts/bench_study.py --workers 1,2,4 --samples 48` 的结果：

| 设计（48 个样本） | 工作进程 | 命令总时间 s | 执行 s | 样本/s | 加速比 | 工作进程启动 s | 峰值内存 MiB | 结果相同 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 扩束器，3 个薄透镜（0.21 s/样本） | 1 | 14.5 | 10.2 | 4.7 | 1.00 | - | 158 | 是 |
| | 2 | 13.6 | 9.1 | 5.3 | 1.12 | 3.5 | 446 | 是 |
| | 4 | 12.1 | 7.9 | 6.1 | 1.30 | 3.8 | 733 | 是 |
| 中继系统，12 个薄透镜、13 个平面（0.43 s/样本） | 1 | 25.3 | 20.7 | 2.3 | 1.00 | - | 167 | 是 |
| | 2 | 20.1 | 15.6 | 3.1 | 1.33 | 3.4 | 465 | 是 |
| | 4 | 15.9 | 11.4 | 4.2 | 1.81 | 3.8 | 769 | 是 |

"命令总时间"包括启动 Python 并在命令自身进程中导入引擎（约 4 s）；加速比按执行时间计算。每个工作进程约增加
3.5 s 启动时间和 150 MiB 内存。几十个亚秒级样本的研究收益有限；样本越重、研究越大，收益越明显。各工作
进程数下的结果完全相同。

先运行一个小规模试验（对两个设计运行 `study candidates`，查看每条记录的 `timing.run_s` 和
`timing.phase_seconds`），再决定工作进程数；`os-optics-kit study bench` 可在当前机器上测量。
