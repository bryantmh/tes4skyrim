"""
TES4 Auto-Convert - the launcher.

The window itself lives in `core/gui/`; this module only starts it, or forwards
to convert.py when asked for the headless path. It re-exports the step tables
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
from core.gui.app import DND_AVAILABLE
from core.gui.config import (
    GLOBAL_ACTIONS,
    PACKING_STEPS,
    STEPS,
    WINDING_AUTO,
    WINDING_MODES,
    WINDING_OFF,
    WINDING_ON,
    default_on_steps,
    scan_converted,
    scan_plugins,
    winding_enabled_for,
)
from core.process_job import create_pool_job
from core.subprocess_flags import POPEN_FLAGS as _POPEN_FLAGS

#: Re-exported for callers that import `gui` directly.
__all__ = [
    "DND_AVAILABLE", "GLOBAL_ACTIONS", "PACKING_STEPS", "STEPS",
    "WINDING_AUTO", "WINDING_FIX_DEFAULT_PLUGINS", "WINDING_MODES",
    "WINDING_OFF", "WINDING_ON", "default_on_steps", "gui_main", "main",
    "scan_converted", "scan_plugins", "winding_enabled_for",
]


def gui_main():
    """Build the converter window and run it; returns an exit code.

    The window's process owns the Job Object that ties convert.py and its
    workers to it, so it is created here rather than when a module is imported.
    """
    if importlib.util.find_spec("tkinter") is None:
        print("ERROR: tkinter not available")
        return 1
    from core.gui.app import build_window

    create_pool_job()
    build_window().mainloop()
    return 0


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
    """Entry point: open the GUI, or forward `--cli` to convert.py."""
    parser = argparse.ArgumentParser(description="TES4 Auto-Convert GUI")
    parser.add_argument("--cli", action="store_true",
                        help="Headless: forward remaining args to convert.py")
    args, extra = parser.parse_known_args()

    if args.cli:
        cmd = [sys.executable, "-u", str(SCRIPT_DIR / "convert.py")] + extra
        return subprocess.run(cmd, cwd=str(SCRIPT_DIR),
                              **_POPEN_FLAGS).returncode

    if _relaunch_windowless():
        return 0
    return gui_main()


if __name__ == "__main__":
    sys.exit(main())
