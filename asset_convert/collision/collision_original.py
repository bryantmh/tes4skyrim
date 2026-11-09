"""The Morrowind original a Morroblivion mesh was rebuilt from, as a winding oracle.

Morroblivion's collision is often wound backwards, and the render mesh only
guesses at the right side. Where the registered Morrowind install still holds
the original, its triangles (render and RootCollisionNode) stand up every
floor face that exactly matches an up-facing one.
"""

import os

from pyffi.formats.nif import NifFormat

from asset_convert import paths
from asset_convert.sources.morrowind_assets import find_original_mesh

#: Render soups past this size are still indexed: the original is a lookup, not a search.
_MAX_TRIS = 500000

#: [mesh path relative to its meshes root, or None; its triangles once read, or None].
_CURRENT = [None, None]


def latch_original(src_path, src_meshes_dir) -> None:
    """Note which mesh is converting; its original is read only if collision asks."""
    rel = None
    if src_path and src_meshes_dir:
        rel = os.path.relpath(str(src_path), str(src_meshes_dir))
    _CURRENT[0], _CURRENT[1] = rel, None


def _read_tris(path, soup) -> list:
    """Every triangle of the NIF at `path` through `soup`; [] when unreadable."""
    data = NifFormat.Data()
    try:
        with open(path, 'rb') as handle:
            data.read(handle)
    except Exception:
        return []
    return [t for root in data.roots for t in soup(root, max_tris=_MAX_TRIS)]


def original_tris(soup) -> list:
    """The converting mesh's original Morrowind triangles in `soup`'s units; [] when it has none.

    `soup(root, max_tris=...)` is the render-soup reader the repair already
    uses for the converting mesh, so both sides share one frame and scale.
    """
    if _CURRENT[1] is None:
        found = find_original_mesh(paths.EXPORT, _CURRENT[0]) if _CURRENT[0] else None
        _CURRENT[1] = _read_tris(found, soup) if found else []
    return _CURRENT[1]
