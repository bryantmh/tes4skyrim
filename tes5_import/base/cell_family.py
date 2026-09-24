"""TES4 `GetInCell` prefix families re-expressed as exact TES5 tests.

TES4 matches a `GetInCell` argument as an EditorID prefix (`GetInCell Anvil`
is true in `AnvilTheCountsArms` and in the exterior `AnvilBayWest01`); TES5
compares the subject's parent cell to exactly one cell, and an exterior cell is
not a form until it loads, so it can never be a condition parameter.  A
condition naming a family is rewritten over its members: an exact `GetInCell`
per interior member, plus `LocationHasKeyword` for a keyword tagged onto every
exterior member's own Location.  The rewrite is conjunctive, so OR groups keep
their meaning.

See: docs/commentary/tes5_import_conditions.md#getincell-prefix-family
"""

import bisect
import itertools
import struct
from collections import namedtuple

from .ctda_bool import bool_outcomes
from .text_reader import get_formid, get_int, get_str, remap_formid
from .writer import pack_record, pack_string_subrecord

FUNC_GET_IN_CELL = 67
FUNC_LOCATION_HAS_KEYWORD = 562
_CTDA_OR = 0x01
_NO_CONDITIONS = frozenset({'REFR', 'ACHR', 'ACRE', 'LAND', 'PGRD', 'CELL',
                            'NAVM', 'WRLD'})

_Cell = namedtuple('_Cell', 'edid label source interior own world x y')

#: Output CELL FormID -> _Cell, for this plugin and its masters.
_CELLS: dict = {}
#: Sorted (lowercase EditorID, output FormID) pairs, for prefix range lookup.
_SORTED: list = []
#: Lowercase anchor EditorID -> member CELL FormIDs, memoized per run.
_FAMILY: dict = {}
#: Lowercase anchor EditorID -> KYWD FormID tagged on its exterior members' Locations.
_KEYWORD: dict = {}
#: Lowercase anchor EditorIDs whose KYWD this run wrote (it tags their exterior cells).
_OWN_ANCHORS: set = set()


def set_cell_families(by_type: dict, master_export: dict = None, writer=None,
                      master_index=None) -> None:
    """Index this run's cells (masters' included) and settle family keywords.

    A keyword is written here, before any condition converts, for every
    anchor a condition names whose family holds one of this plugin's exterior
    cells; an anchor the masters already tag adopts the master's keyword.
    """
    _CELLS.clear()
    _FAMILY.clear()
    _KEYWORD.clear()
    _OWN_ANCHORS.clear()
    masters = [r for r in (master_export or {}).values()
               if r.get('Signature') == 'CELL']
    for own, recs in ((False, masters), (True, by_type.get('CELL', []))):
        for rec in recs:
            _index_cell(rec, own)
    _SORTED[:] = sorted((c.edid, f) for f, c in _CELLS.items())
    if writer is not None:
        _settle_keywords(_used_anchors(by_type), writer, master_index)


def _index_cell(rec: dict, own: bool) -> None:
    """Record one CELL's identity, kind and grid square."""
    fid, edid = get_formid(rec, 'FormID'), get_str(rec, 'EditorID')
    world = get_formid(rec, 'ParentWRLD')
    if fid and edid:
        _CELLS[fid] = _Cell(edid.lower(), edid, get_str(rec, 'FormID'),
                            not world or bool(get_int(rec, 'DATA.Flags') & 1),
                            own, world, get_int(rec, 'XCLC.X', None),
                            get_int(rec, 'XCLC.Y', None))


def _record_anchors(rec: dict):
    """Yield the output cell FormID of each GetInCell in one record's conditions."""
    for key, raw_hex in rec.items():
        if not key.endswith('.Raw') or 'Condition[' not in key:
            continue
        raw = bytes.fromhex(raw_hex or '')
        if len(raw) >= 16 and struct.unpack_from('<H', raw, 8)[0] == \
                FUNC_GET_IN_CELL:
            yield remap_formid(struct.unpack_from('<I', raw, 12)[0])


def _used_anchors(by_type: dict) -> set:
    """Anchor FormIDs of every GetInCell a condition of this plugin names."""
    return {fid for sig, recs in by_type.items() if sig not in _NO_CONDITIONS
            for rec in recs for fid in _record_anchors(rec) if fid in _CELLS}


def _settle_keywords(anchors: set, writer, master_index) -> None:
    """Write or adopt the keyword of each anchor with exterior members."""
    for anchor in sorted(anchors):
        exteriors = [_CELLS[m] for m in cell_family(anchor)
                     if not _CELLS[m].interior]
        if not exteriors:
            continue
        name = _CELLS[anchor].edid
        edid = 'TES4InCell_' + name
        if any(c.own for c in exteriors):
            fid = writer.derive_formid('KYWD_CELL_FAMILY', name)
            writer.add_record('KYWD', pack_record(
                'KYWD', fid, 0, pack_string_subrecord('EDID', edid)))
            _OWN_ANCHORS.add(name)
            _KEYWORD[name] = fid
        elif master_index is not None:
            adopted = master_index.find_by_edid(b'KYWD', edid)
            if adopted:
                _KEYWORD[name] = adopted


def exterior_family_cells() -> dict:
    """{(world, x, y): (cell source FormID, EditorID, sorted keyword FormIDs)}.

    One entry per exterior grid cell of this plugin that belongs to a family
    this run tags; its Location must carry those keywords.
    """
    out = {}
    for cell in _CELLS.values():
        if cell.interior or not cell.own or cell.x is None:
            continue
        kws = sorted(_KEYWORD[a] for a in _OWN_ANCHORS
                     if cell.edid.startswith(a))
        if kws:
            out[(cell.world, cell.x, cell.y)] = (cell.source, cell.label, kws)
    return out


def cell_family(anchor_fid: int) -> list:
    """Sorted FormIDs of every cell whose EditorID starts with the anchor's."""
    cell = _CELLS.get(anchor_fid)
    if cell is None:
        return []
    members = _FAMILY.get(cell.edid)
    if members is None:
        members = []
        for edid, fid in _SORTED[bisect.bisect_left(_SORTED, (cell.edid,)):]:
            if not edid.startswith(cell.edid):
                break
            members.append(fid)
        members.sort()
        _FAMILY[cell.edid] = members
    return members


def _member_tests(ctda: bytes, anchor: int, members: list) -> list:
    """The anchor's comparison applied to each interior member and the keyword."""
    tests = [ctda[:12] + struct.pack('<I', m) + ctda[16:]
             for m in members if _CELLS[m].interior]
    kw = _KEYWORD.get(_CELLS[anchor].edid)
    if kw and any(not _CELLS[m].interior for m in members):
        tests.append(ctda[:8] + struct.pack('<HHI', FUNC_LOCATION_HAS_KEYWORD,
                                            0, kw) + ctda[16:])
    return tests


def _family_tests(ctda: bytes) -> 'tuple | None':
    """(passes-in-any-member, member CTDAs) for a GetInCell TES5 cannot test as-is.

    None when the CTDA is not one, or its comparison does not depend on the
    cell.  A True first element means "in any member"; False means "in none".
    """
    if struct.unpack_from('<H', ctda, 8)[0] != FUNC_GET_IN_CELL:
        return None
    anchor = struct.unpack_from('<I', ctda, 12)[0]
    members = cell_family(anchor)
    if len(members) < 2 and all(_CELLS[m].interior for m in members):
        return None
    outcomes = bool_outcomes(ctda[0], struct.unpack_from('<I', ctda, 4)[0])
    if outcomes is None or outcomes[0] == outcomes[1]:
        return None
    tests = _member_tests(ctda, anchor, members)
    return (outcomes[0], tests) if tests else None


def or_groups(pairs: list) -> list:
    """Split (CTDA, CIS2) pairs into OR groups: runs joined by the Or flag."""
    groups, cur = [], []
    for pair in pairs:
        cur.append(pair)
        if not pair[0][0] & _CTDA_OR:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return groups


def _as_clause(pairs: list) -> list:
    """Chain `pairs` into one OR group: Or flag on all but the last."""
    last = len(pairs) - 1
    return [(bytes([(c[0] & ~_CTDA_OR) | (_CTDA_OR if i < last else 0)])
             + c[1:], s) for i, (c, s) in enumerate(pairs)]


def _expand_group(group: list) -> list:
    """One OR group with its families expanded, as AND-joined OR clauses.

    (A or not-in-F) becomes AND over members m of (A or not-in-m).
    """
    any_of, none_of, expanded = [], [], False
    for pair in group:
        tests = _family_tests(pair[0])
        if tests is None:
            any_of.append(pair)
            continue
        expanded = True
        if tests[0]:
            any_of.extend((t, None) for t in tests[1])
        else:
            none_of.append(tests[1])
    if not expanded:
        return group
    out = []
    for pick in itertools.product(*none_of):
        out += _as_clause(any_of + [(t, None) for t in pick])
    return out


def expand_cell_families(pairs: list) -> list:
    """Rewrite every family GetInCell in (CTDA, CIS2) pairs as exact tests."""
    if not _CELLS:
        return pairs
    return [p for g in or_groups(pairs) for p in _expand_group(g)]
