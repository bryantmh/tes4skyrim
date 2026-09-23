"""What the window reads before it can draw: tables, settings, disk, children.

Four leaf concerns with one thing in common -- none of them touch tkinter, so
this module imports no GUI toolkit and `gui.py` can re-export from it without
dragging Tk into a headless import (tests/test_version_upgrade.py depends on
that).

The pipeline STEPS/GLOBAL_ACTIONS tables live here because the runner, the
checkbox list and the menu bar all read them and none of them owns them.
`run_process` sits here rather than in `runner.py` for room: it is the only
non-GUI thing the runner needs, and the runner has no headroom left.

REPO_ROOT is derived once, here, from this file's three parents. No other GUI
module may re-anchor on its own `__file__`: doing so silently repoints
`export/` and `output/` the moment that module moves to another depth.

_OFFICIAL_PLUGINS lists the base game and official Creation Club content in
Bethesda's own load-order priority, independent of whatever plugins.txt says.

`create_pool_job()` runs at import so the whole conversion is contained in a
Job Object owned by this process: a GUI that dies without cleanup (crash, Task
Manager, closed window) then takes convert.py and every pool worker with it.
Cancel covers only a DELIBERATE stop; without the job, orphaned console-less
pythonw workers survive invisibly, holding the export index in RAM and keeping
handles open on output/ files.

See: docs/reference/pipeline.md#game-data-path-detection
"""

import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

from core.collision_options import default_for_plugin as _winding_default
from core.process_job import create_pool_job
from core.run_log import DEFAULT_RUNS_KEPT
from core.subprocess_flags import POPEN_FLAGS, configure_multiprocessing
from output_layout import asset_root

#: The repo root: this file is `<root>/core/gui/config.py`.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

CONFIG_FILE = REPO_ROOT / "conversion_config.json"
EXPORT_DIR = REPO_ROOT / "export"


#: Every color the window uses; the teal `global_btn` marks non-pipeline
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
    "global_btn":   "#26404a",
    "global_hover": "#325764",
}


#: Loaded HICONs; freeing one blanks the taskbar button.
ICON_HANDLES = []


# ---------------------------------------------------------------------------
#  Pipeline steps and global actions
# ---------------------------------------------------------------------------

#: (key, cli_flag, label, description, default_on, needs_file), in run order.
STEPS = [
    ("export",             "--export-only",        "1. Export",
     "Parse source binary into a text cache",        True,  True),
    ("extract",            "--extract-only",       "2. Extract",
     "Pull assets from source BSA archives",          True,  True),
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
     "Convert source scripts to Papyrus",        True,  True),
    ("pack",               "--pack-only",          "9. Pack BSAs",
     "Pack assets into BSA archives",             False, True),
    ("pack_zip",           "--pack-zip-only",      "10. Pack Mod Zip",
     "Zip mod files for installation",   True,  True),
]

def step_names(keys) -> str:
    """`keys` as STEPS labels (no "N. " prefix), comma-separated."""
    labels = {key: label.split('. ', 1)[-1] for key, _f, label, *_r in STEPS}
    return ', '.join(labels.get(k, k) for k in keys)


#: The packing pair, whose default tick state is a user setting, not a constant.
PACKING_STEPS = ("pack", "pack_zip")

#: conversion_config.json key for that setting. Absent reads as ON.
PACK_DEFAULT_CONFIG_KEY = "packStepsDefaultOn"

#: conversion_config.json key for the inferred-collision-winding tri-state.
WINDING_CONFIG_KEY = "collisionWindingFix"
WINDING_AUTO, WINDING_ON, WINDING_OFF = "auto", "on", "off"
WINDING_MODES = (WINDING_AUTO, WINDING_ON, WINDING_OFF)

#: conversion_config.json key for the object-LOD detail preset index.
LOD_DETAIL_CONFIG_KEY = "lodDetail"


def lod_detail_labels() -> tuple:
    """One menu label per detail preset: its distant-LOD triangle multiplier.

    Measured against preset 0, which is the pre-preset behaviour. No absolute
    size: the setting applies to every worldspace and each bakes differently.
    See: docs/commentary/asset_convert_terrain.md#object-lod-detail-presets
    """
    from asset_convert.lod.mesh_decimate import LOD_DETAIL_DEFAULT
    out = []
    for i, tri in enumerate(LOD_DETAIL_STEPS):
        tag = "  (default)" if i == LOD_DETAIL_DEFAULT else ""
        note = "  (old default)" if i == 0 else ""
        out.append(f"{tri:.1f}x triangles{tag}{note}")
    return tuple(out)


#: Distant-LOD triangle multiplier per preset, measured against preset 0.
LOD_DETAIL_STEPS = (1.00, 1.35, 2.60, 3.04, 3.48, 3.95, 4.74)

#: (key, label, tooltip, short, row); `row` None means Build menu only, no button.
GLOBAL_ACTIONS = [
    ("create_lod", "Create LOD",
     "Generate distant LOD for the whole load order in one pass, into the "
     "standalone output/AutoConvertLOD mod",
     "Create LOD", 0),
    ("pack_lod", "Pack LOD",
     "Zip the generated AutoConvertLOD folder into output/Finished Mods, "
     "ready to install like any converted plugin",
     "Pack LOD", 0),
    ("make_master", "Convert to Master",
     "Flag converted plugins as masters (ESM). A plugin that is NOT a master "
     "has every reference it contains treated as always-active, and the engine "
     "caps those at 1,048,576 — past that the game hangs on the main menu with "
     "no crash and no log. Applies to a whole master chain at once",
     "To Master", 1),
    ("package_start_mod", "Package Start Mod",
     "Build the TESGameSelect starter mod (the new-game world selector) and "
     "zip it into output/Finished Mods, ready to install like any "
     "converted plugin. Needs Skyrim installed: the MQ101 override is "
     "spliced from its Skyrim.esm",
     "Pack Start Mod", 1),
    ("modify_body_meshes", "Body Slot Patch",
     "Build the ARMA slot-44 body patch for your Skyrim load order",
     "Body Slot Patch", 2),
    ("package_runtime_dll", "Package SKSE Mod",
     "Zip the built TESRuntime.dll (the SKSE plugin that registers every "
     "converted plugin's creature animations at load) into "
     "output/Finished Mods, ready to install like any converted plugin",
     "Pack SKSE Mod", 2),
    ("convert_ui", "Convert Oblivion UI",
     "Build the standalone Oblivion UI mod: Skyrim's message boxes and menu "
     "cursor reskinned with Oblivion's own art, read from your Oblivion "
     "install. Replaces two files "
     "(Interface\\messagebox.swf, Interface\\cursormenu.swf)",
     "Convert Oblivion UI", None),
]


def winding_enabled_for(mode: str, plugin: str) -> bool:
    """Whether the winding repair runs, given the setting and the plugin."""
    if mode == WINDING_ON:
        return True
    if mode == WINDING_OFF:
        return False
    return _winding_default(plugin)


def default_on_steps(pack_default: bool = True) -> set:
    """Step keys that start ticked, given the Pack-by-default setting.

    Deliberately ignores the `default_on` column of STEPS: that column answers
    "did the user keep the default selection?" for the runner, which is a
    different question from what the checkboxes start at.
    """
    keys = {k for k, *_ in STEPS}
    if not pack_default:
        keys -= set(PACKING_STEPS)
    return keys


# ---------------------------------------------------------------------------
#  Saved settings
# ---------------------------------------------------------------------------

#: Game -> registry locations holding its install path, most specific first.
_GAME_REGISTRY_KEYS = {
    "oblivion": (
        r"SOFTWARE\WOW6432Node\Bethesda Softworks\Oblivion",
        r"SOFTWARE\Bethesda Softworks\Oblivion",
    ),
    "skyrimse": (
        r"SOFTWARE\WOW6432Node\Bethesda Softworks\Skyrim Special Edition",
        r"SOFTWARE\Bethesda Softworks\Skyrim Special Edition",
    ),
}


def _installed_data_dir(subkey: str) -> str:
    """The `Data` folder under one registry key's Installed Path, or ""."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, subkey) as key:
            path, _ = winreg.QueryValueEx(key, "Installed Path")
    except (FileNotFoundError, OSError):
        return ""
    data = os.path.join(path, "Data")
    return data if os.path.isdir(data) else ""


def find_game_path(game: str) -> str:
    """Auto-detect a game's Data folder from the Windows registry, or ""."""
    if sys.platform != "win32":
        return ""
    for subkey in _GAME_REGISTRY_KEYS.get(game, ()):
        found = _installed_data_dir(subkey)
        if found:
            return found
    return ""


def load_config() -> dict:
    """The saved config, or {} when nothing has been saved yet."""
    if not CONFIG_FILE.exists():
        return {}
    with open(CONFIG_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def save_config(cfg: dict) -> None:
    """Overwrite the saved config with `cfg`."""
    with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)


def default_config() -> dict:
    """A fresh copy of what a new install's conversion_config.json is seeded with.

    `mesh_decimate` is imported here rather than at module scope because it
    pulls in numpy, and this module is reached by `version.py` ->
    `release_notes.py`, which the release workflow runs on a bare runner that
    installs no dependencies.

    See: docs/reference/pipeline.md#the-config-file-is-per-install
    """
    from asset_convert.lod.mesh_decimate import LOD_DETAIL_DEFAULT
    return {
        "// INSTRUCTIONS": "Files to convert are supplied via -f/--files or the GUI. Masters are auto-detected from binary headers. Data paths are auto-detected from registry.",
        "// SKIP_TYPES": "Skip types are managed in tes5_import/constants.py SKIP_TYPES set.",
        "// logRunsKept": "How many run logs to keep in logs/ (named run-<timestamp>-<plugin>.log, newest sorts last). 0 disables run logging. Default 20.",
        "tes4DataPath": "",
        "tes5DataPath": "",
        "files": ["Oblivion.esm"],
        "logRunsKept": DEFAULT_RUNS_KEPT,
        "lodDetail": LOD_DETAIL_DEFAULT,
    }


def write_default_config() -> bool:
    """Create conversion_config.json from `default_config()`; True if written.

    See: docs/reference/pipeline.md#the-config-file-is-per-install
    """
    if CONFIG_FILE.exists():
        return False
    save_config(default_config())
    return True


def save_setting(key: str, value) -> None:
    """Write one key into the saved config, keeping every other key."""
    updated = load_config()
    updated[key] = value
    save_config(updated)


# ---------------------------------------------------------------------------
#  Scanning the disk
# ---------------------------------------------------------------------------

def scan_plugins(data_path: str) -> list:
    """Sorted .esm/.esp files in `data_path`."""
    if not data_path or not os.path.isdir(data_path):
        return []
    return sorted(name for name in os.listdir(data_path)
                  if name.lower().endswith(('.esm', '.esp')))


def scan_skyrim_plugins(data_path: str) -> list:
    """Sorted .esm/.esp/.esl files in `data_path`."""
    if not data_path or not os.path.isdir(data_path):
        return []
    return sorted(name for name in os.listdir(data_path)
                  if name.lower().endswith(('.esm', '.esp', '.esl')))


def _manifest_sources(folder: str) -> set:
    """Plugin names named by the `<name>.manifest.json` files in `folder`.

    A folder named for its plugin holds ONE manifest; a mod GROUP folder is
    named for the mod and holds one per plugin it converted, hence the scan
    rather than a probe for `<folder>.manifest.json`. The manifest's `source`
    field is authoritative; its filename is the fallback, because a truncated
    manifest still proves the folder is ours.
    """
    try:
        names = sorted(f for f in os.listdir(folder)
                       if f.endswith(".manifest.json"))
    except OSError:
        return set()
    found = set()
    for manifest_name in names:
        source = manifest_name[:-len(".manifest.json")]
        try:
            with open(os.path.join(folder, manifest_name), encoding="utf-8") as fh:
                got = json.load(fh).get("source")
            if isinstance(got, str) and got.strip():
                source = got.strip()
        except (OSError, ValueError):
            pass
        found.add(source)
    return found


def scan_converted(output_path: str) -> list:
    """Plugins already converted into `output_path`, sorted.

    Keyed on the manifests `phase_import` writes, never on directory names:
    `output/` also accumulates the `Finished Mods` folder, the
    `TESGameSelect` folder and `<plugin>.zip` archives, none of which can be
    re-run.
    """
    if not output_path or not os.path.isdir(output_path):
        return []
    try:
        entries = sorted(os.listdir(output_path))
    except OSError:
        return []
    found = set()
    for name in entries:
        folder = os.path.join(output_path, name)
        if os.path.isdir(folder):
            found |= _manifest_sources(folder)
    return sorted(found, key=str.lower)


#: Official content, always listed first and checked when present.
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


def _plugins_txt_lines() -> list:
    """Every line of the user's plugins.txt, or [] if it cannot be read."""
    path = (Path(os.environ.get("LOCALAPPDATA", ""))
            / "Skyrim Special Edition" / "plugins.txt")
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as fh:
            return fh.readlines()
    except OSError:
        return []


def _listed_plugin(raw_line: str, installed: dict) -> tuple:
    """One plugins.txt line as (installed name, is active), or (None, False).

    A leading `*` marks the plugin active; without it the line is merely
    listing a disabled plugin the user has installed.
    """
    line = raw_line.strip()
    if not line or line.startswith("#"):
        return None, False
    active = line.startswith("*")
    return installed.get(line.lstrip("*").lower()), active


def scan_skyrim_load_order(data_path: str) -> tuple:
    """(ordered_names, default_checked) for the Skyrim plugin picker.

    Order: official content in Bethesda's priority, then plugins.txt entries in
    load order, then installed-but-unlisted plugins. Only the first two groups
    default to checked -- a plugin neither list mentions is offered but starts
    unchecked.
    """
    installed = {name.lower(): name for name in scan_skyrim_plugins(data_path)}
    if not installed:
        return [], set()

    ordered = [installed[n.lower()] for n in _OFFICIAL_PLUGINS
               if n.lower() in installed]
    seen = set(ordered)
    default_checked = set(ordered)

    for raw_line in _plugins_txt_lines():
        name, active = _listed_plugin(raw_line, installed)
        if name is None or name in seen:
            continue
        ordered.append(name)
        seen.add(name)
        if active:
            default_checked.add(name)

    ordered.extend(name for name in installed.values() if name not in seen)
    return ordered, default_checked


def scan_mesh_subdirs(file_name: str) -> list:
    """Sorted root mesh subdirectories of `file_name`'s shared asset folder.

    Resolved through `asset_root`, never by joining the name onto the export
    root: plugins imported together from one archive share a single folder
    named for the MOD, so the joined path does not exist for them and the
    lookup silently returns nothing.
    """
    if not file_name:
        return []
    mesh_dir = asset_root(EXPORT_DIR, file_name) / "meshes"
    if not mesh_dir.is_dir():
        return []
    return sorted(d.name for d in mesh_dir.iterdir() if d.is_dir())


def parse_dropped_paths(data: str) -> list:
    """Split a tkdnd <Drop> payload into real filesystem paths.

    tkdnd hands over a Tcl list: paths with spaces are wrapped in braces
    ("{C:/My Mods/a.zip} C:/b.zip"), and a single unbraced path may still
    contain spaces. Tk's own splitlist applies the quoting rules correctly but
    needs a widget to call through, so this is the manual fallback.
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
            out.append(buf)
            buf = ''
            continue
        buf += ch
    out.append(buf)
    return [p for p in (s.strip() for s in out) if p]


# ---------------------------------------------------------------------------
#  Running convert.py
# ---------------------------------------------------------------------------

configure_multiprocessing()
create_pool_job()

#: Returned by `run_process` when the user cancelled the run.
RC_CANCELLED = -2

#: Returned when the process could not be started or streamed at all.
RC_FAILED = -1


def kill_process_tree(proc) -> None:
    """Forcibly kill `proc` and every descendant it spawned.

    `proc.terminate()` signals only convert.py; the conversion spawns
    multiprocessing workers plus helper .exes (ffmpeg, hkxcmd, BSArch, LODGen)
    that would keep running and hold the stdout pipe open, making cancellation
    look like a hang. `taskkill /T` walks the tree by PID.
    """
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True, timeout=15, **POPEN_FLAGS,
            )
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        proc.kill()
    except OSError:
        pass


def _split_lines(buf: bytearray) -> list:
    """Pop every complete line out of `buf`, decoded and newline-stripped."""
    lines = []
    while True:
        nl = buf.find(b"\n")
        if nl == -1:
            return lines
        line = bytes(buf[:nl + 1])
        del buf[:nl + 1]
        lines.append(line.decode("utf-8", errors="replace").rstrip("\r\n"))


def _pipe_reader(out, line_q) -> None:
    """Drain `out` into `line_q` a line at a time, then push a None sentinel."""
    buf = bytearray()
    try:
        while True:
            chunk = out.read(1024)
            if not chunk:
                break
            buf.extend(chunk)
            for line in _split_lines(buf):
                line_q.put(line)
    except (OSError, ValueError):
        pass
    finally:
        text = bytes(buf).decode("utf-8", errors="replace").rstrip("\r\n")
        if text:
            line_q.put(text)
        line_q.put(None)


def _pump(line_q, log_cb, cancel_event, proc) -> bool:
    """Feed queued lines to `log_cb` until the pipe closes; True if cancelled.

    Polls on a short interval so Cancel takes effect even while the child is
    silent or blocked deep inside a long step -- a blocking pipe read must
    never be what stands between the click and the process dying.
    """
    while True:
        if cancel_event is not None and cancel_event.is_set():
            kill_process_tree(proc)
            return True
        try:
            item = line_q.get(timeout=0.1)
        except queue.Empty:
            continue
        if item is None:
            return False
        log_cb(item)


def run_process(cmd, log_cb, env=None, cancel_event=None) -> int:
    """Run `cmd`, streaming its output to `log_cb` as bytes arrive.

    Returns the child's exit code, `RC_CANCELLED` if `cancel_event` fired, or
    `RC_FAILED` if it could not be run at all.
    """
    try:
        full_env = os.environ.copy()
        full_env["PYTHONUNBUFFERED"] = "1"
        if env:
            full_env.update(env)

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            bufsize=0, cwd=str(REPO_ROOT), env=full_env, **POPEN_FLAGS,
        )
        line_q: queue.Queue = queue.Queue()
        threading.Thread(target=_pipe_reader, args=(proc.stdout, line_q),
                         daemon=True).start()

        if _pump(line_q, log_cb, cancel_event, proc):
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            return RC_CANCELLED
        proc.wait()
        return proc.returncode
    except Exception as exc:
        try:
            log_cb(f"ERROR: {exc}")
        except Exception:
            pass
        return RC_FAILED
