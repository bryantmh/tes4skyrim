"""One heavy conversion job at a time on this machine, and no work queued twice.

A second `convert.py` or `create_lod.py` WAITS for the first instead of
racing it for CPU and memory: two pooled stages at once exhaust RAM and die as
`BrokenProcessPool` or a bare `MemoryError`. The lock is a named Windows
mutex held until the process exits, and the kernel releases it however the
holder dies, so a crashed run never leaves a stale lock.

A waiting job leaves a ticket naming its work. A newer job whose work covers a
queued one's replaces it: the replaced job stops waiting, follows its
replacement, and exits with its exit code, so whoever launched it still learns
how the work went. The running job does its work in a child process, so a
newer job covering it can replace it the same way: the child's tree is killed,
and the holder follows the replacement and exits with its code.

See: docs/commentary/performance.md#one-heavy-job-at-a-time
"""

import atexit
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

from convert_cli import STEP_FLAGS
from core.process_job import RC_CANCELLED, create_pool_job, run_process

#: The mutex every heavy job takes; per logon session, as the agents share one.
MUTEX_NAME = "Local\\TESConversionHeavyJob"

#: Set once held, so a child the holder spawns never waits on its own parent.
HELD_ENV_VAR = "TESCONV_HEAVY_LOCK_HELD"

#: Set only in the child a holder runs its work in; that child opens no run log of its own.
SUPERVISED_ENV_VAR = "TESCONV_HEAVY_LOCK_SUPERVISED"

#: Each pipeline step's place in run order; a later step may need an earlier one's output.
_RANK = {step: n for n, (step, _flag) in enumerate(STEP_FLAGS)}

#: What the supervised child prints on reaching the lock; the holder drops it and all before it.
_REACHED = "[heavy-lock] reached"

#: Who holds the lock, `{pid, label, work, queued, started}`; the holder deletes it on exit.
HOLDER_FILE = Path(__file__).resolve().parent.parent / "logs" / "heavy_job.txt"

#: The holder line the first lock version wrote, before the file became JSON.
_TEXT_HOLDER = re.compile(r"^(.*) \(pid (\d+), since (\d\d:\d\d:\d\d)\)$")

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


def _line(k32) -> list:
    """(path, ticket) of every other live, unreplaced job, running one first, then by place.

    Dead tickets are dropped on the way.
    """
    jobs = []
    for path in [HOLDER_FILE, *QUEUE_DIR.glob("*.json")]:
        ticket = _read(path)
        pid = ticket.get("pid")
        if not pid or pid == os.getpid() or ticket.get("replaced_by"):
            continue
        if _exit_code(k32, pid, False) != _STILL_ACTIVE:
            path.unlink(missing_ok=True)
            continue
        jobs.append((path, ticket))
    return sorted(jobs, key=lambda j: (j[0] != HOLDER_FILE, j[1].get("queued", 0), j[1]["pid"]))


def needs(work: dict, other: dict) -> bool:
    """Whether `work` may need what job `other` produces: true unless every step
    of `other` runs after every step of `work` in the pipeline, or either names
    a step outside it.
    """
    mine = [_RANK.get(s) for s in work.get("steps", ())]
    theirs = [_RANK.get(s) for s in other.get("steps", ())]
    if not mine or not theirs or None in mine or None in theirs:
        return True
    return min(theirs) <= max(mine)


def _covered_jobs(k32, work: dict) -> list:
    """(path, ticket) of the jobs `work` replaces, taking the earliest one's place.

    The covered jobs behind the last job `work` may need: moving its work ahead
    of a job it needs, such as scripts ahead of their import, would break it.
    """
    found = []
    for path, ticket in _line(k32):
        other = ticket.get("work") or {}
        if covers(work, other):
            found.append((path, ticket))
        elif needs(work, other):
            found = []
    return found


def _replace(covered: list, log) -> None:
    """Mark each covered job replaced by this process, saying which."""
    for path, ticket in covered:
        if _mark_replaced(path, ticket):
            state = "running" if path == HOLDER_FILE else "queued"
            log(f"  Replacing {state} job pid {ticket['pid']} ({ticket.get('label')}): "
                "this run covers its work")


def _first_in_line(k32, place: float) -> bool:
    """Whether no other waiting job holds an earlier place than `place`."""
    mine = (place, os.getpid())
    return not any(path != HOLDER_FILE and (t.get("queued", 0), t["pid"]) < mine
                   for path, t in _line(k32))


def _take_turn(k32, handle, place: float, wait_ms: int) -> int:
    """Wait up to `wait_ms` for the lock when first in line, else sleep; the wait status.

    A job that gets the lock but has since lost first place to a replacement
    hands it straight back.
    """
    if not _first_in_line(k32, place):
        time.sleep(wait_ms / 1000)
        return _WAIT_TIMEOUT
    status = k32.WaitForSingleObject(handle, wait_ms)
    if status in _ACQUIRED and not _first_in_line(k32, place):
        k32.ReleaseMutex(handle)
        return _WAIT_TIMEOUT
    return status


def _watch_replaced(replaced) -> None:
    """Set `replaced` once the holder file names a replacement for this process."""
    while True:
        holder = _read(HOLDER_FILE)
        if holder.get("pid") == os.getpid() and holder.get("replaced_by"):
            replaced.set()
            return
        time.sleep(_POLL_MS / 1000)


class _ChildOutput:
    """Passes a supervised child's lines on once it reaches the lock.

    Earlier lines repeat what this process already printed before the lock;
    they are kept, and shown only if the child exits without reaching it.
    """

    def __init__(self, log):
        self.log, self.early = log, []

    def __call__(self, line: str) -> None:
        """Print `line`, or hold it back while the child has not reached the lock."""
        if self.early is None:
            self.log(line)
            sys.stdout.flush()
        elif line == _REACHED:
            self.early = None
        else:
            self.early.append(line)

    def show_early(self) -> None:
        """Print the lines held back, when the child never reached the lock."""
        for line in self.early or ():
            self.log(line)


def _supervise(k32, handle, log) -> None:
    """Run this same command in a child that does the work, then exit with its code.

    When a newer job marks this one replaced, the child's tree is killed, the
    lock handed on, and this process exits with the replacement's code.
    """
    create_pool_job()
    replaced = threading.Event()
    threading.Thread(target=_watch_replaced, args=(replaced,), daemon=True).start()
    output = _ChildOutput(log)
    code = run_process([sys.executable, *sys.orig_argv[1:]], output,
                       env={SUPERVISED_ENV_VAR: "1", "PYTHONIOENCODING": "utf-8"},
                       cancel_event=replaced)
    output.show_early()
    replacer = _read(HOLDER_FILE).get("replaced_by")
    if code == RC_CANCELLED and replacer:
        k32.ReleaseMutex(handle)
        _follow(k32, replacer, log)
    sys.stdout.flush()
    sys.exit(code)


def _follow(k32, pid: int, log) -> None:
    """Wait for the job that replaced this one, then exit with its exit code."""
    log(f"  Replaced by pid {pid}, whose work covers this run's; "
        "waiting for it to finish")
    sys.stdout.flush()
    code = _exit_code(k32, pid, True)
    log(f"  pid {pid} finished with exit code {code}")
    sys.exit(code if code is not None else 1)


def _holder() -> str:
    """What the holder wrote about itself, or a placeholder."""
    holder = _read(HOLDER_FILE)
    return f"{holder['label']} (pid {holder['pid']})" if holder else "another job"


def snapshot() -> tuple:
    """(running job or None, [queued jobs] in arrival order), live processes only.

    The running job is the holder file's `{pid, label, work, queued, started,
    replaced_by?}`; each queued job is its ticket, `{pid, label, work, queued,
    replaced_by?}`, `queued` being its place in line.
    """
    alive = (lambda pid: bool(pid) and _exit_code(_kernel32(), pid, False) == _STILL_ACTIVE
             ) if sys.platform == "win32" else bool
    holder = _read(HOLDER_FILE) or _text_holder()
    queued = [dict({"queued": path.stat().st_mtime}, **_read(path))
              for path in QUEUE_DIR.glob("*.json")] if QUEUE_DIR.is_dir() else []
    return ((holder if alive(holder.get("pid")) else None),
            sorted((t for t in queued if alive(t.get("pid"))), key=lambda t: t["queued"]))


def _text_holder() -> dict:
    """The holder file as the first lock version wrote it, `label (pid N, since HH:MM:SS)`."""
    try:
        match = _TEXT_HOLDER.match(HOLDER_FILE.read_text(encoding="utf-8").strip())
    except OSError:
        return {}
    if not match:
        return {}
    started = time.mktime(time.strptime(time.strftime("%Y-%m-%d ") + match[3], "%Y-%m-%d %H:%M:%S"))
    return {"label": match[1], "pid": int(match[2]),
            "started": started - 86400 if started > time.time() else started}


def _forget_holder() -> None:
    """Delete the holder file at exit, when it still names this process."""
    if _read(HOLDER_FILE).get("pid") == os.getpid():
        HOLDER_FILE.unlink(missing_ok=True)


def _queue(k32, handle, label: str, work, log) -> tuple:
    """Wait in line for the lock; returns (final wait status, place in line).

    Jobs take the lock in order of place in line, which is arrival time, or
    the earliest place among the jobs this one covers and replaces. Should
    this job be replaced while it waits -- even in the instant it gets the
    lock -- it hands the lock on and follows its replacement instead.
    """
    covered = _covered_jobs(k32, work) if work else []
    place = min([time.time(), *(t.get("queued", t.get("started", time.time())) for _, t in covered)])
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    ticket = QUEUE_DIR / f"{os.getpid()}.json"
    ticket.write_text(json.dumps({"pid": os.getpid(), "label": label, "work": work,
                                  "queued": place}), encoding="utf-8")
    _replace(covered, log)
    started, reported, wait_ms = time.monotonic(), None, 0
    try:
        while True:
            status = _take_turn(k32, handle, place, wait_ms)
            replacer = _read(ticket).get("replaced_by")
            if replacer:
                if status in _ACQUIRED:
                    k32.ReleaseMutex(handle)
                _follow(k32, replacer, log)
            if status != _WAIT_TIMEOUT:
                return status, place
            if reported is None or time.monotonic() - reported >= _REPORT_S:
                reported = time.monotonic()
                log(f"  Waiting for the running heavy job to finish: {_holder()} "
                    f"({(reported - started) / 60:.0f} min so far)")
                sys.stdout.flush()
            wait_ms = _POLL_MS
    finally:
        ticket.unlink(missing_ok=True)


def hold_heavy_lock(label: str, work: dict = None, log=print) -> bool:
    """Take the machine's heavy-job lock for the rest of this process, waiting
    for any other holder to finish first. Returns True when held.

    With `work` (see `covers`), jobs replace the queued or running jobs they
    cover, and the holder reruns this command as a supervised child, exiting
    here with its code or its replacement's. A no-op off Windows, under a
    holder, or without a mutex: the lock must never fail a conversion.
    See: docs/commentary/performance.md#one-heavy-job-at-a-time
    """
    global _HANDLE
    if sys.platform != "win32" or _HANDLE or os.environ.get(HELD_ENV_VAR):
        if os.environ.pop(SUPERVISED_ENV_VAR, None):
            print(_REACHED, flush=True)
        return bool(_HANDLE or os.environ.get(HELD_ENV_VAR))
    k32 = _kernel32()
    handle = k32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        return False
    status, place = _queue(k32, handle, label, work, log)
    if status not in _ACQUIRED:
        return False
    _HANDLE = handle
    os.environ[HELD_ENV_VAR] = str(os.getpid())
    try:
        HOLDER_FILE.parent.mkdir(parents=True, exist_ok=True)
        HOLDER_FILE.write_text(json.dumps({"pid": os.getpid(), "label": label, "work": work,
                                           "queued": place, "started": time.time()}),
                               encoding="utf-8")
        atexit.register(_forget_holder)
    except OSError:
        pass
    if work:
        _supervise(k32, handle, log)
    return True
