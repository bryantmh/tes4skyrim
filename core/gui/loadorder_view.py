"""Step 4, Load order: what is built once for every converted game together.

Each mod here reads as up to date while its stamp (runner) still matches what it was built
from. Distant LOD bakes one pass over every worldspace, so it waits until every plugin that
changes a worldspace is up to date: building earlier bakes their old versions.
See: docs/commentary/core_gui.md#load-order
"""

from asset_convert.lod.mesh_decimate import LOD_DETAIL_DEFAULT
from core.gui import journal, loadorder, runner
from core.gui.config import LOD_DETAIL_CONFIG_KEY, lod_detail_labels
from core.gui.session import INCLUDE_PREFIX
from core.gui.window import Pick, model, paint_rail, pick_row
from output_layout import FINISHED_DIR_NAME

#: The load-order mods: (key, actions run in order, title, detail, optional?, recommended tag, action button).
ITEMS = (
    ("runtime", ("package_runtime_dll",), "SKSE runtime",
     "TESRuntime, CreatureRuntime, FalloutRuntime, HavokWorldSize and MorrowindRuntime", False, "", ""),
    ("body", ("modify_body_meshes",), "Body slot patch", "Fixes body slot 44 for your Skyrim plugins", True, "",
     "Plugins"),
    ("start", ("package_start_mod",), "Start mod", "New Game asks which converted world to start in", True, "", ""),
    ("journal", (), "Quest journal stage text", "Click a quest objective to read the text its stage had",
     True, "Recommended for Morrowind", ""),
    ("masters", ("make_master",), "Masters",
     "Flag big plugins ESM so their references load per cell; past 1,048,576 always-loaded references the game "
     "hangs on the main menu", True, "Recommended", "Choose"),
    ("lod", ("create_lod", "pack_lod"), "Distant LOD", "One mod for every worldspace", True, "", "Order & detail"),
)
#: Optional extras, built only when turned on.
EXTRAS = (("ui", ("convert_ui",), "Oblivion UI", "Message boxes and menu cursor with Oblivion's art", True, "",
           "Set up"),)

# ---------------------------------------------------------------------------
#  The screen
# ---------------------------------------------------------------------------

class LoadOrderView:
    """The Load order screen."""

    def __init__(self, ctx, run):
        """Wire the screen; statuses are read when the plugins are known."""
        self.ctx = ctx
        self.run = run
        self.status = {}
        self.owners = {}
        self.plugin_status = {}
        self.queue = []
        self.lod = None
        ui = ctx.ui
        ui.lo_include = self._include
        ui.lo_action = self._action
        ui.build_ready = lambda: self.build(self.ready_keys())
        ui.lod_update_waiting = lambda: ctx.screens["choose"].mark_for_update(self.waiting_plugins())
        ui.lod_build_anyway = lambda: self.build(["lod"])
        ui.lod_set_detail = self._detail
        ui.lod_toggle_plugin = lambda i, on: (self.lod.toggle_plugin(i, on), self._paint_lod())
        ui.lod_move = lambda a, b: (self.lod.move(a, b), self._paint_lod())
        ui.lod_toggle_world = lambda i, on: (self.lod.toggle_world(self.lod.worlds()[i][0], on), self._paint_lod())
        ui.lod_set_force = lambda on: (setattr(self.lod, "force", on), self._paint_lod())
        ui.lod_reset = self._lod_reset
        ui.lod_accept = self._lod_accept
        ui.lod_pack_only = lambda: (setattr(ui, "dialog", ""), self._run([("pack_lod", runner.global_cmd(
            ctx.session, "pack_lod"))]))

    def included(self, key: str) -> bool:
        """Whether a load-order mod is built and installed."""
        return key == "runtime" or self.ctx.session.included(key)

    def plugins_changed(self, rows: dict) -> None:
        """The plugin statuses changed: reread what is up to date."""
        self.plugin_status = {name: row["status"] for name, row in rows.items()}
        self.refresh()

    def refresh(self) -> None:
        """Read every mod's status and the worldspace owners on a worker, then paint."""
        session = self.ctx.session
        stamps = self.run.last_stamp

        def _read():
            """Which actions are current, and which plugins ship LOD for each worldspace."""
            current = {}
            for _k, actions, *_r in ITEMS + EXTRAS:
                for action in actions:
                    current[action] = runner.global_is_current(session, action, stamps)
            journal_zip = session.out_root() / FINISHED_DIR_NAME / f"{journal.MOD_NAME}.zip"
            return current, journal_zip.exists(), loadorder.worldspace_owners(session)

        self.ctx.work(_read, self._read_done)

    def _read_done(self, got) -> None:
        """Paint what `refresh` read."""
        current, journal_built, self.owners = got
        self.status = {key: (journal_built if key == "journal" else all(current[a] for a in actions))
                       for key, actions, *_r in ITEMS + EXTRAS}
        self.paint()

    def waiting_plugins(self) -> list:
        """Plugins that change a worldspace and are out of date."""
        return sorted({p for owners in self.owners.values() for p in owners if self.plugin_status.get(p) == "out"})

    def ready_keys(self) -> list:
        """Included mods that are out of date and can be built now (LOD waits for its plugins)."""
        return [key for key, *_r in ITEMS + EXTRAS if self.included(key) and not self.status.get(key, False)
                and (key != "lod" or not self.waiting_plugins())]

    def paint(self) -> None:
        """The two lists, the worldspaces and the build button."""
        ui = self.ctx.ui
        ui.lo_items = model(self._item(row) for row in ITEMS)
        ui.lo_extras = model(self._item(row) for row in EXTRAS)
        worlds = []
        for world, owners in sorted(self.owners.items()):
            out = [p for p in owners if self.plugin_status.get(p) == "out"]
            names = ", ".join(owners[:3]) + (f" and {len(owners) - 3} more" if len(owners) > 3 else "")
            worlds.append({"name": world, "plugins": names, "status": f"{len(out)} to update" if out else "Ready",
                           "tone": "warn" if out else "good"})
        ui.worlds = model(worlds)
        waiting = self.waiting_plugins()
        ui.lod_warn = (f"{len(waiting)} plugin{'s' if len(waiting) != 1 else ''} that change a worldspace "
                       "still need updating. Building now bakes their old versions into LOD." if waiting else "")
        ready = self.ready_keys()
        ui.build_label = f"Build {len(ready)} ready" if ready else "All built"
        ui.build_enabled = bool(ready)
        paint_rail(self.ctx)

    def _item(self, row) -> dict:
        """One LoadItem."""
        key, _actions, title, detail, optional, tag, action = row
        on = self.included(key)
        built = self.status.get(key, False)
        status, tone = ("Up to date", "good") if built else ("Off", "idle") if not on else (
            ("Waiting", "warn") if key == "lod" and self.waiting_plugins() else ("Out of date", "warn"))
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
        """Build `keys` one after another: their commands, then the journal patch (done in-process)."""
        if self.run.busy() or not keys:
            return
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
        self.run.start_commands([(runner.global_label(a), cmd, a) for a, cmd in items], "Building the load order",
                                record)

    def _next(self) -> None:
        """Start what is queued after the commands (the journal patch)."""
        if not self.queue:
            return
        self.queue.pop(0)
        session = self.ctx.session
        self.run.start_task("Quest journal stage text", lambda log: self._journal(session, log))

    @staticmethod
    def _journal(session, log) -> None:
        """Patch the journal movies the game loads into a mod zip."""
        results, zip_path = journal.build_journal_mod(session.tes5, session.out_root())
        for line in journal.report_body(results, zip_path).splitlines():
            log(line)
        headline, _tone = journal.report_status([outcome for outcome, _ in results])
        if zip_path is None:
            raise RuntimeError(headline)

    def run_finished(self) -> None:
        """A run ended: continue the queue, else reread the statuses."""
        if self.queue and self.run.code == 0:
            self._next()
            return
        self.queue = []
        self.refresh()

    def _open_lod(self, pick) -> None:
        """Show the LOD dialog."""
        self.lod = pick
        ui = self.ctx.ui
        ui.lod_details = model(lod_detail_labels())
        session = self.ctx.session
        ui.lod_detail = session.lod_detail if session.lod_detail >= 0 else LOD_DETAIL_DEFAULT
        ui.lod_force = False
        self._paint_lod()
        ui.dialog = "lod"

    def _paint_lod(self) -> None:
        """The LOD dialog's two lists."""
        ui = self.ctx.ui
        ui.lod_plugins = model(pick_row(n, on, "", "", enabled) for n, on, enabled in self.lod.plugin_rows())
        ui.lod_worlds = model(pick_row(w, on) for w, on in self.lod.worlds())
        ui.lod_why = self.lod.why_empty()

    def _detail(self, index: int) -> None:
        """Save the LOD detail preset."""
        self.ctx.session.lod_detail = index
        self.ctx.session.save(LOD_DETAIL_CONFIG_KEY, index)

    def _lod_reset(self) -> None:
        """Back to the computed order and every worldspace."""
        self.ctx.session.selection.lod_plugins.clear()
        self.ctx.session.selection.lod_worldspaces.clear()
        self.ctx.work(lambda: loadorder.LodPick(self.ctx.session), self._open_lod)

    def _lod_accept(self) -> None:
        """Keep the LOD choice for the next build."""
        problem = self.lod.confirm(self.ctx.session)
        if problem:
            self.ctx.toast(problem)
            return
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
