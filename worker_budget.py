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
import os

__all__ = ["worker_count", "cpu_total", "WORKERS_ENV_VAR"]

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
