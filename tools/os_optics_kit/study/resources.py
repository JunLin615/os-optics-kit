"""Resource budget of a study: worker processes, numerical-library threads, per-evaluation memory and time limits.

``ResourceBudget.resolve`` turns the caller's choices into one budget: ``workers`` x ``blas_threads`` stays within the
logical CPUs (``blas_threads`` = 1 when there is more than one worker), and the budget is recorded in
``study.json`` ``execution``. ``suggest_workers`` picks a worker count from measured costs: a worker process pays a
start-up (importing the Engine, about 4 s and 150 MiB on a desktop machine) before its first sample, so a study whose
serial time is not well above that start-up runs serially.

``peak_memory_bytes`` / ``available_memory_bytes`` read this process's peak memory and the memory the operating
system can still hand out (Windows, Linux and macOS; None where it cannot be read); the benchmark uses them.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import os
import sys

from .. import workflow_common as wc

#: Defaults of ``suggest_workers`` when nothing was measured (catalog-free designs of a few elements).
DEFAULT_SECONDS_PER_SAMPLE = 0.2
DEFAULT_STARTUP_S = 4.0
DEFAULT_MEMORY_MIB = 150.0
#: The most workers ``suggest_workers`` picks unless told otherwise (other jobs share the machine; give N for more).
DEFAULT_MAX_WORKERS = 4
MIB = 1024 * 1024
THREAD_VARIABLES = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS")


def cpu_count():
    """Logical CPUs this process may use."""
    try:
        return len(os.sched_getaffinity(0))  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return os.cpu_count() or 1


def _positive(value, name, *, integer=False):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not value > 0 or not math.isfinite(value):
        raise wc.WorkflowError("BAD_ARGUMENT", f"{name} must be a positive number, got {value!r}")
    if integer and int(value) != value:
        raise wc.WorkflowError("BAD_ARGUMENT", f"{name} must be a positive integer, got {value!r}")
    return int(value) if integer else float(value)


@dataclass(frozen=True)
class ResourceBudget:
    """Workers, BLAS/OpenMP threads per worker, and the per-evaluation limits enforced at Engine checkpoints."""

    workers: int = 1
    blas_threads: int | None = None
    max_memory_mib_per_worker: int | None = None
    time_limit_s: float | None = None

    @classmethod
    def resolve(cls, workers=1, *, blas_threads=None, max_memory_mib=None, time_limit_s=None, cpus=None):
        """A checked budget. ``blas_threads`` defaults to 1 with several workers and to the process's own setting
        (None) with one; ``workers`` x ``blas_threads`` above the logical CPUs is refused (``BUDGET_INVALID``)."""
        workers = _positive(workers, "workers", integer=True)
        blas_threads = _positive(blas_threads, "blas_threads", integer=True)
        cpus = cpus or cpu_count()
        if blas_threads is None and workers > 1:
            blas_threads = 1
        if workers * (blas_threads or 1) > cpus:
            raise wc.WorkflowError("BUDGET_INVALID", f"{workers} workers x {blas_threads or 1} threads exceed the "
                                   f"{cpus} logical CPUs of this machine", workers=workers,
                                   blas_threads=blas_threads, cpus=cpus)
        return cls(workers, blas_threads, _positive(max_memory_mib, "max_memory_mib", integer=True),
                   _positive(time_limit_s, "time_limit_s"))

    def limits(self):
        """The per-evaluation limits passed to the evaluator."""
        return {"time_limit_s": self.time_limit_s, "max_memory_mib": self.max_memory_mib_per_worker}

    def thread_environment(self):
        """The thread variables a worker starts with ({} when the process keeps its own)."""
        if self.blas_threads is None:
            return {}
        return {name: str(self.blas_threads) for name in THREAD_VARIABLES}

    def as_dict(self):
        return {**asdict(self), "cpus": cpu_count()}


def suggest_workers(samples, seconds_per_sample=DEFAULT_SECONDS_PER_SAMPLE, startup_s=DEFAULT_STARTUP_S,
                    memory_mib=DEFAULT_MEMORY_MIB, *, cpus=None, available_mib=None,
                    max_workers=DEFAULT_MAX_WORKERS):
    """The worker count with the shortest expected elapsed time, ``(workers, reasoning)``.

    Serial time = ``samples`` x ``seconds_per_sample`` (the calling process has the Engine loaded already);
    with W worker processes = ``startup_s`` + ceil(samples / W) x ``seconds_per_sample``. W is bounded by the
    logical CPUs minus one (the calling process), by ``max_workers`` (default 4), by the samples and by the
    available memory (``available_mib`` / ``memory_mib``, read from the system when None). A parallel run must save
    at least 10 % of the serial time. Returns 1 when no W does."""
    samples = max(0, int(samples))
    cpus = cpus or cpu_count()
    if available_mib is None:
        free = available_memory_bytes()
        available_mib = None if free is None else free / MIB
    bounds = {"cpus": max(1, cpus - 1), "samples": max(1, samples)}
    if available_mib is not None and memory_mib:
        bounds["memory"] = max(1, int(0.8 * available_mib // memory_mib))
    if max_workers is not None:
        bounds["max_workers"] = max(1, int(max_workers))
    limit = min(bounds.values())
    serial = samples * seconds_per_sample
    best, best_time = 1, serial
    for workers in range(2, limit + 1):
        expected = startup_s + math.ceil(samples / workers) * seconds_per_sample
        if expected < best_time * 0.9:  # a parallel run must save at least 10 % to be worth its processes
            best, best_time = workers, expected
    return best, {"samples": samples, "seconds_per_sample": seconds_per_sample, "startup_s": startup_s,
                  "memory_mib": memory_mib, "bounds": bounds, "expected_serial_s": round(serial, 3),
                  "expected_s": round(best_time, 3), "workers": best}


# --------------------------------------------------------------------------------------------- measured processes

def peak_memory_bytes():
    """Peak memory of this process so far (peak working set on Windows, peak resident set elsewhere)."""
    if sys.platform == "win32":
        counters = _windows_counters()
        return None if counters is None else int(counters.PeakWorkingSetSize)
    try:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(peak if sys.platform == "darwin" else peak * 1024)
    except (ImportError, OSError, ValueError):
        return None


def available_memory_bytes():
    """Memory the operating system can still hand out, or None."""
    if sys.platform == "win32":
        import ctypes

        class Status(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        status = Status()
        status.dwLength = ctypes.sizeof(Status)
        try:
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
                return int(status.ullAvailPhys)
        except (AttributeError, OSError):
            return None
        return None
    try:
        with open("/proc/meminfo", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    try:
        return int(os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, OSError, ValueError):
        return None


def _windows_counters():
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t)]

    try:
        kernel, psapi = ctypes.WinDLL("kernel32"), ctypes.WinDLL("psapi")  # type: ignore[attr-defined]
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        if psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return counters
    except (AttributeError, OSError):
        return None
    return None
