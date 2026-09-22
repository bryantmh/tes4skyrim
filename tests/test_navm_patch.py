"""Patching one corrected navmesh into a built ESM: splicing and carry-over."""

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tes5_import.base.tes5_reader import REC_HDR, walk
from tools.navmesh.navm_patch import (
    carry_doors, carry_water_flags, drop_links_to, enclosing_groups,
    grid_of, remap, seam_plane, splice, TRI_FLAG_WATER,
)

VERTS = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0),
         (0.0, 10.0, 0.0)]
TRIS = [(0, 1, 2), (0, 2, 3)]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class FakeNvnm:
    """The fields the carry-over helpers read off the replaced mesh."""

    def __init__(self, verts, tris, doors=(), cell=0):
        """Store the decoded geometry these tests hand the helpers."""
        self.verts, self.tris, self.doors = verts, tris, list(doors)
        self.cell = cell


class FakeView:
    """The link and triangle state `drop_links_to` rewrites."""

    def __init__(self, links, tris):
        """Store the mutable link array and triangle rows."""
        self.links, self.tris = links, tris


def _tri(v0, v1, v2, flags=0):
    """One decoded triangle row: three corners, three edges, flags, cover."""
    return [v0, v1, v2, -1, -1, -1, flags, 0]


def _tes4_header():
    """A minimal, empty TES4 header record."""
    return struct.pack('<4sIIIIHH', b'TES4', 0, 0, 0, 0, 44, 0)


def _record(sig, fid, payload):
    """One uncompressed record holding `payload`."""
    return (struct.pack('<4sIIIIHH', sig, len(payload), 0, fid, 0, 44, 0)
            + payload)


def _group(label, gtype, content):
    """One 24-byte GRUP wrapping `content`, its size covering the header."""
    return struct.pack('<4sI4sIHHHH', b'GRUP', 24 + len(content),
                       label, gtype, 0, 0, 0, 0) + content


def _nested_file(payload):
    """A TES4 header, a CELL top GRUP, a cell-children GRUP, and one NAVM."""
    navm = _record(b'NAVM', 0x0100000A, payload)
    inner = _group(struct.pack('<I', 0x01000005), 9, navm)
    return _tes4_header() + _group(b'CELL', 0, inner), navm


# ---------------------------------------------------------------------------
# Splicing a resized record
# ---------------------------------------------------------------------------

def test_splice_fixes_every_enclosing_group_size():
    """A grown record leaves the file walkable: both GRUPs above it grow too."""
    raw, navm = _nested_file(b'AB')
    start = raw.index(navm)
    bigger = _record(b'NAVM', 0x0100000A, b'ABCDEFGH')
    out = splice(raw, [(start, start + len(navm), bigger)])

    assert len(out) == len(raw) + 6
    found = [rec.form_id for rec, _stack in walk(out, b'NAVM', bodies=())]
    assert found == [0x0100000A]
    top = struct.unpack_from('<I', out, REC_HDR + 4)[0]
    assert top == len(out) - REC_HDR


def test_splice_of_equal_size_leaves_group_sizes_alone():
    """No size change means no delta, so nothing above the record moves."""
    raw, navm = _nested_file(b'AB')
    start = raw.index(navm)
    same = _record(b'NAVM', 0x0100000A, b'XY')
    out = splice(raw, [(start, start + len(navm), same)])
    assert len(out) == len(raw)
    assert out[REC_HDR:REC_HDR + 24] == raw[REC_HDR:REC_HDR + 24]


def test_enclosing_groups_is_outermost_first():
    """Both GRUPs are reported, the top-level one before the cell's."""
    raw, navm = _nested_file(b'AB')
    got = enclosing_groups(raw, raw.index(navm))
    assert got == [REC_HDR, REC_HDR + 24]


# ---------------------------------------------------------------------------
# What is carried over from the mesh being replaced
# ---------------------------------------------------------------------------

def test_water_flags_follow_the_old_water_plane():
    """A triangle below the replaced mesh's water line is flagged again."""
    old = FakeNvnm([(0.0, 0.0, -50.0), (1.0, 0.0, -50.0), (1.0, 1.0, -50.0)],
                   [_tri(0, 1, 2, TRI_FLAG_WATER)])
    verts = [(0.0, 0.0, -80.0), (1.0, 0.0, -80.0), (1.0, 1.0, -80.0),
             (2.0, 2.0, 100.0)]
    flags = carry_water_flags(old, verts, [(0, 1, 2), (1, 2, 3)])
    assert flags == [TRI_FLAG_WATER, 0]


def test_no_water_triangles_means_no_water_flags():
    """A dry cell must not gain a water plane out of an empty maximum."""
    old = FakeNvnm(VERTS, [_tri(0, 1, 2)])
    assert carry_water_flags(old, VERTS, TRIS) == [0, 0]


def test_doors_are_re_aimed_but_keep_their_formid():
    """The door REFR's id survives; only the triangle index is found again."""
    old = FakeNvnm([(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0)],
                   [_tri(0, 1, 2)], doors=[(0, 0x0100ABCD)])
    verts = [(100.0, 100.0, 0.0), (110.0, 100.0, 0.0), (110.0, 110.0, 0.0),
             (0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0)]
    got = carry_doors(old, verts, [(0, 1, 2), (3, 4, 5)])
    assert got == [(1, 0x0100ABCD)]


def test_a_door_on_a_vanished_triangle_is_dropped():
    """An index past the replaced mesh cannot be re-aimed, so it goes."""
    old = FakeNvnm(VERTS, [_tri(0, 1, 2)], doors=[(7, 0x0100ABCD)])
    assert carry_doors(old, VERTS, TRIS) == []


# ---------------------------------------------------------------------------
# Neighbour links
# ---------------------------------------------------------------------------

def test_dropping_a_link_renumbers_the_survivors():
    """An edge pointing past the removed link follows it down one slot.

    Triangle 0's edge 0 names the link being dropped and must revert to a
    plain border edge; triangle 1's edge 1 names link 2, which becomes link 1.
    """
    view = FakeView([[0, 0xAA, 3], [0, 0xBB, 4], [0, 0xCC, 5]],
                    [_tri(0, 1, 2, 0x0001), _tri(0, 2, 3, 0x0002)])
    view.tris[0][3] = 0
    view.tris[1][4] = 2
    drop_links_to(view, 0xAA)

    assert [lk[1] for lk in view.links] == [0xBB, 0xCC]
    assert view.tris[0][3] == -1 and not view.tris[0][6] & 0x0001
    assert view.tris[1][4] == 1 and view.tris[1][6] & 0x0002


def test_seam_plane_is_the_same_for_both_sides_of_one_seam():
    """East from (3, 5) and west from (4, 5) name the same X plane."""
    assert seam_plane(3, 5, 1, 0, 0) == seam_plane(4, 5, -1, 0, 0)
    assert seam_plane(3, 5, 0, 1, 1) == seam_plane(3, 6, 0, -1, 1)


# ---------------------------------------------------------------------------
# FormID and grid decoding
# ---------------------------------------------------------------------------

def test_remap_shifts_only_the_index_byte():
    """The load-order shift moves the high byte and nothing else."""
    assert remap(0x0001ABCD, 1) == 0x0101ABCD
    assert remap(0x0201ABCD, 2) == 0x0401ABCD
    assert remap(0, 3) == 0


def test_grid_is_read_y_first_and_signed():
    """An exterior NVNM packs (Y, X) as two signed shorts in one U32."""
    packed = struct.unpack('<I', struct.pack('<hh', -51, -17))[0]
    assert grid_of(FakeNvnm([], [], cell=packed)) == (-17, -51)
