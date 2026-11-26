"""Jobs that are not a conversion: they wait for a running conversion and never take over the Convert page.

See: docs/commentary/core_gui.md#quiet-tasks
"""

import time
from types import SimpleNamespace

from core.gui.tasks import Tasks


class _Ui:
    """The two properties the jobs write."""

    status_step = ""


class _Ctx:
    """Just what `Tasks` calls."""

    def __init__(self):
        """No notices yet; three screens that count their refreshes."""
        self.ui, self.toasts, self.infos = _Ui(), [], []
        self.screens = {n: _Screen() for n in ("choose", "loadorder", "install")}

    def toast(self, text):
        """Keep the notice."""
        self.toasts.append(text)

    def info(self, title, text):
        """Keep the dialog."""
        self.infos.append((title, text))


class _Screen:
    """Counts `run_finished` calls."""

    refreshed = 0

    def run_finished(self):
        """A refresh."""
        self.refreshed += 1


class _Run:
    """A run that is busy or not."""

    def __init__(self, busy):
        """Set."""
        self.on = busy

    def busy(self):
        """Whether a conversion runs."""
        return self.on


def _drain(tasks):
    """Tick until the job ends."""
    for _ in range(200):
        tasks.tick()
        if not tasks.active():
            return
        time.sleep(0.01)


def test_a_job_runs_and_reports_without_a_run():
    """With no conversion going the job runs at once, then its follow-up and a notice follow."""
    ctx, results = _Ctx(), []
    tasks = Tasks(ctx, _Run(False))
    tasks.submit("Clearing X", lambda log: log("gone") or 7, results.append)
    _drain(tasks)
    assert results == [7] and ctx.toasts == ["Clearing X: done"]
    assert ctx.screens["choose"].refreshed == 1 and ctx.ui.status_step == "Ready"


def test_a_job_during_a_run_waits_and_says_so():
    """It is never dropped: a notice names the wait, and it starts when the run ends."""
    ctx, results, run = _Ctx(), [], _Run(True)
    tasks = Tasks(ctx, run)
    tasks.submit("Clearing X", lambda log: 1, results.append)
    assert not tasks.active() and "waiting" in ctx.toasts[0]
    run.on = False
    tasks.resume()
    _drain(tasks)
    assert results == [1]


def test_a_failed_job_shows_its_error():
    """The last log line (the error) goes to a dialog."""
    ctx = _Ctx()
    tasks = Tasks(ctx, _Run(False))
    tasks.submit("Installing", lambda log: 1 / 0)
    _drain(tasks)
    assert ctx.infos and "division" in ctx.infos[0][1]


def test_a_task_never_takes_over_the_convert_page():
    """start_task runs beside the pages: it goes to `Tasks`, not `_begin`, which would show the Convert page."""
    from core.gui.run_view import RunView

    sent = []
    view = RunView.__new__(RunView)
    view.tasks = SimpleNamespace(submit=lambda *a: sent.append(a))
    view._begin = lambda *a: sent.append("convert page")
    view.start_task("Clearing X", print, None)
    assert sent == [("Clearing X", print, None)]


def test_a_finished_or_cancelled_run_sets_the_steps_back_to_automatic():
    """Steps ticked by hand are not kept after the run: the next plan comes from what is on disk."""
    from core.gui.run_view import RunView

    ui = SimpleNamespace(running=True, run_done=False)
    session = SimpleNamespace(selection=SimpleNamespace(steps=["meshes", "sounds"]))
    screens = {n: _Screen() for n in ("choose", "loadorder", "install")}
    ctx = SimpleNamespace(ui=ui, session=session, screens=screens, toast=lambda _t: None)
    view = RunView.__new__(RunView)
    view.ctx, view.lanes, view.on_success, view.after = ctx, [], {}, None
    view.sink = SimpleNamespace(path=lambda: None, end=lambda _t: None, errors=[], take=lambda _l: "")
    view.lines, view.total = [], 0
    view.tasks = SimpleNamespace(resume=lambda: None)
    view.paint = lambda force=False: None
    view._finish(1)
    assert session.selection.steps is None and screens["choose"].refreshed == 1
