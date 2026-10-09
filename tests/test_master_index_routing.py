"""A child's FormID must resolve to the master its INDEX BYTE names.

Every converted master renumbers into its OWN FormID space, so two masters'
id ranges overlap almost completely. ChainedMasterIndex used to answer a
lookup with the first master (in reverse load order) that happened to contain
the raw integer, which silently answered from the wrong file.

TWMP Valenwood/Elsweyr is the real case: 0202E438 is an exterior CELL in
Tamriel.esp AND a WRLD (ANQVerkarthHillsWorld) in ElsweyrAnequina.esp. The
reverse scan returned ANQ's worldspace for the Tamriel cell, so the writer
emitted a phantom worldspace plus 4,992 duplicate FormIDs (4,552 REFR, 237
CELL, 178 LAND) -- the same id twice with conflicting record types and group
nesting. The engine builds its FormID table while parsing the plugin, before
any cell loads, so the game hung on the main menu with no crash and no log.

Single-master plugins cannot hit this, which is why it stayed hidden.
"""

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tes5_import.overrides.master_index import ChainedMasterIndex, MasterIndex


def _sub(sig, payload):
    return sig + struct.pack('<H', len(payload)) + payload


def _rec(sig, fid, payload=b''):
    """A top-level record: 24-byte header + body."""
    return (sig + struct.pack('<I', len(payload))
            + struct.pack('<I', 0)          # flags
            + struct.pack('<I', fid)        # formid
            + struct.pack('<I', 0)          # revision
            + struct.pack('<HH', 44, 0)     # version, unknown
            + payload)


def _grup(label, gtype, body):
    return (b'GRUP' + struct.pack('<I', len(body) + 24) + label
            + struct.pack('<i', gtype) + b'\x00' * 8 + body)


def _plugin(masters, body):
    """A minimal but structurally real converted-master file."""
    hdr = _sub(b'HEDR', struct.pack('<fII', 1.71, 0, 0x800))
    for m in masters:
        hdr += _sub(b'MAST', m.encode() + b'\x00')
        hdr += _sub(b'DATA', b'\x00' * 8)
    return _rec(b'TES4', 0, hdr) + body


def _write(tmp_path, name, masters, body):
    p = tmp_path / name
    p.write_bytes(_plugin(masters, body))
    return str(p)


@pytest.fixture
def colliding_masters(tmp_path):
    """Two masters that both define 0202E438, as different record types.

    Mirrors the shipped layout: each file's own records carry the index byte
    equal to its own master count, so both land on 0x02.
    """
    shared = 0x0202E438

    # Tamriel.esp: masters = [Skyrim, Oblivion] -> own index 2.
    # The id is an exterior CELL nested under WRLD/type-1/block/sub-block.
    tam_cell = _rec(b'CELL', shared, _sub(b'EDID', b'TamrielCell\x00'))
    tam_body = _grup(b'WRLD', 0, _grup(struct.pack('<I', 0x0100003C), 1,
                                       _grup(b'\x00\x00\xff\xff', 4,
                                             _grup(b'\x00\x00\xff\xff', 5,
                                                   tam_cell))))
    tam = _write(tmp_path, 'Tamriel.esp', ['Skyrim.esm', 'Oblivion.esm'],
                 tam_body)

    # ElsweyrAnequina.esp: masters = [Skyrim, Oblivion] -> own index 2 too.
    # The SAME id is a top-level WRLD here.
    anq_wrld = _rec(b'WRLD', shared, _sub(b'EDID', b'ANQVerkarthHillsWorld\x00'))
    anq_body = _grup(b'WRLD', 0, anq_wrld)
    anq = _write(tmp_path, 'ElsweyrAnequina.esp',
                 ['Skyrim.esm', 'Oblivion.esm'], anq_body)

    return MasterIndex(tam), MasterIndex(anq)


class TestOwnIndex:
    def test_own_index_is_the_master_count(self, colliding_masters):
        tam, anq = colliding_masters
        assert tam.own_index == 2
        assert anq.own_index == 2

    def test_both_really_define_the_same_raw_id(self, colliding_masters):
        """The collision is real, not an artifact of the test setup."""
        tam, anq = colliding_masters
        assert tam.signature(0x0202E438) == b'CELL'
        assert anq.signature(0x0202E438) == b'WRLD'


class TestRoutingByIndexByte:
    """Child master list: 0=Skyrim 1=Oblivion 2=Tamriel 3=ElsweyrAnequina."""

    @pytest.fixture
    def chained(self, colliding_masters):
        tam, anq = colliding_masters
        return ChainedMasterIndex([tam, anq], base_slot=2)

    def test_slot_2_resolves_to_tamriels_cell(self, chained):
        # Before the fix this returned ANQ's WRLD -- the phantom worldspace.
        assert chained.signature(0x0202E438) == b'CELL'

    def test_slot_3_resolves_to_anqs_worldspace(self, chained):
        assert chained.signature(0x0302E438) == b'WRLD'

    def test_group_paths_do_not_cross(self, chained):
        """The wrong path is what mis-nested records into a phantom WRLD."""
        cell_path = chained.group_path(0x0202E438)
        wrld_path = chained.group_path(0x0302E438)
        assert cell_path != wrld_path
        # The CELL keeps its full block/sub-block nesting...
        assert len(cell_path) == 4
        assert cell_path[0] == (0, b'WRLD')
        # ...while the WRLD sits directly under the top-level group.
        assert wrld_path == ((0, b'WRLD'),)

    def test_record_bytes_come_from_the_right_file(self, chained):
        assert b'TamrielCell' in chained.record(0x0202E438)
        assert b'ANQVerkarthHillsWorld' in chained.record(0x0302E438)

    def test_unknown_index_byte_resolves_to_nothing(self, chained):
        """An id naming a slot we hold no master for must not fall through."""
        for fid in (0x0002E438, 0x0102E438, 0x0402E438):
            assert chained.signature(fid) == b''
            assert chained.record(fid) == b''
            assert chained.group_path(fid) == ()
            assert fid not in chained

    def test_contains_matches_signature(self, chained):
        assert 0x0202E438 in chained
        assert 0x0302E438 in chained

    def test_formids_are_reported_in_child_space(self, chained):
        """Callers compare these against child-space ids, so they must match."""
        fids = chained.formids()
        assert 0x0202E438 in fids
        assert 0x0302E438 in fids

    def test_every_id_maps_to_exactly_one_master(self, chained):
        """The property the duplicate FormIDs violated."""
        seen = {}
        for fid in chained.formids():
            sig = chained.signature(fid)
            assert fid not in seen
            seen[fid] = sig
        assert len(seen) == 2


class TestEdidSkipsOverrideCopies:
    """find_by_edid answers with the file's own record, never its override.

    A master that overrides another file's record carries that record's EDID
    under the other file's index byte. Adopting that copy would restamp a
    foreign id into the master's slot, so the lookup skips it.
    """

    @pytest.fixture
    def master_with_override(self, tmp_path):
        own = _rec(b'MESG', 0x02000001, _sub(b'EDID', b'SharedMsg\x00'))
        # Same EDID, but this is Oblivion.esm's record overridden here.
        copy = _rec(b'MESG', 0x01000002, _sub(b'EDID', b'SharedMsg\x00'))
        path = _write(tmp_path, 'Later.esp', ['Skyrim.esm', 'Oblivion.esm'],
                      _grup(b'MESG', 0, own + copy))
        return MasterIndex(path)

    def test_override_copy_is_not_adoptable(self, master_with_override):
        assert master_with_override.find_by_edid(b'MESG', 'SharedMsg') == 0x02000001

    def test_to_child_leaves_foreign_slots_alone(self, colliding_masters):
        tam, anq = colliding_masters
        chained = ChainedMasterIndex([tam, anq], base_slot=2)
        # 0x01 names Oblivion.esm, which neither file defines: not restamped
        # into either file's slot.
        assert chained._to_child(tam, 0x01000001) == 0x01000001
        assert chained._to_child(anq, 0x01000001) == 0x01000001


class TestBaseSlot:
    def test_base_slot_shifts_the_whole_routing_table(self, colliding_masters):
        """With one prepended master the same masters answer one slot lower."""
        tam, anq = colliding_masters
        chained = ChainedMasterIndex([tam, anq], base_slot=1)
        assert chained.signature(0x0102E438) == b'CELL'
        assert chained.signature(0x0202E438) == b'WRLD'

    def test_default_base_slot_assumes_trailing_slots(self, colliding_masters):
        tam, anq = colliding_masters
        chained = ChainedMasterIndex([tam, anq])
        assert chained._base_slot == 2
