"""One heavy conversion job at a time on this machine, and no work queued twice.

A second `convert.py` or `create_lod.py` WAITS for the first instead of
racing it for CPU and memory: two pooled stages at once exhaust RAM and die as
`BrokenProcessPool` or a bare `MemoryError`. The lock is a named Windows
mutex held until the process exits, and the kernel releases it however the
holder dies, so a crashed run never leaves a stale lock.

A waiting job leaves a ticket naming its work. A newer job whose work covers a
queued one's replaces it: the replaced job stops waiting, follows its
replacement, and exits with its exit code, so whoever launched it still learns
how the work went. The running job is never replaced.

See: docs/commentary/performance.md#one-heavy-job-at-a-time
"""

import json
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

#: One `<pid>.json` ticket per waiting job: its label, work, and who replaced it.
QUEUE_DIR = HOLDER_FILE.parent / "heavy_queue"

#: How often a waiting job checks its ticket, and says it is still waiting.
_POLL_MS = 2000
_REPORT_S = 5 * 60

_WAIT_OBJECT_0 = 0x0
_WAIT_ABANDONED = 0x80
_WAIT_TIMEOUT = 0x102
_ACQUIRED = (_WAIT_OBJECT_0, _WAIT_ABANDONED)
_INFINITE = 0xFFFFFFFF
_SYNCHRONIZE = 0x00100000
_QUERY_LIMITED = 0x1000
_STILL_ACTIVE = 259

#: The mutex handle, never closed: the kernel releases it when the process ends.
_HANDLE = None


def _kernel32():
    """kernel32 with pointer-sized handles for the calls used here."""
    import ctypes
    import ctypes.wintypes as wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = ctypes.c_void_p
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, ctypes.c_wchar_p]
    k32.WaitForSingleObject.restype = wintypes.DWORD
    k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    k32.ReleaseMutex.argtypes = [ctypes.c_void_p]
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    return k32


def _exit_code(k32, pid: int, wait: bool):
    """A process's exit code, `_STILL_ACTIVE` while it runs, None when it cannot be opened."""
    import ctypes.wintypes as wintypes

    handle = k32.OpenProcess(_SYNCHRONIZE | _QUERY_LIMITED, False, pid)
    if not handle:
        return None
    if wait:
        k32.WaitForSingleObject(handle, _INFINITE)
    code = wintypes.DWORD()
    ok = k32.GetExitCodeProcess(handle, code)
    k32.CloseHandle(handle)
    return code.value if ok else None


def covers(new: dict, old: dict) -> bool:
    """Whether work `new` does everything work `old` would.

    Both are `{'plugins', 'steps', 'scope': {name: units or None}, 'same'}`:
    `same` (output folder, options) must match, `new` must hold every plugin
    and step of `old`, and each scope of `new` must be unscoped or hold all of
    `old`'s units.
    """
    def within(mine, theirs) -> bool:
        """Whether one scope of `new` covers the matching scope of `old`."""
        return not mine or bool(theirs) and {u.lower() for u in theirs} <= {u.lower() for u in mine}

    new_scope = new.get("scope") or {}
    return (new.get("same") == old.get("same")
            and {p.lower() for p in old.get("plugins", ())} <= {p.lower() for p in new.get("plugins", ())}
            and set(old.get("steps", ())) <= set(new.get("steps", ()))
            and all(within(new_scope.get(name), units)
                    for name, units in (old.get("scope") or {}).items()))


def _read(path: Path) -> dict:
    """A ticket, or {} when it is gone or unreadable."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _mark_replaced(path: Path, ticket: dict) -> bool:
    """Rewrite a still-present ticket as replaced by this process; False when it is gone."""
    ticket["replaced_by"] = os.getpid()
    try:
        with open(path, "r+", encoding="utf-8") as handle:
            handle.truncate(0)
            json.dump(ticket, handle)
    except OSError:
        return False
    return True


def _replace_covered(k32, work: dict, log) -> None:
    """Mark each live queued job `work` covers as replaced by this process; drop dead tickets."""
    for path in sorted(QUEUE_DIR.glob("*.json")):
        ticket = _read(path)
        pid = ticket.get("pid")
        if not pid or pid == os.getpid():
            continue
        if _exit_code(k32, pid, False) != _STILL_ACTIVE:
            path.unlink(missing_ok=True)
        elif (not ticket.get("replaced_by") and covers(work, ticket.get("work") or {})
              and _mark_replaced(path, ticket)):
            log(f"  Replaced queued job pid {pid} ({ticket.get('label')}): this run covers its work")


def _follow(k32, pid: int, log) -> None:
    """Wait for the job that replaced this one, then exit with its exit code."""
    log(f"  Replaced in the queue by pid {pid}, whose work covers this run's; "
        "waiting for it to finish")
    sys.stdout.flush()
    code = _exit_code(k32, pid, True)
    log(f"  pid {pid} finished with exit code {code}")
    sys.exit(code if code is not None else 1)


def _holder() -> str:
    """What the holder wrote about itself, or a placeholder."""
    try:
        return HOLDER_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "another job"


def _queue(k32, handle, label: str, work, log) -> int:
    """Wait in the queue for the lock; returns the final wait status.

    A job this one covers is replaced first. Should this job be replaced while
    it waits -- even in the instant it gets the lock -- it hands the lock on and
    follows its replacement instead.
    """
    if work:
        _replace_covered(k32, work, log)
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    ticket = QUEUE_DIR / f"{os.getpid()}.json"
    ticket.write_text(json.dumps({"pid": os.getpid(), "label": label, "work": work}),
                      encoding="utf-8")
    started, reported = time.monotonic(), None
    try:
        while True:
            if reported is None or time.monotonic() - reported >= _REPORT_S:
                reported = time.monotonic()
                log(f"  Waiting for the running heavy job to finish: {_holder()} "
                    f"({(reported - started) / 60:.0f} min so far)")
                sys.stdout.flush()
            status = k32.WaitForSingleObject(handle, _POLL_MS)
            replacer = _read(ticket).get("replaced_by")
            if replacer:
                if status in _ACQUIRED:
                    k32.ReleaseMutex(handle)
                _follow(k32, replacer, log)
            if status != _WAIT_TIMEOUT:
                return status
    finally:
        ticket.unlink(missing_ok=True)


def hold_heavy_lock(label: str, work: dict = None, log=print) -> bool:
    """Take the machine's heavy-job lock for the rest of this process, waiting
    for any other holder to finish first. Returns True when held.

    `work` (see `covers`) lets a newer job replace this one while it is queued,
    and this one replace a queued job it covers; a replaced job exits here.
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
    status = k32.WaitForSingleObject(handle, 0)
    if status == _WAIT_TIMEOUT:
        status = _queue(k32, handle, label, work, log)
    if status not in _ACQUIRED:
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
