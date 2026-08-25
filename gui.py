"""
TES4 Auto-Convert — GUI

Usage:
  python gui.py          # open GUI
  python gui.py --cli    # headless CLI wrapper (see --help)
"""

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import queue
import time
from pathlib import Path

SCRIPT_DIR  = Path(__file__).parent.resolve()
CONFIG_FILE = SCRIPT_DIR / "conversion_config.json"

from worker_budget import worker_count, cpu_total, WORKERS_ENV_VAR

# The cache opt-out variable name is owned by tools/navmesh_cache.py so the GUI
# and convert.py cannot drift apart (see test_no_download_env_var_is_shared...).
# `tools/` needs its __init__.py for this to resolve from any cwd -- without it
# the directory is only a NAMESPACE package, and a module-scope import here was
# fatal to the entire GUI. Under gui.pyw (pythonw, no console) the traceback is
# invisible, so the window simply never appeared; convert.py survived only
# because it imports this lazily inside a function.
from tools.navmesh_cache import NO_DOWNLOAD_ENV_VAR
import version as version_info
import run_log
from preflight import RC_MISSING_DEP as _RC_MISSING_DEP
from collision_options import (
    WINDING_FIX_DEFAULT_PLUGINS,
    default_for_plugin as _winding_default,
)

# ── Pipeline steps ─────────────────────────────────────────────────────────
# (key, cli_flag, label, description, default_on, needs_file)
STEPS = [
    ("export",             "--export-only",        "1. Export",
     "Parse TES4 binary into a text cache",          True,  True),
    ("extract",            "--extract-only",       "2. Extract",
     "Pull assets from TES4 BSA archives",            True,  True),
    ("meshes",             "--meshes-only",        "3. Meshes",
     "Convert standard NIFs and copy textures",                 True,  True),
    ("speedtrees",         "--speedtrees-only",    "4. SpeedTrees",
     "Convert SPT files",           True,  True),
    ("creatures",          "--creatures-only",     "5. Creatures",
     "Convert creature models and animations",       True,  True),
    ("import_",            "--import-only",        "6. Import",
     "Build TES5 ESM/ESP from text cache",       True,  True),
    ("sounds",             "--sounds-only",        "7. Sounds",
     "Convert voice files to XWM and copy sounds",               True,  True),
    ("scripts",            "--scripts-only",       "8. Scripts",
     "Convert Oblivion scripts to Papyrus",      True,  True),
    ("nemesis",            "--nemesis-only",       "Nemesis baseline",
     "keeps creatures registered when Nemesis runs", False, True),
    ("pack",               "--pack-only",          "9. Pack BSAs",
     "Pack assets into BSA archives",             False, True),
    ("pack_zip",           "--pack-zip-only",      "10. Pack Mod Zip",
     "Zip mod files for installation",   True,  True),
]

# Steps rendered INDENTED under another step instead of as a numbered row of
# their own. They are still real steps -- `--nemesis-only` has to stay runnable
# on its own, because Nemesis has to be re-fed after every one of ITS
# regenerations, which has nothing to do with reconverting creatures -- they
# just belong to their parent conceptually and get no number.
SUB_OF = {"nemesis": "creatures"}
# Nemesis is off by default (the False above): the patch only means anything in
# a load order that actually runs Nemesis Unlimited Behavior Engine. Where it IS
# run, Nemesis rebuilds animationdatasinglefile.txt from the vanilla project
# list and its own output wins the conflict, so every converted creature project
# silently de-registers and creatures slide without animating.
# LOD is deliberately NOT here. It is not per-plugin work: sibling plugins share
# a tile grid, so baking "master + this plugin" once per plugin generates the
# contested tiles once per sibling and then throws all but one away. It is a
# global action ("Create LOD") that runs the whole load order in one pass.

# The two packing steps. They are the only steps whose default tick state is a
# user SETTING (Settings ▸ Pack by default) rather than a constant: packing is
# the tail of the pipeline and anyone iterating on a conversion re-runs the
# earlier steps dozens of times without ever wanting a BSA or a zip rebuilt.
# Turning the setting off makes "Default" (and a fresh launch) leave them clear
# without also hiding them — they stay tickable for the run that does want them.
PACKING_STEPS = ("pack", "pack_zip")

# Steps that start UNTICKED regardless of any setting. Unlike the packing pair
# these are not "the tail of the pipeline everyone re-runs"; they are only
# meaningful for a particular load order. Ticking Nemesis for someone who does
# not run Nemesis writes a patch folder that nothing ever reads.
OPT_IN_STEPS = ("nemesis",)

# Persisted under this key in conversion_config.json. Absent (or any non-false
# value) means ON, so a config written before this option existed keeps the old
# behaviour instead of silently dropping the packing steps from a run.
PACK_DEFAULT_CONFIG_KEY = "packStepsDefaultOn"

# conversion_config.json key holding the Nemesis MOD folder (not its meshes
# subfolder -- see Tools > Set Nemesis Folder). The "Nemesis baseline" step
# reads its shipped nemesis_*singlefile.txt pair from there and never writes to
# it.
NEMESIS_DIR_CONFIG_KEY = "nemesisDir"

# The INFERRED collision winding steps (Settings > Infer collision winding).
# The authored-normal repair always runs and is not covered by this key. A
# tri-state, persisted here: "auto" keeps the measured per-plugin default (on
# for the plugins in WINDING_FIX_DEFAULT_PLUGINS, off for everything else),
# while "on"/"off" pin it for every plugin. Anything unrecognised -- including a
# config written before this setting existed -- reads as "auto", so the defaults
# are unchanged for anyone who never touches it.
WINDING_CONFIG_KEY = "collisionWindingFix"
WINDING_AUTO, WINDING_ON, WINDING_OFF = "auto", "on", "off"
WINDING_MODES = (WINDING_AUTO, WINDING_ON, WINDING_OFF)


def winding_enabled_for(mode: str, plugin: str) -> bool:
    """Whether the winding repair runs, given the setting and the plugin."""
    if mode == WINDING_ON:
        return True
    if mode == WINDING_OFF:
        return False
    return _winding_default(plugin)


def default_on_steps(pack_default: bool = True) -> set:
    """Step keys that start ticked, given the Pack-by-default setting.

    Every step is on by default except the opt-in ones; the packing pair is
    additionally gated on `pack_default`. Note this deliberately does NOT read
    the `default_on` column of STEPS — that column drives the "did the user keep
    the default selection?" check in the runner, which is a separate question
    from what the checkboxes start at.
    """
    keys = {k for k, *_ in STEPS} - set(OPT_IN_STEPS)
    if not pack_default:
        keys -= set(PACKING_STEPS)
    return keys

# ── Global actions ────────────────────────────────────────────────────────────
# Work that belongs to NO single plugin: it takes no `-f`, runs once for the
# whole load order, and produces one shared artefact every conversion uses.
#
# Both used to be (or would have been) step checkboxes, which was wrong in the
# same way for both. A checkbox in the numbered list reads as "part of
# converting THIS plugin", so ticking it while converting Oblivion looked like
# it left the job undone for Nehrim — even though the single shared output
# already covered both. They are buttons now: pressed once, they apply to
# everything, and they grey out until something makes them stale again.
#
# `label` is the full name (menu entries, log lines); `short` is what fits on
# the side-by-side sidebar buttons.
# `label` is the full name (menu entries, log lines); `short` is what fits on
# the side-by-side sidebar buttons. `row` groups them into sidebar rows.
# (key, label, tooltip, short, row)
GLOBAL_ACTIONS = [
    # Row 0: bake the LOD, then wrap it. Left-to-right IS the order they run in,
    # and "Pack LOD" has nothing to zip until "Create LOD" has produced it.
    ("create_lod", "Create LOD",
     "Generate distant LOD for the whole load order in one pass, into the "
     "standalone output/AutoConvertLOD mod",
     "Create LOD", 0),
    ("pack_lod", "Pack LOD",
     "Zip the generated AutoConvertLOD folder into output/Finished Mods, "
     "ready to install like any converted plugin",
     "Pack LOD", 0),
    # Row 1. Not tied to one plugin: the ESM flag has to be applied to a whole
    # dependency chain at once, because an ESM may not master a plain ESP.
    ("make_master", "Convert to Master",
     "Flag converted plugins as masters (ESM). A plugin that is NOT a master "
     "has every reference it contains treated as always-active, and the engine "
     "caps those at 1,048,576 — past that the game hangs on the main menu with "
     "no crash and no log. Applies to a whole master chain at once",
     "To Master", 1),
    # Depends on nothing else — the starter mod is committed prebuilt, so it can
    # be packaged before anything has been converted.
    ("package_start_mod", "Package Start Mod",
     "Zip the prebuilt TESGameSelect starter mod (the new-game world "
     "selector) into output/Finished Mods, ready to install like any "
     "converted plugin",
     "Pack Start Mod", 1),
    # Row 2.
    ("modify_body_meshes", "Patch Skyrim",
     "Build the ARMA slot-44 body patch for your Skyrim load order",
     "Patch Skyrim", 2),
]

# ── Colors ───────────────────────────────────────────────────────────────────
CLR = {
    "bg":           "#1e1e2e",
    "panel":        "#2a2a3d",
    "border":       "#44475a",
    "accent":       "#7c6af7",
    "accent_hover": "#9a8cf8",
    "btn":          "#313244",
    "btn_hover":    "#45475a",
    "green":        "#a6e3a1",
    "red":          "#f38ba8",
    "yellow":       "#f9e2af",
    "blue":         "#89dceb",
    "text":         "#cdd6f4",
    "subtext":      "#6c7086",
    "log_bg":       "#141420",
    "log_fg":       "#cdd6f4",
    "log_info":     "#89b4fa",
    "log_ok":       "#a6e3a1",
    "log_err":      "#f38ba8",
    "log_warn":     "#f9e2af",
    "check_on":     "#7c6af7",
    "check_off":    "#44475a",
    "gold":         "#c9a35c",
    "gold_hover":   "#ddb96f",
    # Global (non-pipeline) actions — teal, to read as a different class of
    # button from the gold Run and the purple accents.
    "global_btn":   "#26404a",
    "global_hover": "#325764",
}


# Loaded HICONs are kept alive for the process lifetime; freeing them would
# blank the taskbar button.
_ICON_HANDLES = []


def _set_app_user_model_id() -> None:
    """Give the process its own taskbar identity so it shows OUR icon.

    Windows groups taskbar buttons by AppUserModelID, and a bare script
    inherits the one python.exe/pythonw.exe registered for itself -- so the
    taskbar shows the Python logo no matter what `iconbitmap` does to the
    window. Claiming a distinct ID detaches us from that group, after which
    the taskbar falls back to the window's own icon.

    This has to run BEFORE the first window exists: the shell reads the ID
    when the button is created, and changing it afterwards does nothing.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "TESConversion.AutoConvert.GUI")
    except Exception:
        pass  # non-Windows shell, or an older build without the API


def _set_window_icon(root, icon_path) -> None:
    """Load the .ico at both icon sizes Windows asks for.

    Tk's `iconbitmap` only ever supplies the SMALL icon (title bar / Alt-Tab).
    The taskbar button and the large Alt-Tab overlay ask for ICON_BIG, which Tk
    never sets, so the shell substitutes a generic one. LoadImage picks the
    best-matching frame out of our multi-resolution .ico for each request.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()

        IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x0010, 0x0040
        WM_SETICON, ICON_SMALL, ICON_BIG = 0x0080, 0, 1
        metrics = {
            ICON_BIG: (ctypes.windll.user32.GetSystemMetrics(11),   # SM_CXICON
                       ctypes.windll.user32.GetSystemMetrics(12)),
            ICON_SMALL: (ctypes.windll.user32.GetSystemMetrics(49),  # SM_CXSMICON
                         ctypes.windll.user32.GetSystemMetrics(50)),
        }

        user32 = ctypes.windll.user32
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                        wintypes.WPARAM, wintypes.LPARAM]

        for which, (cx, cy) in metrics.items():
            flags = LR_LOADFROMFILE | (LR_DEFAULTSIZE if not cx else 0)
            handle = user32.LoadImageW(None, str(icon_path), IMAGE_ICON,
                                       cx, cy, flags)
            if handle:
                # Keep a reference: destroying the icon would blank the button.
                _ICON_HANDLES.append(handle)
                user32.SendMessageW(hwnd, WM_SETICON, which, handle)
    except Exception:
        pass  # any failure just leaves Tk's own icon in place


def _style_titlebar(root) -> None:
    """Recolor the native Windows title bar to match the app's dark/purple theme."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())

        def _bgr(hexcolor: str) -> int:
            hexcolor = hexcolor.lstrip("#")
            r, g, b = (int(hexcolor[i:i + 2], 16) for i in (0, 2, 4))
            return r | (g << 8) | (b << 16)

        DWMWA_CAPTION_COLOR = 35
        DWMWA_TEXT_COLOR = 36
        dwmapi = ctypes.windll.dwmapi
        caption = ctypes.c_int(_bgr(CLR["bg"]))
        text = ctypes.c_int(_bgr(CLR["text"]))
        dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_CAPTION_COLOR, ctypes.byref(caption), ctypes.sizeof(caption))
        dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_TEXT_COLOR, ctypes.byref(text), ctypes.sizeof(text))
    except Exception:
        pass  # older Windows builds (<22H2) don't support these attributes


# ── Config helpers ────────────────────────────────────────────────────────────

def _find_game_path(game: str) -> str:
    """Auto-detect game data path from the Windows registry."""
    try:
        import winreg
        keys = {
            "oblivion": [
                (winreg.HKEY_LOCAL_MACHINE,
                 r"SOFTWARE\WOW6432Node\Bethesda Softworks\Oblivion"),
                (winreg.HKEY_LOCAL_MACHINE,
                 r"SOFTWARE\Bethesda Softworks\Oblivion"),
            ],
            "skyrimse": [
                (winreg.HKEY_LOCAL_MACHINE,
                 r"SOFTWARE\WOW6432Node\Bethesda Softworks\Skyrim Special Edition"),
                (winreg.HKEY_LOCAL_MACHINE,
                 r"SOFTWARE\Bethesda Softworks\Skyrim Special Edition"),
            ],
        }
        for hkey, subkey in keys.get(game, []):
            try:
                with winreg.OpenKey(hkey, subkey) as key:
                    path, _ = winreg.QueryValueEx(key, "Installed Path")
                    data = os.path.join(path, "Data")
                    if os.path.isdir(data):
                        return data
            except (FileNotFoundError, OSError):
                continue
    except ImportError:
        pass
    return ""


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_config(cfg: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)


def scan_plugins(data_path: str) -> list:
    """Return sorted list of .esm/.esp files in data_path."""
    if not data_path or not os.path.isdir(data_path):
        return []
    plugins = []
    for name in sorted(os.listdir(data_path)):
        if name.lower().endswith(('.esm', '.esp')):
            plugins.append(name)
    return plugins


EXPORT_DIR = SCRIPT_DIR / "export"

# Set by _make_root(): True once the tkdnd runtime is loaded and the sidebar
# can accept dropped files. Without it the window still works -- Mods > Import
# is the same feature by a different door -- so nothing here may be fatal.
DND_AVAILABLE = False


def _make_root():
    """The Tk root, drag-and-drop capable when tkinterdnd2 is installed.

    tkinterdnd2 works by subclassing Tk and loading the tkdnd Tcl package, so
    it has to be chosen HERE, at root creation, rather than bolted on later.

    The fallback imports tkinter LOCALLY. `tk` is bound inside gui_main(), not
    at module scope, so reaching for a bare `tk.Tk()` here raised NameError on
    every machine without tkinterdnd2 -- and because main() relaunches under
    pythonw, that traceback had no console to print to and the window simply
    never appeared. Anyone with the package installed (the author) never saw it.
    """
    global DND_AVAILABLE
    _set_app_user_model_id()
    import tkinter
    try:
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
        DND_AVAILABLE = True
        return root
    except Exception:
        # Missing package, or a tkdnd that failed to load on this platform.
        return tkinter.Tk()


def parse_dropped_paths(data: str) -> list:
    """Split a tkdnd <Drop> payload into real filesystem paths.

    tkdnd hands over a Tcl list: paths with spaces are wrapped in braces
    ("{C:/My Mods/a.zip} C:/b.zip"), and a single unbraced path may still
    contain spaces. Tk's own splitlist handles the quoting rules correctly, but
    it needs a widget to call through, so this falls back to a manual parse.
    """
    if not data:
        return []
    out, buf, depth = [], '', 0
    for ch in data:
        if ch == '{':
            depth += 1
            if depth == 1:
                continue
        elif ch == '}':
            depth -= 1
            if depth == 0:
                out.append(buf)
                buf = ''
                continue
        elif ch == ' ' and depth == 0:
            if buf:
                out.append(buf)
                buf = ''
            continue
        buf += ch
    if buf:
        out.append(buf)
    return [p for p in (s.strip() for s in out) if p]


def scan_converted(output_path: str) -> list:
    """Plugins already converted into `output_path`, sorted.

    Keyed on `<name>.manifest.json`, which `phase_import` writes per plugin.
    Listing the directory names instead would offer things that were never a
    source plugin: `output/` also accumulates the standalone `Slot44 Patch.esp`,
    the `TESGameSelect` folder and `<plugin>.zip` archives, none of which can be
    re-run.  The manifest's `source` field names the plugin the pipeline was
    invoked with, which is exactly what re-running needs.
    """
    if not output_path or not os.path.isdir(output_path):
        return []
    found = set()
    try:
        entries = sorted(os.listdir(output_path))
    except OSError:
        return []
    for name in entries:
        folder = os.path.join(output_path, name)
        if not os.path.isdir(folder):
            continue
        # A folder named for its plugin holds ONE manifest; a mod GROUP folder
        # is named for the mod and holds one per plugin it converted, so glob
        # rather than probing for `<folder>.manifest.json`.
        try:
            manifests = sorted(f for f in os.listdir(folder)
                               if f.endswith(".manifest.json"))
        except OSError:
            continue
        for manifest_name in manifests:
            manifest = os.path.join(folder, manifest_name)
            source = manifest_name[:-len(".manifest.json")]
            try:
                with open(manifest, encoding="utf-8") as fh:
                    got = json.load(fh).get("source")
                if isinstance(got, str) and got.strip():
                    source = got.strip()
            except (OSError, ValueError):
                pass      # a truncated manifest still proves the folder is ours
            found.add(source)
    return sorted(found, key=str.lower)


# Base game + official Creation Club content, in Bethesda's own load-order
# priority (the order the game/CC installer expects them in, independent of
# whatever plugins.txt says) — these are always listed first, and default to
# checked whenever they're actually present in the Data folder.
_OFFICIAL_PLUGINS = [
    "Skyrim.esm", "Update.esm", "Dawnguard.esm", "HearthFires.esm", "Dragonborn.esm",
    "ccasvsse001-almsivi.esm", "ccbgssse001-fish.esm", "ccbgssse002-exoticarrows.esl",
    "ccbgssse003-zombies.esl", "ccbgssse004-ruinsedge.esl", "ccbgssse005-goldbrand.esl",
    "ccbgssse006-stendarshammer.esl", "ccbgssse007-chrysamere.esl",
    "ccbgssse010-petdwarvenarmoredmudcrab.esl", "ccbgssse011-hrsarmrelvn.esl",
    "ccbgssse012-hrsarmrstl.esl", "ccbgssse014-spellpack01.esl",
    "ccbgssse019-staffofsheogorath.esl", "ccbgssse020-graycowl.esl",
    "ccbgssse021-lordsmail.esl", "ccmtysse001-knightsofthenine.esl",
    "ccqdrsse001-survivalmode.esl", "cctwbsse001-puzzledungeon.esm",
    "cceejsse001-hstead.esm", "ccqdrsse002-firewood.esl", "ccbgssse018-shadowrend.esl",
    "ccbgssse035-petnhound.esl", "ccfsvsse001-backpacks.esl", "cceejsse002-tower.esl",
    "ccedhsse001-norjewel.esl", "ccvsvsse002-pets.esl", "ccbgssse037-curios.esl",
    "ccbgssse034-mntuni.esl", "ccbgssse045-hasedoki.esl", "ccbgssse008-wraithguard.esl",
    "ccbgssse036-petbwolf.esl", "ccffbsse001-imperialdragon.esl", "ccmtysse002-ve.esl",
    "ccbgssse043-crosselv.esl", "ccvsvsse001-winter.esl", "cceejsse003-hollow.esl",
    "ccbgssse016-umbra.esm", "ccbgssse031-advcyrus.esm", "ccbgssse038-bowofshadows.esl",
    "ccbgssse040-advobgobs.esl", "ccbgssse050-ba_daedric.esl", "ccbgssse052-ba_iron.esl",
    "ccbgssse054-ba_orcish.esl", "ccbgssse058-ba_steel.esl",
    "ccbgssse059-ba_dragonplate.esl", "ccbgssse061-ba_dwarven.esl",
    "ccpewsse002-armsofchaos.esl", "ccbgssse041-netchleather.esl",
    "ccedhsse002-splkntset.esl", "ccbgssse064-ba_elven.esl", "ccbgssse063-ba_ebony.esl",
    "ccbgssse062-ba_dwarvenmail.esl", "ccbgssse060-ba_dragonscale.esl",
    "ccbgssse056-ba_silver.esl", "ccbgssse055-ba_orcishscaled.esl",
    "ccbgssse053-ba_leather.esl", "ccbgssse051-ba_daedricmail.esl",
    "ccbgssse057-ba_stalhrim.esl", "ccbgssse066-staves.esl", "ccbgssse067-daedinv.esm",
    "ccbgssse068-bloodfall.esl", "ccbgssse069-contest.esl", "ccvsvsse003-necroarts.esl",
    "ccvsvsse004-beafarmer.esl", "ccbgssse025-advdsgs.esm", "ccffbsse002-crossbowpack.esl",
    "ccbgssse013-dawnfang.esl", "ccrmssse001-necrohouse.esl", "ccedhsse003-redguard.esl",
    "cceejsse004-hall.esl", "cceejsse005-cave.esm", "cckrtsse001_altar.esl",
    "cccbhsse001-gaunt.esl", "ccafdsse001-dwesanctuary.esm", "_ResourcePack.esl",
]


def scan_skyrim_plugins(data_path: str) -> list:
    """Return sorted list of .esm/.esp/.esl files in data_path."""
    if not data_path or not os.path.isdir(data_path):
        return []
    return sorted(name for name in os.listdir(data_path)
                 if name.lower().endswith(('.esm', '.esp', '.esl')))


def scan_skyrim_load_order(data_path: str) -> tuple:
    """Return (ordered_names, default_checked_set) for the Skyrim plugin picker.

    Order: base game + official Creation Club content first (in Bethesda's
    own priority order), then any other plugins.txt entries in load order,
    then any remaining installed-but-unlisted plugins last. Only the first
    two groups (official content and anything plugins.txt actually lists)
    default to checked — a plugin sitting in Data/ that neither list
    mentions is surfaced but starts unchecked.
    """
    installed = {name.lower(): name for name in scan_skyrim_plugins(data_path)}
    if not installed:
        return [], set()

    ordered = []
    seen = set()
    for name in _OFFICIAL_PLUGINS:
        found = installed.get(name.lower())
        if found and found not in seen:
            ordered.append(found)
            seen.add(found)
    default_checked = set(ordered)

    plugins_txt = (Path(os.environ.get("LOCALAPPDATA", ""))
                   / "Skyrim Special Edition" / "plugins.txt")
    if plugins_txt.exists():
        try:
            with open(plugins_txt, "r", encoding="utf-8-sig", errors="replace") as fh:
                for raw_line in fh:
                    line = raw_line.strip()
                    if not line or line.startswith("#"):
                        continue
                    # A leading "*" means the plugin is active; without it,
                    # plugins.txt is just listing a disabled/known plugin.
                    active = line.startswith("*")
                    line = line.lstrip("*")
                    name = installed.get(line.lower())
                    if name and name not in seen:
                        ordered.append(name)
                        seen.add(name)
                        if active:
                            default_checked.add(name)
        except OSError:
            pass

    for name in installed.values():
        if name not in seen:
            ordered.append(name)
            seen.add(name)
    return ordered, default_checked


def scan_mesh_subdirs(file_name: str) -> list:
    """Return sorted list of root mesh subdirectories in export/<file_name>/meshes/."""
    if not file_name:
        return []
    mesh_dir = SCRIPT_DIR / "export" / file_name / "meshes"
    if not mesh_dir.is_dir():
        return []
    subdirs = sorted(
        d.name for d in mesh_dir.iterdir()
        if d.is_dir()
    )
    return subdirs


# ── Subprocess helper ─────────────────────────────────────────────────────────

# On Windows, hide the console window that subprocess.Popen would otherwise
# create when launched from a console-less process (pythonw / .pyw).
from subprocess_flags import POPEN_FLAGS as _POPEN_FLAGS, configure_multiprocessing
from process_job import create_pool_job

configure_multiprocessing()

# Contain the whole conversion in a Job Object owned by this GUI process. The
# Cancel button (`_kill_process_tree`) only covers a *deliberate* stop; if the
# GUI itself dies without cleanup — crash, Task Manager, window closed — the
# kernel terminates convert.py and every pool worker with it. Without this, the
# workers are console-less pythonw.exe processes that survive invisibly, holding
# the export index in RAM and keeping handles open on output/ files.
create_pool_job()


def _kill_process_tree(proc):
    """Forcibly kill `proc` and every descendant it spawned.

    ``proc.terminate()`` only signals the direct child (convert.py); the
    conversion spawns grandchildren — multiprocessing Pool workers plus helper
    .exes (ffmpeg, hkxcmd, BSArch, LODGen). Those must be killed too or they
    keep running and hold the stdout pipe open, so cancellation appears to hang.

    On Windows, ``taskkill /T`` walks the whole tree by PID. Fall back to
    ``proc.kill()`` if taskkill is unavailable or errors.
    """
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=15,
                **_POPEN_FLAGS,
            )
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        proc.kill()
    except OSError:
        pass


def _run_process(cmd, log_cb, env=None, cancel_event=None):
    """Run cmd as subprocess, streaming output to `log_cb` as bytes arrive.

    A dedicated reader thread pulls bytes off the pipe so the control loop can
    poll `cancel_event` on a short interval — a blocking pipe read must never be
    what stands between the user clicking Cancel and the process dying.

    On cancellation the entire process tree is killed (see
    `_kill_process_tree`) and -2 is returned.
    """
    try:
        full_env = os.environ.copy()
        full_env["PYTHONUNBUFFERED"] = "1"
        if env:
            full_env.update(env)

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,  # unbuffered binary mode
            cwd=str(SCRIPT_DIR),
            env=full_env,
            **_POPEN_FLAGS,
        )

        out = proc.stdout
        line_q: "queue.Queue" = queue.Queue()

        def _reader():
            """Read bytes off the pipe, split into lines, push onto line_q."""
            buf = bytearray()
            try:
                while True:
                    chunk = out.read(1024)
                    if not chunk:
                        break
                    buf.extend(chunk)
                    while True:
                        nl = buf.find(b"\n")
                        if nl == -1:
                            break
                        line = bytes(buf[:nl + 1])
                        del buf[:nl + 1]
                        line_q.put(line.decode("utf-8", errors="replace")
                                   .rstrip("\r\n"))
            except (OSError, ValueError):
                pass
            finally:
                if buf:
                    text = bytes(buf).decode("utf-8", errors="replace").rstrip("\r\n")
                    if text:
                        line_q.put(text)
                line_q.put(None)  # sentinel: pipe closed

        reader = threading.Thread(target=_reader, daemon=True)
        reader.start()

        cancelled = False
        while True:
            # Cancel takes effect within one poll interval, even if the child
            # is silent or blocked deep inside a long-running step.
            if cancel_event is not None and cancel_event.is_set():
                _kill_process_tree(proc)
                cancelled = True
                break

            try:
                item = line_q.get(timeout=0.1)
            except queue.Empty:
                continue
            if item is None:  # pipe closed — process finished on its own
                break
            log_cb(item)

        if cancelled:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            return -2  # sentinel for cancelled

        proc.wait()
        return proc.returncode
    except Exception as exc:
        try:
            log_cb(f"ERROR: {exc}")
        except Exception:
            pass
        return -1


# ══════════════════════════════════════════════════════════════════════════════
#  GUI
# ══════════════════════════════════════════════════════════════════════════════

def gui_main():
    try:
        import tkinter as tk
        from tkinter import ttk, filedialog
    except ImportError:
        print("ERROR: tkinter not available")
        return 1

    # ── Load / init config ────────────────────────────────────────────────────
    cfg = load_config()
    tes4_path    = cfg.get("tes4DataPath", "") or _find_game_path("oblivion")
    tes5_path    = cfg.get("tes5DataPath", "") or _find_game_path("skyrimse")
    output_path  = cfg.get("outputDir", "")  or str(SCRIPT_DIR / "output")

    # Worker count: saved choice if valid, else the pipeline's own default.
    # Never allow more than the machine's logical CPU count.
    cpu_max = cpu_total()
    saved_workers = cfg.get("workers")
    try:
        workers_default = int(saved_workers)
    except (TypeError, ValueError):
        workers_default = worker_count()
    workers_default = max(1, min(workers_default, cpu_max))

    # Navmesh cache download: default ON. Only an explicit saved `false` turns
    # it off, so a config written before this option existed (or a corrupt
    # value) leaves the speed-up enabled rather than silently disabling it.
    cache_download_default = cfg.get("navmeshCacheDownload") is not False

    # Pack BSAs / Pack Mod Zip start ticked unless the user turned this off.
    # Same "only an explicit false disables it" rule as the cache option above.
    pack_default = cfg.get(PACK_DEFAULT_CONFIG_KEY) is not False

    # Collision winding repair mode. Anything that is not one of the three
    # recognised values -- including the key being absent -- means "auto", the
    # per-plugin default this setting replaced a checkbox for.
    winding_mode_default = str(cfg.get(WINDING_CONFIG_KEY, "")).strip().lower()
    if winding_mode_default not in WINDING_MODES:
        winding_mode_default = WINDING_AUTO

    # ── Root window ───────────────────────────────────────────────────────────
    # tkinterdnd2 supplies drag-and-drop by replacing the Tk root class. It is
    # OPTIONAL: without it the window still opens and Mods > Import... still
    # works, so a missing package can never stop the app from starting.
    root = _make_root()
    root.title(f"TES4 Auto-Convert  {version_info.current_version()}")
    # Slightly taller than the old 900 to fit the Global action row below the
    # steps. The progress bar needs no extra height of its own: it is anchored
    # to the bottom beside the status row, so it grows the pair upward rather
    # than demanding headroom that sits empty whenever no run is in flight.
    root.geometry("1060x1000")
    root.minsize(860, 740)
    root.configure(bg=CLR["bg"])
    root.option_add("*Background", CLR["bg"])
    root.option_add("*Foreground", CLR["text"])
    icon_path = SCRIPT_DIR / "docs" / "favicon.ico"
    root.iconbitmap(default=str(icon_path))
    _set_window_icon(root, icon_path)
    _style_titlebar(root)

    style = ttk.Style(root)
    style.theme_use("clam")

    def S(*a, **kw):
        style.configure(*a, **kw)

    S(".",             background=CLR["bg"], foreground=CLR["text"],
                       troughcolor=CLR["panel"], borderwidth=0, relief="flat")
    S("TFrame",        background=CLR["bg"])
    S("Panel.TFrame",  background=CLR["panel"])
    # The sidebar while a mod archive is hovering over it. Only the background
    # shifts: a ttk.Frame cannot draw a border without a relief that would
    # reflow every child, and a visible reflow on hover reads as a glitch.
    S("Drop.TFrame",   background=CLR["global_btn"])

    S("TLabel",        background=CLR["bg"],    foreground=CLR["text"])
    S("Sub.TLabel",    background=CLR["bg"],    foreground=CLR["subtext"],
                       font="Segoe\\ UI 9")
    S("Panel.TLabel",  background=CLR["panel"], foreground=CLR["text"])
    S("PanelSub.TLabel", background=CLR["panel"], foreground=CLR["subtext"],
                       font="Segoe\\ UI 9")
    S("Head.TLabel",   background=CLR["panel"], foreground=CLR["accent"],
                       font=("Segoe UI", 15, "bold"))
    S("Entry.TLabel",  background=CLR["panel"], foreground=CLR["subtext"],
                       font="Segoe\\ UI 9")

    S("TEntry",        fieldbackground=CLR["btn"], foreground=CLR["text"],
                       insertcolor=CLR["text"], borderwidth=1, relief="flat")

    S("TCombobox",     fieldbackground=CLR["btn"], background=CLR["btn"],
                       foreground=CLR["text"], arrowcolor=CLR["text"],
                       selectbackground=CLR["accent"],
                       selectforeground=CLR["text"], borderwidth=1, relief="flat")
    style.map("TCombobox",
              fieldbackground=[("readonly", CLR["btn"])],
              foreground=[("readonly", CLR["text"])])

    S("TButton",       background=CLR["btn"], foreground=CLR["text"],
                       borderwidth=1, relief="flat", padding=(8, 4),
                       font="Segoe\\ UI 10")
    style.map("TButton",
              background=[("active", CLR["btn_hover"]), ("disabled", CLR["border"])],
              foreground=[("disabled", CLR["subtext"])])

    S("Accent.TButton", background=CLR["accent"], foreground="#ffffff",
                        borderwidth=0, relief="flat", padding=(14, 6),
                        font="Segoe\\ UI 10 bold")
    style.map("Accent.TButton",
              background=[("active", CLR["accent_hover"]),
                          ("disabled", CLR["btn"])],
              foreground=[("disabled", CLR["subtext"])])

    S("Run.TButton",   background=CLR["gold"], foreground="#1e1e2e",
                        borderwidth=0, relief="flat", padding=(14, 6),
                        font="Segoe\\ UI 10 bold")
    style.map("Run.TButton",
              background=[("active", CLR["gold_hover"]),
                          ("disabled", CLR["btn"])],
              foreground=[("disabled", CLR["subtext"])])

    S("Cancel.TButton", background="#453030", foreground=CLR["red"],
                        borderwidth=0, relief="flat", padding=(8, 6),
                        font="Segoe\\ UI 10")
    style.map("Cancel.TButton",
              background=[("active", "#5a3030"), ("disabled", CLR["border"])],
              foreground=[("disabled", CLR["subtext"])])

    # Same padding and font as Cancel.TButton, which it shares a row with.
    # A smaller pad (8, 4) and the default font made it visibly shorter than
    # the button beside it.
    S("Danger.TButton", background="#453030", foreground=CLR["red"],
                        borderwidth=0, relief="flat", padding=(8, 6),
                        font="Segoe\\ UI 10")
    style.map("Danger.TButton", background=[("active", "#5a3030")])

    # ── Global actions ────────────────────────────────────────────────────────
    # These two ("Patch Skyrim", "Merge Sibling LOD") are NOT pipeline steps:
    # they take no plugin, run once for the whole load order, and their result
    # is shared by every conversion.  Deliberately teal and outlined rather than
    # gold-and-solid like Run, so the sidebar reads as two different kinds of
    # thing and neither is mistaken for a per-plugin step.
    S("Global.TButton", background=CLR["global_btn"], foreground=CLR["blue"],
                        borderwidth=0, relief="flat", padding=(8, 6),
                        font="Segoe\\ UI 9")
    style.map("Global.TButton",
              background=[("active", CLR["global_hover"]),
                          ("disabled", CLR["panel"])],
              foreground=[("disabled", CLR["subtext"])])

    # The same button once its work is already done and still current: sunk
    # into the panel and muted, so the eye skips it.  It stays CLICKABLE —
    # re-running is always allowed — it just stops advertising itself as
    # outstanding work.
    S("GlobalDone.TButton", background=CLR["btn"], foreground=CLR["subtext"],
                        borderwidth=0, relief="flat", padding=(8, 6),
                        font="Segoe\\ UI 9")
    style.map("GlobalDone.TButton",
              background=[("active", CLR["btn_hover"])],
              foreground=[("active", CLR["text"])])

    S("TSeparator",    background=CLR["border"])
    S("TScrollbar",    background=CLR["btn"], troughcolor=CLR["bg"],
                       borderwidth=0, arrowcolor=CLR["subtext"], relief="flat")
    style.map("TScrollbar", background=[("active", CLR["btn_hover"])])

    S("TCheckbutton",  background=CLR["panel"], foreground=CLR["text"],
                       indicatorcolor=CLR["check_off"],
                       indicatorrelief="flat", focuscolor="")
    style.map("TCheckbutton",
              indicatorcolor=[("selected", CLR["check_on"])],
              background=[("active", CLR["panel"])])

    S("TProgressbar",  troughcolor=CLR["panel"], background=CLR["accent"],
                       borderwidth=0, thickness=4)

    # ── State vars ────────────────────────────────────────────────────────────
    tes4_var    = tk.StringVar(value=tes4_path)
    tes5_var    = tk.StringVar(value=tes5_path)
    output_var  = tk.StringVar(value=output_path)
    file_var    = tk.StringVar()
    workers_var = tk.IntVar(value=workers_default)
    # Navmesh cache download: ON unless the user turned it off. Persisted, so a
    # metered connection stays opted out across sessions rather than having to
    # be re-set every launch.
    cache_dl_var = tk.BooleanVar(value=cache_download_default)
    # Whether the packing steps start ticked. Read once here for the initial
    # checkbox state, then live in the var so "Default" follows the setting.
    pack_default_var = tk.BooleanVar(value=pack_default)
    _initial_on = default_on_steps(pack_default_var.get())
    step_vars   = {key: tk.BooleanVar(value=(key in _initial_on))
                   for key, *_ in STEPS}
    running     = threading.Event()
    cancel_evt  = threading.Event()  # set to request cancellation

    # mesh subfolder state: list of (name, BooleanVar)
    mesh_subdir_vars = []  # populated when "Meshes" step panel expands
    # Skyrim patch-plugin state: list of (name, BooleanVar), all-on by default
    patch_plugin_vars = []

    # Inferred winding steps — Settings ▸ Infer collision winding. "Auto" (the
    # default) follows the measured per-plugin defaults: on for the plugins in
    # WINDING_FIX_DEFAULT_PLUGINS, off for everything else. The other two modes
    # pin it for every plugin. Persisted, so a user who has decided either way
    # does not have to re-set it every launch. The authored-normal repair runs
    # regardless of this setting.
    winding_mode_var = tk.StringVar(value=winding_mode_default)

    # Parallax carry-over.  Always starts OFF and never tracks the plugin: the
    # question it answers is about the PLAYER's Skyrim setup (Community Shaders
    # or ENB present), which nothing here can detect.
    parallax_var = tk.BooleanVar(value=False)
    tex_only_var = tk.BooleanVar(value=False)

    def _winding_on() -> bool:
        """Whether the INFERRED steps run for the plugin currently selected."""
        return winding_enabled_for(winding_mode_var.get(), file_var.get())

    def _get_workers() -> int:
        """Current worker-count value, clamped to [1, cpu_max]."""
        try:
            n = int(workers_var.get())
        except (tk.TclError, ValueError):
            n = workers_default
        n = max(1, min(n, cpu_max))
        if n != workers_var.get():
            workers_var.set(n)
        return n

    def _on_workers_change(*_):
        _save_dir_to_config()

    # ── Top menu bar (dark, custom-drawn) ─────────────────────────────────────
    # Windows renders a native (white) bar for root.configure(menu=...) and
    # ignores tk colors on it, so the bar is built from dark Menubuttons whose
    # dropdown popups (which DO honour color options) are tk.Menu instances.
    _menu_opts = dict(
        tearoff=0,
        bg=CLR["panel"], fg=CLR["text"],
        activebackground=CLR["accent"], activeforeground="#ffffff",
        selectcolor=CLR["accent"], relief="flat",
        borderwidth=0, activeborderwidth=0,
        font=("Segoe UI", 9),
    )
    menubar = tk.Frame(root, bg=CLR["panel"])
    menubar.pack(side=tk.TOP, fill=tk.X)
    ttk.Separator(root, orient=tk.HORIZONTAL).pack(side=tk.TOP, fill=tk.X)

    def _menubutton(text: str) -> tk.Menu:
        """Add a dark top-level menu button; return its dropdown Menu."""
        mb = tk.Menubutton(menubar, text=text,
                           bg=CLR["panel"], fg=CLR["text"],
                           activebackground=CLR["btn_hover"],
                           activeforeground=CLR["text"],
                           disabledforeground=CLR["subtext"],
                           relief="flat", borderwidth=0, padx=10, pady=4,
                           font=("Segoe UI", 9))
        mb.pack(side=tk.LEFT)
        menu = tk.Menu(mb, **_menu_opts)
        mb.configure(menu=menu)
        return menu

    # Settings ▸ Workers ▸ (1..cpu_max) — a radio group bound to workers_var.
    settings_menu = _menubutton("Settings")
    workers_menu  = tk.Menu(settings_menu, **_menu_opts)
    for n in range(1, cpu_max + 1):
        label = f"{n}  (default)" if n == worker_count() else str(n)
        workers_menu.add_radiobutton(
            label=label, value=n, variable=workers_var,
            command=_on_workers_change)
    settings_menu.add_cascade(label=f"Workers  (max {cpu_max})",
                              menu=workers_menu)

    # Settings ▸ Download navmesh cache — a checkbutton, saved immediately.
    # Navmesh generation is the slowest import stage and the prebuilt cache
    # turns minutes into seconds, so this is ON by default; it exists for
    # metered/offline connections and for anyone who would rather generate
    # locally. Turning it off does NOT disable the cache itself: a zip dropped
    # in navmesh_cache/ is still installed, and an existing cache is still used.
    def _on_cache_dl_change():
        updated = load_config()
        updated["navmeshCacheDownload"] = bool(cache_dl_var.get())
        save_config(updated)

    settings_menu.add_checkbutton(
        label="Download navmesh cache", variable=cache_dl_var,
        onvalue=True, offvalue=False, command=_on_cache_dl_change)

    # Settings ▸ Pack BSAs / Mod Zip by default — persisted, and applied to the
    # live checkboxes as soon as it is toggled so the setting's effect is
    # visible immediately rather than only after the next launch. It only moves
    # the two packing boxes; every other step keeps whatever the user has set.
    def _on_pack_default_change():
        updated = load_config()
        on = bool(pack_default_var.get())
        updated[PACK_DEFAULT_CONFIG_KEY] = on
        save_config(updated)
        for key in PACKING_STEPS:
            step_vars[key].set(on)
        _update_run_btn()

    settings_menu.add_checkbutton(
        label="Pack BSAs / Mod Zip by default", variable=pack_default_var,
        onvalue=True, offvalue=False, command=_on_pack_default_change)

    # Settings ▸ Infer collision winding ▸ Automatic / Always on / Always off.
    # This controls ONLY the inferred steps. The authored-normal repair (which
    # reads the winding each triangle records for itself) always runs and has
    # no switch -- it is what fixes "I fall straight through the floor" on
    # vanilla Oblivion and on Nehrim. The inferred steps GUESS from adjacency,
    # enclosed volume and the render mesh, so they can invert geometry that was
    # already correct, and are only worth it where the exporter destroyed the
    # normals. "Automatic" turns them on for exactly those plugins (measured;
    # see collision_options). A radio group rather than a checkbox because
    # "follow the per-plugin default" is a third answer, not the absence of one.
    def _on_winding_mode_change():
        updated = load_config()
        updated[WINDING_CONFIG_KEY] = winding_mode_var.get()
        save_config(updated)

    winding_menu = tk.Menu(settings_menu, **_menu_opts)
    _auto_plugins = ", ".join(sorted(WINDING_FIX_DEFAULT_PLUGINS))
    for _mode, _label in (
            (WINDING_AUTO, f"Automatic  (on for {_auto_plugins})"),
            (WINDING_ON,   "Always on"),
            (WINDING_OFF,  "Always off")):
        winding_menu.add_radiobutton(
            label=_label, value=_mode, variable=winding_mode_var,
            command=_on_winding_mode_change)
    settings_menu.add_cascade(label="Infer collision winding", menu=winding_menu)

    # ── Converted ▸ (plugins already in output/) ──────────────────────────────
    # Picking one selects it AND ticks the steps its last conversion still owes,
    # so "re-run what this plugin needs" is two clicks from a cold start.
    # Rebuilt on every open: output/ gains entries as conversions finish, and a
    # menu built once at startup would go stale within the session.
    converted_menu = _menubutton("Converted")

    def _select_converted(name: str):
        """Point the GUI at an already-converted plugin and plan its re-run.

        A plugin imported from a mod archive has no data directory at all: it
        lives in export/<plugin>/ and is reached by switching the source scope
        instead.  Checked FIRST, because such a plugin will never be found in
        the Oblivion folder and would otherwise be rejected as "not a plugin in
        the Oblivion data directory" -- leaving it listed here but impossible
        to actually re-run.

        Plugins do NOT share a data directory -- Nehrim and Morrowind_ob each
        live in their own install -- so selecting one has to restore the
        directory it was actually converted from.  Without this, picking Nehrim
        while the box points at Oblivion's Data folder fails the "is this a
        real plugin" check in _run_clicked, even though Nehrim converts fine
        from its own directory.
        """
        # One lookup for every source kind: an imported mod, or whichever
        # registered folder actually holds this plugin.
        if not _select_source_for_plugin(name):
            _info("Source Not Found",
                  f"{name!r} is in the output folder, but no source has it.\n\n"
                  "Add the folder it came from with the + button beside "
                  "Source, then pick it again.")
            return

        # Picking from Converted > is an explicit request to re-plan that
        # plugin, even when it is the one already selected (where _commit
        # deliberately leaves the user's edits alone) and even if its plan was
        # already auto-applied once this session. Clear the guard BEFORE
        # committing so the refresh _commit starts already re-applies it.
        _plan_applied.discard(name)
        _set_default()
        _commit(name)
        file_combo.selection_clear()

    def _rebuild_converted_menu():
        converted_menu.delete(0, tk.END)
        names = scan_converted(output_var.get().strip())
        if not names:
            converted_menu.add_command(label="(nothing converted yet)",
                                       state="disabled")
            return
        for name in names:
            converted_menu.add_command(
                label=name, command=lambda n=name: _select_converted(n))

    # <Map> is the menu's own "about to be shown" signal, so the list is always
    # current without polling the filesystem on a timer.
    converted_menu.bind("<Map>", lambda _e: _rebuild_converted_menu())
    _rebuild_converted_menu()

    # ── Mods ▸ import a mod archive as a conversion source ────────────────────
    # The discoverable half of the feature: dragging an archive onto the sidebar
    # does the same thing, but drag-and-drop needs an optional package and is
    # invisible until you try it, so this menu is the path that always works.
    mods_menu = _menubutton("Mods")

    def _import_mod_archive():
        path = filedialog.askopenfilename(
            title="Select a mod archive",
            filetypes=[("Mod archives", "*.zip *.7z *.rar"),
                       ("All files", "*.*")])
        if path:
            _begin_import(path)

    def _import_mod_folder():
        path = filedialog.askdirectory(title="Select an extracted mod folder")
        if path:
            _begin_import(path)

    mods_menu.add_command(label="Import Mod Archive…",
                          command=_import_mod_archive)
    mods_menu.add_command(label="Import Mod Folder…",
                          command=_import_mod_folder)
    mods_menu.add_separator()
    mods_menu.add_command(label="Manage Imported Mods…",
                          command=lambda: _manage_mods())

    # ── Tools ─────────────────────────────────────────────────────────────────
    tools_menu = _menubutton("Tools")

    def _check_dependencies():
        """Report every phase's unmet dependencies, without running anything.

        Called IN-PROCESS rather than shelling out to `python preflight.py`:
        under gui.pyw a spawn can allocate a console window, and the GUI is one
        window.  preflight's probes are plain imports and file checks, so this
        is fast enough to run inline.
        """
        try:
            import preflight
        except Exception as exc:
            _info("Check Dependencies",
                  f"Could not load the dependency checker:\n\n{exc}")
            return

        ok, bad = [], []
        for phase, label in preflight.PHASE_LABELS.items():
            try:
                missing = preflight.check_phase(phase)
            except Exception as exc:
                bad.append((label, [f"check failed: {exc}"]))
                continue
            if missing:
                bad.append((label, [f"{m.name} — {m.purpose}" for m in missing]))
            else:
                ok.append(label)

        lines = []
        warn = preflight.python_version_warning()
        if warn:
            # A version mismatch is a warning, not a gate (the pipeline runs on
            # other 3.x once the navmesh extension is rebuilt), so it leads but
            # does not turn the report into a failure.
            lines.append(warn.strip())
            lines.append("")

        if not bad:
            lines.append("All phases have what they need.")
            lines.append("")
            lines.append(f"Checked: {', '.join(ok)}")
            _info("Check Dependencies", "\n".join(lines))
            return

        # Deduplicate: one missing tool usually blocks several phases, and
        # listing it once per phase buries how few things are actually absent.
        seen, items = set(), []
        for _label, missing in bad:
            for m in missing:
                if m not in seen:
                    seen.add(m)
                    items.append(m)

        lines.append(f"{len(bad)} phase(s) are missing dependencies:")
        lines.append("")
        for label, missing in bad:
            lines.append(f"  {label}: {', '.join(m.split(' — ')[0] for m in missing)}")
        lines.append("")
        lines.append("Missing:")
        for m in items:
            lines.append(f"  • {m}")
        lines.append("")
        lines.append("See the Requirements section of README.md to install these.")
        if ok:
            lines.append("")
            lines.append(f"Ready: {', '.join(ok)}")
        _info("Check Dependencies", "\n".join(lines))

    def _open_folder(path: str, what: str):
        """Reveal `path` in the system file manager."""
        if not path or not os.path.isdir(path):
            _info(f"Open {what}",
                  f"{what} does not exist yet:\n\n{path or '(not set)'}\n\n"
                  "Run a conversion first.")
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)          # no subprocess, no console window
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path], **_POPEN_FLAGS)
            else:
                subprocess.Popen(["xdg-open", path], **_POPEN_FLAGS)
        except OSError as exc:
            _info(f"Open {what}", f"Could not open:\n\n{path}\n\n{exc}")

    tools_menu.add_command(label="Check Dependencies",
                           command=_check_dependencies)

    # Tools ▸ Set Nemesis Folder — where the "Nemesis baseline" step reads from.
    # It cannot be auto-detected on a Mod Organizer setup: the mods live outside
    # the game's Data folder entirely, so only the user knows the path. The
    # picker takes the MOD folder (the thing with a recognisable name) and the
    # `meshes` subfolder is resolved in code, because "pick the meshes folder
    # inside the mod" is knowledge nobody should need to have.
    def _set_nemesis_folder():
        from asset_convert.nemesis import baseline_dir, BASELINE_FILES
        current = (load_config().get(NEMESIS_DIR_CONFIG_KEY) or "").strip()
        path = filedialog.askdirectory(
            title="Select the Nemesis Unlimited Behavior Engine mod folder",
            initialdir=current or None)
        if not path:
            return
        resolved = baseline_dir(path)
        if not resolved:
            _info("Set Nemesis Folder",
                  f"That folder does not hold Nemesis's baseline files.\n\n"
                  f"Looked for {BASELINE_FILES[0]} in:\n"
                  f"  {path}\n  {os.path.join(path, 'meshes')}\n\n"
                  f"Pick the Nemesis Unlimited Behavior Engine mod folder "
                  f"(the one containing Nemesis_Engine and meshes).")
            return
        updated = load_config()
        updated[NEMESIS_DIR_CONFIG_KEY] = path
        save_config(updated)
        _info("Set Nemesis Folder",
              f"Nemesis folder set to:\n{path}\n\nBaseline files found in:\n"
              f"{resolved}\n\nThe \"Nemesis baseline\" step under Creatures "
              f"will read from there. Load this mod AFTER Nemesis Unlimited "
              f"Behavior Engine and BEFORE Nemesis Output.")

    tools_menu.add_command(label="Set Nemesis Folder…",
                           command=_set_nemesis_folder)
    tools_menu.add_separator()
    # The same two global actions as the sidebar buttons. Menu entries are
    # late-bound (`_run_global_action` is defined further down, with the rest of
    # the run logic) so the lambda resolves it at click time, not build time.
    for _gkey, _glabel, _gtip, _gshort, _grow in GLOBAL_ACTIONS:
        tools_menu.add_command(
            label=_glabel,
            command=(lambda k=_gkey: _run_global_action(k)))
    tools_menu.add_separator()
    tools_menu.add_command(
        label="Open Output Folder",
        command=lambda: _open_folder(output_var.get().strip(), "Output folder"))
    # Rotation is worthless if nobody knows the files are there.  The run also
    # prints its own log path into the scrollback (see `_run_log_note`).
    tools_menu.add_command(
        label="Open Logs Folder",
        command=lambda: _open_folder(str(SCRIPT_DIR / "logs"), "Logs folder"))

    # ── About ─────────────────────────────────────────────────────────────────
    # Top-level rather than under Help: it is currently the only such item, and
    # a one-entry menu is a worse click than a direct button.  Move it back
    # under Help once there are more help items to group with it.
    DISCORD_URL = "https://discord.gg/NTkCDfYUru"
    YOUTUBE_URL = "https://www.youtube.com/@bryanthinton"

    def _about():
        version = version_info.current_version()
        note = ("  (development build)"
                if version_info.is_dev_version(version) else "")
        _info("About TES4 Auto-Convert",
              f"TES4 Auto-Convert {version}{note}\n\n"
              "Converts TES4 (Oblivion) master and plugin files into TES5 "
              "(Skyrim SE) format — records, meshes, textures, collision, "
              "animations, sounds, dialogue and scripts.\n\n"
              "Released under the MIT License. Bethesda game assets are not "
              "redistributed; this tool converts the copies you already own.",
              links=(("GitHub — source, releases and issues", version_info.REPO_URL),
                     ("YouTube — @bryanthinton", YOUTUBE_URL),
                     ("Discord — community and support", DISCORD_URL)))

    about_mb = tk.Menubutton(menubar, text="About",
                             bg=CLR["panel"], fg=CLR["text"],
                             activebackground=CLR["btn_hover"],
                             activeforeground=CLR["text"],
                             disabledforeground=CLR["subtext"],
                             relief="flat", borderwidth=0, padx=10, pady=4,
                             font=("Segoe UI", 9))
    about_mb.pack(side=tk.LEFT)
    about_mb.bind("<Button-1>", lambda _e: _about())

    # ── Check for Updates ─────────────────────────────────────────────────────
    # Never automatic: the check is a network call, and a GUI that phones home
    # on launch would both stall startup and do it without being asked.
    update_mb = tk.Menubutton(menubar, text="Check for Updates",
                              bg=CLR["panel"], fg=CLR["text"],
                              activebackground=CLR["btn_hover"],
                              activeforeground=CLR["text"],
                              disabledforeground=CLR["subtext"],
                              relief="flat", borderwidth=0, padx=10, pady=4,
                              font=("Segoe UI", 9))
    update_mb.pack(side=tk.LEFT)

    def _show_update_result(result: dict):
        """Report a finished check.  UI thread only (via root.after)."""
        update_mb.configure(text="Check for Updates", state="normal")
        if not result["reachable"]:
            _info("Update Check Failed",
                  "Could not reach GitHub to check for updates.\n\n"
                  "Check your connection, or see:\n"
                  f"{version_info.RELEASES_URL}")
            return
        if not result["available"]:
            _info("Up to Date",
                  f"You are running {result['current']}, which is the newest "
                  f"release.")
            return
        if _confirm("Update Available",
                    f"{result['latest']} is available (you have "
                    f"{result['current']}).\n\n"
                    "Download it and paste it over this folder; the Upgrade "
                    "button will then select only the steps that changed.\n\n"
                    "Open the downloads page now?",
                    yes="Open Page", no="Not Now"):
            _open_url(version_info.RELEASES_URL)

    def _check_for_updates():
        if str(update_mb.cget("state")) == "disabled":
            return                      # a check is already in flight
        update_mb.configure(text="Checking...", state="disabled")

        def _worker():
            try:
                result = version_info.check_for_update()
            except Exception:
                result = {"current": version_info.current_version(),
                          "latest": None, "available": False, "reachable": False}
            # Hop back to the UI thread; tkinter is not thread-safe.
            root.after(0, lambda: _show_update_result(result))

        threading.Thread(target=_worker, daemon=True).start()

    # A Menubutton with no menu behaves as a plain click target, matching the
    # look of Settings/Converted without pretending to be a dropdown.
    update_mb.bind("<Button-1>", lambda _e: _check_for_updates())

    # ── Layout: sidebar + log pane ────────────────────────────────────────────
    outer = ttk.Frame(root)
    outer.pack(fill=tk.BOTH, expand=True)

    def _open_url(url: str):
        """Open `url` in the default browser, hidden.

        webbrowser can shell out on some platforms, so it runs on a worker
        thread: a slow browser launch must not freeze the window, and nothing
        on the UI thread may block.
        """
        def _go():
            try:
                import webbrowser
                webbrowser.open(url)
            except Exception:
                pass
        threading.Thread(target=_go, daemon=True).start()

    def _dialog(title: str, message: str, buttons=("OK",),
                default: int = 0, links=()) -> str:
        """Modal message card in the app's palette.  Returns the button clicked.

        tkinter's `messagebox` renders NATIVE OS dialogs, which ignore every
        color here and flash white in a dark UI.  This is the same card the
        mesh-subfolder panel uses, so dialogs match the rest of the window.

        `links` is [(label, url)] rendered as clickable rows under the message.

        Modal via grab_set + wait_window, so it returns the user's answer
        inline exactly like messagebox did.
        """
        result = [buttons[-1] if len(buttons) > 1 else buttons[0]]

        # Just the card, placed over the window -- NOT a full-size backdrop
        # frame.  A backdrop covers the log and the whole sidebar, which reads
        # as the app disappearing rather than a popup appearing.  The card
        # still takes the grab, so it is modal without hiding anything.
        card = tk.Frame(outer, bg=CLR["panel"],
                        highlightbackground=CLR["border"], highlightthickness=1)

        def _close(choice: str):
            result[0] = choice
            card.grab_release()
            card.destroy()

        tk.Label(card, text=title, bg=CLR["panel"], fg=CLR["text"],
                 font=("Segoe UI", 10, "bold")).pack(anchor="w",
                                                     padx=16, pady=(14, 0))
        ttk.Separator(card, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16, pady=8)
        tk.Label(card, text=message, bg=CLR["panel"], fg=CLR["subtext"],
                 font=("Segoe UI", 9), justify=tk.LEFT, anchor="w",
                 wraplength=380).pack(anchor="w", padx=16,
                                      pady=(0, 6 if links else 12))

        for text, url in links:
            lnk = tk.Label(card, text=text, bg=CLR["panel"], fg=CLR["accent_hover"],
                           font=("Segoe UI", 9, "underline"), cursor="hand2",
                           anchor="w")
            lnk.pack(anchor="w", padx=16, pady=1)
            lnk.bind("<Button-1>", lambda _e, u=url: _open_url(u))
        if links:
            # Breathing room before the button row, matching the no-link case.
            tk.Frame(card, bg=CLR["panel"], height=6).pack(fill=tk.X)

        btn_row = tk.Frame(card, bg=CLR["panel"])
        btn_row.pack(fill=tk.X, padx=16, pady=(0, 14))
        for i, label in enumerate(reversed(buttons)):
            is_default = (len(buttons) - 1 - i) == default
            ttk.Button(btn_row, text=label, width=10,
                       style="Accent.TButton" if is_default else "TButton",
                       command=lambda l=label: _close(l)).pack(
                           side=tk.RIGHT, padx=(6, 0))

        card.update_idletasks()
        card.place(in_=outer, anchor="center", relx=0.5, rely=0.5)
        card.lift()
        # Escape answers as the non-default button, matching a native dialog's
        # Cancel semantics.
        card.bind("<Escape>", lambda _e: _close(buttons[-1]))
        card.focus_set()
        card.grab_set()
        root.wait_window(card)
        return result[0]

    def _info(title: str, message: str, links=()) -> None:
        _dialog(title, message, links=links)

    def _confirm(title: str, message: str,
                 yes: str = "Yes", no: str = "No") -> bool:
        return _dialog(title, message, buttons=(yes, no)) == yes
    outer.columnconfigure(0, weight=0, minsize=330)
    outer.columnconfigure(1, weight=1)
    outer.rowconfigure(0, weight=1)

    sidebar  = ttk.Frame(outer, style="Panel.TFrame")
    sidebar.grid(row=0, column=0, sticky="nsew")

    log_pane = tk.Frame(outer, bg=CLR["log_bg"])
    log_pane.grid(row=0, column=1, sticky="nsew")

    # ── Sidebar helpers ───────────────────────────────────────────────────────
    # One rule, one gap. Every block that meets a separator packs flush against
    # it (pady 0 on that side) and lets this own the whole space, so the gap
    # above a rule always equals the gap below it. When both sides contributed
    # their own padding the two halves silently differed — 12px over vs 16px
    # under the same line.
    _SEP_GAP = 12

    def _sep():
        ttk.Separator(sidebar, orient=tk.HORIZONTAL).pack(
            fill=tk.X, padx=14, pady=_SEP_GAP)

    def _section(text: str):
        f = ttk.Frame(sidebar, style="Panel.TFrame")
        f.pack(fill=tk.X, padx=14, pady=(6, 2))
        ttk.Label(f, text=text, style="PanelSub.TLabel").pack(anchor="w")
        return f

    def _attach_tooltip(widget, text: str, width: int = 340):
        """Show `text` in a small dark popup while the cursor rests on `widget`.

        A borderless Toplevel rather than a Frame: the sidebar is narrow, so the
        popup has to be free to overhang the window edge.  It is created on
        enter and destroyed on leave, so nothing lingers if the widget itself is
        destroyed while the tip is up.

        Returns a setter for the text.  Widgets whose tip changes (the Upgrade
        button) MUST use it rather than calling _attach_tooltip again: the
        bindings below are `add="+"`, so re-attaching stacks another set of
        handlers on every refresh and leaks a popup per call.
        """
        state = {"win": None, "after": None, "text": text}

        def _hide(*_):
            if state["after"] is not None:
                widget.after_cancel(state["after"])
                state["after"] = None
            if state["win"] is not None:
                state["win"].destroy()
                state["win"] = None

        def _show():
            state["after"] = None
            if state["win"] is not None:
                return
            win = tk.Toplevel(widget)
            win.wm_overrideredirect(True)   # no title bar / decorations
            win.configure(bg=CLR["border"])
            tk.Label(
                win, text=state["text"], justify="left", wraplength=width,
                bg=CLR["log_bg"], fg=CLR["text"],
                font=("Segoe UI", 9), padx=8, pady=6,
            ).pack(padx=1, pady=1)       # 1px border via the parent's bg
            # Below-right of the cursor, clamped so it stays on screen.
            x = widget.winfo_pointerx() + 14
            y = widget.winfo_pointery() + 18
            win.update_idletasks()
            sw = win.winfo_screenwidth()
            if x + win.winfo_width() > sw:
                x = max(0, sw - win.winfo_width() - 4)
            win.wm_geometry(f"+{x}+{y}")
            state["win"] = win

        def _enter(_=None):
            _hide()
            state["after"] = widget.after(450, _show)  # brief hover delay

        widget.bind("<Enter>", _enter, add="+")
        widget.bind("<Leave>", _hide, add="+")
        widget.bind("<Button-1>", _hide, add="+")
        widget.bind("<Destroy>", _hide, add="+")

        def _set_text(new_text: str):
            state["text"] = new_text
            if state["win"] is not None:   # retarget a tip that is already up
                _hide()

        return _set_text

    def _path_row(parent, label_text: str, var: tk.StringVar,
                  browse_dir=True, on_change=None):
        """A labelled Entry + Browse button row."""
        # No pady on the label: every field block owns its own spacing via its
        # frame's pady, so a second source of vertical padding here made the
        # four blocks (three path rows + the plugin selector) sit at four
        # different intervals.
        ttk.Label(parent, text=label_text, style="PanelSub.TLabel").pack(
            anchor="w")
        row = ttk.Frame(parent, style="Panel.TFrame")
        row.pack(fill=tk.X)
        row.columnconfigure(0, weight=1)
        entry = ttk.Entry(row, textvariable=var, width=26)
        entry.grid(row=0, column=0, sticky="ew", padx=(0, 4))

        def _browse():
            if browse_dir:
                path = filedialog.askdirectory(
                    initialdir=var.get() or str(SCRIPT_DIR),
                    title=f"Select {label_text}")
            else:
                path = filedialog.askopenfilename(
                    initialdir=var.get() or str(SCRIPT_DIR),
                    title=f"Select {label_text}")
            if path:
                var.set(path)
                if on_change:
                    on_change(path)

        ttk.Button(row, text="...", command=_browse, width=3).grid(
            row=0, column=1)
        return entry

    # ── Title ─────────────────────────────────────────────────────────────────
    tf = ttk.Frame(sidebar, style="Panel.TFrame")
    tf.pack(fill=tk.X, padx=14, pady=(16, 0))

    banner_img = None
    banner_path = SCRIPT_DIR / "docs" / "banner.png"
    if banner_path.exists():
        try:
            from PIL import Image, ImageTk
            src = Image.open(banner_path)
            target_w = 350
            # Scale to 1.5x the width-fit height, then crop left/right back
            # down to target_w so the logo reads larger within the same column.
            base_h = target_w * src.height / src.width
            scale_h = round(base_h * 1.5)
            scale_w = round(target_w * (scale_h / base_h))
            src = src.resize((scale_w, scale_h), Image.LANCZOS)
            left = (scale_w - target_w) // 2
            src = src.crop((left, 0, left + target_w, scale_h))
            banner_img = ImageTk.PhotoImage(src)
        except Exception:
            banner_img = None

    if banner_img is not None:
        banner_label = ttk.Label(tf, image=banner_img, style="Panel.TLabel")
        banner_label.image = banner_img  # keep a reference alive
        banner_label.pack(fill=tk.X)
    else:
        ttk.Label(tf, text="TES4 Auto-Convert", style="Head.TLabel").pack(anchor="w")
        ttk.Label(tf, text="Oblivion to Skyrim SE converter",
                  style="PanelSub.TLabel").pack(anchor="w")

    _sep()

    # ── Oblivion data directory ───────────────────────────────────────────────

    # ── Source ────────────────────────────────────────────────────────────────
    # ONE list of every place plugins come from: each game folder (Oblivion,
    # Nehrim, a second install) and each imported mod archive. They are the
    # same concept -- somewhere plugins live -- so they are one control.
    #
    # This replaces the single "Oblivion Data Directory" path box, which forced
    # anyone with two installs to keep retyping one field, and made whichever
    # path was in the box at conversion time get recorded as that plugin's
    # origin (Oblivion.esm ended up stamped against the Nehrim folder).
    sf = ttk.Frame(sidebar, style="Panel.TFrame")
    sf.pack(fill=tk.X, padx=14, pady=(0, 8))
    src_head = ttk.Frame(sf, style="Panel.TFrame")
    src_head.pack(fill=tk.X)
    ttk.Label(src_head, text="Source", style="PanelSub.TLabel").pack(
        side=tk.LEFT, anchor="w")
    src_hint = ttk.Label(src_head, text="", style="PanelSub.TLabel")
    src_hint.pack(side=tk.RIGHT, anchor="e")

    src_row = ttk.Frame(sf, style="Panel.TFrame")
    src_row.pack(fill=tk.X, pady=(2, 0))
    src_row.columnconfigure(0, weight=1)
    scope_var = tk.StringVar(value="")
    scope_combo = ttk.Combobox(src_row, state="readonly", width=26)
    scope_combo.grid(row=0, column=0, sticky="ew", padx=(0, 4))
    # Parallel to scope_combo["values"]: display label -> source id.
    scope_ids: list = []
    # Every source dict from the registry, keyed by id, so the plugin list and
    # the per-plugin directory lookup share one definition.
    scope_rows: dict = {}

    def _add_source_dir():
        """Register another game Data folder as a source."""
        path = filedialog.askdirectory(
            initialdir=tes4_var.get() or str(SCRIPT_DIR),
            title="Select a game Data folder")
        if not path:
            return
        found = scan_plugins(path)
        if not found:
            if not _confirm(
                    "Add Source",
                    f"No .esm/.esp plugins found in:\n\n{path}\n\n"
                    "Add it anyway?", yes="Add", no="Cancel"):
                return
        from asset_convert import source_registry
        source_registry.add_directory(EXPORT_DIR, path)
        _refresh_scopes(select=f"dir:{os.path.normcase(os.path.normpath(path))}")
        _apply_scope()

    def _remove_source():
        """Unregister the selected source. Folders on disk are never deleted."""
        row = scope_rows.get(scope_var.get())
        if not row:
            return
        if row["kind"] == "mod":
            _manage_mods()
            return
        if len(_dir_sources()) <= 1:
            _info("Remove Source",
                  "This is the only game folder left. Add another before "
                  "removing it.")
            return
        if not _confirm("Remove Source",
                        f"Stop listing this folder?\n\n{row['path']}\n\n"
                        "Nothing on disk is deleted.",
                        yes="Remove", no="Cancel"):
            return
        from asset_convert import source_registry
        source_registry.remove_directory(EXPORT_DIR, row["path"])
        _refresh_scopes()
        _apply_scope()

    ttk.Button(src_row, text="+", width=2, command=_add_source_dir).grid(
        row=0, column=1)
    ttk.Button(src_row, text="−", width=2, command=_remove_source).grid(
        row=0, column=2, padx=(3, 0))

    # ── Plugin selector ───────────────────────────────────────────────────────
    pf = ttk.Frame(sidebar, style="Panel.TFrame")
    pf.pack(fill=tk.X, padx=14, pady=(0, 8))
    ttk.Label(pf, text="Plugin File", style="PanelSub.TLabel").pack(anchor="w")
    initial_plugins = scan_plugins(tes4_path)
    # Master list of every plugin in the CURRENT scope.  file_combo["values"]
    # holds only what the current search text matches, so the unfiltered set
    # has to live somewhere the filter can always restore from.
    all_plugins = list(initial_plugins)
    file_combo = ttk.Combobox(pf, textvariable=file_var,
                               values=initial_plugins, width=30)
    file_combo.pack(fill=tk.X, pady=(2, 0))

    # The last name the user actually committed to.  Typing a search scribbles
    # over the entry text, so focus leaving mid-search has to fall back to this
    # rather than stranding a half-typed fragment in the field.
    last_valid = [file_var.get()]
    # True while the entry text is a search fragment rather than a committed
    # plugin name.  Set the moment the user starts typing, cleared on commit.
    searching = [False]

    def _all_sources():
        """Every source: registered folders + the configured one + mods."""
        try:
            from asset_convert import source_registry
            return source_registry.all_sources(
                EXPORT_DIR, extra_dirs=[tes4_var.get()])
        except Exception:
            path = tes4_var.get()
            return ([{'id': 'dir:' + os.path.normcase(os.path.normpath(path)),
                      'kind': 'directory', 'label': os.path.basename(path),
                      'path': path}] if path else [])

    def _dir_sources():
        return [r for r in _all_sources() if r["kind"] == "directory"]

    def _refresh_scopes(select=None):
        """Rebuild the Source dropdown; optionally switch to `select`."""
        rows = _all_sources()
        scope_rows.clear()
        labels, ids = [], []
        for row in rows:
            scope_rows[row["id"]] = row
            if row["kind"] == "directory":
                count = len(scan_plugins(row["path"]))
                detail = f"{count} plugin{'s' if count != 1 else ''}"
            elif row.get("asset_only"):
                # Counting "1 plugin" for a texture pack that has none is a
                # lie the user would act on; say what it actually is.
                detail = "assets only"
            else:
                count = len(row.get("plugins") or [])
                detail = f"{count} plugin{'s' if count != 1 else ''}"
            labels.append(f"{row['label']}  ({detail})")
            ids.append(row["id"])
        scope_ids[:] = ids
        scope_combo["values"] = labels

        want = select if select in scope_rows else scope_var.get()
        if want not in scope_rows:
            want = ids[0] if ids else ""
        scope_var.set(want)
        if want in ids:
            scope_combo.current(ids.index(want))
            row = scope_rows[want]
            # The full path is what distinguishes two folders both called
            # "Data"; the dropdown only has room for the install name.
            src_hint.configure(
                text=("mod archive" if row["kind"] == "mod"
                      else _shorten_path(row["path"])))

    def _shorten_path(path, limit=34):
        p = str(path)
        return p if len(p) <= limit else "..." + p[-(limit - 3):]

    def _apply_scope(select_plugin=None):
        """Repopulate the plugin list from the active source."""
        row = scope_rows.get(scope_var.get())
        if not row:
            plugins = []
        elif row["kind"] == "directory":
            plugins = scan_plugins(row["path"])
            # Keep tes4_var pointing at the ACTIVE folder: every downstream
            # consumer (the run command, config, version stamping) still reads
            # it, so switching source has to move it too.
            if tes4_var.get() != row["path"]:
                tes4_var.set(row["path"])
                _save_dir_to_config()
        else:
            plugins = list(row.get("plugins") or [])

        all_plugins[:] = plugins
        file_combo["values"] = plugins

        if select_plugin and select_plugin in plugins:
            file_var.set(select_plugin)
        elif file_var.get() not in plugins:
            preferred = next(
                (p for p in plugins if p.lower() == 'oblivion.esm'), None)
            file_var.set(preferred or (plugins[0] if plugins else ""))
        last_valid[0] = file_var.get()
        searching[0] = False

    def _select_source_for_plugin(name):
        """Switch to whichever source actually holds `name`. True if found.

        Used by Converted > so re-running a plugin restores its real origin
        instead of whatever folder happens to be selected.
        """
        try:
            from asset_convert import source_registry
            entry = source_registry.get(EXPORT_DIR, name)
        except Exception:
            entry = None
        if entry:
            _refresh_scopes(select=f"mod:{entry.get('group_id')}")
            _apply_scope(select_plugin=entry.get("plugin") or name)
            return True

        # A directory source: prefer the one recorded for this plugin, else
        # the first registered folder that actually contains it.
        recorded = version_info.source_path_for(name)
        candidates = ([recorded] if recorded else []) + \
                     [r["path"] for r in _dir_sources()]
        for path in candidates:
            if path and os.path.isfile(os.path.join(path, name)):
                _refresh_scopes(
                    select="dir:" + os.path.normcase(os.path.normpath(path)))
                _apply_scope(select_plugin=name)
                return True
        return False

    def _capabilities_for_selection():
        """What the selected plugin can actually do, or None for 'everything'.

        A plugin in a game folder is assumed fully capable -- its BSAs are not
        catalogued until the extract step runs, so guessing would be wrong.
        Only an imported mod has a measured content list.
        """
        try:
            from asset_convert import mod_ingest, source_registry
            from output_layout import asset_root
            name = file_var.get()
            entry = source_registry.get(EXPORT_DIR, name)
        except Exception:
            return None
        if not entry:
            return None
        caps = entry.get("capabilities")
        if isinstance(caps, dict):
            return caps
        # Imported before capabilities were recorded: measure the tree now
        # rather than falling back to "allow everything", which would offer
        # steps the mod has no content for.
        #
        # Measure the SHARED asset tree. Every plugin from one archive draws on
        # the same meshes/textures, so capabilities are a property of the MOD;
        # measuring `export/<plugin>/` looked at a folder that no longer exists
        # and reported a resource pack as having no meshes at all.
        try:
            return mod_ingest.capabilities_for(
                asset_root(EXPORT_DIR, entry.get("plugin") or name),
                has_plugin=bool(entry.get("plugin")))
        except Exception:
            return None

    def _apply_step_availability():
        """Grey out steps the selected source has no content for."""
        caps = _capabilities_for_selection()
        if caps is None:
            for key, (cb, lbl, tip) in step_widgets.items():
                cb.configure(state="normal")
                lbl.configure(text=tip)
            _update_run_btn()
            return

        try:
            from asset_convert import mod_ingest
            usable = mod_ingest.available_steps(caps)
        except Exception:
            return

        for key, (cb, lbl, tip) in step_widgets.items():
            if key in usable:
                cb.configure(state="normal")
                lbl.configure(text=tip)
            else:
                # Untick as well as disable: a step left ticked but greyed
                # would still be collected by _run_clicked.
                step_vars[key].set(False)
                cb.configure(state="disabled")
                lbl.configure(text=_why_unavailable(key, caps))
        _update_run_btn()

    def _why_unavailable(key, caps):
        """Why a step is greyed, in the user's terms rather than 'disabled'."""
        if key == "extract":
            return "already extracted on import"
        from asset_convert import mod_ingest
        needs = mod_ingest.STEP_REQUIREMENTS.get(key, ())
        if needs == ("plugin",):
            return "needs a plugin"
        missing = ", ".join(n for n in needs if not caps.get(n))
        return f"no {missing}" if missing else "nothing to convert"

    def _on_scope_selected(_evt=None):
        idx = scope_combo.current()
        if 0 <= idx < len(scope_ids):
            scope_var.set(scope_ids[idx])
        _apply_scope()
        _refresh_scopes(select=scope_var.get())
        _apply_step_availability()
        _refresh_upgrade_notice()

    scope_combo.bind("<<ComboboxSelected>>", _on_scope_selected)

    _CB = str(file_combo)
    _POPDOWN = f"ttk::combobox::PopdownWindow {_CB}"

    def _tcl(script: str) -> str:
        return file_combo.tk.eval(script)

    def _list_is_open() -> bool:
        try:
            return bool(int(_tcl(f"winfo ismapped [{_POPDOWN}]")))
        except tk.TclError:
            return False

    def _unhijack_listbox():
        """Let the entry keep the keyboard while the dropdown stays posted.

        Tk's stock listbox bindtag grabs focus on <Map> (`focus -force`) and, on
        win32 only, cancels the popup on <FocusOut>.  Together those make "popup
        open" and "entry typable" mutually exclusive.  Swapping in a private
        bindtag that keeps only the selection bindings drops both behaviours;
        the popdown's global grab is untouched, so clicking the list still
        works.  Idempotent, and the popdown must already exist.
        """
        listbox = f"{_tcl(_POPDOWN)}.f.l"
        tags = _tcl(f"bindtags {listbox}").split()
        if "ComboboxListbox" not in tags:
            return
        _tcl("""
            bind FilterComboListbox <ButtonRelease-1> {ttk::combobox::LBSelected %W}
            bind FilterComboListbox <Return>          {ttk::combobox::LBSelected %W}
            bind FilterComboListbox <Escape>          {ttk::combobox::LBCancel %W}
            bind FilterComboListbox <Motion>          {ttk::combobox::LBHover %W %x %y}
            bind FilterComboListbox <Destroy>         {ttk::combobox::LBCleanup %W}
        """)
        patched = ["FilterComboListbox" if t == "ComboboxListbox" else t
                   for t in tags]
        _tcl(f"bindtags {listbox} {{{' '.join(patched)}}}")

    def _search_text() -> str:
        """The text acting as the current search term.

        Only text the user has actually typed counts.  A committed plugin name
        sitting in the field is not a search, so the list opens unfiltered.
        """
        if not searching[0]:
            return ""
        try:
            return file_combo.get()
        except tk.TclError:
            return ""

    def _matches_for(typed: str) -> list:
        typed = typed.strip().lower()
        return [p for p in all_plugins if typed in p.lower()] if typed \
            else list(all_plugins)

    def _sync_values():
        """Point -values at the current search text's matches.

        Wired to -postcommand as well, because ttk::combobox::Post re-reads
        -values via ConfigureListbox *after* running the postcommand — setting
        the values any later in the cycle just gets overwritten.
        """
        file_combo["values"] = _matches_for(_search_text())

    file_combo.configure(postcommand=_sync_values)

    def _is_disabled() -> bool:
        # Our handlers bypass ttk's own `instate disabled` guard, so the
        # disabled state during a conversion run has to be honoured here.
        return "disabled" in file_combo.state()

    def _open_list():
        """Post the dropdown and keep the caret in the entry so typing filters."""
        if _is_disabled():
            return
        if not _list_is_open():
            _tcl(f"ttk::combobox::Post {_CB}")
        _unhijack_listbox()          # popdown now exists; strip the focus grab
        file_combo.focus_force()     # take the keyboard back from the listbox

    def _refresh_list():
        """Re-filter, and push the new values into an already-posted listbox.

        ConfigureListbox is what -postcommand's values normally flow through;
        calling it directly repopulates the open popup without re-posting (a
        re-post would bounce focus back to the listbox mid-word).
        """
        _sync_values()
        if _list_is_open():
            _tcl(f"ttk::combobox::ConfigureListbox {_CB}")
            _tcl(f"ttk::combobox::PlacePopdown {_CB} [{_POPDOWN}]")

    def _on_key(evt):
        # Navigation/selection keys drive the dropdown itself — filtering on
        # them would fight the listbox and reopen it after every pick.
        if evt.keysym in ("Up", "Down", "Return", "Escape", "Tab",
                          "Left", "Right", "Home", "End",
                          "Shift_L", "Shift_R", "Control_L", "Control_R",
                          "Alt_L", "Alt_R"):
            return
        # Any other keystroke means the field now holds a search fragment.
        # <KeyRelease> fires after the entry text is updated, so filtering here
        # sees what the user just typed.
        searching[0] = True
        _open_list()
        _refresh_list()

    def _on_click(_evt=None):
        """Click the field: open the full dropdown, ready for typing.

        The whole name is selected so the first keystroke replaces it rather
        than appending to the plugin already there.
        """
        if _is_disabled():
            return "break"
        searching[0] = False          # a committed name isn't a search term
        file_combo.selection_range(0, tk.END)
        file_combo.icursor(tk.END)
        _open_list()
        _refresh_list()
        return "break"  # suppress ttk's own Press handler (it would re-grab)

    def _on_down(_evt=None):
        _on_click()
        return "break"

    def _commit(name: str):
        searching[0] = False
        previous = last_valid[0]
        last_valid[0] = name
        file_var.set(name)
        file_combo["values"] = list(all_plugins)
        # Switching plugin starts that plugin's selection over. The ticks are
        # per-plugin state -- what THIS plugin still owes -- so carrying the
        # last one's boxes across meant edits made for plugin A silently
        # governed the run for plugin B.
        #
        # Reset to the defaults first so the window is never showing another
        # plugin's selection, then let the upgrade plan narrow it to what is
        # actually outstanding when the (threaded) lookup returns. Re-selecting
        # the plugin already shown is not a switch and must not discard the
        # user's edits.
        # Case-insensitive: the combo and a typed name differ in case for
        # the same file often enough that a raw compare would read as a
        # switch and wipe the selection.
        if (name or '').strip().lower() != (previous or '').strip().lower():
            _plan_applied.discard(name)
            _set_default()
        # Each plugin carries its own conversion history, so the upgrade notice
        # is per-plugin and has to follow the selection.
        _refresh_upgrade_notice()
        # ...as does which steps it can run: an asset-only mod has no plugin,
        # so Export/Import/Scripts would run on nothing.
        _apply_step_availability()

    def _on_selected(_evt=None):
        _commit(file_var.get())
        file_combo.selection_clear()

    def _on_return(_evt=None):
        """Enter commits the sole/first match, so a filtered search is keyboard-
        completable without reaching for the mouse."""
        matches = _matches_for(_search_text())
        typed = file_combo.get().strip().lower()
        exact = next((p for p in all_plugins if p.lower() == typed), None)
        if exact:
            _commit(exact)
        elif matches:
            _commit(matches[0])
        if _list_is_open():
            _tcl(f"ttk::combobox::Unpost {_CB}")
        return "break"

    def _on_escape(_evt=None):
        if _list_is_open():
            _tcl(f"ttk::combobox::Unpost {_CB}")
        if last_valid[0]:
            _commit(last_valid[0])
        return "break"

    def _on_focus_out(_evt=None):
        # Focus bouncing to our own dropdown isn't the user leaving the field.
        if _list_is_open():
            return
        # Commit an exact match, otherwise snap back to the last good name so
        # the field never keeps a search fragment that isn't a real plugin.
        typed = file_combo.get().strip()
        exact = next((p for p in all_plugins if p.lower() == typed.lower()), None)
        _commit(exact if exact else (last_valid[0] or ""))

    file_combo.bind("<KeyRelease>", _on_key)
    file_combo.bind("<Button-1>", _on_click)
    file_combo.bind("<Down>", _on_down)
    file_combo.bind("<Return>", _on_return)
    file_combo.bind("<Escape>", _on_escape)
    file_combo.bind("<<ComboboxSelected>>", _on_selected)
    file_combo.bind("<FocusOut>", _on_focus_out)

    if initial_plugins and not file_var.get():
        # Prefer Oblivion.esm if present, otherwise pick the first plugin
        preferred = None
        for p in initial_plugins:
            if p.lower() == 'oblivion.esm':
                preferred = p
                break
        file_var.set(preferred if preferred else initial_plugins[0])

    # Seed the fallback with whatever ended up selected above, so a focus-out
    # before the first pick still has a real plugin name to snap back to.
    last_valid[0] = file_var.get()

    # ── Output directory ──────────────────────────────────────────────────────
    out_frame = ttk.Frame(sidebar, style="Panel.TFrame")
    out_frame.pack(fill=tk.X, padx=14, pady=(0, 8))

    def _on_output_change(path):
        _save_dir_to_config()

    _path_row(out_frame, "Output Directory", output_var,
              browse_dir=True, on_change=_on_output_change)

    def _save_dir_to_config(*_):
        updated = load_config()
        updated["tes4DataPath"] = tes4_var.get()
        updated["tes5DataPath"] = tes5_var.get()
        updated["outputDir"]    = output_var.get()
        updated["workers"]      = _get_workers()
        save_config(updated)

    tes4_var.trace_add("write", lambda *_: None)  # live binding via on_change

    # ── Skyrim SE data directory (for the "Patch Skyrim" step) ───────────────
    tes5_frame = ttk.Frame(sidebar, style="Panel.TFrame")
    tes5_frame.pack(fill=tk.X, padx=14, pady=(0, 0))

    def _on_tes5_change(path):
        _refresh_patch_plugin_vars()
        _save_dir_to_config()

    _path_row(tes5_frame, "Skyrim SE Data Directory", tes5_var,
              browse_dir=True, on_change=_on_tes5_change)

    # ── Field order ───────────────────────────────────────────────────────────
    # Display order: Source, Skyrim dir, Output dir, then Plugin File.
    # Skyrim/Output are set once and rarely touched again; Source and Plugin
    # change run to run, so Plugin reads last, nearest the steps it drives.
    #
    # Re-packed here rather than by moving the blocks themselves: the plugin
    # selector carries ~200 lines of combobox search/focus handling that the
    # source callbacks close over, so reordering the block itself would mean
    # reordering those dependencies too. Pack order alone decides layout.
    for _blk in (sf, tes5_frame, out_frame):
        _blk.pack_forget()
        _blk.pack(fill=tk.X, padx=14, pady=(0, 8))
    # Last directory before the rule sits flush against it; _sep owns that gap.
    out_frame.pack_configure(pady=(0, 0))

    # The rule goes ABOVE the plugin box, not below it: it closes off the three
    # set-once directories, leaving the plugin selector grouped with the
    # Pipeline Steps it actually drives.
    _sep()

    # A little more room below than the 8px the directory blocks use between
    # each other: this gap separates two different KINDS of thing (the plugin
    # field and the step list), not two fields of the same kind.
    pf.pack_forget()
    pf.pack(fill=tk.X, padx=14, pady=(0, 12))

    # ── Pipeline steps ────────────────────────────────────────────────────────
    sh = ttk.Frame(sidebar, style="Panel.TFrame")
    sh.pack(fill=tk.X, padx=14, pady=(0, 4))  # flush to the rule above
    ttk.Label(sh, text="Pipeline Steps", style="PanelSub.TLabel").pack(side=tk.LEFT)

    def _runnable(key: str) -> bool:
        """False for a step the selected source has no content for.

        All/Default must not re-tick a greyed step -- the run would collect it
        and the phase would work on nothing.
        """
        cb = step_widgets.get(key, (None,))[0]
        return cb is None or str(cb.cget("state")) != "disabled"

    def _set_all():
        for key, v in step_vars.items():
            v.set(_runnable(key))
        _update_run_btn()

    def _set_default():
        on = default_on_steps(pack_default_var.get())
        for key, v in step_vars.items():
            v.set(key in on and _runnable(key))
        _update_run_btn()

    def _set_none():
        for v in step_vars.values():
            v.set(False)
        _update_run_btn()

    ttk.Button(sh, text="None", command=_set_none, width=5).pack(
        side=tk.RIGHT, padx=(2, 0))
    ttk.Button(sh, text="Default", command=_set_default, width=7).pack(
        side=tk.RIGHT, padx=(2, 0))
    ttk.Button(sh, text="All", command=_set_all, width=4).pack(
        side=tk.RIGHT, padx=(2, 0))

    # ── Upgrade shortcut ──────────────────────────────────────────────────────
    # Sits with All/Default/None: ticks exactly the steps whose code changed
    # between the version that last converted this plugin and the one in this
    # folder.  Without it every upgrade costs a full multi-hour reconversion
    # for what is usually a 3-step change.  Disabled and labelled "Up to date"
    # when nothing is owed, so the button's state IS the status readout -- no
    # separate banner needed.
    _upgrade_plan = [None]
    # Plugins already auto-applied, so re-selecting one does not stamp over
    # choices the user has since made by hand.
    _plan_applied = set()

    def _apply_upgrade_plan():
        """Tick exactly the steps the plan says this plugin still owes.

        Filtered through the Pack-by-default setting: the plan answers "what
        code changed since this plugin was last converted", which for a
        packaging change legitimately includes the packing steps -- but the
        setting is the user saying "never tick those for me automatically".
        Without this filter, selecting a plugin (which auto-applies the plan)
        silently re-ticked the boxes the setting had just cleared.
        """
        plan = _upgrade_plan[0]
        if not plan or not plan.get("steps"):
            return
        wanted = set(plan["steps"]) & default_on_steps(pack_default_var.get())
        for key, v in step_vars.items():
            v.set(key in wanted)
        _update_run_btn()

    upgrade_btn = ttk.Button(sh, text="Upgrade", width=9,
                             command=_apply_upgrade_plan)
    upgrade_btn.pack(side=tk.RIGHT, padx=(2, 0))

    _UPGRADE_TIP_IDLE = (
        "Everything in this folder's version has already been run for this "
        "plugin, so there is nothing to re-convert.")
    # Bound ONCE; the refresh only swaps the text (see _attach_tooltip).
    _set_upgrade_tip = _attach_tooltip(upgrade_btn, _UPGRADE_TIP_IDLE)

    _UPGRADE_TIP_NEVER_RUN = (
        "This plugin has never been converted, so there is no previous version "
        "to compare against and nothing to narrow down.\n\nLeave the default "
        "steps ticked and press Run.")
    _UPGRADE_TIP_OFFLINE = (
        "The list of steps each release changed is published on GitHub and "
        "could not be fetched.\n\nConnect to the internet and reselect this "
        "plugin to enable the shortcut, or tick the steps by hand.")

    def _refresh_upgrade_notice(auto_apply: bool = True):
        """Recompute the upgrade shortcut for the selected plugin.

        `auto_apply` ticks the implied steps the first time a given plugin's
        plan is seen -- the point of the feature is that a user who pastes a
        new build over the old one and hits Run gets the right subset without
        having to read anything.

        The plan is now a NETWORK call: the version -> steps table is fetched
        from the release assets rather than shipped in the tree (see
        version.py).  It therefore runs on a worker thread and comes back
        through `root.after`, because this function is called from combo-box
        and tab handlers where a blocking socket would freeze the window for
        the whole timeout.  version.py caches the result, including a failure,
        so only the first call per process actually goes out.
        """
        fname = file_var.get().strip()

        def _worker():
            try:
                plan = version_info.upgrade_plan(fname or None)
            except Exception:
                plan = None
            root.after(0, lambda: _apply_refresh(plan))

        def _apply_refresh(plan):
            # The user may have changed plugin while the fetch was in flight;
            # a stale answer must not overwrite the current selection's state.
            if file_var.get().strip() != fname:
                return
            _upgrade_plan[0] = plan

            if not plan:
                upgrade_btn.configure(text="Upgrade", state="disabled")
                _set_upgrade_tip(_UPGRADE_TIP_IDLE)
                return

            if plan["never_run"]:
                # NEVER CONVERTED is not "up to date".  It owes every step, and
                # labelling it "Up to date" told users with no output at all
                # that they had nothing to run -- the exact inversion of the
                # truth.  The shortcut still has nothing to *narrow* (there is
                # no previous version to diff against), so it stays inert, but
                # it must not claim the work is done.
                upgrade_btn.configure(text="Not converted", state="disabled")
                _set_upgrade_tip(_UPGRADE_TIP_NEVER_RUN)
                return

            if not plan["steps"]:
                # Genuinely current: something has been converted, and nothing
                # has changed since.  Keep the button in place but inert, so the
                # row never reflows and the state is legible.
                upgrade_btn.configure(text="Up to date", state="disabled")
                _set_upgrade_tip(_UPGRADE_TIP_IDLE)
                return

            if plan.get("offline"):
                # No table means no plan.  Ticking all twelve boxes here would
                # look like a considered recommendation for a multi-hour
                # reconversion when it is really just "we could not ask", so
                # the button goes inert and says why instead.
                upgrade_btn.configure(text="Offline", state="disabled")
                _set_upgrade_tip(_UPGRADE_TIP_OFFLINE)
                return

            label_of = dict(version_info.STEP_KEYS)
            names = ", ".join(label_of.get(k, k) for k in plan["steps"])
            if plan["unknown"]:
                # Not necessarily ALL of them: each step is resolved against its
                # own recorded version, so a step already run at this version
                # stays unticked even when another step's range cannot be read.
                tip = (f"Updated to {plan['current']} from "
                       f"{plan['installed']}.\n\n"
                       f"Which steps changed could not be determined for some "
                       f"of these, so they are selected to be safe:\n{names}")
            elif plan["upgraded"]:
                tip = (f"Updated {plan['installed']} → {plan['current']}.\n\n"
                       f"Selects only the steps still owed at this version:"
                       f"\n{names}")
            else:
                tip = (f"These steps have not been run at {plan['current']} "
                       f"for this plugin:\n{names}")

            upgrade_btn.configure(text="Upgrade", state="normal")
            _set_upgrade_tip(tip)

            if auto_apply and fname and fname not in _plan_applied:
                _plan_applied.add(fname)
                _apply_upgrade_plan()

        threading.Thread(target=_worker, daemon=True).start()

    def _update_run_btn(*_):
        has = any(v.get() for v in step_vars.values())
        st  = "normal" if has and not running.is_set() else "disabled"
        run_btn.configure(state=st)

    # ── Mesh subfolder modal overlay ──────────────────────────────────────────
    # A Frame placed over `outer` (fills the whole window) with a card centred
    # inside it.  No Toplevel — entirely within the existing window.

    def _open_mesh_subdir_panel():
        nonlocal mesh_subdir_vars

        fname   = file_var.get()
        subdirs = scan_mesh_subdirs(fname)

        old_vals = {name: v.get() for name, v in mesh_subdir_vars}
        mesh_subdir_vars.clear()
        for name in subdirs:
            mesh_subdir_vars.append((name, tk.BooleanVar(value=old_vals.get(name, True))))

        # Card placed directly over the window, no overlay behind it
        card = tk.Frame(outer, bg=CLR["panel"],
                        highlightbackground=CLR["border"], highlightthickness=1)
        _wheel_bound = []  # [bind_id] once a canvas is created below

        def _close():
            if _wheel_bound:
                card.unbind_all("<MouseWheel>")
            card.destroy()

        # Title row
        title_row = tk.Frame(card, bg=CLR["panel"])
        title_row.pack(fill=tk.X, padx=16, pady=(14, 0))
        tk.Label(title_row, text="Mesh subfolders to convert",
                 bg=CLR["panel"], fg=CLR["text"],
                 font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT)
        ttk.Button(title_row, text="All",
                   command=lambda: [v.set(True) for _, v in mesh_subdir_vars],
                   width=4).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(title_row, text="None",
                   command=lambda: [v.set(False) for _, v in mesh_subdir_vars],
                   width=5).pack(side=tk.RIGHT)

        ttk.Separator(card, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16, pady=8)

        if not subdirs:
            tk.Label(card, text="Run the Extract step first to populate this list.",
                     bg=CLR["panel"], fg=CLR["subtext"],
                     font=("Segoe UI", 9)).pack(anchor="w", padx=16, pady=(0, 8))
        else:
            list_frame = tk.Frame(card, bg=CLR["panel"])
            list_frame.pack(fill=tk.BOTH, expand=True, padx=8)

            canvas = tk.Canvas(list_frame, bg=CLR["panel"], highlightthickness=0,
                               width=320, height=min(360, 22 * len(mesh_subdir_vars)))
            vsb = ttk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
            inner = tk.Frame(canvas, bg=CLR["panel"])
            inner.bind("<Configure>",
                      lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
            canvas.create_window((0, 0), window=inner, anchor="nw")
            canvas.configure(yscrollcommand=vsb.set)
            canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            vsb.pack(side=tk.RIGHT, fill=tk.Y)

            def _wheel(e):
                canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")
            card.bind_all("<MouseWheel>", _wheel)
            _wheel_bound.append(True)

            for name, var in mesh_subdir_vars:
                ttk.Checkbutton(inner, text=name, variable=var,
                                style="TCheckbutton").pack(anchor="w", padx=12, pady=1)

        ttk.Separator(card, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16, pady=8)

        ttk.Button(card, text="OK", style="Accent.TButton",
                   command=_close).pack(pady=(0, 14))

        # Centre the card over the window
        card.update_idletasks()
        card.place(in_=outer, anchor="center", relx=0.5, rely=0.5)
        card.lift()

    def _refresh_patch_plugin_vars():
        """(Re)populate patch_plugin_vars from the Skyrim load order,
        preserving any existing checkbox state. New entries default to
        checked only if they're official content or listed in plugins.txt;
        plugins found only by a raw directory scan default to unchecked."""
        nonlocal patch_plugin_vars
        names, default_checked = scan_skyrim_load_order(tes5_var.get())
        old_vals = {name: v.get() for name, v in patch_plugin_vars}
        patch_plugin_vars = [
            (name, tk.BooleanVar(value=old_vals.get(name, name in default_checked)))
            for name in names]

    _refresh_patch_plugin_vars()

    # ── Create LOD selection ──────────────────────────────────────────────────
    # What the Create LOD dialog last confirmed: which plugins to generate, in
    # which order, and which worldspaces.
    #
    # The ORDER is conflict resolution, not presentation — LOD tiles are files
    # on a fixed grid, so the LAST plugin applied wins every tile two of them
    # both change.
    #
    # Empty means "never confirmed"; the defaults below are derived fresh each
    # time the dialog opens, so converting another plugin shows up without the
    # user having to reset anything.
    lod_plugins: list[str] = []      # chosen, in apply order (lowest first)
    lod_worldspaces: list[str] = []  # chosen worldspace EDIDs

    # "Convert to Master": the plugins to flag ESM. Empty means "never
    # confirmed", same convention as the LOD lists above.
    master_plugins: list[str] = []

    def _lod_out_root() -> Path:
        return Path(output_var.get().strip() or str(SCRIPT_DIR / "output"))

    def _plugin_masters(name: str) -> list[str]:
        """The MAST list of a converted plugin, or [] if it cannot be read."""
        try:
            sys.path.insert(0, str(SCRIPT_DIR / "tools"))
            from make_master import read_header, resolve
            _flags, masters = read_header(
                resolve(name, str(_lod_out_root())))
            return masters
        except Exception:
            return []

    def _default_master_plugins() -> list[str]:
        """Converted plugins that are worth flagging ESM, masters first.

        Ordered so a plugin always follows the masters it depends on: the tool
        rejects a batch that would leave an ESM mastering a plain ESP, and this
        is also the order the user must install them in.
        """
        try:
            from asset_convert.sibling_lod import converted_plugins
            names = sorted(converted_plugins(_lod_out_root()))
        except Exception:
            return []
        deps = {n: [m for m in _plugin_masters(n) if m in set(names)]
                for n in names}
        ordered, seen = [], set()

        def _visit(n, stack=()):
            if n in seen or n in stack:   # cycle: leave it where it lands
                return
            for m in deps.get(n, ()):
                _visit(m, stack + (n,))
            seen.add(n)
            ordered.append(n)

        for n in names:
            _visit(n)
        return ordered

    def _default_lod_plugins() -> list[str]:
        """Converted plugins in plugins.txt order, the rest appended.

        Must mirror `sibling_lod.create_lod_order` exactly — this list is what
        the user sees and drags, so deriving it differently here would show an
        order the run does not apply and misreport which plugin wins a tile.
        """
        try:
            from asset_convert.sibling_lod import (converted_plugins,
                                                   create_lod_order)
        except Exception:
            return []
        return create_lod_order(converted_plugins(_lod_out_root()),
                                SCRIPT_DIR / "export")

    def _default_lod_worldspaces(names: list[str]) -> list[str]:
        """Every worldspace the selected plugins would generate LOD for."""
        try:
            from asset_convert.sibling_lod import lod_worldspaces as _lw
        except Exception:
            return []
        return _lw(names, SCRIPT_DIR / "export", _lod_out_root())

    def _open_make_master_panel(on_apply=None):
        """Pick which converted plugins to flag as masters (ESM).

        Ticking a plugin auto-ticks the masters it depends on, because the tool
        refuses a batch that would leave an ESM mastering a plain ESP — an
        invalid load order, since a master must load first. Doing it here means
        the user sees the whole chain before anything is written rather than
        getting a refusal after pressing Apply.

        `on_apply` is called when Apply is pressed; None makes this a pure
        editor of the saved selection.
        """
        all_names = _default_master_plugins()
        deps = {n: [m for m in _plugin_masters(n) if m in set(all_names)]
                for n in all_names}

        try:
            sys.path.insert(0, str(SCRIPT_DIR / "tools"))
            from make_master import read_header, resolve, FLAG_ESM
            is_esm = {}
            for n in all_names:
                try:
                    flags, _m = read_header(resolve(n, _lod_out_root()))
                    is_esm[n] = bool(flags & FLAG_ESM)
                except Exception:
                    is_esm[n] = False
        except Exception:
            is_esm = {n: False for n in all_names}

        # Default selection: anything not already a master. A plugin that is
        # already flagged stays ticked so the picture reads as the FINAL state
        # rather than "what will change".
        wanted = set(master_plugins) & set(all_names) if master_plugins \
            else set(all_names)

        card = tk.Frame(outer, bg=CLR["panel"],
                        highlightbackground=CLR["border"], highlightthickness=1)
        _wheel_bound = []

        def _close():
            if _wheel_bound:
                card.unbind_all("<MouseWheel>")
            card.destroy()

        title_row = tk.Frame(card, bg=CLR["panel"])
        title_row.pack(fill=tk.X, padx=16, pady=(14, 0))
        tk.Label(title_row, text="Convert to Master",
                 bg=CLR["panel"], fg=CLR["text"],
                 font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT)
        tk.Label(card,
                 text=("A plugin that is not a master has EVERY reference it "
                       "contains treated as\nalways-active. The engine caps "
                       "those at 1,048,576 — past that the game hangs\non the "
                       "main menu with no crash and no log. Flagging a large "
                       "worldspace\nplugin ESM lets its references load per "
                       "cell instead.\n\nTicking a plugin also ticks the "
                       "masters it depends on: a master must load\nfirst, so "
                       "an ESM may not master a plain ESP."),
                 bg=CLR["panel"], fg=CLR["subtext"], justify=tk.LEFT,
                 font=("Segoe UI", 9)).pack(anchor="w", padx=16, pady=(4, 0))

        ttk.Separator(card, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16,
                                                       pady=8)

        if not all_names:
            tk.Label(card,
                     text="Nothing converted yet — convert a plugin first.",
                     bg=CLR["panel"], fg=CLR["subtext"],
                     font=("Segoe UI", 9)).pack(anchor="w", padx=16,
                                                pady=(0, 8))
            ttk.Separator(card, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16,
                                                           pady=8)
            ttk.Button(card, text="Close", command=_close).pack(pady=(0, 14))
            card.update_idletasks()
            card.place(in_=outer, anchor="center", relx=0.5, rely=0.5)
            card.lift()
            return

        list_frame = tk.Frame(card, bg=CLR["panel"])
        list_frame.pack(fill=tk.BOTH, expand=True, padx=16)

        canvas = tk.Canvas(list_frame, bg=CLR["panel"], highlightthickness=0,
                           width=460, height=min(300, 26 * len(all_names)))
        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=CLR["panel"])
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        def _wheel(e):
            canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")
        card.bind_all("<MouseWheel>", _wheel)
        _wheel_bound.append(True)

        warn = tk.Label(card, text="", bg=CLR["panel"], fg=CLR["yellow"],
                        justify=tk.LEFT, font=("Segoe UI", 9))
        warn.pack(anchor="w", padx=16, pady=(6, 0))

        row_vars: dict[str, tk.BooleanVar] = {}

        def _validate():
            """Warn if the ticked set would leave an ESM mastering an ESP."""
            sel = {n for n, v in row_vars.items() if v.get()}
            bad = []
            for n in sel:
                for m in deps.get(n, ()):
                    if m not in sel and not is_esm.get(m):
                        bad.append(f"{n} needs {m}")
            warn.configure(
                text=("⚠ " + "; ".join(bad[:3]) +
                      ("" if len(bad) <= 3 else f" (+{len(bad) - 3} more)"))
                if bad else "")
            return not bad

        def _on_toggle(name):
            """Tick a plugin -> tick its masters. Untick -> untick dependents."""
            if row_vars[name].get():
                stack = [name]
                while stack:
                    cur = stack.pop()
                    for m in deps.get(cur, ()):
                        if not row_vars[m].get():
                            row_vars[m].set(True)
                            stack.append(m)
            else:
                changed = True
                while changed:
                    changed = False
                    for n, v in row_vars.items():
                        if v.get() and any(not row_vars[m].get()
                                           for m in deps.get(n, ())):
                            v.set(False)
                            changed = True
            _validate()

        def _set_all(on: bool):
            """All -> every plugin (masters included, so nothing dangles);
            None -> nothing (so no dependent is left without its master)."""
            for v in row_vars.values():
                v.set(on)
            _validate()

        ttk.Button(title_row, text="All", width=4,
                   command=lambda: _set_all(True)).pack(side=tk.RIGHT,
                                                        padx=(4, 0))
        ttk.Button(title_row, text="None", width=5,
                   command=lambda: _set_all(False)).pack(side=tk.RIGHT)

        for name in all_names:
            row = tk.Frame(inner, bg=CLR["panel"])
            row.pack(fill=tk.X, anchor="w", pady=1)
            var = tk.BooleanVar(value=name in wanted)
            row_vars[name] = var
            ttk.Checkbutton(row, text=name, variable=var,
                            style="TCheckbutton",
                            command=lambda n=name: _on_toggle(n)
                            ).pack(side=tk.LEFT, padx=12)
            if is_esm.get(name):
                tk.Label(row, text="already ESM", bg=CLR["panel"],
                         fg=CLR["green"], font=("Segoe UI", 8)
                         ).pack(side=tk.LEFT, padx=(4, 0))
            if deps.get(name):
                tk.Label(row, text="masters: " + ", ".join(deps[name]),
                         bg=CLR["panel"], fg=CLR["subtext"],
                         font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=(6, 0))

        _validate()

        ttk.Separator(card, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16,
                                                       pady=8)

        btns = tk.Frame(card, bg=CLR["panel"])
        btns.pack(pady=(0, 14))

        def _apply():
            if not _validate():
                return
            master_plugins[:] = [n for n in all_names if row_vars[n].get()]
            _close()
            if on_apply is not None:
                on_apply()

        ttk.Button(btns, text="Apply", style="Accent.TButton",
                   command=_apply).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="Cancel", command=_close).pack(side=tk.LEFT,
                                                             padx=4)

        card.update_idletasks()
        card.place(in_=outer, anchor="center", relx=0.5, rely=0.5)
        card.lift()

    def _open_create_lod_panel(on_generate=None):
        """Pick the plugins (left, ordered) and worldspaces (right) to build.

        Two lists rather than one because they answer different questions. The
        plugin list is ORDERED — its order decides who wins a contested tile —
        so it is a drag-reorder list with a tick per row. The worldspace list is
        an unordered filter, so it is plain checkboxes.

        `on_generate` is called with (plugins, worldspaces) when Generate is
        pressed; passing None makes the dialog a pure editor of the saved
        selection, which is what the menu entry wants.
        """
        all_names = _default_lod_plugins()
        # Two sets, deliberately:
        #   `wanted`  - what the user has ticked. Their intent, edited only by
        #               their own clicks.
        #   `checked` - what would actually run: `wanted` minus anything greyed
        #               out for resting on a master they turned off.
        # Keeping them apart is what lets unticking a master grey its dependents
        # and re-ticking it restore them, instead of the dependents' own ticks
        # being destroyed on the way through.
        #
        # Start from the confirmed selection, but never hide a plugin converted
        # since it was made: unknown names are appended in default order, so a
        # new conversion appears (ticked) rather than silently dropping out of
        # every future run.
        if lod_plugins:
            ordered = [n for n in lod_plugins if n in all_names]
            ordered += [n for n in all_names if n not in ordered]
            wanted = {n for n in lod_plugins if n in all_names}
            wanted |= {n for n in all_names if n not in lod_plugins}
        else:
            ordered = list(all_names)
            wanted = set(all_names)
        checked: set[str] = set(wanted)

        card = tk.Frame(outer, bg=CLR["panel"],
                        highlightbackground=CLR["border"], highlightthickness=1)
        _wheel_bound = []

        def _close():
            if _wheel_bound:
                card.unbind_all("<MouseWheel>")
            card.destroy()

        tk.Label(card, text="Create LOD",
                 bg=CLR["panel"], fg=CLR["text"],
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=16,
                                                     pady=(14, 0))
        tk.Label(card,
                 text=("Distant LOD is generated once for the whole load "
                       "order.\nDrag to reorder — the plugin at the BOTTOM "
                       "wins any tile two of them both change."),
                 bg=CLR["panel"], fg=CLR["subtext"], justify=tk.LEFT,
                 font=("Segoe UI", 9)).pack(anchor="w", padx=16, pady=(4, 0))

        ttk.Separator(card, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16,
                                                       pady=8)

        if not all_names:
            tk.Label(card,
                     text="Nothing converted yet — convert a plugin first.",
                     bg=CLR["panel"], fg=CLR["subtext"],
                     font=("Segoe UI", 9)).pack(anchor="w", padx=16,
                                                pady=(0, 8))
            ttk.Separator(card, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16,
                                                           pady=8)
            ttk.Button(card, text="Close", command=_close).pack(pady=(0, 14))
            card.update_idletasks()
            card.place(in_=outer, anchor="center", relx=0.5, rely=0.5)
            card.lift()
            return

        cols = tk.Frame(card, bg=CLR["panel"])
        cols.pack(fill=tk.BOTH, expand=True, padx=16)
        cols.columnconfigure(0, weight=1, uniform="lodcol")
        cols.columnconfigure(1, weight=1, uniform="lodcol")

        # ── Left: plugins, ordered ────────────────────────────────────────────
        left = tk.Frame(cols, bg=CLR["panel"])
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        lhead = tk.Frame(left, bg=CLR["panel"])
        lhead.pack(fill=tk.X)
        tk.Label(lhead, text="Plugins", bg=CLR["panel"], fg=CLR["text"],
                 font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)

        # Who depends on whom, and which worldspaces each plugin brings. Both
        # are scanned ONCE here — they read every export dir — so that every
        # tick is a dict lookup rather than a rescan.
        _ws_why: dict = {}
        try:
            from asset_convert.sibling_lod import (
                dependents_of, worldspaces_by_plugin_diagnosed,
                merge_worldspaces)
            _deps = dependents_of(all_names, SCRIPT_DIR / "export")
            _ws_by, _ws_why = worldspaces_by_plugin_diagnosed(
                all_names, SCRIPT_DIR / "export", _lod_out_root())
        except Exception as _exc:
            _deps = {n: set() for n in all_names}
            _ws_by = {n: [] for n in all_names}
            _ws_why = {"": f"Worldspace scan failed: {_exc}"}

            def merge_worldspaces(names, by_plugin):
                return []

        # A Listbox, not a stack of Checkbuttons: it already gives index ->
        # item hit-testing (`nearest`), selection and scrolling, which is the
        # whole mechanic a drag-reorder needs. The tick is drawn INTO the row
        # text and toggled by clicking it, so one widget carries both the
        # order and the on/off state without them fighting over the mouse.
        TICK, UNTICK = "☑ ", "☐ "

        # A plugin whose master is unticked cannot be generated: its LOD is
        # baked as "master + itself", so without the master there is no terrain
        # to overlay onto. Those rows are unticked AND greyed, which is the
        # difference between "you turned this off" and "this is unavailable".
        disabled: set[str] = set()

        def _recompute_disabled():
            """Grey out everything that rests on a master the user turned off.

            `_deps[m]` is already transitive, so one pass over the plugins the
            user has unticked covers indirect dependents too: unticking
            Nehrim.esm greys Translation.esp without Translation ever naming
            Nehrim's own masters.

            Driven by `wanted` — what the user actually clicked — rather than by
            `checked`, which this function itself narrows. Reading `checked`
            would make a greyed row look like a user choice on the next pass and
            grey ITS dependents too, so a chain would keep collapsing.
            """
            disabled.clear()
            for name in all_names:
                if name not in wanted:
                    disabled.update(_deps.get(name, ()))
            # A plugin the user unticked themselves is OFF, not unavailable;
            # only the fallout of someone else's master greys out.
            disabled.difference_update(n for n in all_names if n not in wanted)
            checked.clear()
            checked.update(n for n in wanted if n not in disabled)

        def _row(name: str) -> str:
            # A disabled row shows an EMPTY box, like an unticked one — it is
            # genuinely not going to run. The grey foreground applied in
            # _redraw is what separates "unavailable" from "you turned it off".
            return (TICK if name in checked else UNTICK) + name

        plb = tk.Listbox(left, bg=CLR["log_bg"], fg=CLR["text"],
                         selectbackground=CLR["accent"],
                         selectforeground="#ffffff", highlightthickness=0,
                         borderwidth=0, activestyle="none",
                         font=("Segoe UI", 9), width=34,
                         height=min(14, max(5, len(ordered))),
                         exportselection=False)
        lsb = ttk.Scrollbar(left, orient="vertical", command=plb.yview)
        plb.configure(yscrollcommand=lsb.set)
        plb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, pady=(4, 0))
        lsb.pack(side=tk.RIGHT, fill=tk.Y, pady=(4, 0))

        def _name_at(i: int) -> str:
            """The plugin name in row `i`, with any tick prefix stripped.

            Tolerates a bare name so the list can be populated with plain
            strings and painted afterwards, rather than every insertion site
            having to know the prefix format.
            """
            row = plb.get(i)
            return row[2:] if row[:2] in (TICK, UNTICK) else row

        def _redraw():
            """Repaint every row's tick and grey state, preserving order.

            Rewrites in place rather than clearing and re-inserting the whole
            list, so the scroll position survives a toggle.
            """
            _recompute_disabled()
            for i in range(plb.size()):
                n = _name_at(i)
                plb.delete(i)
                plb.insert(i, _row(n))
                # Greyed rows are drawn in the subtext color so "unavailable"
                # is visible at a glance and not just inferred from the tick.
                plb.itemconfigure(
                    i, foreground=(CLR["subtext"] if n in disabled
                                   else CLR["text"]))
            _refresh_worldspaces()

        def _set_all(on: bool):
            wanted.update(all_names) if on else wanted.clear()
            _redraw()

        ttk.Button(lhead, text="All", width=4,
                   command=lambda: _set_all(True)).pack(side=tk.RIGHT,
                                                        padx=(4, 0))
        ttk.Button(lhead, text="None", width=5,
                   command=lambda: _set_all(False)).pack(side=tk.RIGHT)

        # Drag state. `moved` separates a click (toggle the tick) from a drag
        # (reorder): without it, every reorder would also flip the tick of the
        # row it started on.
        drag = {"from": None, "moved": False}

        def _press(e):
            drag["from"] = plb.nearest(e.y)
            drag["moved"] = False
            plb.selection_clear(0, tk.END)
            plb.selection_set(drag["from"])

        def _motion(e):
            src = drag["from"]
            if src is None:
                return
            dst = plb.nearest(e.y)
            if dst < 0 or dst == src:
                return
            # Move one row at a time so the list follows the cursor
            # continuously instead of jumping on release.
            item = plb.get(src)
            plb.delete(src)
            plb.insert(dst, item)
            plb.selection_clear(0, tk.END)
            plb.selection_set(dst)
            drag["from"] = dst
            drag["moved"] = True

        def _release(e):
            i = drag["from"]
            drag["from"] = None
            if i is None or drag["moved"] or i < 0 or i >= plb.size():
                return
            n = _name_at(i)
            # A greyed row is not clickable: it is off because its master is
            # off, and the fix is to re-tick the master, not this.
            if n in disabled:
                return
            wanted.discard(n) if n in wanted else wanted.add(n)
            _redraw()
            plb.selection_clear(0, tk.END)
            plb.selection_set(i)

        plb.bind("<Button-1>", _press)
        plb.bind("<B1-Motion>", _motion)
        plb.bind("<ButtonRelease-1>", _release)

        # ── Right: worldspaces ────────────────────────────────────────────────
        right = tk.Frame(cols, bg=CLR["panel"])
        right.grid(row=0, column=1, sticky="nsew")

        rhead = tk.Frame(right, bg=CLR["panel"])
        rhead.pack(fill=tk.X)
        tk.Label(rhead, text="Worldspaces", bg=CLR["panel"], fg=CLR["text"],
                 font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)

        # The worldspaces the TICKED plugins bring, rebuilt on every toggle.
        # A worldspace only exists in this run because some selected plugin
        # ships LOD for it, so unticking that plugin must remove it — leaving it
        # on screen would offer work the run cannot do.
        #
        # `ws_state` remembers each worldspace's tick across rebuilds, so a
        # worldspace that disappears when its plugin is unticked comes back
        # ticked exactly as the user left it, rather than silently resetting.
        ws_state: dict[str, bool] = {w: True for w in
                                     _default_lod_worldspaces(all_names)}
        if lod_worldspaces:
            for w in ws_state:
                ws_state[w] = w in lod_worldspaces
        ws_vars: list = []

        ws_frame = tk.Frame(right, bg=CLR["panel"])
        ws_frame.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

        wcanvas = tk.Canvas(ws_frame, bg=CLR["panel"], highlightthickness=0,
                            width=220, height=300)
        wsb = ttk.Scrollbar(ws_frame, orient="vertical", command=wcanvas.yview)
        winner = tk.Frame(wcanvas, bg=CLR["panel"])
        winner.bind("<Configure>",
                    lambda e: wcanvas.configure(
                        scrollregion=wcanvas.bbox("all")))
        wcanvas.create_window((0, 0), window=winner, anchor="nw")
        wcanvas.configure(yscrollcommand=wsb.set)
        wcanvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        wsb.pack(side=tk.RIGHT, fill=tk.Y)

        def _wheel(e):
            wcanvas.yview_scroll(-1 if e.delta > 0 else 1, "units")
        card.bind_all("<MouseWheel>", _wheel)
        _wheel_bound.append(True)

        def _refresh_worldspaces():
            """Rebuild the worldspace list from the currently ticked plugins."""
            # Save what is on screen before tearing it down, or a rebuild
            # triggered by an unrelated plugin toggle would discard the
            # worldspace ticks the user just made.
            for wname, wvar in ws_vars:
                ws_state[wname] = bool(wvar.get())

            names = [n for n in (_name_at(i) for i in range(plb.size()))
                     if n in checked]
            live = merge_worldspaces(names, _ws_by)

            for child in winner.winfo_children():
                child.destroy()
            ws_vars.clear()

            if not live:
                # Say WHY, per plugin. "No worldspace ships distant LOD" is a
                # symptom shared by every failure mode, and it is outright
                # wrong for the commonest one — assets never extracted — which
                # sent users looking for a plugin problem that did not exist.
                why = [_ws_why[n] for n in names if n in _ws_why]
                why += [v for k, v in _ws_why.items() if k == ""]
                text = ("No selected plugin has worldspace terrain\n"
                        "to generate LOD from.")
                if why:
                    text = "\n\n".join(why[:6])
                    if len(why) > 6:
                        text += f"\n\n(+{len(why) - 6} more)"
                lbl = tk.Label(winner, text=text, bg=CLR["panel"],
                               fg=CLR["subtext"], justify=tk.LEFT,
                               wraplength=210, font=("Segoe UI", 9))
                lbl.pack(anchor="w", padx=4, pady=4)
                return

            # A MISSING EXPORT has to be visible even when other plugins
            # filled the list. Shipped LOD is the authority now, so "this
            # plugin offers nothing" is the ordinary case for most of the load
            # order and cannot itself be the trigger -- but a deleted export
            # produces the same silence, and previously the warning only
            # appeared when NOTHING was offered, so one healthy plugin hid it.
            lost = [_ws_why[n] for n in names
                    if n in _ws_why and 'run the Export stage' in _ws_why[n]]
            if lost:
                warn = tk.Label(
                    winner,
                    text="\n\n".join(lost[:4]) +
                         (f"\n\n(+{len(lost) - 4} more)" if len(lost) > 4
                          else ""),
                    bg=CLR["panel"], fg=CLR["yellow"],
                    justify=tk.LEFT, wraplength=210, font=("Segoe UI", 9))
                warn.pack(anchor="w", padx=4, pady=(4, 6))

            for wname in live:
                var = tk.BooleanVar(value=ws_state.get(wname, True))
                ws_vars.append((wname, var))
                ttk.Checkbutton(winner, text=wname, variable=var,
                                style="TCheckbutton").pack(anchor="w", padx=4,
                                                           pady=1)

        ttk.Button(rhead, text="All", width=4,
                   command=lambda: [v.set(True) for _, v in ws_vars]
                   ).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(rhead, text="None", width=5,
                   command=lambda: [v.set(False) for _, v in ws_vars]
                   ).pack(side=tk.RIGHT)

        # Fill the list and paint the initial state. Deferred to here because
        # _redraw calls _refresh_worldspaces, which needs the right-hand panel
        # to exist — so both columns must be built before the first paint.
        for n in ordered:
            plb.insert(tk.END, n)
        _redraw()

        # ── Buttons ───────────────────────────────────────────────────────────
        ttk.Separator(card, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16,
                                                       pady=8)

        btns = tk.Frame(card, bg=CLR["panel"])
        btns.pack(fill=tk.X, padx=16, pady=(0, 14))

        def _reset():
            """Back to the derived default: everything on, default order."""
            plb.delete(0, tk.END)
            for n in _default_lod_plugins():
                plb.insert(tk.END, n)     # text is repainted by _redraw
            wanted.clear()
            wanted.update(all_names)
            for w in ws_state:
                ws_state[w] = True
            _redraw()

        ttk.Button(btns, text="Reset", command=_reset).pack(side=tk.LEFT)

        def _collect():
            plugins = [_name_at(i) for i in range(plb.size())
                       if _name_at(i) in checked]
            worlds = [w for w, v in ws_vars if v.get()]
            return plugins, worlds

        def _generate():
            plugins, worlds = _collect()
            if not plugins:
                _info("No Plugins", "Tick at least one plugin to generate "
                                    "LOD for.")
                return
            if ws_vars and not worlds:
                _info("No Worldspaces", "Tick at least one worldspace to "
                                        "generate LOD for.")
                return
            # Store the confirmed selection even when it equals the default:
            # the user having LOOKED and approved is itself information, and it
            # keeps the next run stable if plugins.txt changes in between.
            lod_plugins[:] = plugins
            lod_worldspaces[:] = worlds
            _close()
            if on_generate is not None:
                on_generate(plugins, worlds)

        ttk.Button(btns, text="Generate", style="Accent.TButton",
                   command=_generate).pack(side=tk.RIGHT)
        ttk.Button(btns, text="Cancel", command=_close).pack(side=tk.RIGHT,
                                                             padx=(0, 6))

        card.update_idletasks()
        card.place(in_=outer, anchor="center", relx=0.5, rely=0.5)
        card.lift()

    def _open_patch_plugin_panel():
        _refresh_patch_plugin_vars()

        card = tk.Frame(outer, bg=CLR["panel"],
                        highlightbackground=CLR["border"], highlightthickness=1)
        _wheel_bound = []  # [bind_id] once a canvas is created below

        def _close():
            if _wheel_bound:
                card.unbind_all("<MouseWheel>")
            card.destroy()

        title_row = tk.Frame(card, bg=CLR["panel"])
        title_row.pack(fill=tk.X, padx=16, pady=(14, 0))
        tk.Label(title_row, text="Plugins to patch (slot 44 / body)",
                 bg=CLR["panel"], fg=CLR["text"],
                 font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT)
        ttk.Button(title_row, text="All",
                   command=lambda: [v.set(True) for _, v in patch_plugin_vars],
                   width=4).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(title_row, text="None",
                   command=lambda: [v.set(False) for _, v in patch_plugin_vars],
                   width=5).pack(side=tk.RIGHT)

        ttk.Separator(card, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16, pady=8)

        if not patch_plugin_vars:
            tk.Label(card,
                     text="No plugins found. Set the Skyrim SE Data Directory above.",
                     bg=CLR["panel"], fg=CLR["subtext"],
                     font=("Segoe UI", 9)).pack(anchor="w", padx=16, pady=(0, 8))
        else:
            list_frame = tk.Frame(card, bg=CLR["panel"])
            list_frame.pack(fill=tk.BOTH, expand=True, padx=8)

            canvas = tk.Canvas(list_frame, bg=CLR["panel"], highlightthickness=0,
                               width=320, height=min(360, 22 * len(patch_plugin_vars)))
            vsb = ttk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
            inner = tk.Frame(canvas, bg=CLR["panel"])
            inner.bind("<Configure>",
                      lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
            canvas.create_window((0, 0), window=inner, anchor="nw")
            canvas.configure(yscrollcommand=vsb.set)
            canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            vsb.pack(side=tk.RIGHT, fill=tk.Y)

            def _wheel(e):
                canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")
            card.bind_all("<MouseWheel>", _wheel)
            _wheel_bound.append(True)

            for name, var in patch_plugin_vars:
                ttk.Checkbutton(inner, text=name, variable=var,
                                style="TCheckbutton").pack(anchor="w", padx=8, pady=1)

        ttk.Separator(card, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16, pady=8)

        ttk.Button(card, text="OK", style="Accent.TButton",
                   command=_close).pack(pady=(0, 14))

        card.update_idletasks()
        card.place(in_=outer, anchor="center", relx=0.5, rely=0.5)
        card.lift()

    # Build step checkboxes
    _mesh_step_row = None
    # key -> (checkbutton, description label). Kept so a step the selected
    # source cannot run (an asset-only mod has no plugin to export) can be
    # disabled rather than silently running on nothing.
    step_widgets: dict = {}
    def _step_row(key, label, tip, indent=0):
        row = ttk.Frame(sidebar, style="Panel.TFrame")
        row.pack(fill=tk.X, padx=14, pady=(0, 1) if indent else 1)
        cb = ttk.Checkbutton(row, text=label, variable=step_vars[key],
                             command=_update_run_btn)
        cb.pack(side=tk.LEFT, padx=(indent, 0))
        tip_lbl = ttk.Label(row, text=tip, style="PanelSub.TLabel")
        tip_lbl.pack(side=tk.LEFT, padx=(6, 0))
        step_widgets[key] = (cb, tip_lbl, tip)
        return row

    # Sub-steps are emitted right after their parent, so ordinary pack order
    # places them correctly without any `after=` juggling.
    _children = {}
    for step in STEPS:
        if step[0] in SUB_OF:
            _children.setdefault(SUB_OF[step[0]], []).append(step)
    for step in STEPS:
        key, label, tip = step[0], step[2], step[3]
        if key in SUB_OF:
            continue
        row = _step_row(key, label, tip)
        for sub in _children.get(key, ()):
            _step_row(sub[0], sub[2], sub[3], indent=20)
        if key == "meshes":
            _mesh_step_row = row

    _NEMESIS_TIP = (
        "Only needed if you run Nemesis Unlimited Behavior Engine.\n\n"
        "Nemesis does not read the game's animationdatasinglefile.txt -- it "
        "reads its OWN meshes\\nemesis_*singlefile.txt pair and "
        "regenerates the game-facing one from it. Our creature projects are "
        "not in that baseline, so they de-register: the creature still "
        "appears and idles, but no clip has any data -- it slides along with "
        "no walk animation and never attacks.\n\n"
        "This ships our own copy of that pair (Nemesis's originals + our "
        "creatures) so BOTH Skyrim's creatures and ours survive every "
        "regeneration. The Nemesis install is only read, never modified.\n\n"
        "Load order: this mod AFTER 'Nemesis Unlimited Behavior Engine' "
        "and BEFORE 'Nemesis Output'.\n\n"
        "Point Tools > Set Nemesis Folder at your Nemesis mod folder first."
    )
    if "nemesis" in step_widgets:
        _attach_tooltip(step_widgets["nemesis"][0], _NEMESIS_TIP)
        _attach_tooltip(step_widgets["nemesis"][1], _NEMESIS_TIP)

    # Small link sitting just below the Meshes checkbox row
    _mesh_toggle_row = ttk.Frame(sidebar, style="Panel.TFrame")
    _mesh_toggle_row.pack(fill=tk.X, padx=14, pady=(0, 1), after=_mesh_step_row)
    mesh_toggle_lbl = tk.Label(
        _mesh_toggle_row, text="  filter subfolders...",
        bg=CLR["panel"], fg=CLR["subtext"],
        font=("Segoe UI", 9, "underline"), cursor="hand2",
    )
    mesh_toggle_lbl.pack(side=tk.LEFT, padx=(20, 0))
    mesh_toggle_lbl.bind("<Button-1>", lambda _: _open_mesh_subdir_panel())

    # Parallax carry-over, the other Meshes sub-option.  No per-plugin default
    # and no auto-on: whether the output renders correctly depends on the
    # PLAYER's setup, not on the plugin, so only they can answer it.
    _parallax_row = ttk.Frame(sidebar, style="Panel.TFrame")
    _parallax_row.pack(fill=tk.X, padx=14, pady=(0, 1), after=_mesh_toggle_row)
    _parallax_chk = ttk.Checkbutton(_parallax_row, text="Convert parallax",
                                    variable=parallax_var,
                                    style="TCheckbutton")
    _parallax_chk.pack(side=tk.LEFT, padx=(20, 0))
    _parallax_hint = ttk.Label(_parallax_row, text="needs Community Shaders",
                               style="PanelSub.TLabel")
    _parallax_hint.pack(side=tk.LEFT, padx=(6, 0))

    _PARALLAX_TIP = (
        "Carries Oblivion's own parallax (depth on dungeon walls, rock and "
        "architecture) across to Skyrim.\n\n"
        "ONLY turn this on if you play with Community Shaders or an ENB.\n\n"
        "Without one, the affected surfaces do not just look flat -- the "
        "texture visibly swims across them as you move. Tested: the SSE "
        "Parallax Shader Fix does not repair it."
    )
    _attach_tooltip(_parallax_chk, _PARALLAX_TIP)
    _attach_tooltip(_parallax_hint, _PARALLAX_TIP)

    # Textures only, a sub-option OF parallax: it exists so PGPatcher can
    # do the mesh side across the player's whole load order, and without
    # parallax on it would just be a texture copy with the meshes missing.
    # Enabled/disabled follows the parallax box for exactly that reason.
    _texonly_row = ttk.Frame(sidebar, style="Panel.TFrame")
    _texonly_row.pack(fill=tk.X, padx=14, pady=(0, 1), after=_parallax_row)
    _texonly_chk = ttk.Checkbutton(_texonly_row, text="Textures only",
                                   variable=tex_only_var,
                                   style="TCheckbutton")
    _texonly_chk.pack(side=tk.LEFT, padx=(40, 0))
    _texonly_hint = ttk.Label(_texonly_row, text="for PGPatcher",
                              style="PanelSub.TLabel")
    _texonly_hint.pack(side=tk.LEFT, padx=(6, 0))

    _TEXONLY_TIP = (
        "Ships the textures and their height maps, but NO meshes.\n\n"
        "For PGPatcher (ParallaxGen), which patches meshes across your "
        "whole load order and can also upgrade them to complex material "
        "-- neither of which a single-plugin conversion can see.\n\n"
        "The meshes are still read: Oblivion's parallax flag lives in the "
        "mesh, and it is the only evidence that a texture carries a "
        "height map at all."
    )
    _attach_tooltip(_texonly_chk, _TEXONLY_TIP)
    _attach_tooltip(_texonly_hint, _TEXONLY_TIP)

    def _sync_texonly(*_a):
        on = parallax_var.get()
        _texonly_chk.state(['!disabled'] if on else ['disabled'])
        if not on:
            tex_only_var.set(False)

    parallax_var.trace_add('write', _sync_texonly)
    _sync_texonly()


    # ── Action buttons ────────────────────────────────────────────────────────
    # 12px above, matching a separator's gap: the step rows are packed tight
    # (pady=1 each), so without it the Run button crowds the last checkbox and
    # reads as another entry in the list rather than the thing that runs them.
    # Bottom pad 0: the rule below owns that gap. A 6px pad here stacked on the
    # separator's own 12px and made the space above that line visibly wider
    # than the matching space below it.
    bf = ttk.Frame(sidebar, style="Panel.TFrame")
    bf.pack(fill=tk.X, padx=14, pady=(_SEP_GAP, 0))

    run_btn = ttk.Button(bf, text="  Run Selected Steps",
                         style="Run.TButton", command=lambda: _run_clicked())
    run_btn.pack(fill=tk.X, pady=(0, 6))

    # Clear Log + Cancel on the same row
    btn_row = ttk.Frame(bf, style="Panel.TFrame")
    btn_row.pack(fill=tk.X)
    btn_row.columnconfigure(0, weight=1)
    btn_row.columnconfigure(1, weight=1)

    clear_btn = ttk.Button(btn_row, text="Clear Log", command=lambda: _clear_log(),
                           style="Danger.TButton")
    clear_btn.grid(row=0, column=0, sticky="ew", padx=(0, 3))

    cancel_btn = ttk.Button(btn_row, text="Cancel", command=lambda: _cancel_clicked(),
                            style="Cancel.TButton", state="disabled")
    cancel_btn.grid(row=0, column=1, sticky="ew", padx=(3, 0))

    # ── Global actions ────────────────────────────────────────────────────────
    # Below the whole Run/Clear/Cancel cluster, behind its own rule. Those three
    # are one control group — Run starts the numbered steps for the ONE selected
    # plugin, Cancel stops them, Clear tidies their log — and putting Global in
    # the middle of it split that group in half.
    #
    # These take no plugin at all: they run once over everything converted so
    # far. Separate concern, so they get their own captioned block at the
    # bottom rather than sitting among the per-plugin controls.
    #
    # Uses the shared _sep() rather than a hand-rolled Separator so its spacing
    # matches the only two other rules in the sidebar instead of being a third,
    # slightly different gap.
    _sep()

    # Padded exactly like the "Pipeline Steps" header block above — same frame
    # pady, same bare label — so the gap under this rule matches the gap under
    # the other two instead of being its own value.
    # The 8px top pad is deliberate and matches, not pads past, the "Pipeline
    # Steps" header: that frame is 31px tall because it also carries the
    # All/Default/None buttons, which centre its label 16px below the rule.
    # This header has no buttons to stretch it, so the same optical gap has to
    # be stated outright — copying the other block's padding alone left it
    # sitting 8px high and the two rules looked unevenly spaced.
    gh = ttk.Frame(sidebar, style="Panel.TFrame")
    gh.pack(fill=tk.X, padx=14, pady=(0, 4))  # flush to the rule above
    ttk.Label(gh, text="Global", style="PanelSub.TLabel").pack(side=tk.LEFT)

    gf = ttk.Frame(sidebar, style="Panel.TFrame")
    gf.pack(fill=tk.X, padx=14, pady=(0, 6))

    # Laid out by the `row` field: position in GLOBAL_ACTIONS decides the
    # column. EVERY row keeps the same two uniform columns, so every button is
    # the same width and a row holding one action fills only its own half —
    # spanning the full width made a lone button read as the most important
    # action here, which none of them is.
    global_btns: dict[str, ttk.Button] = {}
    _rows: dict[int, list] = {}
    for _act in GLOBAL_ACTIONS:
        _rows.setdefault(_act[4], []).append(_act)

    for _r in sorted(_rows):
        _acts = _rows[_r]
        _row_f = ttk.Frame(gf, style="Panel.TFrame")
        _row_f.pack(fill=tk.X, pady=(0 if _r == 0 else 4, 0))
        _row_f.columnconfigure(0, weight=1, uniform="global")
        _row_f.columnconfigure(1, weight=1, uniform="global")
        for _i, (gkey, glabel, gtip, gshort, _rr) in enumerate(_acts):
            gb = ttk.Button(_row_f, text=gshort, style="Global.TButton",
                            command=(lambda k=gkey: _run_global_action(k)))
            gb.grid(row=0, column=_i, sticky="ew",
                    padx=((0, 3) if _i == 0 else (3, 0)))
            _attach_tooltip(gb, gtip)
            global_btns[gkey] = gb

    # One sub-link per global action, on a single row sharing the SAME two
    # columns as the buttons above, so each link sits directly under the button
    # it belongs to. Stacked on separate rows they read as two options of the
    # left-hand button rather than one option of each.
    _links_row = ttk.Frame(gf, style="Panel.TFrame")
    _links_row.pack(fill=tk.X, pady=(5, 0))
    _links_row.columnconfigure(0, weight=1, uniform="global")
    _links_row.columnconfigure(1, weight=1, uniform="global")

    body_toggle_lbl = tk.Label(
        _links_row, text="select plugins...",
        bg=CLR["panel"], fg=CLR["subtext"],
        font=("Segoe UI", 9, "underline"), cursor="hand2",
    )
    body_toggle_lbl.grid(row=0, column=0, sticky="w", padx=(0, 3))
    body_toggle_lbl.bind("<Button-1>", lambda _: _open_patch_plugin_panel())

    # Column 0 of this row is under "Patch Skyrim", the last row's left-hand
    # button, which is the only action with a selection to make from here.
    # "Create LOD" needs no sub-link — the button itself opens the selection
    # dialog, so a second entry point to the same panel would just duplicate it
    # — and neither packaging action has anything to choose.

    # Progress bar + status, both anchored to the BOTTOM of the sidebar.
    #
    # The status row was already side=BOTTOM while the bar was side=TOP, so
    # every spare pixel in the sidebar pooled between the two — the taller
    # window needed to fit the bar turned into a 74px void above "Ready".
    # Anchoring the bar to the bottom as well keeps the pair together and puts
    # the slack above the whole group, where it reads as ordinary breathing
    # room instead of a hole. status_row is packed FIRST so that, with
    # side=BOTTOM stacking upward, it ends up underneath the bar.
    status_row = ttk.Frame(sidebar, style="Panel.TFrame")
    status_row.pack(side=tk.BOTTOM, fill=tk.X, padx=14, pady=(0, 10))

    # pady on the BOTTOM side, not the top: the bar sits ABOVE "Ready" now, so
    # a top pad puts the gap on the wrong side of it and the bar ends up
    # touching the status text.
    prog_bar = ttk.Progressbar(sidebar, mode="indeterminate", length=200)
    prog_bar.pack(side=tk.BOTTOM, fill=tk.X, padx=14, pady=(0, 6))
    prog_bar.pack_forget()

    status_var = tk.StringVar(value="Ready")
    ttk.Label(status_row, textvariable=status_var, style="PanelSub.TLabel").pack(
        side=tk.LEFT)

    timer_var = tk.StringVar(value="")
    ttk.Label(status_row, textvariable=timer_var, style="PanelSub.TLabel").pack(
        side=tk.LEFT, padx=(8, 0))

    # ── Log pane ──────────────────────────────────────────────────────────────
    log_hdr = tk.Frame(log_pane, bg=CLR["panel"], height=34)
    log_hdr.pack(fill=tk.X)
    log_hdr.pack_propagate(False)
    tk.Label(log_hdr, text="Output Log", bg=CLR["panel"],
             fg=CLR["subtext"], font=("Segoe UI", 9)).pack(
        side=tk.LEFT, padx=12, pady=8)

    log_text = tk.Text(
        log_pane, wrap=tk.WORD,
        font=("Consolas", 9),
        bg=CLR["log_bg"], fg=CLR["log_fg"],
        insertbackground=CLR["text"],
        selectbackground=CLR["accent"],
        relief="flat", borderwidth=0,
        state=tk.DISABLED, padx=10, pady=8,
    )
    log_sb = ttk.Scrollbar(log_pane, command=log_text.yview)
    log_text.configure(yscrollcommand=log_sb.set)
    log_sb.pack(side=tk.RIGHT, fill=tk.Y)
    log_text.pack(fill=tk.BOTH, expand=True)

    log_text.tag_configure("head", foreground=CLR["accent"],
                                   font=("Consolas", 9, "bold"))
    log_text.tag_configure("ok",   foreground=CLR["log_ok"])
    log_text.tag_configure("err",  foreground=CLR["log_err"])
    log_text.tag_configure("warn", foreground=CLR["log_warn"])
    log_text.tag_configure("cmd",  foreground=CLR["blue"],
                                   font=("Consolas", 9, "bold"))
    log_text.tag_configure("dim",  foreground=CLR["subtext"])

    # preflight emits two multi-line banners, each `HEADER / rule / body / rule`
    # (header FIRST, so no leading rule steals the "head" style).  Both must
    # keep one color throughout: their bodies are install/build instructions
    # that would otherwise classify as plain text and lose the visual grouping.
    # State is (tag, rules_seen); the banner closes on its second rule.
    #   MISSING DEPENDENCY … / "=" rules  -> err   (run aborted)
    #   WARNING: Python version / "-" rules -> warn (run continues)
    _banner = [None]

    # A run's failure verdicts, in the words each producer actually prints.
    # Matched on the lower-cased line so every one of them is red regardless of
    # what else the sentence contains.
    #
    # NOTE these are VERDICTS, never bare words.  A plain "failed" substring is
    # wrong: the compile phase prints "154/154 succeeded, 0 failed", and a
    # COUNT of failures -- especially a count of zero -- is not a failure.  This
    # is the same trap the "errors" not in l guard below already exists for.
    _FAIL_MARKERS = (
        "completed with errors",
        "traceback (most recent call last)",
        "stopped - missing dependency",
        "fatal error",
        "fatal:",
    )
    # "N failed" / "N failures" / "N errors": a count line.  Red only when the
    # number is non-zero, neutral when it is zero, so a clean run's tally never
    # reads as a failure.
    # "12 more failures" / "4 compile errors" are still counts, so allow a
    # couple of adjectives between the number and the noun.
    _COUNT_RE = re.compile(
        r"(?<![\w.])(\d+)\s+(?:\w+\s+){0,2}?(?:failed|failures?|errors?)(?![\w])")

    def _count_verdict(l: str):
        """'err' if a count line reports >0, 'ok' if it reports 0, else None."""
        hits = _COUNT_RE.findall(l)
        if not hits:
            return None
        return "err" if any(int(n) for n in hits) else "ok"
    # Stages that ran fine and had nothing to do.  Informational, never a
    # failure -- these exit 0.
    _NOTHING_MARKERS = (
        "nothing to generate",
        "nothing to do",
        "nothing to convert",
        "nothing to pack",
        "nothing to patch",
        "nothing to audit",
        "no work to do",
    )

    # A standalone FAILED verdict: the runners' "  FAILED (exit 1)" and the
    # summary's "- import: FAILED for Oblivion.esm".  Anchored so it cannot fire
    # on a tally like "0 failed" -- a count is always PRECEDED by its number,
    # which this refuses to match.
    # Trailing forms: "FAILED", "FAILED (exit 1)", "FAILED for X", "FAILED: X",
    # "FAILED/skipped", and "<thing> failed." mid-sentence.  Anchored on what
    # PRECEDES `failed` too, so a tally ("0 failed") -- always preceded by its
    # number -- can never reach it.
    _FAILED_RE = re.compile(
        r"(?:^|[:\-]\s*|\s)fail(?:ed|ure)"
        r"(?:\s*[(:/,.;]|\s+for\b|\s+-\s|\s*$)")

    # Recoverable: the stage says so in the same breath as the failure and then
    # carries on, exiting 0.  These must NOT be red -- a red line the user
    # cannot act on trains them to ignore red.  Checked before the hard-failure
    # rule, so "download failed; generating normally" is orange, not red.
    _RECOVERED_RE = re.compile(
        r"falling back|fall back|generating normally|will be (?:copied|skipped)"
        r"|is allowed to continue|continuing|ignored|retrying|using .* instead")

    # Deliberately-skipped work.  Deliberately narrow: bare "skip" also shows up
    # in tallies ("ok=5 skip=2 fail=0") and in routine per-record chatter, which
    # must stay neutral or every run turns orange.  Only a sentence that leads
    # with the skip, or explicitly says it is skipping something, counts.
    _SKIP_RE = re.compile(
        r"^\s*(?:\[[^\]]+\]\s*)?skipp?(?:ing|ed)\b"
        r"|\bskipping\s+(?:the\s+)?[\w.\-]+\s+(?:generation|phase|stage|step)"
        r"|[:\-;]\s*skipp?(?:ing|ed)\b"
        r"|,\s*skipping\b|\bnot\s+overlaid\b")

    def _is_recovered_line(l: str) -> bool:
        return bool(_RECOVERED_RE.search(l))

    def _is_failure_line(l: str) -> bool:
        if any(m in l for m in _FAIL_MARKERS):
            return True
        return bool(_FAILED_RE.search(l)) and _count_verdict(l) != "ok"

    def _is_nothing_line(l: str) -> bool:
        return any(m in l for m in _NOTHING_MARKERS)

    # Every error line seen since the current run started, in order, so the end
    # of a failed run can restate them.  The log scrollback is thousands of
    # lines long and the user should never have to hunt upward through it to
    # find out WHAT failed.  Capped so a stage that fails per-file (thousands of
    # meshes) cannot flood the summary or the memory holding it.
    _run_errors: list = []
    _ERR_SUMMARY_CAP = 40

    def _reset_run_errors():
        _run_errors.clear()

    def _record_error(line: str, tag: str):
        """Remember an error line for the end-of-run summary.

        Called from _log, so it sees output from BOTH runners (the single
        convert.py invocation and the per-step loop) and from every child
        process, without either having to opt in.
        """
        if tag != "err":
            return
        text = line.strip()
        if not text:
            return
        # The final verdict lines are printed by the summary itself; recording
        # them would echo "FAILED" back inside its own error list.
        if text.strip(" -") in ("FAILED", "DONE", "CANCELLED"):
            return
        low = text.lower()
        if low.startswith("failed (exit "):
            return
        # convert.py's own verdict, and the summary block it now prints under
        # it, are restatements of the failure -- not additional errors.  Left
        # in, every run's summary opened with "Pipeline completed with errors."
        # summarising itself.
        if low.startswith(("pipeline completed with errors",
                           "error summary", "stopped - missing dependency")):
            return
        if text.strip("- ") == "" or set(text) == {"-"}:
            return
        if len(_run_errors) < _ERR_SUMMARY_CAP + 1:
            _run_errors.append(text)

    def _classify(line: str) -> str:
        l = line.lower()
        stripped = line.strip()
        is_rule = bool(stripped) and set(stripped) in ({"="}, {"-"})
        if _banner[0] is not None:
            tag, seen = _banner[0]
            if is_rule:
                seen += 1
                _banner[0] = None if seen >= 2 else (tag, seen)
            return tag
        # Match each banner's own header line only — the one-line "STOPPED -
        # MISSING DEPENDENCY" summary the GUI prints afterwards also says
        # "missing dependency" and must not re-open the banner.
        if l.startswith("missing dependency"):
            _banner[0] = ("err", 0)
            return "err"
        if l.startswith("warning: python version"):
            _banner[0] = ("warn", 0)
            return "warn"
        if "missing dependency" in l:
            return "err"
        if line.startswith("===") or "phase" in l[:20]:
            return "head"
        # Failure verdicts come FIRST, before the generic "complete"/"error"
        # rules below.  "Pipeline completed with errors." used to fall past the
        # err rule (guarded on "errors" not in l, so plural error counts like
        # "0 errors" stay uncolored) and land on the "complete" rule -- the
        # single most important line in the run was painted GREEN.  A line that
        # states the run failed is red no matter what other words it carries.
        # A tally ("154/154 succeeded, 0 failed", "3 errors") is judged by its
        # NUMBER, before any word-based rule can see the word "failed" in it.
        _cnt = _count_verdict(l)
        if _cnt is not None:
            return "err" if _cnt == "err" else (
                "ok" if ("succeeded" in l or "success" in l) else None)
        # A failure the stage RECOVERED from ("download failed; generating
        # normally") is orange, not red -- it is checked first so the hard-fail
        # rule below cannot claim it.  Red is reserved for what actually broke
        # the run; coloring recoverable notices red trains the user to ignore
        # red altogether.
        if _is_recovered_line(l):
            return "warn"
        if _is_failure_line(l):
            return "err"
        # "Nothing to generate" is not an error: the stage ran, found no work,
        # and exited 0.  Orange, so it reads as "look at this" without being
        # mistaken for a failure.
        if _is_nothing_line(l):
            return "warn"
        # Deliberately-skipped work is informational, never a failure.
        if _SKIP_RE.search(l):
            return "warn"
        if "error" in l and "errors" not in l:
            return "err"
        if "warning" in l or "warn" in l:
            return "warn"
        # Compare LOWER-cased: the runners' own verdict line is "  DONE", which
        # never matched the lower-case-only literals here and so printed
        # uncolored while its FAILED counterpart is red.
        if l.strip() in ("done", "ok") or "complete" in l or "success" in l:
            return "ok"
        # `cmd` is the command echo ONLY.  This used to also return "cmd" for
        # any line starting "[", but a census of the pipeline's bracketed
        # prefixes finds only stage/plugin tags -- [{file_name}], [LOD],
        # [TerrainLOD], [skin_replacement] -- and no bracketed timestamps at
        # all.  So that branch styled ordinary stage output as a command and, by
        # claiming the line, denied it the warn/err styling it had earned.
        if line.startswith("Running:"):
            return "cmd"
        return None

    # -- Run log ---------------------------------------------------------------
    # The scrollback used to be the ONLY copy of a run's output: closing the
    # window, or starting the next run (which clears the widget), destroyed the
    # record of the run the user had just played in game.  `_log` is the single
    # funnel every line already passes through -- both runners and every child
    # process -- so mirroring it here also captures the GUI's OWN lines (the
    # header, the ERROR SUMMARY), which are in no child's stdout.
    #
    # One writer only.  A GUI run is usually several convert.py processes, so
    # the children are told a log already exists (TESCONV_RUN_LOG) and write
    # nothing; two processes appending to one file would interleave and, on
    # Windows, corrupt it.
    _run_log = [None]

    def _run_log_begin(header: dict):
        """Rotate and open this run's log.  Never fails a run."""
        _run_log_end()
        try:
            cfg = load_config()
        except Exception:
            cfg = {}
        try:
            keep = run_log.runs_kept(cfg)
            if keep <= 0:
                return
            logs_dir = SCRIPT_DIR / "logs"
            if not run_log.rotate(logs_dir, keep):
                return
            full = {"Version": version_info.current_version()}
            full.update(header)
            log = run_log.RunLog(run_log.log_path(logs_dir, 1), full)
            if log.active:
                _run_log[0] = log
        except Exception:
            _run_log[0] = None

    def _run_log_end(status: str = None):
        log = _run_log[0]
        _run_log[0] = None
        if log is None:
            return
        try:
            log.close(status)
        except Exception:
            pass

    def _run_log_env() -> dict:
        """Tell child processes a run log already exists, so they don't rotate."""
        log = _run_log[0]
        return {run_log.RUN_LOG_ENV_VAR: str(log.path)} if log is not None else {}

    def _run_log_note():
        """Log where this run is being recorded, and how big the last one was."""
        log = _run_log[0]
        if log is None:
            return
        try:
            shown = str(log.path.relative_to(SCRIPT_DIR))
        except ValueError:
            shown = str(log.path)
        _log(f"Log: {shown}")

    def _run_log_size_note():
        """Report the finished log's size, so a runaway file is visible."""
        log = _run_log[0]
        if log is None:
            return
        try:
            size = log.path.stat().st_size
        except OSError:
            return
        try:
            shown = str(log.path.relative_to(SCRIPT_DIR))
        except ValueError:
            shown = str(log.path)
        _log(f"Log saved: {shown} ({run_log.format_size(size)})")

    def _log(line: str):
        log_text.configure(state=tk.NORMAL)
        tag = _classify(line)
        _record_error(line, tag)
        if tag:
            log_text.insert(tk.END, line + "\n", tag)
        else:
            log_text.insert(tk.END, line + "\n")
        log_text.see(tk.END)
        log_text.configure(state=tk.DISABLED)
        log = _run_log[0]
        if log is not None:
            try:
                log.write_line(line)
            except Exception:
                pass

    def _log_error_summary():
        """Print the collected errors under the FAILED verdict.

        Runs on the UI thread after the queue has drained, so it lands at the
        very bottom of the log with every error already recorded.  A failed run
        with no captured error lines still prints a line saying so, rather than
        leaving a bare FAILED with no explanation.
        """
        _log("")
        _log("-" * 54)
        if not _run_errors:
            _log("  ERROR SUMMARY: no error lines were captured -- check the "
                 "stage output above for the failure.")
            _log("-" * 54)
            return
        shown = _run_errors[:_ERR_SUMMARY_CAP]
        extra = len(_run_errors) - len(shown)
        _log(f"  ERROR SUMMARY ({len(_run_errors)}"
             f"{'+' if extra else ''} error line"
             f"{'' if len(_run_errors) == 1 else 's'}):")
        for text in shown:
            _log(f"    - {text}")
        if extra:
            _log(f"    ... and {extra} more (see the log above)")
        _log("-" * 54)

    def _clear_log():
        log_text.configure(state=tk.NORMAL)
        log_text.delete("1.0", tk.END)
        log_text.configure(state=tk.DISABLED)

    # ── Mod archive import ────────────────────────────────────────────────────
    # Entry points: Mods > Import..., and dropping an archive on the sidebar.
    # Both land here.

    def _begin_import(path: str):
        """Inspect `path` on a worker thread, then show the confirm dialog.

        Inspection is threaded because a 400 MB archive takes real time to list
        and the UI thread must not freeze while it does. NOTHING is written
        until the user confirms.
        """
        if running.is_set():
            _info("Busy", "A conversion is running. Wait for it to finish "
                          "before importing a mod.")
            return

        status_var.set(f"Reading {os.path.basename(path)}...")
        result = {}

        def _work():
            try:
                from asset_convert import mod_ingest
                result["manifest"] = mod_ingest.inspect(path)
            except Exception as exc:            # IngestError and anything else
                result["error"] = exc

        def _done(thread):
            if thread.is_alive():
                root.after(80, lambda: _done(thread))
                return
            status_var.set("Ready")
            if "error" in result:
                _info("Cannot Import",
                      f"{os.path.basename(path)}\n\n{result['error']}")
                return
            _confirm_import(path, result["manifest"])

        th = threading.Thread(target=_work, daemon=True)
        th.start()
        root.after(80, lambda: _done(th))

    def _confirm_import(path, manifest):
        """Show what was found and let the user choose plugins, then ingest."""
        card = tk.Frame(outer, bg=CLR["panel"],
                        highlightbackground=CLR["border"], highlightthickness=1)

        def _close():
            card.grab_release()
            card.destroy()

        tk.Label(card, text="Import Mod", bg=CLR["panel"], fg=CLR["text"],
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=16,
                                                     pady=(14, 0))
        ttk.Separator(card, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16,
                                                       pady=8)

        layout = (f"Data folder: {manifest.payload_root}"
                  if manifest.payload_root else "Payload at archive root")
        info = [os.path.basename(str(manifest.path)), layout,
                manifest.summary()]
        if manifest.bsas:
            info.append(f"{len(manifest.bsas)} BSA(s) will be extracted")
        if manifest.nested:
            info.append(f"{len(manifest.nested)} nested archive(s)")
        if manifest.ambiguous_data:
            info.append("NOTE: several equally-shallow Data folders; "
                        f"using {manifest.payload_root}")
        tk.Label(card, text="\n".join(info), bg=CLR["panel"],
                 fg=CLR["subtext"], font=("Segoe UI", 9), justify=tk.LEFT,
                 anchor="w", wraplength=420).pack(anchor="w", padx=16)

        # Which plugins to register. Defaults to all -- both TWMP archives ship
        # two, and taking only one silently loses half the mod.
        # An asset-only mod (texture/mesh pack) has none: say so rather than
        # showing an empty "Plugins" heading.
        picks = []
        if manifest.plugins:
            tk.Label(card, text="Plugins", bg=CLR["panel"], fg=CLR["text"],
                     font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=16,
                                                        pady=(10, 2))
            for rel in manifest.plugins:
                var = tk.BooleanVar(value=True)
                picks.append((rel, var))
                tk.Checkbutton(card, text=os.path.basename(rel), variable=var,
                               bg=CLR["panel"], fg=CLR["text"],
                               selectcolor=CLR["btn"],
                               activebackground=CLR["panel"],
                               activeforeground=CLR["text"],
                               font=("Segoe UI", 9), anchor="w",
                               highlightthickness=0, borderwidth=0).pack(
                    anchor="w", padx=24)
        else:
            tk.Label(card,
                     text="No plugin — assets only.\n"
                          "Export, Import, Scripts and Creatures will be "
                          "unavailable for this mod.",
                     bg=CLR["panel"], fg=CLR["subtext"],
                     font=("Segoe UI", 9), justify=tk.LEFT, anchor="w",
                     wraplength=420).pack(anchor="w", padx=16, pady=(10, 0))

        # Masters that have no export yet. This is the project's classic silent
        # failure -- a mod whose master was never converted imports "fine" and
        # resolves every master-owned record to nothing.
        missing = _missing_masters_for(manifest)
        if missing:
            tk.Label(card,
                     text="Missing masters — convert these FIRST:\n  "
                          + "\n  ".join(sorted(missing)),
                     bg=CLR["panel"], fg=CLR["red"], font=("Segoe UI", 9),
                     justify=tk.LEFT, anchor="w", wraplength=420).pack(
                anchor="w", padx=16, pady=(10, 0))

        keep_var = tk.BooleanVar(value=True)
        if not manifest.is_folder:
            try:
                size_mb = manifest.path.stat().st_size / 1024 ** 2
            except OSError:
                size_mb = 0
            tk.Checkbutton(
                card,
                text=f"Keep a copy of the archive ({size_mb:.0f} MB) so steps "
                     f"can be re-run later",
                variable=keep_var, bg=CLR["panel"], fg=CLR["subtext"],
                selectcolor=CLR["btn"], activebackground=CLR["panel"],
                activeforeground=CLR["text"], font=("Segoe UI", 9),
                anchor="w", highlightthickness=0, borderwidth=0,
                wraplength=400, justify=tk.LEFT).pack(anchor="w", padx=16,
                                                      pady=(10, 0))

        btns = ttk.Frame(card, style="Panel.TFrame")
        btns.pack(anchor="e", padx=16, pady=(14, 14))

        def _go():
            chosen = [rel for rel, var in picks if var.get()]
            keep = bool(keep_var.get())
            _close()
            # Only a mod that HAS plugins can have none selected. An
            # asset-only mod legitimately passes an empty list.
            if manifest.plugins and not chosen:
                _info("Import Mod", "No plugins selected.")
                return
            _run_import(manifest.path, manifest, chosen or None, keep)

        ttk.Button(btns, text="Cancel", command=_close).pack(side=tk.RIGHT,
                                                             padx=(6, 0))
        ttk.Button(btns, text="Import", style="Accent.TButton",
                   command=_go).pack(side=tk.RIGHT)

        card.place(relx=0.5, rely=0.5, anchor="center")
        card.grab_set()

    def _plugin_esm(out_root, plugin: str):
        """The converted plugin file, wherever its mod's folder is."""
        try:
            from output_layout import plugin_esm
            return plugin_esm(out_root, plugin, EXPORT_DIR)
        except ImportError:
            return Path(out_root) / plugin / plugin   # noqa: plugin-path (no-registry fallback)

    def _master_export_present(master: str) -> bool:
        """True when `master`'s exported records exist under EXPORT_DIR."""
        try:
            from output_layout import record_dir
            return record_dir(EXPORT_DIR, master).is_dir()
        except ImportError:
            return (EXPORT_DIR / master).is_dir()   # noqa: plugin-path (no-registry fallback)

    def _missing_masters_for(manifest):
        """Masters of the archive's plugins with no export records yet.

        Read straight out of the archive, so the warning appears BEFORE the
        import rather than after a failed conversion.

        Resolved through `record_dir`, never by joining the name onto export/:
        an imported mod's plugins live inside their mod's shared folder, so a
        master that IS converted reads as missing under the plain join and the
        dialog tells the user to convert something they already have.
        """
        try:
            import tempfile

            from asset_convert import archive as _archive
            from convert import get_masters_from_binary
        except Exception:
            return set()

        missing = set()
        with tempfile.TemporaryDirectory(prefix="tesconv_mast_") as tmp:
            for rel in manifest.plugins:
                try:
                    src = (manifest.path / manifest.payload_root / rel
                           if manifest.is_folder else None)
                    if src is not None:
                        target = src
                    else:
                        member = (f"{manifest.payload_root}/{rel}"
                                  if manifest.payload_root else rel)
                        target = _archive.extract_one(
                            manifest.path, member,
                            os.path.join(tmp, os.path.basename(rel)))
                    for master in get_masters_from_binary(str(target)):
                        if not _master_export_present(master):
                            missing.add(master)
                except Exception:
                    continue
        return missing

    def _run_import(path, manifest, chosen, keep_archive):
        """Do the ingest on a worker thread, streaming progress to the log."""
        _clear_log()
        _log(f"Importing {os.path.basename(str(path))}")
        _set_running(True)
        _start_timer()
        q = queue.Queue()
        outcome = {}

        def _work():
            try:
                from asset_convert import mod_ingest
                outcome["results"] = mod_ingest.ingest(
                    path, EXPORT_DIR, plugin_members=chosen,
                    keep_archive=keep_archive, manifest=manifest,
                    log=q.put)
            except Exception as exc:
                outcome["error"] = exc

        def _drain(thread):
            try:
                while True:
                    _log(q.get_nowait())
            except queue.Empty:
                pass
            if thread.is_alive():
                root.after(60, lambda: _drain(thread))
                return
            _set_running(False)
            _stop_timer()
            if "error" in outcome:
                _log(f"  FAILED: {outcome['error']}")
                _info("Import Failed", str(outcome["error"]))
                return
            names = sorted(outcome.get("results") or {})
            _log(f"  Imported: {', '.join(names)}")
            # Switch the plugin selector to the mod just imported -- that is
            # what the user wants to convert next.
            try:
                from asset_convert import source_registry
                entry = source_registry.get(EXPORT_DIR, names[0]) if names else None
                # Source ids are prefixed ("mod:<group_id>"); passing the bare
                # group_id silently fails to match and leaves the old source
                # selected, so the user has to switch by hand.
                gid = entry.get("group_id") if entry else None
                _refresh_scopes(select=f"mod:{gid}" if gid else None)
                _apply_scope(select_plugin=names[0] if names else None)
            except Exception:
                _refresh_scopes()
                _apply_scope()
            _apply_step_availability()
            _refresh_upgrade_notice()

        th = threading.Thread(target=_work, daemon=True)
        th.start()
        root.after(60, lambda: _drain(th))

    def _manage_mods():
        """List imported mods, with a Remove action for each."""
        try:
            from asset_convert import source_registry
            groups = source_registry.groups(EXPORT_DIR)
        except Exception as exc:
            _info("Imported Mods", f"Could not read the mod registry:\n\n{exc}")
            return

        if not groups:
            _info("Imported Mods",
                  "No mods imported yet.\n\n"
                  "Use Mods > Import Mod Archive…, or drag an archive onto "
                  "the left panel.")
            return

        card = tk.Frame(outer, bg=CLR["panel"],
                        highlightbackground=CLR["border"], highlightthickness=1)

        def _close():
            card.grab_release()
            card.destroy()

        tk.Label(card, text="Imported Mods", bg=CLR["panel"], fg=CLR["text"],
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=16,
                                                     pady=(14, 0))
        ttk.Separator(card, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=16,
                                                       pady=8)

        body = ttk.Frame(card, style="Panel.TFrame")
        body.pack(fill=tk.BOTH, padx=16)

        def _remove(plug_names):
            if not _confirm(
                    "Remove Imported Mod",
                    "Delete the imported copy of:\n\n  "
                    + "\n  ".join(plug_names)
                    + "\n\nThis removes their export folders. The original "
                      "archive on disk is not touched.",
                    yes="Remove", no="Cancel"):
                return
            from asset_convert import mod_ingest
            for name in plug_names:
                try:
                    mod_ingest.remove(name, EXPORT_DIR)
                except Exception as exc:
                    _log(f"  Could not remove {name}: {exc}")
            _close()
            _refresh_scopes()
            _apply_scope()
            _manage_mods()

        for _gid, label, plugs in groups:
            row = ttk.Frame(body, style="Panel.TFrame")
            row.pack(fill=tk.X, pady=3)
            tk.Label(row, text=f"{label}\n  " + "\n  ".join(plugs),
                     bg=CLR["panel"], fg=CLR["subtext"],
                     font=("Segoe UI", 9), justify=tk.LEFT,
                     anchor="w").pack(side=tk.LEFT, fill=tk.X, expand=True)
            ttk.Button(row, text="Remove", style="Danger.TButton",
                       command=lambda p=list(plugs): _remove(p)).pack(
                side=tk.RIGHT, padx=(8, 0))

        ttk.Button(card, text="Close", command=_close).pack(anchor="e",
                                                            padx=16,
                                                            pady=(14, 14))
        card.place(relx=0.5, rely=0.5, anchor="center")
        card.grab_set()

    _timer_job = [None]
    _timer_start = [0.0]

    def _tick_timer():
        elapsed = time.monotonic() - _timer_start[0]
        m, s = divmod(int(elapsed), 60)
        h, m = divmod(m, 60)
        timer_var.set(f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}")
        _timer_job[0] = root.after(1000, _tick_timer)

    def _start_timer():
        _timer_start[0] = time.monotonic()
        _tick_timer()

    def _stop_timer():
        if _timer_job[0] is not None:
            root.after_cancel(_timer_job[0])
            _timer_job[0] = None

    def _set_running(state: bool):
        running.set() if state else running.clear()
        if not state:
            cancel_evt.clear()
        run_btn.configure(state="disabled" if state else "normal")
        cancel_btn.configure(state="normal" if state else "disabled",
                             text="Cancel")
        file_combo.configure(state="disabled" if state else "normal")
        if state:
            # Must match the original pack exactly — side=BOTTOM (or the bar
            # re-appears at the TOP of the sidebar on the first run) and the
            # pad on the bottom side (or it butts against "Ready").
            prog_bar.pack(side=tk.BOTTOM, fill=tk.X, padx=14, pady=(0, 6))
            prog_bar.start(12)
            status_var.set("Running...")
            _start_timer()
        else:
            prog_bar.stop()
            prog_bar.pack_forget()
            status_var.set("Ready")
            _stop_timer()
            # The run just rewrote the conversion state, so what is still
            # outstanding has changed.  Recompute, but do NOT re-tick the
            # boxes: the user is looking at the selection they just ran.
            _refresh_upgrade_notice(auto_apply=False)
            # A conversion that just added a plugin changes what the global
            # actions would produce, so a previously-done one goes live again.
            _refresh_global_btns()
        _update_run_btn()

    def _cancel_clicked():
        if running.is_set():
            cancel_evt.set()
            status_var.set("Cancelling...")
            cancel_btn.configure(state="disabled", text="Cancelling...")
            _log("")
            _log("  Cancelling — killing running processes...")

    # ── Run logic ─────────────────────────────────────────────────────────────
    def _winding_flag() -> str:
        """The explicit collision-winding flag matching the setting."""
        return ("--collision-winding-fix" if _winding_on()
                else "--no-collision-winding-fix")

    def _build_cmd(step_key: str, fname: str, out_dir: str,
                   selected_subdirs=None) -> list:
        """Build the convert.py command for a single step."""
        _, flag, _, _, _, needs_file = next(
            s for s in STEPS if s[0] == step_key)
        cmd = [sys.executable, "-u", str(SCRIPT_DIR / "convert.py"), flag]
        if needs_file and fname:
            cmd += ["-f", fname]
        if out_dir:
            cmd += ["--output-dir", out_dir]
        if step_key == "meshes":
            if selected_subdirs:
                cmd += ["--mesh-subdirs"] + selected_subdirs
            # Always explicit: the setting is the user's answer, whether it
            # resolved from Automatic or from them pinning it on or off.
            cmd.append(_winding_flag())
            if parallax_var.get():
                cmd.append("--parallax")
                if tex_only_var.get():
                    cmd.append("--textures-only")
        return cmd

    # ── Global actions ────────────────────────────────────────────────────────
    def _global_cmd(key: str, out_dir: str) -> list:
        """The argv for one global action."""
        if key == "package_start_mod":
            cmd = [sys.executable, "-u",
                   str(SCRIPT_DIR / "tools" / "package_start_mod.py")]
            if out_dir:
                cmd += ["--output-dir", out_dir]
            return cmd

        if key == "pack_lod":
            cmd = [sys.executable, "-u",
                   str(SCRIPT_DIR / "tools" / "pack_lod.py")]
            if out_dir:
                cmd += ["--output-dir", out_dir]
            return cmd

        if key == "make_master":
            cmd = [sys.executable, "-u",
                   str(SCRIPT_DIR / "tools" / "make_master.py")]
            cmd += master_plugins or _default_master_plugins()
            if out_dir:
                cmd += ["--output-dir", out_dir]
            return cmd

        if key == "create_lod":
            cmd = [sys.executable, "-u",
                   str(SCRIPT_DIR / "tools" / "create_lod.py")]
            if out_dir:
                cmd += ["--output-dir", out_dir]
            # Always explicit once the dialog has been confirmed: the ORDER is
            # the conflict resolution, so the run must apply exactly what the
            # user saw. Before that, the tool derives both lists itself, which
            # keeps a menu-less run correct as their load order changes.
            if lod_plugins:
                cmd += ["--plugins"] + lod_plugins
            if lod_worldspaces:
                cmd += ["--worldspaces"] + lod_worldspaces
            return cmd

        cmd = [sys.executable, "-u", str(SCRIPT_DIR / "convert.py"),
               "--modify-body-meshes"]
        if out_dir:
            cmd += ["--output-dir", out_dir]
        chosen = [n for n, v in patch_plugin_vars if v.get()]
        if chosen and chosen != [n for n, _ in patch_plugin_vars]:
            cmd += ["--patch-plugins"] + chosen
        return cmd

    def _global_is_current(key: str) -> bool:
        """Has this action run, and is its result still up to date?

        "Done" is not permanent. These actions consume the set of things
        CONVERTED SO FAR, so converting another plugin (or editing the Skyrim
        load order) can invalidate a result that was correct when it was
        produced — a merged LOD folder built before ElsweyrAnequina existed
        knows nothing about ElsweyrAnequina's tiles. So the stamp is compared
        against the newest input rather than merely existing.

        The ARTEFACT is the evidence, not the run record. An action whose output
        is already on disk is done however it got there — packaged by an earlier
        session, by the CLI tool directly, or by a teammate's copy of output/ —
        and demanding a locally-recorded run marked finished work as outstanding.
        """
        art = _global_artifact(key)
        if art is not None and not art.exists():
            return False
        try:
            stamp = _global_stamp(key)
        except Exception:
            return False
        if key == "make_master":
            # No artefact to point at, but the flags themselves are the
            # evidence: done exactly when every selected plugin reads back as
            # ESM, however it got that way (this session, an earlier one, or
            # the CLI tool). A rebuilt plugin comes back unflagged and re-lights
            # the button on its own.
            done = bool(stamp) and all(
                part.endswith(":1") for part in stamp.split("\x1f"))
            if done:
                _last_global_stamp[key] = stamp
            return done
        if _last_global_stamp.get(key) == stamp:
            return True
        # No stamp from this session: fall back to the recorded run, which is
        # all there is for an action whose output we cannot point at.
        if art is None:
            return False
        # The artefact exists AND its inputs are unchanged since it was made.
        # Adopt the stamp so later input changes are still detected.
        _last_global_stamp[key] = stamp
        return True

    def _global_artifact(key: str):
        """The file this action produces, when it has a single obvious one.

        None means "no artefact to test" — the result is spread over a folder
        (merged LOD) or lives outside output/, so only the run record can speak
        for it.
        """
        out_dir = output_var.get().strip() or str(SCRIPT_DIR / "output")
        # FINISHED_DIR_NAME, not finished_dir(): this only ASKS whether the
        # artefact is there, and the helper would create the folder as a side
        # effect — opening the GUI would leave an empty "Finished Mods"
        # promising deliverables nothing has produced.
        from output_layout import FINISHED_DIR_NAME
        finished = Path(out_dir) / FINISHED_DIR_NAME
        if key == "package_start_mod":
            return finished / "TESGameSelect.zip"
        if key == "modify_body_meshes":
            return finished / "Slot44 Patch.esp"
        if key == "pack_lod":
            from asset_convert.sibling_lod import LOD_DIR_NAME
            return finished / f"{LOD_DIR_NAME}.zip"
        # make_master produces no file — it flips a bit inside plugins that
        # already exist. Its stamp reads the flags directly instead.
        return None

    def _global_stamp(key: str) -> str:
        """A fingerprint of everything `key`'s result depends on.

        Changing any input changes the stamp, which un-greys the button. For
        both actions that means the set of converted plugins; "Patch Skyrim"
        additionally depends on the Skyrim load order it patches.
        """
        out_dir = output_var.get().strip() or str(SCRIPT_DIR / "output")

        if key == "package_start_mod":
            # Depends on the COMMITTED starter mod, not on anything converted:
            # keying it to the plugin set would re-light it every time an
            # unrelated conversion finished, when the zip is still correct.
            # Name+size+mtime of each dist file is enough to notice a pull that
            # updated the prebuilt mod.
            dist = SCRIPT_DIR / "TESGameSelect" / "dist"
            try:
                src = sorted(
                    f"{p.relative_to(dist).as_posix()}:{st.st_size}:"
                    f"{int(st.st_mtime)}"
                    for p in dist.rglob('*') if p.is_file()
                    for st in (p.stat(),))
            except OSError:
                src = []
            # Whether the zip EXISTS is not folded in here: _global_is_current
            # already tests the artefact directly, so this only has to describe
            # the inputs that would make an existing zip stale.
            return "\x1f".join(src)

        if key == "pack_lod":
            # Depends on the BAKED LOD folder, not on the converted plugin set:
            # the zip is a copy of that folder, so it goes stale exactly when
            # Create LOD rewrites a tile and at no other time. Converting an
            # unrelated plugin re-lights Create LOD, and packing follows only
            # once that has actually rebaked something.
            #
            # Size+mtime per file, not content: the folder is thousands of
            # tiles and hashing them would cost more than the zip itself.
            from asset_convert.sibling_lod import LOD_DIR_NAME
            lod_dir = Path(out_dir) / LOD_DIR_NAME
            try:
                src = sorted(
                    f"{p.relative_to(lod_dir).as_posix()}:{st.st_size}:"
                    f"{int(st.st_mtime)}"
                    for p in lod_dir.rglob('*') if p.is_file()
                    for st in (p.stat(),))
            except OSError:
                src = []
            return "\x1f".join(src)

        if key == "make_master":
            # The ESM flag IS the result, so the stamp reads it back off disk.
            # The button greys out once every selected plugin is flagged and
            # lights up again the moment one is not -- including a rebuild,
            # which writes a fresh unflagged plugin over a flagged one.
            names = master_plugins or _default_master_plugins()
            out = []
            for n in names:
                try:
                    sys.path.insert(0, str(SCRIPT_DIR / "tools"))
                    from make_master import read_header, resolve, FLAG_ESM
                    flags, _m = read_header(resolve(n, out_dir))
                    out.append(f"{n}:{1 if flags & FLAG_ESM else 0}")
                except Exception:
                    out.append(f"{n}:?")
            return "\x1f".join(out)

        parts = []
        try:
            from asset_convert.sibling_lod import converted_plugins
            parts += sorted(converted_plugins(Path(out_dir)))
        except Exception:
            pass
        if key == "modify_body_meshes":
            parts += ["|"] + sorted(n for n, v in patch_plugin_vars if v.get())
        if key == "create_lod":
            # The ORDER is an input, not a presentation detail: it decides
            # which plugin wins a contested tile, so re-ordering invalidates a
            # run exactly as much as converting another plugin does. Falls back
            # to the derived defaults so an edit to plugins.txt counts too, and
            # the worldspace filter is folded in for the same reason — a run
            # that skipped SEWorld is not current for one that wants it.
            names = lod_plugins or _default_lod_plugins()
            parts += ["|"] + names
            # The worldspace filter is an input too, but ONLY the user's
            # explicit pick is cheap to read. Falling back to the derived
            # default here used to call _default_lod_worldspaces, which now
            # parses every converted ESM (Oblivion.esm alone is 613 MB) —
            # 0.01s -> 1.63s on a path that runs for every global button
            # BEFORE the window first paints, which is the blank-screen
            # startup stall.
            #
            # A stamp only has to CHANGE when the inputs change, never to
            # contain them. When the user has not pinned a worldspace set, the
            # derived set is a pure function of the converted ESMs, so their
            # identity (mtime+size) invalidates exactly as precisely at a
            # fraction of the cost.
            if lod_worldspaces:
                parts += ["|"] + list(lod_worldspaces)
            else:
                out_root = Path(out_dir)
                sig = []
                for n in names:
                    try:
                        st = _plugin_esm(out_root, n).stat()
                        sig.append(f"{n}:{st.st_size}:{st.st_mtime_ns}")
                    except OSError:
                        sig.append(f"{n}:-")
                parts += ["|"] + sig
        return "\x1f".join(parts)

    # The stamp each action was last completed at, so a later conversion can
    # un-grey it. Session-scoped: on a fresh start the buttons show as done if
    # the step was ever recorded, and go live again the moment an input moves.
    _last_global_stamp: dict[str, str] = {}

    def _refresh_global_btns():
        """Grey out the actions whose result is current; light up the rest."""
        for gkey, _glabel, _tip, gshort, _grow in GLOBAL_ACTIONS:
            btn = global_btns.get(gkey)
            if btn is None:
                continue
            if _global_is_current(gkey):
                btn.configure(text=f"✓ {gshort}", style="GlobalDone.TButton")
            else:
                btn.configure(text=gshort, style="Global.TButton")

    def _run_global_action(key: str):
        """Run one global action in a worker thread, logging to the main pane.

        "Create LOD" opens its selection dialog first and starts only when the
        user presses Generate — the plugin ORDER and the worldspace set decide
        what gets built and which plugin wins a contested tile, so it is a
        decision to confirm, not a default to fire off.
        """
        if running.is_set():
            return
        if key == "create_lod":
            _open_create_lod_panel(
                on_generate=lambda _p, _w: _start_global_action(key))
            return
        if key == "make_master":
            # Confirm first: this rewrites plugins in place and changes how
            # every reference in them is loaded, so the set is a decision to
            # confirm rather than a default to fire off.
            _open_make_master_panel(
                on_apply=lambda: _start_global_action(key))
            return
        _start_global_action(key)

    def _start_global_action(key: str):
        """Launch one global action with the selection already settled."""
        if running.is_set():
            return
        out_dir = output_var.get().strip()
        cmd = _global_cmd(key, out_dir)
        label = next(l for k, l, _t, _s, _r in GLOBAL_ACTIONS if k == key)

        _clear_log()
        # Opened BEFORE the header lines so they land in the file too.
        _run_log_begin({"Command": label, "Output": out_dir})
        _log(label)
        _log(f"Output: {out_dir}")
        _run_log_note()
        _log("")

        q = queue.Queue()
        _reset_run_errors()
        # See the pipeline runner: the summary is emitted by the drain once the
        # queue is empty, never by the worker (whose last lines are still in
        # flight when it exits).
        _want_summary = [False]

        def _drain():
            try:
                while True:
                    _log(q.get_nowait())
            except queue.Empty:
                pass
            if running.is_set():
                root.after(50, _drain)
            elif _want_summary[0] or _run_log[0] is not None:
                # The queue is empty and the worker is done, so every line --
                # including the summary below -- has been through _log.
                # Either condition must enter: with run logging disabled
                # (logRunsKept: 0) the summary still has to print.
                if _want_summary[0]:
                    _want_summary[0] = False
                    _log_error_summary()
                _run_log_size_note()
                _run_log_end(f"EXIT: {'OK' if not _run_errors else 'ERRORS'}")

        run_env = {WORKERS_ENV_VAR: str(_get_workers())}
        # This process owns the run log; a child must not rotate it.
        run_env.update(_run_log_env())

        def _worker():
            _set_running(True)
            ret = 1
            try:
                q.put(f"Running: {' '.join(cmd)}")
                ret = _run_process(cmd, q.put, env=run_env,
                                   cancel_event=cancel_evt)
                q.put("")
                q.put("  CANCELLED" if ret == -2
                      else "  DONE" if ret == 0
                      else f"  FAILED (exit {ret})")
            finally:
                _want_summary[0] = ret not in (0, -2)
                if ret == 0:
                    # Stamp only on success: a failed or cancelled run must
                    # leave the button lit, not quietly mark the work done.
                    try:
                        _last_global_stamp[key] = _global_stamp(key)
                        import version as _v
                        _v.record_step_run(key, None)
                    except Exception:
                        pass
                root.after(0, lambda: _set_running(False))

        threading.Thread(target=_worker, daemon=True).start()
        # Schedule the drain, never call it inline: `running` is set INSIDE
        # _worker, on the worker thread, so an immediate call almost always
        # sees it still clear, skips the `if running.is_set()` re-arm and never
        # runs again — every line of output stays stranded in the queue and the
        # button looks dead. Deferring lets the worker set the flag first.
        root.after(50, _drain)

    def _run_clicked():
        if running.is_set():
            return
        fname   = file_var.get()
        out_dir = output_var.get().strip()
        steps   = [key for key, *_ in STEPS if step_vars[key].get()]
        if not steps:
            _info("No Steps", "Select at least one pipeline step.")
            return

        # The plugin box is typable, so the text may not name a real plugin.
        # `all_plugins` tracks the ACTIVE source, so an imported mod's plugins
        # pass this check; the message has to name the right source too.
        if all_plugins and fname not in all_plugins:
            row = scope_rows.get(scope_var.get()) or {}
            _info("Unknown Plugin",
                  f"{fname!r} is not a plugin in "
                  f"{row.get('label') or 'the selected source'}.\n"
                  "Pick one from the list.")
            return

        # Collect selected mesh subdirs (None = all)
        selected_subdirs = None
        if "meshes" in steps and mesh_subdir_vars:
            chosen = [name for name, v in mesh_subdir_vars if v.get()]
            all_names = [name for name, _ in mesh_subdir_vars]
            if chosen and chosen != all_names:
                selected_subdirs = chosen

        _clear_log()
        # Opened BEFORE the header block so the run's settings -- the first
        # thing anyone reads when diagnosing it later -- are in the file.
        _run_log_begin({
            "Command": "Pipeline run",
            "File":    fname or "(none)",
            "Steps":   ", ".join(steps),
            "Output":  out_dir,
            "Workers": str(_get_workers()),
        })
        _log(f"File: {fname or '(none)'}")
        _log(f"Steps: {', '.join(steps)}")
        _log(f"Output: {out_dir}")
        _log(f"Workers: {_get_workers()} (of {cpu_max})")
        if selected_subdirs:
            _log(f"Mesh subdirs: {', '.join(selected_subdirs)}")
        if "meshes" in steps:
            _log(f"Collision winding fix: {'on' if _winding_on() else 'off'}")
            _log(f"Parallax: {'on' if parallax_var.get() else 'off'}"
                 + (" (Community Shaders or ENB required in game)"
                    if parallax_var.get() else ""))
            if tex_only_var.get():
                _log("Textures only: no meshes written (for PGPatcher)")
        _run_log_note()
        _log("")

        q = queue.Queue()
        _reset_run_errors()
        # Set by the worker when the run ends in failure.  The summary is
        # emitted by the DRAIN, not the worker: the worker finishes while its
        # last lines are still sitting in the queue, so printing there would put
        # the summary above the errors it summarises -- and _run_errors would
        # not yet contain them.
        _want_summary = [False]

        def _drain_queue():
            try:
                while True:
                    line = q.get_nowait()
                    _log(line)
            except queue.Empty:
                pass
            # Continue draining while running
            if running.is_set():
                root.after(50, _drain_queue)
            elif _want_summary[0] or _run_log[0] is not None:
                # Final pass: the worker is done and the queue is empty, so
                # every error line has been through _log and recorded.
                # Either condition must enter: with run logging disabled
                # (logRunsKept: 0) the summary still has to print.
                if _want_summary[0]:
                    _want_summary[0] = False
                    _log_error_summary()
                _run_log_size_note()
                _run_log_end(f"EXIT: {'OK' if not _run_errors else 'ERRORS'}")

        # Propagate the chosen worker count to every child process (and the
        # multiprocessing workers they spawn) via the environment.
        run_env = {WORKERS_ENV_VAR: str(_get_workers())}
        # Same channel for the cache opt-out. Only set when the user turned it
        # OFF: the variable is read as "1/true means skip", so an unset value is
        # the enabled default and inheriting a stale "1" from the parent
        # environment can never silently disable a run the user re-enabled.
        run_env[NO_DOWNLOAD_ENV_VAR] = '' if cache_dl_var.get() else '1'
        # A GUI run is several convert.py processes; THIS process owns the log
        # (every line already flows through _log). Children see the variable
        # and neither rotate nor write, so the file has exactly one writer.
        run_env.update(_run_log_env())

        def _worker():
            _set_running(True)
            try:
                # The one-process fast path fires when the selection IS the
                # default one. That default now depends on the Pack-by-default
                # setting, so it is computed the same way the checkboxes are:
                # with packing off, "everything except the two packing steps"
                # is still a default selection and still runs as a single
                # pipeline invocation rather than degrading to one process per
                # step.
                default_set = {k for k, *rest in STEPS if rest[3]}
                default_set &= default_on_steps(pack_default_var.get())
                active_set  = set(steps)
                ret = 0
                # If selection == default set and a file is specified and no
                # mesh subfolder filter, run the pipeline once.
                #
                # NOT via a bare `convert.py -f <plugin>`: that takes convert's
                # own default path, which still switches Patch Skyrim on. That
                # step is a global BUTTON now, so running it as a side effect of
                # converting one plugin is exactly the coupling this change
                # removes — it would rewrite the shared patch (and the load
                # order it was built for) behind the user's back. Listing the
                # steps explicitly keeps the button the only thing that runs it.
                if (active_set == default_set and fname
                        and not selected_subdirs):
                    cmd = [sys.executable, "-u", str(SCRIPT_DIR / "convert.py"),
                           "-f", fname, _winding_flag()]
                    cmd += [flag for key, flag, *_ in STEPS if key in active_set]
                    if out_dir:
                        cmd += ["--output-dir", out_dir]
                    q.put(f"Running: {' '.join(cmd)}")
                    ret = _run_process(cmd, q.put, env=run_env,
                                       cancel_event=cancel_evt)
                else:
                    for step in steps:
                        if cancel_evt.is_set():
                            break
                        cmd = _build_cmd(step, fname, out_dir, selected_subdirs)
                        q.put(f"Running: {' '.join(cmd)}")
                        r = _run_process(cmd, q.put, env=run_env,
                                         cancel_event=cancel_evt)
                        if r == -2:
                            ret = -2
                            break
                        if r == _RC_MISSING_DEP:
                            # convert.py already printed what is missing and how
                            # to install it.  Each step is its own process, so
                            # nothing else would stop the remaining ones from
                            # running and producing half-converted output.
                            ret = r
                            break
                        if r != 0:
                            ret = r

                q.put("")
                if ret == -2:
                    q.put("  CANCELLED")
                elif ret == _RC_MISSING_DEP:
                    q.put("  STOPPED - MISSING DEPENDENCY (see above)")
                elif ret == 0:
                    q.put("  DONE")
                else:
                    q.put(f"  FAILED (exit {ret})")
                # Flag BEFORE clearing `running`, so the drain pass that sees
                # the run finished is guaranteed to see this too.
                _want_summary[0] = ret not in (0, -2)
            except Exception as exc:
                # The launcher itself broke (bad command build, OS refusal).
                # Without this the run ended with NO verdict line at all -- the
                # log just stopped and the button went idle.
                q.put("")
                q.put(f"ERROR: {exc}")
                q.put("  FAILED")
                _want_summary[0] = True
            finally:
                root.after(0, lambda: _set_running(False))

        threading.Thread(target=_worker, daemon=True).start()
        # Start draining the queue in the UI thread
        root.after(50, _drain_queue)

    # ── Sidebar drop zone ────────────────────────────────────────────────────
    # The WHOLE sidebar accepts a dropped mod archive, so there is no small
    # target to hunt for. Registered last: dropping runs the same import the
    # Mods menu does, and every handler it needs exists by now.
    def _install_dropzone():
        if not DND_AVAILABLE:
            return
        try:
            from tkinterdnd2 import DND_FILES
        except Exception:
            return

        # tkdnd delivers <<DragLeave>> unreliably as the pointer crosses child
        # widgets, so the highlight is guarded by a depth counter and force-
        # reset on drop and on focus loss. Without that the sidebar can stay
        # stuck in its highlighted state after the pointer has gone.
        depth = [0]
        saved_cursor = [None]

        def _paint(on: bool):
            try:
                sidebar.configure(style="Drop.TFrame" if on
                                  else "Panel.TFrame")
                if on:
                    if saved_cursor[0] is None:
                        saved_cursor[0] = sidebar.cget("cursor")
                    sidebar.configure(cursor="hand2")
                else:
                    sidebar.configure(cursor=saved_cursor[0] or "")
                    saved_cursor[0] = None
            except tk.TclError:
                pass

        def _enter(_e=None):
            depth[0] += 1
            _paint(True)
            return "copy"

        def _leave(_e=None):
            depth[0] = max(0, depth[0] - 1)
            if depth[0] == 0:
                _paint(False)

        def _reset(_e=None):
            depth[0] = 0
            _paint(False)

        def _drop(event):
            _reset()
            paths = parse_dropped_paths(getattr(event, "data", "") or "")
            if not paths:
                return
            if len(paths) > 1:
                _info("Import Mod",
                      "Drop one mod archive at a time.")
                return
            path = paths[0]
            if not os.path.exists(path):
                _info("Import Mod", f"Not found:\n\n{path}")
                return
            # A folder is a valid mod source; any other non-archive is not.
            if not os.path.isdir(path):
                from asset_convert import archive as _archive
                if not _archive.is_archive(path):
                    _info("Import Mod",
                          f"{os.path.basename(path)} is not a mod archive.\n\n"
                          "Drop a .zip, .7z or .rar, or an extracted mod "
                          "folder.")
                    return
            _begin_import(path)

        try:
            sidebar.drop_target_register(DND_FILES)
            sidebar.dnd_bind("<<DropEnter>>", _enter)
            sidebar.dnd_bind("<<DropLeave>>", _leave)
            sidebar.dnd_bind("<<Drop>>", _drop)
        except Exception:
            return
        root.bind("<FocusOut>", _reset, add="+")

    _install_dropzone()

    # Startup: seed the source list from the folders this project already knows
    # about (the configured tes4DataPath plus every folder a past conversion
    # ran from), so someone with two installs sees both immediately rather
    # than having to re-add the second by hand.
    try:
        from asset_convert import source_registry as _sr
        _sr.migrate_known_directories(EXPORT_DIR, extra_dirs=[tes4_var.get()])
    except Exception:
        pass

    # The plugin combo is already populated, so an upgrade is visible
    # (and pre-selected) before the user touches anything.
    _refresh_scopes()
    _apply_scope(select_plugin=file_var.get())
    _apply_step_availability()
    _refresh_upgrade_notice()
    _update_run_btn()
    # Seed the global buttons from what has already been run, so a session that
    # opens onto an up-to-date output shows them greyed rather than outstanding.
    # Actions with a testable artefact are resolved by _global_is_current
    # itself; this only has to seed the ones that have none, where a recorded
    # run is the sole evidence they ever completed.
    for _gk, *_ in GLOBAL_ACTIONS:
        try:
            import version as _v
            if (_global_artifact(_gk) is None
                    and _v.steps_run_at(None).get(_gk)):
                _last_global_stamp[_gk] = _global_stamp(_gk)
        except Exception:
            pass
    _refresh_global_btns()
    root.mainloop()
    return 0


# ══════════════════════════════════════════════════════════════════════════════
#  Entry point
# ══════════════════════════════════════════════════════════════════════════════

def _relaunch_windowless() -> bool:
    """On Windows, re-exec the GUI under pythonw.exe so no console window lingers.

    When launched as ``python gui.py`` / ``./gui.py`` from a terminal (or via the
    ``py`` launcher, which allocates its own console), the GUI ends up with a
    console window that just sits behind it. Detaching under the console-less
    interpreter removes that stray window; the GUI's own log pane is the only
    place subprocess output should appear.

    Returns True if a relaunch was started (caller should exit), False otherwise.
    """
    if sys.platform != "win32":
        return False
    # Already console-less (launched via .pyw / pythonw) — nothing to do.
    exe = sys.executable
    if not exe.lower().endswith("python.exe"):
        return False
    pythonw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if not os.path.isfile(pythonw):
        return False
    # Guard against an infinite relaunch loop.
    if os.environ.get("_TES_GUI_RELAUNCHED") == "1":
        return False

    env = os.environ.copy()
    env["_TES_GUI_RELAUNCHED"] = "1"
    # DETACHED_PROCESS so the new process has no console at all and is not tied
    # to the (soon-to-close) parent terminal.
    flags = getattr(subprocess, "DETACHED_PROCESS", 0)
    try:
        subprocess.Popen(
            [pythonw, str(Path(__file__).resolve())] + sys.argv[1:],
            cwd=str(SCRIPT_DIR),
            env=env,
            creationflags=flags,
            close_fds=True,
        )
        return True
    except OSError:
        return False


def main():
    parser = argparse.ArgumentParser(description="TES4 Auto-Convert GUI")
    parser.add_argument("--cli", action="store_true",
                        help="Headless: forward remaining args to convert.py")
    args, extra = parser.parse_known_args()

    if args.cli:
        cmd = [sys.executable, "-u", str(SCRIPT_DIR / "convert.py")] + extra
        ret = subprocess.run(cmd, cwd=str(SCRIPT_DIR), **_POPEN_FLAGS)
        return ret.returncode

    # Detach from any inherited console so the GUI stands alone (Windows only).
    if _relaunch_windowless():
        return 0

    return gui_main()


if __name__ == "__main__":
    sys.exit(main())
