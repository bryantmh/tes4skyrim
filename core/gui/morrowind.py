"""
Settings ▸ Morrowind source: the GUI half of the Morrowind master switch.

The export stage reads the chosen set from `conversion_config.json`, so the
radio group saves on every change and nothing else has to be plumbed. Choosing
Morroblivion also builds the compatibility patch when it is missing, because
that mode refuses every conversion without it, and asks whether to rebuild it
when it exists.

See: docs/commentary/tes4_export_morrowind.md#masters
"""

import sys
import tkinter as tk
from tkinter import filedialog

from asset_convert.ui.menu_art import (ICONS_MORROWIND, ICONS_OBLIVION,
                                       MENU_ICONS_KEY, MENU_STYLE_KEY,
                                       STYLE_MORROWIND, STYLE_SKYRIM, available,
                                       resolve)
from core.gui import runner
from core.chargen_source import CHARGEN_SOURCE_KEY, candidates, chosen_source
from core.gui.config import REPO_ROOT
from core.gui.menubar_behavior import enable_tips
from tes4_export.export_morrowind import (MORROWIND_SOURCE_KEY, SOURCE_HYBRID, SOURCE_MORROBLIVION, SOURCE_VANILLA,
                                          morroblivion_exports)
from tes4_export.morrowind_patch import PATCH_NAME, PATCH_SOURCES, patch_exists, source_dir, source_paths

#: Source set -> its menu label, in menu order.
_LABELS = (
    (SOURCE_VANILLA, "Vanilla  (Morrowind + Tribunal + Bloodmoon)"),
    (SOURCE_MORROBLIVION, "Morroblivion + patch"),
    (SOURCE_HYBRID, "Vanilla + Morroblivion meshes"),
)

#: Title of every dialog the patch build raises.
_TITLE = "Morroblivion compatibility patch"

#: Menu tip on the Morroblivion entry.
MORROBLIVION_TIP = (
    f"Convert Morrowind plugins against Morroblivion and {PATCH_NAME}. "
    "Builds the patch if it is missing (convert Morrowind_ob.esm first); "
    "asks whether to rebuild it if it exists")


#: Menu tip on the hybrid entry.
HYBRID_TIP = (
    "Convert vanilla Morrowind and its mods as authored, swapping in only the "
    "Morroblivion meshes the reviewed whitelist approves, its furniture, and its "
    "proven higher-resolution textures. Convert Morrowind_ob.esm first")


def source_default(cfg: dict) -> str:
    """The configured source set; anything unrecognised reads as vanilla."""
    value = str(cfg.get(MORROWIND_SOURCE_KEY, "")).strip().lower()
    return value if value in dict(_LABELS) else SOURCE_VANILLA


def add_source_menu(app, settings_menu, menu_opts: dict, cfg: dict,
                    load_config, save_config, export_dir) -> tk.StringVar:
    """Add Settings ▸ Morrowind source as a radio cascade saved on change."""
    var = tk.StringVar(value=source_default(cfg))
    chosen = {"mode": var.get()}

    def _save():
        """Persist the chosen set and remember it as the current one."""
        chosen["mode"] = var.get()
        updated = load_config()
        updated[MORROWIND_SOURCE_KEY] = var.get()
        save_config(updated)

    def _pick_morroblivion():
        """Switch, building the patch first when it is missing or asked for."""
        was = chosen["mode"]
        if patch_exists(str(export_dir)) and not app.confirm(
                _TITLE, f"{PATCH_NAME} already exists.\n\nRebuild it?",
                yes="Rebuild", no="Keep"):
            _save()
            return
        if build_patch_dialog(app, str(export_dir)):
            _save()
        else:
            var.set(was)

    menu = tk.Menu(settings_menu, **menu_opts)
    enable_tips(menu)
    menu.add_radiobutton(label=dict(_LABELS)[SOURCE_VANILLA],
                         value=SOURCE_VANILLA, variable=var, command=_save)
    menu.add_radiobutton(label=dict(_LABELS)[SOURCE_MORROBLIVION],
                         value=SOURCE_MORROBLIVION, variable=var,
                         command=_pick_morroblivion)
    menu.entry_tips[menu.index("end")] = MORROBLIVION_TIP
    menu.add_radiobutton(label=dict(_LABELS)[SOURCE_HYBRID],
                         value=SOURCE_HYBRID, variable=var, command=_save)
    menu.entry_tips[menu.index("end")] = HYBRID_TIP
    settings_menu.add_cascade(label="Morrowind source", menu=menu)
    return var


#: Each radio group: its config key, then (value, label, whose art it needs or None).
_MENU_GROUPS = (
    (MENU_STYLE_KEY, ((STYLE_MORROWIND, "Morrowind look", ICONS_MORROWIND),
                      (STYLE_SKYRIM, "Skyrim look", None))),
    (MENU_ICONS_KEY, ((ICONS_MORROWIND, "Morrowind icons", ICONS_MORROWIND),
                      (ICONS_OBLIVION, "Oblivion icons", ICONS_OBLIVION))),
)

#: The groups' Tk variables, held so Tk does not drop them while the menu lives.
_MENU_VARS = []

#: Menu tip on the cascade's entries.
MENU_STYLE_TIP = ("The look and icons of the runtime's dialogue, stats and level-up "
                  "menus; applies the next time the runtime is packaged. Greyed out: "
                  "no registered game install has that game's menu art")


def add_menu_style_menu(settings_menu, menu_opts: dict, cfg: dict,
                        load_config, save_config, export_dir) -> None:
    """Add Settings ▸ Menu style: the look, then the icons, each a radio
    group saved on change and showing what would be built; a choice whose
    art no registered install has is greyed out. The installs are scanned
    (about a second) when the cascade first opens, not at startup.

    See: docs/commentary/morrowind_runtime.md#menu-styles
    """
    menu = tk.Menu(settings_menu, **menu_opts)
    enable_tips(menu)

    def _fill():
        """Scan the installs and add both groups, the first time only."""
        if menu.index("end") is None:
            _add_menu_groups(menu, cfg, load_config, save_config, export_dir)

    menu.configure(postcommand=_fill)
    settings_menu.add_cascade(label="Menu style", menu=menu)


def _add_menu_groups(menu, cfg: dict, load_config, save_config, export_dir) -> None:
    """The look and icon radio groups, greyed where no install has the art."""
    have = available(export_dir)
    built = resolve(export_dir, cfg.get(MENU_STYLE_KEY), cfg.get(MENU_ICONS_KEY))
    for (key, entries), current in zip(_MENU_GROUPS, built):
        if menu.index("end") is not None:
            menu.add_separator()
        var = tk.StringVar(value=current or "")

        def _save(key=key, var=var):
            """Persist this group's choice."""
            updated = load_config()
            updated[key] = var.get()
            save_config(updated)

        for value, label, needs in entries:
            menu.add_radiobutton(label=label, value=value, variable=var, command=_save,
                                 state="normal" if needs is None or have[needs] else "disabled")
            menu.entry_tips[menu.index("end")] = MENU_STYLE_TIP
        _MENU_VARS.append(var)


#: Menu tip on the classes-and-birthsigns entries.
CHARGEN_TIP = ("Every converted game shares one class menu and one birthsign menu; they "
               "list this game's classes and birthsigns (a game with only one of them takes "
               "the other from the next game). Applies the next time the runtime is packaged")


def add_chargen_menu(settings_menu, menu_opts: dict, cfg: dict,
                     load_config, save_config, export_dir) -> None:
    """Add Settings ▸ Classes and birthsigns: a radio entry per exported game
    with either, saved on change; the exports are scanned when it first opens.

    See: docs/commentary/morrowind_runtime.md#chargen-menus
    """
    menu = tk.Menu(settings_menu, **menu_opts)
    enable_tips(menu)
    var = tk.StringVar()
    _MENU_VARS.append(var)

    def _save():
        """Persist the chosen game."""
        updated = load_config()
        updated[CHARGEN_SOURCE_KEY] = var.get()
        save_config(updated)

    def _fill():
        """List the games the first time only."""
        if menu.index("end") is not None:
            return
        names = candidates(export_dir)
        var.set(chosen_source(export_dir, names, cfg.get(CHARGEN_SOURCE_KEY)))
        for name in names:
            menu.add_radiobutton(label=name, value=name, variable=var, command=_save)
            menu.entry_tips[menu.index("end")] = CHARGEN_TIP
        if not names:
            menu.add_command(label="(no converted game has classes or birthsigns)",
                             state="disabled")

    menu.configure(postcommand=_fill)
    settings_menu.add_cascade(label="Classes and birthsigns", menu=menu)


def _morrowind_data_dir(app, export_dir: str) -> str:
    """The Morrowind Data Files folder: the registered install, else asked for.

    Returns "" when the user cancels or picks a folder without the masters.
    """
    known = source_dir(export_dir)
    if known and not source_paths(known, PATCH_SOURCES)[1]:
        return known
    data_dir = filedialog.askdirectory(
        title="Select your Morrowind 'Data Files' folder")
    if not data_dir:
        return ""
    _, missing = source_paths(data_dir, PATCH_SOURCES)
    if missing:
        app.info(
            _TITLE,
            "That folder is not a Morrowind Data Files directory.\n\n"
            f"Looked in:\n{data_dir}\n\nMissing:\n  " + "\n  ".join(missing))
        return ""
    return data_dir


def build_patch_dialog(app, export_dir: str) -> bool:
    """Start building the patch as a run in the main log pane; False when it could not start.

    Morroblivion has to be converted first -- the patch holds what it does NOT
    supply, so without it there is no gap to measure.
    """
    if app.running.is_set():
        app.info(_TITLE, "Wait for the current run to finish, "
                         "then choose Morroblivion again.")
        return False
    if not morroblivion_exports(export_dir):
        app.info(
            _TITLE,
            "No converted Morroblivion plugin was found.\n\n"
            f"{PATCH_NAME} holds the objects Morroblivion does NOT convert, so "
            "convert Morrowind_ob.esm first, then choose Morroblivion again.")
        return False
    data_dir = _morrowind_data_dir(app, export_dir)
    if not data_dir:
        return False
    cmd = [sys.executable, "-u", str(REPO_ROOT / "convert.py"),
           "--build-morrowind-patch", data_dir,
           "--output-dir", str(app.out_root())]
    runner.start_command(app, f"Build {PATCH_NAME}", cmd, PATCH_NAME,
                         lambda _key: None)
    return True
