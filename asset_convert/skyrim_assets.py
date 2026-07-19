"""Vanilla Skyrim asset lookup with automatic BSA extraction.

Mesh conversion must never resolve through the references/ folder: that is a
comparison/analysis tree only, not part of the pipeline.  Any vanilla Skyrim
file the conversion needs (body/hands/feet meshes for skin splicing, the book
reading templates, ...) is resolved through this module:

    1. export/skyrim_assets/<rel>           (cache of prior BSA extractions)
    2. the game's own SSE BSA archives      (auto-detected via registry),
       extracted on demand and cached in 1.

BSA-sourced files are SSE-format; read them with asset_convert.sse_nif
(pyffi Patch 8 + BSTriShape->NiTriShape conversion).
"""

import os
from pathlib import Path

_REPO = Path(__file__).parent.parent
_CACHE_DIR = _REPO / 'export' / 'skyrim_assets'

_skyrim_data_override = None
_skyrim_data_cached = False
_skyrim_data = None


def set_skyrim_data(path):
    """Explicitly set the SSE Data folder (overrides registry detection)."""
    global _skyrim_data_override, _skyrim_data_cached
    _skyrim_data_override = str(path) if path else None
    _skyrim_data_cached = False


def find_skyrim_data():
    """SSE Data folder: explicit override, else Windows registry."""
    global _skyrim_data_cached, _skyrim_data
    if _skyrim_data_cached:
        return _skyrim_data
    _skyrim_data_cached = True
    _skyrim_data = None
    if _skyrim_data_override and os.path.isdir(_skyrim_data_override):
        _skyrim_data = _skyrim_data_override
        return _skyrim_data
    try:
        import winreg
        for hive_key in (r"SOFTWARE\WOW6432Node\Bethesda Softworks\Skyrim Special Edition",
                         r"SOFTWARE\Bethesda Softworks\Skyrim Special Edition"):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, hive_key) as key:
                    path, _ = winreg.QueryValueEx(key, "Installed Path")
                data = os.path.join(path, "Data")
                if os.path.isdir(data):
                    _skyrim_data = data
                    break
            except OSError:
                continue
    except ImportError:
        pass
    return _skyrim_data


def _bsa_glob_for(rel):
    """BSA name pattern that holds files under this top-level folder."""
    top = rel.replace('/', '\\').split('\\', 1)[0].lower()
    return {
        'meshes': 'Skyrim - Meshes*.bsa',
        'textures': 'Skyrim - Textures*.bsa',
    }.get(top, 'Skyrim - *.bsa')


def get_asset_bytes(rel):
    """Return the bytes of a vanilla Skyrim file, or None.

    rel: data-relative path, e.g. r'meshes\\actors\\character\\character
    assets\\malebody_0.nif'.  Search order: extraction cache, then game BSAs
    (SSE format; extracted + cached).
    """
    rel = rel.replace('/', '\\').lstrip('\\')

    cached = _CACHE_DIR / Path(*rel.split('\\'))
    if cached.is_file():
        return cached.read_bytes()

    data_dir = find_skyrim_data()
    if not data_dir:
        return None
    from .bsa_extract import read_bsa_files
    for bsa in sorted(Path(data_dir).glob(_bsa_glob_for(rel))):
        found = read_bsa_files(str(bsa), [rel])
        raw = found.get(rel.lower())
        if raw is not None:
            # Cache atomically: pool workers may race on the same file.
            cached.parent.mkdir(parents=True, exist_ok=True)
            tmp = cached.with_suffix(cached.suffix + '.tmp%d' % os.getpid())
            tmp.write_bytes(raw)
            try:
                os.replace(tmp, cached)
            except OSError:
                try:
                    tmp.unlink()
                except OSError:
                    pass
            return raw
    return None


def get_body_nif_bytes(basename):
    """Vanilla character-asset NIF (malebody_0.nif etc.) as bytes, or None."""
    return get_asset_bytes(
        'meshes\\actors\\character\\character assets\\' + basename)
