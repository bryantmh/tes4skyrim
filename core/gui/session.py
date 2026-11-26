"""The window's settings and choices, as plain values: what the old tk variables held.

Every saved setting is read once from `conversion_config.json` and written back the moment it
changes (`Session.save`), so a crash never loses a choice. Choices made in dialogs (LOD order,
masters, body-patch plugins, Oblivion UI movies, mesh folders, manual steps) live for the
session only: an empty list means "never confirmed", which keeps the computed defaults.
See: docs/commentary/core_gui.md#session
"""

from pathlib import Path

from core.gui.config import (
    LOD_CHOICE_CONFIG_KEY,
    LOD_DETAIL_CONFIG_KEY,
    NAVMESH_GENERATOR_CONFIG_KEY,
    WINDING_AUTO,
    WINDING_CONFIG_KEY,
    WINDING_MODES,
    load_config,
    save_config,
    winding_enabled_for,
)
from core.navmesh_options import DEFAULT_GENERATOR, GENERATORS
from core.worker_budget import cpu_total, worker_count
from output_layout import configured_output, output_setting
from source_paths import find_game_path
from tools.esm.make_master import CONFIG_KEY as MASTERS_CONFIG_KEY

#: conversion_config.json key: the navmesh cache download switch (navmesh_cache.DOWNLOAD_CONFIG_KEY).
CACHE_CONFIG_KEY = "navmeshCacheDownload"
#: conversion_config.json key: system, dark or light.
THEME_CONFIG_KEY = "theme"
#: conversion_config.json key: mo2, vortex or zip.
MANAGER_CONFIG_KEY = "modManager"
#: conversion_config.json key: the MO2 folder (portable instance or the instance's base folder).
MO2_CONFIG_KEY = "mo2Path"
#: conversion_config.json key: carry Oblivion parallax across.
PARALLAX_CONFIG_KEY = "parallax"
#: conversion_config.json key prefix: whether a load-order mod is built and installed.
INCLUDE_PREFIX = "include."
#: Load-order mods built unless the user turns them off; the rest are off until turned on.
INCLUDED_BY_DEFAULT = frozenset({"body", "start", "journal"})


class Selection:
    """What the dialogs confirmed for this session; empty means "use the computed default"."""

    __slots__ = ("lod_plugins", "lod_order", "lod_off", "lod_worldspaces", "lod_force", "master_plugins",
                 "convert_ui", "patch_plugins", "mesh_subdirs", "steps")

    def __init__(self):
        """Nothing confirmed yet."""
        self.lod_plugins = []
        self.lod_order = []
        self.lod_off = []
        self.lod_worldspaces = []
        self.lod_force = False
        self.master_plugins = []
        self.convert_ui = {"messagebox": True, "cursor": False}
        self.patch_plugins = []
        self.mesh_subdirs = []
        self.steps = None


def _saved_int(cfg: dict, key: str, default: int) -> int:
    """`cfg[key]` as an int, or `default` when absent or unreadable."""
    try:
        return int(cfg.get(key, default))
    except (TypeError, ValueError):
        return default


class Session:
    """Paths, saved settings and this session's dialog choices."""

    __slots__ = ("cfg", "tes5", "output", "workers", "cache_dl", "winding",
                 "navgen", "lod_detail", "parallax", "tex_only", "theme", "manager", "selection",
                 "cpu_max")

    def __init__(self, cfg=None):
        """Read every setting from `cfg` (the saved config when None)."""
        self.cfg = load_config() if cfg is None else cfg
        cfg = self.cfg
        self.cpu_max = cpu_total()
        self.tes5 = cfg.get("tes5DataPath", "") or find_game_path("skyrimse")
        self.output = str(configured_output(cfg.get("outputDir", "")))
        self.workers = max(0, min(_saved_int(cfg, "workers", 0), self.cpu_max))
        self.cache_dl = cfg.get(CACHE_CONFIG_KEY) is not False
        mode = str(cfg.get(WINDING_CONFIG_KEY, "")).strip().lower()
        self.winding = mode if mode in WINDING_MODES else WINDING_AUTO
        gen = cfg.get(NAVMESH_GENERATOR_CONFIG_KEY)
        self.navgen = gen if gen in GENERATORS else DEFAULT_GENERATOR
        self.lod_detail = _saved_int(cfg, LOD_DETAIL_CONFIG_KEY, -1)
        self.parallax = cfg.get(PARALLAX_CONFIG_KEY) is True
        self.tex_only = False
        self.theme = cfg.get(THEME_CONFIG_KEY) or "system"
        self.manager = cfg.get(MANAGER_CONFIG_KEY) or "mo2"
        self.selection = Selection()
        self.selection.master_plugins = list(cfg.get(MASTERS_CONFIG_KEY) or [])
        self.load_lod_choice()

    def load_lod_choice(self) -> None:
        """The saved LOD order, unticked plugins and Force; nothing saved leaves the computed default."""
        saved = self.cfg.get(LOD_CHOICE_CONFIG_KEY)
        saved = saved if isinstance(saved, dict) else {}
        self.selection.lod_order[:] = [str(n) for n in saved.get("order") or []]
        self.selection.lod_off[:] = [str(n) for n in saved.get("off") or []]
        self.selection.lod_force = saved.get("force") is True

    def save_lod_choice(self) -> None:
        """Write the LOD order, unticked plugins and Force to the config (all empty: forget them)."""
        sel = self.selection
        choice = {"order": list(sel.lod_order), "off": list(sel.lod_off), "force": sel.lod_force}
        self.save(LOD_CHOICE_CONFIG_KEY, choice if sel.lod_order else None)

    def save(self, key: str, value) -> None:
        """Remember one setting and write it to the config file now."""
        self.cfg[key] = value
        current = load_config()
        current[key] = value
        save_config(current)

    def out_root(self) -> Path:
        """The output folder."""
        return configured_output(self.output)

    def set_output(self, path: str) -> None:
        """Use and save another output folder."""
        self.output = str(configured_output(path))
        self.save("outputDir", output_setting(path))

    def worker_count(self) -> int:
        """Workers for a run: the saved choice, else the pipeline's own default."""
        return max(1, min(self.workers or worker_count(), self.cpu_max))

    def winding_on(self, plugin: str) -> bool:
        """Whether the collision winding fix runs for `plugin`."""
        return winding_enabled_for(self.winding, plugin)

    def included(self, key: str) -> bool:
        """Whether the load-order mod `key` is built and installed."""
        saved = self.cfg.get(INCLUDE_PREFIX + key)
        return key in INCLUDED_BY_DEFAULT if saved is None else bool(saved)
