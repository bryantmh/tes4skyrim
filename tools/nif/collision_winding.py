"""Detect (and audit repairs of) inverted collision-triangle winding.

Havok mesh collision is single-sided: a near-horizontal triangle whose normal
points DOWN is walked straight through from above.  Nehrim's meshes re-export
collision as bhkPackedNiTriStripsShape triangle lists, and that flatten dropped
the strip parity flip on odd-indexed triangles — so one half of a floor quad
faces the wrong way and you fall through half the floor.

The default scan looks for that signature: an up-facing and a down-facing
near-horizontal triangle sharing an edge.

DO NOT USE VANILLA OBLIVION AS A CONTROL TEST -- it is NOT clean, and the
earlier claim here that it was (~10 of 4199 dungeon+architecture meshes) is
withdrawn.  rocks/seisland/seisland.nif alone scores 553 bad edge-pairs, and
14.5% of decidable floor faces in meshes/rocks are genuinely inverted.  A
detector that lights up on an Oblivion tree may well be right.  The authored
per-triangle normal is the ground truth to check against instead; see
docs/commentary/asset_convert_nif.md "round 3".

IMPORTANT — the default scan has a blind spot.  It only fires on a MIXED pair,
so a surface that is UNIFORMLY reversed reports zero: Morrowind_ob's
inuhlaaluuroomuside.nif has an all-down-facing floor you fall straight through
and scores 0 bad edge-pairs here.  Use --floor-orientation for that class, and
do not read a clean default scan as "this mesh is fine".

--floor-orientation reports the lowest near-horizontal surface in each mesh and
whether it faces up (walkable) or down (fall-through), which catches uniformly
reversed floors the pair test cannot see.

Usage:
    python tools/nif/collision_winding.py <nif_or_dir> [--workers N] [--top N]
    python tools/nif/collision_winding.py export/Nehrim.esm/meshes/dungeons
    python tools/nif/collision_winding.py <dir> --converted   # scan CMS output
    python tools/nif/collision_winding.py <dir> --floor-orientation

    # Control test — should report very few hits:
    python tools/nif/collision_winding.py ../TESConversion/export/Oblivion.esm/meshes/dungeons

The repair is `asset_convert.collision.collision_winding.repair_inverted_floors`.
See: docs/commentary/asset_convert_collision.md#morroblivion-collision-is-copied-render
"""
import argparse
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

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
    from asset_convert.nif.pyffi_monkey_patch import apply_patches
    apply_patches()
    from pyffi.formats.nif import NifFormat
    data = NifFormat.Data()
    with open(path, 'rb') as f:
        data.read(f)

    tris = []
    for blk in data.blocks:
        name = type(blk).__name__
        if converted and name == 'bhkCompressedMeshShapeData':
            from asset_convert.collision.cms import decode_cms
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


def _repair_soups(path):
    """[(node, tris_hu, groups)] for every mesh collision, converter-side."""
    from asset_convert.collision import collision as C
    from pyffi.formats.nif import NifFormat as NF
    data = NF.Data()
    with open(path, 'rb') as f:
        data.read(f)
    out = []
    for root in data.roots:
        for blk in root.tree():
            obj = getattr(blk, 'collision_object', None)
            body = getattr(obj, 'body', None) if obj else None
            shape = getattr(body, 'shape', None) if body else None
            if isinstance(shape, NF.bhkMoppBvTreeShape):
                shape = shape.shape
            if shape is None:
                continue
            soup = C._shape_tri_soup(shape)
            if soup is None:
                continue
            tris = C._bake_body_transform_into_tris(body, soup[0])
            out.append((blk, tris, C.shape_tri_groups(shape)))
    return out


def _floor_state(tris):
    """(up, down) counts for the lowest near-horizontal band."""
    if not tris:
        return (0, 0)
    lo = min(v[2] for t in tris for v in t)
    up = down = 0
    for t in tris:
        n = _normal(*t)
        if n is None or abs(n[2]) < _FLAT:
            continue
        if (sum(v[2] for v in t) / 3.0) - lo > 0.5:   # havok units
            continue
        if n[2] > 0:
            up += 1
        else:
            down += 1
    return (up, down)


def _scan_regress(path):
    """Does the shipped repair turn a walkable floor into a fall-through one?

    This is the invariant that matters in-game.  Counting "triangles changed"
    is misleading, because several vanilla meshes really are inconsistently
    wound at the source (the SI bridges have 242 of 324 shared edges
    disagreeing), so a changed triangle there is a repair, not damage.
    """
    from asset_convert.collision import collision as C
    from asset_convert.collision import collision_winding as W
    try:
        soups = _repair_soups(path)
    except Exception:
        return None
    if not soups:
        return None
    broke = fixed = 0
    for node, tris, groups in soups:
        vis = C._visual_tri_soup(node)
        rep, _n = W.repair_inverted_floors(list(tris), vis, groups)
        (u0, d0), (u1, d1) = _floor_state(tris), _floor_state(rep)
        if u0 and not d0 and d1 and not u1:
            broke += 1
        elif d0 and not u0 and u1 and not d1:
            fixed += 1
    return (path, fixed, broke)


def _vertex_key(v):
    """A triangle vertex rounded to 0.1 hu, so the two trees' floats match."""
    return (round(v[0], 1), round(v[1], 1), round(v[2], 1))


def _same_winding(a, b):
    """True when `a` and `b` are the same triangle in the same cyclic order."""
    return any((a[r], a[(r + 1) % 3], a[(r + 2) % 3]) == b for r in range(3))


def _truth_index(soups):
    """Vertex SET -> the cyclic orders the ground-truth tree ships for it."""
    ref = {}
    for _n, tris, _g in soups:
        for t in tris:
            ks = tuple(_vertex_key(v) for v in t)
            ref.setdefault(frozenset(ks), []).append(ks)
    return ref


def _score_repair(nsoups, ref):
    """(matched, bad, left, broke) for the repair against `ref`.

    `bad` is damage present before the repair, `left` what survived it, and
    `broke` already-correct triangles the repair reversed.
    """
    from asset_convert.collision import collision as C
    from asset_convert.collision import collision_winding as W
    matched = bad = left = broke = 0
    for node, tris, groups in nsoups:
        vis = C._visual_tri_soup(node)
        rep, _n = W.repair_inverted_floors(list(tris), vis, groups)
        for before, after in zip(tris, rep):
            kb = tuple(_vertex_key(v) for v in before)
            cands = ref.get(frozenset(kb))
            if not cands:
                continue
            matched += 1
            was_ok = any(_same_winding(kb, c) for c in cands)
            ka = tuple(_vertex_key(v) for v in after)
            now_ok = any(_same_winding(ka, c) for c in cands)
            if not was_ok:
                bad += 1
                left += 0 if now_ok else 1
            elif not now_ok:
                broke += 1
    return matched, bad, left, broke


def _scan_ab(args):
    """Exact A/B of the repair against the same mesh in another tree.

    Nehrim and Morrowind_ob re-export assets Oblivion also ships, and the
    vanilla file's winding is correct, so it is ground truth: match each
    triangle by vertex set and compare cyclic order.  Reports recall (how
    much of the real damage the repair fixes) and, critically, how many
    already-correct triangles it breaks.
    """
    rel, src, dst = args
    npath, opath = os.path.join(src, rel), os.path.join(dst, rel)
    if not (os.path.exists(npath) and os.path.exists(opath)):
        return None
    try:
        nsoups, osoups = _repair_soups(npath), _repair_soups(opath)
    except Exception:
        return None
    if not nsoups or not osoups:
        return None
    matched, bad, left, broke = _score_repair(nsoups, _truth_index(osoups))
    if not matched:
        return None
    return (rel, matched, bad, left, broke)


def _scan_floor(args):
    """Orientation of the lowest near-horizontal surface (the floor).

    Catches uniformly-reversed floors, which _scan cannot see because they
    produce no mixed up/down edge pair.  Returns (path, n_up, n_down) or None
    when the mesh has no near-horizontal collision at all.
    """
    path, converted = args
    try:
        tris = _collision_tris(path, converted)
    except Exception as exc:
        return (path, -1, 0, repr(exc)[:60])
    if not tris:
        return None

    lo = min(v[2] for t in tris for v in t)
    band = 0.5 if converted else 5.0   # havok units vs game units
    up = down = 0
    for t in tris:
        n = _normal(*t)
        if n is None or abs(n[2]) < _FLAT:
            continue
        if (sum(v[2] for v in t) / 3.0) - lo > band:
            continue
        if n[2] > 0:
            up += 1
        else:
            down += 1
    if not up and not down:
        return None
    return (path, up, down, None)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('root')
    ap.add_argument('--converted', action='store_true',
                    help='scan converted output (bhkCompressedMeshShape) '
                         'instead of TES4-format source')
    ap.add_argument('--floor-orientation', action='store_true',
                    help='report meshes whose lowest near-horizontal surface '
                         'faces DOWN (uniformly reversed floors, which the '
                         'default mixed-pair scan cannot detect)')
    ap.add_argument('--floor-regress', action='store_true',
                    help='run the SHIPPED repair and report floors it turns '
                         'from walkable into fall-through (the in-game '
                         'invariant; run this on any tree before shipping)')
    ap.add_argument('--ab', metavar='REF_TREE',
                    help='score the shipped repair against the same meshes in '
                         'REF_TREE (e.g. export/Oblivion.esm/meshes), whose '
                         'winding is ground truth.  Reports recall and how '
                         'many correct triangles the repair breaks.')
    ap.add_argument('--max', type=int, default=0,
                    help='limit to the first N meshes')
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
    if a.max:
        files = files[:a.max]
    print(f"scanning {len(files)} NIFs ({'converted' if a.converted else 'source'} format)")

    if a.ab:
        base = a.root if os.path.isdir(a.root) else os.path.dirname(a.root)
        # Both trees are addressed by the path relative to their meshes root.
        src = base
        while src and os.path.basename(src).lower() != 'meshes':
            nxt = os.path.dirname(src)
            if nxt == src:
                src = base
                break
            src = nxt
        rels = [(os.path.relpath(f, src).replace('\\', '/'), src, a.ab)
                for f in files]
        T = [0, 0, 0, 0, 0]
        with ProcessPoolExecutor(max_workers=a.workers) as ex:
            for r in ex.map(_scan_ab, rels, chunksize=4):
                if r is None:
                    continue
                rel, m, bad, left, broke = r
                T[0] += 1; T[1] += m; T[2] += bad; T[3] += left; T[4] += broke
                if left or broke:
                    print(f"  {rel}: tris={m} reversed={bad} "
                          f"left={left} broke={broke}")
        print(f"\n{T[0]} meshes scored against {a.ab}")
        print(f"  triangles matched  : {T[1]}")
        print(f"  reversed at source : {T[2]}")
        if T[2]:
            print(f"  still reversed     : {T[3]}   "
                  f"(fixed {T[2]-T[3]}, {100*(T[2]-T[3])/T[2]:.1f}% recall)")
        if T[1] > T[2]:
            print(f"  BROKEN by repair   : {T[4]}   "
                  f"({100*T[4]/(T[1]-T[2]):.2f}% of correct triangles)")
        return

    if a.floor_regress:
        fixed = broke = 0
        bad_files = []
        with ProcessPoolExecutor(max_workers=a.workers) as ex:
            for r in ex.map(_scan_regress, files, chunksize=4):
                if r is None:
                    continue
                path, f_, b_ = r
                fixed += f_
                broke += b_
                if b_:
                    bad_files.append(path)
        print(f"\nfall-through -> walkable (fixed)      : {fixed}")
        print(f"walkable -> fall-through (REGRESSION) : {broke}")
        for p in bad_files[:a.top]:
            print(f"   REGRESSED {p}")
        return

    if a.floor_orientation:
        bad, good, errors = [], 0, 0
        payload = [(f, a.converted) for f in files]
        with ProcessPoolExecutor(max_workers=a.workers) as ex:
            for r in ex.map(_scan_floor, payload, chunksize=32):
                if r is None:
                    continue
                if r[1] < 0:
                    errors += 1
                    continue
                _, up, down, _ = r
                if down and not up:
                    bad.append(r)
                elif up:
                    good += 1
        print(f"\nfloors facing DOWN (fall-through): {len(bad)}"
              f"   walkable: {good}   unreadable: {errors}")
        if bad:
            print("\n   up  down  mesh")
            for path, up, down, _ in bad[:a.top]:
                print(f"  {up:4d}  {down:4d}  {path}")
        return

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
