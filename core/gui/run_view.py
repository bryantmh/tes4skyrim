"""Step 3, Convert: the run, as lanes on a time axis, the selected lane's details, and the log.

A run is a list of lanes, each one step of one command (or one load-order action, or one task
done in this process). convert.py's phase banners move the running lane along, and each step's
sub-step lines move it through its sub-steps; a lane's bar is its real duration once done, and
its sub-steps' estimates (`eta`, from what the plugins hold, scaled by this run's measured speed)
while queued or running.
See: docs/commentary/core_gui.md#convert
"""

import re
import time

import version as version_info
from core.gui import eta, native, runner
from core.gui.config import STEPS, step_names
from core.gui.tasks import REFRESHED, Task, Tasks
from core.gui.window import model, paint_rail
from core.process_job import RC_CANCELLED
from output_layout import DATA_ROOT
from preflight import RC_MISSING_DEP

#: A step label without its number: "3. Meshes" -> "Meshes".
LABELS = {key: label.split(". ", 1)[-1] for key, _f, label, *_r in STEPS}
#: Seconds a lane with no estimate (a load-order action, a task) is drawn as while queued.
UNKNOWN_SECONDS = 60
#: The most a run's measured speed may scale its remaining estimates, either way.
SPEED_LIMIT = 4.0
#: Seconds of on-time sub-steps the run's speed starts from, so it moves only with real evidence.
SPEED_PRIOR = 30.0
#: A running sub-step's progress rate counts once 1/PROGRESS_MIN of its count is done.
PROGRESS_MIN = 50
#: A progress line: "  123/4567 ..." as the mesh, navmesh and other batches print it.
PROGRESS_RE = re.compile(r"^\s*(\d+)/(\d+)\b")
#: A running sub-step past its estimate still ends at least this many seconds after now...
OVERRUN_MIN = 5.0
#: ...or this share of the time it has run, whichever is longer: it is still running.
OVERRUN_SHARE = 0.1
#: Log lines kept on screen; the run log on disk has every line.
LOG_SHOWN = 500
#: How often the timeline is redrawn while running, in seconds.
PAINT_EVERY = 0.25
#: Pixels the running bar's stripes move each redraw, and the stripes' period.
STRIPE_STEP, STRIPE_PERIOD = 2, 16
#: Step key -> what the step does, as its detail card says it.
DESCRIPTIONS = {key: text for key, _f, _l, text, *_r in STEPS}


class Sub:
    """One sub-step of one plugin in a lane, with the log line it starts at."""

    __slots__ = ("plugin", "name", "estimate", "pattern", "start", "end", "progress")

    def __init__(self, plugin, name: str, estimate: float, marker):
        """Not started; `marker` is the regex of its first line (None: it starts with its plugin's step)."""
        self.plugin, self.name, self.estimate = plugin, name, estimate
        self.pattern = re.compile(marker) if marker else None
        self.start = self.end = self.progress = None

    def took(self) -> float:
        """Seconds it ran (0 when it never did)."""
        return (self.end - self.start) if self.start is not None and self.end is not None else 0.0

    def left(self, now: float, speed: float) -> float:
        """Seconds still to run: from its `done/total` rate once that counts, else its estimate; past either, the
        overrun margin, since it is still running."""
        ran = now - self.start
        left = self.estimate * speed - ran
        if self.progress is not None:
            t0, d0, done, total = self.progress
            if done - d0 >= max(1, total // PROGRESS_MIN) and now > t0:
                left = (now - t0) * (total - done) / (done - d0)
        return max(left, OVERRUN_MIN, OVERRUN_SHARE * ran)


class Lane:
    """One step of one command (or one action, or one task), as its sub-steps."""

    __slots__ = ("label", "sub", "cmd", "step", "plugins", "subs", "cur", "start", "end", "state")

    def __init__(self, label, sub, cmd, step, plugins):
        """Queued, not started, timed as one unknown sub-step until `eta` has read the plugins."""
        self.label, self.sub, self.cmd, self.step, self.plugins = label, sub, cmd, step, plugins
        self.subs = [Sub(None, "", UNKNOWN_SECONDS, None)]
        self.cur = 0
        self.start = self.end = None
        self.state = "idle"

    @property
    def estimate(self):
        """Estimated seconds over every sub-step, or None before `eta` has given any."""
        return None if self.subs[0].plugin is None else sum(s.estimate for s in self.subs)

    def plan(self, subs: list, now: float) -> None:
        """Take `eta`'s sub-steps [(plugin, name, seconds, marker)]; a running lane starts on the first."""
        if not subs:
            return
        self.subs, self.cur = [Sub(*s) for s in subs], 0
        if self.state != "idle":
            self.subs[0].start = self.start
        if self.state == "done":
            self.subs[0].end = self.end

    def took(self) -> float:
        """Seconds a finished lane ran."""
        return self.end - self.start

    def current(self) -> Sub:
        """The running sub-step (the last one once done)."""
        return self.subs[self.cur]

    def begin(self, now: float) -> None:
        """Start running, on the first sub-step."""
        self.state, self.start, self.cur = "run", now, 0
        self.subs[0].start = now

    def observe(self, line: str, now: float) -> list:
        """Move to the sub-step `line` starts, if any; the sub-steps that finished."""
        here = self.current().plugin
        for j in range(self.cur + 1, len(self.subs)):
            sub = self.subs[j]
            starts = (line.startswith(f"[{sub.plugin}] ") if sub.plugin != here
                      else sub.pattern is not None and sub.pattern.search(line))
            if starts:
                return self._move(j, now)
        return []

    def _move(self, j: int, now: float) -> list:
        """Close the running sub-step and those skipped over; start sub-step `j`."""
        done = self.subs[self.cur]
        done.end = now
        for sub in self.subs[self.cur + 1:j]:
            sub.start = sub.end = now
        self.cur, self.subs[j].start = j, now
        return [done]

    def finish(self, now: float) -> Sub:
        """The lane ended: its running sub-step closes, the rest never ran."""
        self.state, self.end = "done", now
        last = self.current()
        last.end = now
        for sub in self.subs[self.cur + 1:]:
            sub.start = sub.end = now
        return last

    def report(self, done: int, total: int, now: float) -> None:
        """A `done/total` progress line for the running sub-step; a new total starts a new count."""
        sub = self.current()
        if sub.progress is None or sub.progress[3] != total:
            sub.progress = (now, done, done, total)
        else:
            sub.progress = (*sub.progress[:2], done, total)

    def length(self, now: float, speed: float) -> float:
        """Seconds the lane runs: real once done; queued, its sub-steps' estimates; running, what it has run, what
        its running sub-step still needs (never nothing) and the later sub-steps. `speed` is this run's
        actual/estimated so far."""
        if self.state == "done":
            return self.took()
        later = sum(s.estimate for s in self.subs[self.cur + 1:]) * speed
        if self.state == "idle":
            return self.subs[self.cur].estimate * speed + later
        return now - self.start + self.current().left(now, speed) + later


def lane_plan(lane: Lane, workers: int, session=None) -> list:
    """A conversion lane's sub-steps [(plugin, name, seconds, marker)] from what its plugins hold; one empty sub-step
    when they hold nothing the step works on; [] when no plugin file could be read (or for any other lane). Create
    LOD's lane is timed from `session`'s LOD choice."""
    if lane.step == eta.LOD_STEP:
        sources = runner.lod_sources(session) if session is not None else []
        return [(lane.label, name, seconds, None) for name, seconds in eta.lod_plan(sources)]
    subs, read = [], False
    markers = dict(eta.SUBSTEPS.get(lane.step, ()))
    for plugin in lane.plugins if lane.cmd is not None else ():
        plan = eta.plugin_plan(plugin, [lane.step], workers)
        read = read or bool(plan)
        subs += [(plugin, name, seconds, markers.get(name)) for name, seconds in plan.get(lane.step, ())]
    return subs or ([(lane.plugins[0], "", 0.0, None)] if read else [])


def estimates(lanes: list, workers: int, session=None) -> list:
    """Each lane's `lane_plan` (worker thread).

    See: docs/commentary/core_gui.md#run-estimates
    """
    return [lane_plan(lane, workers, session) for lane in lanes]


def bar_text(lane: Lane, span: tuple) -> tuple:
    """(text inside a lane's bar, shorter text when that does not fit): the running sub-step and its count, the
    queued estimate, a finished lane's time."""
    if lane.state == "done":
        return runner.format_elapsed(span[1] - span[0]), ""
    if lane.state == "idle":
        return (f"Queued · ~{runner.format_elapsed(lane.estimate)}" if lane.estimate else "Queued"), "Queued"
    sub = lane.current()
    count = f" · {sub.progress[2]}/{sub.progress[3]}" if sub.progress else ""
    many = len({s.plugin for s in lane.subs}) > 1
    return (sub.name or "Running") + (f" · {sub.plugin}" if many and sub.plugin else "") + count, "Running"


def substep_rows(lane: Lane, now: float) -> list:
    """The detail card's row per sub-step (summed over the lane's plugins, in run order): its time once run, the
    running one's time so far against its estimate, the rest's estimates."""
    order, took, est, running = [], {}, {}, None
    for i, sub in enumerate(lane.subs):
        if sub.plugin is None or not sub.name:
            continue
        if sub.name not in est:
            order.append(sub.name)
        est[sub.name] = est.get(sub.name, 0.0) + sub.estimate
        took[sub.name] = took.get(sub.name, 0.0) + sub.took()
        if lane.state == "run" and i == lane.cur:
            running, took[sub.name] = sub.name, took[sub.name] + now - sub.start
    rows = []
    for name in order:
        if name == running:
            value = f"{runner.format_elapsed(took[name])} of ~{runner.format_elapsed(est[name])}"
        elif took[name] or lane.state == "done":
            value = runner.format_elapsed(took[name])
        else:
            value = f"~{runner.format_elapsed(est[name])}"
        rows.append({"k": ("• " if name == running else "") + name, "v": value})
    return rows


class RunView:
    """The Convert screen and the one run it shows."""

    def __init__(self, ctx):
        """Nothing has run yet."""
        self.ctx = ctx
        self.job = None
        self.cmd_now = None
        self.lanes = []
        self.lines, self.total, self.pushed = [], 0, 0
        self.log_rows = model([])
        self.stripe = 0
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
        self.tasks = Tasks(ctx, self)
        ui = ctx.ui
        ui.cancel_run = self.cancel
        ui.run_again = lambda: self.again() if self.again and not self.busy() else None
        ui.select_lane = self._select
        ui.clear_log = self._clear
        ui.open_run_log = self._open_log
        ui.log_caught_up = self._push_log
        ui.copy_log = self._copy
        ui.log_lines = self.log_rows

    def busy(self) -> bool:
        """True while a run is going."""
        return self.job is not None and self.code is None

    def start_conversion(self, groups: list, name: str, new_plugins=frozenset(), listed=None) -> None:
        """Convert each group (plugins owing the same steps) in its own convert.py; only the steps in `listed` (all
        when None) show as skipped lanes."""
        lanes, cmds = [], []
        for i, (plugins, steps, winding) in enumerate(groups):
            cmds.append(runner.convert_cmd(self.ctx.session, plugins, steps, winding))
            sub = ", ".join(plugins[:2]) + (f" +{len(plugins) - 2}" if len(plugins) > 2 else "")
            lanes += [Lane(LABELS[s], sub, i, s, plugins) for s in steps]
        if len(groups) == 1:
            lanes += [Lane(LABELS[k], "Up to date", None, k, []) for k, *_ in STEPS
                      if k not in groups[0][1] and (listed is None or k in listed)]
        plugins = [p for g in groups for p in g[0]]
        verb = "Converting" if set(plugins) <= set(new_plugins) else "Updating"
        self._begin(f"{verb} {', '.join(plugins[:3])}" + (f" and {len(plugins) - 3} more" if len(plugins) > 3 else ""),
                    lanes, runner.Run(cmds, {}, RC_MISSING_DEP), {"File": ", ".join(plugins), "Command": "Convert",
                     "Steps": step_names(sorted({s for g in groups for s in g[1]}, key=[k for k, *_ in STEPS].index))})
        self.again = lambda: self.start_conversion(groups, name, new_plugins, listed)
        workers = self.ctx.session.worker_count()
        session = self.ctx.session
        self.ctx.work(lambda: estimates(lanes, workers, session), lambda found: self._estimated(lanes, found), counted=False)

    def _estimated(self, lanes: list, found: list) -> None:
        """Give each lane its sub-steps, if this run is still the one on screen."""
        if lanes is self.lanes:
            now = time.monotonic()
            for lane, subs in zip(lanes, found):
                lane.plan(subs, now)
            self.paint(force=True)

    def start_commands(self, items: list, title: str, on_success=None) -> None:
        """Run commands one after another: `items` is [(label, argv)] or [(label, argv, key)]."""
        lanes = [Lane(item[0], "", i, item[2] if len(item) > 2 else None, []) for i, item in enumerate(items)]
        self.on_success = on_success or {}
        self._begin(title, lanes, runner.Run([item[1] for item in items], {}, RC_MISSING_DEP), {"Command": title})
        self.again = lambda: self.start_commands(items, title, on_success)

    def start_task(self, title: str, fn, done=None, foreground: bool = False) -> None:
        """Run `fn(log)` in this process; `done(result)` follows on success. Beside the pages (`Tasks`) unless
        `foreground`, which shows it as a run on the Convert page."""
        if not foreground:
            self.tasks.submit(title, fn, done)
            return
        task = Task(fn)
        self.after = (lambda: done(task.result)) if done else None
        self._begin(title, [Lane(title, "", 0, None, [])], task, {"Command": title})
        self.again = None

    def _begin(self, title: str, lanes: list, job, header: dict) -> None:
        """Show the new run and start it."""
        if self.busy():
            return
        if self.tasks.active():
            self.ctx.toast(f"Wait for {self.tasks.title} to finish")
            return
        self.title, self.lanes = title, lanes
        self._clear()
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
        """Take the quiet jobs' and the run's events and redraw (called on the UI thread)."""
        self.tasks.tick()
        if self.job is None:
            return
        while not self.job.events.empty():
            self._event(*self.job.events.get_nowait())
        if self.code is None:
            self.paint()

    def _event(self, kind: str, value) -> None:
        """One event from the run."""
        if kind == "cmd":
            self.cmd_now = value
            self._advance(next((i for i, lane in enumerate(self.lanes) if lane.cmd == value), None))
        elif kind == "line":
            self._phase(runner.phase_step(value))
            self._substep(value)
            self._log(value)
        else:
            self._finish(value)

    def _phase(self, step) -> None:
        """A phase banner: move to that step's queued lane in the running command; any other line changes nothing."""
        if step is None or getattr(self._running(), "step", None) == step:
            return
        index = next((i for i, lane in enumerate(self.lanes) if lane.cmd == self.cmd_now and lane.step == step
                      and lane.state == "idle"), None)
        if index is not None:
            self._advance(index)

    def _substep(self, line: str) -> None:
        """A sub-step's first line moves the running lane to it; a `done/total` line moves its progress."""
        running = self._running()
        if running is None:
            return
        now = time.monotonic()
        for sub in running.observe(line, now):
            self._learn(running, sub)
        hit = PROGRESS_RE.match(line)
        if hit and int(hit.group(2)) > 0:
            running.report(int(hit.group(1)), int(hit.group(2)), now)

    @staticmethod
    def _learn(lane: Lane, sub: Sub) -> None:
        """A conversion sub-step finished: this computer's correction for it learns from its time."""
        if lane.plugins and sub.plugin is not None and sub.name:
            eta.learn(lane.step, sub.name, sub.estimate, sub.took())

    def _running(self):
        """The running lane, or None."""
        return next((lane for lane in self.lanes if lane.state == "run"), None)

    def _advance(self, index) -> None:
        """Close the running lane and start lane `index`."""
        now = time.monotonic()
        running = self._running()
        if running is not None:
            self._learn(running, running.finish(now))
        if index is not None:
            self.lanes[index].begin(now)

    def _log(self, line: str) -> None:
        """Classify, keep and record one log line; only the last LOG_SHOWN or more are kept here."""
        tag = self.sink.take(line)
        self.lines.append({"text": line, "tag": tag or ""})
        self.total += 1
        if len(self.lines) > 2 * LOG_SHOWN:
            del self.lines[:-LOG_SHOWN]

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
        self.ctx.session.selection.steps = None
        for name in REFRESHED:
            self.ctx.screens[name].run_finished()
        self.tasks.resume()

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


    def _speed(self) -> float:
        """This run's actual time over the estimate, across its finished sub-steps of LEARN_MIN seconds or more,
        with SPEED_PRIOR seconds of on-time evidence so one early sub-step cannot swing it (1.0 before any)."""
        done = [sub for lane in self.lanes for sub in lane.subs if sub.plugin is not None and sub.end is not None
                and sub.estimate > 0 and sub.took() > 0 and max(sub.estimate, sub.took()) >= eta.LEARN_MIN]
        ratio = (sum(sub.took() for sub in done) + SPEED_PRIOR) / (sum(sub.estimate for sub in done) + SPEED_PRIOR)
        return min(SPEED_LIMIT, max(1 / SPEED_LIMIT, ratio))

    def _times(self, now: float) -> list:
        """(start, end) in seconds from the run's start for each lane; queued lanes follow the last one, never
        starting before now."""
        speed, clock = self._speed(), now - self.started
        cursor, out = clock, []
        for lane in self.lanes:
            if lane.cmd is None:
                out.append((0.0, 0.0))
                continue
            start = lane.start - self.started if lane.start is not None else cursor
            end = start + lane.length(now, speed)
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
        ui.lanes = model(self._lane_row(i, lane, span, total) for i, (lane, span) in enumerate(zip(self.lanes, spans)))
        ui.ticks = model(self._ticks(total))
        elapsed = clock - self.started
        ui.now_pos = min(1.0, elapsed / total)
        ui.now_label = runner.format_elapsed(elapsed)
        self.stripe = (self.stripe + STRIPE_STEP) % STRIPE_PERIOD if self.code is None else 0
        ui.stripe_phase = self.stripe
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
        self._push_log()
        self._paint_detail()
        paint_rail(self.ctx)

    def _lane_row(self, i: int, lane: Lane, span: tuple, total: float) -> dict:
        """One lane as the timeline draws it."""
        full, short = bar_text(lane, span)
        return {"label": lane.label, "sub": self._sub(lane, span), "start": span[0] / total, "end": span[1] / total,
                "state": "skip" if lane.cmd is None else lane.state, "selected": i == self.selected,
                "done_mark": lane.state == "done", "bar_text": full, "bar_short": short}

    @staticmethod
    def _sub(lane, span) -> str:
        """The line under a lane's name."""
        if lane.cmd is None:
            return "Up to date"
        if lane.state == "done":
            took = runner.format_elapsed(span[1] - span[0])
            return f"{lane.sub} · {took}" if lane.sub else took
        if lane.state == "run":
            name = lane.current().name or lane.sub
            return "Running" + (f" · {name}" if name else "")
        return "Queued" + (f" · ~{runner.format_elapsed(lane.estimate)}" if lane.estimate else "") + (
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
            if running is None:
                return "Starting…"
            name = running.current().name
            return f"Running · {running.label}" + (f" · {name}" if name else "")
        return "Done" if self.code == 0 else "Cancelled" if self.code == RC_CANCELLED else "Finished with errors"

    def _paint_detail(self) -> None:
        """The selected lane's card: its plugins, time so far or taken, estimate, and each sub-step."""
        ui = self.ctx.ui
        if not self.lanes:
            return
        lane = self.lanes[self.selected]
        states = {"done": ("Done", "good"), "run": ("Running", "run"), "idle": ("Queued", "idle")}
        ui.detail_title = lane.label
        ui.detail_status, ui.detail_tone = ("Skipped", "idle") if lane.cmd is None else states[lane.state]
        ui.detail_text = DESCRIPTIONS.get(lane.step, "") if lane.step else ""
        now = self.finished_at or time.monotonic()
        rows = [{"k": "Plugins", "v": ", ".join(lane.plugins)}] if lane.plugins else []
        if lane.state == "done":
            rows.append({"k": "Took", "v": runner.format_elapsed(lane.took())})
        elif lane.state == "run":
            rows.append({"k": "So far", "v": runner.format_elapsed(now - lane.start)})
        if lane.estimate:
            rows.append({"k": "Estimated", "v": runner.format_elapsed(lane.estimate)})
        ui.detail_rows = model(rows + substep_rows(lane, now))

    def _select(self, index: int) -> None:
        """Show one lane's details."""
        self.selected = index
        self.paint(force=True)

    def _push_log(self) -> None:
        """Show new lines, unless the reader has scrolled up (they wait until the view is back at its end); the
        list keeps the last LOG_SHOWN, added and dropped a row at a time so Slint lays out only what changed."""
        fresh = self.total - self.pushed
        if not self.ctx.ui.log_follow or not fresh:
            return
        if fresh >= LOG_SHOWN:
            del self.log_rows[:]
            fresh = LOG_SHOWN
        for row in self.lines[-fresh:]:
            self.log_rows.append(row)
        if len(self.log_rows) > LOG_SHOWN:
            del self.log_rows[:len(self.log_rows) - LOG_SHOWN]
        self.pushed = self.total

    def _copy(self) -> None:
        """Hand the log lines on screen to the Copy button, as one text."""
        self.ctx.ui.log_copy = chr(10).join(row["text"] for row in self.log_rows)

    def _clear(self) -> None:
        """Empty the log on screen; the run log on disk is untouched."""
        self.lines, self.total, self.pushed = [], 0, 0
        del self.log_rows[:]
        self.ctx.ui.log_follow = True

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
