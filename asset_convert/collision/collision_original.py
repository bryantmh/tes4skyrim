"""The Morrowind original a Morroblivion mesh was rebuilt from, as a winding oracle.

Morroblivion's collision is often wound backwards, and the render mesh only
guesses at the right side. Where the registered Morrowind install still holds
the original, its triangles (render and RootCollisionNode) stand up every
floor face that exactly matches an up-facing one.
"""

import os
from pathlib import Path

from pyffi.formats.nif import NifFormat

from asset_convert import paths
from asset_convert.sources.morrowind_assets import find_original_mesh
from output_layout import asset_root
from tes4_export.morroblivion import MORROBLIVION_PREFIX

#: Render soups past this size are still indexed: the original is a lookup, not a search.
_MAX_TRIS = 500000

#: [mesh path under its meshes root or None, its triangles once read, its source file, is-Morroblivion's once asked].
_CURRENT = [None, None, None, None]

#: The converted Morroblivion plugins' mesh trees, listed once per process.
_MORROBLIVION_ROOTS = []


def latch_original(src_path, src_meshes_dir) -> None:
    """Note which mesh is converting; a mesh outside `src_meshes_dir` has no original."""
    rel = None
    if src_path and src_meshes_dir:
        src, root = Path(os.path.abspath(src_path)), Path(os.path.abspath(src_meshes_dir))
        rel = str(src.relative_to(root)) if src.is_relative_to(root) else None
    _CURRENT[:] = [rel, None, src_path, None]


def _morroblivion_roots() -> list:
    """Each converted Morroblivion plugin's mesh tree."""
    if not _MORROBLIVION_ROOTS and os.path.isdir(paths.EXPORT):
        _MORROBLIVION_ROOTS.extend(
            asset_root(paths.EXPORT, name) / 'meshes' for name in sorted(os.listdir(paths.EXPORT))
            if name.lower().startswith(MORROBLIVION_PREFIX))
    return _MORROBLIVION_ROOTS


def _same_file(a: Path, b) -> bool:
    """Whether two files hold the same bytes."""
    return a.is_file() and a.stat().st_size == os.path.getsize(b) and a.read_bytes() == Path(b).read_bytes()


def is_morroblivion_mesh() -> bool:
    """Whether the converting mesh is Morroblivion's own file, wherever it was copied to.

    See: docs/commentary/asset_convert_collision.md#morroblivion-collision-is-copied-render
    """
    rel, src = _CURRENT[0], _CURRENT[2]
    if _CURRENT[3] is None:
        _CURRENT[3] = bool(rel) and any(_same_file(root / rel, src) for root in _morroblivion_roots())
    return _CURRENT[3]


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
