"""
FO3/FNV distant-LOD layout.

Oblivion keys its shipped LOD tiles by the worldspace's decimal FormID, flat in
one directory. FO3/FNV key them by EDITORID, one directory per worldspace, so
the FormID scan sees nothing and every FO3/FNV worldspace looks like it ships no
LOD at all.

See: docs/commentary/asset_convert_terrain.md#fo3fnv-keys-lod-by-editorid
"""

from collections import Counter
from pathlib import Path

#: FO3/FNV LOD roots, each holding one directory per worldspace EditorID.
_LOD_ROOTS = ('meshes/landscape/lod', 'textures/landscape/lod')


def edid_keyed_lod_tiles(export_dir) -> Counter:
    """{worldspace edid (lowercase): tile count} from a FO3/FNV export tree.

    Empty for an Oblivion-style tree, whose LOD roots hold files rather than
    per-worldspace directories.
    """
    from output_layout import assets_for
    from asset_convert import case_paths
    # Read from the shared asset tree (a nested mod keeps records below assets)
    # and match each LOD root case-blind, so an imported FO3/FNV mod's shipped
    # LOD is not missed for a record-dir/case mismatch (see shipped_lod_worldspaces).
    asset_dir = assets_for(Path(export_dir))
    counts = Counter()
    for sub in _LOD_ROOTS:
        for spelling in case_paths.variants(asset_dir, sub):
            root = Path(spelling)
            if not root.is_dir():
                continue
            for entry in root.iterdir():
                if entry.is_dir():
                    counts[entry.name.lower()] += _count_tiles(entry)
    return counts


def _count_tiles(directory: Path) -> int:
    """Every file under a worldspace's LOD directory, at any depth.

    The folder holds its own tiles plus 'blocks' and 'normals' subfolders of the
    same worldspace's data; the caller needs only a relative weight to rank
    worldspaces by, so all of it counts.
    """
    total = 0
    for entry in directory.iterdir():
        total += _count_tiles(entry) if entry.is_dir() else 1
    return total


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
