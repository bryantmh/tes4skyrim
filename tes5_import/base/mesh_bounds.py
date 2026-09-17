"""
Axis-aligned bounding boxes (AABB) for converted NIF meshes.

Used by the import pipeline to set accurate OBND values on records instead of
type-based defaults.

This module is the READER half only.  The cache is produced by
`asset_convert.collision.collision_extract.scan_mesh_data`, which computes bounds and
collision from a SINGLE NIF parse — parsing dominates both analyses, so the
bounds scan used to re-read every mesh the collision scan had just read.  See
that function for the details.

    scan_mesh_data(mesh_dir, collision_cache, bounds_cache)  — after mesh
                                                               conversion
    load_mesh_bounds(cache_path)                             — import_main.py

Path keys are normalized: lowercase, forward slashes, relative to the mesh
output directory root.  Example: "tes4/furniture/chairnoble01.nif".

Records store raw TES4 model paths like "Furniture\\ChairNoble01.NIF"; after
prefix_path() and normalization these map to the same key.
"""

import json
import os
from typing import Dict, Optional, Tuple

OBNDTuple = Tuple[int, int, int, int, int, int]

# Module-level caches populated by load_mesh_bounds().
_MESH_BOUNDS: Dict[str, OBNDTuple] = {}
# Optional 7th element of a cache entry: physics flags from
# asset_convert.collision.collision_extract.physics_flags_from_data (bit 0 =
# constrained dynamic island -> the record must be MSTT, not STAT).
_MESH_PHYSICS: Dict[str, int] = {}


def _read_bounds_cache(path):
    """One cache file as {key: value}, minus the schema marker.

    '__schema__' carries the cache version, not a mesh (see
    collision_extract.BOUNDS_SCHEMA_VERSION): never a lookup key, and never
    a bounds entry either.
    """
    try:
        with open(path, encoding='utf-8') as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    return {k: v for k, v in raw.items() if k != '__schema__'}


def load_mesh_bounds(cache_path, quiet: bool = False) -> int:
    """Load computed bounds into the module cache; return the entry count.

    `cache_path` is one path or a MASTERS-FIRST iterable of them, so a child
    plugin sees its masters' meshes rather than falling back to type defaults
    for them.  The plugin's own entry wins a shared key.  quiet=True skips the
    prints, for the navmesh workers' pool initializer.
    """
    global _MESH_BOUNDS, _MESH_PHYSICS
    paths = [cache_path] if isinstance(cache_path, str) else list(cache_path)
    merged, found = {}, False
    for path in paths:
        if not os.path.exists(path):
            continue
        raw = _read_bounds_cache(path)
        if raw is None:
            continue
        merged.update(raw)
        found = True
    if not found:
        if not quiet:
            print(f"  Mesh bounds: no cache in {paths}, using type defaults")
        return 0
    _MESH_BOUNDS = {k: tuple(v[:6]) for k, v in merged.items()}
    _MESH_PHYSICS = {k: int(v[6]) for k, v in merged.items() if len(v) > 6}
    if not quiet:
        print(f"  Mesh bounds: loaded {len(_MESH_BOUNDS)} entries "
              f"from {len(paths)} cache(s)")
    return len(_MESH_BOUNDS)


def get_mesh_obnd(path_key: str) -> Optional[OBNDTuple]:
    """Return cached OBND tuple for *path_key*, or ``None`` if not found.

    *path_key* must be lowercase with forward slashes, relative to the mesh
    output directory root (e.g. ``"tes4/furniture/chairnoble01.nif"``).
    """
    return _MESH_BOUNDS.get(path_key)


def get_mesh_physics_flags(path_key: str) -> int:
    """Physics flags for *path_key* (0 if unknown).

    Bit 0: the converted NIF is a constrained dynamic havok island (swinging
    chains/signs).  Skyrim never simulates those on a STAT reference — the
    base record must be written as MSTT (see items.convert_STAT).

    Bit 1: a held keyframed body — the mesh needs SetMotionType(Dynamic) before
    it can move (read by script_convert.cross_ref for breakaway releases).
    """
    return _MESH_PHYSICS.get(path_key, 0)
