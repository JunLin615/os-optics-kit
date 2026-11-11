"""Benchmark of study execution: one catalog-free sweep run with several worker counts, each in a fresh process.

``run_benchmark(out_dir, workers=None, samples=24, designs="both")`` runs, for every design and worker count (default
1, 2 and 4 up to the logical CPUs),
``os-optics-kit sweep``'s Python entry in a child process (``python -m os_optics_kit.study.bench --child ...``) on a
new study directory and reports per run:

* ``elapsed_s``: the child's whole run (interpreter start, Engine import, planning, execution, aggregation);
* ``execute_s`` and ``throughput`` (samples per second of the execution step);
* ``startup_s``: each worker's start-up (spawn + Engine import), ``first_result_s``: per worker, the time from the
  start of the execution to its first finished sample;
* ``peak_memory_mib``: the child's peak plus every worker's peak (read during its runs);
* ``equal_to_one_worker``: whether every sample's outcome, target values and result / input / Project hashes equal
  those of the one-worker run, and ``speedup`` against it (below 1: parallelism did not pay).

Designs: ``expander`` (three ideal thin lenses, two planes; the sweep moves L2) and ``relay`` (twelve ideal thin
lenses in 4f pairs with thirteen planes; the sweep moves the first lens), both catalog-free. Results go to
``bench.json`` and ``bench.md`` (no machine paths); the study directories are removed unless ``keep``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

from .. import workflow_common as wc
from .._version import __version__

DESIGNS = ("expander", "relay")
#: Worker counts measured by default (those above the logical CPUs are skipped).
DEFAULT_WORKERS = (1, 2, 4)
MIB = 1024 * 1024


# --------------------------------------------------------------------------------------------- designs

def expander_documents(samples):
    """Expander + focus with L2 moved by a gap error from -2 to +2 mm."""
    from .. import optical_designs as od

    values = _values(samples, -2.0, 2.0)
    return "gap_error_mm", "mm", values, [od.expander_focus(gap_error_mm=v) for v in values]


def relay_documents(samples, lenses=12):
    """A chain of ``lenses`` ideal thin lenses (f = 50 mm, 4f pairs) with a plane after every lens and one before,
    the first lens moved by -1 to +1 mm."""
    from .. import optical_designs as od

    values = _values(samples, -1.0, 1.0)
    documents = []
    for shift in values:
        document = od._project(od.gaussian_source(wavelength_nm=780.0, waist_radius_mm=0.5,
                                                  position_mm=(-50.0, 0.0, 0.0)))
        planes = [od.observation_plane("p00", (-25.0, 0.0, 0.0))]
        for index in range(lenses):
            x = 100.0 * index + (shift if index == 0 else 0.0)
            document = od._add(document, "lens.ideal_thin",
                               {"focal_length_mm": "50", "clear_aperture_diameter_mm": "25",
                                "valid_band_nm": ["400", "1100"], "model_id": f"relay-{index + 1:02d}"},
                               f"L{index + 1:02d}", (x, 0.0, 0.0), od.BEAM_ALONG_X)
            planes.append(od.observation_plane(f"p{index + 1:02d}", (100.0 * index + 50.0, 0.0, 0.0)))
        document["observations"] = planes
        document["targets"] = [od._target(f"radius_{p['observation_id']}", "beam_radius_mm", p["observation_id"], "mm",
                                          maximum=5.0) for p in planes]
        documents.append(document)
    return "l1_shift_mm", "mm", values, documents


def _values(samples, low, high):
    return [low] if samples == 1 else [low + (high - low) * i / (samples - 1) for i in range(samples)]


BUILDERS = {"expander": expander_documents, "relay": relay_documents}


# --------------------------------------------------------------------------------------------- child

def _timestamp(text):
    from datetime import datetime

    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def child(design, samples, workers, study_dir):
    """One measured run (in its own process): plan, execute and aggregate a sweep; returns the measurements."""
    from .. import sweep_parameters as sp
    from . import aggregate, resources, store

    t0 = time.perf_counter()
    name, unit, values, documents = BUILDERS[design](samples)
    spec = {"parameters": [{"name": name, "unit": unit, "values": values}], "max_evaluations": len(values)}
    built = time.perf_counter()
    summary = sp.run_sweep(spec, study_dir, documents=documents, workers=workers)
    done = time.perf_counter()
    study = store.read_study(study_dir)
    execution = study["execution"]
    journal = store.read_journal(study_dir)
    start = next(_timestamp(e["t"]) for e in journal if e["event"] == "execution_started")
    finished = {e["sample_id"]: _timestamp(e["t"]) for e in journal if e["event"] == "finished"}
    first, peaks = {}, {}
    for entry, record in aggregate.collect_records(study_dir):
        worker = record.get("worker") or "this process"
        if entry["sample_id"] in finished:
            first[worker] = min(first.get(worker, float("inf")), finished[entry["sample_id"]] - start)
        if record.get("peak_memory_bytes"):
            peaks[worker] = max(peaks.get(worker, 0), record["peak_memory_bytes"])
    parent_peak = resources.peak_memory_bytes() or 0
    worker_peaks = sum(v for k, v in peaks.items() if k != f"pid-{os.getpid()}")
    basis = [[row["sample_id"], row["outcome"], row["result_hash"], row["input_hash"], row["project_hash"],
              sorted((k, row[k]) for k in row if k.startswith("target:") and k.count(":") == 1)]
             for row in summary["rows"]]
    return {"design": design, "samples": len(values), "workers": workers, "scheduler": execution["scheduler"],
            "blas_threads": execution["blas_threads"], "build_s": round(built - t0, 3),
            "execute_s": execution["seconds"], "study_s": round(done - built, 3),
            "throughput": round(len(values) / execution["seconds"], 3) if execution["seconds"] else None,
            "startup_s": [w["startup_s"] for w in execution.get("startup") or []],
            "first_result_s": sorted(round(v, 3) for v in first.values()),
            "peak_memory_mib": round((parent_peak + worker_peaks) / MIB, 1),
            "parent_peak_mib": round(parent_peak / MIB, 1), "states": execution["counts"]["states"],
            "basis_sha256": wc.canonical_digest(basis)}


# --------------------------------------------------------------------------------------------- parent

def environment():
    """What the numbers depend on (no paths, no host names)."""
    from .resources import available_memory_bytes, cpu_count

    try:
        engine = wc.engine_identity()
    except ImportError:
        engine = {}
    free = available_memory_bytes()
    return {"kit_version": __version__, "engine_version": engine.get("engine_version"),
            "engine_build_id": engine.get("engine_build_id"), "python": platform.python_version(),
            "os": platform.system(), "machine": platform.machine(), "logical_cpus": cpu_count(),
            "available_memory_mib_at_start": None if free is None else round(free / MIB),
            "start_method": "spawn"}


def count_value(text):
    """One worker count of ``--workers 1,2,4``: a positive integer."""
    try:
        value = int(str(text).strip())
    except ValueError:
        value = 0
    if value < 1:
        raise wc.WorkflowError("BAD_ARGUMENT", f"--workers: give comma-separated positive integers, not {text!r}")
    return value


def worker_counts(workers=None, cpus=None):
    """``(counts, skipped)``: the default counts 1, 2, 4 up to the logical CPUs (the others listed in ``skipped``),
    or the given counts, refused (``BUDGET_INVALID``) before any run when one exceeds the CPUs."""
    from .resources import cpu_count

    cpus = cpus or cpu_count()
    if workers is None:
        counts = [w for w in DEFAULT_WORKERS if w <= cpus]
        skipped = [{"workers": w, "reason": f"above the {cpus} logical CPUs"} for w in DEFAULT_WORKERS if w > cpus]
        return counts, skipped
    counts = sorted({w for w in workers})
    if not counts or any(isinstance(w, bool) or not isinstance(w, int) or w < 1 for w in counts):
        raise wc.WorkflowError("BAD_ARGUMENT", "worker counts must be positive integers")
    above = [w for w in counts if w > cpus]
    if above:
        raise wc.WorkflowError("BUDGET_INVALID", f"worker counts {above} exceed the {cpus} logical CPUs of this "
                               "machine; give counts up to that number", workers=above, cpus=cpus)
    return counts, []


def run_benchmark(out_dir=None, *, workers=None, samples=24, designs="both", repeat=1, keep=False):
    """Run the benchmark (module docstring); returns the result document (also ``bench.json`` / ``bench.md``).
    ``workers``: worker counts (default 1, 2, 4 up to the logical CPUs; a count above them is refused)."""
    designs = list(DESIGNS) if designs == "both" else [designs]
    for design in designs:
        if design not in BUILDERS:
            raise wc.WorkflowError("BAD_ARGUMENT", f"design {design!r}: choose {', '.join(DESIGNS)} or both")
    workers, skipped = worker_counts(workers)
    if samples < 1 or repeat < 1:
        raise wc.WorkflowError("BAD_ARGUMENT", "samples and repeat must be positive")
    temporary = out_dir is None
    out_dir = Path(tempfile.mkdtemp(prefix="os-optics-kit-bench-")) if temporary else Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "OPENSCI_CATALOG_CHECK_INTERVAL": "0"}
    runs = []
    try:
        for design in designs:
            for count in workers:
                measured = []
                for index in range(repeat):
                    study_dir = out_dir / "studies" / f"{design}-w{count}-r{index + 1}"
                    shutil.rmtree(study_dir, ignore_errors=True)
                    t0 = time.perf_counter()
                    done = subprocess.run([sys.executable, "-m", "os_optics_kit.study.bench", "--child", design,
                                           str(samples), str(count), str(study_dir)], capture_output=True, text=True,
                                          env=env, timeout=3600)
                    elapsed = time.perf_counter() - t0
                    if done.returncode != 0:
                        raise wc.WorkflowError("BENCH_FAILED", f"{design} with {count} workers failed: "
                                               f"{done.stderr.strip()[-2000:]}")
                    result = json.loads(done.stdout.strip().splitlines()[-1])
                    result["elapsed_s"] = round(elapsed, 3)
                    measured.append(result)
                    if not keep:
                        shutil.rmtree(study_dir, ignore_errors=True)
                runs.append(_median(measured))
        _compare(runs)
        document = {"status": "BENCHMARKED", "kind": "os-optics-kit.study-benchmark", "schema_version": 1,
                    "environment": environment(), "samples": samples, "repeat": repeat, "runs": runs,
                    "workers_skipped": skipped,
                    "parallel_not_faster": [{"design": r["design"], "workers": r["workers"], "speedup": r["speedup"]}
                                            for r in runs if r["workers"] > 1 and (r["speedup"] or 0) <= 1.0]}
        wc.write_json(out_dir / "bench.json", document)
        (out_dir / "bench.md").write_text(markdown(document), encoding="utf-8", newline="\n")
        if keep or not temporary:
            document["paths"] = {"out": out_dir.as_posix(), "json": "bench.json", "markdown": "bench.md"}
        return document
    finally:
        if temporary:
            shutil.rmtree(out_dir, ignore_errors=True)


def _median(measured):
    if len(measured) == 1:
        return measured[0]
    out = dict(measured[len(measured) // 2])
    for key in ("elapsed_s", "execute_s", "study_s", "throughput", "peak_memory_mib"):
        values = [m[key] for m in measured if m.get(key) is not None]
        out[key] = round(statistics.median(values), 3) if values else None
    out["runs"] = len(measured)
    return out


def _compare(runs):
    for run in runs:
        reference = next((r for r in runs if r["design"] == run["design"] and r["workers"] == 1), None)
        run["equal_to_one_worker"] = None if reference is None else run["basis_sha256"] == reference["basis_sha256"]
        run["speedup"] = (round(reference["execute_s"] / run["execute_s"], 2)
                          if reference and run["execute_s"] else None)
        run["speedup_elapsed"] = (round(reference["elapsed_s"] / run["elapsed_s"], 2)
                                  if reference and run["elapsed_s"] else None)


def markdown(document):
    env = document["environment"]
    lines = [f"# Study execution benchmark (os-optics-kit {env['kit_version']}, os-optics {env['engine_version']})",
             "",
             f"{env['os']} {env['machine']}, Python {env['python']}, {env['logical_cpus']} logical CPUs, worker start "
             f"method {env['start_method']}; {document['samples']} samples per study, median of {document['repeat']} "
             "run(s). Elapsed: the whole command (interpreter start, Engine import, plan, execution, aggregation); "
             "execute: the execution step; start-up: spawn + Engine import of each worker; first result: from the "
             "start of the execution to each worker's first finished sample; peak memory: the command's process plus "
             "every worker.", "",
             "| design | workers | elapsed s | execute s | samples/s | speed-up (execute) | worker start-up s | "
             "first result s | peak memory MiB | equal to 1 worker |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in document["runs"]:
        startup = ", ".join(f"{v:.2f}" for v in r["startup_s"]) or "-"
        first = ", ".join(f"{v:.2f}" for v in r["first_result_s"]) or "-"
        lines.append(f"| {r['design']} | {r['workers']} | {r['elapsed_s']:.2f} | {r['execute_s']:.2f} | "
                     f"{r['throughput']:.2f} | {r['speedup']} | {startup} | {first} | {r['peak_memory_mib']:.0f} | "
                     f"{'yes' if r['equal_to_one_worker'] else 'no'} |")
    slow = document["parallel_not_faster"]
    lines += ["", "Parallel runs that were not faster than one worker: " + (
        "; ".join(f"{s['design']} with {s['workers']} workers (speed-up {s['speedup']})" for s in slow)
        if slow else "none") + "."]
    for item in document.get("workers_skipped") or []:
        lines += ["", f"Not run: {item['workers']} workers ({item['reason']})."]
    designs = sorted({r["design"] for r in document["runs"]})
    command = ("os-optics-kit study bench --workers "
               + ",".join(str(w) for w in sorted({r["workers"] for r in document["runs"]}))
               + f" --samples {document['samples']}")
    if len(designs) == 1:
        command += f" --design {designs[0]}"
    if document["repeat"] > 1:
        command += f" --repeat {document['repeat']}"
    lines += ["", f"Reproduce: `{command}`.", ""]
    return "\n".join(lines)


def _main(argv):
    if argv[:1] == ["--child"]:
        design, samples, workers, study_dir = argv[1], int(argv[2]), int(argv[3]), argv[4]
        os.environ.setdefault("OPENSCI_CATALOG_CHECK_INTERVAL", "0")
        result = child(design, samples, workers, study_dir)
        print(json.dumps(wc.json_safe(result), separators=(",", ":")))
        return 0
    print("usage: python -m os_optics_kit.study.bench --child DESIGN SAMPLES WORKERS STUDY_DIR "
          "(use os-optics-kit study bench)", file=sys.stderr)
    return 4


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
