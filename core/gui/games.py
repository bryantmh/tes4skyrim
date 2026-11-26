"""The games the window offers and the plugins in each, with what every plugin still owes.

A game is a registered Data folder. Its plugins are the ones in that folder plus those of every
mod added to it; an added mod belongs to the game whose Data folder holds its masters (or the
game it was added to, remembered in the config). A plugin's status comes from the version state:
never converted, out of date (steps changed since it was converted), or up to date. A plugin is
"base game" when an official game lists it (OFFICIAL); the folder's root plugin (Oblivion.esm,
FalloutNV.esm) is the one that starts ticked.
See: docs/commentary/core_gui.md#games
"""

import mmap
import os
import shutil
import struct
from pathlib import Path

import version as version_info
from asset_convert.sources import mod_ingest, source_registry
from asset_convert.sources.bsa_extract import get_bsa_files, read_bsa_folders
from asset_convert.sources.bsa_extract_morrowind import is_morrowind_bsa, read_index
from core.gui import eta, mods
from core.gui.config import EXPORT_DIR, STEPS, scan_plugins
from output_layout import FINISHED_DIR_NAME, asset_root, paths
from source_paths import find_game_path, installed_game_dirs
from tes4_export.tes3_reader import is_tes3

#: conversion_config.json key: {added mod id: the game id it was added to}.
MOD_GAMES_KEY = "modGames"
#: Steps a Morrowind plugin never runs: it has no SpeedTrees, and its scripts run on their own interpreter.
TES3_SKIPPED = frozenset({"scripts", "speedtrees"})
#: Every pipeline step key, in run order.
STEP_KEYS = [k for k, *_ in STEPS]
#: Fallout 3's official plugins: its own game, and part of a Tale of Two Wastelands New Vegas install.
FALLOUT3 = ("Fallout3.esm", "Anchorage.esm", "ThePitt.esm", "BrokenSteel.esm", "PointLookout.esm", "Zeta.esm")
#: Each official game's plugins, its root master first.
OFFICIAL = (
    ("Oblivion.esm", "DLCShiveringIsles.esp", "Knights.esp", "DLCBattlehornCastle.esp", "DLCFrostcrag.esp",
     "DLCHorseArmor.esp", "DLCMehrunesRazor.esp", "DLCOrrery.esp", "DLCSpellTomes.esp", "DLCThievesDen.esp",
     "DLCVileLair.esp"),
    ("Morrowind.esm", "Tribunal.esm", "Bloodmoon.esm"),
    FALLOUT3,
    ("FalloutNV.esm", "DeadMoney.esm", "HonestHearts.esm", "OldWorldBlues.esm", "LonesomeRoad.esm",
     "GunRunnersArsenal.esm", "CaravanPack.esm", "ClassicPack.esm", "MercenaryPack.esm", "TribalPack.esm",
     *FALLOUT3),
    ("Nehrim.esm", "Translation.esp"),
    ("Arktwend_English.esm",),
)
#: Every official plugin, lowercased.
BASE_PLUGINS = frozenset(name.lower() for game in OFFICIAL for name in game)
#: conversion_config.json key: True once the user has chosen which game folders to keep.
GAMES_ASKED_KEY = "gamesAsked"

# ---------------------------------------------------------------------------
#  Games
# ---------------------------------------------------------------------------


def list_games(cfg: dict) -> list:
    """Every game as {'id', 'name', 'path', 'tes3', 'mod'}: Data folders, then standalone added mods.

    An added mod whose plugins need no master from outside it (a total conversion) is a game of
    its own, with an empty path and its mod under 'mod'. Oblivion is found from the registry when
    the config names no folder, so a first start lists it.
    """
    extra = [cfg.get("tes4DataPath", "") or find_game_path("oblivion")]
    try:
        source_registry.migrate_known_directories(EXPORT_DIR, extra_dirs=extra)
        rows = source_registry.all_sources(EXPORT_DIR, extra_dirs=extra)
    except Exception:
        rows = []
    out = []
    for row in rows:
        if row["kind"] == "directory":
            paths = [os.path.join(row["path"], p) for p in scan_plugins(row["path"])]
            out.append({"id": row["id"], "name": row["label"], "path": row["path"], "tes3": _any_tes3(paths),
                        "mod": None})
    for mod in mods.added_mods():
        if mod["plugins"] and not mod["asset_only"] and not mods.mod_masters(mod):
            out.append({"id": "mod:" + mod["id"], "name": mod["label"], "path": "",
                        "tes3": _any_tes3(mods.plugin_paths(mod)), "mod": mod})
    return out


def found_game_dirs(cfg: dict) -> list:
    """Every game Data folder worth offering: those already listed, then installs found on this PC."""
    listed = [g["path"] for g in list_games(cfg) if g["mod"] is None]
    seen = {os.path.normcase(os.path.normpath(p)) for p in listed}
    found = [p for p in installed_game_dirs([game[0] for game in OFFICIAL])
             if os.path.normcase(os.path.normpath(p)) not in seen]
    return listed + found


def keep_game_dirs(keep, drop) -> None:
    """List the folders in `keep` and stop listing those in `drop`; nothing on disk changes."""
    for path in keep:
        add_game(path)
    for path in drop:
        remove_game(path)


def _any_tes3(paths) -> bool:
    """True when any master file among `paths` is a Morrowind file."""
    return any(p.lower().endswith(".esm") and is_tes3(p) for p in paths)


def add_game(path: str) -> bool:
    """Register another game Data folder."""
    return source_registry.add_directory(EXPORT_DIR, path)


def remove_game(path: str) -> bool:
    """Stop listing a game folder; nothing on disk is deleted."""
    return source_registry.remove_directory(EXPORT_DIR, path)


def select_game(game: dict) -> None:
    """Make `game` the folder convert.py reads from (children inherit it)."""
    source_registry.select_directory(game["path"])


def mods_by_game(cfg: dict, games: list) -> dict:
    """{game id: [added mods]}: the remembered game, else the one holding the mod's masters, else the first."""
    remembered = cfg.get(MOD_GAMES_KEY) or {}
    folders = [g for g in games if g["mod"] is None]
    out = {g["id"]: ([g["mod"]] if g["mod"] else []) for g in games}
    standalone = {g["mod"]["id"] for g in games if g["mod"]}
    for mod in mods.added_mods():
        if mod["id"] in standalone:
            continue
        gid = remembered.get(mod["id"])
        if gid not in out:
            gid = mods.game_for(mods.mod_masters(mod), folders) or (folders[0]["id"] if folders else "")
        if gid in out:
            out[gid].append(mod)
    return out


# ---------------------------------------------------------------------------
#  Plugins and what they owe
# ---------------------------------------------------------------------------

def _plan_status(plugin: str) -> tuple:
    """("new"|"out"|"ok", owed step keys) from the version state. Network on first call."""
    try:
        plan = version_info.upgrade_plan(plugin)
    except Exception:
        return "new", []
    if plan.get("never_run"):
        return "new", []
    steps = [k for k in plan.get("steps") or [] if k in STEP_KEYS]
    return ("out" if steps else "ok"), steps


def _record_types(path) -> set:
    """The record types a plugin holds, from headers only: each top group's label (Oblivion, New Vegas) or each
    record's type (Morrowind)."""
    fmt = eta.plugin_format(path)
    header = eta.TES3_HEADER if fmt == "tes3" else eta.TES4_HEADERS[fmt == "fnv"]
    found, pos = set(), 0
    with open(path, "rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
        while pos + header <= len(mm):
            sig, size = mm[pos:pos + 4], struct.unpack_from("<I", mm, pos + 4)[0]
            if sig == b"GRUP":
                if size > header:
                    found.add(mm[pos + 8:pos + 12].decode("latin-1"))
                pos += size
            else:
                found.add(sig.decode("latin-1"))
                pos += header + size
    return found


def _archive_folders(bsa) -> set:
    """The lower-case folder names in one archive of either generation, from its directory only."""
    if is_morrowind_bsa(bsa):
        return {os.path.dirname(name).lower() for name in read_index(bsa)}
    with open(bsa, "rb") as fh:
        return set(read_bsa_folders(fh))


def _asset_folders(plugin: str) -> set:
    """The folders the plugin's archives hold, plus an added mod's top asset folders."""
    data_dir = source_registry.directory_for(EXPORT_DIR, plugin)
    folders = set()
    for bsa in get_bsa_files(data_dir, plugin) if data_dir else ():
        folders |= _archive_folders(bsa)
    if source_registry.get(EXPORT_DIR, plugin):
        root = Path(source_registry.asset_root(EXPORT_DIR, plugin))
        folders |= {sub.replace("/", "\\") for sub in ("trees", "meshes/creatures", "sound", "music")
                    if (root / sub).is_dir()}
    return folders


def idle_steps(plugin: str) -> set:
    """Steps with nothing to work on, from record headers and archive folder names only.

    See: docs/commentary/core_gui.md#idle-steps
    """
    path = eta.plugin_path(plugin)
    if path is None:
        return set()
    try:
        types, folders = _record_types(path), _asset_folders(plugin)
    except (OSError, ValueError, struct.error):
        return set()

    def under(*prefixes):
        """True when an archive folder sits under any of `prefixes`."""
        return any(f == p or f.startswith(p + "\\") for f in folders for p in prefixes)

    work = {"speedtrees": "TREE" in types or under("trees"),
            "creatures": bool(types & {"CREA"}) or under("meshes\\creatures")
            or (eta.plugin_format(path) == "fnv" and "WEAP" in types),
            "sounds": bool(types & {"SOUN", "DIAL", "INFO", "MUSC"}) or under("sound", "music")}
    return {step for step, needed in work.items() if not needed}


#: {plugin: idle steps}, filled only for ticked plugins by `read_idle` on a worker, never at startup.
_IDLE = {}


def read_idle(plugins) -> None:
    """Work out the idle steps of each of `plugins` not yet known (reads the plugin; call it on a worker)."""
    for plugin in plugins:
        if plugin not in _IDLE:
            _IDLE[plugin] = idle_steps(plugin)


def runnable(row: dict) -> set:
    """The row's steps less those already known to have nothing to work on."""
    return row["available"] - _IDLE.get(row["name"], set())


def _available(plugin: str, mod, tes3: bool) -> set:
    """The steps this plugin can run at all, as far as is known without reading it."""
    steps = set(STEP_KEYS)
    if mod is not None:
        caps = mod["entry"].get("capabilities")
        if not isinstance(caps, dict):
            caps = mod_ingest.capabilities_for(asset_root(EXPORT_DIR, mod["entry"].get("plugin") or plugin),
                                               has_plugin=not mod["asset_only"])
        steps &= mod_ingest.available_steps(caps)
    return steps - TES3_SKIPPED if tes3 else steps


def roots(names) -> set:
    """Present official root masters (lowercased) that no other present game lists.

    See: docs/commentary/core_gui.md#games
    """
    present = {n.lower() for n in names}
    games = [(g[0].lower(), {p.lower() for p in g}) for g in OFFICIAL if g[0].lower() in present]
    return {root for root, _ in games if not any(root in members for other, members in games if other != root)}


def plugin_rows(game: dict, game_mods: list) -> list:
    """Every plugin of a game as a dict: name, origin, archive, base, root, status, owed, available, mod.

    A plugin is out of date only when it owes a step it can run.
    A plugin an added mod provides is that mod's row only, even when a copy also sits in the Data folder.
    Reads each plugin's masters and version state, so call it off the UI thread.
    """
    rows = []
    names = scan_plugins(game["path"]) if game["path"] else []
    folder_roots = roots(names)
    added = {p.lower() for mod in game_mods for p in mod["plugins"]}
    for name in (n for n in names if n.lower() not in added):
        rows.append(_row(name, os.path.join(game["path"], name), "Data folder", None, name.lower() in BASE_PLUGINS,
                         name.lower() in folder_roots, game["tes3"]))
    for mod in game_mods:
        parts = mod["entry"].get("subpackages_all") or []
        origin = mod["label"] + (f" · {len(mod['entry'].get('subpackages') or parts)} of {len(parts)} parts"
                                 if parts else "") + (" · assets only" if mod["asset_only"] else "")
        for plugin, path in zip(mod["plugins"], mods.plugin_paths(mod)):
            own_root = mod is game["mod"] and not mods.plugin_masters(path)
            rows.append(_row(plugin, path, origin, mod, own_root, own_root, game["tes3"], bool(parts)))
    return rows


def _row(name: str, path, origin: str, mod, base: bool, root: bool, tes3: bool,
         parts: bool = False) -> dict:
    """One plugin's row: out of date only when it owes a step it can and would run."""
    status, owed = _plan_status(name)
    available = _available(name, mod, tes3)
    if status == "out" and not set(owed) & available:
        status = "ok"
    return {"name": name, "path": path, "origin": origin, "archive": mod is not None, "base": base, "root": root,
            "parts": parts, "status": status, "owed": owed, "available": available, "mod": mod}


def _unowned_stem_folder(plugin: str, out_root) -> list:
    """`output/<plugin stem>` when it exists and is no registered plugin's group folder."""
    stem = Path(out_root) / Path(plugin).stem
    owned = {source_registry.asset_root_name(EXPORT_DIR, p).lower() for p in source_registry.plugins(EXPORT_DIR)}
    return [stem] if stem.is_dir() and stem.name.lower() not in owned else []


def converted_files(plugin: str, out_root) -> list:
    """A plugin's converted files: its export records, output folder, finished zip and any unowned stem folder.

    A registered mod's imported payload (its asset folder) is never listed.
    See: docs/commentary/core_gui.md#games
    """
    where = paths(plugin, EXPORT_DIR, out_root)
    records = [] if source_registry.get(EXPORT_DIR, plugin) and where.records == where.assets else [where.records]
    found = [*records, where.out, *_unowned_stem_folder(plugin, out_root),
             Path(out_root) / FINISHED_DIR_NAME / f"{where.out.name}.zip"]
    roots_ok = (Path(EXPORT_DIR), Path(out_root), Path(out_root) / FINISHED_DIR_NAME)
    return list(dict.fromkeys(p for p in found if p.exists() and p.parent in roots_ok))


def _delete(paths_found: list, log) -> None:
    """Delete each file or folder, logging it."""
    for path in paths_found:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        log(f"Removed {path}")


def clear_converted(plugin: str, out_root, log) -> None:
    """Delete a plugin's converted files and its conversion records, so it reads as not converted."""
    _delete(converted_files(plugin, out_root), log)
    version_info.forget_plugin(plugin)
    log(f"{plugin} now reads as not converted.")


def remove_mod(plugins: list, out_root, log) -> None:
    """Remove added mods and their converted output, keeping a folder a remaining plugin of the mod still uses."""
    leaving = {p.lower() for p in plugins}
    found = {}
    for name in plugins:
        shared = any(m.lower() not in leaving for m in source_registry.group_members(EXPORT_DIR, name))
        keep = paths(name, EXPORT_DIR, out_root).out if shared else None
        found[name] = [p for p in converted_files(name, out_root) if p != keep]
    mods.remove(plugins, log)
    for name, files in found.items():
        _delete([p for p in files if p.exists()], log)
        version_info.forget_plugin(name)


def masters_first(owed: dict, masters: dict) -> dict:
    """`owed` ({plugin: steps}) reordered so every plugin comes after its masters among it; otherwise as given."""
    by_lower = {name.lower(): name for name in owed}
    placed, seen = [], set()

    def place(name):
        """Place `name` after its masters (a master cycle is broken where it is met)."""
        if name.lower() in seen:
            return
        seen.add(name.lower())
        for master in masters.get(name, ()):
            if master.lower() in by_lower:
                place(by_lower[master.lower()])
        placed.append(name)

    for name in owed:
        place(name)
    return {name: owed[name] for name in placed}


def steps_for(row: dict, manual=None) -> list:
    """The steps a run does for this plugin: the manual choice, else what it owes (all when new)."""
    wanted = set(manual) if manual is not None else (
        set(STEP_KEYS) if row["status"] == "new" else set(row["owed"]))
    return [k for k in STEP_KEYS if k in wanted and k in runnable(row)]
