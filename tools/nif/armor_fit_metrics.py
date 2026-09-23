"""Armor retarget fit metrics: edge distortion, body clearance, explosions.

Compares a source Oblivion armor NIF against its converted Skyrim NIF and
reports, per matching geometry block:

  * edge-length failures  — % of triangle edges whose length changed more
    than a threshold (mesh integrity / stretching);
  * clearance preservation — each vertex's signed distance to the OB body
    surface (source) vs to the FITTED body surface (converted).  The wrap
    retarget should preserve this by construction; large negative deltas
    mean new body clipping was introduced;
  * penetration            — % of converted verts sunk below the Skyrim body
    surface deeper than they were authored below the OB body;
  * max displacement       — explosion detector.

Usage:
  python -m tools.nif.armor_fit_metrics <src.nif> <converted.nif> [--gender male]
  python -m tools.nif.armor_fit_metrics --pair export/...cuirass.nif temp/out.nif
  --morrowind measures an assembled Morrowind source against the Morrowind field.
"""

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()
from pyffi.formats.nif import NifFormat

from asset_convert.character.body_wrap import get_field
from asset_convert.character.wrap_mesh import (block_name, closest_point_on_triangles,
                                               geom_triangles, geom_world,
                                               iter_skinned_geoms)

_SPLICE_PREFIXES = ('MaleUnderwear', 'FemaleUnderwear', 'HandMale',
                    'HandFemale', 'MaleFeet', 'FemaleFeet', 'BodyFill')


def _load_blocks(path):
    data = NifFormat.Data()
    with open(path, 'rb') as f:
        data.read(f)
    out = {}
    for block, skel_root in iter_skinned_geoms(data):
        name = block_name(block)
        verts, _G = geom_world(block, skel_root)
        tris = geom_triangles(block)
        out.setdefault(name, []).append((verts, tris))
    return out


def _signed_clearance(pts, surf_v, surf_t, tree, tri_n, k=8):
    """Signed distance of pts to a triangle soup (positive = outside)."""
    k = min(k, len(surf_t))
    _, cand = tree.query(pts, k=k)
    if k == 1:
        cand = cand[:, None]
    a = surf_v[surf_t[cand, 0]]
    b = surf_v[surf_t[cand, 1]]
    c = surf_v[surf_t[cand, 2]]
    cp = closest_point_on_triangles(pts[:, None, :], a, b, c)
    d = np.linalg.norm(cp - pts[:, None, :], axis=2)
    best = np.argmin(d, axis=1)
    rows = np.arange(len(pts))
    cp_b = cp[rows, best]
    n_b = tri_n[cand[rows, best]]
    off = pts - cp_b
    sign = np.sign(np.einsum('pi,pi->p', off, n_b))
    sign[sign == 0] = 1.0
    return d[rows, best] * sign


def _surface(verts, tris):
    from scipy.spatial import cKDTree
    n = np.cross(verts[tris[:, 1]] - verts[tris[:, 0]],
                 verts[tris[:, 2]] - verts[tris[:, 0]])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    return cKDTree(verts[tris].mean(axis=1)), n


def _parse_args():
    """The command line."""
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('src')
    ap.add_argument('out')
    ap.add_argument('--gender', choices=['male', 'female'], default=None,
                    help='default: from path (/f/ = female)')
    ap.add_argument('--edge-tol', type=float, default=0.15)
    ap.add_argument('--weight', type=int, choices=[0, 1], default=0,
                    help='which weight-slider body target the converted NIF '
                         'was fitted to (_0 or _1)')
    ap.add_argument('--morrowind', action='store_true',
                    help='the source is an assembled Morrowind NIF: measure against '
                         'the Morrowind reference body field')
    return ap.parse_args()


def _edge_report(sv, ov, st, tol):
    """(text, edges measured, edges over `tol`) for one source/converted pair.

    hf is the per-triangle SPREAD of the three edge stretch ratios: smooth
    reshaping keeps it near zero, crumpling or shearing makes it large.
    """
    if not len(st):
        return '', 0, 0
    e = np.vstack([st[:, [0, 1]], st[:, [1, 2]], st[:, [0, 2]]])
    l0 = np.linalg.norm(sv[e[:, 0]] - sv[e[:, 1]], axis=1)
    l1 = np.linalg.norm(ov[e[:, 0]] - ov[e[:, 1]], axis=1)
    ok = l0 > 0.01
    ratio = np.abs(l1[ok] / l0[ok] - 1.0)
    bad = int((ratio > tol).sum())
    r_tri = np.where(ok, l1 / np.maximum(l0, 0.01), 1.0).reshape(3, -1).T
    hf = r_tri.max(axis=1) - r_tri.min(axis=1)
    text = (f'edges>{tol*100:.0f}%: {bad}/{ok.sum()} ({bad/max(ok.sum(),1)*100:5.2f}%) '
            f'p95={np.percentile(ratio, 95)*100:5.1f}% max={ratio.max()*100:5.1f}% '
            f'hf_mean={hf.mean()*100:4.1f}% hf_p95={np.percentile(hf, 95)*100:5.1f}%  ')
    return text, int(ok.sum()), bad


def _clearance_report(sv, ov, field, surfaces, weight):
    """Clearance change from the source body to the fitted body, as text."""
    (src_tree, src_tri_n), (dst_tree, dst_tri_n) = surfaces
    c_src = _signed_clearance(sv, field.src, field.tris, src_tree, src_tri_n)
    c_out = _signed_clearance(ov, field.dst[weight], field.tris, dst_tree, dst_tri_n)
    dc = c_out - c_src
    new_clip = (dc < -0.3) & (c_out < -0.1)
    return (f'clearance dmean={dc.mean():+5.2f} dmin={dc.min():+5.2f} '
            f'newclip={new_clip.sum()}/{len(sv)} ({new_clip.mean()*100:4.1f}%)')


def _pairs(src_blocks, out_blocks):
    """(name, source verts, source tris, converted verts) per matched geometry."""
    for name, src_list in sorted(src_blocks.items()):
        if name.startswith(_SPLICE_PREFIXES):
            continue
        for sv, st in src_list:
            match = [ov for ov, _ot in out_blocks.get(name, []) if len(ov) == len(sv)]
            if match:
                yield name, sv, st, match[0]


def main():
    """Print the per-geometry fit metrics and the edge-failure total."""
    args = _parse_args()
    female = (args.gender == 'female') if args.gender else \
        ('/f/' in args.src.replace('\\', '/').lower())
    field = get_field(female, args.morrowind)
    surfaces = None
    if field is None:
        print('NOTE: no wrap field — clearance metrics unavailable')
    else:
        surfaces = (_surface(field.src, field.tris),
                    _surface(field.dst[args.weight], field.tris))
    grand_edges = grand_bad = 0
    for name, sv, st, ov in _pairs(_load_blocks(args.src), _load_blocks(args.out)):
        text, edges, bad = _edge_report(sv, ov, st, args.edge_tol)
        grand_edges, grand_bad = grand_edges + edges, grand_bad + bad
        line = f'{name:32s} verts={len(sv):5d} {text}'
        line += f'maxdisp={np.linalg.norm(ov - sv, axis=1).max():6.2f} '
        if surfaces is not None:
            line += _clearance_report(sv, ov, field, surfaces, args.weight)
        print(line)
    if grand_edges:
        print(f'{"TOTAL":32s} edges>{args.edge_tol*100:.0f}%: '
              f'{grand_bad}/{grand_edges} '
              f'({grand_bad/grand_edges*100:.2f}%)')


if __name__ == '__main__':
    main()
