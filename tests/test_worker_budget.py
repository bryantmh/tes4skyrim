"""Worker counts and the memory gate: fewer tasks at once when commit runs short, never none."""

from core import worker_budget


def _commit(monkeypatch, mb):
    """Pretend the machine has `mb` of commit free."""
    monkeypatch.setattr(worker_budget, "free_commit_mb", lambda: mb)
    monkeypatch.setattr(worker_budget, "GATE_WAIT", 0)


def test_short_commit_holds_new_tasks_until_one_finishes(monkeypatch, capsys):
    """Below the reserve the gate yields only while nothing runs, says so once, and never stalls the pool."""
    _commit(monkeypatch, worker_budget.RESERVE_MB - 1)
    gate = worker_budget.MemoryGate(range(2))
    feed = iter(gate)
    assert next(feed) == 0 and gate.running == 1
    monkeypatch.setattr(worker_budget, "free_commit_mb", iter([0, 0, 0, worker_budget.RESERVE_MB]).__next__)
    assert next(feed) == 1
    assert capsys.readouterr().out.count("Memory: holding tasks") == 1
    gate.done()
    assert list(feed) == []
    assert "held tasks for" in capsys.readouterr().out


def test_never_stalls_when_nothing_runs(monkeypatch):
    """With no task running the gate lets one through however short commit is."""
    _commit(monkeypatch, 0)
    assert next(iter(worker_budget.MemoryGate(range(3)))) == 0


def test_plenty_of_commit_is_silent_and_feeds_every_task(monkeypatch, capsys):
    """Above the reserve the gate is invisible: all tasks go straight to the pool, no output."""
    _commit(monkeypatch, worker_budget.RESERVE_MB + 10_000)
    gate = worker_budget.MemoryGate(range(5))
    assert list(gate) == [0, 1, 2, 3, 4] and gate.running == 5
    assert capsys.readouterr().out == ""


def test_commit_caps_the_worker_count_and_says_so(monkeypatch, capsys):
    """Workers fit in free commit less the reserve; a lowered count prints one line, a full one none."""
    _commit(monkeypatch, worker_budget.RESERVE_MB + 1000)
    assert worker_budget.memory_workers(4, 100) == 4
    assert capsys.readouterr().out == ""
    assert worker_budget.memory_workers(29, 100) == 10
    assert "Memory: 10 of 29 workers" in capsys.readouterr().out


def test_cap_never_below_one(monkeypatch):
    """Even with no commit left a pool gets one worker."""
    _commit(monkeypatch, 10)
    assert worker_budget.memory_workers(29, 100) == 1
