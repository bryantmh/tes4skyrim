"""Step 3, Convert: the run, as lanes on a time axis, the selected lane's details, and the log.

A run is a list of lanes, each one step of one command (or one load-order action, or one task
done in this process). convert.py's phase banners move the running lane along; a lane's bar
is its real duration once done, and its last recorded duration while queued.
See: docs/commentary/core_gui.md#convert
"""

import queue
import threading
import time

import version as version_info
from core.gui import native, runner
from core.gui.config import STEPS, step_names
from core.gui.window import model, paint_rail
from core.process_job import RC_CANCELLED
from output_layout import DATA_ROOT
from preflight import RC_MISSING_DEP

#: A step label without its number: "3. Meshes" -> "Meshes".
LABELS = {key: label.split(". ", 1)[-1] for key, _f, label, *_r in STEPS}
#: Seconds a never-timed lane is drawn as while queued.
UNKNOWN_SECONDS = 60
#: Log lines kept on screen; the run log on disk has every line.
LOG_SHOWN = 4000
#: How often the timeline is redrawn while running, in seconds.
PAINT_EVERY = 0.25
#: Step key -> what the step does, as its detail card says it.
DESCRIPTIONS = {key: text for key, _f, _l, text, *_r in STEPS}


class Lane:
    """One step of one command (or one action, or one task)."""

    __slots__ = ("label", "sub", "cmd", "step", "plugins", "estimate", "start", "end", "state")

    def __init__(self, label, sub, cmd, step, plugins, estimate):
        """Queued, not started."""
        self.label, self.sub, self.cmd, self.step, self.plugins = label, sub, cmd, step, plugins
        self.estimate = estimate
        self.start = self.end = None
        self.state = "idle"


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


class RunView:
    """The Convert screen and the one run it shows."""

    def __init__(self, ctx):
        """Nothing has run yet."""
        self.ctx = ctx
        self.job = None
        self.lanes = []
        self.lines = []
        self.sink = runner.RunLogSink()
        self.title = ""
        self.started = 0.0
        self.finished_at = None
        self.selected = 0
        self.after = None
        self.on_success = {}
        self.code = None
        self.painted = 0.0
        self.last_stamp = {}
        self.again = None
        self.log_path = None
        ui = ctx.ui
        ui.cancel_run = self.cancel
        ui.run_again = lambda: self.again() if self.again and not self.busy() else None
        ui.select_lane = self._select
        ui.clear_log = self._clear
        ui.open_run_log = self._open_log


    def busy(self) -> bool:
        """True while a run is going."""
        return self.job is not None and self.code is None

    def start_conversion(self, groups: list, name: str, new_plugins=frozenset()) -> None:
        """Convert each group (plugins owing the same steps) in its own convert.py."""
        lanes, cmds = [], []
        for i, (plugins, steps, winding) in enumerate(groups):
            cmds.append(runner.convert_cmd(self.ctx.session, plugins, steps, winding))
            sub = ", ".join(plugins[:2]) + (f" +{len(plugins) - 2}" if len(plugins) > 2 else "")
            lanes += [Lane(LABELS[s], sub, i, s, plugins, runner.estimate(plugins, s)) for s in steps]
        if len(groups) == 1:
            lanes += [Lane(LABELS[k], "Up to date", None, k, [], None) for k, *_ in STEPS
                      if k not in groups[0][1]]
        plugins = [p for g in groups for p in g[0]]
        verb = "Converting" if set(plugins) <= set(new_plugins) else "Updating"
        self._begin(f"{verb} {', '.join(plugins[:3])}" + (f" and {len(plugins) - 3} more" if len(plugins) > 3 else ""),
                    lanes, runner.Run(cmds, {}, RC_MISSING_DEP), {"File": ", ".join(plugins), "Command": "Convert",
                     "Steps": step_names(sorted({s for g in groups for s in g[1]}, key=[k for k, *_ in STEPS].index))})
        self.again = lambda: self.start_conversion(groups, name, new_plugins)

    def start_commands(self, items: list, title: str, on_success=None) -> None:
        """Run commands one after another: `items` is [(label, argv)] or [(label, argv, key)]."""
        lanes = [Lane(item[0], "", i, item[2] if len(item) > 2 else None, [], None) for i, item in enumerate(items)]
        self.on_success = on_success or {}
        self._begin(title, lanes, runner.Run([item[1] for item in items], {}, RC_MISSING_DEP), {"Command": title})
        self.again = lambda: self.start_commands(items, title, on_success)

    def start_task(self, title: str, fn, done=None) -> None:
        """Run `fn(log)` in this process, shown like a command; `done(result)` follows on success."""
        task = Task(fn)
        self.after = (lambda: done(task.result)) if done else None
        self._begin(title, [Lane(title, "", 0, None, [], None)], task, {"Command": title})
        self.again = None

    def _begin(self, title: str, lanes: list, job, header: dict) -> None:
        """Show the new run and start it."""
        if self.busy():
            return
        self.title, self.lanes, self.lines = title, lanes, []
        self.selected = next((i for i, lane in enumerate(lanes) if lane.cmd is not None), 0)
        self.sink.begin(header, DATA_ROOT / "logs")
        if isinstance(job, runner.Run):
            job.env = runner.run_env(self.ctx.session, self.sink)
        self.job, self.code, self.finished_at = job, None, None
        self.started = time.monotonic()
        self._log(f"{title} · {self.ctx.session.worker_count()} workers")
        job.start()
        self.ctx.ui.running = True
        self.ctx.ui.run_done = False
        self.ctx.go("convert")
        self.paint(force=True)


    def tick(self) -> None:
        """Take the run's events and redraw (called on the UI thread)."""
        if self.job is None:
            return
        while not self.job.events.empty():
            self._event(*self.job.events.get_nowait())
        if self.code is None:
            self.paint()

    def _event(self, kind: str, value) -> None:
        """One event from the run."""
        if kind == "cmd":
            self._advance(next((i for i, lane in enumerate(self.lanes) if lane.cmd == value), None))
        elif kind == "line":
            step = runner.phase_step(value)
            if step is not None:
                running = self._running()
                cmd = running.cmd if running else None
                self._advance(next((i for i, lane in enumerate(self.lanes) if lane.cmd == cmd and lane.step == step
                                    and lane.state == "idle"), None))
            self._log(value)
        else:
            self._finish(value)

    def _running(self):
        """The running lane, or None."""
        return next((lane for lane in self.lanes if lane.state == "run"), None)

    def _advance(self, index) -> None:
        """Close the running lane and start lane `index`."""
        now = time.monotonic()
        running = self._running()
        if running is not None:
            running.state, running.end = "done", now
            if running.plugins:
                runner.record_duration(running.plugins, running.step, now - running.start)
        if index is not None:
            lane = self.lanes[index]
            lane.state, lane.start = "run", now

    def _log(self, line: str) -> None:
        """Classify, keep and record one log line."""
        tag = self.sink.take(line)
        self.lines.append({"text": line, "tag": tag or ""})

    def _finish(self, code: int) -> None:
        """The run ended: verdict, error summary, records, and the other screens refreshed."""
        self.code = code
        if code == 0:
            self._advance(None)
        else:
            running = self._running()
            if running is not None:
                running.state, running.end = "idle", time.monotonic()
        self.finished_at = time.monotonic()
        self._log("")
        self._log(runner.verdict_line(code, RC_MISSING_DEP))
        if code not in (0, RC_CANCELLED):
            for line in runner.error_summary(self.sink):
                self._log(line)
        self.log_path = self.sink.path()
        self.sink.end(f"EXIT: {'OK' if not self.sink.errors else 'ERRORS'}")
        if code == 0:
            self._succeeded()
        self.ctx.ui.running = False
        self.ctx.ui.run_done = code == 0
        self.ctx.toast("Done" if code == 0 else "Cancelled" if code == RC_CANCELLED else "Finished with errors")
        self.paint(force=True)
        for name in ("choose", "loadorder", "install"):
            self.ctx.screens[name].run_finished()

    def _succeeded(self) -> None:
        """Record finished load-order actions and run whatever follows a task."""
        for lane in self.lanes:
            key = lane.step
            if key in self.on_success:
                self.on_success[key]()
        if self.after is not None:
            after, self.after = self.after, None
            after()

    def cancel(self) -> None:
        """Stop the run after the current file; finished steps are kept."""
        if self.busy():
            self.job.cancel()
            self._log("Cancelling: stopping the running processes…")


    def _times(self, now: float) -> list:
        """(start, end) in seconds from the run's start for each lane; queued lanes follow the last one."""
        cursor = 0.0
        out = []
        for lane in self.lanes:
            if lane.cmd is None:
                out.append((0.0, 0.0))
                continue
            if lane.start is not None:
                start = lane.start - self.started
                end = (lane.end - self.started) if lane.end else max(now - self.started,
                                                                     start + (lane.estimate or 1))
            else:
                start = cursor
                end = start + (lane.estimate or UNKNOWN_SECONDS)
            cursor = max(cursor, end)
            out.append((start, end))
        return out

    def paint(self, force: bool = False) -> None:
        """Redraw the timeline, the progress, the details and the log."""
        now = time.monotonic()
        if not force and now - self.painted < PAINT_EVERY:
            return
        self.painted = now
        ui = self.ctx.ui
        clock = self.finished_at or now
        spans = self._times(clock)
        total = max([end for _s, end in spans] + [1.0])
        ui.lanes = model({"label": lane.label, "sub": self._sub(lane, span), "start": span[0] / total,
                          "end": span[1] / total, "state": "skip" if lane.cmd is None else lane.state,
                          "selected": i == self.selected, "done_mark": lane.state == "done"}
                         for i, (lane, span) in enumerate(zip(self.lanes, spans)))
        ui.ticks = model(self._ticks(total))
        elapsed = clock - self.started
        ui.now_pos = min(1.0, elapsed / total)
        ui.now_label = runner.format_elapsed(elapsed)
        timed = [lane for lane in self.lanes if lane.cmd is not None]
        done = sum(1 for lane in timed if lane.state == "done")
        ui.run_frac = 1.0 if self.code == 0 else done / max(1, len(timed))
        left = max(0.0, total - elapsed)
        ui.run_title = self.title
        ui.run_time = (f"Finished in {runner.format_elapsed(elapsed)}" if self.code is not None
                       else f"{runner.format_elapsed(elapsed)} · about {runner.format_elapsed(left)} left")
        ui.workers_text = f"{self.ctx.session.worker_count()} workers"
        ui.skipped = ""
        ui.status_step = self._status()
        ui.status_elapsed = runner.format_elapsed(elapsed)
        ui.status_left = "" if self.code is not None else f"About {runner.format_elapsed(left)} left"
        ui.log = model(self.lines[-LOG_SHOWN:])
        self._paint_detail(spans)
        paint_rail(self.ctx)

    def _sub(self, lane, span) -> str:
        """The line under a lane's name."""
        if lane.cmd is None:
            return "Up to date"
        if lane.state == "done":
            return f"{lane.sub} · {runner.format_elapsed(span[1] - span[0])}" if lane.sub else \
                runner.format_elapsed(span[1] - span[0])
        if lane.state == "run":
            return "Running" + (f" · {lane.sub}" if lane.sub else "")
        return (f"~{runner.format_elapsed(lane.estimate)}" if lane.estimate else "Queued") + (
            f" · {lane.sub}" if lane.sub else "")

    @staticmethod
    def _ticks(total: float) -> list:
        """Four or five evenly spaced labels on the time axis."""
        steps = (10, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200)
        every = next((s for s in steps if total / s <= 6), 14400)
        return [{"pos": t / total, "label": runner.format_elapsed(t)} for t in range(0, int(total) + 1, every)]

    def _status(self) -> str:
        """The status line's step."""
        if self.code is None:
            running = self._running()
            return f"Running · {running.label}" if running else "Starting…"
        return "Done" if self.code == 0 else "Cancelled" if self.code == RC_CANCELLED else "Finished with errors"

    def _paint_detail(self, spans) -> None:
        """The selected lane's card."""
        ui = self.ctx.ui
        if not self.lanes:
            return
        lane, span = self.lanes[self.selected], spans[self.selected]
        states = {"done": ("Done", "good"), "run": ("Running", "run"), "idle": ("Queued", "idle")}
        ui.detail_title = lane.label
        ui.detail_status, ui.detail_tone = ("Skipped", "idle") if lane.cmd is None else states[lane.state]
        ui.detail_text = DESCRIPTIONS.get(lane.step, "") if lane.step else ""
        rows = [{"k": "Plugins", "v": ", ".join(lane.plugins)}] if lane.plugins else []
        if lane.state != "idle" and lane.cmd is not None:
            rows.append({"k": "Took" if lane.state == "done" else "So far", "v": runner.format_elapsed(span[1] - span[0])})
        if lane.estimate:
            rows.append({"k": "Last time", "v": runner.format_elapsed(lane.estimate)})
        ui.detail_rows = model(rows)

    def _select(self, index: int) -> None:
        """Show one lane's details."""
        self.selected = index
        self.paint(force=True)

    def _clear(self) -> None:
        """Empty the log on screen; the run log on disk is untouched."""
        self.lines = []
        self.ctx.ui.log = model([])

    def _open_log(self) -> None:
        """Open the run's log file (or the logs folder)."""
        path = self.sink.path() or self.log_path
        native.open_path(str(path) if path else str(DATA_ROOT / "logs"))

    def rail_state(self) -> tuple:
        """(state, summary) for the rail."""
        if self.job is None:
            return "idle", "Nothing running"
        if self.code is None:
            timed = [lane for lane in self.lanes if lane.cmd is not None]
            done = sum(1 for lane in timed if lane.state == "done")
            return "active", f"Running · {done} of {len(timed)}"
        return ("done", "Finished") if self.code == 0 else ("warn", "Stopped")

    def record_global(self, key: str) -> None:
        """Remember that a load-order action finished, so it reads as up to date."""
        try:
            self.last_stamp[key] = runner.global_stamp(self.ctx.session, key)
            version_info.record_step_run(key, None)
        except Exception:
            pass
