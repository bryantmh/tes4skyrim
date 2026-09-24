"""A single-box collision is replaced only where the plugin places items inside it.

See: docs/commentary/asset_convert_collision.md#morrowind-stand-in-boxes
"""

import math

from asset_convert.collision import resting_items_plan
from asset_convert.nif import fixture_plan
from tes4_export.morrowind_patch import PATCH_NAME

#: The shelf's box in its own frame: 100 x 40 x 200, centered on the origin.
_HALF = (50.0, 20.0, 100.0)


def _box_tris():
    """The 12 triangles of the shelf's box."""
    hx, hy, hz = _HALF
    c = [(x, y, z) for x in (-hx, hx) for y in (-hy, hy) for z in (-hz, hz)]
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6),
             (0, 2, 6, 4), (1, 5, 7, 3)]
    return [tri for a, b, d, e in quads
            for tri in ((c[a], c[b], c[d]), (c[a], c[d], c[e]))]


def _write(path, records):
    """One export text file holding `records`."""
    body = ''.join('---RECORD_BEGIN---\n' + ''.join(
        f'{k}={v}\n' for k, v in rec.items()) + '---RECORD_END---\n\n'
        for rec in records)
    path.write_text(body, encoding='utf-8')


def _plugin(root, name, masters=(), **files):
    """Export dump `root/name` with a header naming `masters` and `files`."""
    d = root / name
    d.mkdir()
    (d / '_HEADER.txt').write_text(''.join(
        f'Master[{i}]={m}\n' for i, m in enumerate(masters)), encoding='utf-8')
    for fname, records in files.items():
        _write(d / f'{fname}.txt', records)


def _refs(shelf_id, book_id, item_pos):
    """A shelf, rotated 90 degrees and scaled 2x, and a book at `item_pos`."""
    shelf = {'NAME': shelf_id, 'ParentCELL': '0000C001', 'PosX': '1000',
             'PosY': '500', 'PosZ': '0', 'RotX': '0', 'RotY': '0',
             'RotZ': str(math.pi / 2), 'XSCL.Scale': '2.0'}
    book = {'NAME': book_id, 'ParentCELL': '0000C001',
            'PosX': str(item_pos[0]), 'PosY': str(item_pos[1]),
            'PosZ': str(item_pos[2])}
    return [shelf, book]


def _export(tmp_path, item_pos):
    """One plugin defining and placing the shelf and the book."""
    _plugin(tmp_path, 'P.esm',
            STAT=[{'FormID': '00000A01', 'Model.MODL': 'f\\\\shelf.nif'}],
            BOOK=[{'FormID': '00000B01', 'Model.MODL': 'm\\\\book.nif'}],
            REFR=_refs('00000A01', '00000B01', item_pos))


def _stocked(tmp_path, item_pos, export=_export) -> bool:
    """Whether the shelf's box holds the book placed at `item_pos`."""
    export(tmp_path, item_pos)
    path, _ = resting_items_plan.write_index(tmp_path, 'P.esm')
    resting_items_plan._LOADED.pop(path, None)
    plan = {fixture_plan.FIXTURE_KEY: {'f/shelf.nif'},
            resting_items_plan.RESTING_KEY: path}
    fixture_plan.latch_fixture_model(plan, tmp_path / 'm' / 'f' / 'shelf.nif',
                                     tmp_path / 'm')
    try:
        return resting_items_plan.items_rest_inside(_box_tris())
    finally:
        fixture_plan.latch_fixture_model(None, '', '')


def test_item_inside_the_box_is_found(tmp_path):
    """Scaled 2x and turned 90 degrees, the box spans x 960..1040, y 400..600."""
    assert _stocked(tmp_path, (1030.0, 420.0, 150.0))


def test_item_on_top_of_the_box_is_not(tmp_path):
    """Above the box's top face (z 200) is outside it."""
    assert not _stocked(tmp_path, (1000.0, 500.0, 205.0))


def test_rotation_is_applied(tmp_path):
    """Inside the UNROTATED footprint (x 900..1100) but outside the turned one."""
    assert not _stocked(tmp_path, (1080.0, 500.0, 0.0))


def _patch_export(tmp_path, item_pos):
    """P.esm defines the shelf; only its dependent places it, with a master's book.

    The shelf is 01000A01 in P.esm but 02000A01 in the dependent, whose
    master list puts another plugin between the two.
    """
    _plugin(tmp_path, 'Base.esm',
            BOOK=[{'FormID': '00000B01', 'Model.MODL': 'm\\\\book.nif'}])
    _plugin(tmp_path, 'Other.esm', ['Base.esm'])
    _plugin(tmp_path, 'P.esm', ['Base.esm'],
            STAT=[{'FormID': '01000A01', 'Model.MODL': 'f\\\\shelf.nif'}])
    _plugin(tmp_path, 'Dep.esm', ['Base.esm', 'Other.esm', 'P.esm'],
            REFR=_refs('02000A01', '00000B01', item_pos))


def test_a_dependents_placements_are_read(tmp_path):
    """The shelf's owner places nothing; its dependent stocks the shelf."""
    assert _stocked(tmp_path, (1030.0, 420.0, 150.0), _patch_export)


def test_a_dependents_item_outside_is_not(tmp_path):
    """The dependent's book on top of the shelf leaves the box closed."""
    assert not _stocked(tmp_path, (1000.0, 500.0, 205.0), _patch_export)


def _morroblivion_patch(tmp_path, placer):
    """The generated patch defines the shelf; `placer` stocks it."""
    _plugin(tmp_path, PATCH_NAME,
            STAT=[{'FormID': '00000A01', 'Model.MODL': 'f\\\\shelf.nif'}],
            BOOK=[{'FormID': '00000B01', 'Model.MODL': 'm\\\\book.nif'}])
    _plugin(tmp_path, placer, [PATCH_NAME],
            REFR=_refs('00000A01', '00000B01', (1030.0, 420.0, 150.0)))
    path, _ = resting_items_plan.write_index(tmp_path, PATCH_NAME)
    return path is not None


def test_the_patch_reads_the_esms_it_patches(tmp_path):
    """Tribunal is one of the patch's sources."""
    assert _morroblivion_patch(tmp_path, 'Tribunal.esm')


def test_the_patch_ignores_other_mods_mastering_it(tmp_path):
    """A third-party plugin on top of the patch does not open its boxes."""
    assert not _morroblivion_patch(tmp_path, 'TR_Mainland.esm')
