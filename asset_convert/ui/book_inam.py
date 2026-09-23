r"""Inventory-art (INAM) generator for TES4 books, notes and scrolls.

Skyrim's BookMenu does not render the world model when a book is opened: it
renders the BOOK record's INAM inventory-art mesh, which must be one of the
vanilla rigged reading templates — a skinned page-turn skeleton driven by a
behavior graph (BSBehaviorGraphExtraData -> Book01Project.hkx) plus a special
4-vert "PageText" NiTriShape (NiStringExtraData 'Keep' = "NiHide") that the
engine replaces with the rendered page text.  A static mesh there means the
book opens invisible with no text.  Oblivion book meshes are all static, so a
converted mesh can never serve as INAM directly.

Strategy (keeps the template's UVs and rig untouched — animation guaranteed):
  1. Calibrate the Oblivion book mesh: find the front-cover / spine / page-edge
     UV islands and fit an affine map from normalized cover coordinates to the
     Oblivion texture layout.  (Layouts differ per family: Octavo has the spine
     on the left edge, Quarto/Folio in the middle — hence per-mesh fitting.)
  2. Calibrate the Skyrim template (BookSkyrim01 for bound books, Note02 for
     flat sheets) the same way.
  3. Bake the Oblivion textures (cover + normal map) into a new atlas laid out
     in the *template's* UV space, by composing dst-uv -> normalized cover
     coords -> src-uv per region.
  4. Re-emit the template NIF with its cover texture set pointed at the baked
     atlas.  Pages keep the vanilla Skyrim paper textures (loaded from the
     game's own BSAs).

One INAM mesh + texture pair is generated per *distinct* TES4 book model
(~38 for Oblivion.esm); tes5_import/record_types/equipment.py synthesizes one
shared STAT per model pointing at meshes\tes4\clutter\books\inv\<base>.nif.

CLI:
    python -m asset_convert.ui.book_inam Oblivion.esm [--extract-dir export]
        [--output-dir output] [--templates-dir <explicit meshes tree>]
        [--skyrim-data "C:/.../Skyrim Special Edition/Data"] [--workers N]

Templates are auto-extracted from the SSE BSAs by default (skyrim_assets).
"""

from asset_convert.game_paths import current_namespace
import argparse
import os
import struct
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import cpu_count
from pathlib import Path

import numpy as np

if not hasattr(time, 'clock'):
    time.clock = time.perf_counter  # pyffi uses the removed time.clock
from pyffi.formats.nif import NifFormat


# Shared-folder resolution -- see output_layout. An imported mod's plugins keep
# records in `export/<Mod>/<plugin>/` but share `export/<Mod>/` for assets.

def record_dir(extract_dir, plugin):
    try:
        from output_layout import record_dir
        return record_dir(extract_dir, plugin)
    except ImportError:
        return Path(extract_dir) / plugin


def _asset_root(extract_dir, plugin):
    try:
        from output_layout import asset_root
        return asset_root(extract_dir, plugin)
    except ImportError:
        return Path(extract_dir) / plugin


def _out_root(output_dir, plugin, extract_dir=None):
    try:
        from output_layout import plugin_out_root
        return plugin_out_root(output_dir, plugin,
                               str(extract_dir) if extract_dir else None)
    except ImportError:
        return Path(output_dir) / plugin


CANVAS = 512  # baked atlas size (matches vanilla LargeBookSkyrim.dds density)

BOOK_TEMPLATE = 'meshes\\clutter\\books\\book02\\character assets\\bookskyrim01.nif'
NOTE_TEMPLATE = 'meshes\\clutter\\books\\note01\\note02.nif'

# Template texture basenames whose texture set gets retargeted at the atlas.
# Pages (largebookpaper01.dds) intentionally stay vanilla.
BOOK_COVER_TEXES = ('largebookskyrim.dds', 'largebookskyrimback.dds')
NOTE_SHEET_TEXES = ('largenote02.dds',)

FLAT_NORMAL = (128, 128, 255, 255)  # RGBA flat tangent-space normal


# ---------------------------------------------------------------------------
# NIF geometry access
# ---------------------------------------------------------------------------

def inv_mesh_dir() -> str:
    """Inventory-art mesh folder under the ACTIVE namespace."""
    return 'meshes\\' + current_namespace() + '\\clutter\\books\\inv'


def inv_tex_dir() -> str:
    """Inventory-art texture folder under the ACTIVE namespace."""
    return 'textures\\' + current_namespace() + '\\clutter\\books\\inv'


def read_nif(source):
    # sse_nif handles LE, SSE (BSA-sourced templates) and Oblivion formats;
    # SSE templates come back as complete LE graphs (partitions included) so
    # emit_inam_nif can write them straight back out.
    from asset_convert.nif.sse_nif import read_nif
    return read_nif(source)


def _shape_textures(shape):
    """Texture paths of a trishape for both TES4 (NiTexturingProperty) and
    TES5 (BSLightingShaderProperty/BSShaderTextureSet) conventions."""
    texs = []
    props = list(getattr(shape, 'properties', []) or [])
    props += list(getattr(shape, 'bs_properties', []) or [])
    for prop in props + [getattr(shape, 'shader_property', None)]:
        if prop is None:
            continue
        ts = getattr(prop, 'texture_set', None)
        if ts is not None:
            texs += [t.decode('ascii', 'replace') for t in ts.textures if t]
        bt = getattr(prop, 'base_texture', None)
        if bt is not None and getattr(bt, 'source', None):
            texs.append(bt.source.file_name.decode('ascii', 'replace'))
    return texs


def _local_transform(block):
    """(rot 3x3, trans 3, scale) of an NiAVObject, row-vector convention."""
    r = block.rotation
    rot = np.array([[r.m_11, r.m_12, r.m_13],
                    [r.m_21, r.m_22, r.m_23],
                    [r.m_31, r.m_32, r.m_33]])
    t = block.translation
    return rot, np.array([t.x, t.y, t.z]), block.scale


def _bind_pose_verts(shape, verts, rot, trans, scale):
    """World bind-pose vertices: skinned shapes use the NiSkinData skin
    transform (verts stored in skeleton-root space), unskinned shapes use the
    accumulated scene-graph transform."""
    si = getattr(shape, 'skin_instance', None)
    if si:
        st = si.data.skin_transform
        rot = np.array([[st.rotation.m_11, st.rotation.m_12, st.rotation.m_13],
                        [st.rotation.m_21, st.rotation.m_22, st.rotation.m_23],
                        [st.rotation.m_31, st.rotation.m_32, st.rotation.m_33]])
        trans = np.array([st.translation.x, st.translation.y, st.translation.z])
        scale = st.scale
    return verts @ rot * scale + trans


def read_shapes(source):
    """Load every trishape of a NIF: verts (world bind pose), uvs, normals,
    tris, texture list.  Returns a list of dicts."""
    data = read_nif(source)
    shapes = []

    def visit(block, prot, ptrans, pscale):
        rot, trans, scale = _local_transform(block)
        rot = rot @ prot
        trans = trans @ prot * pscale + ptrans
        scale = scale * pscale
        if isinstance(block, (NifFormat.NiTriShape, NifFormat.NiTriStrips)):
            gdata = block.data
            if gdata is None or gdata.num_vertices == 0:
                return
            verts = np.array([[v.x, v.y, v.z] for v in gdata.vertices])
            uvs = (np.array([[u.u, u.v] for u in gdata.uv_sets[0]])
                   if len(gdata.uv_sets) else None)
            norms = (np.array([[n.x, n.y, n.z] for n in gdata.normals])
                     if gdata.has_normals else None)
            tris = [tuple(t) for t in gdata.get_triangles()]
            if uvs is None or not tris:
                return
            if norms is not None and getattr(block, 'skin_instance', None) is None:
                norms = norms @ rot
            shapes.append({
                'name': block.name.decode('ascii', 'replace'),
                'verts': _bind_pose_verts(block, verts, rot, trans, scale),
                'uvs': uvs,
                'norms': norms,
                'tris': tris,
                'texs': [t.lower().replace('/', '\\') for t in _shape_textures(block)],
            })
            return
        for child in getattr(block, 'children', []) or []:
            if child is not None:
                visit(child, rot, trans, scale)

    for root in data.roots:
        if isinstance(root, NifFormat.NiAVObject):
            visit(root, np.eye(3), np.zeros(3), 1.0)
    return shapes


#: Face normals meeting at a sharper angle than ~45 degrees start a new island.
CREASE_COS = 0.7


def _union_roots(count, links):
    """Union-find over `count` items joined by (a, b) links -> root of each item."""
    parent = list(range(count))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for a, b in links:
        parent[find(a)] = find(b)
    return [find(i) for i in range(count)]


def _islands(tris, nverts):
    """Connected components over shared vertices -> list of triangle-id lists."""
    roots = _union_roots(nverts, ((t[i], t[i + 1]) for t in tris for i in (0, 1)))
    groups = {}
    for i, t in enumerate(tris):
        groups.setdefault(roots[t[0]], []).append(i)
    return list(groups.values())


def _welded_tris(shape):
    """Triangles re-indexed so vertices equal in position AND uv share one index."""
    key = np.round(np.hstack([shape['verts'], shape['uvs']]), 4)
    _, first, inverse = np.unique(key, axis=0, return_index=True, return_inverse=True)
    canon = first[inverse.reshape(-1)]
    return [tuple(int(canon[i]) for i in t) for t in shape['tris']]


def _facets(v, tris, tri_ids):
    """Split one island at creases -> list of triangle-id lists.

    Triangles stay together only when they share a vertex AND their face
    normals agree within CREASE_COS, so a cover skinned continuously round
    the spine (Morrowind) separates into front, spine and back exactly as an
    Oblivion cover with split seam vertices already does.
    """
    t = np.asarray([tris[i] for i in tri_ids])
    n = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
    n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-12
    by_vert = {}
    for k, tri in enumerate(t):
        for vid in tri:
            by_vert.setdefault(vid, []).append(k)
    links = ((a, b) for ks in by_vert.values() for i, a in enumerate(ks)
             for b in ks[i + 1:] if n[a] @ n[b] > CREASE_COS)
    groups = {}
    for k, root in enumerate(_union_roots(len(t), links)):
        groups.setdefault(root, []).append(tri_ids[k])
    return list(groups.values())


class Island:
    """One connected face group with its geometry/uv summary."""

    def __init__(self, shape, tri_ids):
        tris = shape['tris']
        vids = sorted({v for ti in tri_ids for v in tris[ti]})
        self.shape = shape
        self.vids = vids
        self.verts = shape['verts'][vids]
        uvs = shape['uvs'][vids]
        # Normalize wrapped UVs (template islands sit at u-1 / u+1 / v+1 and
        # rely on texture wrap): shift by the floor of the island midpoint.
        mid = (uvs.min(axis=0) + uvs.max(axis=0)) / 2.0
        self.uv_shift = np.floor(mid)
        self.uvs = uvs - self.uv_shift
        # area + area-weighted normal from triangle cross products
        area = 0.0
        nsum = np.zeros(3)
        v = shape['verts']
        for ti in tri_ids:
            a, b, c = tris[ti]
            cr = np.cross(v[b] - v[a], v[c] - v[a])
            area += np.linalg.norm(cr) / 2.0
            nsum += cr / 2.0
        self.area = area
        self.normal = nsum / (np.linalg.norm(nsum) + 1e-12)
        self.pos_min = self.verts.min(axis=0)
        self.pos_max = self.verts.max(axis=0)
        self.uv_min = self.uvs.min(axis=0)
        self.uv_max = self.uvs.max(axis=0)

    def span(self):
        return self.pos_max - self.pos_min


class RegionFit:
    """Affine map: normalized plane coords (2) -> uv (2), fitted per island."""

    def __init__(self, island, axes):
        self.axes = axes
        lo = island.pos_min[list(axes)]
        hi = island.pos_max[list(axes)]
        self.lo, self.rng = lo, np.maximum(hi - lo, 1e-6)
        coords = (island.verts[:, list(axes)] - lo) / self.rng
        A = np.hstack([coords, np.ones((len(coords), 1))])
        sol, *_ = np.linalg.lstsq(A, island.uvs, rcond=None)
        self.M = sol[:2].T  # 2x2
        self.b = sol[2]
        resid = island.uvs - (coords @ sol[:2] + sol[2])
        self.rms = float(np.sqrt((resid ** 2).sum(axis=1).mean()))
        self.uv_min = island.uv_min
        self.uv_max = island.uv_max

    def uv_from_n(self, n):
        """n: (...,2) normalized coords -> uv, clamped to the island uv bbox."""
        uv = n @ self.M.T + self.b
        return np.clip(uv, self.uv_min, self.uv_max)

    def n_from_uv(self, uv):
        """Inverse map, clamped to [0,1]^2."""
        inv = np.linalg.inv(self.M)
        n = (uv - self.b) @ inv.T
        return np.clip(n, 0.0, 1.0)


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------

class Calibration:
    """Per-mesh region fits.  kind: 'book' | 'sheet' | 'identity'."""

    def __init__(self, kind, cover=None, spine=None, pages=None,
                 cover_tex=None, pages_tex=None, pages_islands=None):
        self.kind = kind
        self.cover = cover              # RegionFit (front cover, plane x/y)
        self.spine = spine              # RegionFit (plane y/z) or None
        self.pages = pages              # RegionFit for page-edge src or None
        self.pages_islands = pages_islands or []  # dst strips (template only)
        self.cover_tex = cover_tex      # source texture path (lowercase)
        self.pages_tex = pages_tex


def _all_islands(shapes, split=True, weld=False):
    """Every shape's islands, crease-split into flat facets unless `split` is False.

    `weld` joins vertices equal in position and uv first, so a mesh stored as
    loose triangles still forms islands.
    """
    out = []
    for shape in shapes:
        tris = _welded_tris(shape) if weld else shape['tris']
        for tri_ids in _islands(tris, len(shape['verts'])):
            parts = _facets(shape['verts'], tris, tri_ids) if split else [tri_ids]
            out += [Island(shape, part) for part in parts]
    return out


def calibrate(shapes):
    """Classify a mesh's UV islands into book regions.

    Books lying closed (_closed_book) or open (_open_book) calibrate as
    'book'.  Flat sheets: one dominant flat island.  Anything unfittable
    (rolled scrolls, crumpled paper) falls back to an identity full-texture map.
    """
    islands = _all_islands(shapes)
    if not islands:
        return Calibration('identity')
    all_min = np.min([i.pos_min for i in islands], axis=0)
    all_max = np.max([i.pos_max for i in islands], axis=0)
    total_span = np.maximum(all_max - all_min, 1e-6)
    return (_closed_book(shapes, islands, total_span, (all_min[2] + all_max[2]) / 2.0)
            or _open_book(_all_islands(shapes, weld=True), total_span)
            or _sheet(shapes))


def _closed_book(shapes, islands, total_span, z_mid):
    """Calibrate a book lying CLOSED, face-up, or None.

    Front cover: the largest flat +Z island above the midplane, spanning the
    footprint.  Spine: the largest tall |n_x| island on the same texture.
    Page edges: the largest island of the first shape on another texture
    (matched by id(): shapes hold numpy arrays, so `in` would compare them).
    """
    flats = [i for i in islands if i.normal[2] > 0.85
             and (i.pos_min[2] + i.pos_max[2]) / 2.0 >= z_mid]
    front = max(flats, key=lambda i: i.area, default=None)
    if front is None or not _spans_footprint(front, total_span) or total_span[2] <= 1.0:
        return None
    front_tex = _tex_of(front.shape)
    spines = [i for i in islands
              if abs(i.normal[0]) > 0.7
              and i.span()[1] > 0.6 * total_span[1]
              and _tex_of(i.shape) == front_tex]
    if not spines:
        return None
    page_shapes = [s for s in shapes if s['texs'] and s['texs'][0] != front_tex]
    page_ids = {id(s) for s in page_shapes}
    big = max((i for i in islands if id(i.shape) in page_ids),
              key=lambda i: i.area, default=None)
    return Calibration(
        'book',
        cover=RegionFit(front, (0, 1)),
        spine=RegionFit(max(spines, key=lambda i: i.area), (1, 2)),
        pages=big and RegionFit(big, (int(np.argmax(big.span()[:2])), 2)),
        cover_tex=front_tex,
        pages_tex=page_shapes[0]['texs'][0] if page_shapes else None,
    )


def _sheet(shapes):
    """Calibrate a flat sheet, or 'identity' when its UVs are not affine.

    The sheet is the largest island, its plane the two axes orthogonal to its
    dominant normal axis (x before z / y before z keeps width-then-height).
    Judged on whole connected islands, never crease-split facets: a crumpled
    page's single facet is flat, the page as a whole is not.  Non-affine UVs
    (rolled scroll, crumpled paper) show the source texture as-is.
    """
    big = max(_all_islands(shapes, split=False), key=lambda i: i.area)
    d = int(np.argmax(np.abs(big.normal)))
    axes = tuple(a for a in (0, 1, 2) if a != d)
    fit = RegionFit(big, axes)
    tex = _tex_of(big.shape)
    if fit.rms > 0.08:
        return Calibration('identity', cover_tex=tex)
    return Calibration('sheet', cover=fit, cover_tex=tex)


def _spans_footprint(isle, total_span):
    """True when `isle` covers >= 75% of the mesh footprint on both horizontal axes."""
    return bool(np.all(isle.span()[:2] >= 0.75 * total_span[:2]))


def _open_book(islands, total_span):
    """Calibrate a book lying OPEN, or None.

    Cover: one flat down-facing island (back | spine | front) whose inner
    vertex rows bound the spine; front = the higher-u side.  Requires an
    up-facing page spread on another texture of >= half the cover's area
    (a folded note has none).  Fits follow the closed-book convention in
    texture space: cover n = (spine -> fore-edge, bottom -> top), spine
    n = (length, back -> front).
    """
    unders = [i for i in islands
              if i.normal[2] < -0.85 and _spans_footprint(i, total_span)]
    if not unders:
        return None
    under = max(unders, key=lambda i: i.area)
    spread = sum(i.area for i in islands if i.normal[2] > 0.85
                 and _tex_of(i.shape) not in (None, _tex_of(under.shape)))
    if spread < 0.5 * under.area:
        return None
    across = int(np.argmax(np.abs(RegionFit(under, (0, 1)).M[0])))
    rows = {}
    for pos, u in zip(under.verts[:, across], under.uvs[:, 0]):
        rows.setdefault(round(float(pos), 1), []).append(u)
    us = [float(np.mean(rows[k])) for k in sorted(rows)]
    if len(us) < 4:
        return None
    spine_lo, spine_hi = sorted((us[1], us[-2]))
    v_bot, v_top = under.uv_max[1], under.uv_min[1]
    edges = [i for i in islands
             if abs(i.normal[2]) < 0.5 and i.shape is not under.shape]
    pages = max(edges, key=lambda i: i.area, default=None)
    return Calibration(
        'book',
        cover=bbox_fit((spine_hi, v_bot), (max(us[0], us[-1]), v_top)),
        spine=_SpineFit((spine_lo, v_bot), (spine_hi, v_top)),
        pages=pages and RegionFit(pages, (int(np.argmax(pages.span()[:2])), 2)),
        cover_tex=_tex_of(under.shape),
        pages_tex=pages and _tex_of(pages.shape),
    )


def _tex_of(shape):
    """First texture path of a read_shapes() shape, or None."""
    return shape['texs'][0] if shape['texs'] else None


def _tex_basename(path: str) -> str:
    """Leaf filename of a NIF texture path, normalized to '/' first."""
    forward = path.replace('\\', '/')
    return os.path.basename(forward)


def calibrate_book_template(shapes):
    """Calibrate the Skyrim book template.  Same geometric front/spine rules
    as the Oblivion side, plus the page-edge strips of the cover shape (the
    thin islands that wrap the page block between the covers)."""
    cal = calibrate(shapes)
    if cal.kind != 'book':
        raise ValueError('book template did not calibrate as a book')
    cover_shape = None
    for s in shapes:
        if s['texs'] and _tex_basename(s['texs'][0]) == BOOK_COVER_TEXES[0]:
            if cover_shape is None or len(s['tris']) > len(cover_shape['tris']):
                cover_shape = s
    if cover_shape is None:
        raise ValueError('book template has no %s shape' % BOOK_COVER_TEXES[0])
    all_isles = _all_islands(shapes)
    z_lo = min(i.pos_min[2] for i in all_isles)
    z_hi = max(i.pos_max[2] for i in all_isles)
    z_mid, z_half = (z_lo + z_hi) / 2.0, (z_hi - z_lo) / 2.0
    strips = []
    for tri_ids in _islands(cover_shape['tris'], len(cover_shape['verts'])):
        isle = Island(cover_shape, tri_ids)
        # page-edge strips span the page block interior between the covers
        # with side-facing normals
        if (abs(isle.normal[2]) < 0.5
                and isle.pos_min[2] < z_mid - 0.25 * z_half
                and isle.pos_max[2] > z_mid + 0.25 * z_half):
            if abs(isle.normal[0]) > 0.7:
                continue  # that's the spine (or its inner face)
            long_axis = int(np.argmax(isle.span()[:2]))
            strips.append((isle, RegionFit(isle, (long_axis, 2))))
    cal.pages_islands = strips
    return cal


def calibrate_note_template(shapes):
    cal = calibrate(shapes)
    if cal.kind != 'sheet':
        raise ValueError('note template did not calibrate as a flat sheet')
    return cal


# ---------------------------------------------------------------------------
# Texture bake
# ---------------------------------------------------------------------------

def _load_texture(path):
    """Decode a DDS/TGA to an RGBA numpy array (H,W,4), or None."""
    if path is None or not os.path.isfile(path):
        return None
    from PIL import Image
    with Image.open(path) as im:
        return np.asarray(im.convert('RGBA'), dtype=np.uint8)


def _flat_canvas(color):
    return np.tile(np.array(color, dtype=np.uint8), (CANVAS, CANVAS, 1))


def _sample_bilinear(img, u, v):
    """img (H,W,4); u,v arrays in [0,1] -> (...,4) uint8."""
    h, w = img.shape[:2]
    x = np.clip(u, 0.0, 1.0) * (w - 1)
    y = np.clip(v, 0.0, 1.0) * (h - 1)
    x0 = np.floor(x).astype(np.int64)
    y0 = np.floor(y).astype(np.int64)
    x1 = np.minimum(x0 + 1, w - 1)
    y1 = np.minimum(y0 + 1, h - 1)
    fx = (x - x0)[..., None]
    fy = (y - y0)[..., None]
    p00 = img[y0, x0].astype(np.float32)
    p01 = img[y0, x1].astype(np.float32)
    p10 = img[y1, x0].astype(np.float32)
    p11 = img[y1, x1].astype(np.float32)
    top = p00 * (1 - fx) + p01 * fx
    bot = p10 * (1 - fx) + p11 * fx
    return (top * (1 - fy) + bot * fy + 0.5).astype(np.uint8)


def _paint_region(canvas, dst_rect, dst_fit, src_fit, src_img):
    """Fill canvas pixels inside dst_rect (uv-space rect) by composing
    dst-uv -> normalized coords (dst_fit inverse) -> src-uv (src_fit)."""
    u0, v0, u1, v1 = dst_rect
    px0 = max(int(np.floor(u0 * CANVAS)), 0)
    py0 = max(int(np.floor(v0 * CANVAS)), 0)
    px1 = min(int(np.ceil(u1 * CANVAS)), CANVAS)
    py1 = min(int(np.ceil(v1 * CANVAS)), CANVAS)
    if px1 <= px0 or py1 <= py0:
        return
    us = (np.arange(px0, px1) + 0.5) / CANVAS
    vs = (np.arange(py0, py1) + 0.5) / CANVAS
    uu, vv = np.meshgrid(us, vs)
    uv = np.stack([uu, vv], axis=-1)
    n = dst_fit.n_from_uv(uv)
    src_uv = np.clip(src_fit.uv_from_n(n), 0.0, 1.0)
    canvas[py0:py1, px0:px1] = _sample_bilinear(src_img, src_uv[..., 0], src_uv[..., 1])


def _inflate(rect, px):
    d = px / CANVAS
    return (rect[0] - d, rect[1] - d, rect[2] + d, rect[3] + d)


def bake_atlas(obl_cal, tpl_cal, cover_img, pages_img):
    """Bake the Oblivion textures into the template's UV layout."""
    if cover_img is None:
        return None
    if obl_cal.kind in ('identity', 'sheet') or tpl_cal.kind == 'sheet':
        # sheet path: plain UV-space rect copy of the source sheet region onto
        # the whole canvas.  Deliberately NOT composed through mesh coords:
        # sheet art is always authored upright in texture space, while the
        # world mesh may lie in any orientation (a flat-lying broadsheet would
        # otherwise arrive rotated 90 degrees on the portrait note template).
        canvas = np.empty((CANVAS, CANVAS, 4), dtype=np.uint8)
        if obl_cal.kind == 'sheet':
            src_fit = bbox_fit(np.clip(obl_cal.cover.uv_min, 0, 1),
                                np.clip(obl_cal.cover.uv_max, 0, 1))
        else:
            src_fit = _identity_fit()
        _paint_region(canvas, (0.0, 0.0, 1.0, 1.0), _identity_fit(), src_fit, cover_img)
        return canvas

    # book path: base layer = cover art everywhere (edge-extended), then the
    # spine strip, then the page-edge strips
    canvas = np.empty((CANVAS, CANVAS, 4), dtype=np.uint8)
    _paint_region(canvas, (0.0, 0.0, 1.0, 1.0), tpl_cal.cover, obl_cal.cover, cover_img)
    sr = tpl_cal.spine
    spine_rect = _inflate((sr.uv_min[0], sr.uv_min[1], sr.uv_max[0], sr.uv_max[1]), 6)
    _paint_region(canvas, spine_rect, tpl_cal.spine, obl_cal.spine, cover_img)
    if pages_img is not None and obl_cal.pages is not None:
        for isle, strip_fit in tpl_cal.pages_islands:
            rect = _inflate((isle.uv_min[0], isle.uv_min[1],
                             isle.uv_max[0], isle.uv_max[1]), 1)
            _paint_region(canvas, rect, strip_fit, obl_cal.pages, pages_img)
    return canvas


class _BBoxFit:
    """Axis-aligned rect map: n in [0,1]^2 <-> uv in [lo,hi]."""

    def __init__(self, lo, hi):
        self.uv_min = np.asarray(lo, dtype=float)
        self.uv_max = np.asarray(hi, dtype=float)

    def uv_from_n(self, n):
        return self.uv_min + n * (self.uv_max - self.uv_min)

    def n_from_uv(self, uv):
        rng = np.maximum(self.uv_max - self.uv_min, 1e-9)
        return np.clip((uv - self.uv_min) / rng, 0.0, 1.0)


class _SpineFit(_BBoxFit):
    """Rect map with the axes crossed: n = (along uv v, along uv u)."""

    def uv_from_n(self, n):
        return super().uv_from_n(n[..., ::-1])


def bbox_fit(lo, hi):
    return _BBoxFit(lo, hi)


def _identity_fit():
    return _BBoxFit((0.0, 0.0), (1.0, 1.0))


# ---------------------------------------------------------------------------
# DDS output (uncompressed BGRA8 with a full mip chain)
# ---------------------------------------------------------------------------

def write_dds(path, rgba):
    """Write an RGBA (H,W,4) array as an uncompressed BGRA DDS with mips."""
    h, w = rgba.shape[:2]
    mips = [rgba]
    while mips[-1].shape[0] > 1 and mips[-1].shape[1] > 1:
        m = mips[-1].astype(np.uint16)
        m = (m[0::2, 0::2] + m[1::2, 0::2] + m[0::2, 1::2] + m[1::2, 1::2] + 2) >> 2
        mips.append(m.astype(np.uint8))
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000 | 0x8  # caps|h|w|pf|mips|pitch
    caps = 0x1000 | 0x400000 | 0x8  # texture | mipmap | complex
    hdr = bytearray(128)
    hdr[0:4] = b'DDS '
    struct.pack_into('<7I', hdr, 4, 124, flags, h, w, w * 4, 0, len(mips))
    struct.pack_into('<2I', hdr, 76, 32, 0x41)  # pf size, RGB|ALPHA
    struct.pack_into('<5I', hdr, 88, 32, 0x00FF0000, 0x0000FF00, 0x000000FF,
                     0xFF000000)  # BGRA masks
    struct.pack_into('<I', hdr, 108, caps)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(hdr)
        for m in mips:
            f.write(m[..., [2, 1, 0, 3]].tobytes())  # RGBA -> BGRA


# ---------------------------------------------------------------------------
# Template NIF emit
# ---------------------------------------------------------------------------

def emit_inam_nif(template_bytes, out_path, retarget_texes, diffuse_path, normal_path):
    """Copy the template with the cover texture sets pointed at the atlas."""
    data = read_nif(template_bytes)
    changed = 0
    for root in data.roots:
        for block in root.tree():
            if not isinstance(block, NifFormat.BSShaderTextureSet):
                continue
            if block.num_textures < 2 or not block.textures[0]:
                continue
            base = _tex_basename(
                block.textures[0].decode('ascii', 'replace').lower())
            if base in retarget_texes:
                block.textures[0] = diffuse_path.encode('ascii')
                block.textures[1] = normal_path.encode('ascii')
                changed += 1
    if not changed:
        raise ValueError('template had no retargetable texture sets')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'wb') as f:
        data.write(f)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def _find_source_texture(extract_roots, tex_rel):
    """Map a NIF-internal texture path to the extracted file, trying the
    _n-suffix sibling for normal maps via tex_rel directly.

    `extract_roots` is this plugin's export dir followed by its MASTERS'. A
    child plugin places its masters' book models (Clutter\\Books\\*), whose
    meshes and textures were extracted into the MASTER's export only, so
    searching this plugin's tree alone reported every one of them missing and
    silently shipped no inventory art for them."""
    if not tex_rel:
        return None
    rel = tex_rel.lower().replace('/', '\\')
    if not rel.startswith('textures\\'):
        rel = 'textures\\' + rel
    roots = list(_as_roots(extract_roots))
    for candidate in _texture_names(rel):
        for root in roots:
            p = os.path.join(root, *candidate.split('\\'))
            if os.path.isfile(p):
                return p
    return None


def _texture_names(rel):
    """The names a texture may ship under: as written, then as DDS.

    Morrowind meshes name textures .tga while its archives ship .dds, so the
    authored name alone finds nothing for any Morrowind book.
    """
    yield rel
    stem, dot, ext = rel.rpartition('.')
    if dot and ext in ('tga', 'bmp'):
        yield stem + '.dds'


def _as_roots(roots):
    """Accept a single root or an ordered list; always yield a list."""
    if not roots:
        return []
    if isinstance(roots, (str, os.PathLike)):
        return [str(roots)]
    return [str(r) for r in roots]


def _find_source_mesh(extract_roots, rel):
    """Locate a book model across this plugin's export tree then its masters'."""
    for root in _as_roots(extract_roots):
        p = os.path.join(root, 'meshes', *rel.split('\\'))
        if os.path.isfile(p):
            return p
    return None


def _normal_sibling(tex_path):
    if not tex_path:
        return None
    root, ext = os.path.splitext(tex_path)
    p = root + '_n' + ext
    return p if os.path.isfile(p) else None


def load_templates(templates_dir=None, skyrim_data=None):
    """Return {'book': bytes, 'note': bytes} template NIFs.

    Sources, in order: an explicit on-disk Skyrim meshes tree (templates_dir),
    then asset_convert.sources.skyrim_assets (references tree -> extraction cache ->
    the game's own SSE BSAs, auto-detected via registry).
    """
    from asset_convert.sources import skyrim_assets

    if skyrim_data:
        skyrim_assets.set_skyrim_data(skyrim_data)
    out = {}
    wanted = {'book': BOOK_TEMPLATE, 'note': NOTE_TEMPLATE}
    for key, rel in wanted.items():
        if templates_dir:
            p = os.path.join(templates_dir, *rel.split('\\'))
            if os.path.isfile(p):
                out[key] = open(p, 'rb').read()
                continue
        raw = skyrim_assets.get_asset_bytes(rel)
        if raw is not None:
            out[key] = raw
    still = [k for k in wanted if k not in out]
    if still:
        raise FileNotFoundError(
            'book INAM templates not found (%s); no references tree and no '
            'SSE install detected — pass --templates-dir or --skyrim-data'
            % ', '.join(wanted[k] for k in still))
    return out


def distinct_book_models(export_subdir):
    """Distinct Model.MODL paths from the export's BOOK.txt."""
    from tes5_import.base.text_reader import parse_export_file
    records = parse_export_file(os.path.join(export_subdir, 'BOOK.txt'))
    models = {}
    for rec in records:
        model = rec.get('Model.MODL', '')
        if model:
            models.setdefault(model.lower().replace('/', '\\'), model)
    return list(models.values())


def inv_basename(model_path):
    """BOOK MODL -> generated asset basename (leaf filename, no extension).

    Single source of truth: tes5_import/record_types/equipment.py imports this
    for its STAT synthesis, so the mesh written here and the STAT that points
    at it can never disagree.

    NOT unique on its own — see inv_basename_map() for the collision-aware
    form.  Callers that must handle a whole plugin's model set go through that.
    """
    base = model_path.replace('/', '\\').rsplit('\\', 1)[-1]
    return base.rsplit('.', 1)[0].lower()


def _qualified_basename(model_path):
    """Collision fallback: fold the parent directory into the name."""
    norm = model_path.replace('/', '\\').strip('\\')
    parts = norm.split('\\')
    base = parts[-1].rsplit('.', 1)[0].lower()
    if len(parts) < 2:
        return base
    parent = ''.join(ch for ch in parts[-2].lower() if ch.isalnum())
    return (parent + '_' + base) if parent else base


def inv_basename_map(models):
    """{model path -> unique asset basename} for one plugin's BOOK models.

    The leaf filename alone is not unique: plugins that merge several asset
    trees reuse a name across directories (Morroblivion ships both
    Clutter\\Books\\Note01.NIF and Morroblivion\\Clutter\\Paper\\Note01.nif).
    Both collapsed onto one basename, so two distinct models fought over a
    single output mesh and the collision guard aborted the whole asset stage.

    Resolution order is stable and additive: every model keeps its bare leaf
    name (so existing generated assets are untouched), and ONLY the models
    that actually collide fall back to a directory-qualified name, then to a
    numeric suffix.  Input order decides who keeps the bare name, so callers
    pass a deterministically ordered list.
    """
    counts = {}
    for m in models:
        counts[inv_basename(m)] = counts.get(inv_basename(m), 0) + 1

    out, taken = {}, set()
    for m in models:
        base = inv_basename(m)
        if counts[base] == 1:
            out[m] = base
            taken.add(base)
            continue
        for cand in (base, _qualified_basename(m)):
            if cand not in taken:
                out[m] = cand
                taken.add(cand)
                break
        else:
            i = 2
            while '%s_%d' % (base, i) in taken:
                i += 1
            out[m] = '%s_%d' % (base, i)
            taken.add(out[m])
    return out


# worker globals (populated by _worker_init in each pool process)
_W = {}


def _worker_init(book_tpl, note_tpl, extract_root, out_root, basenames=None):
    _W['book_tpl'] = book_tpl
    _W['note_tpl'] = note_tpl
    _W['book_cal'] = calibrate_book_template(read_shapes(book_tpl))
    _W['note_cal'] = calibrate_note_template(read_shapes(note_tpl))
    _W['extract_root'] = extract_root
    _W['out_root'] = out_root
    # Collision-resolved {model -> basename}; workers must not re-derive it
    # independently or two of them can pick the same output name.
    _W['basenames'] = basenames or {}


def _convert_one(model_rel):
    """Generate the INAM NIF + baked textures for one TES4 book model.
    Returns (model_rel, status, detail)."""
    base = _W['basenames'].get(model_rel) or inv_basename(model_rel)
    rel = model_rel.replace('/', '\\')
    src_nif = _find_source_mesh(_W['extract_root'], rel)
    if src_nif is None:
        return (model_rel, 'skip', 'source mesh missing')
    try:
        obl_cal = calibrate(read_shapes(src_nif))
        kind = 'book' if obl_cal.kind == 'book' else 'note'
        tpl_bytes = _W[kind + '_tpl']
        tpl_cal = _W[kind + '_cal']

        cover_src = _find_source_texture(_W['extract_root'], obl_cal.cover_tex)
        pages_src = _find_source_texture(_W['extract_root'], obl_cal.pages_tex)
        cover_img = _load_texture(cover_src)
        pages_img = _load_texture(pages_src)
        atlas = bake_atlas(obl_cal, tpl_cal, cover_img, pages_img)
        if atlas is None:
            return (model_rel, 'skip', 'source texture missing: %s' % obl_cal.cover_tex)

        cover_n = _load_texture(_normal_sibling(cover_src))
        pages_n = _load_texture(_normal_sibling(pages_src))
        atlas_n = (bake_atlas(obl_cal, tpl_cal, cover_n, pages_n)
                   if cover_n is not None else _flat_canvas(FLAT_NORMAL))

        out_root = _W['out_root']
        tex_dir, mesh_dir = inv_tex_dir(), inv_mesh_dir()
        dds_out = os.path.join(out_root, *tex_dir.split('\\'), base + '.dds')
        dds_n_out = os.path.join(out_root, *tex_dir.split('\\'), base + '_n.dds')
        nif_out = os.path.join(out_root, *mesh_dir.split('\\'), base + '.nif')
        write_dds(dds_out, atlas)
        write_dds(dds_n_out, atlas_n)
        emit_inam_nif(
            tpl_bytes, nif_out,
            BOOK_COVER_TEXES if kind == 'book' else NOTE_SHEET_TEXES,
            tex_dir + '\\' + base + '.dds',
            tex_dir + '\\' + base + '_n.dds')
        return (model_rel, 'ok', kind)
    except Exception as exc:  # keep the batch going; report per-model
        return (model_rel, 'fail', '%s: %s' % (type(exc).__name__, exc))


def split_master_owned(models, asset_subdir, extract_roots):
    """Drop the models whose source assets belong to a MASTER, not to us.

    A plugin's BOOK.txt lists every book it PLACES, including its masters'.
    Baking from a master's assets is what lets those books have inventory art
    at all (`_find_source_mesh` searches the masters for exactly that reason),
    but the generated pair is named after the model's leaf filename, so two
    plugins placing the same master book both write
    `clutter/books/inv/<base>.nif|.dds`. The bytes differ -- the atlas is baked
    from whichever extract root won -- so the copies genuinely conflict and the
    install order decides which one the game loads.

    Ownership follows the SOURCE mesh: a model this plugin extracted itself is
    its own to bake, and one that resolves only in a master's export is the
    master's.  `asset_subdir` is therefore this plugin's ASSET root, never its
    record dir -- the record dir has no meshes/ and every model would defer. The master's own run generates it at the identical path, so
    deferring loses nothing -- the STAT that `equipment.py` synthesizes points
    at the same `inv_basename`, and Data holds one file per path.

    A plugin that OVERRIDES a master's book mesh still ships its own copy in
    its export, so it keeps the bake and legitimately wins the path.
    """
    own, deferred = [], 0
    # `asset_subdir` is this plugin's own ASSET root -- the tree that actually
    # holds meshes/. It must be the same root `extract_roots[0]` names, or the
    # plugin's own assets get filtered in as a "master" root below and it
    # defers its own books to nobody.
    master_roots = [r for r in _as_roots(extract_roots)
                    if os.path.normpath(r) != os.path.normpath(asset_subdir)]
    if not master_roots:
        return list(models), 0
    for model in models:
        rel = model.replace('/', chr(92)).strip(chr(92))
        if _find_source_mesh([asset_subdir], rel) is not None:
            own.append(model)                 # we ship the source: ours to bake
        elif _find_source_mesh(master_roots, rel) is not None:
            deferred += 1                     # master ships it: master bakes it
        else:
            own.append(model)                 # missing everywhere: report as before
    return own, deferred


def generate_book_inams(source_file, extract_dir='export', output_dir='output',
                        templates_dir=None, skyrim_data=None, workers=None,
                        master_names=None):
    """Generate INAM meshes/textures for every distinct book model of a plugin.

    `master_names` are this plugin's TES4 masters, in load order. A plugin
    routinely places its masters' book models, whose meshes and textures were
    extracted into the MASTER's export dir only; without them every such model
    resolves to "source mesh missing" and ships no inventory art.

    Returns {'ok': n, 'skip': n, 'fail': n}.
    """
    source_name = Path(source_file).name
    # Records are per plugin; the meshes/textures they name live in the mod's
    # SHARED asset tree, which for an imported mod is the group folder.
    export_subdir = str(record_dir(extract_dir, source_name))
    asset_subdir = str(_asset_root(extract_dir, source_name))
    out_root = str(_out_root(output_dir, source_name, extract_dir))
    # This plugin's assets first, then its masters' — nearest wins, so a
    # plugin that overrides a master's asset still uses its own copy.
    extract_roots = [asset_subdir]
    for m in (master_names or []):
        d = str(_asset_root(extract_dir, m))
        if os.path.isdir(d) and os.path.normpath(d) != os.path.normpath(asset_subdir):
            extract_roots.append(d)
    models = distinct_book_models(export_subdir)
    if not models:
        return {'ok': 0, 'skip': 0, 'fail': 0}
    # The plugin's own ASSET root, not its record dir: ownership is decided by
    # which tree ships the source MESH, and the record dir holds no meshes at
    # all. Passing it meant the own-mesh probe always missed, the plugin's own
    # asset root was classed as a MASTER root (it no longer equals the record
    # dir), and every book a grouped mod ships was deferred to a master that
    # never bakes it -- so those books shipped with no inventory art.
    models, deferred = split_master_owned(models, asset_subdir, extract_roots)
    if deferred:
        print('  [book_inam] %d model(s) left to the master that ships them'
              % deferred)

    # Distinct models sharing a leaf filename each get their own unique asset
    # name (see inv_basename_map) — nothing is dropped, and the old hard
    # ValueError that aborted the entire asset stage on a clash is gone.
    models = sorted(models, key=lambda m: m.lower())
    basenames = inv_basename_map(models)

    tpls = load_templates(templates_dir, skyrim_data)
    stats = {'ok': 0, 'skip': 0, 'fail': 0}
    n_workers = workers if workers is not None else max(1, cpu_count() - 1)
    n_workers = min(n_workers, len(models))
    init_args = (tpls['book'], tpls['note'], extract_roots, out_root, basenames)
    # Validate templates in the parent BEFORE spawning workers: an initializer
    # crash in a pool worker (e.g. an SSE-format BSTriShape template pyffi
    # can't parse) surfaces only as an opaque BrokenProcessPool — and the
    # worker's stderr is invisible when multiprocessing runs pythonw.exe.
    _worker_init(*init_args)
    if n_workers <= 1:
        results = [_convert_one(m) for m in models]
    else:
        with ProcessPoolExecutor(max_workers=n_workers, initializer=_worker_init,
                                 initargs=init_args) as ex:
            results = list(ex.map(_convert_one, models))
    for model_rel, status, detail in results:
        stats[status] += 1
        if status != 'ok':
            print('  [book_inam] %s %s: %s' % (status.upper(), model_rel, detail))
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('source_file', help='Plugin name, e.g. Oblivion.esm')
    ap.add_argument('--extract-dir', default='export')
    ap.add_argument('--output-dir', default='output')
    ap.add_argument('--templates-dir', default=None,
                    help='explicit Skyrim meshes tree containing the reading '
                         'templates (default: auto-extract from the SSE BSAs)')
    ap.add_argument('--skyrim-data', default=None,
                    help='Skyrim SE Data folder (default: registry-detected)')
    ap.add_argument('--workers', type=int, default=None)
    args = ap.parse_args(argv)

    from asset_convert.lod.terrain_lod import master_names
    stats = generate_book_inams(args.source_file, args.extract_dir, args.output_dir,
                                args.templates_dir, args.skyrim_data, args.workers,
                                master_names=master_names(
                                    Path(args.extract_dir) / args.source_file))
    print('book_inam: ok=%(ok)d skip=%(skip)d fail=%(fail)d' % stats)
    return 0 if stats['fail'] == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
