"""The machine-wide heavy-job lock: a second job waits, a crashed holder frees it,
and a holder's own child never waits on it.

See: docs/commentary/performance.md#one-heavy-job-at-a-time
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows mutex")

#: Takes the lock under a test-only name, says so, then holds it for argv[1] seconds.
_HOLDER = (
    "import sys, time; sys.path.insert(0, {root!r}); import core.heavy_lock as h; "
    "h.MUTEX_NAME = 'Local\\\\TESConversionHeavyJobTest'; "
    "h.HOLDER_FILE = h.Path({holder!r}); "
    "print('held', h.hold_heavy_lock('test'), flush=True); time.sleep(float(sys.argv[1]))"
)


def _start(tmp_path, seconds: float, env=None) -> subprocess.Popen:
    """A process that takes the test lock, then holds it for `seconds`."""
    code = _HOLDER.format(root=str(ROOT), holder=str(tmp_path / "holder.txt"))
    return subprocess.Popen([sys.executable, "-c", code, str(seconds)],
                            stdout=subprocess.PIPE, text=True,
                            env=env or {k: v for k, v in os.environ.items()
                                        if k != "TESCONV_HEAVY_LOCK_HELD"})


def test_second_job_waits_until_the_first_exits(tmp_path):
    """A second job names the holder, then takes the lock once it exits."""
    first = _start(tmp_path, 3)
    assert first.stdout.readline().strip() == "held True"
    started = time.monotonic()
    second = _start(tmp_path, 0)
    out, _ = second.communicate(timeout=60)
    first.wait(timeout=60)
    assert "Waiting for the running heavy job" in out and "test (pid" in out
    assert out.strip().endswith("held True")
    assert time.monotonic() - started >= 2


def test_a_killed_holder_frees_the_lock(tmp_path):
    """A holder killed outright leaves no stale lock behind."""
    first = _start(tmp_path, 60)
    assert first.stdout.readline().strip() == "held True"
    first.kill()
    first.wait(timeout=30)
    second = _start(tmp_path, 0)
    out, _ = second.communicate(timeout=30)
    assert out.strip().endswith("held True")


def test_a_holders_child_does_not_wait(tmp_path):
    """A child the holder spawns skips the wait instead of deadlocking."""
    first = _start(tmp_path, 5)
    assert first.stdout.readline().strip() == "held True"
    env = dict(os.environ, TESCONV_HEAVY_LOCK_HELD=str(first.pid))
    started = time.monotonic()
    child = _start(tmp_path, 0, env)
    out, _ = child.communicate(timeout=30)
    first.kill()
    assert out.strip() == "held True" and time.monotonic() - started < 4
