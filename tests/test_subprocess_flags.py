"""Processes holding invalid standard handles can still start programs once subprocess_flags loads."""

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
#: A child with a frozen pool worker's invalid stdin, starting a program as the creature tools do.
CHILD = """
import ctypes, subprocess, sys
sys.path.insert(0, {repo!r})
ctypes.windll.kernel32.SetStdHandle(0xFFFFFFF6, ctypes.c_void_p(0x7FFF0000))
if {repair}:
    import core.subprocess_flags
try:
    r = subprocess.run(['cmd', '/c', 'echo ok'], capture_output=True, text=True)
    print('RAN', r.stdout.strip())
except OSError as exc:
    print('FAILED', exc.winerror)
"""


@pytest.mark.skipif(sys.platform != "win32", reason="Windows standard handles")
@pytest.mark.parametrize("repair, expected", [(False, "FAILED 6"), (True, "RAN ok")])
def test_loading_subprocess_flags_repairs_invalid_handles(repair, expected):
    """Without the module a capture fails with WinError 6; with it the program runs."""
    code = CHILD.format(repo=str(REPO), repair=repair)
    got = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert got.stdout.strip() == expected
