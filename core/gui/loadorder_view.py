"""Step 4, Shared mods: what is built once for every converted game together.

Each mod here reads as up to date while its stamp (runner) still matches what it was built
from. Distant LOD builds the ticked worldspaces, the out-of-date ones unless the user changes the ticks,
and asks first when one of their plugins is itself out of date.
See: docs/commentary/core_gui.md#load-order
See: docs/commentary/core_gui.md#distant-lod-worldspaces
"""

from asset_convert.lod.mesh_decimate import LOD_DETAIL_DEFAULT
from core.gui import journal, loadorder, runner
from core.gui.config import LOD_DETAIL_CONFIG_KEY, lod_detail_labels
from core.gui.session import INCLUDE_PREFIX, MASTERS_CONFIG_KEY
from core.gui.window import Pick, model, paint_rail, pick_row

#: The load-order mods: (key, actions run in order, title, detail, optional?, recommended tag, action button).
ITEMS = (
    ("runtime", ("package_runtime_dll",), "SKSE runtime",
     "TESRuntime, ArchiveLoader, CreatureRuntime, FalloutRuntime, HavokWorldSize and MorrowindRuntime", False, "", ""),
    ("body", ("modify_body_meshes",), "Body patch", "Creates a patch for the Skyrim NPC model and all your Skyrim plugins to enable seamless pants, pauldrons, and split gauntlets. Must be last in your load order", True, "",
     "Plugins"),
    ("start", ("package_start_mod",), "Start mod", "A plugin that allows choosing which converted game to start, and gives your player an Elder Scroll to allow switching between games in one save", True, "", ""),
    ("journal", (), "Quest journal stage text", "Click a quest objective to read the text its stage had",
     True, "Recommended for Morrowind", ""),
    ("masters", ("make_master",), "Masters",
     "Flag big plugins ESM so their references load per cell; past 1,048,576 always-loaded references the game "
     "hangs on the main menu", True, "Recommended", "Choose"),
    ("lod", ("create_lod",), "Distant LOD", "One mod for every worldspace; builds the ticked ones", True, "",
     "Order & detail"),
)
#: Optional extras, built only when turned on.
EXTRAS = (("ui", ("convert_ui",), "Oblivion UI", "Message boxes and menu cursor with Oblivion's art", True, "",
           "Set up"),)

# ---------------------------------------------------------------------------
#  The screen
# ---------------------------------------------------------------------------

class LoadOrderView:
    """The Shared mods screen."""

    def __init__(self, ctx, run):
        """Wire the screen; statuses are read when the plugins are known."""
        self.ctx = ctx
        self.run = run
        self.status = {}
        self.plugin_status = {}
        self.reading = False
        self.queue = []
        self.lod = None
        self.edit = None
        self.world_rows = []
        self.world_ticks = {}
        ui = ctx.ui
        ui.lo_include = self._include
        ui.lo_action = self._action
        ui.build_ready = self._build_ready
        ui.lod_tick_world = lambda i, on: self._tick_worlds([self.world_rows[i][0]], on)
        ui.lod_all_worlds = lambda on: self._tick_worlds([w for w, *_r in self.world_rows], on)
        ui.lod_set_detail = self._detail
        ui.lod_toggle_plugin = lambda i, on: self._lod_change(self.edit.toggle_plugin, i, on)
        ui.lod_move = lambda a, b: self._lod_change(self.edit.move, a, b)
        ui.lod_all_plugins = lambda on: self._lod_change(self.edit.all_plugins, on)
        ui.lod_set_force = lambda on: self._lod_change(setattr, self.edit, "force", on)
        ui.lod_reset = self._lod_reset
        ui.lod_accept = self._lod_accept

    def included(self, key: str) -> bool:
        """Whether a load-order mod is built and installed."""
        return key == "runtime" or self.ctx.session.included(key)

    def plugins_changed(self, rows: dict) -> None:
        """New plugin statuses: reread what is up to date if they changed, else repaint (a read running paints)."""
        known, self.plugin_status = self.plugin_status, {name: row["status"] for name, row in rows.items()}
        if self.reading:
            return
        if known and known != self.plugin_status:
            self.refresh()
        else:
            self.paint()

    def refresh(self) -> None:
        """Read every mod's status and each worldspace's LOD state on a worker, then paint."""
        session = self.ctx.session
        stamps = self.run.last_stamp

        def _read():
            """Which actions are current, the LOD choice, and each worldspace's plugins and staleness."""
            current = {}
            for _k, actions, *_r in ITEMS + EXTRAS:
                for action in actions:
                    current[action] = runner.global_is_current(session, action, stamps)
            built = (session.out_root() / journal.MOD_NAME).is_dir()
            pick = loadorder.LodPick(session)
            return current, built, pick, loadorder.world_rows(session, pick)

        self.reading = True
        self.ctx.work(_read, self._read_done)

    def _read_done(self, got) -> None:
        """Paint what `refresh` read; LOD reads the saved plugin choice, and is up to date when no worldspace is stale."""
        self.reading = False
        current, journal_built, self.lod, self.world_rows = got
        if self.ctx.session.selection.lod_order:
            self.ctx.session.selection.lod_plugins[:] = self.lod.checked()
        self.status = {key: (journal_built if key == "journal" else all(current[a] for a in actions))
                       for key, actions, *_r in ITEMS + EXTRAS}
        self.status["lod"] = not any(stale for *_r, stale in self.world_rows)
        self.paint()

    def ticked_worlds(self) -> list:
        """The worldspaces Distant LOD builds: the user's ticks, else the out-of-date ones."""
        return [w for w, _owners, stale in self.world_rows if self.world_ticks.get(w, stale)]

    def waiting_plugins(self) -> list:
        """Out-of-date plugins that ship LOD for a ticked worldspace."""
        ticked = set(self.ticked_worlds())
        return sorted({p for w, owners, _s in self.world_rows if w in ticked for p in owners
                       if self.plugin_status.get(p) == "out"})

    def _wants_build(self, key: str) -> bool:
        """Whether an included mod has work: LOD when a worldspace is ticked, the rest when out of date."""
        return bool(self.ticked_worlds()) if key == "lod" else not self.status.get(key, False)

    def ready_keys(self) -> list:
        """Included mods with work to do."""
        return [key for key, *_r in ITEMS + EXTRAS if self.included(key) and self._wants_build(key)]

    def paint(self) -> None:
        """The two lists, the worldspaces and the build button."""
        ui = self.ctx.ui
        ui.lo_items = model(self._item(row) for row in ITEMS)
        ui.lo_extras = model(self._item(row) for row in EXTRAS)
        ticked = set(self.ticked_worlds())
        ui.worlds = model(self._world(w, owners, stale, w in ticked) for w, owners, stale in self.world_rows)
        ui.lod_why = self.lod.why_empty() if self.lod is not None and not self.world_rows else ""
        ready = self.ready_keys()
        ui.build_label = f"Build {len(ready)} ready" if ready else "All built"
        ui.build_enabled = bool(ready)
        paint_rail(self.ctx)

    def _world(self, world: str, owners: list, stale: bool, ticked: bool) -> dict:
        """One WorldRow: its plugins, and why it needs building (its own out-of-date plugins first)."""
        out = sum(1 for p in owners if self.plugin_status.get(p) == "out")
        names = ", ".join(owners[:3]) + (f" and {len(owners) - 3} more" if len(owners) > 3 else "")
        status, tone = ((f"{out} plugin{'s' if out != 1 else ''} out of date", "warn") if out else
                        ("Out of date", "warn") if stale else ("Up to date", "good"))
        return {"name": world, "plugins": names, "status": status, "tone": tone, "checked": ticked}

    def _tick_worlds(self, worlds: list, on: bool) -> None:
        """Tick or untick worldspaces until the next run ends."""
        self.world_ticks.update(dict.fromkeys(worlds, on))
        self.paint()

    def _build_ready(self) -> None:
        """Build every ready mod, first asking when a ticked worldspace's plugins are out of date."""
        keys = self.ready_keys()
        waiting = self.waiting_plugins() if "lod" in keys else []
        if not waiting:
            self.build(keys)
            return
        many = len(waiting) != 1
        names = ", ".join(waiting[:4]) + (f" and {len(waiting) - 4} more" if len(waiting) > 4 else "")
        self.ctx.confirm(
            "Plugins out of date",
            f"{len(waiting)} plugin{'s' if many else ''} that change{'' if many else 's'} a ticked worldspace "
            f"{'are' if many else 'is'} out of date: {names}.\n\nBuilding now bakes the old "
            f"version{'s' if many else ''} into distant LOD.",
            "Build anyway", "Cancel", lambda: self.build(keys),
            alt="Update them first", on_alt=lambda: self.ctx.screens["choose"].mark_for_update(waiting))

    def _item(self, row) -> dict:
        """One LoadItem."""
        key, _actions, title, detail, optional, tag, action = row
        on = self.included(key)
        built = self.status.get(key, False)
        status, tone = ("Up to date", "good") if built else ("Off", "idle") if not on else ("Out of date", "warn")
        if key == "lod" and on and self.ticked_worlds():
            count = len(self.ticked_worlds())
            status, tone = f"{count} to build", "warn"
        if key == "masters":
            chosen = self.ctx.session.selection.master_plugins
            detail = ("Flag: " + ", ".join(chosen[:4]) + (" …" if len(chosen) > 4 else "")) if chosen else detail
        return {"key": key, "title": title, "detail": detail, "status": status, "tone": tone, "include": on,
                "toggle": optional, "tag": tag, "action": action}

    def rail_state(self) -> tuple:
        """(state, summary) for the rail."""
        stale = [key for key, *_r in ITEMS + EXTRAS if self.included(key) and not self.status.get(key, False)]
        if not self.status:
            return "idle", "Reading…"
        return ("warn", f"{len(stale)} out of date") if stale else ("done", "Up to date")

    def _include(self, key: str, on: bool) -> None:
        """Turn a mod on or off."""
        self.ctx.session.save(INCLUDE_PREFIX + key, on)
        self.paint()

    def _action(self, key: str) -> None:
        """A mod's own button: its dialog."""
        if key == "body":
            loadorder.refresh_patch_plugins(self.ctx.session)
            self.ctx.open_pick(BodyPatch(self.ctx.session))
        elif key == "masters":
            self.ctx.work(lambda: loadorder.MasterPick(self.ctx.session),
                          lambda pick: self.ctx.open_pick(Masters(pick, self)))
        elif key == "lod":
            self.ctx.work(lambda: loadorder.LodPick(self.ctx.session), self._open_lod)
        elif key == "ui":
            self.ctx.open_pick(OblivionUi(self.ctx.session, self))

    def build(self, keys: list) -> None:
        """Build `keys` one after another (LOD: the ticked worldspaces), then the journal patch, in-process."""
        if self.run.busy() or not keys:
            return
        self.ctx.session.selection.lod_worldspaces[:] = self.ticked_worlds()
        items = [(a, runner.global_cmd(self.ctx.session, a)) for key, actions, *_r in ITEMS + EXTRAS
                 if key in keys for a in actions]
        self.queue = ["journal"] if "journal" in keys else []
        if items:
            self._run(items)
        elif self.queue:
            self._next()

    def _run(self, items: list) -> None:
        """Run load-order actions as one run, recording each that succeeds."""
        record = {a: (lambda a=a: self.run.record_global(a)) for a, _c in items}
        self.run.start_commands([(runner.global_label(a), cmd, a) for a, cmd in items], "Building the shared mods",
                                record)

    def _next(self) -> None:
        """Start what is queued after the commands (the journal patch)."""
        if not self.queue:
            return
        self.queue.pop(0)
        session = self.ctx.session
        self.run.start_task("Quest journal stage text", lambda log: self._journal(session, log), foreground=True)

    @staticmethod
    def _journal(session, log) -> None:
        """Patch the journal movies the game loads into a mod folder."""
        results, folder = journal.build_journal_mod(session.tes5, session.out_root())
        for line in journal.report_body(results, folder).splitlines():
            log(line)
        headline, _tone = journal.report_status([outcome for outcome, _ in results])
        if folder is None:
            raise RuntimeError(headline)

    def run_finished(self) -> None:
        """A run ended: continue the queue, else drop the user's worldspace ticks and reread the statuses."""
        if self.queue and self.run.code == 0:
            self._next()
            return
        self.queue = []
        self.world_ticks = {}
        self.refresh()

    def _open_lod(self, pick) -> None:
        """Show the LOD dialog."""
        self.edit = pick
        ui = self.ctx.ui
        ui.lod_details = model(lod_detail_labels())
        session = self.ctx.session
        ui.lod_detail = session.lod_detail if session.lod_detail >= 0 else LOD_DETAIL_DEFAULT
        self._paint_lod()
        ui.dialog = "lod"

    def _paint_lod(self) -> None:
        """The LOD dialog's plugin list and its force tick."""
        ui = self.ctx.ui
        ui.lod_force = self.edit.force
        ui.lod_plugins = model(pick_row(n, on, "", "", enabled) for n, on, enabled in self.edit.plugin_rows())

    def _detail(self, index: int) -> None:
        """Save the LOD detail preset."""
        self.ctx.session.lod_detail = index
        self.ctx.session.save(LOD_DETAIL_CONFIG_KEY, index)

    def _lod_change(self, change, *args) -> None:
        """Apply one change to the LOD choice, then repaint the dialog."""
        change(*args)
        self._paint_lod()

    def _lod_reset(self) -> None:
        """Back to the computed order, every plugin and no Force."""
        self.ctx.session.selection.lod_plugins.clear()
        self.ctx.session.selection.lod_off.clear()
        self.ctx.session.selection.lod_order.clear()
        self.ctx.session.selection.lod_force = False
        self.ctx.work(lambda: loadorder.LodPick(self.ctx.session), self._reset_read)

    def _reset_read(self, pick) -> None:
        """Show the rescanned choice, if the dialog is still open (never reopen one the user closed)."""
        if self.ctx.ui.dialog == "lod":
            self.edit = pick
            self._paint_lod()

    def _lod_accept(self) -> None:
        """Keep the LOD choice for the next build and the next launch."""
        problem = self.edit.confirm(self.ctx.session)
        if problem:
            self.ctx.toast(problem)
            return
        self.ctx.session.save_lod_choice()
        self.ctx.ui.dialog = ""
        self.refresh()


# ---------------------------------------------------------------------------
#  Pickers
# ---------------------------------------------------------------------------

class BodyPatch(Pick):
    """Which Skyrim plugins the body slot patch covers."""

    title, blurb = "Body slot patch", "Your active Skyrim plugins are ticked."

    def __init__(self, session):
        """Use the session's list (refreshed from the load order)."""
        self.session = session

    def rows(self) -> list:
        """One row per Skyrim plugin."""
        return [pick_row(n, on) for n, on in self.session.selection.patch_plugins]

    def toggle(self, index, on) -> None:
        """Tick or untick a plugin."""
        pairs = self.session.selection.patch_plugins
        pairs[index] = (pairs[index][0], on)

    def set_all(self, on) -> None:
        """All or none."""
        self.session.selection.patch_plugins = [(n, on) for n, _ in self.session.selection.patch_plugins]

    def warning(self) -> str:
        """No plugin means nothing to patch."""
        return "" if any(on for _n, on in self.session.selection.patch_plugins) else "Tick at least one plugin."


class Masters(Pick):
    """Which converted plugins to flag ESM."""

    title = "Masters"
    blurb = "Ticking a plugin ticks the masters it depends on: a master loads first, so an ESM can't master a plain ESP."
    ok = "Apply"

    def __init__(self, pick, view):
        """Show what `loadorder.MasterPick` read."""
        self.pick, self.view = pick, view

    def rows(self) -> list:
        """One row per converted plugin, in dependency order."""
        return [pick_row(n, on, detail, tag) for n, detail, tag, on in self.pick.rows()]

    def toggle(self, index, on) -> None:
        """Tick a plugin and its masters, or untick it and its dependents."""
        self.pick.toggle(self.pick.names[index], on)

    def set_all(self, on) -> None:
        """All or none."""
        self.pick.set_all(on)

    def warning(self) -> str:
        """A master left behind."""
        return self.pick.problems()

    def accept(self, ctx) -> None:
        """Keep the choice and include masters in the next build."""
        if self.pick.problems():
            return
        ctx.session.selection.master_plugins[:] = self.pick.chosen()
        ctx.session.save(MASTERS_CONFIG_KEY, self.pick.chosen())
        ctx.session.save(INCLUDE_PREFIX + "masters", bool(self.pick.chosen()))
        ctx.ui.dialog = ""
        self.view.refresh()


class OblivionUi(Pick):
    """Which Skyrim menus get Oblivion's art."""

    title, blurb, ok, bulk = "Oblivion UI", "Reads your Oblivion install. Nothing Bethesda ships is redistributed.", \
        "Turn on", False

    #: (selection key, label, file, tag).
    MOVIES = (("messagebox", "Message boxes", "messagebox.swf", ""),
              ("cursor", "Menu cursor", "cursormenu.swf", "Experimental"))

    def __init__(self, session, view):
        """Edit the session's choice."""
        self.session, self.view = session, view

    def rows(self) -> list:
        """One row per menu."""
        chosen = self.session.selection.convert_ui
        return [pick_row(label, chosen[key], file, tag) for key, label, file, tag in self.MOVIES]

    def toggle(self, index, on) -> None:
        """Tick or untick a menu."""
        self.session.selection.convert_ui[self.MOVIES[index][0]] = on

    def warning(self) -> str:
        """Nothing ticked builds nothing."""
        return "" if any(self.session.selection.convert_ui.values()) else "Tick at least one menu."

    def accept(self, ctx) -> None:
        """Turn the Oblivion UI on with this choice."""
        if self.warning():
            return
        ctx.session.save(INCLUDE_PREFIX + "ui", True)
        ctx.ui.dialog = ""
        self.view.paint()
