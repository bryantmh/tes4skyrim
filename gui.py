"""
TESRACT - the launcher, and TESRACT.exe's entry point.

The window itself lives in `core/gui/`; this module only starts it, forwards
to convert.py when asked for the headless path, or (inside TESRACT.exe) runs
the module another TESRACT process asked for (`core.python_cmd`). It re-exports the step tables
and the winding settings for callers that import `gui` directly.

Usage:
  python gui.py          # open GUI
  python gui.py --cli    # headless CLI wrapper (see --help)
"""

import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()

from core.collision_options import WINDING_FIX_DEFAULT_PLUGINS
from core.gui.config import (
    GLOBAL_ACTIONS,
    STEPS,
    WINDING_AUTO,
    WINDING_MODES,
    WINDING_OFF,
    WINDING_ON,
    scan_converted,
    scan_plugins,
    winding_enabled_for,
)
from core.gui.native import set_app_id
from core.gui.splash import Splash
from core.process_job import create_pool_job
from core.python_cmd import python_cmd, run_from_argv
from core.subprocess_flags import POPEN_FLAGS as _POPEN_FLAGS
from core.subprocess_flags import ensure_std_handles

#: Re-exported for callers that import `gui` directly.
__all__ = [
    "GLOBAL_ACTIONS", "STEPS",
    "WINDING_AUTO", "WINDING_FIX_DEFAULT_PLUGINS", "WINDING_MODES",
    "WINDING_OFF", "WINDING_ON", "gui_main", "main",
    "scan_converted", "scan_plugins", "winding_enabled_for",
]


def gui_main():
    """Build the converter window and run it; returns an exit code.

    The window's process owns the Job Object that ties convert.py and its
    workers to it, so it is created here rather than when a module is imported.
    """
    if importlib.util.find_spec("slint") is None:
        text = f"The window needs Slint, which {sys.executable} lacks.\n\npip install -r requirements.txt"
        print(f"ERROR: {text}")
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, "TESRACT", 0x10)
        return 1
    set_app_id()
    splash = Splash()
    splash.show()
    from core.gui.window import run_window

    create_pool_job()
    return run_window(splash)


def _relaunch_windowless() -> bool:
    """Re-exec under pythonw.exe so no console lingers; True if relaunched.

    Launched as `python gui.py` from a terminal -- or via the `py` launcher,
    which allocates its own console -- the GUI ends up with a console window
    sitting behind it. DETACHED_PROCESS gives the new process no console at
    all and unties it from the soon-to-close parent terminal. An env flag
    guards against an infinite relaunch loop, and an interpreter that is
    already console-less is left alone.
    """
    if sys.platform != "win32":
        return False
    exe = sys.executable
    if not exe.lower().endswith("python.exe"):
        return False
    pythonw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if not os.path.isfile(pythonw):
        return False
    if os.environ.get("_TES_GUI_RELAUNCHED") == "1":
        return False

    env = os.environ.copy()
    env["_TES_GUI_RELAUNCHED"] = "1"
    try:
        subprocess.Popen(
            [pythonw, str(Path(__file__).resolve())] + sys.argv[1:],
            cwd=str(SCRIPT_DIR),
            env=env,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
            close_fds=True,
        )
        return True
    except OSError:
        return False


def main():
    """Entry point: run a module TESRACT.exe was asked to run, else open the GUI or forward `--cli`."""
    ensure_std_handles()
    if run_from_argv(sys.argv):
        return 0
    parser = argparse.ArgumentParser(description="TES4 Auto-Convert GUI")
    parser.add_argument("--cli", action="store_true",
                        help="Headless: forward remaining args to convert.py")
    args, extra = parser.parse_known_args()

    if args.cli:
        cmd = python_cmd(SCRIPT_DIR / "convert.py", *extra)
        return subprocess.run(cmd, cwd=str(SCRIPT_DIR),
                              **_POPEN_FLAGS).returncode

    if _relaunch_windowless():
        return 0
    return gui_main()


if __name__ == "__main__":
    sys.exit(main())
