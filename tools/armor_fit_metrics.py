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
  python -m tools.armor_fit_metrics <src.nif> <converted.nif> [--gender male]
  python -m tools.armor_fit_metrics --pair export/...cuirass.nif temp/out.nif
"""

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from asset_convert import pyffi_monkey_patch as _patch  # noqa: F401
from pyffi.formats.nif import NifFormat

from asset_convert.body_wrap import (get_field, closest_point_on_triangles,
                                     _iter_skinned_geoms, _geom_world,
                                     _geom_triangles, _block_name)

_SPLICE_PREFIXES = ('MaleUnderwear', 'FemaleUnderwear', 'HandMale',
                    'HandFemale', 'MaleFeet', 'FemaleFeet', 'BodyFill')


def _load_blocks(path):
    data = NifFormat.Data()
    with open(path, 'rb') as f:
        data.read(f)
    out = {}
    for block, skel_root in _iter_skinned_geoms(data):
        name = _block_name(block)
        verts, _G = _geom_world(block, skel_root)
        tris = _geom_triangles(block)
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


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('src')
    ap.add_argument('out')
    ap.add_argument('--gender', choices=['male', 'female'], default=None,
                    help='default: from path (/f/ = female)')
    ap.add_argument('--edge-tol', type=float, default=0.15)
    ap.add_argument('--weight', type=int, choices=[0, 1], default=0,
                    help='which weight-slider body target the converted NIF '
                         'was fitted to (_0 or _1)')
    args = ap.parse_args()

    female = (args.gender == 'female') if args.gender else \
        ('/f/' in args.src.replace('\\', '/').lower())
    field = get_field(female)
    if field is None:
        print('NOTE: no wrap field — clearance metrics unavailable')

    src_blocks = _load_blocks(args.src)
    out_blocks = _load_blocks(args.out)

    if field is not None:
        src_tree, src_tri_n = _surface(field.src, field.tris)
        dst_tree, dst_tri_n = _surface(field.dst[args.weight], field.tris)

    grand_edges = grand_bad = 0
    for name, src_list in sorted(src_blocks.items()):
        if name.startswith(_SPLICE_PREFIXES):
            continue
        out_list = out_blocks.get(name, [])
        for sv, st in src_list:
            match = [(ov, ot) for ov, ot in out_list if len(ov) == len(sv)]
            if not match:
                continue
            ov, ot = match[0]

            disp = np.linalg.norm(ov - sv, axis=1)

            # edge failures
            e = np.vstack([st[:, [0, 1]], st[:, [1, 2]], st[:, [0, 2]]]) \
                if len(st) else np.zeros((0, 2), dtype=int)
            line = f'{name:32s} verts={len(sv):5d} '
            if len(e):
                l0 = np.linalg.norm(sv[e[:, 0]] - sv[e[:, 1]], axis=1)
                l1 = np.linalg.norm(ov[e[:, 0]] - ov[e[:, 1]], axis=1)
                ok = l0 > 0.01
                ratio = np.abs(l1[ok] / l0[ok] - 1.0)
                bad = int((ratio > args.edge_tol).sum())
                grand_edges += int(ok.sum())
                grand_bad += bad
                # High-frequency distortion: per-triangle SPREAD of the three
                # edge stretch ratios.  Smooth reshaping (bigger body) keeps
                # this near zero; crumpling/shearing makes it large.
                r_full = np.where(l0 > 0.01, l1 / np.maximum(l0, 0.01), 1.0)
                r_tri = r_full.reshape(3, -1).T          # (T, 3)
                hf = r_tri.max(axis=1) - r_tri.min(axis=1)
                line += (f'edges>{args.edge_tol*100:.0f}%: '
                         f'{bad}/{ok.sum()} ({bad/max(ok.sum(),1)*100:5.2f}%) '
                         f'p95={np.percentile(ratio, 95)*100:5.1f}% '
                         f'max={ratio.max()*100:5.1f}% '
                         f'hf_mean={hf.mean()*100:4.1f}% '
                         f'hf_p95={np.percentile(hf, 95)*100:5.1f}%  ')
            line += f'maxdisp={disp.max():6.2f} '

            if field is not None:
                c_src = _signed_clearance(sv, field.src, field.tris,
                                          src_tree, src_tri_n)
                c_out = _signed_clearance(ov, field.dst[args.weight],
                                          field.tris, dst_tree, dst_tri_n)
                dc = c_out - c_src
                new_clip = (dc < -0.3) & (c_out < -0.1)
                line += (f'clearance dmean={dc.mean():+5.2f} '
                         f'dmin={dc.min():+5.2f} '
                         f'newclip={new_clip.sum()}/{len(sv)} '
                         f'({new_clip.mean()*100:4.1f}%)')
            print(line)

    if grand_edges:
        print(f'{"TOTAL":32s} edges>{args.edge_tol*100:.0f}%: '
              f'{grand_bad}/{grand_edges} '
              f'({grand_bad/grand_edges*100:.2f}%)')


if __name__ == '__main__':
    main()
