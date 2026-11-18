"""The converter's own package template roots: their package-data conditions name inputs the engine can read.

See: docs/commentary/tes5_import_package.md#sit-in-a-seat-in-another-cell
"""
import struct

import pytest

from tes5_import.packages.ambush_near import ambush_root_record
from tes5_import.packages.escort_when_near import escort_root_record
from tes5_import.packages.sit_at import SEAT_UNAM, sit_at_root_record

_TREE = (b'Procedure', b'Sequence', b'Stacked', b'Simultaneous', b'Random')
_REFERENCE_FUNCS = {1, 0x027F}


def _subrecords(record: bytes):
    """(type, payload) pairs of a packed record."""
    out, at, body = [], 0, record[24:]
    while at < len(body):
        size = struct.unpack_from('<H', body, at + 4)[0]
        out.append((body[at:at + 4], body[at + 6:at + 6 + size]))
        at += 6 + size
    return out


def _input_types_by_unam(subs):
    """{editor index: ANAM type} of the record's data inputs (the UNAM run right after them)."""
    types = []
    for sig, data in subs:
        if sig == b'ANAM':
            if data.rstrip(b'\0') in _TREE:
                break
            types.append(data.rstrip(b'\0'))
    first = next(i for i, (sig, _d) in enumerate(subs) if sig == b'UNAM')
    unams = [subs[first + k][1][0] for k in range(len(types))]
    return dict(zip(unams, types))


def _packdata_reference_params(subs):
    """Reference inputs of every package-data GetDistance / GetWithinDistance condition: its first parameter,
    and the input it runs on when Run On = Package Data."""
    out = []
    for sig, data in subs:
        if sig == b'CTDA' and data[0] & 0x08:
            _op, _v, func, _pad, p1, _p2, run_on, _ref, p3 = struct.unpack('<B3xfHHIIIIi', data[:32])
            if func in _REFERENCE_FUNCS:
                out += [p1, p3] if run_on == 6 else [p1]
    return out


def test_the_ambush_root_measures_from_its_place():
    """The target's distance is taken from the Place input, not the actor (who stops at the radius's edge)."""
    subs = _subrecords(ambush_root_record(0x01000ABF))
    gate = next(data for sig, data in subs if sig == b'CTDA')
    run_on, p3 = struct.unpack_from('<I', gate, 20)[0], struct.unpack_from('<i', gate, 28)[0]
    assert run_on == 6 and _input_types_by_unam(subs)[p3] == b'SingleRef'


@pytest.mark.parametrize('record', [
    sit_at_root_record(0x01000ABC), escort_root_record(0x01000ABD), ambush_root_record(0x01000ABF),
], ids=['sit', 'escort', 'ambush'])
def test_a_reference_condition_reads_a_single_ref_input(record):
    """A TargetSelector there faulted the engine on load (SkyrimSE+0x4a0a5f)."""
    subs = _subrecords(record)
    types = _input_types_by_unam(subs)
    params = _packdata_reference_params(subs)
    assert params
    assert all(types.get(p) == b'SingleRef' for p in params), (params, types)


def test_the_sit_root_gates_find_on_its_seat_input():
    """Vanilla Sit's tree with one condition, on Find, reading the Seat input."""
    subs = _subrecords(sit_at_root_record(0x01000ABC))
    assert _packdata_reference_params(subs) == [SEAT_UNAM]
    find = next(i for i, (sig, data) in enumerate(subs) if sig == b'PNAM' and data.rstrip(b'\0') == b'Find')
    assert subs[find - 1][0] == b'CTDA'
    assert [d.rstrip(b'\0') for s, d in subs if s == b'ANAM' and d.rstrip(b'\0') in _TREE].count(b'Stacked') == 0
