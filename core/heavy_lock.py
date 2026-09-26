"""One heavy conversion job at a time on this machine.

A second `convert.py` or `create_lod.py` WAITS for the first instead of
racing it for CPU and memory: two pooled stages at once exhaust RAM and die as
`BrokenProcessPool` or a bare `MemoryError`. The lock is a named Windows
mutex held until the process exits, and the kernel releases it however the
holder dies, so a crashed run never leaves a stale lock.

See: docs/commentary/performance.md#one-heavy-job-at-a-time
"""

import os
import sys
import time
from pathlib import Path

#: The mutex every heavy job takes; per logon session, as the agents share one.
MUTEX_NAME = "Local\\TESConversionHeavyJob"

#: Set once held, so a child the holder spawns never waits on its own parent.
HELD_ENV_VAR = "TESCONV_HEAVY_LOCK_HELD"

#: Who holds the lock, written by the holder for the waiting message only.
HOLDER_FILE = Path(__file__).resolve().parent.parent / "logs" / "heavy_job.txt"

#: How often a waiting job says it is still waiting.
_REPORT_MS = 5 * 60 * 1000

_WAIT_OBJECT_0 = 0x0
_WAIT_ABANDONED = 0x80
_WAIT_TIMEOUT = 0x102

#: The mutex handle, never closed: the kernel releases it when the process ends.
_HANDLE = None


def _kernel32():
    """kernel32 with pointer-sized handles for the three calls used here."""
    import ctypes
    import ctypes.wintypes as wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = ctypes.c_void_p
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, ctypes.c_wchar_p]
    k32.WaitForSingleObject.restype = wintypes.DWORD
    k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    return k32


def _holder() -> str:
    """What the holder wrote about itself, or a placeholder."""
    try:
        return HOLDER_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "another job"


def hold_heavy_lock(label: str, log=print) -> bool:
    """Take the machine's heavy-job lock for the rest of this process, waiting
    for any other holder to finish first. Returns True when held.

    A no-op off Windows, when a parent already holds it, or when the mutex
    cannot be made: the lock is a courtesy between runs and must never be the
    reason a conversion fails.
    """
    global _HANDLE
    if sys.platform != "win32" or _HANDLE or os.environ.get(HELD_ENV_VAR):
        return bool(_HANDLE or os.environ.get(HELD_ENV_VAR))
    k32 = _kernel32()
    handle = k32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        return False
    started = time.monotonic()
    status = k32.WaitForSingleObject(handle, 0)
    while status == _WAIT_TIMEOUT:
        log(f"  Waiting for the running heavy job to finish: {_holder()} "
            f"({(time.monotonic() - started) / 60:.0f} min so far)")
        sys.stdout.flush()
        status = k32.WaitForSingleObject(handle, _REPORT_MS)
    if status not in (_WAIT_OBJECT_0, _WAIT_ABANDONED):
        return False
    _HANDLE = handle
    os.environ[HELD_ENV_VAR] = str(os.getpid())
    try:
        HOLDER_FILE.parent.mkdir(parents=True, exist_ok=True)
        HOLDER_FILE.write_text(f"{label} (pid {os.getpid()}, since "
                               f"{time.strftime('%H:%M:%S')})\n", encoding="utf-8")
    except OSError:
        pass
    return True
