"""End-to-end run-log behaviour of the real convert.py process.

Unit tests cover naming and retention in isolation; these run the actual entry
point, because the defect this feature is most likely to grow is a wiring one
-- the log opening in the wrong place, or every step of a GUI run opening its
own file so the retained runs become the last N STEPS of one run.

Every test logs into its own directory via TESCONV_LOGS_DIR.  The real logs/
is never read, written or renamed: a run the user has going owns an open
handle there, so Windows refuses the rename and the test counts that live log
as its own.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core.run_log as run_log
from core.gui.config import DEFAULT_CONFIG


# A plugin that cannot exist: the export stage fails immediately, so the run
# is fast, converts nothing, and still exercises the REAL logging path
# (informational flags like --list-mods deliberately skip run logging).
FAST_RUN = ("-f", "NoSuchPlugin.esm", "--export-only")


def _default_config_copy():
    """A fresh copy of the in-code defaults the GUI seeds a new install with."""
    return dict(DEFAULT_CONFIG)


def _run(logs_dir, env_extra=None, args=FAST_RUN):
    """Run convert.py with its logs redirected into `logs_dir`."""
    env = dict(os.environ)
    env.pop(run_log.RUN_LOG_ENV_VAR, None)
    env["PYTHONIOENCODING"] = "utf-8"
    env[run_log.LOGS_DIR_ENV_VAR] = str(logs_dir)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(ROOT / "convert.py"), *args],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=110,
    )


def _config_with(tmp_path, keep):
    """A copy of the default config with `logRunsKept` set; returns its path."""
    cfg_path = tmp_path / f"cfg-{keep}.json"
    base = _default_config_copy()
    base["logRunsKept"] = keep
    cfg_path.write_text(json.dumps(base), encoding="utf-8")
    return cfg_path


def _names(logs_dir):
    """Every run-log name currently in `logs_dir`."""
    return sorted(p.name for p in logs_dir.glob("run-*.log"))


def _latest_text(logs_dir):
    """Contents of the newest run log."""
    return run_log.latest_log(logs_dir).read_text(encoding="utf-8")


@pytest.fixture
def logs_dir(tmp_path):
    """A private logs/ for one test."""
    return tmp_path / "logs"


def test_cli_run_writes_a_log(logs_dir):
    """A standalone run leaves one log, named and framed."""
    r = _run(logs_dir)
    path = run_log.latest_log(logs_dir)
    assert path is not None, "a standalone convert.py run must leave a run log"
    text = path.read_text(encoding="utf-8")
    assert "# TESRACT run log" in text
    assert "NoSuchPlugin.esm" in text                # header records the argv
    assert "# Finished:" in text
    assert f"EXIT: {r.returncode}" in text           # the REAL exit status
    assert "Conversion Pipeline" in text             # console output captured


def test_log_name_states_the_time_and_the_plugin(logs_dir):
    """The name alone identifies which build a log belongs to."""
    _run(logs_dir)
    name = run_log.latest_log(logs_dir).name
    assert name.endswith("-NoSuchPlugin.esm.log"), name
    assert name.split("-")[1] == time.strftime("%Y%m%d"), name


def test_each_run_gets_its_own_file(logs_dir):
    """Two runs leave two logs -- a run never overwrites its predecessor."""
    _run(logs_dir)
    first = run_log.latest_log(logs_dir).name
    _run(logs_dir)
    names = _names(logs_dir)
    assert len(names) == 2 and first in names


def test_informational_run_writes_no_log(logs_dir):
    """--help/--list-mods convert nothing; they must not evict a real log."""
    _run(logs_dir)                                   # a real run
    before = _latest_text(logs_dir)
    for args in (("--help",), ("--list-mods",)):
        _run(logs_dir, args=args)
    assert len(_names(logs_dir)) == 1
    assert _latest_text(logs_dir) == before


def test_console_output_is_unchanged_by_teeing(logs_dir):
    """The Tee must not swallow or mangle stdout -- the GUI pipes it."""
    r = _run(logs_dir)
    body = _latest_text(logs_dir)
    for line in (l for l in r.stdout.splitlines() if l.strip()):
        assert line in body, f"console line missing from log: {line!r}"


def test_child_process_writes_no_log(logs_dir):
    """A GUI child (TESCONV_RUN_LOG set) must not touch logs/ at all.

    This is the whole reason the owner opens the log: a 7-step GUI run would
    otherwise leave seven files holding one step each.
    """
    _run(logs_dir)
    owned = run_log.latest_log(logs_dir)
    first = owned.read_text(encoding="utf-8")

    _run(logs_dir, {run_log.RUN_LOG_ENV_VAR: str(owned)})
    assert len(_names(logs_dir)) == 1, "child opened its own log"
    assert owned.read_text(encoding="utf-8") == first


def test_retention_keeps_only_configured_count(logs_dir, tmp_path):
    """Four runs at keep=3 -> the oldest is evicted, three remain."""
    cfg_path = _config_with(tmp_path, 3)
    for _ in range(4):
        _run(logs_dir, args=("--config", str(cfg_path), *FAST_RUN))
    assert len(_names(logs_dir)) == 3


def test_log_runs_kept_zero_disables(logs_dir, tmp_path):
    """`logRunsKept: 0` is an explicit opt-out and must write nothing."""
    _run(logs_dir, args=("--config", str(_config_with(tmp_path, 0)), *FAST_RUN))
    assert _names(logs_dir) == []


def test_log_runs_kept_honours_custom_count(logs_dir, tmp_path):
    """A lowered count is obeyed, not the shipped default."""
    cfg_path = _config_with(tmp_path, 2)
    for _ in range(4):
        _run(logs_dir, args=("--config", str(cfg_path), *FAST_RUN))
    assert len(_names(logs_dir)) == 2


def test_seeded_config_declares_the_key():
    """The key must be discoverable in the config a new install is seeded with.

    conversion_config.json is untracked, so the defaults are the contract.
    """
    cfg = _default_config_copy()
    assert cfg.get("logRunsKept") == run_log.DEFAULT_RUNS_KEPT
    assert any("logRunsKept" in k for k in cfg if k.startswith("//")), \
        "logRunsKept needs a // comment entry explaining it"


def test_config_file_is_untracked():
    """A tracked config would ship one machine's paths to every user."""
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "conversion_config.json"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=30,
    )
    assert tracked.returncode != 0, \
        "conversion_config.json is tracked again; it must stay gitignored"
