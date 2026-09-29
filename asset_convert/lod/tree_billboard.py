"""Render Oblivion-style tree billboard textures from SpeedTree NIFs.

Pipeline code: `lod_far_gen._far_nif_worker` calls this whenever a TREE has no
shipped billboard, so no tree ever reaches the geometry simplifier.  The CLI
wrapper for ad-hoc/batch runs is `tools/lod/render_tree_billboard.py`.

Oblivion ships a pre-rendered front view of each tree as
`textures\\tes4\\trees\\billboards\\<stem>.dds`, and `lod_far_gen` builds its
crossed-quad LOD card straight from that image.  A tree WITHOUT one falls back
to decimating the full canopy, which is ruinous: `dementiatree10l` came out at
52 KB and, at 8,006 placements inside one level-16 tile, that single model
contributed most of a 663 MB tile.  Censused across the whole load order, 162
tree models (58,997 placements, 19% of all tree placements) have no billboard.

This renders the missing ones the way Oblivion's own tool did: an orthographic
front projection of the tree, sampling each shape's own diffuse texture with
its alpha, over a transparent background.  Output matches what
`generate_tree_billboard_far` expects — v=0 at the top of the tree, centered on
the canopy in X and standing on the trunk base in Z.

Usage:
    python -m tools.lod.render_tree_billboard --all [--dry-run]
    python -m tools.lod.render_tree_billboard --plugin Oblivion.esm [--size 256]
    python -m tools.lod.render_tree_billboard --nif <tree.nif> --out <name.dds>
"""

import struct
from pathlib import Path

import numpy as np
from PIL import Image

from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()
from pyffi.formats.nif import NifFormat
from asset_convert.game_paths import current_namespace, win_join
from asset_convert.nif.tex_paths import authored_rel

BS = chr(92)


def billboard_dir() -> str:
    """Billboard folder under the ACTIVE game namespace."""
    return current_namespace() + BS + 'trees' + BS + 'billboards'

# Baked lighting.  The LOD card is drawn UNLIT in game, so all of a distant
# tree's shading has to live in this texture — a flat sample reads as a
# cardboard cut-out next to vanilla's billboards.
_KEY_LIGHT = np.array([0.45, -0.55, 0.70])
_KEY_LIGHT = _KEY_LIGHT / np.linalg.norm(_KEY_LIGHT)
_AMBIENT = 0.55       # floor, so foliage never crushes to black
_CANOPY_AO = 0.22     # gentle top-to-bottom gradient for self-shadowing


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


def _as_text(v) -> str:
    """Decode a PyFFI string field.

    These come back as `bytes`, and `str(b'...')` yields the literal
    "b'textures\\\\...'" — a path that can never resolve.  That silently sent
    every shape to the flat-color fallback and rendered plain green cards.
    """
    if v is None:
        return ''
    if isinstance(v, bytes):
        return v.decode('latin1', 'replace').strip()
    if isinstance(v, str):
        return v.strip()
    inner = getattr(v, 'value', None)
    if isinstance(inner, bytes):
        return inner.decode('latin1', 'replace').strip()
    if isinstance(inner, str):
        return inner.strip()
    return str(v).strip()


def _diffuse_of(shape):
    """Game-relative diffuse path for a shape, or ''."""
    props = list(getattr(shape, 'bs_properties', []) or [])
    props += list(getattr(shape, 'properties', []) or [])
    for prop in props:
        if prop is None:
            continue
        ts = getattr(prop, 'texture_set', None)
        if ts is not None:
            try:
                got = _as_text(ts.textures[0])
                if got:
                    return got
            except Exception:
                pass
        src = getattr(prop, 'source_texture', None)
        if src is not None:
            try:
                got = _as_text(getattr(src, 'file_name', None))
                if got:
                    return got
            except Exception:
                pass
    return ''


def collect_textured_geometry(nif_data):
    """[(verts world, tris, uvs, diffuse_rel)] for every textured shape."""
    out = []

    def walk(node, xform):
        m = xform @ _node_transform(node)
        if isinstance(node, NifFormat.NiTriShape):
            d = getattr(node, 'data', None)
            if d is not None and d.num_vertices >= 3 and d.num_triangles:
                v = np.array([(p.x, p.y, p.z) for p in d.vertices],
                             dtype=np.float64)
                v = (m[:3, :3] @ v.T).T + m[:3, 3]
                t = np.array([(x.v_1, x.v_2, x.v_3) for x in d.triangles],
                             dtype=np.int32)
                uv = None
                try:
                    if len(d.uv_sets) and len(d.uv_sets[0]) == d.num_vertices:
                        uv = np.array([(u.u, u.v) for u in d.uv_sets[0]],
                                      dtype=np.float64)
                except Exception:
                    pass
                out.append((v, t, uv, _diffuse_of(node)))
        for c in getattr(node, 'children', []) or []:
            if c is not None:
                walk(c, m)

    for root in nif_data.roots:
        if root is not None:
            walk(root, np.eye(4))
    return out


def _load_texture(rel, tex_roots):
    """Load a game-relative diffuse as RGBA, or None."""
    if not rel:
        return None
    r = authored_rel(str(rel))[0]
    for root in tex_roots:
        p = win_join(root, r)
        if p.exists():
            try:
                return Image.open(p).convert('RGBA')
            except Exception:
                return None
    return None


def render_billboard(nif_path: Path, tex_roots, size=256, supersample=2):
    """Orthographic front render as RGBA, or None if nothing was drawn."""
    data = NifFormat.Data()
    with open(nif_path, 'rb') as f:
        data.read(f)
    shapes = collect_textured_geometry(data)
    if not shapes:
        return None

    allv = np.concatenate([s[0] for s in shapes])
    lo, hi = allv.min(axis=0), allv.max(axis=0)
    width = max(hi[0] - lo[0], hi[1] - lo[1])
    height = hi[2] - lo[2]
    span = max(width, height)
    if span <= 0:
        return None
    cx = (lo[0] + hi[0]) / 2.0

    S = int(size * supersample)
    rgb = np.zeros((S, S, 3), dtype=np.float64)
    alpha = np.zeros((S, S), dtype=np.float64)
    # No depth buffer: triangles are drawn back-to-front, so the last write at
    # a pixel is the nearest one.

    missing = []
    for verts, tris, uvs, diff in shapes:
        tex = _load_texture(diff, tex_roots)
        if tex is not None:
            ta = np.asarray(tex, dtype=np.float64) / 255.0
        else:
            # No flat-color stand-in: a billboard drawn from a placeholder is
            # a green blob, and doing that silently is how the first version of
            # this tool produced 162 useless cards.  Skip the shape and report.
            missing.append(diff or '(no texture set)')
            continue
        if uvs is None:
            missing.append((diff or '?') + ' (no UVs)')
            continue
        # X -> screen x, Z -> screen y (v=0 at the top), Y -> depth
        sx = (verts[:, 0] - cx) / span * S + S / 2.0
        sy = (hi[2] - verts[:, 2]) / span * S
        dep = verts[:, 1]
        # Two-sided lambert per face: leaf cards are double-sided, so the sign
        # of the normal carries no meaning and only its angle to the key does.
        p0 = verts[tris[:, 0]]
        p1 = verts[tris[:, 1]]
        p2 = verts[tris[:, 2]]
        fn = np.cross(p1 - p0, p2 - p0)
        fl = np.linalg.norm(fn, axis=1)
        fn = fn / np.maximum(fl, 1e-12)[:, None]
        lam = np.abs(fn @ _KEY_LIGHT)
        # ---- vectorised rasteriser ---------------------------------
        # Rasterising one triangle per Python iteration cost ~0.7 s of a ~1.0 s
        # render (1,854 triangles for one tree, hundreds of trees in a load
        # order).  This does the whole shape in a handful of array ops.
        #
        # Bucketing triangles onto a shared padded grid was tried first and was
        # WORSE (4 s): leaf cards vary hugely in size, so padding every box to
        # the bucket's square inflated 9M real pixels to 33.5M.  Instead build
        # a flat list of (pixel, triangle) candidate pairs directly — one entry
        # per pixel actually inside a bounding box, nothing padded — and scatter
        # them in one pass.  Triangles are drawn back-to-front so the last
        # write at a pixel is the nearest, which resolves occlusion without a
        # per-pixel depth compare.
        order = np.argsort(-dep[tris].mean(axis=1))   # far -> near
        t_ord = tris[order]
        lam_o = lam[order]
        ax = sx[t_ord[:, 0]]; ay = sy[t_ord[:, 0]]
        bx = sx[t_ord[:, 1]]; by = sy[t_ord[:, 1]]
        cxx = sx[t_ord[:, 2]]; cyy = sy[t_ord[:, 2]]
        den = (by - cyy) * (ax - cxx) + (cxx - bx) * (ay - cyy)
        x_lo = np.clip(np.floor(np.minimum(np.minimum(ax, bx), cxx)), 0, S - 1).astype(np.int64)
        x_hi = np.clip(np.ceil(np.maximum(np.maximum(ax, bx), cxx)), 0, S - 1).astype(np.int64)
        y_lo = np.clip(np.floor(np.minimum(np.minimum(ay, by), cyy)), 0, S - 1).astype(np.int64)
        y_hi = np.clip(np.ceil(np.maximum(np.maximum(ay, by), cyy)), 0, S - 1).astype(np.int64)
        wid = x_hi - x_lo + 1
        hgt = y_hi - y_lo + 1
        live = (np.abs(den) > 1e-12) & (wid > 0) & (hgt > 0)
        th, tw = ta.shape[0], ta.shape[1]
        u_t = uvs[:, 0]
        v_t = uvs[:, 1]
        idx_all = np.nonzero(live)[0]
        if not len(idx_all):
            continue
        # Cap the working set per pass so peak memory stays bounded regardless
        # of how dense the canopy is.
        area = (wid * hgt).astype(np.int64)
        budget = 4_000_000
        starts = []
        run = 0
        cur = []
        for i in idx_all:
            if run and run + area[i] > budget:
                starts.append(np.array(cur, dtype=np.int64)); cur = []; run = 0
            cur.append(i); run += area[i]
        if cur:
            starts.append(np.array(cur, dtype=np.int64))
        for chunk in starts:
            w_c = wid[chunk]
            h_c = hgt[chunk]
            n_px = (w_c * h_c).astype(np.int64)
            # ONE repeat, for the triangle index; every other per-pixel value
            # is then a cheap gather off it.  Repeating each coordinate array
            # separately cost 2.4 s of a 9.5 s render.
            tri_of = np.repeat(np.arange(len(chunk), dtype=np.int64), n_px)
            # Offset of each candidate pixel within its own bounding box.
            base = np.concatenate([[0], np.cumsum(n_px)[:-1]])
            within = np.arange(n_px.sum(), dtype=np.int64) - base[tri_of]
            wrow = w_c[tri_of]
            ix = x_lo[chunk][tri_of] + (within % wrow)
            iy = y_lo[chunk][tri_of] + (within // wrow)
            px = ix + 0.5
            py = iy + 0.5
            a0 = ax[chunk][tri_of]; b0 = ay[chunk][tri_of]
            a1 = bx[chunk][tri_of]; b1 = by[chunk][tri_of]
            a2 = cxx[chunk][tri_of]; b2 = cyy[chunk][tri_of]
            dd = den[chunk][tri_of]
            w0 = ((b1 - b2) * (px - a2) + (a2 - a1) * (py - b2)) / dd
            w1 = ((b2 - b0) * (px - a2) + (a0 - a2) * (py - b2)) / dd
            w2 = 1.0 - w0 - w1
            keep = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
            if not keep.any():
                continue
            w0 = w0[keep]; w1 = w1[keep]; w2 = w2[keep]
            ix = ix[keep]; iy = iy[keep]; py = py[keep]
            tsel = chunk[tri_of[keep]]
            uu = (w0 * u_t[t_ord[tsel, 0]] + w1 * u_t[t_ord[tsel, 1]]
                  + w2 * u_t[t_ord[tsel, 2]])
            vv = (w0 * v_t[t_ord[tsel, 0]] + w1 * v_t[t_ord[tsel, 1]]
                  + w2 * v_t[t_ord[tsel, 2]])
            txi = np.clip((uu % 1.0) * (tw - 1), 0, tw - 1).astype(np.int32)
            tyi = np.clip((vv % 1.0) * (th - 1), 0, th - 1).astype(np.int32)
            col = ta[tyi, txi]
            # Alpha-tested, exactly as the game draws leaf cards.
            solid = col[:, 3] > 0.35
            if not solid.any():
                continue
            col = col[solid]
            ix = ix[solid]; iy = iy[solid]; py = py[solid]
            tsel = tsel[solid]
            # Baked lighting: the card renders UNLIT in game, so all of a
            # distant tree's shading has to live in this texture.
            shade = _AMBIENT + (1.0 - _AMBIENT) * lam_o[tsel]
            shade = shade * (1.0 - _CANOPY_AO * np.clip(py / S, 0.0, 1.0))
            lin = iy * S + ix
            rgb.reshape(-1, 3)[lin] = col[:, :3] * shade[:, None]
            alpha.reshape(-1)[lin] = 1.0

    if missing:
        print('    unresolved texture(s): %s' % '; '.join(sorted(set(missing))[:3]),
              flush=True)
    if alpha.max() <= 0:
        return None
    out = np.concatenate([rgb, alpha[..., None]], axis=2)
    img = Image.fromarray((np.clip(out, 0, 1) * 255).astype(np.uint8), 'RGBA')
    if supersample > 1:
        img = img.resize((int(size), int(size)), Image.LANCZOS)
    return img


def write_dds_rgba(img: Image.Image, path: Path):
    """Write the billboard as DXT5, matching what Oblivion ships.

    Vanilla's own billboards are DXT5 at 512x512 (341 KB); an uncompressed
    BGRA8 copy of the same image is 1 MB, so 144 rendered trees would add
    ~150 MB of textures for no visual gain.  DXT5 carries the 8-bit alpha the
    leaf cut-out needs — DXT1's 1-bit alpha would leave hard fringes.  Falls
    back to uncompressed if the encoder is unavailable, since a large texture
    still beats no billboard at all (the tree would be decimated instead).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        img.convert('RGBA').save(path, format='DDS', pixel_format='DXT5')
        return
    except Exception:
        pass
    w, h = img.size
    a = np.asarray(img.convert('RGBA'))
    bgra = a[..., [2, 1, 0, 3]].tobytes()
    hdr = b'DDS ' + struct.pack('<I', 124)
    hdr += struct.pack('<I', 0x1 | 0x2 | 0x4 | 0x1000 | 0x8)
    hdr += struct.pack('<II', h, w)
    hdr += struct.pack('<I', w * 4)
    hdr += struct.pack('<II', 0, 0)
    hdr += b'\x00' * 44
    hdr += struct.pack('<II', 32, 0x41)
    hdr += struct.pack('<I', 0)
    hdr += struct.pack('<I', 32)
    hdr += struct.pack('<IIII', 0x00ff0000, 0x0000ff00, 0x000000ff, 0xff000000)
    hdr += struct.pack('<I', 0x1000)
    hdr += struct.pack('<IIII', 0, 0, 0, 0)
    path.write_bytes(hdr + bgra)


