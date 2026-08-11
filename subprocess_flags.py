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
    one of the bundled Windows tools (BSArch, hkxcmd, LODGen, the mopp bridge,
    the papyrus compiler, xWMAEncode, LipGenerator):
        subprocess.run(windows_cmd(cmd), **POPEN_FLAGS)
    On Windows it returns ``cmd`` unchanged. Off Windows, if ``cmd[0]`` is a
    ``.exe``, it prepends ``wine``.

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
import os
import shutil
import subprocess
import sys

__all__ = ["POPEN_FLAGS", "configure_multiprocessing", "windows_cmd",
          "to_wine_path"]

# Flags to hide the console window of any subprocess we spawn on Windows.
POPEN_FLAGS: dict = {}
if sys.platform == "win32":
    POPEN_FLAGS["creationflags"] = subprocess.CREATE_NO_WINDOW

_mp_configured = False


def configure_multiprocessing() -> None:
    """Make ``multiprocessing`` spawn console-less Python workers on Windows.

    ``multiprocessing`` re-launches the interpreter for each worker. Point it at
    ``pythonw.exe`` (the console-less interpreter) so spawned workers do not each
    pop a console window. Idempotent; no-op off Windows.
    """
    global _mp_configured
    if _mp_configured or sys.platform != "win32":
        return

    import multiprocessing
    import os

    # Prefer pythonw.exe next to the current interpreter so workers have no
    # console. Fall back silently if it is missing (unusual embedded installs).
    exe = sys.executable
    if exe.lower().endswith("python.exe"):
        candidate = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.isfile(candidate):
            exe = candidate

    try:
        multiprocessing.set_executable(exe)
    except (RuntimeError, OSError):
        pass

    _mp_configured = True


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
