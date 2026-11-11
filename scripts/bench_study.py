"""Benchmark of os-optics-kit study execution: one catalog-free study per worker count, each in a fresh process.

Usage:
  python scripts/bench_study.py [--out DIR] [--workers 1,2,4] [--samples 24] [--design expander|relay|both]
                                [--repeat 1] [--keep]

For every design and worker count it plans, executes and aggregates a sweep in a new study directory (a child
process: interpreter start, Engine import, plan, execution, aggregation) and writes DIR/bench.json and DIR/bench.md:
elapsed time, execution time, throughput (samples/s), each worker's start-up, each worker's first-result latency,
peak memory of the command plus its workers, the speed-up against one worker and whether every sample's outcome,
target values and hashes equal the one-worker run. Runs where parallelism did not pay are listed. The study
directories are removed unless --keep. The files hold no machine paths. Same as `os-optics-kit study bench`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=Path("run-bench-study"), help="output directory")
    parser.add_argument("--workers", default="1,2,4", help="comma-separated worker counts (default 1,2,4)")
    parser.add_argument("--samples", type=int, default=24, help="samples per study (default 24)")
    parser.add_argument("--design", choices=("expander", "relay", "both"), default="both")
    parser.add_argument("--repeat", type=int, default=1, help="runs per worker count; the median is reported")
    parser.add_argument("--keep", action="store_true", help="keep the study directories")
    args = parser.parse_args(argv)
    from os_optics_kit.study import bench

    document = bench.run_benchmark(args.out, workers=[int(w) for w in args.workers.split(",") if w.strip()],
                                   samples=args.samples, designs=args.design, repeat=args.repeat, keep=args.keep)
    print(json.dumps({"status": document["status"], "runs": [
        {k: r[k] for k in ("design", "workers", "elapsed_s", "execute_s", "throughput", "speedup",
                           "peak_memory_mib", "equal_to_one_worker")} for r in document["runs"]],
        "parallel_not_faster": document["parallel_not_faster"],
        "files": [str(args.out / "bench.json"), str(args.out / "bench.md")]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
