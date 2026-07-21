"""Detect (and audit repairs of) inverted collision-triangle winding.

Havok mesh collision is single-sided: a near-horizontal triangle whose normal
points DOWN is walked straight through from above.  Nehrim's meshes re-export
collision as bhkPackedNiTriStripsShape triangle lists, and that flatten dropped
the strip parity flip on odd-indexed triangles — so one half of a floor quad
faces the wrong way and you fall through half the floor.

This scans source NIFs (Oblivion or Nehrim format) for that signature: an
up-facing and a down-facing near-horizontal triangle sharing an edge.  Vanilla
Oblivion is essentially clean (~10 of 4199 dungeon+architecture meshes), so a
run against an Oblivion tree is the control test for this detector.

Usage:
    python tools/collision_winding.py <nif_or_dir> [--workers N] [--top N]
    python tools/collision_winding.py export/Nehrim.esm/meshes/dungeons
    python tools/collision_winding.py <dir> --converted   # scan CMS output

    # Control test — should report very few hits:
    python tools/collision_winding.py ../TESConversion/export/Oblivion.esm/meshes/dungeons

See docs/nif_conversion_notes.md "Inverted collision winding in Nehrim source
meshes" for the repair (`asset_convert.collision._repair_inverted_floors`).
"""
import argparse
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_FLAT = 0.85          # near-horizontal cutoff
_HAVOK_SCALE = 69.99125


def _normal(v0, v1, v2):
    ux, uy, uz = v1[0]-v0[0], v1[1]-v0[1], v1[2]-v0[2]
    vx, vy, vz = v2[0]-v0[0], v2[1]-v0[1], v2[2]-v0[2]
    nx = uy*vz - uz*vy
    ny = uz*vx - ux*vz
    nz = ux*vy - uy*vx
    mag = math.sqrt(nx*nx + ny*ny + nz*nz)
    if mag == 0:
        return None
    return nx/mag, ny/mag, nz/mag


def _collision_tris(path, converted):
    """Return collision triangles as xyz tuples, roughly in game units."""
    from asset_convert import pyffi_monkey_patch  # noqa: F401  (clock patch)
    from pyffi.formats.nif import NifFormat
    data = NifFormat.Data()
    with open(path, 'rb') as f:
        data.read(f)

    tris = []
    for blk in data.blocks:
        name = type(blk).__name__
        if converted and name == 'bhkCompressedMeshShapeData':
            from asset_convert.cms import decode_cms
            for _key, tri in decode_cms(blk):
                tris.append(tuple(tuple(c * _HAVOK_SCALE for c in v)
                                  for v in tri))
        elif not converted and name == 'hkPackedNiTriStripsData':
            verts = [(v.x*7, v.y*7, v.z*7) for v in blk.vertices]
            for t in blk.triangles:
                a, b, c = t.triangle.v_1, t.triangle.v_2, t.triangle.v_3
                if a == b or b == c or a == c:
                    continue
                tris.append((verts[a], verts[b], verts[c]))
        elif not converted and name == 'bhkNiTriStripsShape':
            for sd in blk.strips_data:
                if sd is None:
                    continue
                verts = [(v.x, v.y, v.z) for v in sd.vertices]
                for a, b, c in sd.get_triangles():
                    if a == b or b == c or a == c:
                        continue
                    tris.append((verts[a], verts[b], verts[c]))
    return tris


def _scan(args):
    path, converted = args
    try:
        tris = _collision_tris(path, converted)
    except Exception as exc:
        return (path, -1, 0.0, repr(exc)[:60])
    if not tris:
        return None

    normals = []
    for t in tris:
        n = _normal(*t)
        normals.append(n[2] if n else 0.0)

    def key(v):
        return (round(v[0], 3), round(v[1], 3), round(v[2], 3))

    by_edge = {}
    for i, (v0, v1, v2) in enumerate(tris):
        k0, k1, k2 = key(v0), key(v1), key(v2)
        for e in ((k0, k1), (k1, k2), (k0, k2)):
            by_edge.setdefault(tuple(sorted(e)), []).append(i)

    pairs = 0
    worst = 0.0
    for idxs in by_edge.values():
        if len(idxs) != 2:
            continue
        i, j = idxs
        ni, nj = normals[i], normals[j]
        if not ((ni > _FLAT and nj < -_FLAT) or (nj > _FLAT and ni < -_FLAT)):
            continue
        pairs += 1
        # XY area of the smaller half — how much floor is passable.
        for k in (i, j):
            (x1, y1, _), (x2, y2, _), (x3, y3, _) = tris[k]
            area = abs((x2-x1)*(y3-y1) - (x3-x1)*(y2-y1)) / 2
            worst = max(worst, area)
    if pairs:
        return (path, pairs, worst, None)
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('root')
    ap.add_argument('--converted', action='store_true',
                    help='scan converted output (bhkCompressedMeshShape) '
                         'instead of TES4-format source')
    ap.add_argument('--top', type=int, default=25)
    ap.add_argument('--workers', type=int,
                    default=max(1, (os.cpu_count() or 2) - 1))
    a = ap.parse_args()

    if os.path.isfile(a.root):
        files = [a.root]
    else:
        files = [os.path.join(dp, fn)
                 for dp, _, fns in os.walk(a.root)
                 for fn in fns if fn.lower().endswith('.nif')]
    print(f"scanning {len(files)} NIFs ({'converted' if a.converted else 'source'} format)")

    hits, errors = [], 0
    payload = [(f, a.converted) for f in files]
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        for r in ex.map(_scan, payload, chunksize=32):
            if r is None:
                continue
            if r[1] < 0:
                errors += 1
                continue
            hits.append(r)

    hits.sort(key=lambda x: -x[2])
    total = sum(h[1] for h in hits)
    print(f"\nmeshes with inverted floor halves: {len(hits)} / {len(files)}"
          f"   bad edge-pairs: {total}   unreadable: {errors}")
    if hits:
        print("\n  pairs   max XY area  mesh")
        for path, pairs, area, _ in hits[:a.top]:
            print(f"  {pairs:5d}  {area:12.0f}  {path}")
    print("\n(vanilla Oblivion should report only a handful — if a scan of an "
          "Oblivion tree lights up, the detector is miscalibrated.)")


if __name__ == '__main__':
    main()
