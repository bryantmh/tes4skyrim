"""The machine-wide heavy-job lock: a second job waits, a crashed holder frees it,
a holder's own child never waits on it, and a queued job is replaced by a newer
one that covers its work.

See: docs/commentary/performance.md#one-heavy-job-at-a-time
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from core.heavy_lock import covers

ROOT = Path(__file__).resolve().parent.parent

_WINDOWS = pytest.mark.skipif(sys.platform != "win32", reason="Windows mutex")

#: Takes the test lock for the work in argv[3], holds it argv[1] seconds, exits argv[2].
_HOLDER = (
    "import json, sys, time; sys.path.insert(0, {root!r}); import core.heavy_lock as h; "
    "h.MUTEX_NAME = 'Local\\\\TESConversionHeavyJobTest'; "
    "h.HOLDER_FILE = h.Path({holder!r}); h.QUEUE_DIR = h.Path({queue!r}); "
    "print('held', h.hold_heavy_lock('test', json.loads(sys.argv[3])), flush=True); "
    "time.sleep(float(sys.argv[1])); sys.exit(int(sys.argv[2]))"
)

_WORK = {'plugins': ['TR_Mainland.esm'], 'steps': ['import'],
         'scope': {'only': None}, 'same': ['output']}


def _start(tmp_path, seconds: float, code: int = 0, work=None, env=None) -> subprocess.Popen:
    """A process that takes the test lock for `work`, holds it `seconds`, then exits `code`."""
    source = _HOLDER.format(root=str(ROOT), holder=str(tmp_path / "holder.txt"),
                            queue=str(tmp_path / "queue"))
    return subprocess.Popen([sys.executable, "-c", source, str(seconds), str(code),
                             json.dumps(work)],
                            stdout=subprocess.PIPE, text=True,
                            env=env or {k: v for k, v in os.environ.items()
                                        if k != "TESCONV_HEAVY_LOCK_HELD"})


def test_covers_needs_every_plugin_step_and_unit():
    """A newer run covers a queued one only when it does all of that run's work."""
    wider = dict(_WORK, plugins=['Tamriel_Data.esm', 'TR_Mainland.esm'],
                 steps=['import', 'scripts'])
    assert covers(wider, _WORK) and not covers(_WORK, wider)
    assert not covers(dict(_WORK, same=['elsewhere']), _WORK)
    rat = dict(_WORK, scope={'only': ['rat']})
    assert covers(_WORK, rat) and not covers(rat, _WORK)
    assert covers(dict(_WORK, scope={'only': ['rat', 'bear']}), rat)


@_WINDOWS
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


@_WINDOWS
def test_a_killed_holder_frees_the_lock(tmp_path):
    """A holder killed outright leaves no stale lock behind."""
    first = _start(tmp_path, 60)
    assert first.stdout.readline().strip() == "held True"
    first.kill()
    first.wait(timeout=30)
    second = _start(tmp_path, 0)
    out, _ = second.communicate(timeout=30)
    assert out.strip().endswith("held True")


@_WINDOWS
def test_a_holders_child_does_not_wait(tmp_path):
    """A child the holder spawns skips the wait instead of deadlocking."""
    first = _start(tmp_path, 5)
    assert first.stdout.readline().strip() == "held True"
    env = dict(os.environ, TESCONV_HEAVY_LOCK_HELD=str(first.pid))
    started = time.monotonic()
    child = _start(tmp_path, 0, env=env)
    out, _ = child.communicate(timeout=30)
    first.kill()
    assert out.strip() == "held True" and time.monotonic() - started < 4


@_WINDOWS
def test_a_covering_job_replaces_the_queued_one(tmp_path):
    """The queued job steps aside for a newer one with the same work, and exits with its code."""
    running = _start(tmp_path, 6, work=_WORK)
    assert running.stdout.readline().strip() == "held True"
    queued = _start(tmp_path, 0, code=3, work=_WORK)
    assert "Waiting" in queued.stdout.readline()
    newer = _start(tmp_path, 0, code=7, work=_WORK)
    queued_out, _ = queued.communicate(timeout=60)
    newer_out, _ = newer.communicate(timeout=60)
    running.wait(timeout=60)
    assert f"Replaced queued job pid {queued.pid}" in newer_out
    assert newer_out.strip().endswith("held True") and newer.returncode == 7
    assert "held" not in queued_out and queued.returncode == 7
    assert not list((tmp_path / "queue").glob("*.json"))
