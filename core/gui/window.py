"""The TESRACT window: loads the Slint interface, owns the shared helpers every screen uses, and
wires the steps, the rail and the dialogs that are not one screen's own.

Slint is not thread-safe and offers no way to post from a worker, so work that blocks (reading
plugins, inspecting an archive, installing) runs on a thread and hands its result back through
`Ctx.post`; a 50 ms timer on the UI thread runs whatever was posted.
See: docs/commentary/core_gui.md#threads
"""

import datetime
import gc
import queue
import threading
import time
from pathlib import Path

import slint
from PIL import Image

import version as version_info
from core.gui import exe_update, native, navmesh_editor
from core.gui.config import (NAVMESH_PINS_CONFIG_KEY, REPO_ROOT, STEPS, navmesh_pins_dir, save_setting,
                             write_default_config)
from core.gui.session import PARALLAX_CONFIG_KEY, THEME_CONFIG_KEY, Session

#: The interface definition.
UI_FILE = Path(__file__).with_name("ui") / "app.slint"
#: The banner shown at the top of the rail and in About.
BANNER = REPO_ROOT / "docs" / "assets" / "banner.png"
#: The window and taskbar icon.
ICON = REPO_ROOT / "docs" / "assets" / "favicon.ico"
#: How often posted work and the run are checked, in milliseconds.
TICK_MS = 50
#: Longest the window stays hidden while its first data loads, in seconds.
STARTUP_WAIT = 20
#: Ticks between full garbage collections (the others collect only new objects).
FULL_GC_TICKS = 100
#: How long a notice stays up, in seconds.
TOAST_SECONDS = 2.6
#: The project's links, as About shows them.
LINKS = {"github": version_info.REPO_URL, "youtube": "https://www.youtube.com/@bryanthinton",
         "discord": "https://discord.gg/NTkCDfYUru"}
#: The steps of the rail: (key, title).
RAIL = (("setup", "Setup"), ("choose", "Choose"), ("convert", "Convert"), ("loadorder", "Load order"),
        ("install", "Install & play"))
#: Skyrim builds and what each supports, as the versions dialog shows them.
VERSIONS = (("1.6 and 1.7", "Full support · Steam, GOG, Epic"),
            ("1.5.97 Special Edition", "Every game but Morrowind, whose runtime needs 1.6 or later"),
            ("VR 1.4.15", "Every game but Morrowind, whose runtime needs 1.6 or later"))

# ---------------------------------------------------------------------------
#  Small helpers
# ---------------------------------------------------------------------------

def model(rows) -> slint.ListModel:
    """A Slint list model of `rows` (dicts or plain values)."""
    return slint.ListModel(list(rows))


def banner_clip(path) -> tuple:
    """(x, y, width, height) of the banner's opaque area, so the rail shows no empty margin."""
    try:
        with Image.open(path) as im:
            box = (im.getchannel("A").getbbox() if im.mode == "RGBA" else None) or (0, 0, im.width, im.height)
    except (OSError, ValueError):
        return 0, 0, 0, 0
    return box[0], box[1], box[2] - box[0], box[3] - box[1]


# ---------------------------------------------------------------------------
#  Shared helpers
# ---------------------------------------------------------------------------

class Ctx:
    """What every screen controller shares: the interface, the session and the helpers."""

    def __init__(self, app, session):
        """Bind the window and the settings."""
        self.app = app
        self.ui = app.Ui
        self.session = session
        self.posted = queue.Queue()
        self.toast_until = 0.0
        self.on_yes = None
        self.pick = None
        self.screens = {}
        self.ticks = 0
        self.busy = 0

    def post(self, fn) -> None:
        """Run `fn` on the UI thread at the next tick; safe from any thread."""
        self.posted.put(fn)

    def work(self, fn, done=None, failed=None) -> None:
        """Run `fn()` on a worker thread, then `done(result)` (or `failed(error)`) on the UI thread."""
        def _run():
            """The worker body: every failure is reported, never raised."""
            try:
                result = fn()
            except Exception as exc:
                self.post(lambda err=exc: self._finish((failed or self.fail), err))
                return
            self.post(lambda: self._finish(done, result))

        self.busy += 1
        threading.Thread(target=_run, daemon=True).start()

    def _finish(self, then, value) -> None:
        """A worker's job ended: count it done, then hand `value` to `then` (on the UI thread)."""
        self.busy -= 1
        if then is not None:
            then(value)

    def fail(self, exc) -> None:
        """Show an unexpected error."""
        self.info("Something went wrong", str(exc))

    def toast(self, text: str) -> None:
        """Show a short notice at the bottom of the window."""
        self.ui.toast = text
        self.toast_until = time.monotonic() + TOAST_SECONDS

    def info(self, title: str, text: str) -> None:
        """Show a message with an OK button."""
        self.ui.info_title, self.ui.info_text = title, text
        self.ui.dialog = "info"

    def confirm(self, title: str, text: str, yes: str, no: str, on_yes) -> None:
        """Ask a yes/no question; `on_yes()` runs only on yes."""
        self.ui.confirm_title, self.ui.confirm_text = title, text
        self.ui.confirm_yes, self.ui.confirm_no = yes, no
        self.on_yes = on_yes
        self.ui.dialog = "confirm"

    def open_pick(self, pick) -> None:
        """Show the list-picker dialog driven by `pick` (see `Pick`)."""
        self.pick = pick
        self.ui.pick_title, self.ui.pick_blurb = pick.title, pick.blurb
        self.ui.pick_ok, self.ui.pick_extra = pick.ok, pick.extra
        self.ui.pick_bulk = pick.bulk
        self.refresh_pick()
        self.ui.dialog = "pick"

    def refresh_pick(self) -> None:
        """Repaint the picker's rows and warning."""
        self.ui.pick_rows = model(self.pick.rows())
        self.ui.pick_warn = self.pick.warning()

    def go(self, view: str) -> None:
        """Show one step."""
        self.ui.view = view
        screen = self.screens.get(view)
        if screen is not None and hasattr(screen, "shown"):
            screen.shown()

    def pick_folder(self, title: str) -> str:
        """A folder the user picks, or ""."""
        return native.pick_folder(title, native.find_window("T.E.SR.A.C.T"))

    def pick_file(self, title: str, filters: list) -> str:
        """A file the user picks, or ""."""
        return native.pick_file(title, filters, native.find_window("T.E.SR.A.C.T"))


class Pick:
    """A list-picker dialog's content; subclasses override what they need."""

    title, blurb, ok, extra, bulk = "", "", "Done", "", True

    def rows(self) -> list:
        """The rows as PickRow dicts."""
        return []

    def warning(self) -> str:
        """A warning under the list, or ""."""
        return ""

    def toggle(self, index: int, on: bool) -> None:
        """A row was ticked or unticked."""

    def set_all(self, on: bool) -> None:
        """All or None was pressed."""

    def accept(self, ctx) -> None:
        """The main button was pressed; close the dialog when done."""
        ctx.ui.dialog = ""

    def extra_clicked(self, ctx) -> None:
        """The extra button was pressed."""


def pick_row(name: str, checked: bool, detail: str = "", tag: str = "", enabled: bool = True) -> dict:
    """One PickRow."""
    return {"name": name, "detail": detail, "tag": tag, "checked": checked, "enabled": enabled}


def _bind_common(ctx) -> None:
    """Callbacks every dialog shares: closing, confirm answers, the picker."""
    ui = ctx.ui

    def _confirm(yes):
        """Close the question and act on yes."""
        ui.dialog = ""
        if yes and ctx.on_yes is not None:
            ctx.on_yes()

    def _pick_toggle(i, on):
        """Forward a tick and repaint."""
        ctx.pick.toggle(i, on)
        ctx.refresh_pick()

    def _pick_all(on):
        """Forward All/None and repaint."""
        ctx.pick.set_all(on)
        ctx.refresh_pick()

    ui.close_dialog = lambda: setattr(ui, "dialog", "")
    ui.confirm_answer = _confirm
    ui.pick_toggle = _pick_toggle
    ui.pick_all = _pick_all
    ui.pick_accept = lambda: ctx.pick.accept(ctx)
    ui.pick_extra_clicked = lambda: ctx.pick.extra_clicked(ctx)
    ui.go = ctx.go
    ui.open_link = lambda key: native.open_url(LINKS[key])
    ui.theme_changed = lambda mode: ctx.session.save(THEME_CONFIG_KEY, mode)


# ---------------------------------------------------------------------------
#  Advanced, navmesh, about
# ---------------------------------------------------------------------------

class Advanced:
    """The Advanced dialog: steps, mesh options, navmesh options, performance."""

    def __init__(self, ctx, steps_owner):
        """Wire the dialog; `steps_owner` (the Choose screen) supplies the automatic steps."""
        self.ctx = ctx
        self.owner = steps_owner
        ui, s = ctx.ui, ctx.session
        ui.adv_step = self._step
        ui.adv_steps_all = lambda on: self._set_steps([k for k, *_ in STEPS] if on else [])
        ui.adv_auto = self._auto
        ui.adv_mesh_folders = lambda: self.owner.open_mesh_folders()
        ui.set_parallax = self._parallax
        ui.set_tex_only = lambda on: setattr(s, "tex_only", on)
        ui.set_winding = lambda i: self._save("winding", ("auto", "on", "off")[i], "collisionWindingFix")
        ui.set_navgen = lambda i: self._save("navgen", ("corridor", "lattice")[i], "navmeshGenerator")
        ui.set_nav_cache = lambda on: self._save("cache_dl", on, "navmeshCacheDownload")
        ui.set_workers = lambda i: self._save("workers", i, "workers")
        ui.set_logs_kept = self._logs_kept
        ui.set_pack_default = lambda on: (self._save("pack_default", on, "packStepsDefaultOn"), self.owner.replan())

    def show(self) -> None:
        """Fill every tab from the session and open the dialog."""
        ui, s = self.ctx.ui, self.ctx.session
        self.paint_steps()
        ui.parallax = int(s.parallax)
        ui.parallax_note = "Only with Community Shaders or an ENB; without one, surfaces swim as you move"
        ui.tex_only = s.tex_only
        ui.winding = ("auto", "on", "off").index(s.winding)
        ui.navgen = ("corridor", "lattice").index(s.navgen) if s.navgen in ("corridor", "lattice") else 0
        ui.nav_cache = s.cache_dl
        ui.worker_options = model([f"Automatic ({s.worker_count()} of {s.cpu_max})"]
                                  + [str(n) for n in range(1, s.cpu_max + 1)])
        ui.workers = s.workers
        ui.logs_kept = str(s.cfg.get("logRunsKept", 20))
        ui.pack_default = s.pack_default
        ui.mesh_folders = self.owner.mesh_folder_label()
        ui.dialog = "advanced"

    def paint_steps(self) -> None:
        """The Steps tab: the next run's steps, ticked."""
        auto, manual = self.owner.auto_steps(), self.ctx.session.selection.steps
        on = set(manual) if manual is not None else set(auto)
        self.ctx.ui.adv_steps = model(pick_row(f"{i + 1}. {label.split('. ', 1)[-1]}", key in on,
                                               "owed" if key in auto else "")
                                      for i, (key, _f, label, *_r) in enumerate(STEPS))
        self.ctx.ui.adv_steps_note = ("Your choice for the ticked plugins." if manual is not None
                                      else "Automatic: only what the ticked plugins owe.")

    def _step(self, index: int, on: bool) -> None:
        """Tick or untick one step, which makes the choice manual."""
        current = self.owner.current_steps()
        key = STEPS[index][0]
        self._set_steps([k for k in current if k != key] + ([key] if on else []))

    def _set_steps(self, keys) -> None:
        """Make `keys` the manual step choice."""
        self.ctx.session.selection.steps = [k for k, *_ in STEPS if k in set(keys)]
        self.paint_steps()
        self.owner.replan()

    def _auto(self) -> None:
        """Go back to the automatic step choice."""
        self.ctx.session.selection.steps = None
        self.paint_steps()
        self.owner.replan()

    def _parallax(self, i: int) -> None:
        """Parallax on or off; textures-only needs it."""
        self._save("parallax", bool(i), PARALLAX_CONFIG_KEY)
        if not i:
            self.ctx.session.tex_only = False
            self.ctx.ui.tex_only = False

    def _logs_kept(self, text: str) -> None:
        """Save how many run logs to keep."""
        if text.strip().isdigit():
            self.ctx.session.save("logRunsKept", int(text))

    def _save(self, attr: str, value, key: str) -> None:
        """Set and save one setting."""
        setattr(self.ctx.session, attr, value)
        self.ctx.session.save(key, value)


class Navmesh:
    """The navmesh editor dialog: the server, its pins folder, re-running Import."""

    def __init__(self, ctx, start_import):
        """Wire the dialog; `start_import(plugin)` runs Import for one plugin."""
        self.ctx = ctx
        self.server = navmesh_editor.EditorServer()
        self.start_import = start_import
        ui = ctx.ui
        ui.nav_toggle = lambda: (self.server.stop() if self.server.running() else self.server.start(), self.paint())
        ui.nav_open = lambda: native.open_url(self.server.url) if self.server.url else None
        ui.nav_pins = self._pins
        ui.nav_import = lambda: (setattr(ui, "dialog", ""), self.start_import(ui.nav_plugin))

    def show(self, plugin: str) -> None:
        """Open the dialog, starting the server if it is not running."""
        if not self.server.running():
            self.server.start()
        self.ctx.ui.nav_plugin = plugin
        self.paint()
        self.ctx.ui.dialog = "navmesh"

    def paint(self) -> None:
        """Copy the server's state into the dialog."""
        ui = self.ctx.ui
        ui.nav_state, ui.nav_tone = self.server.state()
        ui.nav_url, ui.nav_last = self.server.url, self.server.last
        ui.pins_dir = navmesh_pins_dir()

    def _pins(self) -> None:
        """Pick another pins folder; a running server restarts to use it."""
        folder = self.ctx.pick_folder("Folder for your navmesh pins")
        if not folder:
            return
        save_setting(NAVMESH_PINS_CONFIG_KEY, folder)
        if self.server.running():
            self.server.stop()
            self.server.start()
        self.paint()


def _bind_about(ctx) -> None:
    """The About and versions dialogs."""
    ui = ctx.ui
    current = version_info.current_version()
    ui.about_version = current + (" (development build)" if version_info.is_dev_version(current) else "")
    ui.versions = model({"k": k, "v": v} for k, v in VERSIONS)

    def _check():
        """Ask GitHub for a newer release."""
        ctx.toast("Checking for updates…")
        ctx.work(lambda: version_info.check_for_update(), _checked)

    def _checked(result):
        """Report the update check."""
        if not result["reachable"]:
            ctx.toast("Could not reach GitHub")
        elif result["available"]:
            ui.update_version = result["latest"]
            ctx.toast(f"{result['latest']} is available")
        else:
            ctx.toast("You have the newest version")

    ui.check_updates = _check
    ctx.work(lambda: version_info.check_for_update(), lambda r: setattr(ui, "update_version",
                                                                         r["latest"] if r["available"] else ""))


# ---------------------------------------------------------------------------
#  Building the window
# ---------------------------------------------------------------------------

def paint_rail(ctx) -> None:
    """The five steps with each one's state and one-line summary."""
    rows = []
    for i, (key, title) in enumerate(RAIL):
        screen = ctx.screens.get(key)
        state, sub = screen.rail_state() if screen is not None else ("idle", "")
        rows.append({"key": key, "num": str(i + 1), "title": title, "sub": sub, "state": state})
    ctx.ui.rail = model(rows)


def _collect_garbage(ctx) -> None:
    """Free unreachable objects on the UI thread: Slint aborts when a worker thread frees one of its objects.

    See: docs/commentary/core_gui.md#threads
    """
    ctx.ticks += 1
    gc.collect(0 if ctx.ticks % FULL_GC_TICKS else 2)


def _tick(ctx, drops) -> None:
    """Run posted work, expire the notice, take dropped files, advance the run."""
    _collect_garbage(ctx)
    while not ctx.posted.empty():
        ctx.posted.get_nowait()()
    if ctx.ui.toast and time.monotonic() > ctx.toast_until:
        ctx.ui.toast = ""
    paths = drops.take()
    if paths:
        ctx.screens["choose"].add_mod(paths[0])
    ctx.screens["convert"].tick()
    if ctx.ui.dialog == "navmesh":
        ctx.screens["navmesh"].paint()


def build():
    """Create the window and every screen; returns (app, ctx, timer, drops)."""
    from core.gui.choose_view import ChooseView
    from core.gui.install_view import InstallView
    from core.gui.loadorder_view import LoadOrderView
    from core.gui.run_view import RunView
    from core.gui.setup_view import SetupView

    app = slint.load_file(UI_FILE).App()
    write_default_config()
    session = Session()
    ctx = Ctx(app, session)
    ui = ctx.ui
    ui.version = version_info.current_version()
    ui.output_path = str(session.out_root())
    app.Theme.os_dark = native.os_dark()
    app.Theme.mode = session.theme
    if BANNER.is_file():
        ui.banner = slint.Image.load_from_path(str(BANNER))
        ui.banner_x, ui.banner_y, ui.banner_w, ui.banner_h = banner_clip(BANNER)
    _bind_common(ctx)
    run = RunView(ctx)
    choose = ChooseView(ctx, run)
    ctx.screens.update(setup=SetupView(ctx), choose=choose, convert=run, loadorder=LoadOrderView(ctx, run),
                       install=InstallView(ctx))
    ctx.screens["advanced"] = Advanced(ctx, choose)
    ctx.screens["navmesh"] = Navmesh(ctx, choose.run_import_for)
    ui.open_dialog = lambda name: _open_dialog(ctx, name)
    _bind_about(ctx)
    drops = native.DropTarget()
    timer = slint.Timer()
    timer.start(slint.TimerMode.Repeated, datetime.timedelta(milliseconds=TICK_MS), lambda: _tick(ctx, drops))
    return app, ctx, timer, drops


def _start_checks(ctx) -> None:
    """The first checks: Setup's, which go on to fill every other screen, and the old-version clean-up."""
    ctx.screens["setup"].refresh(first=True)
    ctx.work(exe_update.clean_after_update)


def _open_dialog(ctx, name: str) -> None:
    """Open a dialog the interface asks for by name."""
    if name == "advanced":
        ctx.screens["advanced"].show()
    elif name == "navmesh":
        ctx.screens["navmesh"].show(ctx.screens["choose"].first_ticked())
    elif name == "mode":
        ctx.screens["choose"].open_mode_dialog()
    else:
        ctx.ui.dialog = name


def _hook(timer, drops, splash) -> None:
    """Once Windows has created the window: its icon, files dropped on it, and the splash closes."""
    hwnd = native.find_window("T.E.SR.A.C.T")
    if hwnd:
        timer.stop()
        native.set_window_icon(hwnd, ICON)
        drops.attach(hwnd)
        splash.close()


def _load_before_showing(ctx) -> None:
    """Start the first checks and run what they post until every job is done (or STARTUP_WAIT passes).

    See: docs/commentary/core_gui.md#splash
    """
    _start_checks(ctx)
    deadline = time.monotonic() + STARTUP_WAIT
    while (ctx.busy or not ctx.posted.empty()) and time.monotonic() < deadline:
        try:
            fn = ctx.posted.get(timeout=TICK_MS / 1000)
        except queue.Empty:
            continue
        fn()
        _collect_garbage(ctx)


def run_window(splash) -> int:
    """Build the window and its data behind `splash`, show it finished, and run until it is closed.

    Stops the run and the editor server on the way out.
    """
    gc.disable()
    app, ctx, timer, drops = build()
    gc.freeze()
    _load_before_showing(ctx)
    app.show()
    hooked = slint.Timer()
    hooked.start(slint.TimerMode.Repeated, datetime.timedelta(milliseconds=TICK_MS),
                 lambda: _hook(hooked, drops, splash))
    app.run()
    timer.stop()
    ctx.screens["convert"].cancel()
    ctx.screens["navmesh"].server.stop()
    return 0
