"""Where a plugin's files come from: game paths and plugin resolution.

Split out of convert.py.  Everything here answers "where does this source
live", independent of any pipeline stage.

See: docs/reference/pipeline.md#plugin-source-resolution
"""

import json
import os
from pathlib import Path

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
}


def _configured_path(game: str, config: dict) -> str:
    """The Data path this config pins for `game`, if it names a real one."""
    if not config:
        return ""
    key = _CONFIG_PATH_KEYS.get(game)
    configured = config.get(key, "") or "" if key else ""
    return configured if configured and os.path.isdir(configured) else ""


def _registry_path(game: str) -> str:
    """The Data path Windows records for `game`; "" off Windows or if absent."""
    try:
        import winreg
    except ImportError:
        return ""
    for subkey in _REGISTRY_KEYS.get(game, ()):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, subkey) as key:
                path, _ = winreg.QueryValueEx(key, "Installed Path")
        except (FileNotFoundError, OSError):
            continue
        data = os.path.join(path, "Data")
        if os.path.isdir(data):
            return data
    return ""


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
        with open(SCRIPT_DIR / "conversion_config.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _registered_source(export_dir: str, file_name: str) -> str:
    """The plugin's own retained binary or owning game directory, else "".

    A missing or broken registry returns "" so a default-directory conversion
    still works -- that is the additive guarantee.
    """
    try:
        from asset_convert.sources import source_registry
        imported = source_registry.plugin_binary(export_dir, file_name)
        if imported:
            return str(imported)
        owner = source_registry.directory_for(export_dir, file_name)
    except Exception:
        return ""
    if not owner:
        return ""
    candidate = os.path.join(owner, file_name)
    return candidate if os.path.isfile(candidate) else ""


def resolve_plugin_path(file_name: str, tes4_data: str,
                        export_dir: str = None) -> str:
    """Absolute path to a plugin's TES4 binary.

    Tries an imported mod's retained binary, then the registered game
    directory owning the plugin, then the default Data directory.  EVERY
    `os.path.join(tes4_data, name)` must go through here.
    See: docs/reference/pipeline.md#plugin-source-resolution
    """
    export_dir = export_dir or str(SCRIPT_DIR / "export")
    return (_registered_source(export_dir, file_name)
            or os.path.join(tes4_data or "", file_name))


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
