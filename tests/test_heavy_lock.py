"""The machine-wide heavy-job lock: a second job waits, a crashed holder frees it,
a holder's own child never waits on it, jobs take the lock in order, and a
queued or running job is replaced by a newer one that covers its work.

See: docs/commentary/performance.md#one-heavy-job-at-a-time
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from core.heavy_lock import covers, needs

ROOT = Path(__file__).resolve().parent.parent

_WINDOWS = pytest.mark.skipif(sys.platform != "win32", reason="Windows mutex")

#: Takes the test lock for the work in argv[3], holds it argv[1] seconds, exits argv[2].
_HOLDER = (
    "import json, os, sys, time; sys.path.insert(0, {root!r}); import core.heavy_lock as h; "
    "h.MUTEX_NAME = 'Local\\\\TESConversionHeavyJobTest'; "
    "h.HOLDER_FILE = h.Path({holder!r}); h.QUEUE_DIR = h.Path({queue!r}); "
    "print('held', h.hold_heavy_lock('test', json.loads(sys.argv[3])), os.getpid(), time.time(), "
    "flush=True); time.sleep(float(sys.argv[1])); sys.exit(int(sys.argv[2]))"
)

_WORK = {'plugins': ['TR_Mainland.esm'], 'steps': ['import'],
         'scope': {'only': None}, 'same': ['output']}

_OTHER = dict(_WORK, plugins=['Oblivion.esm'])

_SCRIPTS = dict(_WORK, steps=['scripts'])


def _start(tmp_path, seconds: float, code: int = 0, work=None, env=None) -> subprocess.Popen:
    """A process that takes the test lock for `work`, holds it `seconds`, then exits `code`."""
    source = _HOLDER.format(root=str(ROOT), holder=str(tmp_path / "holder.txt"),
                            queue=str(tmp_path / "queue"))
    return subprocess.Popen([sys.executable, "-c", source, str(seconds), str(code),
                             json.dumps(work)],
                            stdout=subprocess.PIPE, text=True,
                            env=env or {k: v for k, v in os.environ.items()
                                        if k != "TESCONV_HEAVY_LOCK_HELD"})


def _held(out: str):
    """(pid, time) from the process's `held True` line, or None when it never held the lock."""
    for line in out.splitlines():
        parts = line.split()
        if parts[:2] == ["held", "True"]:
            return int(parts[2]), float(parts[3])
    return None


def _alive(pid: int) -> bool:
    """Whether process `pid` still runs."""
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                         capture_output=True, text=True).stdout
    return str(pid) in out


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
    assert _held(first.stdout.readline())
    started = time.monotonic()
    second = _start(tmp_path, 0)
    out, _ = second.communicate(timeout=60)
    first.wait(timeout=60)
    assert "Waiting for the running heavy job" in out and "test (pid" in out
    assert _held(out.strip().splitlines()[-1])
    assert time.monotonic() - started >= 2


@_WINDOWS
def test_a_killed_holder_frees_the_lock(tmp_path):
    """A holder killed outright leaves no stale lock behind."""
    first = _start(tmp_path, 60)
    assert _held(first.stdout.readline())
    first.kill()
    first.wait(timeout=30)
    second = _start(tmp_path, 0)
    out, _ = second.communicate(timeout=30)
    assert _held(out)


@_WINDOWS
def test_a_killed_supervisor_takes_its_worker(tmp_path):
    """A holder killed outright takes the child doing its work with it, and frees the lock."""
    first = _start(tmp_path, 60, work=_WORK)
    worker, _ = _held(first.stdout.readline())
    first.kill()
    first.wait(timeout=30)
    second = _start(tmp_path, 0, work=_OTHER)
    out, _ = second.communicate(timeout=30)
    assert _held(out) and not _alive(worker)


@_WINDOWS
def test_a_holders_child_does_not_wait(tmp_path):
    """A child the holder spawns skips the wait instead of deadlocking."""
    first = _start(tmp_path, 5)
    assert _held(first.stdout.readline())
    env = dict(os.environ, TESCONV_HEAVY_LOCK_HELD=str(first.pid))
    started = time.monotonic()
    child = _start(tmp_path, 0, env=env)
    out, _ = child.communicate(timeout=30)
    first.kill()
    assert _held(out) and time.monotonic() - started < 4


@_WINDOWS
def test_a_covering_job_replaces_the_queued_one(tmp_path):
    """The queued job steps aside for a newer one with the same work, and exits with its code."""
    running = _start(tmp_path, 6, work=_OTHER)
    assert _held(running.stdout.readline())
    queued = _start(tmp_path, 0, code=3, work=_WORK)
    assert "Waiting" in queued.stdout.readline()
    newer = _start(tmp_path, 0, code=7, work=_WORK)
    queued_out, _ = queued.communicate(timeout=60)
    newer_out, _ = newer.communicate(timeout=60)
    running.wait(timeout=60)
    assert f"Replacing queued job pid {queued.pid}" in newer_out
    assert _held(newer_out) and newer.returncode == 7
    assert not _held(queued_out) and queued.returncode == 7
    assert not list((tmp_path / "queue").glob("*.json"))


@_WINDOWS
def test_a_covering_job_replaces_the_running_one(tmp_path):
    """The running job's work is killed for a newer one, and its launcher gets the newer one's code."""
    running = _start(tmp_path, 60, work=_WORK)
    worker, _ = _held(running.stdout.readline())
    started = time.monotonic()
    newer = _start(tmp_path, 0, code=7, work=_WORK)
    newer_out, _ = newer.communicate(timeout=30)
    running_out, _ = running.communicate(timeout=30)
    assert f"Replacing running job pid {running.pid}" in newer_out
    assert _held(newer_out) and newer.returncode == 7
    assert f"Replaced by pid {newer.pid}" in running_out and running.returncode == 7
    assert not _alive(worker) and time.monotonic() - started < 20


@_WINDOWS
def test_a_job_with_other_work_leaves_the_running_one(tmp_path):
    """A newer job that does not cover the running job's work waits instead of replacing it."""
    running = _start(tmp_path, 3, work=_WORK)
    assert _held(running.stdout.readline())
    other = _start(tmp_path, 0, work=_OTHER)
    other_out, _ = other.communicate(timeout=60)
    running.wait(timeout=60)
    assert "Replacing" not in other_out and running.returncode == 0


@_WINDOWS
def test_a_running_jobs_replacement_runs_before_later_jobs(tmp_path):
    """Import running, scripts queued, a new import: the new import still runs before scripts."""
    running = _start(tmp_path, 60, work=_WORK)
    assert _held(running.stdout.readline())
    scripts = _start(tmp_path, 0, work=_SCRIPTS)
    assert "Waiting" in scripts.stdout.readline()
    newer = _start(tmp_path, 2, code=7, work=_WORK)
    newer_out, _ = newer.communicate(timeout=30)
    scripts_out, _ = scripts.communicate(timeout=30)
    running.wait(timeout=30)
    assert _held(newer_out)[1] < _held(scripts_out)[1]


def test_needs_follows_pipeline_order():
    """Scripts need an import, never the reverse; unknown steps need all."""
    assert needs(_SCRIPTS, _WORK) and not needs(_WORK, _SCRIPTS) and needs(_WORK, _OTHER)
    assert needs(dict(_WORK, steps=['build_patch']), _SCRIPTS)


@_WINDOWS
def test_a_replacement_never_jumps_a_job_it_needs(tmp_path):
    """Scripts, import, scripts queued: new scripts replace only the scripts behind the import."""
    running = _start(tmp_path, 4, work=_OTHER)
    assert _held(running.stdout.readline())
    early = _start(tmp_path, 0, work=_SCRIPTS)
    assert "Waiting" in early.stdout.readline()
    import_ = _start(tmp_path, 1, work=_WORK)
    assert "Waiting" in import_.stdout.readline()
    late = _start(tmp_path, 0, work=_SCRIPTS)
    assert "Waiting" in late.stdout.readline()
    newer = _start(tmp_path, 1, work=_SCRIPTS)
    outs = [p.communicate(timeout=60)[0] for p in (early, import_, newer, late)]
    running.wait(timeout=60)
    early_at, import_at, newer_at = (_held(out)[1] for out in outs[:3])
    assert f"Replacing queued job pid {late.pid}" in outs[2] and str(early.pid) not in outs[2]
    assert early_at < import_at < newer_at and not _held(outs[3])


@_WINDOWS
def test_a_queued_jobs_replacement_keeps_its_place(tmp_path):
    """A replacement for a queued job takes that job's place, ahead of jobs queued after it."""
    running = _start(tmp_path, 4, work=_OTHER)
    assert _held(running.stdout.readline())
    queued = _start(tmp_path, 0, work=_WORK)
    assert "Waiting" in queued.stdout.readline()
    scripts = _start(tmp_path, 0, work=_SCRIPTS)
    assert "Waiting" in scripts.stdout.readline()
    newer = _start(tmp_path, 2, work=_WORK)
    newer_out, _ = newer.communicate(timeout=30)
    scripts_out, _ = scripts.communicate(timeout=30)
    queued.wait(timeout=30)
    running.wait(timeout=30)
    assert _held(newer_out)[1] < _held(scripts_out)[1]
