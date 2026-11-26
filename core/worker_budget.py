"""Single source of truth for the pipeline's parallel-worker count.

Every parallel stage (export, import, mesh/hkx/audio/lod/script conversion) used
to compute its own worker count inline as ``max(1, (os.cpu_count() or 4) - N)``
with N wandering between 1 and 3 across modules. That made the degree of
parallelism impossible to control centrally — the GUI's worker selector, for
one, had nothing to talk to.

All of that now funnels through :func:`worker_count`.

Resolution order:

  1. ``TESCONV_WORKERS`` env var, if set to a positive integer, is the user's
     explicit choice and wins outright. The GUI sets it from its worker selector;
     it propagates to every child process and every ``multiprocessing`` worker
     because it lives in the environment.
  2. Otherwise the default: the physical CPU count minus 3 (leave headroom for
     the UI / OS), floored at 1.

The result is always clamped to ``[1, cpu_total()]`` — a stage never runs more
workers than the machine has logical cores, no matter what the env var says.
"""
import ctypes
import os
import threading
import time
from ctypes import wintypes

__all__ = ["worker_count", "cpu_total", "WORKERS_ENV_VAR", "MemoryGate", "free_commit_mb", "free_memory_mb", "memory_workers"]

WORKERS_ENV_VAR = "TESCONV_WORKERS"


def cpu_total() -> int:
    """Total logical CPUs on this machine (never below 1)."""
    return max(1, os.cpu_count() or 4)


def worker_count() -> int:
    """Number of parallel workers a pipeline stage should use.

    The user's ``TESCONV_WORKERS`` choice wins if set; otherwise the default is
    ``cpu_total() - 3`` floored at 1. Always clamped to ``[1, cpu_total()]``.
    """
    total = cpu_total()

    override = os.environ.get(WORKERS_ENV_VAR, "").strip()
    if override:
        try:
            chosen = int(override)
        except ValueError:
            chosen = 0
        if chosen > 0:
            return max(1, min(chosen, total))

    return max(1, total - 3)


#: Commit a pool leaves free for everything else: below it, new tasks wait for running ones.
RESERVE_MB = 2048
#: Seconds the memory gate waits before looking at free commit again.
GATE_WAIT = 0.25


class _MemoryStatus(ctypes.Structure):
    """MEMORYSTATUSEX."""

    _fields_ = (("length", wintypes.DWORD), ("load", wintypes.DWORD), ("total", ctypes.c_uint64),
                ("avail", ctypes.c_uint64), ("total_page", ctypes.c_uint64), ("avail_page", ctypes.c_uint64),
                ("total_virtual", ctypes.c_uint64), ("avail_virtual", ctypes.c_uint64), ("extended", ctypes.c_uint64))


def _free_mb(field: str) -> float:
    """MEMORYSTATUSEX `field` in MB (infinite where Windows can't say)."""
    status = _MemoryStatus()
    status.length = ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return float("inf")
    return getattr(status, field) / 2 ** 20


def free_memory_mb() -> float:
    """Physical memory free now, in MB (profiling only; the throttle looks at commit)."""
    return _free_mb("avail")


def free_commit_mb() -> float:
    """Commit (RAM plus page file, less what is already committed) free now, in MB."""
    return _free_mb("avail_page")


def memory_workers(workers: int, per_worker_mb: float) -> int:
    """`workers`, fewer when free commit less RESERVE_MB can't hold that many at `per_worker_mb` each (never 0).

    Prints one line when it lowers the count.
    """
    free = free_commit_mb()
    room = (free - RESERVE_MB) / per_worker_mb
    capped = max(1, min(workers, int(room))) if room != float("inf") else workers
    if capped < workers:
        print(f"  Memory: {capped} of {workers} workers (commit free {free / 1024:.1f} GB)")
    return capped


class MemoryGate:
    """Feeds a pool its tasks only while free commit stays above RESERVE_MB (at least one task always runs),
    so a machine short of commit runs fewer at once and catches up when it frees. Call `done` per result.
    Prints once when it first holds a task and a summary at the end.

    See: docs/commentary/performance.md#memory-gate
    """

    def __init__(self, tasks):
        """Gate `tasks` (any iterable)."""
        self.tasks = tasks
        self.running = 0
        self.held = 0
        self.lock = threading.Lock()

    def _hold(self) -> None:
        """Wait while commit is short and a task is running, reporting the first hold."""
        while self.running > 0 and (free := free_commit_mb()) < RESERVE_MB:
            if not self.held:
                print(f"  Memory: holding tasks (commit free {free / 1024:.1f} GB)")
            self.held += 1
            time.sleep(GATE_WAIT)

    def __iter__(self):
        """Each task, once commit allows (runs on the pool's task-feeding thread)."""
        for task in self.tasks:
            self._hold()
            with self.lock:
                self.running += 1
            yield task
        if self.held:
            print(f"  Memory: held tasks for {self.held * GATE_WAIT:.0f} s in total")

    def done(self) -> None:
        """One task's result arrived."""
        with self.lock:
            self.running -= 1
