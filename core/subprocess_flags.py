"""Windows subprocess/console-window suppression — shared across the project.

On Windows, when a *console-less* parent process (e.g. ``pythonw.exe`` launched
by double-clicking ``gui.pyw``) spawns a child via ``subprocess`` or
``multiprocessing``, the child gets its *own* freshly-allocated console window.
In a pipeline that shells out per-file (audio/mesh/hkx/bsa/lodgen/papyrus), that
means a flood of terminal windows flashing open in the background.

To fix this everywhere in one place:

  * ``POPEN_FLAGS`` — spread into every ``subprocess.run``/``Popen`` call:
        subprocess.run(cmd, **POPEN_FLAGS)
    It carries ``creationflags=CREATE_NO_WINDOW`` on Windows and is empty
    elsewhere.

  * ``configure_multiprocessing()`` — call ONCE at process start (before any
    ``multiprocessing.Pool`` is created) so spawned Python workers also inherit
    a hidden console. Safe to call on every platform; no-ops off Windows.

  * ``windows_cmd(cmd)`` — wrap a command list before every call that invokes
    one of the bundled Windows tools (hkxcmd, LODGen, the mopp bridge,
    the papyrus compiler, xWMAEncode, LipGenerator):
        subprocess.run(windows_cmd(cmd), **POPEN_FLAGS)
    On Windows it returns ``cmd`` unchanged. Off Windows, if ``cmd[0]`` is a
    ``.exe``, it prepends ``wine``.

  * ``run_streamed(cmd, ...)`` — for a LONG-running bundled tool whose
    progress must reach the log as it happens rather than in one dump when the
    child finally exits, while still returning the full text a caller needs to
    parse afterwards.

  * ``to_wine_path(path)`` — some of those bundled tools are old Windows
    console apps (confirmed under Wine 11.0: hkxcmd, xWMAEncode) that parse
    their own argv and treat a leading ``/`` as a switch prefix, silently
    swallowing an absolute POSIX path as an unrecognised flag instead of
    running. Wrap any absolute-path ARGUMENT (not ``cmd[0]`` — Wine's loader
    itself accepts a plain Unix path for the target exe) passed to one of
    those tools with this first; it prefixes Wine's ``Z:`` drive, which
    mirrors the Unix root, and swaps in backslashes. No-op on Windows.

All are no-ops on non-Windows-*relevant* input, so callers never need their
own ``sys.platform`` guard.
"""
import ctypes
import os
import shutil
import subprocess
import sys
from ctypes import wintypes

__all__ = ["POPEN_FLAGS", "configure_multiprocessing", "windows_cmd",
          "run_streamed", "to_wine_path", "ensure_std_handles"]

#: GetStdHandle's codes for standard input, output and error (as DWORDs).
STD_INPUT, STD_OUTPUT, STD_ERROR = 0xFFFFFFF6, 0xFFFFFFF5, 0xFFFFFFF4
#: CreateFileW arguments for NUL: read or write access, shared both ways, open the existing device.
GENERIC_READ, GENERIC_WRITE, FILE_SHARE_READ_WRITE, OPEN_EXISTING = 0x80000000, 0x40000000, 3, 3
#: INVALID_HANDLE_VALUE as GetStdHandle returns it, and GetFileType's answer for a non-handle.
INVALID_HANDLE, FILE_TYPE_UNKNOWN = 0xFFFFFFFFFFFFFFFF, 0


def ensure_std_handles() -> None:
    """Point every invalid standard handle of this process at NUL (runs when this module loads).

    A process started without a console or inherited handles (the GUI, its runs, their pool workers) can
    hold values that are not handles; `subprocess` then fails every start with WinError 6.
    See: docs/commentary/core_gui.md#native-pieces
    """
    if sys.platform != "win32":
        return
    k32 = ctypes.windll.kernel32
    k32.GetStdHandle.restype = wintypes.HANDLE
    k32.GetFileType.argtypes = (wintypes.HANDLE,)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.SetStdHandle.argtypes = (wintypes.DWORD, wintypes.HANDLE)
    for which, access in ((STD_INPUT, GENERIC_READ), (STD_OUTPUT, GENERIC_WRITE), (STD_ERROR, GENERIC_WRITE)):
        handle = k32.GetStdHandle(which)
        if handle and handle != INVALID_HANDLE and k32.GetFileType(handle) != FILE_TYPE_UNKNOWN:
            continue
        nul = k32.CreateFileW("NUL", access, FILE_SHARE_READ_WRITE, None, OPEN_EXISTING, 0, None)
        if nul and nul != INVALID_HANDLE:
            k32.SetStdHandle(which, nul)


ensure_std_handles()

# Flags to hide the console window of any subprocess we spawn on Windows.
POPEN_FLAGS: dict = {}
if sys.platform == "win32":
    POPEN_FLAGS["creationflags"] = subprocess.CREATE_NO_WINDOW

_mp_configured = False


def configure_multiprocessing() -> None:
    """Make ``multiprocessing`` spawn console-less Python workers on Windows.

    ``multiprocessing`` re-launches the interpreter for each worker. Point it at
    ``pythonw.exe`` (the console-less interpreter) so spawned workers do not each
    pop a console window. Also defaults ``OPENBLAS_NUM_THREADS`` to 1 so each
    process's BLAS does not reserve per-core buffers. Idempotent; the
    interpreter swap is Windows-only.

    See: docs/commentary/performance.md#blas-threads-commit
    """
    global _mp_configured
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    if _mp_configured or sys.platform != "win32":
        return

    import multiprocessing

    try:
        multiprocessing.set_executable(_worker_executable())
    except (RuntimeError, OSError):
        pass

    _mp_configured = True


def _worker_executable() -> str:
    """The console-less ``pythonw.exe`` of the BASE interpreter, else
    ``sys.executable``. A venv's ``pythonw.exe`` is a redirector stub whose
    re-launch drops the pool's handles, so workers die at start; the base one
    is told the venv by ``__PYVENV_LAUNCHER__``, as CPython's spawner does.

    See: docs/commentary/performance.md#venv-pool-workers
    """
    exe = sys.executable
    base = getattr(sys, "_base_executable", exe) or exe
    candidate = os.path.join(os.path.dirname(base), "pythonw.exe")
    if not exe.lower().endswith("python.exe") or not os.path.isfile(candidate):
        return exe
    if os.path.normcase(base) != os.path.normcase(exe):
        os.environ["__PYVENV_LAUNCHER__"] = exe
    return candidate


_wine_bin = None
_wine_checked = False


def _find_wine() -> 'str | None':
    global _wine_bin, _wine_checked
    if not _wine_checked:
        _wine_checked = True
        _wine_bin = shutil.which('wine')
    return _wine_bin


def windows_cmd(cmd: list) -> list:
    """Wrap a command that invokes a bundled Windows ``.exe`` so it also runs
    off Windows, under Wine. No-op on Windows, and no-op for any command whose
    executable is not a ``.exe`` (e.g. ``ffmpeg``, which ships native Linux
    builds) — so callers never need their own ``sys.platform`` guard, exactly
    like ``POPEN_FLAGS`` above.

    Raises ``FileNotFoundError`` with install instructions if wine is required
    but not on PATH, rather than letting Wine's absence surface as a confusing
    "No such file or directory: 'wine'" from deep inside subprocess.
    """
    if sys.platform == 'win32':
        return list(cmd)
    exe = str(cmd[0])
    if not exe.lower().endswith('.exe'):
        return list(cmd)
    wine = _find_wine()
    if not wine:
        raise FileNotFoundError(
            f"wine not found on PATH -- required to run the bundled Windows "
            f"tool {exe!r} on this platform. Install it (e.g. `apt install "
            f"wine` / `pacman -S wine` / `brew install --cask wine-stable`)."
        )
    return [wine, exe] + list(cmd[1:])


def run_streamed(cmd: list, **kwargs) -> tuple:
    """Run ``cmd``, echoing each output line as it arrives; return (text, code).

    stderr is folded into stdout so the echoed order matches the child's own.
    A caller that must both SHOW progress live and PARSE the whole output uses
    this instead of ``capture_output``, which withholds every line until the
    child exits.
    """
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1,
                            errors='replace', **kwargs, **POPEN_FLAGS)
    lines = []
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        lines.append(line)
    return ''.join(lines), proc.wait()


def to_wine_path(path) -> str:
    """Render an absolute path the way Wine's guest argv-parsers need it: a
    ``Z:`` drive prefix and backslash separators.

    No-op on Windows and on non-absolute-POSIX-path input (flags, bare verbs
    like 'convert'). See the module docstring — this is for ARGUMENTS to a
    handful of legacy Windows console tools whose own argument parser (not
    Win32's file APIs, which tolerate '/') breaks on a plain '/'-leading path.
    """
    s = str(path)
    if sys.platform == 'win32' or not s.startswith('/'):
        return s
    return 'Z:' + os.path.abspath(s).replace('/', '\\')
