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

from asset_convert.game_paths import set_borrowed
from asset_convert.nif.tex_paths import path_bytes, texture_rel_path
from asset_convert.texture.texture_prune import read_manifest, write_manifest
from asset_convert.sources.hybrid_assets import MANIFEST as HYBRID_MANIFEST
from asset_convert.sources.morrowind_assets import CACHE_DIR
from core.plugin_masters import export_root, master_chain

#: The map's file name, in `map_dir`.
MAP_NAME = 'morroblivion_textures.txt'

#: NiAlphaProperty flags bit 9: alpha testing on.
ALPHA_TEST_ENABLE = 0x0200

#: NiAlphaProperty flags bits 9-12: alpha-test enable and its comparison function.
ALPHA_TEST_BITS = 0x1E00

#: The alpha tests Morroblivion authored on its twins' shapes, per paired mesh and texture-wide, in `map_dir`.
ALPHA_NAME = 'morroblivion_texture_alpha_by_mesh.txt'

#: The texture-wide-only list older builds wrote; deleted when the map is proven again.
LEGACY_ALPHA_NAME = 'morroblivion_texture_alpha.txt'

#: The mesh field of a texture-wide alpha row.
ANY_MESH = '*'

#: A mesh's alpha row when its counterpart wears the twin without an alpha test.
NO_TEST = (0, 0)

#: The compatibility patch: Morroblivion mode's master, whose dependents use the map.
PATCH_NAME = 'Morrowind-Morroblivion-Compatibility.esp'

#: A hybrid plugin's texture keys it ships its own copy of, which never swap.
KEEP_NAME = 'morroblivion_textures_keep.txt'

#: Carries the active map's folder into spawned workers and child processes.
MAP_ENV = 'TESCONV_MORROBLIVION_TEXTURES'

#: Carries the active plugin's record dir, whose KEEP_NAME list the map skips.
KEEP_ENV = 'TESCONV_MORROBLIVION_KEEP'

#: The map and alpha list last read in this process, and the folders they were read from.
_LOADED = {'folder': None, 'table': {}, 'alpha': {}}


def write_map(record_dir, table: dict):
    """Write {vanilla texture stem: Morroblivion path} beside a plugin's records."""
    lines = {stem + '\t' + path for stem, path in table.items()}
    return write_manifest(record_dir, lines, MAP_NAME)


def read_map(record_dir) -> dict:
    """The map written into `record_dir`, or {} when there is none."""
    rows = (line.split('\t', 1) for line in read_manifest(record_dir, MAP_NAME))
    return {row[0]: row[1] for row in rows if len(row) == 2}


def write_alpha(folder, alpha: dict):
    """Write {(vanilla mesh key or ANY_MESH, texture stem): (alpha-test flag bits, threshold)} beside the map."""
    lines = {f'{mesh}\t{stem}\t{bits}\t{threshold}' for (mesh, stem), (bits, threshold) in alpha.items()}
    return write_manifest(folder, lines, ALPHA_NAME)


def read_alpha(folder) -> dict:
    """The alpha list written into `folder`, or {} when there is none."""
    rows = (line.split('\t') for line in read_manifest(folder, ALPHA_NAME))
    return {(row[0], row[1]): (int(row[2]), int(row[3])) for row in rows if len(row) == 4}


def mesh_key(rel) -> str:
    """A mesh's identity in the alpha list: its path below `meshes\\`, lower case, backslashes."""
    return str(rel).replace('/', '\\').lower()


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
    """Arm this plugin's map and its hybrid borrowed list here and in every process spawned from here on."""
    set_borrowed(record_dir)
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
        alpha = read_alpha(folders[0]) if folders[0] else {}
        _LOADED.update(folder=folders, table=table,
                       alpha={key: cut for key, cut in alpha.items() if key[1] in table})
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


def twin_cutoff(authored_tex, mesh_rel=None):
    """(alpha-test flag bits, threshold) Morroblivion authored with this texture's twin, or None.

    The test on `mesh_rel`'s own paired mesh wins over the texture-wide one.
    See: docs/commentary/asset_convert_texture.md#twin-alpha-cutoff
    """
    _table()
    if not authored_tex:
        return None
    key, alpha = texture_key(authored_tex), _LOADED['alpha']
    own = alpha.get((mesh_key(mesh_rel), key)) if mesh_rel else None
    if own is None:
        return alpha.get((ANY_MESH, key))
    return None if own == NO_TEST else own
