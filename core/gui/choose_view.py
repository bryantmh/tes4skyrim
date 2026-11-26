"""Step 2, Choose: the games, each game's plugins, the Morrowind mode, adding mods, and the run.

Every game's plugins are read once on a worker thread (status needs the version state). Ticks
are kept by plugin name, so filtering and searching never lose them. A Morrowind game asks for
its mode before anything else, and is refused outright on a Skyrim older than 1.6.
See: docs/commentary/core_gui.md#choose
"""

import os

from asset_convert.sources.source_registry import label_for_directory
from core.gui import eta, games, mods, morrowind, runner
from core.gui.config import STEPS, scan_mesh_subdirs
from core.gui.window import Pick, model, paint_rail, pick_row

#: Plugin statuses as the list shows them: (label, tone).
STATUS = {"new": ("Not converted", "idle"), "out": ("Out of date", "warn"), "ok": ("Up to date", "good")}
#: The filter chips, in order: (key, label).
FILTERS = (("all", "All"), ("done", "Converted"), ("out", "Out of date"), ("new", "Not converted"),
           ("arch", "From archives"), ("on", "Ticked"))
#: conversion_config.json key: the game shown last.
LAST_GAME_KEY = "lastGame"
#: Archive types the add-a-mod picker offers.
ARCHIVES = [("Mod archives", "*.7z;*.zip;*.rar"), ("All files", "*.*")]

# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

def mod_names(mod: dict) -> list:
    """The names `mods.remove` takes for an added mod: its plugins, else the name it registered under."""
    return mod["plugins"] or [mod["entry"].get("plugin") or mod["label"]]


# ---------------------------------------------------------------------------
#  The screen
# ---------------------------------------------------------------------------

class ChooseView:
    """The Choose screen."""

    def __init__(self, ctx, run):
        """Wire the screen; games are read when Setup finishes."""
        self.ctx = ctx
        self.run = run
        self.games = []
        self.by_game = {}
        self.rows = {}
        self.filter_chosen = False
        self.game = None
        self.ticked = set()
        self.pending = set()
        self.import_pick = None
        self.plan_seconds = None
        self.planned = 0
        ui = ctx.ui
        ui.pick_game = lambda i: self.select(self.games[i])
        ui.add_game = self._add_game
        ui.remove_game = self._remove_game
        ui.filter_keys = model(key for key, _label in FILTERS)
        ui.set_filter = lambda f: self._narrow("filter", f)
        ui.set_query = lambda q: self._narrow("query", q)
        ui.toggle_plugin = self._toggle
        ui.tick_shown = self._tick_shown
        ui.add_archive = lambda: self.add_mod(ctx.pick_file("Add a mod archive", ARCHIVES))
        ui.add_folder = lambda: self.add_mod(ctx.pick_folder("Add a mod folder"))
        ui.manage_mods = self._manage
        ui.plugin_parts = self._parts
        ui.clear_plugin = self._clear
        ui.use_mode = self._use_mode
        ui.mode_switch = self._switch_mode
        ui.start_run = self.start
        ui.import_toggle_sub = self._import_sub
        ui.import_toggle_plugin = self._import_plugin
        ui.import_all = self._import_all
        ui.import_go = self._import_go


    def setup_changed(self) -> None:
        """Setup finished a check: read the games the first time (asking which to keep once), repaint after."""
        if self.games:
            self.paint_game()
            return
        self.reload()
        if not self.ctx.session.cfg.get(games.GAMES_ASKED_KEY):
            self.ctx.work(lambda: games.found_game_dirs(self.ctx.session.cfg), self._ask_games)

    def _ask_games(self, found: list) -> None:
        """Ask which of the game folders found to keep; with none found there is nothing to ask."""
        if found:
            self.ctx.open_pick(FoundGames(self, found))
        else:
            self.ctx.session.save(games.GAMES_ASKED_KEY, True)

    def reload(self, keep=None) -> None:
        """Read every game and its plugins on a worker, then show `keep` (or the last game)."""
        self.ctx.ui.scanning = True

        def _read():
            """Games, mods per game, and every game's plugin rows."""
            found = games.list_games(self.ctx.session.cfg)
            by_game = games.mods_by_game(self.ctx.session.cfg, found)
            return found, by_game, {g["id"]: games.plugin_rows(g, by_game.get(g["id"], [])) for g in found}

        self.ctx.work(_read, lambda got: self._loaded(*got, keep))

    def _loaded(self, found, by_game, rows, keep) -> None:
        """Show what the reload found."""
        self.games, self.by_game, self.rows = found, by_game, rows
        self.ctx.ui.scanning = False
        want = keep or self.ctx.session.cfg.get(LAST_GAME_KEY)
        pick = next((g for g in found if g["id"] == want), found[0] if found else None)
        if pick is not None:
            self.select(pick)
        self.ctx.screens["loadorder"].plugins_changed(self.all_rows())

    def all_rows(self) -> dict:
        """{plugin name: row} across every game."""
        return {r["name"]: r for rows in self.rows.values() for r in rows}

    def select(self, game: dict) -> None:
        """Show one game."""
        self.game = game
        if game["mod"] is None:
            games.select_game(game)
            self.ctx.session.save("tes4DataPath", game["path"])
        self.ctx.session.save(LAST_GAME_KEY, game["id"])
        rows = self.rows.get(game["id"], [])
        names = {r["name"] for r in rows}
        wanted = self.pending & names
        self.ticked = wanted or {r["name"] for r in rows if games.steps_for(r)
                                 and (r["status"] == "out" or (r["root"] and r["status"] == "new"))}
        self.pending -= wanted
        self.ctx.ui.pending_mode = morrowind.mode_index(morrowind.saved_mode(self.ctx.session.cfg))
        if not self.filter_chosen:
            self.ctx.ui.filter = "out" if wanted or any(r["status"] == "out" for r in rows) else "all"
        self.ctx.ui.query = ""
        self.paint_game()

    def paint_cards(self) -> None:
        """The game cards with each game's status."""
        cards = []
        for g in self.games:
            rows = self.rows.get(g["id"], [])
            out = sum(1 for r in rows if r["status"] == "out")
            done = sum(1 for r in rows if r["status"] != "new")
            status, tone = (("Not converted", "idle") if not done else
                            (f"{out} out of date", "warn") if out else ("Up to date", "good"))
            if g["tes3"] and not self.ctx.screens["setup"].morrowind_ok:
                status, tone = "Needs Skyrim 1.6+", "bad"
            sub = f"{len(rows)} plugins" + (" · added mod" if g["mod"] else "")
            if g["tes3"] and morrowind.saved_mode(self.ctx.session.cfg):
                sub += " · " + morrowind.mode_name(morrowind.saved_mode(self.ctx.session.cfg))
            cards.append({"key": g["id"], "name": g["name"], "sub": sub, "status": status, "tone": tone})
        self.ctx.ui.games = model(cards)
        self.ctx.ui.game = next((i for i, g in enumerate(self.games) if self.game and g["id"] == self.game["id"]), -1)

    def paint_game(self) -> None:
        """The selected game's header, mode question or plugin list, and the go bar."""
        self.paint_cards()
        ui, g = self.ctx.ui, self.game
        if g is None:
            return
        ui.game_name, ui.game_path = g["name"], g["path"] or "Added mod"
        mode = morrowind.saved_mode(self.ctx.session.cfg) if g["tes3"] else None
        ui.game_mode = morrowind.mode_name(mode) if mode else ""
        ui.game_error = ("Morrowind needs Skyrim 1.6 or later. Oblivion, Nehrim and Fallout games still work."
                         if g["tes3"] and not self.ctx.screens["setup"].morrowind_ok else "")
        ui.ask_mode = g["tes3"] and not morrowind.mode_asked(self.ctx.session.cfg)
        ui.mode_options = model(morrowind.mode_options())
        ui.mode_note = "Changeable later; converted Morrowind plugins then convert again."
        self.paint_list()
        paint_rail(self.ctx)

    def _add_game(self) -> None:
        """Register another game's Data folder."""
        folder = self.ctx.pick_folder("A game's Data folder")
        if folder:
            games.add_game(folder)
            self.reload()

    def _remove_game(self) -> None:
        """Ask, then stop listing the selected game: a Data folder is only unlisted, an added mod is removed."""
        game = self.game
        if game is None:
            return
        if game["mod"] is None:
            text = (f"{game['name']} ({game['path']}) leaves the list. Nothing on disk is deleted, and its "
                    "converted output stays. Add a game brings it back.")
            self.ctx.confirm("Remove " + game["name"], text, "Remove", "Keep",
                             lambda: (games.remove_game(game["path"]), self.reload()))
            return
        plugins, out_root = mod_names(game["mod"]), self.ctx.session.out_root()
        text = f"The added mod {game['name']}, its imported files and its converted output are deleted."
        self.ctx.confirm("Remove " + game["name"], text, "Remove", "Keep",
                         lambda: self.run.start_task("Removing mods", lambda log: games.remove_mod(plugins, out_root, log),
                                                     lambda _r: self.reload()))

    def current_rows(self) -> list:
        """The selected game's rows."""
        return self.rows.get(self.game["id"], []) if self.game else []

    def shown(self) -> list:
        """The rows the filter and search leave."""
        ui = self.ctx.ui
        query, chosen = ui.query.lower(), ui.filter
        return [r for r in self.current_rows() if query in (r["name"] + r["origin"]).lower()
                and self._filter_keeps(chosen, r)]

    def _filter_keeps(self, key: str, row: dict) -> bool:
        """True when filter `key` shows `row`."""
        return {"all": True, "done": row["status"] != "new", "out": row["status"] == "out",
                "new": row["status"] == "new", "arch": row["archive"], "on": row["name"] in self.ticked}[key]

    def _narrow(self, what: str, value: str) -> None:
        """Set the filter or the search text, then show the list it leaves; a chosen filter stays across games."""
        self.filter_chosen = self.filter_chosen or what == "filter"
        setattr(self.ctx.ui, what, value)
        self.paint_list()

    def paint_list(self) -> None:
        """The filtered plugin list, the filter counts and the go bar."""
        self.ctx.ui.plugins = model(self._plugin_row(r) for r in self.shown())
        self.paint_counts()

    def paint_counts(self) -> None:
        """Each filter's count, the header tick, then the go bar."""
        rows = self.current_rows()
        shown = self.shown()
        self.ctx.ui.all_shown_ticked = bool(shown) and all(r["name"] in self.ticked for r in shown)
        counts = {key: sum(self._filter_keeps(key, r) for r in rows) for key, _label in FILTERS}
        self.ctx.ui.filter_labels = model(f"{label} · {counts[key]}" for key, label in FILTERS)
        self.replan()

    def _plugin_row(self, r: dict) -> dict:
        """One PluginRow for the list; a plugin the Morrowind mode never converts is locked and unticked."""
        locked = self._skipped(r)
        return {"name": r["name"], "origin": "Replaced by the compatibility patch in Morroblivion mode" if locked
                else r["origin"], "archive": r["archive"], "base": r["base"],
                "parts": r["parts"], "status": STATUS[r["status"]][0], "tone": STATUS[r["status"]][1],
                "checked": r["name"] in self.ticked and not locked, "locked": locked,
                "clearable": r["status"] != "new" and r["mod"] is None}

    def _skipped(self, r: dict) -> bool:
        """True when the saved Morrowind mode never converts row `r`."""
        return bool(self.game and self.game["tes3"]
                    and morrowind.mode_skips(morrowind.saved_mode(self.ctx.session.cfg), r["name"]))

    def _toggle(self, index: int, on: bool) -> None:
        """Tick or untick one shown plugin."""
        row = self.shown()[index]
        (self.ticked.add if on else self.ticked.discard)(row["name"])
        self.ctx.ui.plugins.set_row_data(index, self._plugin_row(row))
        self.paint_counts()

    def _tick_shown(self, on: bool) -> None:
        """Tick or untick every shown plugin."""
        names = {r["name"] for r in self.shown() if not self._skipped(r)}
        self.ticked = self.ticked | names if on else self.ticked - names
        self.paint_list()

    def first_ticked(self) -> str:
        """The first ticked plugin of the selected game, or ""."""
        return next((r["name"] for r in self.current_rows() if r["name"] in self.ticked), "")


    def owed(self) -> dict:
        """{plugin: steps} for every ticked plugin, in the game's order."""
        manual = self.ctx.session.selection.steps
        return {r["name"]: games.steps_for(r, manual)
                for r in self.current_rows() if r["name"] in self.ticked and not self._skipped(r)}

    def auto_steps(self) -> list:
        """The steps the ticked plugins owe, ignoring any manual choice."""
        keys = {k for r in self.current_rows() if r["name"] in self.ticked
                for k in games.steps_for(r)}
        return [k for k, *_ in STEPS if k in keys]

    def listed_steps(self) -> list:
        """The step keys worth listing: those some ticked plugin can run (all of them when nothing is ticked)."""
        rows = [r for r in self.current_rows() if r["name"] in self.ticked]
        return [k for k, *_ in STEPS if not rows or any(k in games.runnable(r) for r in rows)]

    def current_steps(self) -> list:
        """The steps the next run does (manual or automatic)."""
        keys = {k for steps in self.owed().values() for k in steps}
        return [k for k, *_ in STEPS if k in keys]

    def replan(self) -> None:
        """Recompute the go bar, then (on a worker) the ticked plugins' idle steps and the time estimate."""
        self._paint_go()
        self.planned += 1
        ask, workers = self.planned, self.ctx.session.worker_count()
        ticked = sorted(self.ticked)
        self.ctx.work(lambda: self._estimate(ticked, workers), lambda seconds: self._timed(ask, seconds),
                      lambda _exc: None, counted=False)
        paint_rail(self.ctx)

    def _paint_go(self) -> None:
        """The go bar's title, button and second line from what the ticked plugins owe now."""
        ui = self.ctx.ui
        owed = {p: s for p, s in self.owed().items() if s}
        rows = {r["name"]: r for r in self.current_rows()}
        upd = sum(1 for p in owed if rows[p]["status"] != "new")
        new = len(owed) - upd
        verb = f"Convert {new}, update {upd}" if new and upd else f"Convert {new}" if new else f"Update {upd}"
        block = self._blocker()
        ui.go_title = block or (f"{verb} plugin{'s' if len(owed) != 1 else ''}" if owed else "Nothing to do")
        ui.go_label = verb if owed else "Convert"
        ui.go_enabled = bool(owed) and not block
        self._paint_go_sub()

    def _estimate(self, ticked: list, workers: int) -> float:
        """On a worker: learn the ticked plugins' idle steps, then estimate what they owe."""
        games.read_idle(ticked)
        owed = {p: s for p, s in self.owed().items() if s}
        return sum(sum(eta.plugin_estimate(p, ks, workers).values()) for p, ks in owed.items())

    def _timed(self, ask: int, seconds: float) -> None:
        """The estimate for plan `ask` arrived (idle steps now known); shown only if no newer plan replaced it."""
        if ask == self.planned:
            self.plan_seconds = seconds
            self._paint_go()
            paint_rail(self.ctx)

    def _paint_go_sub(self) -> None:
        """The go bar's second line: steps, the time estimate once known, the Morrowind mode."""
        owed = {p: s for p, s in self.owed().items() if s}
        idle = "The ticked plugins are up to date" if self.owed() else "Tick plugins above"
        seconds = self.plan_seconds
        self.ctx.ui.go_sub = " · ".join(x for x in (
            f"{len(self.current_steps())} of {len(self.listed_steps())} steps" if owed else idle,
            f"about {runner.format_elapsed(seconds)}" if owed and seconds else "", self.ctx.ui.game_mode) if x)

    def _blocker(self) -> str:
        """Why the ticked plugins can't be converted right now, or ""."""
        g = self.game
        if g is None or not g["tes3"]:
            return ""
        if not self.ctx.screens["setup"].morrowind_ok:
            return "Can't convert Morrowind on this Skyrim"
        mode = morrowind.saved_mode(self.ctx.session.cfg)
        if morrowind.needs_morroblivion(mode) and not morrowind.morroblivion_converted():
            return "Convert Morrowind_ob.esm (Oblivion) first"
        return ""

    def rail_state(self) -> tuple:
        """(state, summary) for the rail."""
        if self.game is None:
            return "idle", "Pick a game"
        count = len([n for n in self.owed() if n])
        return ("done" if count else "idle"), f"{self.game['name']} · {count} plugin{'s' if count != 1 else ''}"

    def start(self) -> None:
        """Start converting the ticked plugins."""
        owed = {p: s for p, s in self.owed().items() if s}
        if not owed or self._blocker() or self.run.busy():
            return
        rows = {r["name"]: r for r in self.current_rows()}
        masters = {p: mods.plugin_masters(rows[p]["path"]) for p in owed}
        groups = runner.group_plugins(self.ctx.session, games.masters_first(owed, masters), masters)
        self.run.start_conversion(groups, self.game["name"], set(owed) - {r["name"] for r in self.current_rows()
                                                                            if r["status"] != "new"},
                                  self.listed_steps())

    def run_import_for(self, plugin: str) -> None:
        """Re-run only Import for `plugin` (after placing navmesh pins)."""
        if plugin and not self.run.busy():
            self.run.start_conversion(runner.group_plugins(self.ctx.session, {plugin: ["import_"]}), plugin, set())

    def run_finished(self) -> None:
        """A run ended: statuses may have changed."""
        self.reload(keep=self.game["id"] if self.game else None)

    def mark_for_update(self, plugins) -> None:
        """Tick `plugins` and show the game that holds the first of them."""
        self.pending = set(plugins)
        holder = next((g for g in self.games if self.pending & {r["name"] for r in self.rows.get(g["id"], [])}), None)
        if holder is not None:
            self.select(holder)
        self.ctx.go("choose")


    def mesh_folder_label(self) -> str:
        """How many mesh folders the Meshes step converts."""
        chosen = self.ctx.session.selection.mesh_subdirs
        return f"{len(chosen)} folders" if chosen else "All folders"

    def open_mesh_folders(self) -> None:
        """Pick which mesh folders the Meshes step converts."""
        plugin = self.first_ticked()
        names = scan_mesh_subdirs(plugin) if plugin else []
        if not names:
            self.ctx.info("Mesh folders", "Tick a plugin whose meshes have been extracted first.")
            return
        self.ctx.open_pick(MeshFolders(self.ctx.session, names, self))


    def _use_mode(self, index: int) -> None:
        """The first answer to the mode question."""
        self._apply_mode(morrowind.MODES[index][0], rebuild=False)

    def open_mode_dialog(self) -> None:
        """The change-mode dialog."""
        ui = self.ctx.ui
        ui.mode_choice = morrowind.mode_index(morrowind.saved_mode(self.ctx.session.cfg))
        ui.patch_state = ("(already built)" if morrowind.patch_built() else "")
        ui.patch_rebuild = False
        ui.dialog = "mode"

    def _switch_mode(self) -> None:
        """The change-mode dialog's Switch button."""
        ui = self.ctx.ui
        ui.dialog = ""
        self._apply_mode(morrowind.MODES[ui.mode_choice][0], rebuild=ui.patch_rebuild)

    def _apply_mode(self, key: str, rebuild: bool) -> None:
        """Save the mode; Morroblivion builds its patch when missing or asked to."""
        from tes4_export.export_morrowind import MORROWIND_SOURCE_KEY

        self.ctx.session.save(MORROWIND_SOURCE_KEY, key)
        self.ctx.session.save(morrowind.MODE_ASKED_KEY, True)
        if morrowind.needs_patch(key) and morrowind.morroblivion_converted() and (rebuild or not morrowind.patch_built()):
            self._build_patch()
        self.paint_game()

    def _build_patch(self) -> None:
        """Build the Morroblivion compatibility patch as a run."""
        data_dir = morrowind.patch_data_dir() or (self.game["path"] if self.game else "")
        missing = morrowind.missing_patch_sources(data_dir) if data_dir else ["Morrowind.esm"]
        if missing:
            self.ctx.info("Morroblivion compatibility patch", "Morrowind's Data Files folder is needed to build the "
                          "patch. Missing: " + ", ".join(missing))
            return
        self.run.start_commands([("Morroblivion patch", morrowind.patch_cmd(self.ctx.session, data_dir))],
                                "Morroblivion compatibility patch")


    def add_mod(self, path: str) -> None:
        """Read an archive or folder, then ask what to take from it."""
        if not path:
            return
        if self.run.busy():
            self.ctx.info("Busy", "Wait for the current run to finish before adding a mod.")
            return
        self.ctx.ui.busy = True
        self.ctx.ui.status_step = "Reading the mod…"
        self.ctx.work(lambda: mods.inspect_mod(path), self._inspected, self._read_failed)

    def _read_failed(self, exc) -> None:
        """The mod could not be read."""
        self.ctx.ui.busy = False
        self.ctx.ui.status_step = "Ready"
        self.ctx.info("Can't add this mod", str(exc))

    def _inspected(self, got) -> None:
        """Show the add-a-mod dialog."""
        manifest, by_plugin = got
        self.ctx.ui.busy = False
        self.ctx.ui.status_step = "Ready"
        self.import_pick = mods.ImportPick(manifest, by_plugin)
        folders = [g for g in self.games if g["mod"] is None]
        target = mods.game_for(self.import_pick.masters(), folders)
        game = next((g for g in folders if g["id"] == target), self.game)
        self.import_pick.game = game
        ui = self.ctx.ui
        ui.import_title = "Add " + manifest.path.name
        ui.import_summary = mods.summary(manifest)
        ui.import_target = (f"Adds to {game['name']}" if game else "") + (
            " (it needs " + ", ".join(sorted(self.import_pick.masters())[:3]) + ")" if target else "")
        ui.import_keep = True
        ui.import_keep_text = self.import_pick.keep_text()
        self._paint_import()
        ui.dialog = "import"

    def _paint_import(self) -> None:
        """The add-a-mod dialog's lists and warning."""
        pick, ui = self.import_pick, self.ctx.ui
        ui.import_subs = model(pick_row(n, on) for n, on in pick.subs.items())
        ui.import_plugins = model(pick_row(rel, on) for rel, on in pick.plugin_rows())
        ui.import_warn = pick.problem() or mods.missing_text(mods.missing_masters(pick.by_plugin, pick.chosen_plugins()))

    def _import_sub(self, index: int, on: bool) -> None:
        """Tick or untick one part."""
        self.import_pick.subs[list(self.import_pick.subs)[index]] = on
        self._paint_import()

    def _import_plugin(self, index: int, on: bool) -> None:
        """Tick or untick one plugin."""
        self.import_pick.plugin_state[self.import_pick.plugin_rows()[index][0]] = on
        self._paint_import()

    def _import_all(self, which: str, on: bool) -> None:
        """All or None for the parts or the plugins."""
        pick = self.import_pick
        if which == "subs":
            pick.subs = {n: on for n in pick.subs}
        else:
            pick.plugin_state.update({rel: on for rel, _ in pick.plugin_rows()})
        self._paint_import()

    def _import_go(self) -> None:
        """Add the mod with the ticked parts and plugins."""
        pick = self.import_pick
        if pick.problem():
            return
        pick.keep = self.ctx.ui.import_keep
        self.ctx.ui.dialog = ""
        self.run.start_task(f"Adding {pick.manifest.path.name}", pick.ingest, lambda results: self._added(pick, results))

    def _added(self, pick, results) -> None:
        """Remember which game the mod belongs to, then reread the games."""
        names = sorted(results or {})
        entry = mods.source_registry.get(mods.EXPORT_DIR, names[0]) if names else None
        if entry and pick.game:
            remembered = dict(self.ctx.session.cfg.get(games.MOD_GAMES_KEY) or {})
            remembered[entry.get("group_id")] = pick.game["id"]
            self.ctx.session.save(games.MOD_GAMES_KEY, remembered)
        self.pending = set(names)
        self.ctx.toast(f"Added {', '.join(names) or pick.manifest.path.name}")
        self.ctx.go("choose")
        self.reload(keep=pick.game["id"] if pick.game else None)

    def _manage(self) -> None:
        """The added-mods dialog for this game."""
        game_mods = self.by_game.get(self.game["id"], []) if self.game else []
        if not game_mods:
            self.ctx.info("Added mods", "No mods have been added to this game.")
            return
        self.ctx.open_pick(ManageMods(self, game_mods))

    def _clear(self, index: int) -> None:
        """Ask, then delete one converted Data-folder plugin's files and records; refused while a converted
        plugin of this game still uses it as a master."""
        name, out_root = self.shown()[index]["name"], self.ctx.session.out_root()
        users = self._converted_users(name)
        if users:
            self.ctx.info("Clear " + name, f"{name} is a master of {', '.join(users[:4])}"
                          f"{f' and {len(users) - 4} more' if len(users) > 4 else ''}. Clear those first.")
            return
        found = ", ".join(str(p) for p in games.converted_files(name, out_root)) or "no files are left"
        text = (f"Deletes {name}'s converted files ({found}) and its conversion records, so it reads as not "
                "converted and is no longer reported out of date. The plugin in your game's Data folder and any "
                "copy installed in your mod manager stay.")
        self.ctx.confirm("Clear " + name, text, "Clear", "Keep", lambda: self.run.start_task(
            f"Clearing {name}", lambda log: games.clear_converted(name, out_root, log), lambda _r: self.reload()))

    def _converted_users(self, master: str) -> list:
        """The converted Data-folder plugins of this game that list `master` among their masters."""
        out = []
        for row in self.current_rows():
            if row["status"] == "new" or row["mod"] is not None:
                continue
            masters = mods.plugin_masters(os.path.join(self.game["path"], row["name"]))
            if master.lower() in {m.lower() for m in masters}:
                out.append(row["name"])
        return out

    def _parts(self, index: int) -> None:
        """Change which parts of the plugin's mod are installed."""
        mod = self.shown()[index]["mod"]
        if mod is not None:
            self.change_parts(mod)

    def change_parts(self, mod: dict) -> None:
        """Read a mod's parts on a worker, then show them."""
        self.ctx.work(lambda: mods.parts_of(mod["plugins"][0]),
                      lambda got: self.ctx.open_pick(Parts(self, mod, *got)))


# ---------------------------------------------------------------------------
#  Pickers
# ---------------------------------------------------------------------------

class FoundGames(Pick):
    """First start: which game folders, already listed or found on this PC, to keep."""

    title, ok = "Games found", "Keep these"
    blurb = "Tick the games to convert from. Add a game brings back any you leave out."

    def __init__(self, view, paths):
        """Every folder starts ticked."""
        self.view, self.paths = view, paths
        self.on = set(paths)

    def rows(self) -> list:
        """One row per folder: its install name, then its path."""
        return [pick_row(label_for_directory(p), p in self.on, p) for p in self.paths]

    def toggle(self, index, on) -> None:
        """Tick or untick a folder."""
        (self.on.add if on else self.on.discard)(self.paths[index])

    def set_all(self, on) -> None:
        """All or none."""
        self.on = set(self.paths) if on else set()

    def accept(self, ctx) -> None:
        """List the ticked folders, hide the rest, and remember the question was answered."""
        ctx.ui.dialog = ""
        games.keep_game_dirs([p for p in self.paths if p in self.on], [p for p in self.paths if p not in self.on])
        ctx.session.save(games.GAMES_ASKED_KEY, True)
        self.view.reload()

    def cancel(self, ctx) -> None:
        """Close without changing the games, and never ask again."""
        ctx.ui.dialog = ""
        ctx.session.save(games.GAMES_ASKED_KEY, True)


class MeshFolders(Pick):
    """Which mesh folders the Meshes step converts."""

    title, blurb = "Mesh folders", "Which folders under meshes\\ the Meshes step converts."

    def __init__(self, session, names, owner):
        """Start from the session's choice (all when none)."""
        self.session, self.names, self.owner = session, names, owner
        chosen = session.selection.mesh_subdirs
        self.on = set(chosen) & set(names) if chosen else set(names)

    def rows(self) -> list:
        """One row per folder."""
        return [pick_row(n, n in self.on) for n in self.names]

    def toggle(self, index, on) -> None:
        """Tick or untick a folder."""
        (self.on.add if on else self.on.discard)(self.names[index])

    def set_all(self, on) -> None:
        """All or none."""
        self.on = set(self.names) if on else set()

    def warning(self) -> str:
        """Nothing ticked converts nothing."""
        return "" if self.on else "Tick at least one folder."

    def accept(self, ctx) -> None:
        """Save the choice; all ticked means no filter."""
        if not self.on:
            return
        self.session.selection.mesh_subdirs = [] if self.on == set(self.names) else sorted(self.on)
        ctx.ui.mesh_folders = self.owner.mesh_folder_label()
        ctx.ui.dialog = "advanced"


class ManageMods(Pick):
    """The mods added to one game: remove them, or change their parts."""

    title, blurb, ok, extra = "Added mods", "Tick mods to remove. Their converted output is deleted too.", "Remove", \
        "Change parts"

    def __init__(self, view, game_mods):
        """List the game's mods."""
        self.view, self.mods = view, game_mods
        self.on = set()

    def rows(self) -> list:
        """One row per mod."""
        return [pick_row(m["label"], i in self.on, ", ".join(m["plugins"]) or "assets only")
                for i, m in enumerate(self.mods)]

    def toggle(self, index, on) -> None:
        """Tick or untick a mod."""
        (self.on.add if on else self.on.discard)(index)

    def set_all(self, on) -> None:
        """All or none."""
        self.on = set(range(len(self.mods))) if on else set()

    def accept(self, ctx) -> None:
        """Remove the ticked mods."""
        doomed = [self.mods[i] for i in sorted(self.on)]
        if not doomed:
            return
        ctx.ui.dialog = ""
        plugins = [p for m in doomed for p in mod_names(m)]
        out_root = self.view.ctx.session.out_root()
        self.view.run.start_task("Removing mods", lambda log: games.remove_mod(plugins, out_root, log),
                                 lambda _r: self.view.reload())

    def extra_clicked(self, ctx) -> None:
        """Change the parts of the one ticked mod."""
        if len(self.on) != 1:
            ctx.toast("Tick one mod")
            return
        self.view.change_parts(self.mods[next(iter(self.on))])


class Parts(Pick):
    """Which parts of an added mod are installed; applying adds it again."""

    ok = "Apply"

    def __init__(self, view, mod, every, installed):
        """List the mod's parts."""
        self.view, self.mod, self.every = view, mod, every
        self.on = set(installed)
        self.title = mod["label"] + " parts"
        self.blurb = "Changing parts adds the mod again. Its plugins then show as out of date."

    def rows(self) -> list:
        """One row per part."""
        return [pick_row(n, n in self.on) for n in self.every]

    def toggle(self, index, on) -> None:
        """Tick or untick a part."""
        (self.on.add if on else self.on.discard)(self.every[index])

    def set_all(self, on) -> None:
        """All or none."""
        self.on = set(self.every) if on else set()

    def warning(self) -> str:
        """No parts means nothing to install."""
        return "" if self.on else "Tick at least one part."

    def accept(self, ctx) -> None:
        """Add the mod again with these parts."""
        if not self.on:
            return
        ctx.ui.dialog = ""
        parts = [n for n in self.every if n in self.on]
        self.view.run.start_task(f"Adding {self.mod['label']} again",
                                 lambda log: mods.reimport(self.mod["plugins"][0], parts, log),
                                 lambda _r: self.view.reload())
