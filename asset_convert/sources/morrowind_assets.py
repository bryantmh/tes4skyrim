"""
Vanilla Morrowind meshes for a plugin whose TES3 masters were never exported.

In Morroblivion mode the three vanilla ESMs are dropped from the master chain,
but a dependent plugin's wearables are still assembled from vanilla BODY parts
on the vanilla skeleton. Those resolve through the registered Morrowind
install: a loose file under its Data Files, else the first archive holding the
path, extracted once into `export/morrowind_assets/`.

See: docs/commentary/tes4_export_morrowind.md#vanilla-assets
"""

import os
from pathlib import Path

from asset_convert.sources import source_registry
from asset_convert.sources.bsa_extract_morrowind import (is_morrowind_bsa,
                                                         read_entry,
                                                         read_index)

#: Folder under the export root holding the extracted vanilla files.
CACHE_DIR = 'morrowind_assets'

#: The plugin whose registered directory is the Morrowind install.
_ANCHOR_PLUGIN = 'Morrowind.esm'

#: Archive subfolder every mesh path is stored under.
_MESHES = 'meshes'

#: Folder Morroblivion moved its reworked Morrowind meshes under.
_MORRO = 'morro' + chr(92)

_index_cache = {}
_folded_cache = {}


def _archive_index(data_dir: str) -> dict:
    """{lower stored path: (archive, start, size)} over the install's archives."""
    if data_dir in _index_cache:
        return _index_cache[data_dir]
    index = {}
    for name in sorted(os.listdir(data_dir)):
        path = os.path.join(data_dir, name)
        if name.lower().endswith('.bsa') and is_morrowind_bsa(path):
            for stored, (start, size) in read_index(path).items():
                index[stored] = (path, start, size)
    _index_cache[data_dir] = index
    return index


def _cache_entry(cached: Path, entry) -> Path:
    """Extract an archive `entry` to `cached` atomically, so a concurrent reader never sees half a file."""
    cached.parent.mkdir(parents=True, exist_ok=True)
    tmp = cached.with_name(f'{cached.name}.{os.getpid()}.tmp')
    tmp.write_bytes(read_entry(*entry))
    os.replace(tmp, cached)
    return cached


def _normalize(rel: str) -> str:
    """A mesh path in archive form: lower case, backslashes, no doubling."""
    rel = rel.replace(chr(92) * 2, chr(92)).replace('/', chr(92))
    return rel.strip(chr(92)).lower()


def find_vanilla_mesh(export_root, rel: str):
    """The vanilla mesh at `rel` (relative to meshes) as a local path, or None."""
    data_dir = source_registry.directory_for(str(export_root), _ANCHOR_PLUGIN)
    if not data_dir:
        return None
    rel = _normalize(rel)
    loose = Path(data_dir) / _MESHES / rel
    if loose.is_file():
        return loose
    cached = Path(export_root) / CACHE_DIR / _MESHES / rel
    if cached.is_file():
        return cached
    entry = _archive_index(data_dir).get(_MESHES + chr(92) + rel)
    return None if entry is None else _cache_entry(cached, entry)


def find_archived_mesh(export_root, rel: str):
    """The vanilla mesh at `rel` AS SHIPPED, ignoring any loose replacer.

    `find_vanilla_mesh` prefers a loose file because that is what the game
    loads. Geometry a plugin's coordinates were authored against is the
    opposite question: a user's mesh replacer must not change what we measure,
    or two installs convert the same plugin differently.

    See: docs/commentary/tes4_export_morrowind.md#morroblivion-origin-shift
    """
    data_dir = source_registry.directory_for(str(export_root), _ANCHOR_PLUGIN)
    if not data_dir:
        return None
    rel = _normalize(rel)
    cached = Path(export_root) / CACHE_DIR / _MESHES / rel
    if cached.is_file():
        return cached
    entry = _archive_index(data_dir).get(_MESHES + chr(92) + rel)
    return None if entry is None else _cache_entry(cached, entry)


def _folded_meshes(data_dir: str) -> dict:
    """{mesh path with `_` folded to `u`: archived mesh path}; a fold two paths share is left out."""
    if data_dir not in _folded_cache:
        prefix = _MESHES + chr(92)
        seen = {}
        for key in _archive_index(data_dir):
            if key.startswith(prefix):
                rel = key[len(prefix):]
                seen.setdefault(rel.replace('_', 'u'), []).append(rel)
        _folded_cache[data_dir] = {k: v[0] for k, v in seen.items() if len(v) == 1}
    return _folded_cache[data_dir]


def find_original_mesh(export_root, rel: str):
    """The archived Morrowind original of Morroblivion mesh `rel` (under `morro\\`), or None.

    Morroblivion writes each `_` of the original's file name as `U`, so the
    two match once `_` is folded to `u`.
    """
    rel = _normalize(rel)
    data_dir = source_registry.directory_for(str(export_root), _ANCHOR_PLUGIN)
    if not rel.startswith(_MORRO) or not data_dir:
        return None
    original = _folded_meshes(data_dir).get(rel[len(_MORRO):])
    return find_archived_mesh(export_root, original) if original else None


def resolve_mesh(roots, rel: str, export_root):
    """The first mesh root holding `rel`, then the vanilla install; else None."""
    rel = _normalize(rel)
    for root in roots:
        path = Path(root) / rel
        if path.is_file():
            return path
    return find_vanilla_mesh(export_root, rel)


def find_archived_file(export_root, rel: str):
    """Any vanilla file at `rel` (a stored path like `textures\\x.dds`), else None.

    Prefers the ARCHIVED copy over a loose replacer, as `find_archived_mesh`
    does. Falls back to loose only when no archive holds the path: vanilla
    ships `Fonts/` loose, so there is no shipped copy to prefer.
    See: docs/commentary/tes4_export_morrowind.md#vanilla-assets
    """
    data_dir = source_registry.directory_for(str(export_root), _ANCHOR_PLUGIN)
    if not data_dir:
        return None
    rel = _normalize(rel)
    cached = Path(export_root) / CACHE_DIR / rel
    if cached.is_file():
        return cached
    entry = _archive_index(data_dir).get(rel)
    if entry is None:
        loose = Path(data_dir) / rel
        return loose if loose.is_file() else None
    return _cache_entry(cached, entry)


def source_meshes(source_path: str) -> frozenset:
    """Mesh paths the archives beside `source_path` ship, relative to `meshes\\`.

    Asked at EXPORT time, when the plugin's own extracted tree may not exist.
    See: docs/commentary/tes4_export_morrowind.md#who-owns-a-mesh
    """
    data_dir = os.path.dirname(str(source_path))
    if not os.path.isdir(data_dir):
        return frozenset()
    prefix = _MESHES + chr(92)
    return frozenset(
        key[len(prefix):] for key in _archive_index(data_dir)
        if key.startswith(prefix))
