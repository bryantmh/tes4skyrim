"""The navmesh editor's server: cellview (`tools/cellview/server.py`), run as a child of the window.

It starts on a free port; its first line is `cellview: <url>`, which a reader thread keeps along
with the latest line. The server lives exactly as long as the window: closing the window stops
it, and the window's kill-on-close job catches a crash.
See: docs/commentary/tes5_import_navmesh.md#navmesh-editor-in-the-gui
"""

import subprocess
import sys
import threading

from core.python_cmd import python_cmd
from core.gui.config import REPO_ROOT, navmesh_pins_dir
from core.process_job import kill_process_tree
from core.subprocess_flags import POPEN_FLAGS

#: The editor's server script.
SERVER = REPO_ROOT / "tools" / "cellview" / "server.py"


class EditorServer:
    """The one editor server this window runs."""

    def __init__(self):
        """Nothing running yet."""
        self.proc = None
        self.url = ""
        self.last = ""

    def running(self) -> bool:
        """True while the server process is alive."""
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> None:
        """Start the server on a free port, saving pins to the chosen folder."""
        if self.running():
            return
        self.url, self.last = "", "starting…"
        self.proc = subprocess.Popen(
            python_cmd(SERVER, "--port", "0", "--no-browser", "--pins", navmesh_pins_dir()),
            cwd=str(REPO_ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            errors="replace", **POPEN_FLAGS)
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def _read(self, proc) -> None:
        """Keep the URL from the banner and the latest line the server printed."""
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("cellview: ") and not self.url:
                self.url = line.split(" ", 1)[1]
            if line:
                self.last = line

    def stop(self) -> None:
        """Stop the server and anything it started, such as a collision scan."""
        if self.running():
            kill_process_tree(self.proc.pid)
        self.proc = None
        self.url = ""
        self.last = ""

    def state(self) -> tuple:
        """(headline, tone) describing the server right now."""
        if self.running():
            return ("Running", "good") if self.url else ("Starting…", "warn")
        if self.proc is not None:
            return "Stopped unexpectedly", "bad"
        return "Stopped", "idle"
