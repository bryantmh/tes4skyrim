"""The Morrowind switches, with no toolkit: the mode every Morrowind plugin converts in, the
Morroblivion compatibility patch that mode needs, and the menus every converted game shares.

The export stage reads the mode from `conversion_config.json`. Morroblivion and the hybrid both
need Morrowind_ob.esm converted first; Morroblivion also needs its compatibility patch, built
from the converted Morroblivion and the Morrowind Data Files.
See: docs/commentary/tes4_export_morrowind.md#masters
"""

import sys

from asset_convert.ui.menu_art import (ICONS_MORROWIND, ICONS_OBLIVION, MENU_ICONS_KEY, MENU_STYLE_KEY,
                                       STYLE_MORROWIND, STYLE_SKYRIM, available, resolve)
from core.chargen_source import CHARGEN_SOURCE_KEY, candidates, chosen_source
from core.python_cmd import python_cmd
from core.gui.config import EXPORT_DIR, REPO_ROOT
from tes4_export.export_morrowind import (MORROWIND_SOURCE_KEY, SOURCE_HYBRID, SOURCE_MORROBLIVION,
                                          SOURCE_VANILLA, morroblivion_exports)
from tes4_export.morrowind_patch import PATCH_NAME, PATCH_SOURCES, patch_exists, source_dir, source_paths

#: The modes in the order offered: (key, name, what it converts, what it needs).
MODES = (
    (SOURCE_VANILLA, "Vanilla",
     "Morrowind, Tribunal and Bloodmoon as authored, with Morrowind's own meshes and textures",
     "Needs nothing else"),
    (SOURCE_MORROBLIVION, "Morroblivion",
     f"Converts against Morroblivion and {PATCH_NAME}, built automatically",
     "Needs Morrowind_ob.esm converted first"),
    (SOURCE_HYBRID, "Vanilla + Morroblivion meshes",
     "Morrowind records as authored; reviewed Morroblivion meshes, furniture and higher-resolution textures",
     "Needs Morrowind_ob.esm converted first"),
)

#: Menu look choices: (config value, label, the art it needs or None).
LOOKS = ((STYLE_MORROWIND, "Morrowind", ICONS_MORROWIND), (STYLE_SKYRIM, "Skyrim", None))
#: Menu icon choices: (config value, label, the art it needs).
ICONS = ((ICONS_MORROWIND, "Morrowind", ICONS_MORROWIND), (ICONS_OBLIVION, "Oblivion", ICONS_OBLIVION))
#: Which config key each shared-menu choice is saved under.
MENU_KEYS = {"look": MENU_STYLE_KEY, "icons": MENU_ICONS_KEY, "chargen": CHARGEN_SOURCE_KEY}

# ---------------------------------------------------------------------------
#  The mode
# ---------------------------------------------------------------------------


def saved_mode(cfg: dict):
    """The mode the user chose, or None when they have never been asked."""
    value = str(cfg.get(MORROWIND_SOURCE_KEY, "")).strip().lower()
    return value if value in {m[0] for m in MODES} else None


def mode_index(key) -> int:
    """`key`'s position in MODES (vanilla when unknown)."""
    return next((i for i, m in enumerate(MODES) if m[0] == key), 0)


def mode_name(key) -> str:
    """A mode's display name."""
    return MODES[mode_index(key)][1]


def needs_morroblivion(key) -> bool:
    """True for the modes that need Morrowind_ob.esm converted first."""
    return key in (SOURCE_MORROBLIVION, SOURCE_HYBRID)


def needs_patch(key) -> bool:
    """True for the mode that converts against the compatibility patch."""
    return key == SOURCE_MORROBLIVION


def morroblivion_converted() -> bool:
    """True when Morrowind_ob.esm has been converted."""
    return bool(morroblivion_exports(EXPORT_DIR))


def patch_built() -> bool:
    """True when the Morroblivion compatibility patch exists."""
    return patch_exists(str(EXPORT_DIR))


def patch_data_dir() -> str:
    """The registered Morrowind Data Files folder holding the patch's sources, or ""."""
    known = source_dir(str(EXPORT_DIR))
    return known if known and not source_paths(known, PATCH_SOURCES)[1] else ""


def missing_patch_sources(data_dir: str) -> list:
    """The Morrowind masters `data_dir` lacks for building the patch."""
    return source_paths(data_dir, PATCH_SOURCES)[1]


def patch_cmd(session, data_dir: str) -> list:
    """The command that builds the compatibility patch."""
    return python_cmd(REPO_ROOT / "convert.py", "--build-morrowind-patch", data_dir,
                      "--output-dir", session.out_root())


# ---------------------------------------------------------------------------
#  Shared menus
# ---------------------------------------------------------------------------

def menu_choices(cfg: dict) -> dict:
    """{"look"|"icons"|"chargen": (labels, current index, config values)}.

    A look or icon set whose art no registered install has is left out; classes and birthsigns
    list the converted games that have them.
    """
    have = available(EXPORT_DIR)
    built = resolve(EXPORT_DIR, cfg.get(MENU_STYLE_KEY), cfg.get(MENU_ICONS_KEY))
    out = {}
    for name, table, current in (("look", LOOKS, built[0]), ("icons", ICONS, built[1])):
        usable = [(value, label) for value, label, needs in table if needs is None or have[needs]]
        values = [v for v, _l in usable]
        out[name] = ([label for _v, label in usable], values.index(current) if current in values else 0, values)
    names = candidates(EXPORT_DIR)
    pick = chosen_source(EXPORT_DIR, names, cfg.get(CHARGEN_SOURCE_KEY))
    out["chargen"] = (names, names.index(pick) if pick in names else 0, names)
    return out
