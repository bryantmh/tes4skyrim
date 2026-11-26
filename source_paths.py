"""Where a plugin's files come from: game paths and plugin resolution.

Split out of convert.py.  Everything here answers "where does this source
live", independent of any pipeline stage.

See: docs/reference/pipeline.md#plugin-source-resolution
"""

import json
import os
import re
import struct
from pathlib import Path

from output_layout import DATA_ROOT

try:
    import winreg
except ImportError:
    winreg = None

SCRIPT_DIR = Path(__file__).resolve().parent

#: Game -> the conversion_config.json key overriding its auto-detected path.
_CONFIG_PATH_KEYS = {"oblivion": "tes4DataPath", "skyrimse": "tes5DataPath"}

#: Game -> registry subkeys holding its install path, most specific first.
_REGISTRY_KEYS = {
    "oblivion": (
        r"SOFTWARE\WOW6432Node\Bethesda Softworks\Oblivion",
        r"SOFTWARE\Bethesda Softworks\Oblivion",
    ),
    "skyrimse": (
        r"SOFTWARE\WOW6432Node\Bethesda Softworks\Skyrim Special Edition",
        r"SOFTWARE\Bethesda Softworks\Skyrim Special Edition",
    ),
    "skyrimvr": (
        r"SOFTWARE\WOW6432Node\Bethesda Softworks\Skyrim VR",
        r"SOFTWARE\Bethesda Softworks\Skyrim VR",
    ),
}
#: Registry folders whose subkeys are installed games: (root, value naming the install folder).
_INSTALL_ROOTS = (
    (r"SOFTWARE\WOW6432Node\Bethesda Softworks", "Installed Path"),
    (r"SOFTWARE\Bethesda Softworks", "Installed Path"),
    (r"SOFTWARE\WOW6432Node\GOG.com\Games", "path"),
    (r"SOFTWARE\GOG.com\Games", "path"),
)
#: An install's Data folder names: every Bethesda game's, then Morrowind's.
_DATA_NAMES = ("Data", "Data Files")
#: The first form version Skyrim writes; FO3/FNV headers carry a lower one.
_SKYRIM_FORM_VERSION = 43
#: HEDR versions the converter reads in a 24-byte header: Fallout 3, then New Vegas's floor.
_FALLOUT_HEDR = (0.94, 1.2)


def _configured_path(game: str, config: dict) -> str:
    """The Data path this config pins for `game`, if it names a real one."""
    if not config:
        return ""
    key = _CONFIG_PATH_KEYS.get(game)
    configured = config.get(key, "") or "" if key else ""
    return configured if configured and os.path.isdir(configured) else ""


def _reg_value(hive, subkey: str, name: str) -> str:
    """One registry string value, or "" off Windows or when absent."""
    if winreg is None:
        return ""
    try:
        with winreg.OpenKey(hive, subkey) as key:
            return str(winreg.QueryValueEx(key, name)[0])
    except OSError:
        return ""


def _registry_path(game: str) -> str:
    """The Data path Windows records for `game`; "" off Windows or if absent."""
    for subkey in _REGISTRY_KEYS.get(game, ()):
        path = _reg_value(winreg and winreg.HKEY_LOCAL_MACHINE, subkey, "Installed Path")
        data = os.path.join(path, "Data")
        if path and os.path.isdir(data):
            return data
    return ""


def _registered_installs() -> list:
    """Install folders Bethesda's and GOG's installers recorded in the registry."""
    out = []
    for root, value in _INSTALL_ROOTS if winreg else ():
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root) as key:
                names = [winreg.EnumKey(key, i) for i in range(winreg.QueryInfoKey(key)[0])]
        except OSError:
            continue
        out += [_reg_value(winreg.HKEY_LOCAL_MACHINE, root + "\\" + n, value) for n in names]
    return [p for p in out if p]


def _steam_installs() -> list:
    """Every game folder in every Steam library."""
    steam = _reg_value(winreg and winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath")
    if not steam:
        return []
    try:
        vdf = Path(steam, "steamapps", "libraryfolders.vdf").read_text(encoding="utf-8", errors="replace")
    except OSError:
        vdf = ""
    libraries = [steam] + [p.replace("\\\\", "\\") for p in re.findall(r'"path"\s+"([^"]+)"', vdf)]
    out = []
    for lib in libraries:
        common = Path(lib, "steamapps", "common")
        out += [str(p) for p in common.iterdir() if p.is_dir()] if common.is_dir() else []
    return out


def is_source_plugin(path: str) -> bool:
    """True when `path` is in a format the converter reads: Morrowind, Oblivion, Fallout 3 or New Vegas.

    A Skyrim or Fallout 4 file under the same name is not.
    See: docs/reference/pipeline.md#game-data-path-detection
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(40)
    except OSError:
        return False
    if head[:4] == b"TES3" or (head[:4] == b"TES4" and head[20:24] == b"HEDR"):
        return True
    if head[:4] != b"TES4" or head[24:28] != b"HEDR" or len(head) < 34:
        return False
    form_version = struct.unpack_from("<H", head, 20)[0]
    hedr = round(struct.unpack_from("<f", head, 30)[0], 2)
    return form_version < _SKYRIM_FORM_VERSION and (hedr == _FALLOUT_HEDR[0] or hedr >= _FALLOUT_HEDR[1])


def installed_game_dirs(masters) -> list:
    """The Data folders of installed games holding any of `masters`, from Bethesda's, GOG's and Steam's records.

    See: docs/reference/pipeline.md#game-data-path-detection
    """
    out, seen = [], set()
    for install in _registered_installs() + _steam_installs():
        for name in _DATA_NAMES:
            data = os.path.normpath(os.path.join(install, name))
            key = os.path.normcase(data)
            if key not in seen and any(is_source_plugin(os.path.join(data, m)) for m in masters):
                seen.add(key)
                out.append(data)
    return out


def find_game_path(game: str, config: dict = None) -> str:
    """A game's Data path: the config key if set, else the registry.

    See: docs/reference/pipeline.md#game-data-path-detection
    """
    return _configured_path(game, config) or _registry_path(game)


def get_paths(config: dict) -> tuple:
    """The (TES4, TES5) Data paths for this run."""
    return find_game_path("oblivion", config), find_game_path("skyrimse", config)


def load_config(config_path: str = None) -> dict:
    """Read conversion_config.json, or the file at `config_path`.

    The default file is per-install and untracked, so its absence is normal and
    reads as {}; every key it can hold has an in-code default. An explicit
    `config_path` still raises, because naming a file that is not there is a typo.

    See: docs/reference/pipeline.md#the-config-file-is-per-install
    """
    if config_path:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    try:
        with open(DATA_ROOT / "conversion_config.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _registered_source(export_dir: str, file_name: str,
                       search_directories: bool) -> str:
    """The plugin's own retained binary or owning game directory, else "".

    A missing or broken registry returns "" so a default-directory conversion
    still works -- that is the additive guarantee.
    """
    try:
        from asset_convert.sources import source_registry
        imported = source_registry.plugin_binary(export_dir, file_name)
        if imported:
            return str(imported)
        owner = (source_registry.directory_for(export_dir, file_name)
                 if search_directories else None)
    except Exception:
        return ""
    return os.path.join(owner, file_name) if owner else ""


def resolve_plugin_path(file_name: str, tes4_data: str,
                        export_dir: str = None) -> str:
    """Absolute path to a plugin's TES4 binary.

    Tries an imported mod's retained binary, then the selected Data directory
    `tes4_data`, then the first registered directory holding the plugin.
    EVERY `os.path.join(tes4_data, name)` must go through here.
    See: docs/reference/pipeline.md#plugin-source-resolution
    """
    export_dir = export_dir or str(DATA_ROOT / "export")
    selected = os.path.join(tes4_data or "", file_name)
    in_selected = bool(tes4_data) and os.path.isfile(selected)
    return (_registered_source(export_dir, file_name, not in_selected)
            or selected)


def is_asset_only(file_name: str, export_dir: str) -> bool:
    """True when this source ships assets but no plugin binary.

    See: docs/reference/pipeline.md#asset-only-mods-are-pseudo-plugins--they-still-take--f
    """
    try:
        from asset_convert.sources import source_registry
        entry = source_registry.get(export_dir, file_name)
    except Exception:
        return False
    if not entry:
        return False
    caps = entry.get('capabilities')
    if isinstance(caps, dict) and 'plugin' in caps:
        return not caps['plugin']
    return not entry.get('plugin')
