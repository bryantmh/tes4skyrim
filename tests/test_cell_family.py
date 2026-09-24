"""TES4 GetInCell prefix families -> exact TES5 tests (cell_family)."""

import struct

import pytest

from tes5_import.base.cell_family import (FUNC_LOCATION_HAS_KEYWORD,
                                          cell_family, exterior_family_cells,
                                          set_cell_families)
from tes5_import.base.conditions import (CTDA_OR,
                                         convert_ctda_list_with_strings)
from tes5_import.base.text_reader import set_formid_index_offset

ANVIL, ARMS, BAY, CHORROL, GUARD, WORLD, KW = (0x100, 0x101, 0x102, 0x200,
                                               0x300, 0x3C, 0x900)


class _Writer:
    """Records what cell_family writes; every derived FormID is KW."""

    def __init__(self):
        self.records = []

    def derive_formid(self, _site, _key):
        return KW

    def add_record(self, sig, data):
        self.records.append(sig)


def _cell(edid: str, fid: int, interior: bool = True) -> dict:
    """An exported CELL record; an exterior one sits at (-49, -9) in WORLD."""
    rec = {'FormID': '%08X' % fid, 'EditorID': edid,
           'DATA.Flags': '1' if interior else '66'}
    if not interior:
        rec.update({'ParentWRLD': '%08X' % WORLD, 'XCLC.X': '-49',
                    'XCLC.Y': '-9'})
    return rec


def _raw(func: int, param: int, value: float = 1.0, op: int = 0,
         is_or: bool = False) -> str:
    """A 24-byte TES4 CTDA as hex."""
    type_byte = (op << 5) | (CTDA_OR if is_or else 0)
    return struct.pack('<B3xfHHII4x', type_byte, value, func, 0, param,
                       0).hex()


def _register(writer=None) -> None:
    """Anvil (dummy) + an Anvil interior + an Anvil exterior + one-cell Chorrol."""
    cells = [_cell('Anvil', ANVIL), _cell('AnvilTheCountsArms', ARMS),
             _cell('anvilbaywest01', BAY, interior=False),
             _cell('Chorrol', CHORROL)]
    info = {'Condition[0].Raw': _raw(67, ANVIL)}
    set_cell_families({'CELL': cells, 'INFO': [info]}, writer=writer)


@pytest.fixture(autouse=True)
def _cells():
    """Offset 0 and the no-writer family index; cleared afterwards."""
    set_formid_index_offset(0)
    _register()
    yield
    set_cell_families({})


def _convert(*raws) -> list:
    """(type byte, function, param1) of each converted TES5 CTDA."""
    rec = {'Condition[%d].Raw' % i: r for i, r in enumerate(raws)}
    return [(c[0], struct.unpack_from('<H', c, 8)[0],
             struct.unpack_from('<I', c, 12)[0])
            for c, _s in convert_ctda_list_with_strings(rec, offset=0)]


def test_family_is_case_insensitive_prefix():
    assert cell_family(ANVIL) == [ANVIL, ARMS, BAY]
    assert cell_family(CHORROL) == [CHORROL]


def test_positive_family_becomes_or_chain_of_interiors():
    """GetInCell Anvil == 1 -> in any Anvil* interior, AND the next condition."""
    out = _convert(_raw(67, ANVIL), _raw(72, GUARD))
    assert [(f, p) for _t, f, p in out] == [(67, ANVIL), (67, ARMS),
                                            (72, GUARD)]
    assert [t & CTDA_OR for t, _f, _p in out] == [1, 0, 0]


def test_exterior_members_are_tested_by_location_keyword():
    """The exterior member is never a GetInCell param; its keyword is."""
    writer = _Writer()
    _register(writer)
    assert writer.records == ['KYWD']
    assert exterior_family_cells() == {
        (WORLD, -49, -9): ('%08X' % BAY, 'anvilbaywest01', [KW])}
    out = _convert(_raw(67, ANVIL))
    assert [(f, p) for _t, f, p in out] == [
        (67, ANVIL), (67, ARMS), (FUNC_LOCATION_HAS_KEYWORD, KW)]


def test_negated_family_becomes_and_chain():
    """GetInCell Anvil == 0 -> in none of the Anvil* interiors."""
    out = _convert(_raw(67, ANVIL, value=0.0))
    assert [p for _t, _f, p in out] == [ANVIL, ARMS]
    assert all(t & CTDA_OR == 0 for t, _f, _p in out)


def test_negated_family_inside_or_group_distributes():
    """(GetIsID(guard) OR not in Anvil*) == AND over members of (guard OR not in m)."""
    out = _convert(_raw(72, GUARD, is_or=True), _raw(67, ANVIL, value=0.0))
    assert [(f, p) for _t, f, p in out] == [
        (72, GUARD), (67, ANVIL), (72, GUARD), (67, ARMS)]
    assert [t & CTDA_OR for t, _f, _p in out] == [1, 0, 1, 0]


def test_single_interior_cell_is_untouched():
    out = _convert(_raw(67, CHORROL))
    assert [(f, p) for _t, f, p in out] == [(67, CHORROL)]
