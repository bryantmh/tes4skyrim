"""Render a NIF's geometry to PNG for LOD decimation validation.

Renders all NiTriShapes (with accumulated node transforms) as flat-shaded
filled triangles (painter's algorithm), from two orbit views.  With --far it
renders the source NIF and its freshly-decimated LOD side by side so holes /
silhouette damage from the simplifier are immediately visible.

Usage:
    python -m tools.nif_lod_preview <src.nif> [--far] [--ratio 0.07]
        [--out temp/preview.png] [--size 700]
"""

import argparse
import io
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from asset_convert import pyffi_monkey_patch  # noqa: F401
from pyffi.formats.nif import NifFormat


def _node_transform(n):
    m = np.eye(4)
    try:
        r = n.rotation
        m[:3, :3] = np.array([[r.m_11, r.m_21, r.m_31],
                              [r.m_12, r.m_22, r.m_32],
                              [r.m_13, r.m_23, r.m_33]]) * n.scale
        t = n.translation
        m[:3, 3] = (t.x, t.y, t.z)
    except Exception:
        pass
    return m


def collect_geometry(nif_data):
    """Return (verts (N,3), tris (M,3)) merged across all shapes, world space."""
    all_v = []
    all_t = []
    base = 0

    def walk(node, xform):
        nonlocal base
        if node is None:
            return
        m = xform @ _node_transform(node)
        d = getattr(node, 'data', None)
        if d is not None and hasattr(d, 'vertices') and getattr(d, 'num_triangles', 0):
            v = np.array([(p.x, p.y, p.z, 1.0) for p in d.vertices], np.float64)
            v = (m @ v.T).T[:, :3]
            t = np.array([(t_.v_1, t_.v_2, t_.v_3) for t_ in d.triangles], np.int64)
            all_v.append(v)
            all_t.append(t + base)
            base += len(v)
        for ch in getattr(node, 'children', []) or []:
            walk(ch, m)

    for root in nif_data.roots:
        walk(root, np.eye(4))

    if not all_v:
        return np.zeros((0, 3)), np.zeros((0, 3), np.int64)
    return np.vstack(all_v), np.vstack(all_t)


def render(verts, tris, size=700, yaw_deg=30.0, pitch_deg=18.0):
    """Painter's-algorithm flat-shaded render; returns PIL Image."""
    img = Image.new('RGB', (size, size), (24, 26, 30))
    if not len(verts) or not len(tris):
        return img
    dr = ImageDraw.Draw(img)

    yaw = math.radians(yaw_deg)
    pitch = math.radians(pitch_deg)
    ry = np.array([[math.cos(yaw), -math.sin(yaw), 0],
                   [math.sin(yaw), math.cos(yaw), 0],
                   [0, 0, 1]])
    rp = np.array([[1, 0, 0],
                   [0, math.cos(pitch), -math.sin(pitch)],
                   [0, math.sin(pitch), math.cos(pitch)]])
    v = verts @ ry.T
    # view: x → screen x, z → screen -y, y → depth
    cam = np.stack([v[:, 0], v[:, 1], v[:, 2]], axis=1) @ rp.T
    ctr = (cam.min(axis=0) + cam.max(axis=0)) / 2
    cam -= ctr
    ext = np.abs(cam).max() or 1.0
    scale = (size * 0.46) / ext
    sx = cam[:, 0] * scale + size / 2
    sy = -cam[:, 2] * scale + size / 2
    depth = cam[:, 1]

    p0, p1, p2 = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    fn = np.cross(p1 - p0, p2 - p0)
    nl = np.linalg.norm(fn, axis=1)
    fn = fn / np.maximum(nl, 1e-12)[:, None]
    light = np.array([0.5, -0.6, 0.62])
    light /= np.linalg.norm(light)
    lam = np.abs(fn @ light)  # two-sided

    order = np.argsort(-(depth[tris[:, 0]] + depth[tris[:, 1]] + depth[tris[:, 2]]))
    for fi in order:
        a, b, c = tris[fi]
        g = int(50 + lam[fi] * 185)
        dr.polygon([(sx[a], sy[a]), (sx[b], sy[b]), (sx[c], sy[c])],
                   fill=(g, g, min(255, g + 8)))
    return img


def _label(img, text):
    d = ImageDraw.Draw(img)
    d.text((8, 6), text, fill=(240, 240, 200))
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('nif')
    ap.add_argument('--far', action='store_true',
                    help='also decimate in-memory and render side by side')
    ap.add_argument('--ratio', type=float, default=None,
                    help='decimation ratio override')
    ap.add_argument('--out', default='temp/nif_lod_preview.png')
    ap.add_argument('--size', type=int, default=700)
    ap.add_argument('--views', type=str, default='30,120',
                    help='comma-separated yaw angles')
    args = ap.parse_args()

    src = Path(args.nif)
    nif = NifFormat.Data()
    with open(src, 'rb') as fh:
        nif.read(fh)
    v, t = collect_geometry(nif)
    print(f"{src.name}: {len(v)} verts, {len(t)} tris")

    yaws = [float(x) for x in args.views.split(',')]
    panels = [_label(render(v, t, args.size, yaw), f"src {len(t)} tris")
              for yaw in yaws]

    if args.far:
        from asset_convert import lod_far_gen
        kw = {}
        if args.ratio is not None:
            kw['decimate_ratio'] = args.ratio
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / (src.stem + '_far.nif')
            ok = lod_far_gen.generate_far_nif(src, dst, **kw)
            if not ok:
                print("decimation FAILED/skipped")
            else:
                nif2 = NifFormat.Data()
                with open(dst, 'rb') as fh:
                    nif2.read(fh)
                v2, t2 = collect_geometry(nif2)
                print(f"far: {len(v2)} verts, {len(t2)} tris "
                      f"({100.0 * len(t2) / max(len(t), 1):.1f}% of src tris)")
                panels += [_label(render(v2, t2, args.size, yaw),
                                  f"far {len(t2)} tris") for yaw in yaws]

    W = args.size
    canvas = Image.new('RGB', (W * len(panels) + 4 * (len(panels) - 1), W), (0, 0, 0))
    for i, p in enumerate(panels):
        canvas.paste(p, (i * (W + 4), 0))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
    print(f"wrote {out}")


if __name__ == '__main__':
    main()
