"""A child worldspace is navmeshed on the land the engine draws there.

With WNAM and no PNAM (every TES4 child) the engine uses the PARENT's land, so
the navmesh must be built on the parent's LAND at the same grid square. The
Fringe (SETheFringe, child of SEWorld) lost its seam at Fringe (-12, 0) this
way: that child cell owns no LAND, the mesh was built unclipped, and Jayred
Ice-Veins' SE02 escort had no route.
See: docs/commentary/tes5_import_navmesh.md#child-worldspaces-walk-the-parents-land
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tes5_import.navmesh.pool import navmesh_land_at

PARENT, CHILD, FNV_CHILD = 0x00001000, 0x00001001, 0x00001002


def _wrld(fid, parent=None, pnam=None):
    """A WRLD export record, optionally a child with an authored PNAM."""
    rec = {'Signature': 'WRLD', 'FormID': '%08X' % fid}
    if parent:
        rec['WNAM.Parent'] = '%08X' % parent
    if pnam is not None:
        rec['PNAM.Flags'] = str(pnam)
    return rec


def _cell(fid, wrld, x, y):
    return {'Signature': 'CELL', 'FormID': '%08X' % fid,
            'ParentWRLD': '%08X' % wrld, 'XCLC.X': str(x), 'XCLC.Y': str(y)}


def _land(fid, cell):
    return {'Signature': 'LAND', 'FormID': '%08X' % fid,
            'ParentCELL': '%08X' % cell, 'VHGT': 'land-%08X' % fid}


def _by_type():
    return {
        'WRLD': [_wrld(PARENT), _wrld(CHILD, PARENT),
                 _wrld(FNV_CHILD, PARENT, pnam=0x02)],
        'CELL': [_cell(0x2000, PARENT, -12, 0), _cell(0x2001, PARENT, -13, 0),
                 _cell(0x2100, CHILD, -12, 0), _cell(0x2101, CHILD, -13, 0),
                 _cell(0x2200, FNV_CHILD, -13, 0)],
        'LAND': [_land(0x3000, 0x2000), _land(0x3001, 0x2001),
                 _land(0x3101, 0x2101), _land(0x3200, 0x2200)],
    }


def _fid(rec):
    return rec['FormID'] if rec else None


def test_landless_child_cell_gets_the_parents_land():
    land_at = navmesh_land_at(_by_type())
    assert _fid(land_at((CHILD, -12, 0))) == '00003000'


def test_child_with_own_land_still_walks_the_parents():
    land_at = navmesh_land_at(_by_type())
    assert _fid(land_at((CHILD, -13, 0))) == '00003001'


def test_authored_pnam_without_land_bit_keeps_own_land():
    land_at = navmesh_land_at(_by_type())
    assert _fid(land_at((FNV_CHILD, -13, 0))) == '00003200'


def test_parent_uses_its_own_land():
    """A top-level world reads its own LAND, and None where it has none."""
    land_at = navmesh_land_at(_by_type())
    assert _fid(land_at((PARENT, -13, 0))) == '00003001'
    assert land_at((PARENT, 5, 5)) is None
