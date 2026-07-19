"""Tests for asset_convert/book_inam.py (INAM reading-rig generation).

The calibration/bake layers are exercised hermetically on synthetic geometry;
the end-to-end template path only runs when a references clone of the Skyrim
meshes is present (main checkout).
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from asset_convert.book_inam import (
    CANVAS,
    Calibration,
    RegionFit,
    Island,
    bake_atlas,
    calibrate,
    inv_basename,
    write_dds,
    _bbox_fit,
)

REFS = os.path.join(os.path.dirname(__file__), '..', 'references', 'Skyrim Meshes')


# ---------------------------------------------------------------------------
# synthetic geometry helpers
# ---------------------------------------------------------------------------

def _quad(corners, uvs):
    """Two triangles over 4 corners (list order: 00, 10, 11, 01)."""
    verts = np.array(corners, dtype=float)
    uv = np.array(uvs, dtype=float)
    tris = [(0, 1, 2), (0, 2, 3)]
    return verts, uv, tris


def _merge(parts, tex):
    verts, uvs, tris = [], [], []
    off = 0
    for v, u, t in parts:
        verts.append(v)
        uvs.append(u)
        tris += [(a + off, b + off, c + off) for a, b, c in t]
        off += len(v)
    return {
        'name': 'synthetic',
        'verts': np.vstack(verts),
        'uvs': np.vstack(uvs),
        'norms': None,
        'tris': tris,
        'texs': [tex],
    }


def _synthetic_book():
    """A closed book: front cover (+Z, art at u 0.2..1.0), back cover (-Z,
    same art rect), spine (-X side, u 0..0.18)."""
    w, h, t = 6.0, 10.0, 1.5
    front = _quad([(-w, -h, t), (w, -h, t), (w, h, t), (-w, h, t)],
                  [(0.2, 1.0), (1.0, 1.0), (1.0, 0.0), (0.2, 0.0)])
    back = _quad([(-w, -h, -t), (w, -h, -t), (w, h, -t), (-w, h, -t)],
                 [(0.2, 1.0), (1.0, 1.0), (1.0, 0.0), (0.2, 0.0)])
    spine = _quad([(-w, -h, -t), (-w, -h, t), (-w, h, t), (-w, h, -t)],
                  [(0.0, 1.0), (0.18, 1.0), (0.18, 0.0), (0.0, 0.0)])
    return [_merge([front], 'textures\\t\\cover.dds'),
            _merge([back], 'textures\\t\\cover.dds'),
            _merge([spine], 'textures\\t\\cover.dds')]


def _synthetic_sheet():
    sheet = _quad([(-4, -5, 0), (4, -5, 0), (4, 5, 0), (-4, 5, 0)],
                  [(0.0, 1.0), (1.0, 1.0), (1.0, 0.0), (0.0, 0.0)])
    return [_merge([sheet], 'textures\\t\\sheet.dds')]


# ---------------------------------------------------------------------------
# calibration
# ---------------------------------------------------------------------------

def test_calibrate_book_finds_cover_and_spine():
    cal = calibrate(_synthetic_book())
    assert cal.kind == 'book'
    assert cal.cover_tex == 'textures\\t\\cover.dds'
    # front cover fit maps normalized (x,y) into the art rect
    uv = cal.cover.uv_from_n(np.array([[0.0, 0.0], [1.0, 1.0]]))
    assert np.allclose(uv[0], [0.2, 1.0], atol=1e-6)
    assert np.allclose(uv[1], [1.0, 0.0], atol=1e-6)
    # spine fit spans the spine strip
    assert cal.spine is not None
    suv = cal.spine.uv_from_n(np.array([[0.5, 0.5]]))
    assert 0.0 <= suv[0, 0] <= 0.18


def test_calibrate_sheet():
    cal = calibrate(_synthetic_sheet())
    assert cal.kind == 'sheet'
    assert cal.cover_tex == 'textures\\t\\sheet.dds'


def test_calibrate_unfittable_falls_back_to_identity():
    # a full-wrap cylinder whose uv is NOT an affine function of position
    n = 24
    ang = np.linspace(0, 2 * np.pi, n)
    verts, uvs, tris = [], [], []
    for i, a in enumerate(ang):
        verts += [(np.cos(a), -5, np.sin(a)), (np.cos(a), 5, np.sin(a))]
        uvs += [(i / (n - 1), 1.0), (i / (n - 1), 0.0)]
    for i in range(n - 1):
        a = 2 * i
        tris += [(a, a + 1, a + 2), (a + 1, a + 3, a + 2)]
    shape = {'name': 's', 'verts': np.array(verts, dtype=float),
             'uvs': np.array(uvs, dtype=float), 'norms': None,
             'tris': tris, 'texs': ['textures\\t\\scroll.dds']}
    cal = calibrate([shape])
    assert cal.kind == 'identity'


def test_island_uv_wrap_normalization():
    v, u, t = _quad([(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)],
                    [(-0.8, 1.2), (-0.2, 1.2), (-0.2, 1.9), (-0.8, 1.9)])
    shape = _merge([(v, u, t)], 'textures\\t\\x.dds')
    isle = Island(shape, [0, 1])
    assert isle.uv_min[0] >= 0.0 and isle.uv_max[0] <= 1.0
    assert isle.uv_min[1] >= 0.0 and isle.uv_max[1] <= 1.0


# ---------------------------------------------------------------------------
# baking
# ---------------------------------------------------------------------------

def _gradient_img(w=64, h=64):
    img = np.zeros((h, w, 4), dtype=np.uint8)
    img[..., 0] = np.linspace(0, 255, w, dtype=np.uint8)[None, :]
    img[..., 1] = np.linspace(0, 255, h, dtype=np.uint8)[:, None]
    img[..., 3] = 255
    return img


def test_bake_book_atlas_regions():
    obl = calibrate(_synthetic_book())
    tpl = calibrate(_synthetic_book())  # template with the same layout
    src = _gradient_img()
    atlas = bake_atlas(obl, tpl, src, None)
    assert atlas.shape == (CANVAS, CANVAS, 4)
    # center of the art rect (u=0.6) must carry the art-rect gradient value:
    # dst u=0.6 -> n=(0.5,·) -> src u=0.6 -> red ~0.6*255
    px = atlas[CANVAS // 2, int(0.6 * CANVAS)]
    assert abs(int(px[0]) - int(0.6 * 255)) < 12
    # spine strip (u=0.09) maps within the src spine strip: red <= ~0.18*255
    spx = atlas[CANVAS // 2, int(0.09 * CANVAS)]
    assert spx[0] <= int(0.20 * 255)


def test_bake_sheet_is_uv_space_copy():
    """Sheets bake as a plain uv rect copy (no mesh-axis rotation), so a
    source gradient must arrive unrotated regardless of mesh orientation."""
    # sheet lying in the XY plane but with uv rotated 90 deg vs position
    sheet = _quad([(-4, -5, 0), (4, -5, 0), (4, 5, 0), (-4, 5, 0)],
                  [(0.0, 0.0), (0.0, 1.0), (1.0, 1.0), (1.0, 0.0)])
    obl = calibrate([_merge([sheet], 'textures\\t\\s.dds')])
    assert obl.kind == 'sheet'
    tpl = Calibration('sheet', cover=_bbox_fit((0, 0), (1, 1)))
    src = _gradient_img()
    atlas = bake_atlas(obl, tpl, src, None)
    # left edge of atlas = left edge of source (red channel low), even though
    # the mesh-space fit would have rotated it
    assert atlas[CANVAS // 2, 2, 0] < 30
    assert atlas[CANVAS // 2, CANVAS - 3, 0] > 225


# ---------------------------------------------------------------------------
# DDS writer
# ---------------------------------------------------------------------------

def test_write_dds_roundtrip(tmp_path):
    from PIL import Image
    img = _gradient_img(128, 128)
    out = tmp_path / 'x.dds'
    write_dds(str(out), img)
    with Image.open(out) as im:
        back = np.asarray(im.convert('RGBA'))
    assert back.shape == img.shape
    assert np.array_equal(back, img)


def test_inv_basename():
    assert inv_basename('Clutter\\Books\\Octavo02.NIF') == 'octavo02'
    assert inv_basename('Architecture/ImperialCity/Interior/ICElderScroll01.NIF') \
        == 'icelderscroll01'


# ---------------------------------------------------------------------------
# template integration (needs a references clone of the Skyrim meshes)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not os.path.isdir(REFS), reason='Skyrim meshes refs not present')
def test_template_calibration():
    from asset_convert.book_inam import (
        BOOK_TEMPLATE, NOTE_TEMPLATE, calibrate_book_template,
        calibrate_note_template, read_shapes)
    book = calibrate_book_template(
        read_shapes(os.path.join(REFS, *BOOK_TEMPLATE.split('\\'))))
    assert book.kind == 'book' and book.spine is not None
    assert book.pages_islands, 'page-edge strips must be found'
    note = calibrate_note_template(
        read_shapes(os.path.join(REFS, *NOTE_TEMPLATE.split('\\'))))
    assert note.kind == 'sheet'
