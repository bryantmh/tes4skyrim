"""A single-box collision is replaced only where the plugin places items inside it.

See: docs/commentary/asset_convert_collision.md#morrowind-stand-in-boxes
"""

import math

from asset_convert.collision import resting_items_plan
from asset_convert.nif import fixture_plan

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


def _export(tmp_path, item_pos):
    """A plugin with one shelf, rotated 90 degrees and scaled 2x, and one book."""
    _write(tmp_path / 'STAT.txt', [{'FormID': '00000A01',
                                    'Model.MODL': 'f\\\\shelf.nif'}])
    _write(tmp_path / 'BOOK.txt', [{'FormID': '00000B01',
                                    'Model.MODL': 'm\\\\book.nif'}])
    shelf = {'NAME': '00000A01', 'ParentCELL': '0000C001', 'PosX': '1000',
             'PosY': '500', 'PosZ': '0', 'RotX': '0', 'RotY': '0',
             'RotZ': str(math.pi / 2), 'XSCL.Scale': '2.0'}
    book = {'NAME': '00000B01', 'ParentCELL': '0000C001',
            'PosX': str(item_pos[0]), 'PosY': str(item_pos[1]),
            'PosZ': str(item_pos[2])}
    _write(tmp_path / 'REFR.txt', [shelf, book])


def _stocked(tmp_path, item_pos) -> bool:
    """Whether the shelf's box holds the book placed at `item_pos`."""
    _export(tmp_path, item_pos)
    path, _ = resting_items_plan.write_index(tmp_path)
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
