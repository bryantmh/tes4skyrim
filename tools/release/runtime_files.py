"""The files each runtime mod ships, and whether a packaged folder still matches them.

Kept apart from `package_runtime_dll` so the GUI can check the packaged copies without importing the menu
generators that packaging needs.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

MOD_NAME = "TESRuntime"

SRC_DIR = REPO_ROOT / "tes_runtime"

#: Where tes_runtime/build.bat puts every finished DLL.
DIST_DIR = SRC_DIR / "dist"

PLUGINS = Path("SKSE") / "Plugins"

#: MorrowindRuntime's dialogue window, composed only when a Morrowind install is registered.
DIALOGUE_MENU = Path("Interface") / "morrowind_dialogue.swf"

CREATURE_RUNTIME = ((DIST_DIR / "CreatureRuntime.dll", PLUGINS / "CreatureRuntime.dll"),)

HAVOK_WORLD_SIZE = (
    (DIST_DIR / "HavokWorldSize.dll", PLUGINS / "HavokWorldSize.dll"),
    (SRC_DIR / "havok_world_size" / "HavokWorldSize.ini",
     PLUGINS / "HavokWorldSize.ini"),
)

#: Mod name -> (required files, optional files), each file a (source, archive path) pair.
MODS = {
    MOD_NAME: (
        ((DIST_DIR / "TESRuntime.dll", PLUGINS / "TESRuntime.dll"),
         (DIST_DIR / "ArchiveLoader.dll", PLUGINS / "ArchiveLoader.dll")),
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


def _same_file(a: Path, b: Path) -> bool:
    """Same size and whole-second mtime (packaging copies with `copy2`); False when either is missing."""
    try:
        sa, sb = a.stat(), b.stat()
    except OSError:
        return False
    return sa.st_size == sb.st_size and int(sa.st_mtime) == int(sb.st_mtime)


def packaged_state(folder: Path, mod_name: str = MOD_NAME) -> list:
    """`(archive path, matches)` for every built source of `mod_name`, against its copy under `folder`."""
    required, optional = MODS[mod_name]
    return [(arc.as_posix(), _same_file(src, Path(folder) / arc))
            for src, arc in required + optional if src.is_file()]
