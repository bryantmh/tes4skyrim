"""Swapping a vanilla Morrowind texture for Morroblivion's higher-res twin.

Morroblivion renamed every texture '_' -> 'u' and repainted many at up to 16x
the resolution, so a Tamriel Rebuilt wall beside a Morroblivion one renders at
a fraction of its texel density. `prove_map`
(`tes4_export/morroblivion_texture_map.py`) proves which twins are
interchangeable and writes the one map to `map_dir`; every plugin whose master
chain reaches the patch, and every hybrid-mode export, swaps those textures
wherever a mesh names them.

See: docs/commentary/asset_convert_texture.md#morroblivion-texture-substitution
"""

import os

from asset_convert.nif.tex_paths import path_bytes, texture_rel_path
from asset_convert.texture.texture_prune import read_manifest, write_manifest
from asset_convert.sources.morrowind_assets import CACHE_DIR
from core.plugin_masters import export_root, master_chain

#: The map's file name, in `map_dir`.
MAP_NAME = 'morroblivion_textures.txt'

#: The compatibility patch: Morroblivion mode's master, whose dependents use the map.
PATCH_NAME = 'Morrowind-Morroblivion-Compatibility.esp'

#: Marks a hybrid-mode export: the files it copied, in its record dir.
HYBRID_MANIFEST = 'morroblivion_hybrid_assets.txt'

#: A hybrid plugin's texture keys it ships its own copy of, which never swap.
KEEP_NAME = 'morroblivion_textures_keep.txt'

#: Carries the active map's folder into spawned workers and child processes.
MAP_ENV = 'TESCONV_MORROBLIVION_TEXTURES'

#: Carries the active plugin's record dir, whose KEEP_NAME list the map skips.
KEEP_ENV = 'TESCONV_MORROBLIVION_KEEP'

#: The map last read in this process, and the folders it was read from.
_LOADED = {'folder': None, 'table': {}}


def write_map(record_dir, table: dict):
    """Write {vanilla texture stem: Morroblivion path} beside a plugin's records."""
    lines = {stem + '\t' + path for stem, path in table.items()}
    return write_manifest(record_dir, lines, MAP_NAME)


def read_map(record_dir) -> dict:
    """The map written into `record_dir`, or {} when there is none."""
    rows = (line.split('\t', 1) for line in read_manifest(record_dir, MAP_NAME))
    return {row[0]: row[1] for row in rows if len(row) == 2}


def map_dir(root) -> str:
    """The one folder holding the map: the export root's shared Morrowind asset cache."""
    return os.path.join(str(root), CACHE_DIR)


def map_folder(record_dir) -> str:
    """`map_dir` when this plugin uses the map (a hybrid export, the patch, or one of its dependents); ''."""
    root = export_root(record_dir)
    names = {n.lower() for n in master_chain(record_dir)} | {os.path.basename(os.path.normpath(str(record_dir))).lower()}
    uses = PATCH_NAME.lower() in names or os.path.isfile(os.path.join(record_dir, HYBRID_MANIFEST))
    folder = map_dir(root)
    return folder if uses and os.path.isfile(os.path.join(folder, MAP_NAME)) else ''


def activate(record_dir) -> str:
    """Arm this plugin's map here and in every process spawned from here on."""
    folder = map_folder(record_dir)
    for name, value in ((MAP_ENV, folder), (KEEP_ENV, str(record_dir) if folder else '')):
        if value:
            os.environ[name] = value
        else:
            os.environ.pop(name, None)
    return folder


def _table() -> dict:
    """The active map less the plugin's kept textures, read once per process per folder."""
    folders = (os.environ.get(MAP_ENV, ''), os.environ.get(KEEP_ENV, ''))
    if _LOADED['folder'] != folders:
        table = read_map(folders[0]) if folders[0] else {}
        for key in read_manifest(folders[1], KEEP_NAME) if folders[1] else ():
            table.pop(key, None)
        _LOADED.update(folder=folders, table=table)
    return _LOADED['table']


def texture_key(authored_tex) -> str:
    """A texture's identity: its path below `textures\\`, lower case, no extension.

    See: docs/commentary/asset_convert_texture.md#texture-identity
    """
    return os.path.splitext(texture_rel_path(authored_tex))[0].lower()


def substitute(authored_tex):
    """Morroblivion's path for a vanilla diffuse the active map clears, or None."""
    twin = _table().get(texture_key(authored_tex)) if authored_tex else None
    return None if twin is None else path_bytes(twin)
