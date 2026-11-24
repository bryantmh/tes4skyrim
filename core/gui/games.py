"""The games the window offers and the plugins in each, with what every plugin still owes.

A game is a registered Data folder. Its plugins are the ones in that folder plus those of every
mod added to it; an added mod belongs to the game whose Data folder holds its masters (or the
game it was added to, remembered in the config). A plugin's status comes from the version state:
never converted, out of date (steps changed since it was converted), or up to date.
See: docs/commentary/core_gui.md#games
"""

import os

import version as version_info
from asset_convert.sources import mod_ingest, source_registry
from core.gui import mods
from core.gui.config import EXPORT_DIR, STEPS, default_on_steps, find_game_path, scan_plugins
from output_layout import asset_root
from tes4_export.tes3_reader import is_tes3

#: conversion_config.json key: {added mod id: the game id it was added to}.
MOD_GAMES_KEY = "modGames"
#: Steps a Morrowind plugin never runs: it has no SpeedTrees, and its scripts run on their own interpreter.
TES3_SKIPPED = frozenset({"scripts", "speedtrees"})
#: Every pipeline step key, in run order.
STEP_KEYS = [k for k, *_ in STEPS]

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


def _available(plugin: str, mod, tes3: bool) -> set:
    """The steps this plugin can run at all."""
    steps = set(STEP_KEYS)
    if mod is not None:
        caps = mod["entry"].get("capabilities")
        if not isinstance(caps, dict):
            caps = mod_ingest.capabilities_for(asset_root(EXPORT_DIR, mod["entry"].get("plugin") or plugin),
                                               has_plugin=not mod["asset_only"])
        steps &= mod_ingest.available_steps(caps)
    return steps - TES3_SKIPPED if tes3 else steps


def plugin_rows(game: dict, game_mods: list) -> list:
    """Every plugin of a game as a dict: name, origin, archive, base, status, owed, available, mod.

    Reads each plugin's masters and version state, so call it off the UI thread.
    """
    rows = []
    for name in scan_plugins(game["path"]) if game["path"] else []:
        masterless = not mods.plugin_masters(os.path.join(game["path"], name))
        rows.append(_row(name, "Data folder", None, masterless, game["tes3"]))
    for mod in game_mods:
        parts = mod["entry"].get("subpackages_all") or []
        origin = mod["label"] + (f" · {len(mod['entry'].get('subpackages') or parts)} of {len(parts)} parts"
                                 if parts else "") + (" · assets only" if mod["asset_only"] else "")
        for plugin, path in zip(mod["plugins"], mods.plugin_paths(mod)):
            masterless = mod is game["mod"] and not mods.plugin_masters(path)
            rows.append(_row(plugin, origin, mod, masterless, game["tes3"], bool(parts)))
    return rows


def _row(name: str, origin: str, mod, base: bool, tes3: bool, parts: bool = False) -> dict:
    """One plugin's row."""
    status, owed = _plan_status(name)
    return {"name": name, "origin": origin, "archive": mod is not None, "base": base, "parts": parts,
            "status": status, "owed": owed, "available": _available(name, mod, tes3), "mod": mod}


def steps_for(row: dict, pack_default: bool, manual=None) -> list:
    """The steps a run does for this plugin: the manual choice, else what it owes (all when new)."""
    wanted = set(manual) if manual is not None else (
        default_on_steps(pack_default) if row["status"] == "new" else set(row["owed"]) & default_on_steps(pack_default))
    return [k for k in STEP_KEYS if k in wanted and k in row["available"]]
