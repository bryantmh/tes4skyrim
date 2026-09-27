"""Windows Job Object containment for the pipeline's process pools.

Two failure modes this fixes, both of which surface only as an opaque
``BrokenProcessPool`` (or, worse, as a machine that needs a reboot):

1. **Orphaned workers.** When the parent dies *without* running any cleanup —
   a hard crash, Task Manager "End task", the console window closed, a kill
   from outside — no Python code in the parent gets a chance to run.
   ``kill_process_tree`` only covers a deliberate kill (the GUI's Cancel button);
   it cannot help here, because the process that would call it is already gone.
   The pool's workers survive as detached ``pythonw.exe`` processes (see
   ``subprocess_flags.configure_multiprocessing`` — they are console-less, so
   they are near-invisible in Task Manager), still holding the multi-GB export
   index and open handles on ``output/`` files. The next run then has less RAM
   and hits file-permission errors, which is what makes a reboot look necessary.

   A Job Object with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` fixes this at the
   kernel level: when the last handle to the job closes — which happens
   automatically when the parent process dies, *however* it dies — the OS
   terminates every process in the job. No cleanup code has to run.

2. **Runaway memory (OPT-IN, off by default).** ``JobMemoryLimit`` caps
   committed memory across every process in the job at once. An allocation that
   would exceed the cap fails in the allocating worker as a normal, catchable
   ``MemoryError``. This is **disabled unless ``TESCONV_JOB_MEM_GB`` is set** —
   the cap is job-wide, so it divides across ~29 workers and a "sensible"
   ceiling strangles normal runs. See :func:`job_memory_limit_bytes` for the
   measurement that established this.

Usage — parent, once, before any pool is created::

    from process_job import create_pool_job, job_env
    create_pool_job()
    # pass job_env() into any child you spawn via subprocess

Usage — worker, in the pool initializer::

    from process_job import join_pool_job
    join_pool_job()

Everything here no-ops off Windows and never raises: containment is a safety
net, and failing to establish it must never take down a conversion that would
otherwise have succeeded.

MEASURED CONTRACT (verified 2026-07-29, Win11 / Python 3.14, by hard-killing a
parent with a live pool and checking for survivors): a worker MUST close its own
handle to the job after assigning itself. ``KILL_ON_JOB_CLOSE`` fires only when
the *last* handle closes, so a worker that holds one keeps the job — and
therefore itself and all its siblings — alive after the parent dies. That single
``CloseHandle`` is the difference between this module working and silently doing
nothing.  Full findings: docs/commentary/performance.md.
"""
import os
import queue
import signal
import subprocess
import sys
import threading

from core.subprocess_flags import POPEN_FLAGS

__all__ = [
    "create_pool_job",
    "join_pool_job",
    "job_env",
    "job_memory_limit_bytes",
    "kill_process_tree",
    "run_process",
    "JOB_ENV_VAR",
    "RC_CANCELLED",
    "RC_FAILED",
]

#: Returned by `run_process` when its cancel event fired.
RC_CANCELLED = -2

#: Returned when the process could not be started or streamed at all.
RC_FAILED = -1

# Name of the job object is passed to children through the environment; a
# spawned worker cannot inherit a Python-level handle, so it re-opens by name.
JOB_ENV_VAR = "TESCONV_JOB"
# Memory ceiling (bytes) for the whole job, propagated so children can report it.
JOB_LIMIT_ENV_VAR = "TESCONV_JOB_MEM"

_IS_WIN = sys.platform == "win32"

# Handle to the job, held for the lifetime of the parent process. This is
# deliberately a module global that is NEVER closed: the job must stay open
# exactly as long as the parent lives, and dropping the last reference is what
# triggers the kill. Letting it die with the process is the whole mechanism.
_JOB_HANDLE = None
_JOB_NAME = ""


def _kernel32():
    """Return kernel32 with the argtypes/restypes this module relies on.

    Handles are pointer-sized. Leaving them as the default ``c_int`` truncates
    them on 64-bit Windows, which shows up as an assignment that *reports*
    success against a garbage handle — the exact bug that made an early version
    of this module appear to work while killing nothing.
    """
    import ctypes
    import ctypes.wintypes as wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = ctypes.c_void_p
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    k32.OpenJobObjectW.restype = ctypes.c_void_p
    k32.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, ctypes.c_wchar_p]
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    k32.SetInformationJobObject.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    return k32


def _extended_limit_struct():
    """JOBOBJECT_EXTENDED_LIMIT_INFORMATION (144 bytes on x64)."""
    import ctypes
    import ctypes.wintypes as wintypes

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class BASIC_LIMIT(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class EXTENDED_LIMIT(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BASIC_LIMIT),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    return EXTENDED_LIMIT


# JOBOBJECT_EXTENDED_LIMIT_INFORMATION class for SetInformationJobObject.
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
# Rights a worker needs: set quota (assign) + terminate (be killed by the job).
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001
_JOB_OBJECT_ALL_ACCESS = 0x1F001F


_MEM_LIMIT_ENV_VAR = "TESCONV_JOB_MEM_GB"


def job_memory_limit_bytes() -> int:
    """Committed-memory ceiling for the job, or 0 (the default) for no limit.

    **Off by default, and that is deliberate.** A job-wide cap is shared by
    every worker at once, so it divides by the worker count: measured
    2026-07-29, a ceiling of "available RAM minus headroom" (13.1 GB) across the
    import stage's 29 workers left ~0.45 GB each and made a normal, previously-
    healthy ``--import-only`` run fail 48 furniture-marker NIF reads. The
    failures surfaced as a mix of ``MemoryError`` and a *misleading* numpy
    ``ImportError`` ("do not import numpy from its source directory") — numpy
    misreports an allocation failure during import as a source-tree problem.
    Worse, the caller catches broad ``Exception`` per file, so the stage
    silently degraded instead of failing loudly.

    A ceiling that is safe for 29 workers has to be near total RAM, at which
    point it is not meaningfully protecting anything the OS would not. So the
    default is no limit, and orphan containment (the actually valuable half of
    this module) is completely independent of it.

    Set ``TESCONV_JOB_MEM_GB`` to a positive number of GB to opt in — useful
    when deliberately running a memory-hungry tool and wanting a hard stop
    rather than a swap storm. Remember the value is for the WHOLE job, so divide
    by your worker count to see the per-worker budget.
    """
    override = os.environ.get(_MEM_LIMIT_ENV_VAR, "").strip()
    if override:
        try:
            gb = float(override)
        except ValueError:
            return 0
        if gb > 0:
            return int(gb * 1024 ** 3)
    return 0


def create_pool_job(memory_limit: int = None) -> bool:
    """Create the containment job and put the CURRENT process in it.

    Call once in the parent, before any pool or subprocess is created. Children
    inherit membership automatically (a process spawned by a job member joins
    the same job), and ``job_env()`` additionally lets explicitly-spawned
    children re-join by name.

    Returns True if containment is active. Never raises — on any failure the
    pipeline runs exactly as it did before, just without the safety net.
    """
    global _JOB_HANDLE, _JOB_NAME
    if not _IS_WIN or _JOB_HANDLE is not None:
        return _JOB_HANDLE is not None

    try:
        import ctypes

        k32 = _kernel32()
        # Name it so children spawned through a fresh interpreter can re-open it.
        name = "TESConversion_%d" % os.getpid()
        handle = k32.CreateJobObjectW(None, name)
        if not handle:
            return False

        limit = job_memory_limit_bytes() if memory_limit is None else memory_limit
        info = _extended_limit_struct()()
        flags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if limit:
            flags |= _JOB_OBJECT_LIMIT_JOB_MEMORY
            info.JobMemoryLimit = limit
        info.BasicLimitInformation.LimitFlags = flags
        if not k32.SetInformationJobObject(
                handle, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(info), ctypes.sizeof(info)):
            k32.CloseHandle(handle)
            return False

        # GetCurrentProcess() returns a PSEUDO-handle (-1) which
        # AssignProcessToJobObject rejects with ERROR_INVALID_HANDLE; a real
        # handle from OpenProcess is required.
        ph = k32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE,
                             False, os.getpid())
        if ph:
            try:
                k32.AssignProcessToJobObject(handle, ph)
            finally:
                k32.CloseHandle(ph)

        # Held open for the process lifetime on purpose — see _JOB_HANDLE.
        _JOB_HANDLE = handle
        _JOB_NAME = name
        os.environ[JOB_ENV_VAR] = name
        if limit:
            os.environ[JOB_LIMIT_ENV_VAR] = str(limit)
        return True
    except (OSError, AttributeError, ValueError):
        return False


def join_pool_job() -> bool:
    """Join the parent's containment job. Safe to call from any worker.

    Call from a process-pool ``initializer``. A worker spawned by a job member
    normally inherits membership already; this covers the cases where it does
    not (a fresh interpreter launched via ``subprocess``, or a worker whose
    parent chain was re-parented), and is a cheap no-op when already a member.

    Returns True if the process is contained. Never raises.
    """
    if not _IS_WIN:
        return False
    name = os.environ.get(JOB_ENV_VAR, "").strip()
    if not name:
        return False

    try:
        k32 = _kernel32()
        handle = k32.OpenJobObjectW(_JOB_OBJECT_ALL_ACCESS, False, name)
        if not handle:
            return False
        ph = k32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE,
                             False, os.getpid())
        try:
            if not ph:
                return False
            ok = bool(k32.AssignProcessToJobObject(handle, ph))
        finally:
            # CRITICAL: the worker must NOT keep a handle to the job open.
            # KILL_ON_JOB_CLOSE fires when the LAST handle closes; a worker
            # holding one would keep the job (and every process in it) alive
            # after the parent dies, silently defeating the whole mechanism.
            # Measured: with these handles leaked, orphans survive a parent
            # kill; with them closed, they are terminated immediately.
            k32.CloseHandle(handle)
            if ph:
                k32.CloseHandle(ph)
        return ok
    except (OSError, AttributeError, ValueError):
        return False


def job_env() -> dict:
    """Env vars that let an explicitly-spawned child join the job.

    Merge into the environment of any ``subprocess`` the pipeline launches that
    will itself create pools (e.g. the GUI launching ``convert.py``).
    """
    env = {}
    if _JOB_NAME:
        env[JOB_ENV_VAR] = _JOB_NAME
        limit = os.environ.get(JOB_LIMIT_ENV_VAR, "")
        if limit:
            env[JOB_LIMIT_ENV_VAR] = limit
    return env


def kill_process_tree(pid: int) -> None:
    """Forcibly kill process `pid` and every descendant it spawned.

    Killing only the parent leaves its pool workers and helper .exes (ffmpeg,
    hkxcmd, BSArch, LODGen) running and holding its stdout pipe open.
    `taskkill /T` walks the tree by PID.
    """
    if _IS_WIN:
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, timeout=15, **POPEN_FLAGS)
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    except OSError:
        pass


def _split_lines(buf: bytearray) -> list:
    """Pop every complete line out of `buf`, decoded and newline-stripped."""
    lines = []
    while True:
        nl = buf.find(b"\n")
        if nl == -1:
            return lines
        line = bytes(buf[:nl + 1])
        del buf[:nl + 1]
        lines.append(line.decode("utf-8", errors="replace").rstrip("\r\n"))


def _pipe_reader(out, line_q) -> None:
    """Drain `out` into `line_q` a line at a time, then push a None sentinel."""
    buf = bytearray()
    try:
        while True:
            chunk = out.read(1024)
            if not chunk:
                break
            buf.extend(chunk)
            for line in _split_lines(buf):
                line_q.put(line)
    except (OSError, ValueError):
        pass
    finally:
        text = bytes(buf).decode("utf-8", errors="replace").rstrip("\r\n")
        if text:
            line_q.put(text)
        line_q.put(None)


def _pump(line_q, log_cb, cancel_event, proc) -> bool:
    """Feed queued lines to `log_cb` until the pipe closes; True if cancelled.

    Polls on a short interval so a cancel takes effect even while the child is
    silent or blocked deep inside a long step -- a blocking pipe read must
    never be what stands between the cancel and the process dying.
    """
    while True:
        if cancel_event is not None and cancel_event.is_set():
            kill_process_tree(proc.pid)
            return True
        try:
            item = line_q.get(timeout=0.1)
        except queue.Empty:
            continue
        if item is None:
            return False
        log_cb(item)


def run_process(cmd, log_cb, env=None, cancel_event=None, cwd=None) -> int:
    """Run `cmd`, streaming its output to `log_cb` as bytes arrive.

    Returns the child's exit code, `RC_CANCELLED` if `cancel_event` fired (the
    child's whole tree is killed), or `RC_FAILED` if it could not be run at all.
    """
    try:
        full_env = os.environ.copy()
        full_env["PYTHONUNBUFFERED"] = "1"
        if env:
            full_env.update(env)

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            bufsize=0, cwd=cwd, env=full_env, **POPEN_FLAGS,
        )
        line_q: queue.Queue = queue.Queue()
        threading.Thread(target=_pipe_reader, args=(proc.stdout, line_q),
                         daemon=True).start()

        if _pump(line_q, log_cb, cancel_event, proc):
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            return RC_CANCELLED
        proc.wait()
        return proc.returncode
    except Exception as exc:
        try:
            log_cb(f"ERROR: {exc}")
        except Exception:
            pass
        return RC_FAILED


def describe_limit() -> str:
    """Human-readable summary of the active containment, for the console."""
    if not _IS_WIN or _JOB_HANDLE is None:
        return "Process containment: off"
    limit = os.environ.get(JOB_LIMIT_ENV_VAR, "")
    if limit:
        try:
            return "Process containment: on (memory limit %.1f GB)" % (
                int(limit) / 1024 ** 3)
        except ValueError:
            pass
    return "Process containment: on"
