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

Both are no-ops on non-Windows platforms, so callers never need their own
``sys.platform`` guard.
"""
import subprocess
import sys

__all__ = ["POPEN_FLAGS", "configure_multiprocessing"]

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
