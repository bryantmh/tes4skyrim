"""Package the converter's SKSE DLLs as one distributable mod.

A DLL is not a plugin asset: one copy serves every converted mod, so they
ship together rather than beside any plugin's meshes. Each converted mod
contributes only its own data -- the animation cache fragment under
SKSE/Plugins/CreatureRuntime/animation, the FO3/FNV guns/bodyparts sidecars
under SKSE/Plugins/FalloutRuntime, the crime sidecar under
SKSE/Plugins/TESRuntime, and a Morrowind plugin's dialogue under
SKSE/Plugins/MorrowindRuntime -- which these DLLs read at load.

The archive mirrors what `convert.py --pack-zip-only` produces -- output/
Finished Mods/<name>.zip, contents rooted as a Data folder -- so a user
installs it exactly like any converted plugin.

MorrowindRuntime's menus -- the dialogue window, the stats window and the
level-up dialog -- are composed here in the `menuStyle` the config chooses
(`--menu-style` overrides it) and go straight into the archive: Morrowind's
art is Bethesda's, so the repo never holds a built copy. A forced Morrowind
style with no registered install skips the menus and the rest still packages.
So is the ONE class and birthsign table every game's menus share, from the
exported game the config's `chargenSource` names.
See: docs/commentary/morrowind_runtime.md#menu-styles
See: docs/commentary/morrowind_runtime.md#chargen-menus

Usage:
  python tools/release/package_runtime_dll.py   # -> output/Finished Mods/TESRuntime.zip
  python tools/release/package_runtime_dll.py --output-dir PATH
  python tools/release/package_runtime_dll.py --mod HavokWorldSize   # standalone
  python tools/release/package_runtime_dll.py --mod CreatureRuntime  # standalone
"""

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

from asset_convert.ui.menu_art import (ICON_SETS, MENU_ICONS_KEY, MENU_STYLE_KEY,
                                       STYLES, effect_icons, menu_art, resolve)
from asset_convert.ui.morrowind_menu_art import MissingArtError
from asset_convert.ui.skyrim_skills import SKILL_TABLE, skill_table_text
from output_layout import finished_dir, write_mod_zip
from core.chargen_source import CHARGEN_SOURCE_KEY, SHARED_CHARGEN, shared_lines
from tools.generators.gen_morrowind_chargen_swf import (BIRTH_MOVIE, CLASS_MOVIE,
                                                        birth_window, class_window)
from tools.generators.gen_morrowind_menu_swf import dialogue_window
from tools.generators.gen_morrowind_stats_swf import (BUTTON_MOVIE,
                                                      LEVELUP_MOVIE,
                                                      STATS_MOVIE,
                                                      levelup_dialog,
                                                      perks_button,
                                                      stats_window)

MOD_NAME = "TESRuntime"

SRC_DIR = SCRIPT_DIR / "tes_runtime"

#: Where the pipeline's exports and the source registry live.
EXPORT_ROOT = SCRIPT_DIR / "export"

#: The per-install settings the GUI writes; its `menuStyle` picks the menus' look.
CONFIG_FILE = SCRIPT_DIR / "conversion_config.json"

#: MorrowindRuntime's menus as the game loads them, each with the function that composes it.
MENUS = ((Path("Interface") / "morrowind_dialogue.swf", dialogue_window),
         (Path("Interface") / STATS_MOVIE, stats_window),
         (Path("Interface") / LEVELUP_MOVIE, levelup_dialog),
         (Path("Interface") / BUTTON_MOVIE, perks_button),
         (Path("Interface") / CLASS_MOVIE, class_window))

#: The birthsign menu, which also carries the shared table's effect icons.
BIRTH_MENU = Path("Interface") / BIRTH_MOVIE

#: Where tes_runtime/build.bat puts every finished DLL.
DIST_DIR = SRC_DIR / "dist"

PLUGINS = Path("SKSE") / "Plugins"

CREATURE_RUNTIME = ((DIST_DIR / "CreatureRuntime.dll", PLUGINS / "CreatureRuntime.dll"),)

HAVOK_WORLD_SIZE = (
    (DIST_DIR / "HavokWorldSize.dll", PLUGINS / "HavokWorldSize.dll"),
    (SRC_DIR / "havok_world_size" / "HavokWorldSize.ini",
     PLUGINS / "HavokWorldSize.ini"),
)

#: Mod name -> (required files, optional files), each file a (source, archive path) pair.
MODS = {
    MOD_NAME: (
        ((DIST_DIR / "TESRuntime.dll", PLUGINS / "TESRuntime.dll"),),
        (*CREATURE_RUNTIME,
         (DIST_DIR / "FalloutRuntime.dll", PLUGINS / "FalloutRuntime.dll"),
         *HAVOK_WORLD_SIZE,
         (DIST_DIR / "MorrowindRuntime.dll", PLUGINS / "MorrowindRuntime.dll"),
         (SRC_DIR / "morrowind" / "MorrowindRuntime.ini",
          PLUGINS / "MorrowindRuntime" / "MorrowindRuntime.ini")),
    ),
    "CreatureRuntime": (CREATURE_RUNTIME, ()),
    "HavokWorldSize": (HAVOK_WORLD_SIZE, ()),
}


def configured(key: str):
    """The config's value for `key`; None when unset or unreadable."""
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    return cfg.get(key)


def configured_choice() -> tuple:
    """The config's `(menuStyle, menuIcons)`; None for either left unset."""
    return configured(MENU_STYLE_KEY), configured(MENU_ICONS_KEY)


def chargen_table(export_root: Path, chosen=None) -> list:
    """The shared class and birthsign table as `[(archive path, bytes)]`, from
    the game `chosen` (the config's chargenSource when None); [] with no game.

    See: docs/commentary/morrowind_runtime.md#chargen-menus
    """
    source, lines, _icons = shared_lines(export_root, chosen or configured(CHARGEN_SOURCE_KEY))
    if not lines:
        print("  - class and birthsign table (no exported game has either, skipped)")
        return []
    print(f"  classes and birthsigns: {source}")
    return [(SHARED_CHARGEN, ('\n'.join(lines) + '\n').encode('utf-8'))]


def morrowind_menus(export_root: Path, choice: tuple = (None, None), chargen=None) -> "list | None":
    """Every Morrowind menu movie as `(archive path, bytes)`, in `choice`'s
    look and icons, and the Skyrim skill table the stats window names them by.
    The birthsign icons come from the game `chargen` (as `chargen_table`).

    None when a Morrowind look finds no install holding its art, so the
    caller skips the menus instead of failing.
    See: docs/commentary/morrowind_runtime.md#menu-styles
    """
    icons = effect_icons(export_root, shared_lines(export_root, chargen or configured(CHARGEN_SOURCE_KEY))[2])
    try:
        art = menu_art(str(export_root), *choice)
        menus = [(arc, build(art).serialize(compress=True)) for arc, build in MENUS]
        menus.append((BIRTH_MENU, birth_window(art, icons).serialize(compress=True)))
    except MissingArtError:
        return None
    table = skill_table_text()
    return menus + ([(SKILL_TABLE, table.encode("utf-8"))] if table else [])


def package(out_root: Path, mod_name: str = MOD_NAME,
            export_root: Path = EXPORT_ROOT, choice: tuple = (None, None), chargen=None) -> int:
    """Zip `mod_name`'s files into <out_root>/Finished Mods/<mod_name>.zip,
    with the chargen menus of the game `chargen` (the config's when None).

    Every runtime is its own DLL, so a fault in one cannot take the others
    down; MorrowindRuntime links GPL-3.0 OpenMW, which stays out of the MIT
    runtimes' binaries. Missing optional files are skipped, and so are the
    menus when a Morrowind look (`choice`, see `morrowind_menus`) finds no install.
    See: docs/commentary/morrowind_runtime.md#licensing
    """
    required, optional = MODS[mod_name]
    missing = [src for src, _ in required if not src.is_file()]
    if missing:
        print(f"ERROR: {missing[0]} not found — build it first with "
              f"tes_runtime\\build.bat.")
        return 1

    zip_path = finished_dir(out_root) / f"{mod_name}.zip"

    print("=" * 54)
    print("  PACKAGE RUNTIME DLL")
    print("=" * 54)
    print(f"  Source: {DIST_DIR}")
    print(f"  Output: {zip_path}")
    print()

    members = [(str(arc), src) for src, arc in required]
    for src, arc in optional:
        if src.is_file():
            members.append((str(arc), src))
        else:
            print(f"  - {arc} (not built, skipped)")
    if mod_name == MOD_NAME:
        menus = morrowind_menus(export_root, choice, chargen)
        if menus is None:
            print("  - Morrowind menus (no Morrowind install registered, skipped)")
        else:
            style, icons = resolve(str(export_root), *choice)
            print(f"  menus: {style} look, {icons or 'no'} icons")
            members += [(str(arc), data) for arc, data in menus]
        members += [(str(arc), data) for arc, data in chargen_table(export_root, chargen)]
    write_mod_zip(zip_path, members, lambda _i, arc: print(f"  + {arc}"))

    size = zip_path.stat().st_size
    print()
    print(f"Packaged -> {zip_path} ({size:,} bytes)")
    print("Install it like any other converted mod: the archive root is the "
          "Data folder.")
    return 0


def main() -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(
        description="Package the runtime DLLs as one standalone SKSE mod.")
    ap.add_argument("--output-dir", metavar="PATH",
                    help="Output directory (default: output/ in project root)")
    ap.add_argument("--mod", choices=sorted(MODS), default=MOD_NAME,
                    help=f"Which archive to build (default: {MOD_NAME})")
    ap.add_argument("--export-root", metavar="PATH", default=str(EXPORT_ROOT),
                    help="Where the Morrowind install is registered "
                         "(default: export/ in project root)")
    ap.add_argument("--menu-style", choices=STYLES, default=None,
                    help="The menus' look (default: the config's menuStyle)")
    ap.add_argument("--menu-icons", choices=ICON_SETS, default=None,
                    help="The menus' icons (default: the config's menuIcons)")
    ap.add_argument("--chargen-source", metavar="PLUGIN", default=None,
                    help="The exported game whose classes and birthsigns the "
                         "chargen menus show (default: the config's chargenSource)")
    args = ap.parse_args()
    out_root = (Path(args.output_dir) if args.output_dir
                else SCRIPT_DIR / "output")
    style, icons = configured_choice()
    return package(out_root, args.mod, Path(args.export_root),
                   (args.menu_style or style, args.menu_icons or icons), args.chargen_source)


if __name__ == "__main__":
    sys.exit(main())
