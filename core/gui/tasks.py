"""Short jobs that are not a conversion (clear, install, uninstall, add a mod) run beside the window's pages.

They never take over the Convert page: progress shows in the status bar and a toast, a failure in a dialog. A
job asked for while a conversion (or another job) runs waits its turn and says so, and starts when that ends.
See: docs/commentary/core_gui.md#quiet-tasks
"""

import queue
import threading

#: The screens that reread what a finished job may have changed.
REFRESHED = ("choose", "loadorder", "install")


class Task:
    """A slow function run on a thread with its log lines reported like a command's."""

    def __init__(self, fn):
        """Prepare, without starting; `fn(log)` does the work."""
        self.fn = fn
        self.events = queue.Queue()
        self.result = None
        self.thread = None

    def start(self) -> None:
        """Run on a daemon thread."""
        self.thread = threading.Thread(target=self._work, daemon=True)
        self.thread.start()

    def _work(self) -> None:
        """Run, reporting every line and the outcome."""
        self.events.put(("cmd", 0))
        try:
            self.result = self.fn(lambda line: self.events.put(("line", str(line))))
            code = 0
        except Exception as exc:
            self.events.put(("line", f"ERROR: {exc}"))
            code = 1
        self.events.put(("end", code))

    def cancel(self) -> None:
        """A task runs to the end; cancelling only stops what follows it."""

    def alive(self) -> bool:
        """True until the work has finished."""
        return self.thread is not None and self.thread.is_alive()


class Tasks:
    """The quiet jobs of one window: one runs at a time, the rest wait."""

    def __init__(self, ctx, run):
        """Nothing running or waiting; `run` says whether a conversion is going."""
        self.ctx, self.run = ctx, run
        self.waiting = []
        self.task = None
        self.title = ""
        self.done = None
        self.last = ""

    def active(self) -> bool:
        """True while a job runs."""
        return self.task is not None

    def submit(self, title: str, fn, done=None) -> None:
        """Run `fn(log)` now, or when the conversion or the job before it ends; `done(result)` follows on success."""
        later = self.run.busy() or self.active()
        self.waiting.append((title, fn, done))
        if later:
            self.ctx.toast(f"{title} is waiting for the current {'run' if self.run.busy() else 'job'} to end")
        else:
            self._start()

    def resume(self) -> None:
        """A conversion ended: start the next waiting job."""
        if self.waiting and not self.active() and not self.run.busy():
            self._start()

    def _start(self) -> None:
        """Start the first waiting job."""
        title, fn, self.done = self.waiting.pop(0)
        self.title, self.last, self.task = title, "", Task(fn)
        self.task.start()
        self._status(f"{title}…")

    def _status(self, text: str) -> None:
        """Show `text` in the status bar unless a conversion owns it."""
        if not self.run.busy():
            self.ctx.ui.status_step = text

    def tick(self) -> None:
        """Take the running job's events (UI thread)."""
        while self.task is not None and not self.task.events.empty():
            kind, value = self.task.events.get_nowait()
            if kind == "line":
                self.last = value
                self._status(f"{self.title} · {value}")
            elif kind == "end":
                self._end(value)

    def _end(self, code: int) -> None:
        """The job ended: its follow-up or its error, a notice, the other screens reread, the next job."""
        task, done, title = self.task, self.done, self.title
        self.task = self.done = None
        self._status("Ready")
        if code == 0:
            if done is not None:
                done(task.result)
            self.ctx.toast(f"{title}: done")
        else:
            self.ctx.info(f"{title} failed", self.last)
        for name in REFRESHED:
            self.ctx.screens[name].run_finished()
        self.resume()
