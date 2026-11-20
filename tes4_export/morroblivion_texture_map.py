"""The Morroblivion texture map: which vanilla textures Morroblivion's twin can replace.

Built by the compatibility patch's Export step. Every vanilla mesh Morroblivion
replaced through its records is paired with that replacement; where a vanilla
shape and a Morroblivion shape are the same geometry with the same UVs, and the
Morroblivion shape wears the vanilla texture's renamed twin ('_' -> 'u'), that
twin is proven to fit the vanilla UVs and goes in the map. Converted meshes of
every plugin downstream of the patch then wear the twin
(`asset_convert/nif/morroblivion_textures.py`).

See: docs/commentary/asset_convert_texture.md#morroblivion-texture-substitution
"""

import logging
import multiprocessing as mp
from collections import Counter
from pathlib import Path

import numpy as np

from asset_convert.nif.flipbook import read_dds_header
from asset_convert.nif.morroblivion_textures import texture_key, write_map
from asset_convert.nif.sse_nif import NifFormat, read_nif
from asset_convert.nif.tex_paths import as_dds, texture_rel_path
from asset_convert.sources.source_registry import asset_root
from core.process_job import join_pool_job
from core.worker_budget import worker_count

from .morroblivion import MorroblivionModels, archive_path

#: Position rounding; NIF float32 makes identical geometry differ at 1e-4.
POS_QUANT = 2

#: UV rounding. The UV is the value under test, so it stays tight.
UV_QUANT = 4

#: Archive folder every vanilla mesh key starts with.
MESH_PREFIX = 'meshes\\'


def _base_texture(block) -> str:
    """The authored base texture path of a geometry block, or ''."""
    for prop in list(getattr(block, 'properties', ()) or ()):
        if isinstance(prop, NifFormat.NiTexturingProperty):
            src = getattr(getattr(prop, 'base_texture', None), 'source', None)
            if src is not None and src.file_name:
                return src.file_name.decode('latin-1')
    return ''


def pair_set(verts, uv) -> Counter:
    """One shape as a multiset of rounded (x, y, z, u, v) tuples.

    See: docs/commentary/asset_convert_texture.md#comparing-shapes-across-the-pair
    """
    rows = np.hstack([np.round(verts, POS_QUANT), np.round(uv, UV_QUANT)])
    return Counter(map(tuple, rows.tolist()))


def shape_signatures(source) -> list:
    """[(authored texture, pair set)] for every textured shape with UVs.

    `source` is NIF bytes or a path.
    """
    out = []
    for block in read_nif(source).blocks:
        if not isinstance(block, NifFormat.NiTriBasedGeom) or block.data is None:
            continue
        geom = block.data
        tex = _base_texture(block)
        uvs = list(getattr(geom, 'uv_sets', ()) or ())
        if not tex or not uvs or len(uvs[0]) != len(geom.vertices) \
                or not len(uvs[0]):
            continue
        uv = np.array([[v.u, v.v] for v in uvs[0]], dtype=np.float64)
        verts = np.array([[v.x, v.y, v.z] for v in geom.vertices],
                         dtype=np.float64)
        out.append((tex, pair_set(verts, uv)))
    return out


def _file_name(key: str) -> str:
    """The last segment of a backslash texture key."""
    return key.rpartition('\\')[2]


def proven_twins(vanilla, morroblivion) -> list:
    """[(vanilla texture key, Morroblivion texture)] for shapes the two share exactly."""
    theirs = [(_file_name(texture_key(tex)), tex, pairs)
              for tex, pairs in shape_signatures(morroblivion)]
    out = []
    for tex, pairs in shape_signatures(vanilla):
        key = texture_key(tex)
        want = _file_name(key).replace('_', 'u')
        match = next((m_tex for name, m_tex, m_pairs in theirs
                      if name == want and m_pairs == pairs), None)
        if match is not None:
            out.append((key, match))
    return out


def _worker_init() -> None:
    """Tie a pool worker to the parent's lifetime and keep pyffi's warnings quiet."""
    join_pool_job()
    logging.getLogger('pyffi').setLevel(logging.ERROR)


def _proven_twins_task(job) -> tuple:
    """Pool task: (ok, twins) for one (vanilla bytes, Morroblivion path) pair."""
    try:
        return True, proven_twins(*job)
    except Exception:
        return False, []


def twin_meshes(export_dir: str, morroblivion, vanilla_esm: str, index) -> dict:
    """{vanilla archive mesh key: Morroblivion mesh file} for every record-paired mesh."""
    models = MorroblivionModels(export_dir, [(n, None) for n in morroblivion],
                                vanilla_esm)
    roots = [asset_root(export_dir, name) / 'meshes' for name in morroblivion]
    return paired_meshes(models, roots, index)


def paired_meshes(models: MorroblivionModels, roots, index) -> dict:
    """{vanilla archive mesh key: replacement file under `roots`} for every record-paired mesh `models` owns."""
    out = {}
    for rel in (r for r in models.owners if r.endswith('.nif')):
        model = archive_path(models.replacement(rel, index))
        if not model:
            continue
        model = model[len(MESH_PREFIX):] if model.startswith(MESH_PREFIX) \
            else model
        found = next((r / model.replace('\\', '/') for r in roots
                      if (r / model.replace('\\', '/')).is_file()), None)
        if found is not None:
            out[MESH_PREFIX + rel] = found
    return out


def _shipped(tex: str, roots) -> Path:
    """The Morroblivion texture file `tex` names, or None when none ships."""
    rel = as_dds(texture_rel_path(tex)).replace('\\', '/')
    return next((r / 'textures' / rel for r in roots
                 if (r / 'textures' / rel).is_file()), None)


def _pixels(path: Path) -> int:
    """Width times height of a DDS file; 0 when its header cannot be read."""
    try:
        with open(path, 'rb') as handle:
            width, height = read_dds_header(handle.read(128))[:2]
    except (OSError, ValueError):
        return 0
    return width * height


def _choose(candidates: set, roots) -> str:
    """The twin to wear: the largest shipped copy, as a `textures\\` path, or ''."""
    files = [(tex, _shipped(tex, roots)) for tex in sorted(candidates)]
    files = [(tex, path) for tex, path in files if path is not None]
    if not files:
        return ''
    tex, _path = max(files, key=lambda item: _pixels(item[1]))
    return 'textures\\' + as_dds(texture_rel_path(tex)).lower()


def _prove_all(jobs) -> tuple:
    """({vanilla texture key: {Morroblivion textures}}, unreadable count) over `jobs`."""
    found, unreadable = {}, 0
    if not jobs:
        return found, unreadable
    with mp.Pool(processes=min(worker_count(), len(jobs)),
                 initializer=_worker_init) as pool:
        for ok, pairs in pool.imap_unordered(_proven_twins_task, jobs, 16):
            unreadable += not ok
            for key, tex in pairs:
                found.setdefault(key, set()).add(tex)
    return found, unreadable


def build_map(meshes: dict, twins: dict, export_dir: str, morroblivion,
              progress=print) -> dict:
    """{vanilla texture key: Morroblivion path} proven over every paired mesh.

    `meshes` holds the vanilla bytes of each key in `twins`, read during the
    patch's single archive pass.
    """
    jobs = [(meshes[key], str(twins[key])) for key in sorted(meshes)]
    found, unreadable = _prove_all(jobs)
    roots = [asset_root(export_dir, name) for name in morroblivion]
    table = {key: _choose(texs, roots) for key, texs in found.items()}
    table = {key: path for key, path in table.items() if path}
    progress(f'  {len(table)} vanilla textures proven to fit Morroblivion\'s '
             f'twin over {len(jobs)} paired meshes ({unreadable} unreadable)')
    return table


def export_texture_map(meshes: dict, twins: dict, export_dir: str,
                       morroblivion, record_dir, progress=print) -> int:
    """Prove and write the map into `record_dir`; return its entry count."""
    table = build_map(meshes, twins, export_dir, morroblivion, progress)
    write_map(record_dir, table)
    return len(table)
