"""
FO3/FNV distant-LOD layout.

Oblivion keys its shipped LOD tiles by the worldspace's decimal FormID, flat in
one directory. FO3/FNV key them by EDITORID, one directory per worldspace, so
the FormID scan sees nothing and every FO3/FNV worldspace looks like it ships no
LOD at all.

See: docs/commentary/asset_convert_terrain.md#fo3fnv-keys-lod-by-editorid
"""

import os
from collections import Counter
from pathlib import Path

#: FO3/FNV LOD roots, each holding one directory per worldspace EditorID.
_LOD_ROOTS = ('meshes/landscape/lod', 'textures/landscape/lod')


def edid_keyed_lod_tiles(export_dir) -> Counter:
    """{worldspace edid (lowercase): tile count} from a FO3/FNV export tree.

    Empty for an Oblivion-style tree, whose LOD roots hold files rather than
    per-worldspace directories.
    """
    export_dir = Path(export_dir)
    counts = Counter()
    for sub in _LOD_ROOTS:
        root = export_dir / sub
        if not root.is_dir():
            continue
        with os.scandir(root) as entries:
            for entry in entries:
                if entry.is_dir():
                    counts[entry.name.lower()] += _count_tiles(entry.path)
    return counts


def _count_tiles(directory: str) -> int:
    """Every file under a worldspace's LOD directory, at any depth (a weight to rank by)."""
    with os.scandir(directory) as entries:
        return sum(_count_tiles(entry.path) if entry.is_dir() else 1 for entry in entries)


def resolve_edid_keyed(by_edid, edid_by_fid, to_converted, already):
    """[(edid, formid)] for EditorID-keyed worldspaces, busiest first.

    Skips any worldspace the FormID-keyed scan already reported, and any whose
    EditorID no WRLD record claims.
    """
    fid_by_edid = {e.lower(): f for f, e in edid_by_fid.items()}
    named = [(edid_by_fid[fid_by_edid[e]], fid_by_edid[e], n)
             for e, n in by_edid.items() if e in fid_by_edid]
    named.sort(key=lambda t: -t[2])
    return [(to_converted(edid), fid)
            for edid, fid, _n in named if fid not in already]
